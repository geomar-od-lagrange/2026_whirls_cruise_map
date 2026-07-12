"""Discovery and dialect handling for the WHIRLS observations-portal source.

The gliders/floats live on a plain Apache autoindex (not a THREDDS catalog), so
platforms are discovered by scanning the directory listing's ``.csv`` links, and
one seaglider (the SeaExplorer) ships a mixed-delimiter, BOM-prefixed,
day-first-dated CSV. The wave gliders add a second discovery+parse path for the
NetCDF-only ``wg1169`` (``.nc`` links, skipping the ``.ncml`` pointer; parsed with
xarray). These tests pin those behaviours; the happy-path parsing of the comma
feeds is already covered elsewhere.
"""
from __future__ import annotations

from datetime import datetime, timezone

from whirls_cruise_map._gliders import (
    Source,
    _csv_datasets,
    _nc_datasets,
    _parse_csv,
    parse_source,
    parse_waveglider_nc,
)

# A trimmed Apache autoindex: the data links plus the parent and sort-column
# links a real listing carries, which must be ignored.
AUTOINDEX = """\
<html><head><title>Index of /.../SEAGLIDERS</title></head><body>
<h1>Index of /.../SEAGLIDERS</h1>
<table>
<tr><th><a href="?C=N;O=D">Name</a></th><th><a href="?C=M;O=A">Modified</a></th></tr>
<tr><td><a href="/aeris/whirls/data/observations/GLIDERS/">Parent Directory</a></td></tr>
<tr><td><a href="sg283_track.csv">sg283_track.csv</a></td><td>2026-07-06 09:45</td></tr>
<tr><td><a href="seaexplorer.csv">seaexplorer.csv</a></td><td>2026-07-06 00:01</td></tr>
</table>
</body></html>
"""

DIR_URL = "https://observations.ipsl.fr/aeris/whirls/data/observations/GLIDERS/SEAGLIDERS/"


def test_csv_datasets_discovers_data_links_and_skips_navigation():
    got = _csv_datasets(AUTOINDEX, DIR_URL)
    # Only the two data files — the parent-dir link and the ?C= sort links are
    # dropped by requiring a .csv suffix and no "/" in the href.
    assert got == [
        ("sg283", DIR_URL + "sg283_track.csv"),
        ("seaexplorer", DIR_URL + "seaexplorer.csv"),
    ]


def test_csv_datasets_ignores_absolute_csv_hrefs():
    # A .csv reachable only by absolute path is not a file in *this* folder.
    html = '<a href="/elsewhere/other.csv">x</a><a href="here_track.csv">y</a>'
    assert _csv_datasets(html, DIR_URL) == [("here", DIR_URL + "here_track.csv")]


# The SeaExplorer export: UTF-8 BOM, ``;``-separated header, ``,``-separated data
# rows, and day-first ``DD/MM/YYYY HH:MM:SS`` times. Column order (lon before lat)
# also differs, so name-mapping — not position — must place them.
SEAEXPLORER = (
    "\ufefftime;longitude;latitude\n"
    "03/07/2026 10:49:00,11.53671667,-38.47646667\n"
    "05/07/2026 20:32:00,12.31161667,-37.17500000\n"
)


def test_seaexplorer_dialect_parses_via_parse_source():
    platform = parse_source(Source("seaexplorer", "seaglider", SEAEXPLORER))
    assert platform is not None
    assert platform.type == "seaglider"
    assert len(platform.fixes) == 2
    first, second = platform.fixes
    # Day-first date read as UTC; latitude mapped by name despite lon coming first.
    assert first[0] == datetime(2026, 7, 3, 10, 49, tzinfo=timezone.utc)
    assert first[1] == -38.47646667 and first[2] == 11.53671667
    assert second[0] == datetime(2026, 7, 5, 20, 32, tzinfo=timezone.utc)


def test_all_comma_feed_still_parses():
    # The plain comma dialect (epoch time here) keeps working through the shared
    # reader — a guard against the delimiter sniff mis-detecting it.
    text = "time,longitude,latitude\n1783078052.0,11.5467,-38.4692\n"
    fixes = _parse_csv(text)
    assert len(fixes) == 1
    assert fixes[0][1] == -38.4692 and fixes[0][2] == 11.5467


# The WAVEGLIDERS folder mixes shapes: a track CSV (melktert), a NetCDF (wg1169),
# and its sibling NcML pointer (.ncml). The CSV scan must take only the CSV; the
# NetCDF scan only the .nc — never the .ncml (whose ".nc" is followed by "ml").
WAVEGLIDER_INDEX = """\
<html><head><title>Index of /.../WAVEGLIDERS</title></head><body>
<table>
<tr><th><a href="?C=N;O=D">Name</a></th></tr>
<tr><td><a href="/aeris/whirls/data/observations/GLIDERS/">Parent Directory</a></td></tr>
<tr><td><a href="melktert_track.csv">melktert_track.csv</a></td></tr>
<tr><td><a href="wg1169_WHIRLS_Cruise_L1.nc">wg1169_WHIRLS_Cruise_L1.nc</a></td></tr>
<tr><td><a href="wg1169_WHIRLS_Cruise_L1.ncml">wg1169_WHIRLS_Cruise_L1.ncml</a></td></tr>
</table>
</body></html>
"""
WG_DIR = "https://observations.ipsl.fr/aeris/whirls/data/observations/GLIDERS/WAVEGLIDERS/"


def test_csv_scan_takes_only_the_csv_wave_glider():
    # The CSV path picks up melktert and ignores the .nc / .ncml siblings.
    assert _csv_datasets(WAVEGLIDER_INDEX, WG_DIR) == [
        ("melktert", WG_DIR + "melktert_track.csv"),
    ]


def test_nc_datasets_takes_the_nc_and_skips_ncml_and_navigation():
    # Only the .nc — the .ncml NcML pointer, the CSV, the parent link, and the
    # ?C= sort link are all dropped. Filename kept whole for identity + raw publish.
    assert _nc_datasets(WAVEGLIDER_INDEX, WG_DIR) == [
        ("wg1169_WHIRLS_Cruise_L1.nc", WG_DIR + "wg1169_WHIRLS_Cruise_L1.nc"),
    ]


def test_nc_datasets_ignores_absolute_nc_hrefs():
    html = '<a href="/elsewhere/other.nc">x</a><a href="local.nc">y</a>'
    assert _nc_datasets(html, WG_DIR) == [("local.nc", WG_DIR + "local.nc")]


def test_parse_waveglider_nc_round_trips_time_lat_lon(tmp_path):
    # Build a tiny NetCDF (time/latitude/longitude, one non-finite fix), serialize
    # it, and parse it back the way the build ingests wg1169's .nc bytes.
    import numpy as np
    import xarray as xr

    # Out of time order, with the NaN-lat fix (07:10) to be dropped.
    times = np.array(
        ["2026-07-01T07:00:00", "2026-07-01T06:50:00", "2026-07-01T07:10:00"],
        dtype="datetime64[ns]",
    )
    ds = xr.Dataset(
        {
            "latitude": ("time", [-38.43, -38.44, np.nan]),
            "longitude": ("time", [11.58, 11.57, 11.59]),
        },
        coords={"time": times},
    )
    path = tmp_path / "wg1169_WHIRLS_Cruise_L1.nc"
    ds.to_netcdf(path)

    platform = parse_waveglider_nc(path.name, path.read_bytes())
    assert platform is not None
    # Identity from the leading _-token of the file name; type is the new class.
    assert platform.id == "wg1169"
    assert platform.type == "waveglider"
    # The NaN-lat fix (07:10) is dropped; the remaining two come back time-sorted, UTC.
    assert len(platform.fixes) == 2
    assert platform.fixes[0][0] == datetime(2026, 7, 1, 6, 50, tzinfo=timezone.utc)
    assert platform.fixes[0][1] == -38.44 and platform.fixes[0][2] == 11.57
    assert platform.fixes[1][0] == datetime(2026, 7, 1, 7, 0, tzinfo=timezone.utc)
    assert platform.fixes[1][1] == -38.43 and platform.fixes[1][2] == 11.58


def test_parse_waveglider_nc_drops_nat_time_keeps_rest(tmp_path):
    # A single NaT timestamp must drop only that fix, not sink the whole track
    # (the epoch conversion of a NaT sentinel would otherwise raise).
    import numpy as np
    import xarray as xr

    times = np.array(
        ["2026-07-01T06:50:00", "NaT", "2026-07-01T07:00:00"], dtype="datetime64[ns]"
    )
    ds = xr.Dataset(
        {
            "latitude": ("time", [-38.44, -38.50, -38.43]),
            "longitude": ("time", [11.57, 11.99, 11.58]),
        },
        coords={"time": times},
    )
    path = tmp_path / "wg1169_L1.nc"
    ds.to_netcdf(path)
    platform = parse_waveglider_nc(path.name, path.read_bytes())
    assert platform is not None
    assert [f[0] for f in platform.fixes] == [
        datetime(2026, 7, 1, 6, 50, tzinfo=timezone.utc),
        datetime(2026, 7, 1, 7, 0, tzinfo=timezone.utc),
    ]


def test_parse_waveglider_nc_missing_coords_returns_none(tmp_path):
    # A NetCDF without latitude/longitude is not a usable track.
    import numpy as np
    import xarray as xr

    ds = xr.Dataset(
        {"temperature": ("time", [1.0, 2.0])},
        coords={"time": np.array(["2026-07-01", "2026-07-02"], dtype="datetime64[ns]")},
    )
    path = tmp_path / "sv3-999_L1.nc"
    ds.to_netcdf(path)
    assert parse_waveglider_nc(path.name, path.read_bytes()) is None
