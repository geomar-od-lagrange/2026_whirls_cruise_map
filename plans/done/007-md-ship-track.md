> Implemented. See [docs/ship.md](../../docs/ship.md).

# 007 — R/V Marion Dufresne live ship track

Add a live position + track for R/V Marion Dufresne, fetched client-side from
the French Oceanographic Fleet (Flotte Océanographique Française) localisation
API — the same source the IPSL WHIRLS operational map uses for its "platform
positions real-time" button.

## Source

```
GET https://localisation.flotteoceanographique.fr/api/v2/vessels/MD/positions
      ?startDate=<ISO>&endDate=<ISO>
```

- `MD` = Marion Dufresne. `…/vessels` lists the fleet; `…/vessels/MD` is metadata.
- No date params → the single latest fix.
- Response: a flat array, ~10-min cadence, of
  `{lat, lon, data:{seatemp, airtemp, pressure, truewinddir, truewindspeed}, date}`.
- Open CORS (`access-control-allow-origin: *`), no auth — fetchable straight from
  a static page.

## Decision: fetch client-side, live (not a build artifact)

Everything else on the map is a build artifact under `site/data/`. The ship is
the exception: it is fetched live in `app.js`, so the marker tracks the vessel in
real time even between cron rebuilds (the eventual Pages deploy — ROADMAP's
"Automation & hosting" — has no server at view-time, so a build artifact would
freeze the ship between runs). The API is CORS-open specifically to allow this; the IPSL map does the
same. Trade-off accepted: this one layer has a runtime third-party dependency —
mitigated by the existing graceful-fetch pattern (a failed fetch omits the layer,
never blanks the map).

## Plan

Client only — no changes to the Python build.

1. **`app.js` — fetch.** `SHIP` config (endpoint, `cruiseStart`, refresh
   interval, colours). `cruiseStart = 2026-06-24T00:00:00.000Z` matches the IPSL
   WHIRLS window start; `endDate` is "now". Helpers: `shipUrl()`, `fetchShip()`
   (optional/graceful, returns `[]` on failure).
2. **`app.js` — render.** `makeShipLayer()` returns a `featureGroup` holding a
   cased track polyline (white halo + dark core, legible on any basemap) and a
   distinct ship marker (dark disc + white ring + boat glyph, `divIcon`), plus
   `setPositions()`/`append()`/`lastDate()`. Own pane `ship` at z-index 660 (above
   the drifters pane). Marker popup carries the underway readout.
3. **`app.js` — live refresh.** Initial load fetches the full cruise window;
   `setInterval` then fetches only the incremental window since the last fix and
   appends (dedup by date), so polling stays cheap as the track grows.
4. **Sidebar.** A `#ship-panel` (top of the sidebar) mirroring the currents/FTLE
   panels: last-fix time + underway readout. `index.html` + `style.css`
   (`.ship-marker`, `.ship-row`).
5. **Layer control.** "R/V Marion Dufresne" overlay, on by default. The
   drifter-cluster fit is unchanged (the ship can be far offshore; including it
   would zoom the map out past the drifters it exists to show).

## Notes / non-goals

- **Agulhas II is not here.** The FOF API is French-fleet only; the South African
  R/V S.A. Agulhas II is absent. A second source is needed for it — out of scope.
- **Units.** Sea/air temp °C, pressure hPa, wind direction degrees are clear from
  the values; the wind-*speed* unit is not specified by the API — shown as a bare
  number, caveated in `docs/ship.md`. Do not assert kn vs m/s.
- **Heading-rotated marker** (from the last track segment) — possible later polish;
  v1 uses a static glyph.

When done: write `docs/ship.md`, move this plan to `plans/done/` with a pointer,
add a ROADMAP entry.
