# 045 — Slow-tier derive cold-start OOM (float64 shading window + full-window landmask copy)

**#37** — on a **cold start** (fresh pod / empty render dir / `--refetch-all`),
`python -m whirls_cruise_map.build --stage derive --tier slow` is OOMKilled at a
2 Gi container limit (exit 137). Steady-state slow crons (populated render PVC,
~10-day pending span) do **not** OOM — this is strictly the first-seed path,
whose fetch span grows with the cruise. `oc_gateway` has bumped the slow-cron cap
2 Gi → 3 Gi as a stopgap (deploy-side; see the note at the end).

## Root cause (verified against current source, branch `deployment-focused-app`)

The cold-start peak is dominated by two full-window arrays over the 6-hourly
shading span (`FIELD_TMIN` 2026-06-28 → now + `FORECAST_REACH_H` 240 h; ~113
six-hourly steps × 481 lat × 541 lon × 2 vars on cluster):

1. **The resident window is float64.** `_currents.fetch_shading_window`
   (`_currents.py:142–183`) does `ds = ds.load()` at **`_currents.py:179`** with
   **no float32 cast**. The 6-hourly product `cmems_mod_glo_phy-cur_anfc_0.083deg_PT6H-i`
   stores float64, so the window sits resident at ~449 MB — matching the logged
   `Total size of the download: 448.94 MB` (copernicusmarine's decoded in-RAM
   estimate, not the zlib-compressed `.nc` on disk).

2. **The land mask copies the whole window.** `to_landmask_webp`
   (`_currents.py:450–473`) is handed the **entire** `shading` window
   (`build.py:351`) and, at **`_currents.py:458`**, `sortby("latitude").sortby("longitude")`s
   all of it (a full second copy), then at **`_currents.py:459`** does
   `uo = np.asarray(f["uo"].values, dtype=float)` — `dtype=float` is **float64** —
   a full float64 copy of `uo` across every timestep, and at
   **`_currents.py:460`** an `isnan`-over-all-steps bool
   (`np.all(np.isnan(uo), axis=0)`). All of that purely to compute a
   **time-invariant** 2-D land/sea mask.

Together — resident float64 window + full-window sortby copy + full-window float64
`uo` copy + the isnan bool, layered under the inertial complex array
(`_inertial.py:128`), copernicusmarine's dependency stack, and glibc malloc
fragmentation — RSS crosses 2 Gi and the kernel OOM-kills the run.

## Fixes

Three independent, independently-committable edits. Suggested commit order below;
each stands alone.

### Fix #1 — make float32 canonical (RAM everywhere; on-disk where we own the file)

**Can we make float32 the on-disk dtype at the source?** Surveyed against the
installed library (`copernicusmarine` **2.4.1** in the pixi env). Every point
currents data is loaded or persisted:

| Site | Path | dtype |
|---|---|---|
| `_currents.py:159` subset → `:178–179` `.load()` | shading window (direct 6-hourly fetch) | float64 temp `.nc` → **float64** in RAM (no cast) |
| `_field_store.py:242` subset → `:260–261` `.load()` | per-day hourly fetch | float64 temp `.nc` → **float64** in RAM (one day only) |
| `_field_store.py:125` `astype` → `:129` `to_netcdf` | per-day store write | **float32 on disk** (verified: `cache/field/uv_*.nc` → `uo/vo` float32) |
| `_field_store.py:458–464` `load_window` → `combined.load()` | store read | **float32** in RAM (reads the float32 day files) |

**`copernicusmarine.subset` cannot emit float32 at download.** The 2.4.1
signature exposes `file_format`, `netcdf_compression_level`, `netcdf3_compatible`,
`chunk_size_limit` — but **no** dtype / precision / variable-encoding knob. So the
temp `.nc` from either subset is product-native **float64**; float32-at-the-source
is not available for a direct CMEMS fetch. The shading temp `.nc` also lives in a
`TemporaryDirectory`, so its on-disk dtype is **moot except via the `.load()`
transient** — nothing downstream reads that file after the load.

That leaves two levers, and we take both:

**(a) Chunked-lazy cast in `fetch_shading_window` (`_currents.py:178–179`)** — cast
as early as possible *and* bound the load transient. `dask` is already a declared
dependency (`pixi.toml:34`, `dask = ">=2024.5.1"`; 2026.6.0 installed), so opening
the temp `.nc` chunked over time and casting lazily costs no new dependency:

```python
        with xr.open_dataset(out, chunks={"time": _SHADING_TIME_CHUNK}) as ds:
            ds = ds.astype({"uo": np.float32, "vo": np.float32}).load()
```

with a module constant e.g. `_SHADING_TIME_CHUNK = 8`. dask evaluates the
`astype` graph block-by-block: each K-timestep block is read as float64, cast to
float32, accumulated into the float32 result, and the float64 block released. So
the **full float64 window is never resident** — peak float64 is a few in-flight
blocks (~`n_threads × K × 4 MB`, e.g. ~32 MB per block at K=8), and the result is
the ~225 MB float32 window. Casting only `uo`/`vo` via the dict leaves the
`time`/`latitude`/`longitude` coords untouched.

This both **halves the resident window** (~449 MB → ~225 MB) and every downstream
float32 copy, *and* **resolves the astype transient**: the naive
`ds.load().astype(...)` would spike to ~674 MB (full float64 + float32 result
co-resident); the chunked form bounds it to a few blocks. The honest caveat is
that dask's threaded scheduler may hold several blocks concurrently, so the
transient is bounded, not literally one block — still ~10× under the full-window
spike, at negligible per-block scheduling cost against the network fetch.

**(b) The per-day fetch (`_field_store.py:260–261`) already writes float32 on
disk** (verified above), so the store *read* path is float32 end-to-end. Its own
`.load()` does briefly materialize one day of float64 (~24 steps, ~95 MB) before
the `:125` write-cast — but that is **one day, never the full span**, so it is not
a cold-start OOM driver. Applying the same chunked/early-cast idiom there is a
consistency nicety (a shared helper would DRY the two subset sites), not a memory
requirement; leave it optional and out of the mandatory scope.

**True float32-everywhere-on-disk is Fix #4.** The one way to get the *shading*
path float32 on disk as well as in RAM — with no second CMEMS egress — is to
source the 6-hourly frames from the **already-float32 per-day store** instead of a
separate float64 subset (the store already spans the whole cruise and every 12 h
frame time lands on its hourly grid). That is a product swap needing its own
validation, so it stays **deferred — see Fix #4**; (a) is the in-place win now.

### Fix #2 — the land mask needs ONE time step (`_currents.py:450–473`)

The land mask is **time-invariant**, definitively: every slice comes off the same
fixed grid, and this grid has no tidal-flat / intertidal cells, so the land/sea
geometry cannot change between steps. The mask from any one step *is* the mask.
The **only** thing `to_landmask_webp` does with the time dimension is the
`np.all(np.isnan(uo), axis=0)` reduction at `_currents.py:460`.

Take one time step up front and drop the over-time reduction. In
`to_landmask_webp`, replace `_currents.py:458–460`:

```python
    f = window.isel(time=0, drop=True) if "time" in window.dims else window
    f = f.sortby("latitude").sortby("longitude")
    land = np.isnan(f["uo"].values)
```

- `.isel(time=0, drop=True)` picks the earliest fetched step and drops the scalar
  `time` coord (the same `drop=True` idiom as the depth drop at `_currents.py:182`).
  The land pattern is step-independent, so *which* step is immaterial; `time=0` is
  deterministic and simplest.
- `sortby` now reorders a 2-D slice (~0.5 MB float32), not the full window.
- `land = np.isnan(...)` on the 2-D slice replaces the `ndim == 3` / `np.all(...,
  axis=0)` branch — the slice is always 2-D. The `dtype=float` (float64) cast at
  the old `_currents.py:459` is dropped; on a 2-D slice it was negligible anyway,
  but there is no reason to reintroduce it.

Everything below (`np.where(land, np.nan, 0.0)`, the `to_rgba` closure, the
`mercator_rgba_webp` warp) is unchanged.

The old `np.all(np.isnan(uo), axis=0)` consensus guarded only against a
*transient missing-data `NaN`* (a one-frame product hiccup — a different thing
from tidal geometry, which the fixed-grid point above already rules out), and that
is negligible for this CMEMS analysis/forecast product, so it is **safe to drop**.

**Also update the docstring.** `to_landmask_webp`'s docstring (`_currents.py:451–457`)
currently states land is `NaN` "at **every** time in `window`" and describes
guarding "a transient missing-data NaN in one frame" — that describes the removed
over-time reduction. Rewrite it to the single-slice reality: the mask is baked
from **one** representative time slice because the land geometry is time-invariant
on this grid.

**Note on the ranking:** the issue attributes a ~225 MB *landmask-copy* saving to
Fix #1, but the landmask copy is float64 regardless of window dtype (the explicit
`dtype=float` at `_currents.py:459`), so Fix #1 alone does not shrink it. Fix #2 is
what removes the full-window landmask cost — and once #2 lands, the landmask is a
single 2-D slice, so #1's landmask-copy saving is moot. The two fixes are
complementary, not additive on the landmask: **#2 owns the landmask saving, #1
owns the resident-window saving.**

### Fix #3 — release the shading window before the inertial step (`build.py`)

`shading` is last used at `build.py:366` (`to_vorticity_frames(shading, to_render)`);
the prune block (`build.py:379–386`) uses only `grid`. Nothing below the shading
block needs `shading`, yet it stays live through `load_window` (`build.py:395`) and
the inertial `decompose` — which build their own hourly window and the complex NI
array on top of the still-resident ~225 MB (post-#1).

Add `import gc` to the imports (alphabetically between `argparse` and `json`), and
release the window **after** the `if shading is not None and grid:` render block,
guarded on the fetch alone:

```python
    if shading is not None:
        del shading
        gc.collect()
```

**Guard on `shading`, not `grid`.** `fetch_shading_window` (inside a `try`) can
succeed while a *later* line in the same `try` raises — e.g. `window_frame_edge(shading,
t_lo)` reduces `window["time"].values.max()`, which raises on an empty time axis — and
the bare `except` then leaves `shading` bound but `grid` `None`. Freeing *inside* the
`if shading is not None and grid:` block (the first draft) would skip exactly that
case, pinning ~225 MB through the inertial step — the second spike this fix targets.
De-gating to `if shading is not None:` frees it whenever it was fetched; `shading` is
unbound afterward and never referenced again.

## Why float32 is safe (every shading consumer is visualization-only)

The window feeds four consumers, each of which produces a colour-quantized
(`N_BINS = 12`) WebP raster or a boolean mask — none carries float64-sensitive
numerics:

- **speed** — `np.hypot(f["uo"].values, f["vo"].values)` (`_currents.py:423`,
  `to_speed_frames`), clipped to `SPEED_VMAX` and snapped to 12 flat classes.
- **vorticity** — `np.gradient` finite differences in
  `_vorticity.zeta_over_f` (`_vorticity.py:78–79`), clipped to `±VORT_CLIP` and
  snapped to 12 classes.
- **flow streamlines** — `_raster.mercator_streamlines_webp(f["uo"].values, …)`
  (`_currents.py:380–382`, `to_flowvis_frames`).
- **land mask** — `np.isnan` (`_currents.py`, `to_landmask_webp`).

The banded output resolves colour at ~1/12 of the scale range; float32's ~7
significant digits is orders of magnitude finer than that. **Corroborating
precedent:** the *hourly* window that drives the more precision-sensitive
near-inertial least-squares decomposition is **already float32** on disk
(`_field_store._write_day_file`, `_field_store.py:125`), and `_inertial.decompose`
consumes it without issue — so the project already accepts float32 for the harder
numerical case. The shading rasters are the easier one.

## Verification

Be realistic about what reproduces where.

**On-cluster (the only true reproduction).** The 2 Gi OOM needs the full cruise-length
span, a Copernicus login, and the pod's memory pressure/fragmentation. The
authoritative check is a cold-start `--stage derive --tier slow` seed (empty render
dir or `--refetch-all`) at the slow-cron limits that previously OOMed. Do not claim
the OOM is fixed from local runs alone.

**Local end-to-end proxy (needs a CMEMS login).** Peak RSS of a cold-start slow
derive, before vs. after:

- macOS: `/usr/bin/time -l python -m whirls_cruise_map.build --stage derive --tier slow`
  → read *maximum resident set size* (bytes).
- Linux: `/usr/bin/time -v …` → *Maximum resident set size (kbytes)*.

The local span (`FIELD_TMIN` 2026-06-28 → now, ~18 days ≈ 76 steps today) is
smaller than the ~113-step cluster span, but the reductions are proportional
(dtype halving, full-window → single-slice), so the before/after *ratio* is
representative even though the absolute peak is lower. Expect the resident window
to roughly halve, the astype transient to stay bounded (chunked-lazy cast, never
the full float64 window), and the landmask transient to collapse from full-window
to a single slice.

**Cheap unit proxy (no CMEMS, deterministic).** On a small synthetic multi-step
window (a few float64 timesteps with a fixed land pattern) written to a temp `.nc`:

- assert `fetch_shading_window(...)["uo"].dtype == np.float32` (Fix #1) — or, to
  avoid the network, drive the chunked-lazy `open_dataset(..., chunks=...)
  .astype(...).load()` on the constructed temp file directly and assert float32
  out.
- around that chunked load, a `tracemalloc` peak shows float64 residency bounded
  to a few time-blocks rather than the whole window (Fix #1(a)) — the honest local
  evidence for the transient claim without CMEMS.
- assert `to_landmask_webp` is single-slice: the mask bytes from an N-step window
  equal those from its 1-step slice (Fix #2 preserves output), and — via
  `tracemalloc` peak around the call — an N-step window no longer allocates an
  N-sized float64 array (peak stays ~2-D, not ~N × 2-D).

## Fix #4 — deferred / optional architectural (bound peak independent of span)

**Out of the mandatory scope of this plan.** #1–#3 shrink the peak but it still
**scales linearly with the growing cold-start span** (`FIELD_TMIN` → now + 240 h);
a long-enough cruise eventually re-crosses any fixed cap. To make peak residency
*independent* of cruise length, one of:

- **Batch N frames** through fetch → render → write → release, so only N steps are
  ever resident (bounded working set), instead of the whole span at once.
- **Derive the frames from the per-day field store** (the referent of Fix #1(b) —
  the *only* route to float32 on disk for the shading path). The store is already
  on disk, already float32 (verified; `_field_store.py:125`), already spans the
  whole cruise, and every 12 h frame time lands on its hourly grid — so the frames
  could be sliced straight from it with **no second CMEMS egress and no full-span
  resident float64 window**. Caveat needing validation: the store is the hourly
  **mean** product (`WINDOW_DATASET_ID`, `…PT1H-m`) while the shadings currently
  fetch the 6-hourly **instantaneous** product (`DATASET_ID`, `…PT6H-i`) — visually
  near-identical for the banded speed/ζ·f/streamline rasters but not the same
  field, so this is a product swap to validate, not a drop-in.

Defer until #1–#3 are measured; revisit if the growing span re-approaches the cap
later in the cruise. This is the definitive "float32 everywhere, on disk too" end
state that Fix #1 approximates in RAM only.

## Deploy-side note (out of this repo's scope)

`oc_gateway` bumped the slow-cron instance template's `limits.memory` 2 Gi → 3 Gi
as a stopgap (protects every cold-start seed, incl. at `stage`/`adopt`/`promote`).
Once #1/#2 land, the cap *could* return to 2 Gi, but 3 Gi is cheap headroom given
the fetch span grows through the cruise — recommend leaving it at 3 Gi. That change
lives in the `oc_gateway` repo; nothing to do here.
