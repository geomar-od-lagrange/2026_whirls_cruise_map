# deployment

The site is a static bundle (`site/`) served by **GitLab Pages** on
`git.geomar.de`, rebuilt and redeployed by `.gitlab-ci.yml`.

The published tree has two public paths: the Leaflet map at `/map/` and the
cleaned dataset downloads at `/data/` (see [data.md](data.md) for what's in the
latter). `/` itself is just an entry point — a GitLab Pages `_redirects` rule
(`site/_redirects`) sends it to `/map/` with a 302. A root `index.html` with a
`<meta http-equiv="refresh">` is the belt-and-suspenders fallback: it's what
lands you on the map if `_redirects` isn't honoured, and it's also what makes
local `python -m http.server` (which doesn't process `_redirects`) land on
`/map/` too.

## Why the build runs in CI, not just a file copy

Both generated trees are **git-ignored** — the committed `site/` carries only
`site/map/{index.html,app.js,style.css}`, `site/index.html`, and
`site/_redirects`, never the derived data. So Pages cannot publish a pre-built
folder; it would ship a dataless map and an empty downloads page. The `pages`
job therefore runs the Python build (`pixi run build`, i.e.
`python -m whirls_cruise_map.build`) on the runner to regenerate both trees
fresh from upstream, then serves the result:

- `site/data/` — the **ingest** stage's output: cleaned per-source CSVs plus
  their raw sources and a manifest (see [data.md](data.md)).
- `site/map/data/` — the **derive** stage's output: the map's own
  GeoJSON/PNG artifacts, read back from `site/data/` rather than re-fetched.

This is deliberate: the data is time-sensitive cruise data, so baking a snapshot
into git would freeze it. Rebuilding per deploy keeps every layer as current as
its last run. (The one exception is the **Marion Dufresne** ship track, fetched
live in the browser rather than baked, because its source API is CORS-open; the
**Agulhas II** track is baked like the rest, because its THREDDS source is not —
see [ship.md](ship.md).)

### Two build stages, one CLI

`python -m whirls_cruise_map.build` runs the whole chain — ingest then derive —
which is what the `pages` job invokes and what a no-arg local run does too.
The stages split with `--stage`, and derive further splits by CMEMS-cost with
`--tier`:

```
python -m whirls_cruise_map.build                     # ingest + derive (all)
python -m whirls_cruise_map.build --stage ingest
python -m whirls_cruise_map.build --stage derive --tier fast   # no secrets, no egress
python -m whirls_cruise_map.build --stage derive --tier slow   # needs CMEMS creds
```

This split exists for the future CronJob deployment (a fast cadence for
positions, a slower one for CMEMS-derived overlays — see
`plans/017-whirlsview-openshift.md`); the `pages` job here just runs `all`.
The two output roots default to the Pages layout above and are overridable —
`--data` / `WHIRLS_DATA` for the download tree, `--map` / `WHIRLS_SITE_DATA` for
the map's tree — so a future CronJob can point them at PVC mounts instead of
`site/`.

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
not a domain root. The map is written to work there: every asset and data
reference in `site/map/index.html` and `site/map/app.js` is **relative**
(`./style.css`, `./app.js`, `./data/…`), and the external resources (Leaflet
CDN, basemap tiles, the vessel API) are absolute HTTPS.

Keep new references in the map relative. This now matters for a second reason
beyond the subpath: an absolute `/data/…` reference from the map would not 404
— it would silently resolve to the *download* tree (`site/data/`, the cleaned
CSVs), a different directory with different contents from the map's own
`site/map/data/` (GeoJSON/PNG). The map must reach its own data via the
relative `./data/…`, never the top-level `/data/…`.

## Code mirror

The repository is also pushed to a GitHub `origin` remote as a code mirror; it
does not deploy anything. Keep the two in sync by pushing `main` to both `origin`
and `gitlab`.
