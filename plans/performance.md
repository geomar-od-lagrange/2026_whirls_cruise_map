# At-sea performance

## Why

The map is used from R/V Marion Dufresne and R/V Agulhas II over poor,
high-latency satellite links (VSAT-class). A read-only profiling pass measured
the live Pages site against that constraint. This plan records what was measured
and the candidate optimizations — ranked, with trade-offs — so we can pick work
deliberately. **Nothing here is implemented yet**; it is intent, not a changelog.

Measurement method: `curl` (headers + on-wire bytes with `Accept-Encoding:
gzip,br`) corroborated by a headless-Chrome net-log, against the build stamped
`2026-07-01T08:38Z`. On-wire figures are the bytes that actually cross the link.

## What was measured

Cold load for a first-time visitor: **≈ 1.46 MB on the wire across ~48 requests
over 4 hosts** (≈ 2.9 MB uncompressed). Where the wire bytes go:

| Resource | Host | Wire | Note |
|---|---|---:|---|
| `currents.json` | pages | 437 KB (30%) | float arrays, gzip only 2.4×; flow overlay **on by default** |
| `speed.png` | pages | 315 KB (22%) | PNG, uncompressible; shading **on by default** |
| OSM tiles ×30 | openstreetmap | 306 KB (21%) | **273 KB is a z12 set that is fetched then discarded** |
| ship API (1st poll) | flotteoceanographique | 182 KB (12%) | **uncompressed, `no-store`, third-party, grows with cruise** |
| `tracks.geojson` | pages | 74 KB (5%) | 533 KB raw; **fetched though its layer is OFF by default** |
| `ftle.geojson` | pages | 56 KB | on by default |
| Leaflet + velocity libs | jsdelivr | 53 KB | brotli, cached immutable 1 yr |
| everything else | pages | < 20 KB | html/css/js/meta/awaiting/forecast |

Off-origin totals: **≈ 541 KB / 37% of cold-load bytes and 35 of 48 requests**
live on hosts the project does not control (jsdelivr, OSM, Flotte Océanographique).

Modeled time-to-usable (transfer + per-host handshakes + the serial-await
latency floor; assumptions stated inline):

| Link | Bandwidth / RTT | First usable map | Fully loaded |
|---|---|---:|---:|
| Constrained VSAT | 0.5 Mbit/s, 700 ms | ~9 s | ~33 s |
| Moderate maritime | 2 Mbit/s, 600 ms | ~7 s | ~14 s |
| Good Ku/Ka | 5 Mbit/s, 600 ms | ~7 s | ~11 s |

Below ~2 Mbit/s the cold load is **bandwidth-bound** (currents + speed.png +
wasted tiles dominate); above it the load is **RTT-bound**, where request
structure matters more than bytes.

## Already sound — preserve

The feared failure mode is absent: GitHub Pages serves the GeoJSON gzipped
(`application/geo+json` + `content-encoding: gzip`, e.g. tracks 533 KB → 74 KB).
The client is also well-defended against partial failure: `fetchJSON(...,
{optional:true})` swallows any layer error to `null` so a missing/unreachable
layer never blanks the map; the ship poll is not awaited and is suppressed while
the tab is hidden. Keep all of this.

The only two hard requirements are `latest.geojson` (non-optional; its failure
shows the "Could not load map data" fallback) and Leaflet itself — see issue 1.

## Ranked opportunities

Ordered biggest-impact / lowest-risk first. Numbers are the measured wire bytes.

1. **Leaflet has no local fallback (total-failure risk).** `app.js` references
   `L` synchronously, so if `cdn.jsdelivr.net` is slow or blocked at sea the map
   **fails completely** (blank + console error). Self-host Leaflet +
   leaflet-velocity on the pages origin, or add a local fallback. Trade-off: lose
   jsdelivr's 1-yr immutable cache, but same-origin brotli is comparable and this
   is the highest-blast-radius dependency. Low risk, high value.
2. **Eight same-origin data fetches run strictly serially** (`await` one at a
   time) → an ~**8 × RTT** floor (~5.6 s at 700 ms) regardless of bandwidth.
   Collapse with `Promise.all` to ~1 RTT. The single biggest structural win on
   high-RTT links; low risk.
3. **Off-by-default layers are fetched eagerly.** `tracks.geojson` (74 KB wire /
   533 KB raw) and `forecast.geojson` (5 KB) download on every load though both
   toggles start off. Defer the fetch until the layer is switched on. Low risk.
4. **273 KB of z12 basemap tiles fetched then discarded.** The map opens at the
   fallback view (zoom 12, Table Bay), pulls a dense urban z12 tile set, then
   re-fits to ~zoom 7 (only 32 KB kept) after `latest.geojson` arrives. Fit
   before the first tile request (or lower `FALLBACK_ZOOM`), and/or make **Esri
   Ocean the default** basemap (lighter ~5.5 KB JPEG tiles, apter for an ocean
   chart). Low risk.
5. **`currents.json` 437 KB (30%)** and **`speed.png` 315 KB (22%)** are the two
   heaviest unavoidable transfers for the default view, both feeding the currents
   visualisation and both on by default. Options: coarser grid / quantization
   (build-side), or lazy-load the flow overlay behind its toggle and default to
   shading-only. Trade-off: visual fidelity vs bytes; needs build changes.
6. **Ship API: 182 KB, uncompressed, uncacheable, third-party, growing.** The
   first poll pulls the whole cruise window though only the latest fix + recent
   tail feed the readout. Fetch a shorter initial window with lazy back-fill.
   Server-side compression/caching is out of our control.
7. **Cache max-age (10 min) equals the rebuild cadence.** Every meaningful repeat
   visit re-pays the full ~890 KB of same-origin data, and the rarely-changing
   shell (HTML/CSS/JS) also revalidates every 10 min. GitHub Pages gives limited
   header control, so this is a note more than an action.
8. **`favicon.ico` → 404 returning a 9.4 KB HTML error page** on every cold load.
   Add a small favicon. Trivial.

## Scheduling & freshness reliability (cron)

Related, because "near-live at sea" depends on both payload size *and* how often
the build actually runs. The `*/10` Pages rebuild cron is **not firing reliably**
— confirmed not a config bug (workflow `active`, Actions enabled, valid schedule
on the default branch), but a property of GitHub's scheduler:

- GitHub docs: the `schedule` event "can be delayed during periods of high loads…
  High load times include the start of every hour. If the load is sufficiently
  high enough, some queued jobs may be dropped." Every `*/10` schedule includes
  the `:00` top-of-hour slot. First runs for a newly-added cron also commonly lag
  30–60+ min.

Options, if guaranteed cadence matters:

- **Accept best-effort native cron** — simplest; freshness is "eventually, mostly."
- **External trigger** — a cron on a GEOMAR host (or a hosted cron service)
  calling `POST /repos/.../actions/workflows/deploy-pages.yml/dispatches` with a
  fine-grained PAT on a fixed interval. Reliable, but adds an external dependency
  and a token to manage.

Also weigh whether frequent rebuilds even help: the upstream sources update on
their own cadences (CMEMS surface currents ~6-hourly; the drifter share and ship
API ~5–10 min), so sub-hourly rebuilds only refresh the drifter/ship-adjacent
data — the currents/FTLE layers change far less often. This bounds the value of a
tight cron and argues against paying the ~1.5 MB rebuild cost every 10 min.

## Suggested sequencing

Cheap, low-risk, no data-format change first: **2 (parallel fetches), 3 (lazy
off-by-default layers), 4 (fit-before-tiles / Esri default), 8 (favicon), 1
(self-host Leaflet).** These alone remove the total-failure risk, the ~5 s serial
floor, ~350 KB of wasted/eager bytes, and one of four host handshakes.

Heavier, deferred: **5 (currents/speed resolution)** and **6 (ship windowing)** —
build-side changes that trade visual fidelity or add client logic; take them only
if the cheap wins don't get first-usable-map comfortably under target on the
constrained-VSAT profile.
