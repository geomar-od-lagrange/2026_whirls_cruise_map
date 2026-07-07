> Implemented — see [docs/interactive_forecast.md](../../docs/interactive_forecast.md).

# Simplify the deploy tool: one multi-click polyline

Supersedes the three PoC deploy tools (single-click forecast, jet-frame fence,
Z-box) with **one** tool: click a multi-segment path, drop drifters at equal
spacing along it, forecast every drop. The jet frame and the Z box were two
special-cased geometries with bespoke client math (`jetFrame`, `zCorners`) and
two server planners (`build_pattern`, `build_z_pattern`); a free polyline is the
general case that contains both — a Z is just four clicks — at a fraction of the
code.

## The four asks

1. **Multi-click polyline, equally-spaced drifters.** Click a path (2+ vertices,
   double-click to finish); drops are placed at equal arc-length along it, both
   ends included. Spacing between drops is a **km knob**.
2. **Density logic in the client.** The JS resamples the clicked polyline into
   drop positions and computes each drop's water-entry time; the API no longer
   knows about patterns, fences, or ship speed. It receives a **sequence of
   `(lon, lat, start)` seeds** and returns one forecast per seed.
3. **Ship speed is a knob** (knots). Drop *i* enters the water at
   `run_start + cum_km_i / (ship_speed · 1.852)` — the client computes this and
   bakes it into each seed's `start`.
4. **Forecast to 48 h** (was 12 h single-click / 24 h pattern).

## API: one batch endpoint

`POST /api/forecast` — body:

```json
{ "seeds": [{"lon": …, "lat": …, "start": "ISO-8601"}, …],
  "horizon_h": 48.0, "mark_step_h": 3.0 }
```

Returns a `FeatureCollection` of one `forecast` `LineString` per **in-window**
seed (`role`, `index`, `valid_time`, `marks`), plus run-level `properties`
(`run_start`, `horizon_h`, `n_seeds`, `forecasts`, `skipped`, `window`). The
drops and the ship track are **not** in the response — the client already has
them (it computed them), so the wire carries only what needs the field.

**Synced-`t0` dots are kept** (the scientific point, per 022): `run_start` is the
earliest seed time; every seed is integrated to the common wall-clock end
`run_start + horizon_h` and dotted at absolute run-relative marks
(`_run_relative_marks`), so one dot colour is the whole array at one instant. A
seed whose `start` is out of the field window (or at/after the common end) is
skipped and counted — a *plan* still stands even when the field doesn't cover it.

The RK4 integrator (`_forecast._advection_feature`), the field lifecycle
(`_load_window`/`_get_sampler`, disk cache, in-memory `_Field`) and the helpers
(`_iso`, `_parse_start`, `_run_relative_marks`) carry forward unchanged. Removed:
`_point_forecast`, `_assemble_plan`, `_deployment_plan`, `_z_plan`, the
`GET /api/forecast` + `/api/deployment` + `/api/deployment_z` endpoints, the
`_pattern` import, and the ship-speed constant (`_KN_TO_KMH`, now client-side).
`_HORIZON_H`/`_MARK_HOURS` stay — the parcels oracle (`_api_parcels.py`) imports
them for its own single-point +12 h validation. CORS opens to `POST` (a JSON body
triggers a preflight the old `GET`-only policy would reject).

`_pattern.py` and `tests/test_pattern.py` are **deleted** — the density logic they
held now lives in the client, and nothing else imports them.

## Client: one tool

`buildDeployTool` replaces `buildForecastTool` + `buildPatternTool` +
`buildZPatternTool`. Arm it, then:

- **click** adds a path vertex; a live preview (rubber-band to the cursor, no
  fetch) redraws the polyline, the equally-spaced drop discs it implies, and a
  label (`N drops · X km · ~Y h transit`) — so the spacing and ship-speed knobs
  read instantly.
- **double-click** finishes: the client resamples the path
  (`resamplePolyline`, the same cos-lat tangent-plane / equal-arc-length math the
  Z used), computes each drop's `start` from `run_start` + ship-speed offset,
  draws the ship track + drops, and POSTs the seeds; the returned forecast lines
  and synced dots are drawn over them. Leaflet fires two `click`s before a
  `dblclick`, so the near-duplicate tail vertex is dropped and
  `doubleClickZoom` is disabled while armed.

Knobs: **Drop spacing (km)**, **Ship speed (kn)**, **Forecast (h)** (default 48),
and a **Forecast drift** checkbox (draw geometry only when off). The synced-`t0`
plasma legend, `deployMarkColor`, and the green `deploy` pane/colour are kept; the
removed jet/Z helpers (`jetFrame`, `drawPreview`, `zCorners`, `drawZ*`,
`drawPattern`, `PATTERN_KIND_COLOR`, `PATTERN_API`, `Z_PATTERN_API`) go. `KM_PER_DEG`
moves up beside the API constants (the client owns the tangent-plane math now).

## Scope

Prototype only, same as 021/022 — no persistence, no build artifact, runs under
`pixi run serve` + `pixi run serve-api`, not in the deployed Pages build. The
`t0`-inversion open problem (022) is unchanged: this makes the forward tool
simpler, not the inverse solved.
