> Implemented — see [docs/interactive_forecast.md](../../docs/interactive_forecast.md)
> ("Waypoint CSV export").

# Deploy tool: waypoint CSV export

Add a **Download CSV** button to the Deploy tool that exports the placed drops as
a flat waypoint table the ship can consume. The drops *are* the deployment
waypoints — each is where one drifter enters the water and the staggered
ship-transit time it enters at — so the export is a straight dump of the geometry
the client already owns, no new derivation and no server round-trip.

## What is a waypoint here

The drop discs, not the clicked route corners. A drop carries everything a ship
needs to execute the deployment: an ordered position and an absolute water-entry
time (the ship-speed stagger already baked into each seed's `start`). The clicked
polyline vertices are only a construction aid — the drops lie along that route, so
exporting the drops gives both the where and the when without the raw corners.

## CSV shape

One row per drop across **every** currently-placed deployment (a running Deploy
session can place several), ordered by deployment then drop:

```
deployment,drop,latitude,longitude,water_entry_utc,cum_km
1,1,-34.50000,17.20000,2026-07-08T00:00:00Z,0
1,2,-34.55000,17.28000,2026-07-08T00:16:12Z,10.0
2,1,...
```

- `deployment` — the placement's `deployCounter` id (namespaces the drop set).
- `drop` — 1-based index within the deployment.
- `latitude` / `longitude` — the seed's 5-decimal values (what was forecast).
- `water_entry_utc` — the seed's absolute ISO `start`.
- `cum_km` — arc length from the path start (3 decimals).

Values are plain numbers / ISO strings with no separators, so no quoting is needed.

## Implementation

Client-only, in `site/map/app.js`; no API change, no build artifact.

- A module-level `deployWaypoints` registry (`deploymentId -> [row]`), mirroring
  `deployDropSets`. Populated in `placeDeployment` from the `drops` + `seeds` it
  already computes (both the drift-on and drift-off branches place drops, so both
  register waypoints). Cleared in `resetDeployHighlights`, so the Deploy tool's
  **Clear** wipes the waypoints alongside the layers and highlight registries.
- `deployWaypointsCsv()` flattens the registry to a CSV string (or null when
  empty); `downloadDeployWaypoints()` wraps it in a Blob and triggers a
  `deploy_waypoints.csv` download via an ephemeral object URL + synthetic anchor.
- A **Download CSV** button beside **Clear** in the Deploy control; on click it
  downloads and reports the count, or "no drops placed yet" in the status line.

## Scope

Prototype only, like the rest of the Deploy tool (021–023): no persistence beyond
the download, no build artifact, runs under `pixi run serve`. The `t0`-inversion
open problem is untouched — this exports the placed drops as-is.
