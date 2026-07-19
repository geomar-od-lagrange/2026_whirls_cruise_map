# hosting

This doc covers build/CI/OpenShift infrastructure — how the map is built and served.
For the in-app virtual-deployment tool (planning drifter deployments on the map), see
[`docs/deploy_tool.md`](deploy_tool.md).

This repo does **not** deploy itself. It builds the static map bundle (`site/`) and
the cleaned dataset downloads, and describes how the map is authored so it serves
correctly. Building on a cadence and serving the result — the OpenShift "whirlsview"
stack: build CronJobs, the gateway that fronts `/map/` and `/api/`, the field-store
PVC, and the forecast-API pod — live in the sibling repo
[`oc_gateway`](https://git.geomar.de/2026-whirlscruise-lagrange/oc_gateway) (see
[`plans/017-whirlsview-openshift.md`](../plans/017-whirlsview-openshift.md)).

The pipeline publishes nothing. `.gitlab-ci.yml` has a single job, a **type-check
guard** — `pixi run check-frontend` (`tsc --checkJs` over
`site/map`) fails the pipeline on any TypeScript error, the ReferenceError-blank-page
risk of the no-bundler ES-module split (see [`tsconfig.json`](../tsconfig.json)).

## The build: two stages, one CLI

`python -m whirls_cruise_map.build` (`pixi run build`) runs the whole chain — ingest
then derive — regenerating two **git-ignored** trees from live upstream sources (the
committed `site/` carries only the map's JS/CSS/HTML sources — `site/map/**` minus
`site/map/data/`, plus `site/index.html` — never the derived data):

- `site/data/` — the **ingest** stage's output: cleaned per-source CSVs plus their raw
  sources and a manifest (see [data.md](data.md)).
- `site/map/data/` — the **derive** stage's output: the map's own GeoJSON/PNG
  artifacts, read back from `site/data/` rather than re-fetched.

The data is time-sensitive cruise data, so it is rebuilt per run rather than baked into
git — every layer stays as current as its last build. (The one exception is the
**Marion Dufresne** ship track, fetched live in the browser because it is a
near-real-time feed; the **Agulhas II** track is baked like the rest — an hourly
scrape, so baking loses no freshness and adds resilience — see [ship.md](ship.md).)

The stages split with `--stage`, and derive further splits by CMEMS-cost with `--tier`:

```
python -m whirls_cruise_map.build                     # ingest + derive (all)
python -m whirls_cruise_map.build --stage ingest
python -m whirls_cruise_map.build --stage derive --tier fast   # no secrets, no egress
python -m whirls_cruise_map.build --stage derive --tier slow   # needs CMEMS creds
```

This split is what the deploy CronJobs use — a fast cadence for positions, a slower one
for the CMEMS-derived overlays. The two output roots default to the layout above and are
overridable — `--data` / `WHIRLS_DATA` for the download tree, `--map` /
`WHIRLS_SITE_DATA` for the map's tree — so the CronJobs point them at PVC mounts instead
of `site/`.

## Secrets: CMEMS credentials

The currents/speed/forecast layers need a Copernicus Marine login. The shading fetch —
`_currents.fetch_shading_window`, feeding the currents/speed/vorticity overlays — and
the field-store's per-day fetch — `_field_store._default_fetch_day`, feeding the
forecast — both call `copernicusmarine.subset(...)` with no explicit credentials; the
client reads them from the environment, supplied by whatever runs the build (the
`oc_gateway` CronJobs):

- `COPERNICUSMARINE_SERVICE_USERNAME`
- `COPERNICUSMARINE_SERVICE_PASSWORD`

**The build degrades gracefully when they are absent.** Each data source is a
best-effort step in `build.py`, caught and logged separately: a shading-fetch failure
logs `WARNING: CMEMS field fetch failed, skipping currents overlays: {exc}` and skips
just the currents/speed/vorticity overlays; a field-store top-up failure logs
`WARNING: field store update failed: {exc}` and skips just the forecast-feeding field.
Either way the build still produces positions and tracks. So a run with no variables,
an expired password, or a CMEMS outage produces a thinner map, never a failed build.
The drifter share is public and needs no secrets.

## Authoring the map to serve under any base

The gateway serves the map under a **subpath** (`…/<namespace>/<project>/…`), not a
domain root, so every asset and data reference in `site/map/index.html` and
`site/map/app.js` is **relative** (`./style.css`, `./app.js`, `./data/…`,
`./vendor/leaflet-1.9.4/…`); the one external resource (the vessel-position API) is
absolute HTTPS. The root redirect follows the same rule — a **relative** `./map/`
meta-refresh in `site/index.html`, never a domain-absolute `_redirects` target (whose
target resolves from the domain root and breaks under a subpath). A relative reference
resolves against whatever base the page is served under — subpath, domain root, or a
local `python -m http.server` — so one mechanism covers all three. (There is no basemap
tile source — the map ships tile-free to spare the at-sea VSAT link; see the currents
overlay + sea-tone background.)

Keep new references in the map relative. This matters for a second reason beyond the
subpath: an absolute `/data/…` reference from the map would not 404 — it would silently
resolve to the *download* tree (`site/data/`, the cleaned CSVs), a different directory
from the map's own `site/map/data/` (GeoJSON/PNG). The map must reach its own data via
the relative `./data/…`, never the top-level `/data/…`.

## Code mirror

The repository is also pushed to a GitHub `github` remote as a code mirror; it does not
deploy anything. Keep the two in sync by pushing `main` to both `origin` (GitLab) and
`github`.
