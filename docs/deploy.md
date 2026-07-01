# deployment

The site is a static bundle (`site/`) served by **GitLab Pages** on
`git.geomar.de`, rebuilt and redeployed by `.gitlab-ci.yml`.

## Why the build runs in CI, not just a file copy

`site/data/` is **git-ignored** — the committed tree carries only `index.html`,
`app.js`, and `style.css`, never the derived JSON/PNG. So Pages cannot publish a
pre-built folder; it would ship a dataless map. The `pages` job therefore runs the
Python build (`pixi run build`, i.e. `python -m whirls_cruise_map.build`) on the
runner to regenerate `site/data/` fresh from upstream, then serves it.

This is deliberate: the data is time-sensitive cruise data, so baking a snapshot
into git would freeze it. Rebuilding per deploy keeps every layer as current as
its last run. (The one exception is the ship track, fetched live in the browser
rather than baked — see [ship.md](ship.md).)

## The `pages` job

GitLab Pages serves the `public/` directory produced by a job named `pages`, so
the job builds into `site/` then copies it to `public/`. It runs on the default
branch (push, scheduled, or manual pipelines).

- **Environment.** A `debian:bookworm-slim` image installs pixi and runs the
  locked environment (`pixi.lock` is committed, so it is reproducible); the pixi
  package cache is cached across runs. If the runner can reach `ghcr.io`, the file
  carries a commented one-line swap to the official pixi image to skip the install.
- **Artifact.** `public/` (the built `site/`) is the Pages artifact.

## Cadence

Freshness comes from a **Pipeline schedule** (Settings → CI/CD → Pipeline
schedules) on the default branch; push and manual pipelines also rebuild on
demand. On self-managed GitLab a schedule fires no more often than the instance's
`PipelineScheduleWorker` polls
(`gitlab_rails['pipeline_schedule_worker_cron']` in `/etc/gitlab/gitlab.rb`, then
`gitlab-ctl reconfigure`), so a sub-hourly cadence needs that worker set to match.

Rebuild frequency only helps as far as the upstream data turns over: CMEMS surface
currents update ~6-hourly, while the drifter share and ship API refresh ~5–10 min
— so the drifter/ship layers are what a tight cadence keeps fresh.

## Secrets: CMEMS credentials

The currents/speed/forecast layers need a Copernicus Marine login. `fetch_field`
in `_currents.py` calls `copernicusmarine.subset(...)` with no explicit
credentials — the client reads them from the environment. Supply them as **masked
CI/CD variables** (Settings → CI/CD → Variables):

- `COPERNICUSMARINE_SERVICE_USERNAME`
- `COPERNICUSMARINE_SERVICE_PASSWORD`

**The build degrades gracefully when they are absent.** Each data source is a
best-effort step in `build.py`: a CMEMS failure is caught and logged
(`WARNING: CMEMS field fetch failed, skipping currents + forecast`), and the
deploy still ships positions and tracks. So a run with no variables, an expired
password, or a CMEMS outage produces a thinner map, never a failed deploy. The
drifter share is public and needs no secrets.

## Serving under a subpath

GitLab Pages serves the project under a path (e.g. `…/2026_whirls_cruise_map/`),
not a domain root. The site is written to work there: every asset and data
reference in `index.html` and `app.js` is **relative** (`./style.css`, `./app.js`,
`./data/…`), and the external resources (Leaflet CDN, basemap tiles, the vessel
API) are absolute HTTPS. Keep new references relative — an absolute `/data/…` path
would 404 under the subpath.

## Code mirror

The repository is also pushed to a GitHub `origin` remote as a code mirror; it
does not deploy anything. Keep the two in sync by pushing `main` to both `origin`
and `gitlab`.
