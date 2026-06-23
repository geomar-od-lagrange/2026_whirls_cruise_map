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
5. Batch selection — controls to filter/highlight drifters by deployment batch.
   Blocked on a batch source (IDs supplied per deployment).
6. Automation & hosting — GitHub Actions cron rebuild and Pages deploy; CMEMS
   credentials via repository secrets.