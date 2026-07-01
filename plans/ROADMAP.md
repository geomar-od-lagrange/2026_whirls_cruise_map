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
   particle-animation velocity. The trail-velocity scaling stays; **the FTLE
   overlay was later dropped — see 13.**
4. [FTLE vectorization](done/004-ftle-vectorize.md) — replaced the FTLE raster
   with a simplified iso-FTLE line contour (GeoJSON). **Dropped — see 13.**
5. [FTLE latitude correction](done/005-ftle-lat-correction.md) — corrected the
   SPASSO FTLE field's ~0.13° northward product-registration shift. **Dropped —
   see 13.**
6. [Batch filter](done/006-batch-filter.md) — a data-driven checkbox control to
   show/hide drifters by deployment batch, picking up new batches from the data
   automatically. **Done** ([docs/batches.md](../docs/batches.md)). Batch *source*
   now wired: a curated `deployments.json` roster (`batch → [D_number]`) applied
   in `_clean.py`; Deployment 1 (20 drifters) and Deployment 2 (3 drifters) are
   the real batches so far. Per-batch colour landed at the `styleForBatch` seam
   (staged grey; per-deployment vivid colours — D1 blue, D2 teal). The
   deployed-and-drifting criterion is documented in
   [docs/batches.md](../docs/batches.md).
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
   coast), solid line with 1/3/6 h dots, toggled per-batch like trajectories.
   **Done** ([docs/forecast.md](../docs/forecast.md)). Frozen single field and
   surface-current-only; time-varying multi-step advection → BACKLOG.
10. Automation & hosting — GitLab CI builds the site and publishes it to GitLab
    Pages on `git.geomar.de` (push, manual, or scheduled pipelines); CMEMS
    credentials via masked CI/CD variables. **Done**
    ([docs/deploy.md](../docs/deploy.md)). (GitHub's native Actions scheduler
    never fired for this repo across three re-registration attempts, so GitHub
    Pages was dropped and GitLab is the sole deploy.)
11. [At-sea performance](performance.md) — profiled the deployed site for
    low-bandwidth / high-latency (VSAT) use; ranked opportunities (parallelize
    the data fetches, self-host Leaflet, lazy-load off-by-default layers,
    fit-before-tiles / Esri default, lighter currents payload, ship-API
    windowing). **Investigated, not yet acted on** (per request — investigate
    only).
12. [Truncate tracks at deployment](done/010-truncate-tracks-at-deployment.md) —
    detect each drifter's detachment from the vessel (build-time, distance to the
    R/V Marion Dufresne track) and truncate its trajectory there, so the layer
    (renamed **True track**) shows only the free drift. **Done**
    ([docs/trajectories.md](../docs/trajectories.md)).
13. Final polishing — dropped the **FTLE** overlay (build step, `_ftle`, client
    layer, sidebar panel, docs) as no longer wanted; dropped the **Esri Ocean**
    basemap and the base-layer selector (OpenStreetMap is the sole basemap); and
    trimmed the forecast/hindcast sidebar explainers to bare facts (no line-style
    legend, no trust guidance). **Done.**