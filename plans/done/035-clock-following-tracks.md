# Plan 035 — the clock moves the map: clipped tracks, moving heads, deploy timing

> Implemented — see [docs/trajectories.md](../../docs/trajectories.md) (clock-clipped
> tracks + moving heads), [docs/deploy_tool.md](../../docs/deploy_tool.md) (timing
> switch, trail/faint drift lines, legend), [docs/controls.md](../../docs/controls.md)
> and [docs/currents.md](../../docs/currents.md) (scrubber), plus
> [docs/ship.md](../../docs/ship.md) / [docs/gliders.md](../../docs/gliders.md).

Follow-up fixes on plan 034's deployment-first frontend, from first use of the
scrubber-driven app:

1. **Scrubbing must move the picture, not just dots on it.** Today every track
   (drifter, glider, ship, virtual) draws in full at all clock positions, the
   latest-position head markers sit still, and only the small at-time dots
   move. Fix: the clock clips every time-aware track to what has happened *by*
   the clock, and the head markers themselves ride the clipped end.
2. **Deployment timing switch.** A placed deployment always staggers water-entry
   times by ship speed (a real vessel steaming the route). Add a switch for the
   other common case: an idealised **instantaneous** release (all drops enter at
   the release time), e.g. to read pure flow-field deformation of a line.
3. **Say what solid vs dashed means.** The virtual drift lines encode field
   provenance (solid = analysed currents, dashed = forecast, split at the run's
   `analysis_edge`) but nothing in the UI says so.
4. **Slider cosmetics.** The "now" mark + label collide with the day tick
   labels; the tick labels read as bare `MM-DD`.

## 1 · Clock-following tracks and heads

One uniform rule for every time-aware layer: at clock *t*, a track displays the
portion **already traversed at *t*** (sample times ≤ *t*, the crossing segment
interpolated), and the layer's position marker sits at the interpolated
position at *t*. Before a track's first sample the layer is hidden; past its
last sample the full track shows and the marker parks at the latest position
(the pre-034 "now" view — so an untouched clock at load looks unchanged).

A backward virtual run needs no special case: normalised to ascending absolute
time, its trail grows from the water's origin toward the release point as the
clock advances, and shows in full at the release instant — exactly like a
forward particle.

- **Observed tracks** (drifter true tracks, glider tracks, ship tracks): the
  per-segment polylines are clipped by group membership (whole segments in/out,
  the crossing segment trimmed via `setLatLngs`), so per-fix hover tooltips
  vanish with the hidden future. The **head markers** (batch-coloured drifter
  circles, glider diamonds, ship discs) move to the at-clock position with the
  bracketing fix's tooltip; they clamp to the latest fix when the clock is
  past the track's end and hide before its start. Drifter heads only move once
  `tracks.geojson` is loaded (it stays lazy — no time series, no movement).
- **Virtual deploy tracks**: clipped the same way, but the not-yet-traversed
  remainder stays visible as a **faint thin line** instead of vanishing — a
  forward run placed at the current clock would otherwise render nothing and
  read as a bug; the faint line is the planned answer, the strong trail the
  animation. Both parts keep the solid/dashed provenance split. The at-time
  marker stays (it is the virtual track's head).
- The small at-time dots on drifter/glider/ship tracks are **removed** — the
  moving heads replace them (two markers on the same spot would z-fight). The
  at-time machinery remains for deployments, whose marker-set click axis stays.

## 2 · Deploy timing switch

`Timing: Along track ⇄ Instantaneous` in the Deploy Settings. Along track is
today's behaviour (water entry staggered by `cum_km / ship speed`).
Instantaneous sets every seed's `start` to the release time; the ship-speed
knob greys out (it shapes nothing), the preview/status drop the transit
estimate, the drop-time-spread pre-validation is trivially 0, and the manager
row tags the run `instant`. The API needs no change — it already takes
absolute per-seed starts.

## 3 · Line legend

A compact legend in the Deploy tab: solid = drift through analysed currents;
dashed = drift through forecast currents (beyond the analysis edge); faint =
ahead of the app clock (not yet traversed); grey dashed = vessel route.
docs/deploy_tool.md explains the same in prose.

## 4 · Slider

The "now" marker becomes a small blue dot sitting on the range track itself
(no label, no tick-lane collision); day tick labels become `Jul 14`-style
(UTC short month + day).

## Touched

`site/map/app.js` (at-time → clock machinery, head registries, track builders,
ship layer, deploy tool + draw path, slider), `site/map/style.css`,
`docs/deploy_tool.md`, `docs/controls.md`, `docs/trajectories.md`,
`docs/ship.md`.
