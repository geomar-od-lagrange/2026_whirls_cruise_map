# Ingest the two new UVP floats (6596, 6597) — a second float CSV schema

*Implemented. See [docs/gliders.md](../../docs/gliders.md) (the floats section,
now covering both CSV schemas) and [docs/data.md](../../docs/data.md) for the
current state.*

Implements issue #6. The IPSL FLOATS folder now serves two UVP floats
(`uvp_float_6596_locations.csv`, `uvp_float_6597_locations.csv`) whose CSV
schema differs from the existing `mr_float_*` files. Discovery and raw-publish
already pick them up; only `parse_float_source` drops them.

## The two float schemas

| | `mr_float_*` (existing) | `uvp_float_*` (new) |
|---|---|---|
| Header | `time,latitude,longitude,filename` | `profile,utc_time,latitude,longitude` |
| Time column | `time` | `utc_time` |
| Float identity | leading `_`-token of the `filename` column | in the **file name** (`uvp_float_6596_locations` → `6596`) |

Lat/lon columns and `_parse_time`'s offset handling are already shared.

## Change

`src/whirls_cruise_map/_gliders.py` — `parse_float_source`:

- Accept `utc_time` as an alias for the `time` column (pick whichever header is
  present).
- Identity: if a `filename` column exists, keep the group-by-leading-token path
  unchanged. Otherwise derive one float's id from the source file name, matching
  only the established `uvp_float_<id>_locations` pattern. A no-`filename`,
  non-UVP source (the aggregate `floats_track`) still yields nothing — the
  "can't separate floats → emit nothing" rule is *narrowed*, not dropped.
- `_FLOAT_LABELS`: leave `6596`/`6597` unmapped (fall back to raw id). The
  institution is not established from the file; inventing a label is out of
  scope.

## Tests / docs / roadmap

- `tests/test_floats.py`: add a UVP case (identity from file name, `utc_time`
  header) and reframe `test_missing_filename_column_yields_nothing` as the
  aggregate-with-no-file-name-identity case.
- `docs/gliders.md`, `docs/data.md`: describe the two float source schemas.
- `plans/ROADMAP.md` item 20: note the UVP schema.
