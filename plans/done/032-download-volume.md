> Implemented. See [docs/deploy_tool.md](../../docs/deploy_tool.md)
> (gzip + 4 dp), [docs/currents.md](../../docs/currents.md) and
> [docs/vorticity.md](../../docs/vorticity.md) (discrete classes + lazy frames),
> and [docs/trajectories.md](../../docs/trajectories.md) (lazy true tracks).

# Plan 032 — Cut at-sea download volume

Implements issue #9 (measured baseline: ~2.95 MB first paint, ~4.90 MB
uncompressed per 1000-seed forecast) plus two follow-on levers measured
against the real artifacts: display-precision cropping and discrete
shading colormaps.

## Lever 1 — gzip `/api/forecast` (~4.90 MB → ~1.44 MB per deployment)

`src/whirls_cruise_map/_api.py`: add Starlette's `GZipMiddleware` to the
FastAPI app (`minimum_size=1024` so tiny responses like
`/api/forecast/limits` and error bodies skip the overhead). In-app rather
than gateway-side so every deployment shape (dev two-port flow, the plan-017
gateway, any future proxy) gets it without infra coupling, and the dev flow
measures what production ships.

Test (`tests/test_forecast_api.py`): a forecast POST with
`Accept-Encoding: gzip` returns `Content-Encoding: gzip` and the decoded
body equals the identity-encoding response; `/api/forecast/limits` stays
uncompressed (below `minimum_size`).

## Lever 2 — don't prefetch `tracks.geojson` while the Tracks layer is off (~0.69 MB)

`site/map/app.js` fetches `DATA.tracks` unconditionally at startup
(`main()`, ~line 2741) although the "True track" overlay defaults to off.
Make it lazy:

- At startup, do not fetch. The "True track" overlay entry passed to
  `buildInstrumentRows` starts with the glider track groups only (glider
  tracks ride in `gliders.geojson`, already fetched for the markers) and
  gains a `lazy` loader: an async () that fetches `tracks.geojson` once,
  builds `buildTrackGroups(...)`, and resolves the drifter groups.
- `buildInstrumentRows`: today `activeOverlays` drops overlays with empty
  `groups` (dead-checkbox avoidance); keep an overlay whose entry has a
  `lazy` loader even when its groups start empty. On the master checkbox's
  first tick, fire the loader once; on resolve, merge the groups into
  `overlay.groups` and call `sync()` so visibility reconciles through the
  existing path. Per-batch checkboxes need no change (`sync()` reads
  `overlay.groups` live).
- Failure keeps today's missing-artifact behaviour: loader resolves empty,
  the master row still governs glider tracks, nothing blanks.

## Lever 3 — frames on demand instead of idle prefetch (~1.6 MB untouched-slider savings)

Today the `+00h` speed WebP and flow grid load on the critical path and the
remaining 8 of each are prefetched via `requestIdleCallback`
(`app.js` ~2648 and ~2718) — so a metered viewer pays all 18 frames even if
the time slider is never touched. Move both prefetches from *idle* to *first
slider interaction*, mirroring the existing lazy ζ/f pattern (vorticity
frames prefetch on first shading selection):

- One-shot flag; on the slider's first `onChange`, kick off
  `prefetchFrames(meta.frames)` and `flowFrames.forEach(loadFlow)` in the
  background so subsequent scrubbing is smooth.
- Scrubbing to a not-yet-prefetched frame already works on demand
  (`imageOverlay.setUrl` fetches the image; `scrubFlow` awaits `loadFlow`),
  so the first scrub costs one frame up front and backfills the rest.

Untouched slider: 1 speed frame + 1 flow grid. First touch: the rest arrive
in the background.

## Lever 4 — crop displayed coordinates to 4 decimal places

What bounds useful precision, in the working area (~37.7°S):

- **map**: `maxZoom: 12` → Web-Mercator ground resolution
  `156543 · cos(lat) / 2¹²` ≈ **30 m/CSS-px** (≈15 m/device-px on a 2×
  display);
- **GPS**: drifter fix scatter is ~5–15 m;
- **currents**: the CMEMS field driving every forecast is 1/12° ≈ 9 km.

4 dp is ≤11 m — sub-pixel at max zoom, at the GPS noise floor, three orders
below the field. 5 dp (~1.1 m, the current forecast rounding and the source
feed's precision) buys nothing visible. Measured on the real artifacts
(gzip-9 on-the-wire):

| artifact | change | raw | gzipped |
|---|---|---:|---:|
| `forecast.geojson` (proxy for the `/api/forecast` payload) | 5 dp → 4 dp | −7% | **−33%** |
| `gliders.geojson` (coords currently unrounded, full float tails) | → 4 dp | −11% | −15% |
| `tracks.geojson` (coords already 5 dp; bulk is per-vertex properties) | → 4 dp | −1% | −6% |
| `inertial_field.json` | rounding | −0% | −0% (skip) |

Changes: `_forecast._COORD_NDIGITS` 5 → 4 (covers the API response and
`forecast/hindcast.geojson`; update its comment with the zoom/GPS/field
anchors above); round geometry coordinates to 4 dp in the `_geojson.py` and
`_gliders.py` emitters so every served coordinate obeys the same bound.
Combined with lever 1 the 1000-seed response lands at ~1 MB (−80% from
baseline). While there: `tracks.geojson` carries 13 stray unrounded
`U_speed_mps` values — find the emitting path and route it through the
existing `_round`.

## Lever 5 — discrete shading colormaps (~−60% per frame)

The speed / ζ/f WebPs are continuous cmocean ramps: ~256 unique colours,
poor spatial coherence, so lossless WebP can't exploit the large
constant-value regions. Quantizing the *existing* frames (no dither) as a
proxy for true field binning:

| frame | today | 8 bins | 12 bins | 16 bins |
|---|---:|---:|---:|---:|
| `speed_+00h.webp` | 87 KB | −68% | −61% | −43% |
| `vorticity_+00h.webp` | 117 KB | −65% | −60% | −60% |

At 12 bins the full frame sets drop ~0.70 MB → ~0.27 MB (speed) and
~0.95 MB → ~0.38 MB (ζ/f); the one frame on the critical path costs ~34 KB
instead of 87 KB. True binning (quantize the normalized field value before
the colormap lookup) should compress at least as well as this proxy. Binned
classes also make the map ↔ legend lookup quantitative — standard practice
for oceanographic charts. The cost is banding; deliberate, and reversible
via one constant.

Changes (build-side, keep the cmocean palettes — no new colours):

- `_currents.to_speed_frames` and `_vorticity.to_rgba`: quantize the
  normalized value to `N_BINS = 12` bin midpoints before the colormap call.
  ζ/f keeps an even bin count so zero stays a bin *edge* (6 bins per sign,
  the diverging midpoint separates the two middle classes).
- Emit the 12 bin colours as `colorbar` in the metas; the client legend
  (`renderCurrentsInfo` / `renderVorticityInfo`) renders hard stops
  (`c₀ 0%, c₀ 8.33%, c₁ 8.33%, …`) so the legend shows the same classes as
  the raster.

## Docs

Update the passages describing eager loading / idle prefetch and the
shading rendering (grep docs/ for prefetch, idle, tracks.geojson, lossless:
`docs/currents.md`, `docs/controls.md`, `docs/trajectories.md`,
`docs/vorticity.md`, `docs/deploy_tool.md` are candidates) to
describe the lazy/binned behaviour as what *is*. Note the gzip contract and
the 4 dp coordinate bound in the API doc.

## Verification

- `pixi run test` (gzip + rounding tests included).
- Frontend fresh-load fetch set, dependency-free: serve `site/`
  (`python -m http.server`), point headless Chrome
  (`--headless=new --virtual-time-budget`) at `/map/`, and assert on the
  server's access log: no `tracks.geojson`, exactly one `currents_*.json`
  and one `speed_*.webp` (was 9 + 9 + tracks). Give the idle window time to
  prove the prefetch is really gone.
- The interactive paths (first "True track" toggle fetches tracks once;
  first slider move backfills frames; scrub-to-unfetched-frame still
  renders) are asserted via CDP `Runtime.evaluate` if a websocket client is
  available in the env, else covered by review + a manual checklist in the
  MR description.
- Lever 5: regenerate one frame from the cached CMEMS window if
  `site/map/data/_cache/forecast_window.nc` is present (else a synthetic
  field) and report the actual binned WebP size against the table above.
