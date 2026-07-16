# Instrument palette

Every per-class identity colour on the map — drifter batches, virtual deployments,
the glider-group platforms, and the two ships — comes from one named **palette**.
A palette maps each instrument *class* to a single colour; the map derives
everything else (a marker's darker outline, a track line, a moving head) from it,
so one class reads in one colour wherever it appears.

The active palette is chosen at load from the URL — `?palette=<name>` — defaulting
to **`ember`**. `PALETTES` (the registry) and `PALETTE` (the resolved active one)
live at the top of `app.js`; the switch exists so alternatives can be compared
side-by-side in the real app without a rebuild.

## The class model: two ordinal ramps + shape-coded categoricals

The classes are not all alike, and the palette leans on that:

- **Drifter batches are ordinal** — a deployment *time sequence* (`deployment_1`,
  `deployment_2`, …). They take a single-hue **lightness ramp**, so successive
  deployments read apart by lightness while the whole set reads as one drifter
  family. `pre_deploy` (staged, not yet in the water) sits outside the ramp as a
  muted grey.
- **Virtual deployments are also ordinal** — a placed run can grow to two or three
  deployments, so they get their *own* ramp in a distinct hue family
  (`deploy_1..3`, cycled by placement order and wrapping after three).
- **Glider-group types and the two ships are nominal** — each a distinct
  categorical colour.

Modelling the two multi-member families as ramps is what makes the scheme fit: a
fully-categorical palette would need ~14 mutually-distinct hues, past the point
where categorical colours stay legible. Ramps collapse each ordinal family to one
hue, leaving only a handful of categorical slots. **Shape carries load too** —
drifters are circles, gliders diamonds, virtual deployments discs, ships icons — so
colour is a reinforcing cue, not the only one.

## The hard constraint: no clash with the shadings

The map renders the tracks and markers over a surface shading — **current speed**
(a green ramp: cream → yellow → green → dark-green) or **vorticity ζ/f** (a
diverging map: navy → teal → pale → tan → red → magenta → dark-purple). An identity
colour that sits in either gamut blends into it, so the palette is built around the
gaps between the two: **orange, azure/blue, and dark neutrals** clear both, while
green/teal collides with speed and blue/magenta collides with vorticity.

The two ramps are placed on **opposite warm/cool ends** so each stays legible over
both fields and they never collide with each other. Under the default `ember`
palette the drifters run warm (amber→crimson) and the virtual deployments cool
(azure→indigo); the glider-group and ship colours are picked from the same
clash-safe space (a violet, a magenta, a spring-green, a near-black, a deep blue, a
dark maroon). Because the shadings are fully opaque and the data layers are drawn
opaque on top (see *Opacity* below), a saturated identity colour reads cleanly over
either field.

Colour-blindness is kept **reasonable, not over-fit**: the batch ramp is ordered by
lightness (which every colour-vision type preserves), the categorical count is small,
shape disambiguates, and each instrument row can be toggled off — so a viewer can
always isolate an overlapping pair. The design does not chase a perfect
fully-colour-blind-safe categorical set, which is impossible at this class count.

## One colour per track: line = dot = head

For every observed instrument, the identity colour is the *only* colour: the track
**line**, the fixed **deployment dot** at its start, and the **moving head** that
rides the clock all share it. The real drifters' **forecast** line takes the same
colour and is styled exactly like the observed track — the one exception is the
dashed **bridge** that spans the reporting-lag gap between the last transmitted fix
and now, whose dash (not a colour change) marks where observed hands off to forecast.

**Highlighting** a track (clicking it) keeps its own identity colour and widens the
line, while every *other* instrument desaturates — so a selection changes contrast
and weight, never hue. (Dimming throughout is by desaturation, never transparency.)

**Heads** all wear a **white outline** — drifter circles, glider diamonds, ship
discs, and virtual at-time markers alike — so a head reads against any shading
regardless of its fill. A single **deployment-mark radius** is shared by the real
drifter deployment dots, the virtual-deployment drops, and the forecast **now-ghost**
(a small dot left at a drifter's now-position once the clock scrubs into the future,
marking the observed→forecast hand-off while the bright head walks on).

## Opacity

Data layers are **opaque**: a diluted line or marker would mix toward the shading
beneath it and muddy crossing tracks of different batches, working against the
clash-safe design, so every marker fill, track line, forecast line, deploy line, and
ship line draws at full opacity. The **shadings** are opaque too (only land is
transparent). Transparency is reserved for things that are meant to layer: the
**flow-overlay** streamlines sit over the shading at reduced alpha, and the
near-inertial animation uses a faint per-frame fade for its motion trail.

## Where it lives (and how to change it)

- **`PALETTES` / `PALETTE`** (`app.js`) — the registry and the resolved active
  palette. Each palette maps class keys (`deployment_1..8`, `deploy_1..3`,
  `pre_deploy`, the four glider types, `ship_md`, `ship_ag`) to hex colours.
- **`styleForBatch()` → `BATCH_STYLES`** — drifter batch marker/line style, built
  from the palette (fill = identity colour, outline = a derived darker stroke).
- **`GLIDER_STYLES`** — glider-group colours, from the palette.
- **`deployColor(id)`** — the virtual-deployment colour for placement `id`, cycling
  `deploy_1..3`.
- **`VESSELS.{md,agulhas}`** — the two ships' colours, from the palette.

To **add a drifter batch**, extend the drifter ramp in the palette (or let it fall
back to the first step). To **change the whole scheme**, edit or add a palette in
`PALETTES` and set it as the default; `?palette=<name>` renders any registered one.
The bundled palettes are **`ember`** (default — warm drifters / cool virtual),
**`azure`** (the inverse split), **`vivid`** (each batch a distinct hue — loud, less
colour-blind-safe), and **`current`** (the pre-palette colours, kept for comparison).

## Non-instrument colours

A few colours deliberately sit *outside* the identity palette so they never read as
an instrument: `TRACK_COLOR` (a fallback for an identity-less track — every real
track passes its identity colour instead), `INERTIAL_COLOR` (cyan, the near-inertial
animation), and the magenta a virtual track/drop lifts to when selected (it pops off
the blue virtual-deployment set).
