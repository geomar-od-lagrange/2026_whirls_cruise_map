# Near-inertial forecast/hindcast via a time-dependent current field

**Status: Phases 0–2 done; Phases 3 (cadence) and 4 (docs) open.**
**Decision: no slab model** — the near-inertial (NI) oscillation is already
present in the CMEMS current field; the fix is to stop advecting through a
*frozen* snapshot and advect through a **time-dependent** field instead. The
Pollard–Millard slab was later tested against winds and drifters and **dropped
permanently** ([done/inertial_slab_model.md](done/inertial_slab_model.md));
the amplitude-gain question is **resolved to no gain**
([done/013-inertial-gain-generalization.md](done/013-inertial-gain-generalization.md)).

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
- **The only open question was realism — answered in two steps.** Phase 0
  (below) found CMEMS gets the inertial phase right but only ~0.31–0.45 of the
  amplitude at the D2 site, suggesting a scalar gain. The broadened 23-drifter
  survey
  ([done/013-inertial-gain-generalization.md](done/013-inertial-gain-generalization.md))
  then showed the ratio is site- and time-dependent (deployment medians
  0.40–0.66, factor-~3 spread window-to-window) with no driver usable at
  forecast time — so **no gain is applied**, and the slab (tested there too)
  is dropped: it is in phase with CMEMS's own wind-forced NI, so adding it
  would double-count.

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

- **Phase 0 — validate. DONE** (`tmp_ni/phase0_compare.py`, `phase0_inertial.py`).
  Seeded a particle at each Deployment-2 drifter's observed position at
  2026-07-01T23:33:22Z and advected ±12 h through the hourly field vs the observed
  track (~20 h overlap, ~1 inertial period). Result: mean drift captured
  (separations 2–4 km over ~14 km); **inertial phase and rotation sense correct**
  (mean-removed residuals trace closed inertial circles, the simulated one
  concentric inside the observed); **amplitude muted to ~0.31–0.45** of observed
  (the true tracks' sharp corners — D-559's deep cusp — are smoothed). The drifters
  are drogued *below* the surface the model samples, where NI is weaker, so the
  deficit is real, not a depth artifact. **Decision: not the slab.** Phase being
  right suggested the gap might close with a single scalar gain (~2.3) on the
  CMEMS-derived inertial component — with the caveat: 3 drifters / one 20 h
  window / one place. The broadened survey (013) confirmed the caveat, not the
  gain: the ~2.3 came from the most under-energized site/time sampled; across
  all 23 drifters the median sim/obs ratio is 0.65 and no scalar or
  parameterized gain generalizes.
- **Phase 1 — time-dependent CMEMS advection. DONE** (commit on
  `near-inertial-forecast`). Windowed hourly `subset` in `_currents.fetch_field_window`
  (keeps `time`); bilinear-space + linear-time in `_forecast._Field`; forecast +
  hindcast advect through the window. This is the "more correct than frozen"
  feature and already carries ~40 % of the inertial excursion with correct phase.
- **Phase 2 — visualization + inertial gain. DONE.** The per-cell
  `(mean u,v, amplitude A, phase φ)` decomposition (`_inertial.py`), the
  inertial-amplitude overlay (`inertial.png` + `inertial_meta.json`, toggling
  exclusively against the speed shading) and the animated ±6 h dot walking
  each forecast/hindcast polyline shipped. The gain question is **resolved:
  Branch C, no gain**
  ([done/013-inertial-gain-generalization.md](done/013-inertial-gain-generalization.md));
  the gain is exposed as a parameter defaulting to 1.0 (`_inertial.GAIN`) —
  the seam where a validated gain would plug in (the advection still reads the
  raw hourly window; with gain 1.0 the two are equivalent). The animated NI
  *flow-trail* reconstruction (rebuilding the leaflet-velocity background from
  mean + A + φ) is deferred.
- **Phase 3 — cadence.** Move the field build to a slow 6-hourly GitLab schedule;
  fast Pages build fetches the artifact (Job Artifacts API +
  `search_recent_successful_pipelines`, recompute-on-miss, `expire_in`).
- **Phase 4 — docs.** Update `docs/forecast.md` (frozen → time-dependent; what NI
  is now included and from where) and `docs/features.md`. Move this plan to
  `plans/done/`, update `ROADMAP.md`.

## Out of scope

- The Pollard–Millard slab — tested and dropped permanently
  ([done/inertial_slab_model.md](done/inertial_slab_model.md)).
- SMOC / tidal CMEMS (no inertial gain here; would double-count tides + Stokes).
- A standalone climatological NI map.
