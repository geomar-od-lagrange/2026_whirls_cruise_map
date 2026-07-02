# Near-inertial forecast/hindcast via a time-dependent current field

**Status: DRAFT for discussion.** Reframed after five research/validation spikes
(2026-07-02). **Decision: no slab model** — the near-inertial (NI) oscillation is
already present in the CMEMS current field; the fix is to stop advecting through a
*frozen* snapshot and advect through a **time-dependent** field instead. The
Pollard–Millard slab approach is parked in
[inertial_slab_model.md](inertial_slab_model.md) (deferred/obsolete). Nothing in
`src/` is touched until Phase 0 validates against the drifters' own velocities.

## The decisive finding

The original premise — "the frozen 6-hourly field can't carry the inertial
oscillation, so add a slab" — was half wrong. An empirical check at the
Deployment-2 site (11.48°E, −37.36°S; local inertial period T_f = 19.8 h) showed:

- **CMEMS already contains a clean near-inertial signal.** Its rotary spectrum has
  a one-handed **counter-clockwise** peak (correct sense for the Southern
  Hemisphere) at ~20–22 h, CCW/CW ratio ~100:1, well separated from M2 (12.42 h,
  ~5× weaker) and diurnal — inertial, not tidal (the global model has no tides).
- **6-hourly resolves it — no aliasing.** T_f (~20 h) is *longer* than the 12 h
  Nyquist of 6-hourly sampling, so the 6-hourly product the repo already uses shows
  an essentially identical peak (2.46 vs 2.45 cm/s hourly). Region-wide T_f is
  ~24 h (−30°S) to ~15 h (−55°S), always above 12 h.
- **So the culprit is the *frozen* field, not its cadence.** Advecting through one
  fixed snapshot gives constant velocity — the oscillation the model *has* is
  discarded at t = 0. **Un-freezing the field recovers the inertial loop for
  free**, no slab — *to the extent CMEMS's NI amplitude is realistic.*
- **The only open question is realism, and it's data-gated.** CMEMS's modelled NI
  at D2 is ~2.5 cm/s; free-running globals can under-represent wind-driven NI. If
  the drifters' *observed* NI is much larger, we'd want the parked slab to add
  amplitude. Phase 0 settles this against the drifter velocities.

Validation scripts + plots: `tmp_ni/` (rotary spectrum, hodograph, NI time series).

## Design

### Time-dependent field; the stepper threads time

Today `_forecast._Field.velocity(lon, lat)` samples a frozen snapshot and the RK4
loop already tracks step→hours. The change: sample a **time-dependent** field —
`u_total(x, y, t) = u_CMEMS(x, y, t)` — adding **linear-in-time** interpolation
alongside the existing bilinear-in-space sampling. Forecast integrates forward,
hindcast backward, both through the same window (which covers past and future).
This is the "time-varying multi-step forecast" the backlog anticipated, and it
produces inertial loops from the model's own NI.

### CMEMS window fetch

Fetch the **hourly** dataset (`cmems_mod_glo_phy_anfc_0.083deg_PT1H-m`; see the
resolution decision below) over a **time window** instead of nearest-now. (Today's
`_currents.py` uses the 6-hourly `PT6H-i` for the single-time overlay; the
time-dependent advection field moves to hourly.) The anfc product is one ~2-year sliding
axis (recent analysis + 10-day forecast), so `[now−12 h, now+24 h]` is a single
`subset` call, no stitching. Use `coordinates_selection_method="outside"` so the
returned steps *bracket* the window (clean edge interpolation), and drop the
current `.sel(time=…, method="nearest")`/`.squeeze()` so the `time` dim survives.

### Resolution: hourly (`PT1H-m`) — decided

Both resolutions resolve the inertial peak equally (see finding); the trade-off
was interpolation accuracy vs fetch cost. **Measured** (`tmp_ni/timing.py`, full
bbox): 6 h ±12 h = 7.0 s / 25 MB; **1 h ±12 h = 7.8 s / 54 MB**; 1 h ±3 d (fit
window) = 21.6 s / 204 MB. Fetch time is dominated by catalog-resolution overhead,
so **hourly costs only ~+0.8 s** — effectively free. Hourly gives ~20 samples per
inertial cycle, so linear-in-time interpolation traces a smooth loop; 6-hourly's
~3 samples/cycle would *chord* the circle and flatten the loop. **Go hourly**
(`cmems_mod_glo_phy_anfc_0.083deg_PT1H-m`, surface bundle, hourly *mean* — a 1 h
boxcar attenuates a 20 h oscillation by <1%). 54 MB/window is trivial for the
6-hourly slow tier; if the per-cell inertial decomposition (visualization track)
lands, the cached artifact shrinks to a few 2-D fields regardless.

### Cadence: the field is slow, the positions are fast

CMEMS anfc updates **once daily** (~08:00 UTC); drifter positions update every few
minutes. So the field (the time-window fetch, plus any derived NI amplitude/phase
fields — see the visualization work) belongs in a **slow tier**, and the fast
5–10 min pipeline re-advects fresh positions through the cached field. This
decoupling is justified by CMEMS's daily cadence alone — it also answers "cache the
currents in the same slow job?": **yes.** Phase 1 may fetch the window in the
existing pipeline first to validate cheaply, then move it to the slow tier as the
optimization once proven (pre-alpha: correctness before cadence).

**Slow/fast wiring (researched).** Two GitLab pipeline schedules: a **slow** one
(`TIER=slow`, ~6-hourly) builds the field artifact; the existing **fast** Pages
build fetches the latest slow artifact via the **Job Artifacts API** with
`search_recent_successful_pipelines=true` (load-bearing — without it the API
inspects only the newest successful pipeline, a fast run with no field job → 404)
and `$CI_JOB_TOKEN` (same-project download allowed by default). On miss it
**recomputes inline** — correct, just slower — since the artifact is a pure
function of the data cycle. `expire_in: 1 week` auto-prunes; no manual prune job.
CI *cache* keyed by cycle was judged strictly worse (best-effort, runner-local, no
"latest" selector). Concrete `.gitlab-ci.yml` in the research notes.

### Visualization — separate design track

How to present the non-frozen/inertial flow ("which current components to plot
how") needs its own design and is being worked by a dedicated spike. The target
shape (to be refined there):

- **Speed at t = 0** — the existing surface-speed shading, unchanged.
- **Inertial magnitude at t = 0** — a field of the NI component's amplitude,
  *derived from the CMEMS time window* (complex demodulation / band-pass around f
  per grid cell), with a **toggle between speed and inertial**.
- **Animated flow particles through ±½ inertial period** — either store enough
  timesteps, or (storage-lean, preferred to explore) fit **per-cell inertial
  amplitude + phase** and reconstruct the rotating vector analytically on top of
  the frozen t = 0 mean current, so the client animates from a handful of static
  2-D fields rather than a time series. The same decomposition could also feed the
  advection stepper, shrinking the cached artifact.

Firm from the presentation spike already done: `leaflet-velocity` can't walk a dot
along a known path (leave it as background flow); the forecast dot is a **~40-line
custom `requestAnimationFrame`** with one shared clock across instruments (synced,
looping, no slider); and with time-dependence the **static polyline is already a
visible cycloid curl** — the animated dot is enhancement, drawn over the
always-present line + 1/3/6 h marks, on an optional toggle, paused on
`document.hidden`.

## Plan of work

- **Phase 0 — validate (`tmp_ni/`, no `src/` changes). The gate.** Compare, at the
  Deployment-2 (and other) drifters: **observed** NI (band-pass the drifters' own
  derived velocities around local f) vs **CMEMS** NI sampled along-track from the
  time-window field. Does time-dependent CMEMS advection reproduce the observed
  loops in phase and amplitude? If amplitude falls short, quantify the deficit
  (that, and only that, is what would revive [the slab](inertial_slab_model.md)).
- **Phase 1 — time-dependent CMEMS advection.** Windowed `subset` in `_currents`
  (keep `time`); linear-in-time in `_forecast._Field`; forecast + hindcast advect
  through the window. Ships the core feature.
- **Phase 2 — visualization.** Per the design spike: speed↔inertial toggle,
  inertial amplitude/phase fields, animated cycloid dot. May introduce the per-cell
  inertial decomposition.
- **Phase 3 — cadence.** Move the field build to a slow 6-hourly GitLab schedule;
  fast Pages build fetches the artifact (Job Artifacts API +
  `search_recent_successful_pipelines`, recompute-on-miss, `expire_in`).
- **Phase 4 — docs.** Update `docs/forecast.md` (frozen → time-dependent; what NI
  is now included and from where) and `docs/features.md`. Move this plan to
  `plans/done/`, update `ROADMAP.md`.

## Out of scope

- The Pollard–Millard slab — parked in [inertial_slab_model.md](inertial_slab_model.md).
- SMOC / tidal CMEMS (no inertial gain here; would double-count tides + Stokes).
- A standalone climatological NI map.
