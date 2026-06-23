# Roadmap

Ordered intent. Each open item links a plan in this directory; implemented plans
move to `done/` and gain a `docs/` counterpart.

1. [Static cruise map — MVP](001-static-cruise-map.md) — fetch the drifter
   share, derive tracks, render latest positions + trajectories + a first CMEMS
   surface-currents overlay in a local Leaflet site. **Done** (built + reviewed;
   docs/ + move to done/ pending).
2. [Currents inclusion](002-currents.md) — turn the placeholder currents overlay
   into a proper feature: product choice, analysis→forecast temporal scope, eddy
   emphasis, payload budget. **Planning.**
3. Batch selection — controls to filter/highlight drifters by deployment batch.
   Blocked on a batch source (IDs supplied per deployment).
4. Automation & hosting — GitHub Actions cron rebuild and Pages deploy; CMEMS
   credentials via repository secrets.
