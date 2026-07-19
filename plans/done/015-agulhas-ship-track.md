> Implemented. See [docs/ship.md](../../docs/ship.md).

# 015 — Add the R/V S.A. Agulhas II ship track

Add the second cruise vessel, the South African **R/V S.A. Agulhas II**, as a
track + marker layer beside the Marion Dufresne. The header and subtitle already
name both vessels; today only the Marion Dufresne is drawn (see docs/ship.md).

## The source, and why it forces a different path than the MD

The Marion Dufresne is fetched **live in the browser** from the Flotte
Océanographique Française API, which is CORS-open (`Access-Control-Allow-Origin:
*`). That is the whole reason it can be a client-side live layer.

The Agulhas is published by IPSL on their WHIRLS THREDDS server:

```
https://thredds-x.ipsl.fr/thredds/fileServer/WHIRLS/OBSERVATIONS/SHIPS/agulhas_positions.csv
```

Two facts settle the architecture:

1. **No CORS.** The THREDDS fileServer returns `Access-Control-Allow-Methods` and
   `-Headers` but **no `Access-Control-Allow-Origin`**, so a browser cannot read
   it cross-origin. A client-side live fetch — the MD pattern — would be blocked.
2. **It is itself a scrape.** The CSV is an hourly scrape of myshiptracking.com
   (`source_url` column, `scraped_at_utc` ~hourly), not a realtime feed. So there
   is little freshness to lose by not fetching it live.

Therefore the Agulhas is **baked at build time**, like every other layer except
the MD (drifters, gliders, currents). The Python build fetches the CSV
server-side (no CORS constraint) and writes `site/data/agulhas.json`; the client
loads that same-origin artifact and renders it with the *same* ship renderer as
the MD.

## CSV shape

```
scraped_at_utc,reported_at,lat,lon,speed_kn,course_deg,status,area,source_url
2026-07-03T09:29:34Z,2026-07-03 08:47,-35.59410,15.37993,6.9,268,START Moving,SW OF CAPE TOWN,https://...
2026-07-03T10:30:01Z,2026-07-03 10:00,-35.50905,15.55999,,284,STOP Moving,SW OF CAPE TOWN,https://...
```

- `reported_at` is `YYYY-MM-DD HH:MM`, no timezone → treated as **UTC** (the CSV's
  own `scraped_at_utc` is UTC and the whole app is UTC).
- `speed_kn` / `course_deg` are **reported** (SOG/COG) — unlike the MD, which
  carries none and derives them from track segments. `speed_kn` can be empty.
- `status` (`START Moving` / `STOP Moving`) and `area` are free text.
- No met data (no sea/air temp, pressure, wind) — the MD's underway readout has
  no Agulhas equivalent.

## Build side

New module `_agulhas.py`, mirroring `_gliders.py`'s best-effort style:

- `fetch_positions() -> list[dict]`: GET the CSV, parse by header name, return
  time-sorted fix dicts `{date, lat, lon, speed_kn, course_deg, status, area}`
  (`date` ISO-8601 UTC `…Z`; `speed_kn`/`course_deg` `float | None`). Any failure
  (dead host, bad row) is swallowed → `[]`.

`build.py`: a best-effort step writing `site/data/agulhas.json` — a **plain JSON
array of fix dicts**, deliberately the same array-of-fixes shape the live MD API
returns, so both feed the identical client ship renderer with no conversion. (Not
a GeoJSON LineString: the ship renderer is fix-array-based, not geojson-based;
a plain array is the zero-shim path.) An empty list still writes `[]` so the
client's optional fetch is uniform. Failure logs a WARNING and skips the file,
like the glider/currents steps.

The build-time deployment-detection snapshot (`_ship.fetch_track`) stays
**MD-only** — drifters detach from the Marion Dufresne, not the Agulhas.

## Client side — generalise the ship renderer over a vessel spec

`app.js` today hard-codes the MD everywhere (`SHIP`, `shipRows`,
`shipPopupHtml`, `renderShipInfo`, `makeShipLayer`). Reshape it to a **vessel
spec** so one renderer serves both:

```js
const VESSELS = {
  md:      { name, source, trackColor:"#1a1a1a", haloColor:"#fff", markerColor:"#1a1a1a",
             live:true,  rows(p, prev){…derived motion + met…} },
  agulhas: { name, source, trackColor:"#9b1c31", haloColor:"#fff", markerColor:"#9b1c31",
             live:false, rows(p){…reported speed/course + status + area…} },
};
```

- `makeShipLayer(vessel)` — parameterise colours, `shipIcon(vessel.markerColor)`
  (disc background set inline, overriding the `.ship-marker` CSS default), popup
  title `vessel.name`, and popup/readout rows via `vessel.rows`. Both vessels
  share the existing `shipTrack` / `ship` panes (both are ship tracks below the
  drifters, markers above). Keep `setPositions` / `append` / `lastDate` / `group`.
- Rows split by vessel: `mdRows(p, prev)` = today's `shipRows` (derived motion +
  met); `agulhasRows(p)` = Last fix / Position / Speed (reported, kn + m/s via
  `speedBoth`) / Course (reported, deg + compass) / Status / Area. Colour of the
  distinct crimson avoids clashes with MD black, drifter blue/teal, glider
  amber/sky.
- Distinct crimson `#9b1c31` reads apart from MD black, drifter blue/teal, glider
  amber/sky.

Wiring in `main()`:

- MD: unchanged live poll (now `makeShipLayer(VESSELS.md)`).
- Agulhas: fetch `./data/agulhas.json` (optional). If non-empty, `setPositions`,
  add the marker, and register the "R/V S.A. Agulhas II" overlay. Re-fetch on the
  same interval so a rebuild's new fixes appear without a page reload (the file is
  tiny; `setPositions` replace is fine). No fix ⇒ no overlay, no dead toggle —
  same contract as the MD.

Sidebar: give the Agulhas its own `#ship-panel`-style section (shared `.ship-row`
/ `.hint` CSS, no new rules). `renderShipInfo(vessel, p, motion)` targets
per-vessel element ids. The Agulhas hint attributes the source
(myshiptracking.com via IPSL WHIRLS).

## Docs

- Rewrite `docs/ship.md`: it becomes "the two vessels". Document the MD (live,
  CORS-open) vs Agulhas (baked, no-CORS, hourly scrape) split and the reported-
  vs-derived motion difference. Drop the "Marion Dufresne only" limitation
  section.
- `docs/hosting.md`: the "one exception, fetched live" line now applies to the MD
  only; note the Agulhas is baked.
- Move this plan to `plans/done/` with a pointer once implemented; tick
  ROADMAP item 5.

## Out of scope

Higher-resolution or authoritative Agulhas positions (the CSV is a
myshiptracking scrape); an Agulhas underway/met readout (the source carries none).
