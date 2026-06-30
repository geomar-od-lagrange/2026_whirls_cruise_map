# Backlog

Unscheduled ideas, not yet promoted to a plan.

- **Track DB parquet cache** — persist the derived `(D_number, date_UTC)` table
  and ingest only new snapshots, if full re-read from the share gets slow.
- **Track thinning** — simplify/decimate dense trajectories (the ship's 10-min
  grid, ≤5 min drifter snapshots over weeks) for rendering performance, now that
  every fix draws a dot.
- **Awaiting-first-fix view** — surface staged-but-not-yet-transmitting drifters.
- **Currents bbox auto-fit** — track the drifter cloud instead of a fixed box.
- **Time scrubber** — animate positions/tracks over time.
- **Time-varying drift forecast** — the drift forecast (`docs/forecast.md`)
  advects through a single *frozen* CMEMS field, so its 6 h mark spans ~one full
  6-hourly field step. Advect through several forecast timesteps instead for a
  faithful multi-step track; the natural next step if the frozen 6 h proves too
  coarse. Costs more data + an interpolation-in-time in the stepper.
- **Flow-trail land bleed** — the animated current trails reach onto land near
  the coast while the speed shading stops cleanly at it. Cause: leaflet-velocity
  needs a hole-free grid and has no land mask, so land is fed in as zero velocity
  (`_currents._component`, `nan -> 0`) and the client bilinearly interpolates
  across the ocean->0 boundary, smearing coastal velocities onshore; the stride-3
  coarsening widens it. The shading instead masks land with per-pixel alpha
  (`to_speed_png`). Not fixed — a fix would erode the ocean by one coarse cell
  before serving, or draw a land polygon above the flow pane; both trade some
  near-shore coverage. Cosmetic only.
- **FTLE ocean clip** — the FTLE field has no land mask, so after the latitude
  registration correction (`docs/ftle.md`) ~1.6% of contour vertices still cross
  the coast (genuine high-FTLE near-coast filaments, deepest ~30 km inland). Mask
  the field to ocean before contouring — rasterize a bundled Natural-Earth land
  polygon onto the FTLE grid (keeps `_ftle` self-contained), or reuse the CMEMS
  ocean mask already fetched in the build (couples the two steps).
