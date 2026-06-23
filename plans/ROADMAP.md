# Roadmap

Ordered intent. Each open item links a plan in this directory; implemented plans
move to `done/` and gain a `docs/` counterpart.

1. [Static cruise map — MVP](001-static-cruise-map.md) — fetch the drifter
   share, derive tracks, render latest positions + trajectories + today's CMEMS
   surface currents in a local Leaflet site. **In progress / under review.**
2. Batch selection — controls to filter/highlight drifters by deployment batch.
   Blocked on a batch source (IDs supplied per deployment).
3. Automation & hosting — GitHub Actions cron rebuild and Pages deploy; CMEMS
   credentials via repository secrets.
