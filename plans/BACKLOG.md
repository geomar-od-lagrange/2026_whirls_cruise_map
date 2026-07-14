# Backlog

Unscheduled ideas, not yet promoted to a plan.

- **Track DB parquet cache** — persist the derived `(D_number, date_UTC)` table
  and ingest only new snapshots, if full re-read from the share gets slow.
  *Largely subsumed by [018](done/018-ingest-derive-data-seam.md)*: the ingest→derive
  seam persists the cleaned tracks (as CSV, not parquet), which is the
  persistence this wanted; parquet stays a deferred efficiency companion there.
- **Track thinning** — simplify/decimate dense trajectories (the ship's 10-min
  grid, ≤5 min drifter snapshots over weeks) for rendering performance. The
  per-fix dots were removed in [031](done/031-track-zorder-and-data-link.md)
  (each track is now one polyline per fix-to-fix segment), so the visual clutter
  is gone, but a dense track is still ~one polyline per fix — the performance
  decimation this wanted is still open. **Now also a transfer-size problem**:
  `tracks.geojson` is 14.6 MB for 146 drifters × ~2 weeks, and the target
  scenario (drifters stay in the water past cruise end) is ~200 drifters ×
  ~100 days under a ~10 MB transfer budget. The bytes are dominated by the
  per-vertex `fixes` records (ISO strings + mostly-null fields), not
  coordinates. Levers, in payoff order: (1) compact parallel arrays instead of
  per-vertex objects (epoch-delta ints for time, drop null fields); (2)
  Douglas–Peucker thinning at ~100–200 m tolerance (retained vertices keep
  their real times, so position-at-time interpolation still works; inertial
  loops survive at display scale); (3) precompressed artifacts inflated
  client-side, since GitLab Pages serves uncompressed (the gateway nginx can
  gzip; Pages can't). Together plausibly 2–5 MB for the target scenario.
  Deliberately kept out of [034](034-deployment-focused-app.md).
- **GPS despike at ingestion** — several Deployment-1 drifters show single-fix
  out-and-back GPS spikes (one stray fix implying 15–140 m/s to both
  neighbours; seen in D-577, D-602, D-606, D-610, D-630, worst in D-611 and
  D-612 with 4 each, per the 2026-07-03 inertial survey). The build currently
  ingests them raw into tracks, markers, and the derived popup speeds. A
  despike in `_clean.py` — e.g. flag a fix whose implied speed exceeds a
  threshold on **both** adjacent segments — would clean tracks and popups. Under
  [018](done/018-ingest-derive-data-seam.md) this becomes a *visible* ingest step,
  reflected in the published `drifters.csv` (a `despiked` flag or documented
  drop) rather than an invisible in-memory filter.
- **Awaiting-first-fix view** — surface staged-but-not-yet-transmitting drifters.
- **Currents bbox auto-fit** — track the drifter cloud instead of a fixed box.
- **Time scrubber** — animate positions/tracks over time.
- **Flow-trail land bleed** — the animated current trails reach onto land near
  the coast while the speed shading stops cleanly at it. Cause: leaflet-velocity
  needs a hole-free grid and has no land mask, so land is fed in as zero velocity
  (`_currents._component`, `nan -> 0`) and the client bilinearly interpolates
  across the ocean->0 boundary, smearing coastal velocities onshore; the stride-3
  coarsening widens it. The shading instead masks land with per-pixel alpha
  (`to_speed_png`). Not fixed — a fix would erode the ocean by one coarse cell
  before serving, or draw a land polygon above the flow pane; both trade some
  near-shore coverage. Cosmetic only.
