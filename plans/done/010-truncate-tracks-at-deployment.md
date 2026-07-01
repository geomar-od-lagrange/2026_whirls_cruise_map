# 010 — Truncate tracks at deployment (detach-from-vessel detection)

> **Done.** Implemented — see [docs/trajectories.md](../../docs/trajectories.md)
> (*Truncation at deployment*) and [docs/ship.md](../../docs/ship.md).

## Intent

The trajectory layer today draws each drifter's *entire* fix history — including
the port-staging period and the transit leg where the drifter was still aboard
(or being towed alongside) the ship. That pre-deployment path is not a free
drift and misleads a viewer reading the track as ocean motion.

Automate, per drifter, the detection of **when it detached from the vessel**, and
truncate its trajectory to start there — so the layer shows only the free-drifting
"true track". Rename the control's **Trajectories** row to **True track** to say
what it now is.

Exactness of the deployment instant does not matter; **not** leaking any
vessel-following fixes into the free track does. So the rule is deliberately
conservative: cut everything up to and including the last fix at which the drifter
was near the vessel.

## Detection

Distance-to-vessel, at build time (the build already has the full track DB; add a
best-effort fetch of the ship track).

- For each drifter fix at time `t`, interpolate the vessel position to `t` from
  the R/V Marion Dufresne track and take the great-circle distance.
- `attached` = fixes within `NEAR_SHIP_KM` (1.0 km — comfortably above GPS/
  ship-length scatter, far below deployed separations of 5–180 km).
- **Deployment start = the fix after the *last* attached fix.** Everything from
  there on is, by construction, beyond the threshold. A drifter never seen near
  the vessel → no truncation (keep full track). One still attached at its latest
  fix → no free fixes yet → empty trajectory (correctly not drawn).

Validated against live data: Deployment 1 tracks start 05:00–09:00, Deployment 2
~15:20, and on-deck/transit drifters (D-432/440/461/481/506/521) yield empty free
tracks.

Detection is purely geometric, but truncation is applied only to drifters in a
deployment batch: `pre_deploy` drifters keep their full track (still staging /
aboard, no free drift to isolate). So the roster decides *who* is truncated and
the detection decides *where*.

## Design

- **`_ship.py`** — `fetch_track()`: best-effort GET of the Flotte Océanographique
  vessel positions (same source as the client, see docs/ship.md), from before the
  cruise so the whole drifter window is covered. Returns time-sorted
  `(datetime, lat, lon)`; `[]` on any failure.
- **`_deploy.py`** — `deployment_starts(tracks, ship_track) -> {D_number: Timestamp}`:
  the first-free-drift time per drifter (absent key = keep full track). Holds
  `NEAR_SHIP_KM` and a local km haversine + linear ship-position interpolation.
- **`_geojson.tracks_geojson(tracks, deploy_starts=None)`** — before building each
  drifter's line, drop fixes earlier than its start; then unchanged. Derived
  velocity is thus computed within the free track (its first free fix derives from
  nothing → blank, which is correct). `latest_geojson`, forecast, hindcast are
  untouched (all keyed off the latest fix, which is post-deployment).
- **`build.py`** — fetch the ship track (best-effort), compute starts, pass to
  `tracks_geojson`. Ship fetch failure → `{}` → full tracks (today's behaviour).
- **`site/app.js`** — rename the overlay label `"Trajectories"` → `"True track"`.

## Files

- `src/whirls_cruise_map/_ship.py` (new), `_deploy.py` (new)
- `src/whirls_cruise_map/_geojson.py`, `build.py`
- `site/app.js`
- docs: `trajectories.md`, `ship.md`, `batches.md`, `features.md`; `ROADMAP.md`

## Verification

Rebuild; confirm Deployment 1/2 trajectories begin at their detected deployment
(not in port), on-deck drifters draw no trajectory, and the control reads
"True track". Ship-fetch failure falls back to full tracks.
