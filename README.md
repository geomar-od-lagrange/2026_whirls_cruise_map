# 2026 Whirls Cruise map

Maps drifter and other device positions during the 2026 Whirls Cruise of
R/V *Marion Dufresne* and R/V *S.A. Agulhas II*.

The build produces two things from upstream sources:

- a static **Leaflet map** (`site/map/`) — drifter/glider/ship tracks and latest
  positions, CMEMS surface-current speed / vorticity / flow shadings, and an
  interactive deployment-forecast tool;
- cleaned **dataset downloads** (`site/data/`).

An optional **forecast API** (`_api.py`, FastAPI) advects client-seeded virtual
drifters through an incremental per-day CMEMS current store — the map's "Deploy"
tool calls it; the static map works without it.

## Quick start

This repo uses [pixi](https://pixi.sh). Common tasks (`pixi run <task>`):

| Task | What it does |
| --- | --- |
| `build` | Regenerate `site/data/` + `site/map/data/` from upstream (needs a CMEMS login) |
| `serve` | Static map on `:8000` — open <http://localhost:8000/map/> |
| `serve-api` | Forecast API on `:8001` (the Deploy tool's backend) |
| `test` | `pytest` |
| `check-frontend` | Type-check the static map's plain-JS ES modules (`tsc --checkJs`, no build step) |

For the interactive tool, run `serve` and `serve-api` together; the client
auto-targets the API on `:8001` (see `resolveApi` in `site/map/app.js`).

## Documentation

- `docs/*.md` — standalone docs for the current state of the code (start with
  [`docs/data.md`](docs/data.md), [`docs/deployment.md`](docs/deployment.md)).
- `plans/*.md` — intent before implementation; `plans/ROADMAP.md` is the index,
  implemented plans move to `plans/done/`.
- [`AGENTS.md`](AGENTS.md) — working guidelines for this repo.

## Deployment

The static site publishes to **GitLab Pages** on `git.geomar.de` via
`.gitlab-ci.yml` (see [`docs/deploy.md`](docs/deploy.md)). Production is the
OpenShift "whirlsview" stack — the gateway, OpenShift manifests, PVC/cron wiring,
and forecast-API pod live in the sibling repo
**[`oc_gateway`](https://git.geomar.de/2026-whirlscruise-lagrange/oc_gateway)**;
[`docs/deployment.md`](docs/deployment.md) describes the streaming field-store
shape both sides share.
