> **Implemented.** Current-state docs: [docs/gliders.md](../../docs/gliders.md)
> (glider/float source + autoindex discovery + CSV dialects) and
> [docs/ship.md](../../docs/ship.md) (Agulhas source + bake rationale).

# Move IPSL CSV ingest from THREDDS to the AERIS observations portal

## Motivation

Every CSV we currently ingest from IPSL comes from the WHIRLS **THREDDS**
server (`thredds-x.ipsl.fr`): the Agulhas II ship track (`_agulhas.py`) and the
glider-group tracks — XSPAR, seagliders, floats (`_gliders.py`). THREDDS is a
heavy Java stack that fails intermittently; at time of writing it returns `503`
while the data behind it is fresh.

The same files are published on the WHIRLS operational centre's own data
portal, a plain Apache autoindex:

```
https://observations.ipsl.fr/aeris/whirls/data/observations/
    SHIPS/agulhas_positions.csv
    GLIDERS/XSPAR/xspar_xeos_track.csv
    GLIDERS/SEAGLIDERS/{sg283_track.csv, sg284_track.csv, seaexplorer.csv}
    GLIDERS/FLOATS/{floats_track.csv, mr_float_soton_positions.csv, mr_float_ugot_positions.csv}
    GLIDERS/WAVEGLIDERS/   (empty at time of writing)
```

This portal is:

- **the more canonical origin** — it *is* `observations.ipsl.fr/aeris/whirls`,
  the source `docs/gliders.md` already names as what the operational map draws;
- **more reliable** — a static file server with far less overhead than THREDDS,
  live and fresh right now while THREDDS is down;
- **CORS-open** — sends `Access-Control-Allow-Origin: *` on both the directory
  listing and the CSVs (THREDDS `fileServer` sends no `-Origin` header at all).

We **keep the build-time bake** regardless of the CORS unlock: baking keeps the
map working (with the last-good data) when the source is down, and avoids
hammering upstream. Moving these feeds client-side is explicitly out of scope.

## Decisions

- **Scope: all IPSL CSVs.** Repoint both `_agulhas.py` (ship) and `_gliders.py`
  (gliders + floats). Fully off THREDDS for CSV ingest.
- **Architecture unchanged.** Same fetch → clean → derive → bake pipeline, same
  `Source` / `Platform` / `parse` shapes, same published artifacts. This is a
  source-URL + discovery-mechanism swap, not a redesign.
- **Discovery changes from `catalog.xml` to HTML autoindex.** The portal has no
  THREDDS `catalog.xml` (404). Each platform-type folder is an Apache directory
  listing; we discover CSVs by scanning `<a href="…​.csv">` links instead of
  parsing an InvCatalog XML tree. Relative hrefs (`sg283_track.csv`) resolve
  against the folder URL; absolute/sort/parent links (`href="/…"`, `?C=N;O=D`)
  are skipped by requiring a `.csv` suffix and no `/`.
- **FTLE / satellite NetCDF stays on THREDDS.** Those use the OPeNDAP `dodsC`
  service (plan 003), which the autoindex portal does not replace. Out of scope.

## Schema notes (verified against the live portal)

Most files parse with the **existing** `_parse_csv` / `_parse_time` unchanged —
headers map by name (case-insensitive) and the time column is epoch / naive-ISO
/ offset-ISO as today. One exception:

- **`SEAGLIDERS/seaexplorer.csv`** is a different dialect: **semicolon**
  delimiter, a **UTF-8 BOM** on the header, and **`DD/MM/YYYY HH:MM:SS`** dates.
  To ingest it (a real SeaExplorer glider, not currently shown), `_parse_csv`
  must sniff `;` vs `,` and strip the BOM, and `_parse_time` must accept the
  day-first date. Low risk — the file exists and the format is unambiguous in
  the European operational context (day-first). If we chose not to handle it, it
  would parse to zero fixes and be silently dropped, which is worse than either
  handling or explicitly excluding it.

- **`GLIDERS/WAVEGLIDERS/`** is a new group, empty for now. Introducing a
  `waveglider` platform *type* would also touch the client colour/label map, so
  it is **not** added in this plan — noted as a follow-up for when it carries
  data.

## Changes

### `src/whirls_cruise_map/_gliders.py`

- Replace `THREDDS = "https://thredds-x.ipsl.fr/thredds"` with the portal base
  `BASE = "https://observations.ipsl.fr/aeris/whirls/data/observations"`.
- `_GROUPS` and `FLOATS_CATALOG` become **folder URLs** (…/GLIDERS/XSPAR/ etc.),
  not `catalog.xml` URLs. Rename `FLOATS_CATALOG` → `FLOATS_DIR`.
- Rewrite `_csv_datasets(catalog_xml)` → `_csv_datasets(index_html, dir_url)`:
  regex the `href="…​.csv"` links, skip any containing `/`, return
  `(id, dir_url + name)` where `id` strips a trailing `_track.csv` (unchanged id
  rule; the float caller still recomputes its own id from the filename and skips
  the aggregate). Drop the `xml.etree` import.
- Extend `_parse_csv`: sniff delimiter (`;` if the header line contains one,
  else `,`) and strip a leading BOM before header parsing, so `seaexplorer.csv`
  is read.
- Extend `_parse_time`: after the epoch and `fromisoformat` attempts, try
  `%d/%m/%Y %H:%M:%S` (day-first) as UTC.
- Rewrite the module docstring and the FLOATS section comment: no more
  DatasetScan / `catalog.xml`; describe the autoindex discovery. Keep the
  best-effort framing.

### `src/whirls_cruise_map/_agulhas.py`

- One-line `CSV_URL` swap to
  `https://observations.ipsl.fr/aeris/whirls/data/observations/SHIPS/agulhas_positions.csv`.
  Schema is identical, so `parse()` is untouched. Update the docstring's
  "THREDDS fileServer / no `Access-Control-Allow-Origin`" wording — the new
  source *is* CORS-open, but we still bake it server-side for resilience.

### Docs

Update every doc that names THREDDS as the CSV origin to name the portal, and
correct the CORS rationale (bake now for resilience, not because CORS forbids
client fetch):

- `docs/gliders.md` — source section, `fileServer`/`catalog.xml` mechanics.
- `docs/ship.md` — Agulhas source rows/URL and the CORS paragraph.
- `docs/data.md` — the `raw/agulhas_ii.csv` / glider-THREDDS provenance lines.
- `docs/deploy.md` — the "THREDDS source is not CORS-open" note.
- `plans/ROADMAP.md` — the entries calling the Agulhas/glider source a
  "non-CORS IPSL THREDDS CSV".

### Tests

- `grep` the suite for THREDDS URLs and `catalog.xml` fixtures; repoint fixtures
  and any autoindex-parsing test to HTML. Add a `_csv_datasets` case over a
  small Apache-autoindex HTML fixture, and a `seaexplorer`-dialect
  (semicolon/BOM/day-first) `_parse_csv` case.

## Validation

1. Run the ingest against the live portal (`503`-proof: THREDDS is down, so a
   successful glider+ship fetch also proves we are off THREDDS): confirm
   `data/ship_agulhas_ii.csv`, `data/gliders.csv`, and the raw copies populate,
   including all seagliders, XSPAR, both floats, and `seaexplorer`.
2. `pytest`.
3. Review agent pass over the diff.

## Follow-ups (not in this plan)

- Add the `waveglider` type once `WAVEGLIDERS/` carries data.
- Consider moving these baked feeds client-side now that CORS allows it (weigh
  against the offline-resilience the bake provides).
