# WHIRLS floats

*Implemented. See [docs/gliders.md](../../docs/gliders.md) (floats fold into the
glider-group doc) and [docs/data.md](../../docs/data.md) for the current state.*

Add the profiling **floats** the WHIRLS operational map (observations.ipsl.fr)
recently gained as a new *FLOAT* instrument type, rendered here alongside the
drifters, gliders, and ships.

## Source

The floats live on the **same** IPSL WHIRLS THREDDS server the gliders already
come from, in a new subfolder *under* `GLIDERS`:

```
…/catalog/WHIRLS/OBSERVATIONS/GLIDERS/FLOATS/
    floats_track.csv              aggregate of every float, id in the `filename` column
    mr_float_soton_positions.csv  SOTON float (id 6594), per-institution
    mr_float_ugot_positions.csv   UGOT  float (id 65a0), per-institution
```

Two physical floats so far: **UGOT** (`65a0`, U. Gothenburg) and **SOTON**
(`6594`, Southampton). Columns are `time,latitude,longitude,filename` — the same
`time`/`latitude`/`longitude` the glider parser already keys on, timestamps in
the naive-UTC ISO form `_parse_time` already handles.

## Which files, and why floats don't ride the glider auto-discovery

We read the **per-institution `mr_float_<inst>_positions.csv` files and skip the
aggregate `floats_track.csv`**. Both carry the same fixes, but the aggregate
**lags** — at build time it was observed with fewer, older fixes (e.g. SOTON 4
fixes to 2026-07-04 on the aggregate vs 6 to 2026-07-05 on the per-institution
file). The operational map reads the aggregate; we prefer the fresher
per-institution source. (First cut read the aggregate to mirror upstream, then
switched once the lag showed.)

`fetch_float_sources()` discovers the files from the FLOATS `catalog.xml` (like
the gliders) and drops `floats_track.csv` by name, so a new institution's float
file appears with **no code change**.

Floats still can't ride the glider `_csv_datasets` one-CSV-one-platform rule: the
platform identity is in a **column** (`filename` → `65a0_015_01_technical.txt`),
not the file name. So `parse_float_source` groups each file's rows by the
`filename`'s leading `_`-token (`65a0`/`6594`) — the operational map's own rule —
and maps it to a label (`65a0→UGOT`, `6594→SOTON`; an unmapped id falls back to
itself). Grouping by the column (not assuming one-float-per-file) stays correct
if a file ever carries more than one float. Each float becomes one
`Platform(type="float")`.

## Downstream is free

A float is just another `_gliders.Platform` with `type="float"`, so it flows
through the existing glider pipeline unchanged: `write_gliders`/`read_gliders`
(generic over `platform_type`), `gliders_geojson` (latest Point + deployed-track
LineString, leading vessel-transit pruned like a glider — floats are ship-
deployed too), `_platform_records` (a `platforms.csv` row), and the
forecast/hindcast. The floats land in `gliders.csv` with `platform_type=float`.

## Changes

- `_gliders.py` — `fetch_float_sources() → list[Source]` (discover the FLOATS
  catalog, skip `floats_track.csv`) + `parse_float_source() → list[Platform]`
  (group each file by `filename` prefix, `_FLOAT_LABELS` map).
- `build.py` ingest — loop the per-institution sources, publish each raw
  (`raw/gliders/mr_float_<inst>_positions.csv`), extend the `gliders` list with
  the parsed floats before `write_gliders` (moved to cover both), best-effort in
  its own block.
- `app.js` — one `GLIDER_STYLES.float = { color:"#a855f7", label:"Floats" }`
  entry (the operational map's own purple). The Instruments control and track
  groups are keyed by `type`, so both floats collapse into one **Floats** row,
  each still individually click-to-highlight by `id`.
- Docs — `docs/gliders.md` (floats as a third GLIDERS-group type + the
  identity-in-a-column / skip-the-aggregate quirks), `docs/data.md`
  (`platform_type` gains `float`).
- `tests/test_floats.py` — the filename-column split (two floats, label map,
  time-sort, interleaving separated).

## Naming

Kept `gliders.csv` / `_gliders.py` / `docs/gliders.md` rather than renaming to
"instruments": THREDDS itself nests FLOATS under GLIDERS, and the existing
gliders doc already commits to following IPSL's *Gliders* grouping (it documents
the XSPAR spar buoy — not a glider either — there for the same reason). Floats
extend that rationale rather than overturning it.
