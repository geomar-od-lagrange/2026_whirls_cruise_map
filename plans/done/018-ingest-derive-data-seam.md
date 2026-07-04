# Ingest → derive: `/data` as the pipeline seam

> **Implemented (2026-07-04).** This repo's deliverables are done — the Pages
> `/map` + `/data` + `/`→`/map/` layout, the ingest/derive split (`_data.py`,
> `build.py --stage/--tier`), the cleaned + raw `/data` CSVs + `manifest.json`,
> and the docs ([docs/data.md](../../docs/data.md), the `/data` schema, and
> [docs/deploy.md](../../docs/deploy.md), the layout). The OpenShift consumption
> (CronJobs + the `/data` autoindex backend) stays with
> [017](../017-whirlsview-openshift.md).

Intent, not implementation. This is the **pipeline-internals** counterpart to
[017](../017-whirlsview-openshift.md), which fixes the *topology* (`/map` and
`/data` as two public paths under one host). 017 leaves `/data` as a
side-emission — "have the fast/slow builder *also* emit exports." This plan
takes the stronger position: **`/data` is not a side-dump, it is the durable
seam the site is built from.**

Two phases, split by a persisted intermediate:

1. **Ingest** — fetch **all instrument and ship tracks** (drifters, gliders,
   Marion Dufresne, Agulhas II), clean/unify/annotate them, and write
   **human-inspectable CSVs into `/data/`**.
2. **Derive** — read those tables back and build every site artifact from them:
   the map GeoJSON, and the CMEMS-derived overlays (ζ/f, u/v, forecast/hindcast,
   inertial).

`/data` is therefore simultaneously the **download product** and the **build
input** — one substrate, two consumers. That collapse is the whole point; it is
cheaper than 017's framing, not more, because 017 already owed us a schema'd
`/data`.

## Two "data" dirs — name them apart

Conflating these will bite us, because both are called "data":

- **download `/data/`** (017's `/data` backend) — cleaned instrument+ship
  *track tables* (CSV), for humans to download and audit.
- **map `…/data/`** (today's `SITE_DATA = site/data/`) — derived
  GeoJSON/JSON/PNG the Leaflet app fetches, served at `/map/data/…`.

Ingest writes the **first**; derive reads the first and writes the **second**.
Where this plan says "`/data`" unqualified, it means the download path.

## Today: one pass, nothing persisted

`build.py:main()` is a single pass in which **every intermediate is in-memory
and discarded**: the de-duped drifter DataFrame (`_clean.tracks`), the glider
`Platform` lists (`_gliders`), the two ship tracks (`_ship`, `_agulhas`). Only
the *derived* artifacts (`latest.geojson`, `tracks.geojson`, `speed.png`, …)
reach disk, in `site/data/`. There is no persisted "tracks" layer, so
re-deriving any GeoJSON means re-fetching from the live upstreams, and a
partial fetch failure this run starves every step downstream of it in the same
run.

## The seam: `/data` as substrate (decisions locked 2026-07-04)

- **Read-back seam**, not one-pass dual-emit. Derive reads the tidy tables back
  from `/data` as its input. Rejected the dual-emit (one process holds tracks in
  memory and writes both `/data` and the map GeoJSON) because the read-back is
  what makes derive **egress-free and independently runnable** — the property
  that pays off in the CronJob split and the future `/analysis` app.
- **CSV is the canonical form** — the human-inspectable download product *and*
  the build input, one format. `/data`'s value-add over the already-public raw
  Nextcloud share is precisely the **cleaning, unification, and annotation**, so
  the cleaned tables must be legible: a user can open `drifters.csv`, read the
  columns, and check the cleaning against the raw share. Parquet is a deferred
  optional companion (emit alongside CSV, derive reads it) *only if* CSV
  read-back ever gets slow — it will not at single-digit MB. This supersedes the
  backlog's "Track DB parquet cache" idea: the seam is the persistence that item
  wanted; CSV is enough.

## What lands in `/data` — the ingest product

The four track sources have heterogeneous native shapes, so per-instrument-type
CSVs (each tidy/long over its platforms) beat one wide, NaN-sparse mega-table —
you download `drifters.csv` and get drifter columns, not a sea of blanks. A
shared **core column convention** (`platform_id, platform_type, time_utc, lat,
lon`) plus native extras keeps them uniform where it matters and faithful where
it counts:

| File | Rows | Core + native columns |
|---|---|---|
| `drifters.csv` | one per (D_number, fix) | core + `u_speed_mps, u_dir_deg, battery_state` |
| `gliders.csv` | one per (glider, fix) | core (`platform_type` ∈ xspar/seaglider) |
| `ship_marion_dufresne.csv` | one per MD fix | core |
| `ship_agulhas_ii.csv` | one per Agulhas fix | core + `speed_kn, course_deg, status, area` |
| `platforms.csv` | one per platform | `platform_id, platform_type, batch, deployed_at, first_fix, last_fix, n_fixes` |
| `raw/…` | as fetched | verbatim source files (drifter snapshots, glider CSVs, MD JSON, Agulhas CSV) — provenance for the cleaned tables |
| `manifest.json` | — | file list + per-file **provenance** (source URL) + schema + freshness stamp |

- **Per-fix tables carry the fixes; `platforms.csv` carries per-platform
  metadata** (batch, deployment time, coverage) so the annotations live once, not
  repeated down every row.
- **Time is one UTC convention** (`time_utc`, ISO-8601 `…Z`), unifying today's
  three glider encodings (epoch / naive-ISO / offset-ISO, `_gliders._parse_time`)
  and the Agulhas naive-UTC assumption — the unification is itself a documented
  cleaning step.
- **Raw sources are published too**, under `data/raw/`, exactly as fetched
  (drifter snapshot CSVs, glider/ship source CSVs, the MD positions JSON). `/data`
  then carries *both* the cleaned tables and the inputs they were cleaned from, so
  the cleaning is reproducible end-to-end from what the directory itself serves —
  not merely described in `docs/data.md`. (The raw drifter share is already public
  on Nextcloud; re-publishing here keeps raw and cleaned side by side for audit.)
- **The two ships stay two files** (`ship_marion_dufresne.csv`,
  `ship_agulhas_ii.csv`), not one `ships.csv` — Agulhas carries
  SOG/COG/status/area the MD API does not, so a merged table would be half-empty.

### The boundary: what cleaning is `/data`, what is `/map`

The pull is between "cleaned enough to download" and "shaped for the map." The
clean cut:

- **`/data` gets the full, annotated tracks.** Every valid fix, plus `batch`
  and `deployed_at` **annotations** — not physical truncation. A download user
  gets *everything* and decides. Deployment detection needs the MD ship track,
  but that is an ingest fetch anyway, so `_deploy.deployment_starts` runs in
  ingest and its result becomes the `deployed_at` column.
- **`/map` derives the map-specific views.** `tracks.geojson`'s truncation at
  `deployed_at` (the "True track" free-drift segment) is a *rendering* decision,
  so it stays in derive, computed from the `/data` annotation. Same for
  `latest.geojson` (last valid fix) and the currents/forecast overlays.
- **`/data` is observations only — no model fields.** The CMEMS-derived layers
  (u/v grids, ζ/f, forecast/hindcast, inertial) are *map overlays*, produced by
  derive into the map's `…/data/` and **never** written to the download `/data/`.
  `/data` holds what was *observed* (drifter/glider/ship tracks, raw + cleaned); a
  model reanalysis field is a different kind of artifact, not a track-data
  download. So option B's field cache, if it ever lands, stays in a private
  unserved path — not `/data`.

### Cleaning made auditable

Because the CSVs *are* the cleaning made legible, they get a companion
[`docs/data.md`](../../docs/data.md) documenting each column and each rule:
de-dup on `(D_number, date_UTC)`, sentinel (`-99999`) drop, per-value glider
time-format detection, Agulhas UTC assumption, deployment detection, batch
roster (`deployments.json`). When the **GPS-despike** backlog item lands, it
becomes a visible ingest step — a `despiked` flag column or a documented drop —
rather than an invisible in-memory filter, which is exactly the auditability the
`/data`-as-CSV choice is for.

## Fast/slow re-cut around the seam

017 split the CronJobs by **CMEMS-creds-needed**. The ingest/derive seam is a
better-factored axis, and it *subdivides* 017's fast tier cleanly:

| Stage | Reads | Writes | CMEMS creds | Cost |
|---|---|---|---|---|
| **ingest** | live upstreams (Nextcloud, THREDDS, FOF) | `/data/*.csv` | no | cheap |
| **derive-fast** | `/data/*.csv` | map GeoJSON (latest/tracks/gliders/agulhas) | no | trivial — **no egress** |
| **derive-slow** | `/data/*.csv` + CMEMS | ζ/f, u/v, forecast/hindcast, inertial | **yes** | heavy |

`derive-fast` becomes pure local transformation — no network at all — so the
fast CronJob is `ingest → derive-fast` back-to-back in one pod (the `/data`
write between them is just the durable checkpoint). The slow CronJob is
`derive-slow`. The forecast/hindcast "straddle" from 017 resolves here: it is a
`derive-slow` step whose *position* input is the fast-fresh `/data` tables and
whose *field* input is CMEMS — so 017's **option B** (fast-tier re-advection off
a cached field) is later just "derive-fast reads a cached window + fresh `/data`
positions," no new machinery, same seam.

## What the seam buys

1. **Decoupled, egress-free re-derivation.** Rebuild every GeoJSON from a
   `/data` snapshot with zero contact to the drifter/glider/ship hosts —
   debugging, pinning, and the future `/analysis` app all read the same tables.
2. **Cleaner partial-failure composition.** Each derive step reads the
   best-available *persisted* input, so a single upstream hiccup no longer
   starves everything downstream in the same run — derive works off last-good
   `/data`. (Freshness stays visible via `manifest.json` + `build.json` so a
   silently-stale layer is legible, not hidden.)
3. **The download product is the build input.** No drift between "what you
   download" and "what the map shows"; no second producer to maintain.

## Code changes in this repo

Small, in keeping with the greenfield/reshape ethos:

1. **Split `main()`** into `ingest(out_data)` and `derive(in_data, out_map)`,
   with a stage switch (`--stage ingest|derive`, and within derive
   `--tier fast|slow`). A no-arg `build` still runs the whole chain end-to-end,
   writing the new `site/map/data/` (map) + `site/data/` (downloads) layout (see
   "GitLab Pages" below), so nothing regresses before the CronJobs exist.
2. **New `_data.py`** — the seam I/O: `write_tracks()` / `read_tracks()` over
   the CSV schema above, plus `write_manifest()`. `_clean` / `_gliders` /
   `_ship` / `_agulhas` keep doing the cleaning; `_data` persists their output
   and reads it back. `_clean.load_raw` (reads *source* snapshot CSVs) is
   unchanged; the new reader loads the *cleaned* tables.
3. **Configurable output roots** — the map root (`SITE_DATA`, now defaulting to
   `site/map/data/`) and the download root (`WHIRLS_DATA`, `site/data/`) become
   `--out` / env (`WHIRLS_SITE_DATA`, `WHIRLS_DATA`), so CronJobs write to PVC
   mounts (017 §Code-changes item 1).
4. **Atomic per-file writes** — `*.tmp` + `os.replace` for every table and
   artifact, so derive never reads a torn CSV that ingest is mid-rewrite (017
   §item 3; here it also guards the seam, not just the served tree).
5. **`docs/data.md`** — the `/data` schema + cleaning-rules spec (see
   "auditable" above), since the schema is now an interface in two directions.

## Land the split on GitLab Pages now (ahead of OpenShift)

The `/map` + `/data` + `/`→`/map/` browser-facing layout is not OpenShift-only —
it lands on the **current GitLab Pages** deploy now, for two reasons: it is what
we want anyway ("for now"), and running the client under `/map/` with a `/data/`
sibling first **de-risks the 017 gateway**, which serves the identical path
shape. The client is already all-relative (`./data/…`, no absolute-root refs —
confirmed), so nothing in `app.js` changes; only where the files sit.

Published tree (`public/`, produced from `cp -r site public`):

```
public/
  _redirects              #  /  /map/  302   (root → the map)
  map/
    index.html app.js style.css
    data/                 # derive output — the map's GeoJSON/PNG (today's site/data/)
  data/                   # ingest output — cleaned CSVs + raw/ + manifest.json
```

- The map moves from `/` to `/map/`; its own derived data moves with it to
  `/map/data/` (the app's `./data/…` refs resolve there unchanged).
- The new top-level `/data/` is the download tree (ingest's CSVs + `raw/`).
- `/` redirects to `/map/` via a GitLab Pages `_redirects` rule; a root
  `index.html` with `<meta http-equiv="refresh">` is the belt-and-suspenders
  fallback if `_redirects` needs confirming.
- Repo layout mirrors this — `site/map/` (shell) + `site/data/` (downloads) — so
  local dev matches production; `.gitlab-ci.yml`'s `pages` job and
  [docs/deploy.md](../../docs/deploy.md) update to match.

Ships as a **pure layout move first** (map under `/map/`, root redirect); `/data/`
fills in once ingest produces the CSVs — the two steps are independent.

## Relationship to 017 and the backlog

- **017 stays the topology/deploy plan** (gateway, Routes, PVC, CronJobs, TLS).
  This plan is what its `/data` backend and §Code-changes reference for the
  *producer's* internal shape. 017's fast/slow tier table is re-cut here into
  ingest / derive-fast / derive-slow.
- **Subsumes** the backlog's "Track DB parquet cache" — the seam is that
  persistence (CSV, not parquet; parquet deferred).
- **Gives a home** to "GPS despike at ingestion" — a visible ingest cleaning
  step reflected in the CSVs.

## Decisions locked (2026-07-04)

- **Read-back seam**, not one-pass dual-emit.
- **CSV** is the canonical `/data` form (human-inspectable + build input);
  parquet deferred.
- **`/data` gets full annotated tracks**; truncation/last-fix/overlays are
  map-side (`derive`).
- **Deployment detection + batch assignment are ingest** (part of cleaning),
  surfaced as `deployed_at` / `batch` in `platforms.csv`.
- **The `/map` + `/data` + `/`→`/map/` split lands on GitLab Pages now**, not
  just OpenShift — same browser-facing layout, de-risking the 017 gateway.
- **`/data` publishes raw *and* cleaned** — raw source files under `data/raw/` as
  provenance; the two ships stay two files.
- **`/data` is observations only** — CMEMS u/v / ζ/f / forecast fields are map
  overlays, never written to the download `/data/` (any field cache stays
  private).
- **Column names are the implementer's call** (the table is the proposed shape).

## Still open / to confirm

- **Raw drifter layout** — publish the Nextcloud snapshot CSVs verbatim under
  `data/raw/drifters/` (faithful but many files) vs. a single concatenated
  pre-clean CSV (smaller, closer to the actual de-dup input). Lean faithful;
  revisit if the snapshot count gets unwieldy.
- **`_redirects` support** — confirm GitLab Pages honours a `/`→`/map/`
  `_redirects` rule; else fall back to a meta-refresh root `index.html`.
- **CMEMS field cache location** — only if 017 option B / ROADMAP #15 Phase 3 is
  taken; a private unserved path, explicitly **not** `/data`.

## Sequencing

1. **GitLab Pages layout move** — restructure `site/` → `site/map/` + a root
   `_redirects` (`/`→`/map/`); the map serves at `/map/` unchanged. Independent of
   the refactor (pure layout), so it can ship first.
2. **Refactor `build.py`** into `ingest`/`derive` + `_data.py`; no-arg `build`
   writes the new `site/map/data/` + `site/data/` layout. Validate the map output
   is byte-identical to today's, just relocated.
3. **Emit `/data`** — cleaned CSVs + `platforms.csv` + `raw/` + `manifest.json`,
   and write `docs/data.md`. `/data/` fills in on Pages.
4. **Wire the stage/tier flags** so the 017 CronJobs can call `--stage ingest`,
   `--stage derive --tier fast|slow`.
5. **(017)** the CronJobs, gateway, and `/data` autoindex backend consume the
   above.
