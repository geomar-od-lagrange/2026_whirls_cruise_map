# locations

Drifter locations are fetched from a read-only Nextcloud share
(`https://cloud.geomar.de/s/as5DjLdynsMNapt/download`) as a zip of snapshot
CSVs. Each ingest run downloads the whole share fresh — no caching, no
incremental fetch — since the data is only a few dozen MB.

See [data.md](data.md) for the cleaned output this feeds (`drifters.csv`),
the de-duplication rules applied to it, and how `manifest.json` records the
share URL as provenance.
