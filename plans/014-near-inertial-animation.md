# Near-inertial motion animation

An animated, looping rotating vector field on the map that shows the
near-inertial (NI) oscillation the CMEMS field carries — the "animated flow
particles through ±½ inertial period" visualization parked under plan 012. It
reconstructs the rotating current **analytically on the client** from the
per-cell `(mean, amplitude, phase)` decomposition (`_inertial.decompose`), so
the build ships a handful of static 2-D fields instead of an hourly time series.

This is **not** the previously-dropped animated drift dot (that walked the
forecast/hindcast polylines and was removed in `e9b339c`). This animates the
*field*, not a marker.

## The idea

`_inertial.decompose(window)` already fits, per grid cell, a mean current plus
one inertial-frequency rotary component:

```
u(t) + i·v(t) = (mean_u + i·mean_v) + amp · exp(i·(phase − f·(t − t_ref)))
f = 2·Ω·sin(lat)     (Ω = 7.2921159e-5 rad/s; f < 0 in the SH → CCW rotation)
```

Ship `mean_u, mean_v, amp, phase` as a small JSON grid. The client sweeps a
**24 h** time offset `dt` over a **~12 s** wall-clock loop and, per cell per
frame, evaluates the rotary term and draws a short vector glyph. Over one loop
each glyph's tip traces (most of) an inertial circle — the visible NI motion.

### Mean-inclusive vs NI-only — decided

The mean current dominates the NI amplitude by ~10–20× here, so animating
`mean + NI` would show near-static vectors with an imperceptible wobble. **The
client defaults to rendering the NI component alone** (`mean` subtracted →
clean rotating vectors), which is what makes the animation legible and matches
the feature's purpose. The `mean_u/mean_v` fields are shipped anyway, so folding
the mean back in is a **one-line client constant** (`SHOW_MEAN`), no rebuild.

### 24 h single clock — decided (user)

One shared wall clock sweeps `dt ∈ [0, 24 h)` for every cell. Because `f` varies
with latitude (T_f ≈ 15–46 h across the bbox), cells complete different
fractions of a turn in 24 h, and the loop wrap is a small visual discontinuity
for cells whose period ≠ 24 h. Accepted: a single clock is the point (no
per-cell desync), and 24 h is close to the deployment-site T_f (~20 h). `t_ref`
is **not** needed for the animation phase — the sweep is relative (`dt` from 0),
so `t_ref` is informational only (sidebar "valid time").

## Build side — `inertial_field.json`

New artifact, produced from the **same hourly `window`** already fetched for the
forecast/hindcast (`build.py`, inside `if window is not None:` after the
hindcast step) — decompose once, no second CMEMS call.

### New function in `_inertial.py`

```python
INERTIAL_STRIDE = 8   # coarser than currents' stride-3: arrows, not particles;
                      # the NI field is spatially smooth. ~68×60 ≈ 4k cells.

def to_inertial_field_json(decomp: xr.Dataset, stride: int = INERTIAL_STRIDE) -> dict:
    """Flatten the decomposition to a JSON vector field the client reconstructs
    the rotating NI current from analytically. Geometry mirrors
    `_currents.to_velocity_json` (NW-corner row-major, la1 = north edge) so the
    grid math is the same. Land → null (JSON has no NaN). True m/s — NO gamma
    magnitude compression (that is animation-cosmetic and would break the
    physical reconstruction)."""
```

Shape:

```json
{
  "header": {
    "nx": 68, "ny": 60,
    "lo1": -10.0, "lo2": 34.x, "la1": -15.0, "la2": -55.0,
    "dx": 0.666…, "dy": 0.666…,          // native 1/12° × stride
    "t_ref": "2026-07-03T12:00:00Z",
    "omega": 7.2921159e-5,
    "units": "m.s-1"
  },
  "mean_u": [ … nx*ny, row-major from NW, land=null … ],
  "mean_v": [ … ],
  "amp":    [ … ],                         // m/s, un-gained (GAIN=1.0)
  "phase":  [ … ]                          // radians
}
```

Details:
- Apply `stride` **inside** `to_inertial_field_json` (via `.isel(latitude=…,
  longitude=…)`), like `to_velocity_json` — `decompose` stays full-res for any
  other consumer.
- Sort latitude-descending, longitude-ascending, then `.ravel(order="C")` —
  identical row order to `_currents._component`, so `la1` is the north edge and
  the client derives `lat = la1 − row·dy`, `lon = lo1 + col·dx`.
- Round to 4 dp to keep the payload small (a few hundred KB, same order as
  `currents.json`).
- Land → `null`: convert NaN→None before `json.dumps` (guard: `json.dumps`
  emits bare `NaN`, which is invalid JSON and would break `fetch().json()`).
- **No** `_scale_for_animation` gamma — ship true m/s.

### build.py wiring

Add `_inertial` to the imports; inside the existing `if window is not None:`
block, after the hindcast write, best-effort:

```python
try:
    decomp = _inertial.decompose(window)
    _write_json(SITE_DATA / "inertial_field.json",
                _inertial.to_inertial_field_json(decomp))
    print(f"wrote inertial_field.json (valid {decomp.attrs['t_ref']})")
except Exception as exc:
    print(f"WARNING: inertial field step failed: {exc}")
```

Add an `inertial_field.json` line to build.py's artifact doc-comment.

### Test (`tests/test_inertial.py`)

Extend with `to_inertial_field_json` coverage, driven by a synthetic decomp
Dataset (no CMEMS): header geometry (nx/ny/lo1/la1/dx/dy match the strided
grid, la1 = north edge), row-major order, land cell → `null` in all four
arrays, `amp` un-gained, JSON is valid (`json.dumps`/`loads` round-trips, no
bare `NaN`), and stride reduces cell count as expected.

## Client side — `site/app.js`

**Custom canvas layer, not per-frame leaflet-velocity.** leaflet-velocity's
`setData()` tears down and reseeds its particle pipeline each call — fine on
pan/zoom-end, wrong at 60 fps — and it draws advected streaklines, not rotating
vectors. Reconstruction is one cheap `cos/sin` pair per cell; a plain canvas in
its own pane is the right tool and gives the exact rotating-arrow visual.

Plug-in points (all in `site/app.js` unless noted):
1. **`DATA` map** (top): `inertialField: "./data/inertial_field.json"`; update
   the header artifact doc-comment.
2. **Fetch** next to the other currents fetches:
   `const inertialField = await fetchJSON(DATA.inertialField, {optional:true});`
   Missing file → `null` → no layer, no control row.
3. **Pane** in the `createPane` stack: `map.createPane("inertial").style.zIndex
   = 360;` (above `shading` 350, below markers/ship).
4. **Layer + cells**: a builder `buildInertialField(inertialField)` that
   precomputes, once, a flat array of plain records
   `{lat, lon, mean_u, mean_v, amp, phase, f}` (`f = 2·Ω·sin(lat)`, keep the SH
   sign) from the header geometry, skipping `null` (land) cells; returns a
   canvas-backed `L.Layer` in the `"inertial"` pane. **Do not** `addTo(map)` →
   default OFF.
5. **Register** as a layer-control overlay (like "Current speed"/"Current
   flow"), NOT in the Instruments panel (this is one map-wide field, not
   per-instrument): `if (inertialField) overlays["Near-inertial animation"] =
   inertialLayer;` before the `L.control.layers(...)` call.
6. **Animation loop** `startInertialClock(map, cells, layer, ctx)`, modeled on
   the reverted `startDriftDotClock` (commit `60c82db`) — reuse verbatim:
   - wall-clock phase, not an accumulator:
     `const tau01 = ((performance.now()/1000) % LOOP_S) / LOOP_S;`
     `const dt = tau01 * 24*3600;`  // seconds
   - `requestAnimationFrame` → **free `document.hidden` pause** (rAF doesn't
     fire in hidden tabs; no `visibilitychange` handler);
   - **self-gate** so idle cost is a hash lookup:
     `if (!map.hasLayer(layer)) { requestAnimationFrame(tick); return; }`
   - started **once** in `main()`, never stopped.
   - per cell per frame: `θ = phase − f·dt`;
     `u = amp·cos θ (+ mean_u if SHOW_MEAN)`, `v = amp·sin θ (+ mean_v …)`;
     project `map.latLngToContainerPoint([lat, lon])`, draw a short line from
     the cell point along `(u, v)` (north = −y on screen), length ∝ speed
     (a fixed px-per-(m/s) scale, clamped), thin stroke. Clear the canvas each
     frame.
   - `SHOW_MEAN = false` constant at top of the builder (see decision above).
7. **Reproject on interaction**: resize/clear the canvas and redraw on
   `map.on("move zoom viewreset", …)` so it tracks pan/zoom (the loop redraws
   continuously anyway; this keeps it aligned mid-gesture and on resize).
8. **Canvas sizing**: size the layer canvas to the map pane and re-place its
   top-left on move, following the ship-track canvas precedent
   (`app.js` ship pane comments).

`index.html` / `style.css`: only if the canvas pane needs a style rule; no new
scripts (custom canvas, no library). No sidebar panel required for a first cut
(optional later, following `renderCurrentsInfo`).

## Validation / hand-off

The user builds and tests the served map (build needs CMEMS credentials; agents
don't run it). Agents may test with a **synthetic `site/data/inertial_field.json`**
(a hand-made small grid) to exercise the client path offline. Because the visual
was twice reconsidered on this map (the amplitude overlay and the drift dot were
both dropped after review), **docs and ROADMAP/plan-move are deferred** until the
user confirms the visual — this plan stays in `plans/` (not `done/`) until then.

## Out of scope (first cut)
- Particle/streakline advection (drawing tracer paths that trace circles) —
  possible later enhancement; vector glyphs are the simpler, clearer first cut.
- Per-cell local-inertial-period looping (keeps the single 24 h clock).
- Any amplitude gain (`GAIN = 1.0`; the no-gain resolution stands, plan 013).
- A sidebar legend/valid-time panel (optional follow-up).
