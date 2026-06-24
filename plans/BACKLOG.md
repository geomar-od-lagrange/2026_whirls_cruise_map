# Backlog

Unscheduled ideas, not yet promoted to a plan.

- **Track DB parquet cache** — persist the derived `(D_number, date_UTC)` table
  and ingest only new snapshots, if full re-read from the share gets slow.
- **Track thinning** — simplify/decimate dense trajectories (≤5 min snapshots
  over weeks) for rendering performance.
- **Drifter velocity from fixes** — derive speed/heading from successive
  positions, since the `U_speed_mps`/`U_Dir_deg` columns are unreliable.
- **Awaiting-first-fix view** — surface staged-but-not-yet-transmitting drifters.
- **Currents bbox auto-fit** — track the drifter cloud instead of a fixed box.
- **Time scrubber** — animate positions/tracks over time.
- **FTLE ocean clip** — the FTLE field has no land mask, so after the latitude
  registration correction (`docs/ftle.md`) ~1.6% of contour vertices still cross
  the coast (genuine high-FTLE near-coast filaments, deepest ~30 km inland). Mask
  the field to ocean before contouring — rasterize a bundled Natural-Earth land
  polygon onto the FTLE grid (keeps `_ftle` self-contained), or reuse the CMEMS
  ocean mask already fetched in the build (couples the two steps).
