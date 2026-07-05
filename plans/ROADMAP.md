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
   5 min. **Done** ([docs/ship.md](../docs/ship.md)). The **R/V S.A. Agulhas II**
   is added from its own source — a non-CORS IPSL THREDDS CSV, so baked at build
   time rather than fetched live — sharing the same ship renderer
   ([015](done/015-agulhas-ship-track.md), [docs/ship.md](../docs/ship.md)).
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
   surface-current-only at the time; time-varying advection landed later — see 15.
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
14. [Glider instruments](done/011-gliders.md) — the **XSPAR** spar buoy and the
    **seagliders**, auto-discovered from the WHIRLS THREDDS catalogs and rendered
    alongside the drifters: latest diamond markers + tracks, folded into the
    batch control (renamed **Instruments**; batches relabelled "Drifter batch N",
    "Drifter pre"), with true tracks in the shared orange and per-instrument
    current-advection forecast/hindcast extended to cover them. Forecast/hindcast
    sidebar explainers merged into one panel. **Done**
    ([docs/gliders.md](../docs/gliders.md)).
15. [Near-inertial forecast/hindcast](012-near-inertial-forecast.md) — the drift
    forecast/hindcast advects through a **time-dependent hourly CMEMS field**
    instead of a frozen snapshot, so the path curls into the near-inertial loop the
    model carries (the drifters' visible corners). **Phases 1–2 resolved** (built +
    validated; [docs/forecast.md](../docs/forecast.md)): time-dependent advection,
    plus the per-cell `(mean, A, φ)` decomposition as a tested library module
    (`_inertial.py`, no build artifact). The inertial-amplitude overlay and the
    animated ±6 h dot were built and then **dropped by decision after review**.
    The amplitude gain resolved to **no gain** (see 16), exposed as a parameter
    defaulting to 1.0; the wind slab was tested and dropped
    ([done/inertial_slab_model.md](done/inertial_slab_model.md)). **Open:**
    Phase 3 (slow-tier cadence + artifact cache).
16. [Inertial-gain generalization](done/013-inertial-gain-generalization.md) —
    does one scalar amplitude gain hold across deployments (D1 has corners too),
    space, and time, or does it need parameterizing / dropping? **Resolved
    (Branch C — no gain):** across all 23 drifters the sim/obs amplitude ratio
    spreads ~3× (deployment medians 0.66 / 0.40; phase right) with no driver
    usable at forecast time, so the un-gained field ships and the gain stays a
    parameter defaulting to 1.0 (`_inertial.GAIN`).
17. [Normalized-vorticity overlay](done/016-vorticity-overlay.md) — a toggleable
    ζ/f (Rossby number) raster derived from the same single-time CMEMS field as the
    speed shading, so cyclonic (+) / anticyclonic (−) eddies read as opposite-signed
    lobes. Signed field → diverging map + symmetric legend; off by default. **Done**
    ([docs/vorticity.md](../docs/vorticity.md)).
18. [whirlsview.geomar.de on OpenShift](017-whirlsview-openshift.md) — host the
    archetypes viewer (`/archetypes`), this repo's map (`/map`), and cleaned
    drifter/glider dataset downloads (`/data`) under one hostname on the same
    cluster as `2026_whirls_cruise_prep`, borrowing its `deploy/viewer/` pattern.
    Net-new vs. that pattern: in-cluster **CronJob** rebuilds on a **fast**
    (positions/tracks/gliders/Agulhas, ~10 min, no creds) and **slow** (CMEMS
    overlays, ~6 h, needs Copernicus login) tier; a small unauthenticated gateway
    nginx fronts one Route (`/`→`/map/`), with auth kept only on `/archetypes` and
    `/map`+`/data` public; TLS/DNS owned by the OC admins. Stays a two-repo split
    by lineage (map here, viewer in prep). **Exploration only — not yet acted on.**
19. [Ingest → derive: the `/data` seam](done/018-ingest-derive-data-seam.md) — the
    pipeline-internals counterpart to 18. Split `build.py` into **ingest** (fetch
    + clean all instrument/ship tracks into human-inspectable **CSVs** under
    `/data/`) and **derive** (read those tables back to build the map GeoJSON and
    the CMEMS overlays). `/data` becomes the durable **seam** — download product
    *and* build input at once — which re-cuts 18's fast/slow tiers into
    ingest / derive-fast (egress-free) / derive-slow (CMEMS). Subsumes the
    backlog "Track DB parquet cache"; gives "GPS despike at ingestion" a visible
    home. **Done** ([docs/data.md](../docs/data.md), [docs/deploy.md](../docs/deploy.md)):
    the map now serves at `/map/` (root redirects there) with cleaned + raw
    dataset CSVs published at `/data/`. OpenShift consumption (CronJobs, `/data`
    backend) stays with 18.
20. [WHIRLS floats](done/019-whirls-floats.md) — the profiling **floats** the
    operational map gained as a new *FLOAT* type (UGOT `65a0`, SOTON `6594`),
    rendered here alongside the gliders. They live under the same THREDDS
    `GLIDERS` tree; we ingest the per-institution
    `mr_float_<inst>_positions.csv` files (fresher than the folder's aggregate
    `floats_track.csv`, which is skipped) and split each by its `filename`-column
    id (`_gliders.fetch_float_sources` / `parse_float_source`), since float
    identity isn't in the file name. Floats then ride the whole glider-group
    pipeline unchanged as `platform_type` `float` (purple **Floats** instrument
    row). **Done** ([docs/gliders.md](../docs/gliders.md),
    [docs/data.md](../docs/data.md)).