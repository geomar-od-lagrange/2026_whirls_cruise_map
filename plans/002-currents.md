# Currents inclusion

Make the CMEMS surface-current overlay a proper, decision-driven feature rather
than the single-snapshot placeholder the MVP shipped. The cruise targets
mesoscale eddies ("whirls") in the Cape Basin / Agulhas region, so currents are
central, not decorative: they should both show *today's* flow and help anticipate
where drifters will go.

## Current state (MVP)

`_currents.py` subsets CMEMS surface `uo`/`vo` over the study region (+10°),
picks the single time nearest "now", coarsens the 1/12° grid by 3× (~1/4°), and
emits one leaflet-velocity grid (`currents.json`, ~1 MB). The site renders it as
a toggleable particle animation. Verified correct (grid orientation, header).

Known rough edges: product was discovered ad hoc
(`cmems_mod_glo_phy-cur_anfc_0.083deg_PT6H-i`); a depth-clamp warning is ignored;
the field silently fell back to the previous step when the dataset was mid-update;
no speed legend; no sense of forecast evolution.

## Goals

1. Show today's surface flow clearly, with a readable speed scale.
2. Make the eddies legible — the whole point of the cruise.
3. Support anticipation: where is the flow heading over the coming days
   (forecast), so deployments and drifter tracks can be reasoned about.
4. Stay a static artifact set the Leaflet app consumes; keep payload sane.

## Decisions to make

### Temporal scope — the big one

- **(a) Single t=0 snapshot** (MVP). Simplest, smallest. No sense of evolution.
- **(b) Analysis→forecast time series + slider.** The `anfc` product is analysis
  plus a multi-day forecast (exact horizon TBC). Emit a short stack of grids
  (e.g. a handful of past-analysis steps through several forecast days) and add a
  time slider that swaps the leaflet-velocity layer's `data`. Directly serves
  goal 3; multiplies payload by the number of steps.

Recommendation: **(b)**, with a modest step count and coarser grid to bound size.
This is what "analysis / forecast t=0" in `features.md` is reaching for.

### Product

- `…_PT6H-i` — 6-hourly **instantaneous total** surface velocity (current MVP).
  Best match for "today's flow" and for a time slider.
- `…_P1D-m` — **daily mean**. Smoother, smaller, less tidal noise; coarser in time.

Recommendation: keep `PT6H-i` for instantaneous total currents; revisit if tidal
aliasing makes the animation noisy, in which case daily-mean is the fallback.
Open: confirm whether "total" here includes tides/Stokes, and whether that's
what we want for surface-drifter context.

### Eddy emphasis

Particles alone show eddies only subtly. Options, cheapest first:

- **Current-speed colormap** underlay (|velocity|) — leaflet-velocity can colour
  particles by speed; add a legend. Cheap, no new data. Reveals jets and eddy rims.
- **Sea-level (ADT/SLA) context** from the SSH field (`zos`) or the altimetry
  product — mesoscale eddy cores show as sea-level highs/lows; contours make the
  whirls unambiguous. Adds a second product and a second artifact.

Recommendation: add the **speed colour scale + legend** now; treat **SLA/ADT
contours** as a follow-up sub-feature (its own small plan) if the team wants
explicit eddy outlines.

### Payload & resolution

Single snapshot at ~1/4° is ~1 MB. A time stack needs a budget: trade grid
stride (`COARSEN_STRIDE`) against step count. Sketch: stride ~4–6 and ~8–16
steps → a few MB total. Decide a target ceiling for `currents.json` (or split
per-timestep files fetched lazily).

### Robustness / cleanups

- Pin the surface depth to the dataset's shallowest level to silence the
  depth-clamp warning.
- Handle the "dataset being updated" case explicitly (we already fall back to the
  latest available step — make that intentional and surfaced, e.g. record the
  field's valid time in the artifact and show it in the UI).
- Record `refTime`/valid-time in the UI so users know how fresh the field is.

### Auth in CI

Building currents in GitHub Actions later needs `copernicusmarine` credentials
as secrets — shared dependency with the automation step (003), noted there too.

## Out of scope here

Drifter-derived velocities; full Lagrangian forecast/particle advection;
basin-wide products beyond the study region.

## Open questions

1. Temporal scope: single t=0 snapshot, or analysis→forecast time slider
   (recommended)? If a slider — how far forward (forecast days) and how many steps?
2. Eddy emphasis: speed colour scale only (recommended now), or also pursue
   SLA/ADT eddy contours as a follow-up?
3. Product: stay on 6-hourly instantaneous total currents, or prefer daily mean?
4. Payload ceiling for `currents.json` (or move to lazily-fetched per-step files)?
