# Backlog

Unscheduled ideas, not yet promoted to a plan.

- **Track DB parquet cache** — persist the derived `(D_number, date_UTC)` table
  and ingest only new snapshots, if full re-read from the share gets slow.
  *Largely subsumed by [018](done/018-ingest-derive-data-seam.md)*: the ingest→derive
  seam persists the cleaned tracks (as CSV, not parquet), which is the
  persistence this wanted; parquet stays a deferred efficiency companion there.
- **Track thinning** — simplify/decimate dense trajectories (the ship's 10-min
  grid, ≤5 min drifter snapshots over weeks) for rendering performance, now that
  every fix draws a dot. *Coarse-zoom visual clutter is handled by the
  zoom-gated dots from [031](done/031-track-zorder-and-data-link.md) (dots hidden
  below the finest zooms); the layers still exist, so the performance decimation
  this wanted is still open.*
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
