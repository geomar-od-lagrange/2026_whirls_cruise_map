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
7. Automation & hosting — GitHub Actions cron rebuild and Pages deploy; CMEMS
   credentials via repository secrets.