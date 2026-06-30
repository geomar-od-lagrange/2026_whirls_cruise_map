# Roadmap

Ordered intent. Each open item links a plan in this directory; implemented plans
move to `done/` and gain a `docs/` counterpart.

1. [Static cruise map — MVP](001-static-cruise-map.md) — fetch the drifter
   share, derive tracks, render latest positions + trajectories + a first CMEMS
   surface-currents overlay in a local Leaflet site. **Done** (built + reviewed;
   docs/ + move to done/ pending).
2. [Currents inclusion](002-currents.md) — speed shading + monochrome flow trails
   from one CMEMS field. **Done** (built + reviewed; pan/zoom fix pending confirm).
3. [FTLE overlay + trail-velocity scaling](003-ftle-and-trail-scaling.md) — red
   alpha-ramped LCS ridges from the SPASSO FTLE product, plus sqrt-scaling of the
   particle-animation velocity. **Planning.**
4. [FTLE vectorization](done/004-ftle-vectorize.md) — replace the FTLE raster
   with a simplified iso-FTLE line contour (GeoJSON), smaller and crisp, no manual
   Mercator warp. **Done** ([docs/ftle.md](../docs/ftle.md)).
5. [FTLE latitude correction](done/005-ftle-lat-correction.md) — the SPASSO FTLE
   field renders ~0.13° too far north (a rigid, latitude-only product-registration
   shift, confirmed via orientation-resolved land-mask registration; currents/
   shading verified clean). Applied a documented empirical `-0.13°` lat correction
   in `fetch_ftle`. **Done** ([docs/ftle.md](../docs/ftle.md)); optional ocean clip
   for residual coastal filaments → BACKLOG.
6. [Batch filter](done/006-batch-filter.md) — a data-driven checkbox control to
   show/hide drifters by deployment batch, picking up new batches from the data
   automatically. **Done** ([docs/batches.md](../docs/batches.md)). Batch *source*
   (which drifter belongs to which deployment batch) still TBD during the cruise;
   per-batch colour deferred to the `styleForBatch` seam.
7. [Ship track](done/007-md-ship-track.md) — live R/V Marion Dufresne position
   and track, fetched client-side from the Flotte Océanographique Française
   localisation API (the IPSL WHIRLS "platform positions" source), polled every
   5 min. **Done** ([docs/ship.md](../docs/ship.md)). Agulhas II is not in that
   API and needs a separate source.
8. [Intermediate positions](done/008-intermediate-positions.md) — a dot at every
   fix along each drifter and ship track, each carrying the latest-position
   popup; drifter popups gained reported + derived velocity (knots and m/s); the
   ship heading row is always shown (NA when slow); trajectories are coupled to
   the batch filter. **Done** ([docs/trajectories.md](../docs/trajectories.md),
   [docs/ship.md](../docs/ship.md)).
9. [Drift forecast](done/009-drifter-forecast.md) — per-drifter current-advection
   track to 6 h (RK4 through the frozen CMEMS field, NaN land so it stops at the
   coast), dashed line with 1/3/6 h dots, toggled per-batch like trajectories.
   **Done** ([docs/forecast.md](../docs/forecast.md)). Frozen single field and
   surface-current-only; time-varying multi-step advection → BACKLOG.
10. Automation & hosting — GitHub Actions cron rebuild and Pages deploy; CMEMS
    credentials via repository secrets.