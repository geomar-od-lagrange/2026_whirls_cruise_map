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
   is added from its own source — an IPSL observations-portal CSV, baked at build
   time (an hourly scrape, so baking loses no freshness and adds resilience)
   rather than fetched live — sharing the same ship renderer
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
    **seagliders**, auto-discovered from the WHIRLS observations portal and rendered
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
    ([done/inertial_slab_model.md](done/inertial_slab_model.md)). **Phase 3
    (slow-tier field cache) landed** for the forecast API
    ([plans/018](done/018-forecast-window-pvc-cache.md)): the slow cron persists the
    hourly window to `data/_cache/` and the forecast API serves it (reload on
    mtime, no per-process CMEMS fetch). **Open:** re-advecting the fast tier's live
    positions off that cache for a fast-fresh forecast origin.
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
    `/map`+`/data` public; TLS/DNS owned by the OC admins. **Revised (2026-07-05):
    the gateway + OpenShift orchestration now live in a dedicated third repo,
    `oc_gateway`** (`git.geomar.de/2026-whirlscruise-lagrange/oc_gateway`) — map
    stays here, archetypes in prep — reversing 017's original "keep the gateway as
    a `deploy/gateway/` subdir here." **Exploration only — not yet acted on.**
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
    rendered here alongside the gliders. They live under the same `GLIDERS` tree
    on the observations portal; we ingest the per-institution
    `mr_float_<inst>_positions.csv` files (fresher than the folder's aggregate
    `floats_track.csv`, which is skipped) and split each by its `filename`-column
    id (`_gliders.fetch_float_sources` / `parse_float_source`), since float
    identity isn't in the file name. Floats then ride the whole glider-group
    pipeline unchanged as `platform_type` `float` (purple **Floats** instrument
    row). **Done** ([docs/gliders.md](../docs/gliders.md),
    [docs/data.md](../docs/data.md)). The portal later added two **UVP floats**
    (`6596`, `6597`) in a **second CSV schema**
    (`uvp_float_<id>_locations.csv`: `utc_time` time column, no `filename` column
    — identity in the file name), which `parse_float_source` now also reads
    ([025](done/025-uvp-float-schema.md), same docs); unmapped, they render under
    their raw id.
21. [Observations-portal CSV source](done/020-observations-portal-csv-source.md)
    — moved all IPSL CSV ingest (Agulhas ship + XSPAR/seagliders/floats) off the
    heavy, intermittently failing THREDDS server onto the operational centre's
    own **observations portal** (`observations.ipsl.fr/aeris/whirls`), a lighter,
    CORS-open Apache static host serving the identical files. Discovery changed
    from THREDDS `catalog.xml` to autoindex-HTML link scanning; fetchers now send
    an `Accept` header (the portal 403s without it). The switch also picked up the
    **SeaExplorer** glider (`seaexplorer.csv`, a mixed-delimiter/BOM/day-first
    dialect the parser now absorbs). Build-time bake kept for resilience. **Done**
    ([docs/gliders.md](../docs/gliders.md), [docs/ship.md](../docs/ship.md)).
22. [Interactive click-to-deploy forecast](done/021-interactive-forecast.md) —
    a live backend advects passive particles through the CMEMS window (the build's
    RK4 reused, seeded by the request) → green solid lines with colour-ramped dots;
    the run start is locked to the displayed field's time. A **two-endpoint** split
    (static `:8000` + forecast API `:8001`) chosen over shipping the field to the
    browser (transport + VSAT caching); the field stays in server memory, each
    response is a small FeatureCollection. Validated against **OceanParcels v4**
    (agree to metres; RK4 ~100× faster per particle, so RK4 is the engine and
    parcels the oracle). **Done** ([docs/deployment.md](../docs/deployment.md));
    a **dev PoC, not in the deployed Pages build**. The single-click +12 h tool this
    started as is **superseded by the one polyline Deploy tool + batch API in 23**.
    **Field-cache productionization landed** ([plans/018](done/018-forecast-window-pvc-cache.md)):
    the API serves the slow cron's persisted window (reload on mtime, no CMEMS
    fetch/creds/egress). **Open:** the remaining `oc_gateway` wiring (PVC mount,
    drop creds, unroute `data/_cache/`), tied to [017](017-whirlsview-openshift.md).
23. [Deploy tool: one polyline + batch API](done/023-simplify-deploy-polyline.md) —
    **one** multi-click Deploy tool supersedes both the single-click forecast (22)
    and the jet-fence / Z deployment patterns
    ([022](done/022-deployment-pattern.md)): click a ship path, double-click to
    finish, and the client lays drifter drops at **equal spacing** along it (drop
    spacing km + ship speed kn knobs) and forecasts each drift to **48 h**. A free
    polyline is the general case that contained both special-cased geometries (a Z
    is four clicks), so the bespoke client math and per-pattern endpoints are gone.
    The client owns the deployment geometry (resample + staggered water-entry times);
    the API is a **pure batch advector** — `POST /api/forecast` takes a sequence of
    `(lon, lat, start)` seeds and returns one advection LineString per in-window
    seed, dotted at **synced wall-clock times** (every drop integrated to a common
    run-end, dots at absolute run-relative marks, colour-ramped by that `t0`), so the
    array's shape at one instant reads off the map by colour. Array geometry follows
    Gui Novelli's
    [Lagrangian-Drifter-Array](https://github.com/guillaumenovelli/Lagrangian-Drifter-Array)
    MATLAB package (Novelli, G. (2026), Zenodo
    [10.5281/zenodo.20650545](https://doi.org/10.5281/zenodo.20650545)). **Done**
    ([docs/deployment.md](../docs/deployment.md)); dev PoC, not
    in the deployed Pages build. **Open — the `t0` inversion:** the reference time
    for a deformation / flow-map estimate is when the array is *complete* in the
    water, but staggered deployment means the clean array exists in the deploy frame,
    not at `t0`. Novelli's package designs the array forward in space and never
    advects it; the inverse — backward-advect an ideal `t0` configuration through the
    field to each drop's deploy time (a fixed point: drop time ↔ ship track ↔ drop
    positions) so deployment *lands* the array in that configuration — is ours to add.
24. [Forecast time slider](done/024-forecast-time-slider.md) — a bottom-centre
    **time slider** scrubs the speed and ζ/f shadings through the CMEMS forecast at
    12 h steps (−12 … now … +72 h, 8 frames). All frames slice **one** 6-hourly
    window (no extra download) on **one shared colour scale**; to keep an 8-frame
    slider affordable at full pixel detail on the at-sea link, each frame is a
    **lossless WebP** (~85 kB, half a PNG) and the client loads only the now frame
    up front (lighter than the old single raster), prefetching the rest lazily — ζ/f
    frames only once selected. Flow trails / near-inertial stay the now snapshot
    (**superseded by 27** — they now scrub too).
    **Done** ([docs/currents.md](../docs/currents.md)).
25. [Deploy tool: waypoint CSV export](done/025-deploy-waypoint-csv.md) — a
    **Download CSV** button on the Deploy tool exports the placed drops as a flat
    waypoint table (`deployment, drop, latitude, longitude, water_entry_utc,
    cum_km`) — one row per drop across every placed deployment. The drops *are* the
    deployment waypoints (position + staggered water-entry ETA), so the export is a
    client-side dump of geometry already owned: no API round-trip, no build
    artifact, wiped by **Clear**. **Done**
    ([docs/deployment.md](../docs/deployment.md)); dev PoC, like
    the rest of the Deploy tool.
26. [Controls dock + tidy sidebar](done/026-controls-dock.md) — on a 13" laptop the
    four top-right controls (Instruments, Currents, Ships, Deploy) stacked ~800 px,
    past the ~706 px of map height, overflowing into the time slider. Consolidated
    into **one collapsible tabbed dock** (Instruments / Currents / Ships / Deploy —
    one body open at a time, bounded footprint), tidied the sidebar (collapsible
    sections; speed / ζ/f legends shown only while their shading is active), and
    made the sidebar responsive (beside → bottom strip → dropped as the window
    narrows and shortens). **Done** ([docs/controls.md](../docs/controls.md)).
27. [Time-aware flow & near-inertial](done/027-time-aware-flow-and-inertial.md) —
    closes the slider gap left by 24: the **flow trails** and the **near-inertial
    animation** now share `displayedFieldTime` with the shadings, so scrubbing to
    +48 h moves the whole map, not just the two scalar rasters. Flow ships one
    leaflet-velocity grid per offset (`currents_±NNh.json` from the same window,
    values rounded to 4 dp so the now frame is *lighter* than the old single grid;
    the rest lazy-load); the near-inertial animation needs no new data — it anchors
    its analytic phase `amp·exp(i(phase − f·(T − t_ref)))` to the displayed instant,
    read live from the slider. **Done** ([docs/currents.md](../docs/currents.md),
    [docs/forecast.md](../docs/forecast.md)).
28. [Forecast cache + client retry](done/028-forecast-cache-and-retry.md) — a large
    placement can advect longer than the deployment gateway's 60 s network timeout,
    which cut the connection and lost the forecast. A FastAPI *sync* task keeps
    running past a client disconnect, so the API now caches each completed
    FeatureCollection keyed by `(request, field version)` with single-flight
    coalescing, and the deploy-tool client simply re-POSTs the identical body on a
    timeout signal (502/504/dropped connection) — the retry hits the warm cache or
    coalesces onto the still-running compute. Same POST, retried; no job IDs, no
    polling. The seed cap stays at **2000**: the retry/cache — not a lower cap — is
    what makes an over-timeout placement safe. **Done**
    ([docs/deployment.md](../docs/deployment.md)); dev PoC, like
    the rest of the Deploy tool. **Superseded by 29:** vectorizing the advection
    removed the 60 s timeout this worked around, so the cache + single-flight +
    client retry were **removed**.
29. [Vectorize the batch forecast](done/029-forecast-vectorize.md) — the batch
    endpoint advected each seed with a pure-Python **scalar** RK4 loop
    (~23 ms/seed, linear → ~46 s at the 2000-seed cap, against the gateway's 60 s
    timeout). Replaced with a **vectorized numpy** RK4 that advances all seeds in
    step-index lockstep (`_forecast._batch_advect`): **bit-identical** to the scalar
    path (same arithmetic order, same land-`NaN`/window truncation, pinned by a
    test) but **~40× faster** — 2000 drops in ~1.2 s. Chosen over numba (87× but a
    new dep + cold-compile on a fresh pod + per-arch JIT), scipy RGI (slower than
    plain numpy + an edge bug), and multiprocessing (core-gated, fork-from-threaded
    hazard) — pure numpy needs no new dependency and is trivial for one sync worker.
    With the timeout gone by a >40× margin, the 60 s-survival machinery from 28 (the
    server result-cache + single-flight and the client retry) was **removed**; the
    scalar integrator stays for the build's per-instrument forecast/hindcast. **Done**
    ([docs/deployment.md](../docs/deployment.md)); dev PoC, like
    the rest of the Deploy tool.
30. [Wave gliders](done/033-wave-gliders.md) — the two **wave gliders** the WHIRLS
    operational map now shows, added as a new `waveglider` instrument type (pink,
    the operational map's own colour). Closes the `WAVEGLIDERS/` follow-up flagged
    in [020](done/020-observations-portal-csv-source.md). The folder serves them in
    two shapes: `melktert` is a CSV picked up by the existing autoindex discovery
    (one `_GROUPS` line), while `wg1169` is published only as an L1 **NetCDF**, read
    as a **static portal file** with xarray (`fetch_waveglider_nc_sources` /
    `parse_waveglider_nc`) — the portal-over-THREDDS choice of 020, and richer than
    the operational map's read (which omits the `.nc`'s CF `time`). Everything
    downstream (track, tooltips, per-instrument forecast/hindcast, the one client
    `GLIDER_STYLES` entry) is type-generic. **Done**
    ([docs/gliders.md](../docs/gliders.md), [docs/data.md](../docs/data.md)).
31. [Deployment-focused app](done/034-deployment-focused-app.md) — reframe the app
    around placing and interrogating **virtual deployments** (vs. the IPSL/aeris
    observations map): forward *and* backward runs described by release time +
    direction + duration, full-cruise field coverage (hard tmin 2026-06-28 →
    CMEMS forecast end) via an incremental per-day field store + streaming batch
    advection, a long absolute-time scrubber that is the app's one clock (release
    time is always the displayed field time), and always-on position-at-time
    markers replacing the synced-t0 coloured dots. This **inverts the earlier
    dev-PoC framing** (22/23/25/28/29): the Deploy tool is now the app's primary
    tab and its own API v2 (`{start, cadence_s, direction}` per track, run-level
    budgets incl. a seeds×hours bound, `/limits`), not an observation viewer with
    a tool bolted on. Four workstreams (store, engine/API, frames/scrubber,
    frontend) landed sequentially, developed entirely in the local pixi flows.
    **Done** ([docs/deployment.md](../docs/deployment.md),
    [docs/field_store.md](../docs/field_store.md),
    [docs/currents.md](../docs/currents.md),
    [docs/controls.md](../docs/controls.md)). **Open remainder:** the
    `oc_gateway` adaptation — mount the field-store PVC subPath (build rw, api
    ro, never the fe pod), drop the api pod's CMEMS creds/egress, never serve the
    subPath — deferred by the plan itself (checklist in it), tied to
    [017](017-whirlsview-openshift.md).
32. [The clock moves the map](done/035-clock-following-tracks.md) — scrubbing
    clips every time-aware track to what has happened by the clock and the head
    markers (drifter circles, glider diamonds, ship discs) ride the clipped ends,
    replacing the per-track at-time dots; virtual drift lines keep a faint
    not-yet-traversed remainder and a Deploy-tab legend names the solid/dashed
    (field provenance) and strong/faint (clock) encodings; a **Timing** switch
    distinguishes along-track (ship-speed-staggered) from instantaneous releases;
    the slider's "now" becomes a blue dot on the scrub line and day ticks label
    as `Jul 14`. **Done** ([docs/trajectories.md](../docs/trajectories.md),
    [docs/deployment.md](../docs/deployment.md),
    [docs/controls.md](../docs/controls.md)).
33. [Tracks master + deploy cleanup](done/036-tracks-master-and-deploy-cleanup.md) —
    one **"Show tracks"** master in the scrubber governs every observed track line
    (drifter, glider, ship) together; single-fix heads (e.g. D-509) now follow the
    clock too (drifter tracks load eagerly, so every head is clock-driven). The
    drifter **forecast/hindcast** layer is removed (controls, drawing, and build).
    The Deploy tab loses its "Settings" caption, its Compute-drift checkbox (always
    on), and its drift-line legend; direction/timing are two-state toggles and CSV
    import/export hides behind a `⌄` menu. Virtual drift lines are one green stroke
    (no dash split, nothing ahead of the clock, no vessel route) and always shown;
    the MD track crops at 28 Jun and the scrubber drops its type-in jump box.
    **Done** ([docs/controls.md](../docs/controls.md)).
34. [Defaults & quick wins](040-defaults-and-quick-wins.md) — a batch of small
    frontend issues: drop the scrubber's "Now" button and the now-marker pulse
    (#36); deployment defaults to 10 km spacing + instantaneous release (#26);
    tracks (drifter/glider/ship) show on first load without stalling first paint
    (#28, which needs the track-visibility state decoupled from the Instruments
    tab's DOM since Deploy is the default tab); and forward + backward runs become
    an OR (both checkable, run and drawn from the same drops) instead of an
    exclusive switch (#32). **Open.**
35. [Track visual overhaul](041-track-visual-overhaul.md) — every instrument and
    virtual track gains a fixed **deployment dot** (4× line width, identity
    colour, no outline) at its deployment point plus a clock-driven **moving
    head**, marrying the real-instrument and virtual-deployment marker styling
    (#33; ships excluded); and the reporting-lag gap between an observed track and
    its forecast is closed with a **dashed bridge** (the advected last-fix→now
    vertices, currently discarded, rendered dashed and clock-clipped, #34).
    Sources the dot colour from a single identity-colour seam so the deferred
    **#35 (per-class palette)** later converges line = dot = head. **Open**
    (#35 deferred to its own session — needs rendered examples + human review).
36. [Zoom levels](042-zoom-levels.md) — half-level intermediate zoom
    (`zoomSnap`/`zoomDelta` 0.5) and a finer max zoom (raise `MAX_ZOOM`), bounded
    by the CMEMS 1/12° raster resolution — past it only the pixels enlarge (#27).
    **Open.**
37. [Default continent basemap](043-continent-basemap.md) — a highly compressed
    static gray-land / blue-sea WebP mask at CMEMS resolution, baked from the
    field's own NaN-land pattern (same grid, co-registered) and drawn in a pane
    below the shadings as a permanent basemap — land context with no OSM and no
    per-pan transfer (#29). **Open.**
38. [Outlier toggle](044-outlier-toggle.md) — a client-only "Hide outliers"
    toggle keyed on the `derived_speed_mps` already in `tracks.geojson` (no second
    download): flag out-and-back GPS spikes (anomalous speed on both adjacent
    segments), then **interpolate** the resulting gap if it spans ≤ 24 h or
    **blank** it beyond 24 h (#30). Server-side despike stays a separate BACKLOG
    item. **Open.**
39. [Slow-derive cold-start OOM](045-slow-derive-oom.md) — the slow-tier
    `derive` is OOMKilled at 2 Gi on a cold start (#37): the full-cruise 6-hourly
    shading window loads as **float64** (~449 MB, no cast) and `to_landmask_webp`
    `sortby`s the **entire** window to compute a time-invariant 2-D land mask (a
    full second copy). Fixes: make float32 canonical in RAM via a **chunked-lazy
    cast** on load (`copernicusmarine.subset` can't emit float32 at download, so no
    on-disk cast for the direct fetch; the field store is already float32 on disk),
    derive the land mask from a **single** time slice, and `del`+`gc` the window
    before the inertial step. Deploy-side, `oc_gateway` bumped the slow-cron cap
    2 Gi → 3 Gi as a stopgap. Fix #4 (store-derived shadings / batching — the true
    float32-on-disk end state, bounds peak independent of span) deferred. **Open.**
40. [Cache the observed-drifter forecast](046-forecast-response-cache.md) — the map
    fires the same observed forecast at page load for every client; on the single API
    pod it should compute once per data version, not once per client. A
    `functools.lru_cache` over `_batch_run`, keyed on the field-manifest mtime (the
    version token) + the request (seeds/horizon/direction); a store write bumps the mtime
    and invalidates. `analysis_edge` is unused by the client, so a frozen copy is harmless.
    **Done** (server-only, no behaviour change). The higher-value precompute-as-static
    variant and the ragged-start compute speedup (numba / lean in-RAM field) are tracked
    in the forecast-perf issue (#39), a separate MR.

Deferred / not in these batches: **#35** (instrument colormap — own session,
human-in-the-loop); **#16** (CMEMS rollover validation), **#17** (static
streamlines), **#25** (re-enable flow/near-inertial overlays), **#31** (xspar/float
forecasts — needs more CMEMS layers) — out of scope for this frontend pass.
