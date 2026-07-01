# deployment

The site is a static bundle (`site/`) served by **GitHub Pages**, rebuilt and
redeployed by a single GitHub Actions workflow:
`.github/workflows/deploy-pages.yml`.

Live URL: <https://geomar-od-lagrange.github.io/2026_whirls_cruise_map/>

## Why the build runs in CI, not just a file copy

`site/data/` is **git-ignored** — the committed tree carries only `index.html`,
`app.js`, and `style.css`, never the derived JSON/PNG. So Pages cannot deploy a
pre-built folder; it would ship a dataless map. The workflow therefore runs the
Python build (`pixi run build`, i.e. `python -m whirls_cruise_map.build`) on the
runner to regenerate `site/data/` fresh from upstream, then uploads the whole
`site/` directory as the Pages artifact.

This is deliberate: the data is time-sensitive cruise data, so baking a snapshot
into git would freeze it. Rebuilding per deploy keeps every layer as current as
its last run. (The one exception is the ship track, fetched live in the browser
rather than baked — see [ship.md](ship.md).)

## Triggers and cadence

The workflow runs on three events:

- **push to `main`** — redeploy when code or docs change.
- **schedule** — a `*/10 * * * *` cron, every 10 minutes, to keep positions and
  currents near-live during the cruise. A full run is ~1 minute, so the cadence
  is comfortable. GitHub's scheduler floor is 5 minutes and it may delay or drop
  scheduled runs under load, so treat this as best-effort freshness, not a
  guarantee. Scheduled runs fire only from the default branch.
- **workflow_dispatch** — a manual "Run workflow" button / `gh workflow run
  deploy-pages.yml`, for an on-demand rebuild.

Actions minutes are free for public repositories, so the 10-minute cadence costs
nothing on GitHub's side. The real budget is politeness to the upstream data
hosts (the drifter share, CMEMS, IPSL THREDDS), each hit once per run.

## Public repository requirement

GitHub Pages on a **private** repo requires a paid plan (Team/Enterprise). The
`geomar-od-lagrange` org is on the **free** plan, so the repo is **public** —
that is the condition under which Pages serves at all here. Note this is not the
only place the data goes public: a Pages site is a public URL regardless of repo
visibility (access control needs Enterprise), so deploying the map at all makes
it internet-reachable. There are no secrets in the source (the only embedded
URLs are the already-public drifter share and FTLE endpoints); CMEMS credentials
live in repo secrets, never in the tree.

## Secrets: CMEMS credentials

The currents/speed/forecast layers need a Copernicus Marine login. `fetch_field`
in `_currents.py` calls `copernicusmarine.subset(...)` with no explicit
credentials — the client reads them from the environment. The workflow passes
two repo secrets into the build step:

- `COPERNICUSMARINE_SERVICE_USERNAME`
- `COPERNICUSMARINE_SERVICE_PASSWORD`

Set them once under **Settings → Secrets and variables → Actions**, or:

```
gh secret set COPERNICUSMARINE_SERVICE_USERNAME --repo geomar-od-lagrange/2026_whirls_cruise_map
gh secret set COPERNICUSMARINE_SERVICE_PASSWORD --repo geomar-od-lagrange/2026_whirls_cruise_map
```

**The build degrades gracefully when they are absent.** Each data source is a
best-effort step in `build.py`: a CMEMS failure is caught and logged
(`WARNING: CMEMS field fetch failed, skipping currents + forecast`), and the
deploy still ships positions plus FTLE. So a run with no secrets, an expired
password, or a CMEMS outage produces a thinner map, never a failed deploy. The
drifter share and the FTLE THREDDS endpoint are public and need no secrets;
FTLE additionally only appears when a SPASSO field exists within 24 h of the
target time, so its absence from a deploy is normal, not an error.

## Serving under a subpath

Pages serves this project at `…/2026_whirls_cruise_map/`, not a domain root. The
site is written to work there: every asset and data reference in `index.html`
and `app.js` is **relative** (`./style.css`, `./app.js`, `./data/…`), and the
external resources (Leaflet CDN, basemap tiles, the vessel API) are absolute
HTTPS. Keep new references relative — an absolute `/data/…` path would 404 under
the subpath.

## Workflow mechanics

- **Environment.** `prefix-dev/setup-pixi` installs the locked pixi environment
  (`pixi.lock` is committed, so it is reproducible) with caching across runs.
- **Jobs.** A `build` job (checkout → setup-pixi → build → configure-pages →
  upload artifact) feeds a `deploy` job (`actions/deploy-pages`) bound to the
  `github-pages` environment.
- **Permissions.** Least-privilege `GITHUB_TOKEN`: `contents: read`,
  `pages: write`, `id-token: write` (Pages deploys via OIDC).
- **Concurrency.** A single `pages` group with `cancel-in-progress: false`, so an
  in-flight deploy is never cancelled when a scheduled and a push run overlap.
- **Enablement.** Pages source is set to "GitHub Actions"; `configure-pages` runs
  with `enablement: true`, so the first run also enables Pages if it is off.

Pinned action versions live in the workflow file; consult it rather than
duplicating them here.

## Operating it

- Watch the latest run: `gh run watch --exit-status` (or the Actions tab).
- Force a rebuild now: `gh workflow run deploy-pages.yml`.
- Inspect what a run wrote: `gh run view <id> --log | grep -iE "wrote|WARNING"`.
  Expect one `wrote positions …` line always, and `wrote currents … / forecast …`
  lines when CMEMS credentials are present.
