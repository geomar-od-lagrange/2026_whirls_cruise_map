# 047 — Instrument palette (#35): head & track colours

> **Done.** Implemented — see [docs/palette.md](../../docs/palette.md). (This plan
> is kept for the design rationale + the shading-clash analysis in `tmp_palettes/`.)

Issue #35 asks for a discrete colour scheme that (a) tells every instrument class
apart, (b) **does not clash with the speed or vorticity surface shadings** (the
hard constraint), and (c) is reasonable for colourblind viewers (soft — per-class
toggles let a CVD viewer disentangle any residual overlap, so we don't over-fit).

This plan covers the **palette seam + candidate review** (rendered examples for a
human pick). The chosen palette then feeds the track-colour follow-ups below.

## The class model — two ordinal ramps + shape-coded categoricals

The insight that makes a ~14-class scheme tractable: not all classes are nominal.

- **Drifter batches** are ORDINAL (a deployment time-sequence) → a single-hue
  **lightness ramp** (`deployment_1..8`; plan for ~8 as 2-3 more batches land).
- **Virtual deployments** are also ordinal and multi-membered (2-3 planned) → a
  second ramp in a *distinct* hue family (`deploy_1..3`).
- **Glider-group types** (seaglider/waveglider/xspar/float) and the **two ships**
  are nominal categoricals. Shape already separates them (circles=drifters,
  diamonds=gliders, discs=virtual, icons=ships), so colour carries less load than
  "by colour alone" implies.

The two ramps sit on **opposite warm/cool ends** so each pops over both shadings
and they never collide with each other.

## The hard constraint, made measurable

The shadings paint (sampled, at their 0.6α composite over the sea tone):
- **speed** (`cmocean.speed`): cream → yellow → green → dark-green.
- **vorticity** (`cmocean.curl`): navy → teal → pale → tan → dusty-red →
  **magenta** → dark-purple.

So the clash-safe gaps are **orange, azure/blue, and dark neutrals**; green/teal
clashes speed and blue/magenta clashes curl. Because the shadings are muted (0.6α),
*saturated* colours pop over them almost regardless of hue — the real risk is
mid-saturation tones matching the shadings' own muted chroma, and pale ramp ends.

`tmp_palettes/gen_palettes.py` scores every candidate colour by CIEDE2000 distance
to both shading gamuts (`pop_report.txt`), draws the colours as bare tracks over
the two shadings (`shading_strips.png` — lines are dense + UNPADDED in the app, so
no white halo rescues them), and emits the `PALETTES` object. All three candidates
clear both gamuts; the current baseline does not (teal batch, green virtual, blue
drifters on the navy/teal vorticity, magenta drifters on the magenta vorticity).

## Candidates (rendered in the app, `tmp_palettes/app_candidates_{speed,vorticity}.png`)

- **ember** (recommended) — warm drifters (amber→crimson) / cool virtual
  (azure→indigo). Warm pops over both fields; strongest all-around.
- **azure** — cool drifters (sky→navy) / warm virtual. Great over speed and the
  warm vorticity lobe; softer over teal eddies (the cool-on-cool trade-off).
- **vivid** — each batch a distinct hue (the literal "by colour alone" reading).
  Loud and maximally separable; less "these are all one drifter family," and one
  green batch is marginal over speed.

Shared categoricals across all three (verified clash-safe + mutually distinct):
Glider `#7c4dff`, Waveglider `#e6299a`, XSPAR `#111827`, Float `#00d68f`, ships
`#12408f`/`#8a1030`, staged `#8a94a3`.

## The seam (implemented)

`site/map/app.js`: a `PALETTES` registry selectable at load via `?palette=<name>`
(default `current` = today's colours, so the shipped map is unchanged until a
palette is picked). Every per-class identity colour funnels through the active
`PALETTE`: `BATCH_STYLES`/`styleForBatch` (fill = identity, stroke = derived
darker), `GLIDER_STYLES[*].color`, `DEPLOY_COLOR` (= `deploy_1` for now),
`VESSELS.{md,agulhas}.trackColor/markerColor`. Observed track *lines* already
adopt the identity colour (plan 041's `line = dot = head` convergence via
`addTrackSegments(..., src.color)`), so the palette flows through markers, dots,
heads, and lines together.

## Follow-ups (after the palette is chosen)

These were deferred by the user until the scheme is picked:

1. **Highlight in the identity colour.** The selected track is hard-coded orange
   (`SELECTED_COLOR = "#ff8c42"`, from the old single-track-colour scheme). Change
   to: widen the picked track and dim the others by lowering saturation — no hue
   swap. (`trackColor`/`restyleLine`, `SELECTED_COLOR`.)
2. **Forecast line = observed identity colour.** Real-drifter forecast lines are
   all violet (`VIOLET_FORECAST_COLOR = "#7c3aed"`). Make each forecast line take
   its drifter's identity colour and obey the same highlight rule. The reporting-
   lag **bridge stays dashed** (that dash is what marks where the forecast starts).
3. **Now-ghost dot.** When the scrubber goes into the future, leave a small
   deployment-dot-sized identity-colour dot at the now-position and walk the bright
   head forward with time — the dot marks the observed→forecast hand-off so the
   present position stays fixed. (Not a faded/dimmed full-size head — that read as
   noisy; a small dot like the deployment dots.)

Forecast lines are styled IDENTICALLY to the observed track (same colour, weight,
opacity, select/dim rule); the only differently-styled part is the dashed reporting-
lag bridge. The now-ghost dot marks where the switch to forecast happens.

4. **Per-deployment virtual colour.** DONE — `DEPLOY_COLOR` removed; a `deployColor(id)`
   helper cycles successive placements through `deploy_1..3` (wrapping after three).
   The colour flows to each deployment's drops, drift lines, and at-time markers; the
   live placement preview uses the NEXT id's colour; and the Deploy-tab manager shows a
   `.batch-swatch` colour indicator per row (stored as `deployments[id].color`). Track/
   drop *selection* still lifts to magenta (pops off the blue deploy set).

5. Docs pass: DONE — [docs/palette.md](../../docs/palette.md) written;
   `docs/batches.md` per-batch-colour section updated to point at it; the stale
   "single true-track colour" / "orange true track, violet forecast" comments in
   `app.js` refreshed to the identity-colour reality; this plan moved to
   `plans/done/`. (Opacity audit — data layers set to α=1 — also landed here.)
