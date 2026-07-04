# whirlsview.geomar.de on OpenShift — exploration

Intent, not implementation. Explore hosting three things under one domain,
`https://whirlsview.geomar.de/`, on the **same OpenShift instance and project**
as [`2026_whirls_cruise_prep`](https://github.com/geomar-od-lagrange/2026_whirls_cruise_prep/)
(whose `deploy/viewer/` is the pattern we borrow):

1. `/archetypes` — the **archetypes viewer** (deck.gl FTLE/LAVD research SPA;
   lives in the prep repo, already has its own image + PVC).
2. `/map` — the **drifter map** from *this* repo (Leaflet SPA + derived
   `site/data/`), rebuilt on a schedule from live upstreams.
3. `/data` — **aggregated / cleaned drifter & glider datasets** for download
   (static files, browsable index), produced by the same rebuild.

Nothing here is built yet. This records the shape, the trade-offs, and the
decisions still owed.

## What we inherit from the prep repo's `deploy/viewer/`

The prep repo already solved "static SPA on this cluster, partner-gated." Reuse
verbatim where we can:

- **Base image** `registry.access.redhat.com/ubi9/nginx-124`, pinned by digest;
  listens on 8080 (only ports >1024 under the default `restricted-v2` SCC);
  docroot + `/opt/app-root/etc` group-writable so a random uid works.
- **Basic auth** as an nginx drop-in in `/opt/app-root/etc/nginx.default.d/`,
  htpasswd from a mounted Secret.
- **`manifest.yaml` shape**: PVC + Deployment (`strategy: Recreate` for an RWO
  PVC) + Service + Route (edge TLS, redirect). ImageStream trigger auto-rolls a
  new pod on `oc start-build`.
- **Defence-in-depth**: `automountServiceAccountToken: false`; NetworkPolicy
  that denies egress on a serve-only pod and allows ingress only where needed.
- **Binary Docker builds** (`oc new-build --binary --strategy=docker` +
  `oc start-build --from-dir=$CTX`) — no GitHub Actions, no CI/CD in-cluster.

What we **drop** as prep-specific (multi-`RUN_ID` research catalogue): the
`zip_upstream.py` loopback server, `rebuild_runs_json.py` + `entrypoint.sh`'s
index rebuild, the `loader.*` NESH pull pod, and the whole per-`RUN_ID` tree.
Our map is one flat `data/` dir, so the viewer image is nearly trivial.

## The one divergence that drives everything: we rebuild data *in* the cluster

The prep viewer **never fetches** — its data is produced on NESH (HPC) and
pushed manually by a loader pod, which is exactly why its pod runs
deny-all-egress. Our map instead rebuilds `site/data/` by pulling **live
internet sources** (the Nextcloud drifter share, THREDDS gliders/Agulhas, CMEMS
currents). So the equivalent of "the CI build" becomes an **in-cluster
CronJob** — and that CronJob, unlike the serve pod, needs egress and (for CMEMS)
credentials.

A concrete upside over today's GitLab Pages deploy: OpenShift `CronJob` is a
real in-cluster scheduler, so a sub-hourly fast cadence fires reliably — it is
not capped by GitLab's `PipelineScheduleWorker` interval (the constraint
[docs/deploy.md](../docs/deploy.md) and [plans/performance.md](performance.md)
both flag).

## Fast vs. slow rebuild paths

The build (`python -m whirls_cruise_map.build`, see `build.py`) is a sequence of
best-effort steps over sources with very different turnover. Splitting them into
two CronJobs on two cadences is the point of the exercise.

| Artifact | Source | Needs CMEMS creds? | Turnover | Tier |
|---|---|---|---|---|
| `latest.geojson`, `tracks.geojson`, `awaiting.json` | Nextcloud drifter share (public) | no | ~5–10 min | **fast** |
| `gliders.geojson` | WHIRLS THREDDS | no | ~5–10 min | **fast** |
| `agulhas.json` | IPSL THREDDS CSV (baked; no-CORS) | no | ~5–10 min | **fast** |
| `build.json` (freshness stamp) | — | no | per run | **fast** |
| `currents.json`, `speed.png`(+meta), `vorticity.png`(+meta) | CMEMS single-time field | **yes** | ~6-hourly | **slow** |
| `inertial_field.json` | CMEMS hourly window | **yes** | ~6-hourly | **slow** |
| `forecast.geojson`, `hindcast.geojson` | CMEMS window **×** live positions | **yes** | — | **straddles** |

- **Fast job** (~10 min): positions, tracks, gliders, Agulhas. No secrets. Cheap
  — a few dozen MB of CSV in, a few MB of GeoJSON out. This is the tier that
  actually keeps a near-live map fresh.
- **Slow job** (~6 h, or aligned to CMEMS release): the CMEMS-derived overlays.
  Needs the Copernicus login. Heavier (xarray + matplotlib render).
- **`forecast`/`hindcast` straddle**: they advect the *current* drifter/glider
  positions through the CMEMS window, so they want fresh positions (fast) *and*
  a CMEMS field (slow). Two options:
  - **(A) ride the slow tier** — recompute forecast/hindcast only when the field
    refreshes. The forecast origin then lags live positions by up to the slow
    cadence. For a rough advective overlay this is acceptable, and it needs zero
    extra machinery. **Recommended first.**
  - **(B) field cache** — the slow job caches the raw CMEMS window to the PVC
    (under an unserved `data/_cache/`), and the fast job, if a fresh-enough cache
    exists, re-advects live positions cheaply with no CMEMS pull. Keeps the
    forecast origin fast-fresh. This is exactly **ROADMAP #15 Phase 3
    ("slow-tier cadence + artifact cache")** — do it there, not now.

The two jobs write **disjoint files**, so they can share the `data/` dir without
coordination beyond per-file atomic writes (write `*.tmp`, `os.replace`) so
nginx never serves a half-written artifact. The client already tolerates any
single missing layer, so no whole-tree swap is needed. `build.json` is stamped
by the fast job; a second `currents`-tier stamp (age of the CMEMS layers) is a
cheap nice-to-have for the sidebar.

## Topology under one hostname

OpenShift's HAProxy router does **not** strip the path prefix — a Route with
`path: /map` forwards `/map/...` unchanged to its backend. Two ways to live with
that:

**Gateway nginx (chosen).** One Route (`host: whirlsview.geomar.de`, `path: /`)
→ a gateway pod that `proxy_pass`es each prefix to a ClusterIP backend Service,
stripping the prefix with a trailing slash, and `return 302`s `/` → `/map/`:

```
                whirlsview.geomar.de  (one Route, edge TLS, router-owned cert)
                              │
                     ┌────────┴────────┐
                     │  gateway nginx  │  no auth here; / → 302 /map/
                     └───┬────┬────┬───┘
    /archetypes/ ────────┘    │    └───────── /data/
   proxy_pass viewer-svc/     │           proxy_pass data-svc/    (public)
     (keeps its OWN      /map/ proxy_pass map-svc/  (public)
      basic-auth)
```

- **No auth at the gateway** — it is pure routing + the `/`→`/map/` redirect,
  so it needs no Secret. `/map` and `/data` are **public**. Auth stays **only on
  the `/archetypes` backend** (the archetypes viewer keeps its existing
  per-server `auth_basic` + htpasswd Secret). The gateway forwards the
  `Authorization` header and the `401`/`WWW-Authenticate` challenge straight
  through, so basic auth is end-to-end browser↔viewer; the gateway never sees a
  credential.
- Backends are **ClusterIP-only** (no Route of their own); NetworkPolicy locks
  their ingress to the gateway pod, so the only exposed surface is the gateway.
- Backends stay **decoupled from their public subpath**: the gateway maps
  `/map/` → `map-svc:8080/`, so the map serves at its own root and its
  **relative** asset/data refs resolve under `/map/` in the browser. This is why
  all-relative refs matter — the map is already all-relative
  ([docs/deploy.md](../docs/deploy.md)); **confirm the archetypes viewer has no
  absolute-root (`/data`, `/app.js`) refs** before wiring it in.
- Cost: one extra small pod + one config file — the *simplest correct* primitive
  for one host / three paths / a root redirect.

Rejected alternative — three path Routes, no gateway: each backend must serve
content laid out to match its public prefix, auth/cert are duplicated per
backend, and `/` still needs its own Route. More moving parts, no upside here.

### Ingress / TLS / DNS / auth (resolved)

- **DNS + TLS are handled by the OpenShift admins** — they wired up the
  `whirlsview.geomar.de` route, so DNS points at the router and a cert covering
  the custom host lives on the router / IngressController.
- **No local TLS in our manifest.** Confirmed by investigating the archetypes
  viewer: its `deploy/viewer/manifest.yaml` Route carries **no `host:` and no
  `tls.certificate`/`key`** — only `termination: edge` +
  `insecureEdgeTerminationPolicy: Redirect`, letting the router terminate with
  its default cert. Our gateway Route follows the same shape, adding just
  `host: whirlsview.geomar.de`; the router (admin-owned) provides the cert.
  Only if the admins say otherwise would we mount a cert Secret.
- **Auth**: `/map` and `/data` **public**; the `/archetypes` backend keeps its
  own basic-auth (unchanged from the prep repo). The gateway is unauthenticated.

## The three backends

- **`/archetypes` (archetypes viewer)** — already built and deployed from the
  prep repo (its `deploy/viewer/`; the Service is named `viewer` there). Here we
  only point the gateway at its Service; its data keeps flowing from HPC and it
  **keeps its own basic-auth** (this is the one gated path). No change to the prep
  repo's image — we only **retire its standalone Route**, since it's now reached
  through the gateway at `/archetypes`.
- **`/map` (this repo)** — `ubi9/nginx-124` serving a PVC-backed `data/`, shell
  (`index.html`/`app.js`/`style.css`) baked into the image so shell changes are
  deliberate image builds and data is pure cron output (exactly the prep
  image/PVC split). Fed by the two CronJobs above.
- **`/data` (cleaned datasets)** — `ubi9/nginx-124` with `autoindex on` over a
  PVC directory of aggregated/cleaned drifter+glider exports (CSV + a small
  generated `manifest.json`). **[018](done/018-ingest-derive-data-seam.md) revises the
  producer story**: `/data` is not merely "the builder *also* emits exports" — it
  is the pipeline's **durable seam**, the cleaned tracks that the **ingest** stage
  writes and the **derive** stage reads back to build the map. So the exports are
  the substrate, not a side-emission; see 018 for the CSV schema, the
  ingest/derive split, and the cleaning-auditability rationale. Could even be the
  same nginx pod as `/map` serving a second docroot subtree; keep it a separate
  Service only if the access pattern or retention differs.

### Shared storage — CronJobs write, nginx reads

The rebuild is an OpenShift **`CronJob`** (chosen over a builder sidecar: real
CronJobs give independent scheduling, history, retries, and no idle container —
a sidecar would just be a `sleep`-loop reimplementing cron in-pod). The builder
Job writes the map's `data/`; the serving nginx reads it. They share one PVC:

- **RWO PVC + `podAffinity`** — pin the CronJob pods to the serving node the way
  the prep repo pins its loader. **Works on any cluster** regardless of whether
  RWX exists, so this is the phase-1 default.
- **RWX PVC** — no node coupling; a later *simplification* once RWX is confirmed
  available, not a prerequisite.

The RWX answer is therefore **not a phase-1 blocker** — the RWO+pin path ships
without it. The one place RWX genuinely decides something is **`/data`**: if we
want the cleaned exports to **persist / accumulate** across restarts as a
download archive (rather than regenerate-only), that wants a durable PVC — decide
before locking `/data`, which is later in the sequence. The map data + exports
are **tiny** (single-digit MB, vs the prep viewer's 50 Gi), so any PVC is 1–5 Gi.
The `/archetypes` viewer keeps its own separate HPC-pushed PVC; don't co-mingle.

## Code changes this needs in *this* repo

Small, and in keeping with the greenfield/reshape ethos in `AGENTS.md`. Items
1–4 are the producer refactor; **[018](done/018-ingest-derive-data-seam.md) owns
their design** (the ingest/derive split, `_data.py` seam I/O, the `/data` CSV
schema, `docs/data.md`) — this list is the summary, 018 is the spec:

1. **Configurable output dirs** — `build.py`'s `SITE_DATA` is hardcoded to
   `site/data`; make the map root *and* the new `/data` root env/args (`--out` /
   `WHIRLS_SITE_DATA`, `WHIRLS_DATA`) so a CronJob writes to the PVC mounts.
2. **Stage + tier selector** — factor `main()` into `ingest` / `derive` with a
   tier switch (`--stage ingest`, `--stage derive --tier fast|slow`). Keep each
   step best-effort as today. Note 018 re-cuts this tier table into
   ingest / derive-fast (egress-free) / derive-slow (CMEMS).
3. **Atomic per-file writes** — `_write_json` / the PNG / CSV writes go through
   `*.tmp` + `os.replace`; under 018 this also guards the seam derive reads.
4. **Dataset-export = ingest** — the cleaned drifter+glider+ship track CSVs +
   `manifest.json` in `/data` are not a separate export step but the **ingest
   stage's** output, which derive then consumes (018).
5. **`deploy/` dir** (mirrors the prep repo's `deploy/<app>/` convention):
   `deploy/map/` (viewer Dockerfile off `ubi9/nginx-124`, nginx conf, manifest),
   `deploy/builder/` (Dockerfile baking the `pixi.lock` env + `src/` so the
   CronJob runs `pixi run build --tier …`; egress-allowed NetworkPolicy; CMEMS
   Secret only on the slow job), and `deploy/gateway/` (gateway conf + the single
   Route + the `/`→`/map/` redirect). Reuse the CI's proven pixi-in-container
   approach (`pixi.lock` is committed and reproducible).

## Repo structure — do we split?

The three-paths framing largely answers it: **we already have a two-repo split
by app lineage, and that's the right amount.**

- The **archetypes viewer** stays in the **prep repo** — it's downstream of the
  parcels/FTLE/LAVD pipeline that lives there, and its data comes from HPC. Don't
  move it.
- The **map + dataset exports + the CronJobs that feed them** stay in **this
  repo** — they share the `whirls_cruise_map` package (`_clean`, `_fetch`,
  `_currents`, `_inertial`, …). Cross-repo code sharing (submodule / published
  package) would be pure overhead for a pre-alpha two-person effort.
- **Do not create further repos** for the builder, the exports, or the gateway —
  those are `deploy/` subdirs here (again mirroring the prep repo, whose deploy
  doc explicitly anticipates a sibling `deploy/api/`). The shared cluster wiring
  (the single Route, the `/`→`/map/` redirect, cross-app NetworkPolicies) is
  cross-cutting; put it
  in this repo's `deploy/gateway/` and treat it as the coordination point, since
  this repo is where the active cron/deploy work lives.

Split further **only if** an app later develops a genuinely independent release
cadence or a conflicting dependency stack. Nothing here does yet.

## The interactive analysis app (the earlier ask) — future 4th path

Distinct from `/data` (static downloads): an interactive **drifter analysis
app** would be a *live* Python service (Panel / Streamlit / Voilà / FastAPI),
not a static SPA — so a **Deployment** (not a CronJob+nginx), with egress + any
creds, sitting behind the gateway at e.g. `/analysis`. It would reuse this
repo's `whirls_cruise_map` package and consume the same cleaned datasets from
`/data`. Runtime choice stays open until we know the interactions we want; don't
pick a framework now. It lands as another `deploy/analysis/` here when scoped.

## Relationship to the current GitLab Pages deploy

Today the map ships via GitLab Pages ([docs/deploy.md](../docs/deploy.md)).
**Keep GitLab Pages running for now** — OpenShift stands up alongside it, not as
a replacement. OpenShift adds a reliable sub-hourly cron and co-location with the
archetypes viewer + datasets under one hostname. Revisit retiring Pages once
whirlsview is proven; no decision forced now.

**Pages adopts the split now.** Rather than staying at `/`, the Pages deploy
takes the same browser-facing layout as this gateway — `/map/`, a sibling
`/data/`, and `/`→`/map/` — per [018](done/018-ingest-derive-data-seam.md). Because the
client is all-relative, that is a pure layout move, and it **de-risks this
gateway**: the identical path shape runs on Pages before the cluster exists.

## Decisions locked (2026-07-04)

- **Gateway**, not three path-Routes.
- **`/` → 302 `/map/`** (no landing page).
- **Auth stays only on `/archetypes`** (archetypes viewer, unchanged); **`/map`
  and `/data` are public**; the gateway is unauthenticated and holds no Secret.
- **Rebuild is a `CronJob`**, not a builder sidecar; **RWO PVC + `podAffinity`**
  is the phase-1 shared-storage default (RWX is a later simplification).
- **TLS/DNS handled by the OC admins** — no cert in our manifest; the gateway
  Route is `host: whirlsview.geomar.de` + `termination: edge` + redirect, same
  shape as the archetypes viewer's Route (which carries no host and no cert).
- **GitLab Pages stays** for now.

## Still open / to confirm

- **RWX storage** — deferred; not a phase-1 blocker (RWO + podAffinity ships
  without it, see "Shared storage"). Check `oc get storageclass` when you can; it
  only decides `/data` durability.
- **`/data` durability**: regenerate-only (ephemeral) vs. a persistent, growing
  download archive (wants a durable PVC).
- **Cron cadences**: fast ~10 min, slow ~6 h are starting points tied to upstream
  turnover — confirm against the CMEMS release schedule.
- **Analysis-app runtime** (future `/analysis`) — defer.

## Suggested sequencing

1. **Map viewer + fast CronJob** — no secrets; gets a live, self-refreshing map
   on the cluster (behind a temp `*.apps` Route first, before the custom host).
2. **Gateway + custom host** — front the map at `whirlsview.geomar.de/map`, with
   `/`→`/map/` (gateway is unauthenticated; the admins' Route provides TLS).
3. **Wire in `/archetypes`** (archetypes Service behind the gateway; it keeps its
   own basic-auth).
4. **Slow CronJob + CMEMS Secret** — the currents/vorticity/forecast overlays.
5. **`/data` exports** — dataset-export build step + autoindex backend.
6. **Field cache** (forecast option B / ROADMAP #15 Phase 3), if the origin lag
   proves annoying.
7. **Analysis app** (`/analysis`) when scoped.

## Non-goals (inherit the prep repo's)

Per-user accounts / `oauth-proxy`, autoscaling, PodDisruptionBudgets,
monitoring/log forwarding, service mesh, in-cluster CI/CD. Each is a separate
plan if it earns its place.
