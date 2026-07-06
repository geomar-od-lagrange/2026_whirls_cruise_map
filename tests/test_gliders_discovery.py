"""Discovery and dialect handling for the WHIRLS observations-portal source.

The gliders/floats live on a plain Apache autoindex (not a THREDDS catalog), so
platforms are discovered by scanning the directory listing's ``.csv`` links, and
one seaglider (the SeaExplorer) ships a mixed-delimiter, BOM-prefixed,
day-first-dated CSV. These tests pin both behaviours; the happy-path parsing of
the comma feeds is already covered elsewhere.
"""
from __future__ import annotations

from datetime import datetime, timezone

from whirls_cruise_map._gliders import Source, _csv_datasets, _parse_csv, parse_source

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
