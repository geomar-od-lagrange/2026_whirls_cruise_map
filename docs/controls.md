# Map controls, sidebar, and responsive layout

The map page is a full-height flex column: a slim header, then the map + sidebar
row. Interactive controls live in one **control dock** floating over the map;
read-only reference lives in the **sidebar**. This split — controls on the map,
reference beside it — keeps the two from competing for the same corner.

## The control dock

Every interactive control is a tab in a single top-right box, rather than a
column of separate Leaflet controls. The reason is vertical budget: separate
stacked boxes (instruments, currents, ships, deploy) summed to ~800 px, more than a
13″ laptop leaves below the header, so the lowest overflowed the map and collided
with the time slider. One dock shows **one tab body at a time**, so its footprint
is the tallest single tab (~a few hundred px, capped and internally scrollable at
`min(60vh, 460px)`), never the sum.

Tabs:

- **Deploy** — placing and interrogating virtual deployments (see the [Deploy
  tab](#the-deploy-tab) below). This is the app's primary capability, so it **leads
  the strip and opens by default**, chosen at build so the dock never flashes another
  tab first. A `/limits` probe runs off the critical path and only downgrades: when the
  deploy API is unreachable — the static/Pages fallback, which has no backend — the dock
  re-selects **Instruments**. Deploy stays present either way (it still places drops and
  exports CSV without computing drift).
- **Instruments** — one panel of marker toggles for *every* platform, in three
  families read top-to-bottom, each a two-column grid separated by a divider: the
  **drifter batches** (`batch 1`…`batch 5` plus the staged `batch X`), the
  **glider-group platforms** (Glider, Float, XSPAR, Waveglider — diamond swatches),
  and the two **ships** (M. Dufresne, Agulhas II — the former separate Ships tab,
  folded in here). Each row toggles that platform's markers; a small **select all /
  deselect all** text control at the bottom drives every row at once. The
  instruments' *track lines* ride the single "Show tracks" master in the scrubber
  box, not a per-tab row (see [Time slider](#time-slider)); unchecking an instrument
  still hides its track along with its markers. A ship row toggles its vessel's
  visibility, but the vessel only appears on the map once its first fix lands (an
  absent or failed feed shows no marker) — toggling the row before then is safe and
  applied on the first fix.
- **Currents** — the surface shadings as mutually-exclusive radios (None / Current
  speed / Vorticity ζ·f) plus the flow and near-inertial overlays as independent
  checkboxes, and an **Animate overlays** master (default off) that freezes the
  near-inertial animation to a still snapshot so time-scrubbing stays cheap (the flow
  overlay is a pre-rendered static streamline raster, always fluent — see
  [currents.md](currents.md)). Present only when the CMEMS field is available.

Each tab body is built once and shown/hidden by `display`, so a tab keeps its
state across switches (the deploy tool stays armed).

### The Deploy tab

Top to bottom: the **run settings** (release, a Direction switch, a Timing switch,
a **duration slider**, and a condensed **drop-spacing / speed** line), then the
per-deployment **manager**, then the **Deploy** arm toggle beside a **Clear**
button, then a collapsible **CSV import / export** menu at the very bottom.

The **deployment manager** replaces a single global Clear: each placed deployment
is one row — its id, release time, direction arrow, duration, drop count, and an
`instant` tag when it was released instantaneously — with a per-row visibility
toggle, a CSV export of that deployment's waypoints, and a delete. The **Clear**
button beside the Deploy toggle wipes them all at once; "Download all CSV" lives in
the CSV menu.

A run is described by **release + direction + timing + duration** (never
"forecast/hindcast" — that naming is reserved for the *field*'s provenance): the
release time is read-only and follows the app clock (one clock — "release at t"
means jump the scrubber to t), a sliding **Direction** switch selects
forward/backward, a sliding **Timing** switch selects along-track (water entry
staggered by the ship's transit) vs instantaneous (every drop at the release time),
and duration is a **1d / 2d / 5d / ∞** segmented slider (default 5d). The `∞` stop
advects to the end of the loaded field (the server truncates at the field edge).
The spacing and speed collapse to one short line — **"Every \[ \] km at \[ \]
kn"**; under instantaneous timing the ship speed shapes nothing, so its input greys
out and shows an `∞` glyph while the km spacing stays editable. Each switch flanks
its knob with the two option labels (no separate caption). Drift is always computed.

The **Deploy** toggle arms click-to-place: while on, the map wears a crosshair and
a click adds a path vertex, a double-click finishes, and right-click / Esc cancels.

The placed deployment draws its **drift lines** (one green line per drop, always
shown, growing up to the app clock as you scrub), the **drops** (water-entry discs),
and a **moving at-time marker** per drift that walks the line to the clock's instant.
The lines carry no analysed-vs-forecast dash split and nothing ahead of the clock,
and there is no vessel route drawn between the drops.

The dock **collapses** to a compact `Controls ⌄` pill via a chevron in its header
row. Open, the header shows only the chevron at the end of the tab strip (the tabs
name themselves); collapsed, it shows the "Controls" label and shrinks to fit, so
it can be tucked away to clear the map. The chevron is an SVG that rotates —
pointing up to collapse, down to expand.

## The sidebar

Reference read-outs, each a collapsible `<details>` section: data freshness, the
two ship read-outs, surface-currents and vorticity notes, and the awaiting-first-fix
list. Freshness, ships and currents open by default; the read-once/occasional panels
(vorticity, awaiting) start collapsed to keep the column short.

**Legends are contextual.** The speed colour bar shows only while the Current
speed shading is active, the ζ·f bar only while Vorticity is — a legend never sits
open for a shading that is off. The active shading radio drives this directly.

## Time slider

The app clock's scrubber sits bottom-centre over the map (see
[currents.md](currents.md) for what a scrub drives — the shadings snap to frames,
the tracks clip to the clock and their head markers move with it). The head row
carries the **Show tracks** master (left) and the live clock readout (right).
"Show tracks" is the single master for every *observed* track line — drifter,
glider, and ship — plus the real-drifter forecast lines, so they show and hide
together; it lives here because those tracks clip to this clock. It does not touch
the head markers (which follow the clock regardless) or the virtual-deployment drift
lines (owned by the deploy tool — scrubber-cropped, but not gated by this master).

The master is **eventually consistent**: checking or unchecking it flips the box
**immediately** and never blocks on the heavy reconcile it triggers (adding/removing
many polylines across every batch, glider, and ship, then re-clipping every track to
the clock). The handler only records the desired state and returns — so the checkbox
repaints at once — and the reconcile is deferred off the event (via a `setTimeout(0)`
macrotask, which yields a paint of the flipped box first; a microtask or `rAF` would
run before that paint and re-block it). At most one reconcile is in flight and it reads
the desired state **live** when it runs, so a rapid on→off→on collapses to the final
value (last-write-wins).

Below the range, a tick lane marks each 00Z day boundary with sparse `Jul 14`-style
labels (the end labels anchored inward so they stay inside the box). The wall-clock
**now** is a two-part affordance: a small blue dot sits on the scrub line itself (on
the line rather than in the tick lane, so it cannot collide with the date labels) with
a slow pulsing ring so the present reads at a glance, and — since the dot is
deliberately non-interactive so it never blocks grabbing a thumb parked near it — a
small **"now" chip** beside the clock readout carries the click, snapping the scrubber
back to the now hour through the same path a drag uses and dimming to a quiet outline
once the thumb already sits on now. Both appear only when now falls inside the covered
span. When there is no currents field (hence no scrubber), the master falls back to a
standalone chip in the same spot (also eventually consistent). Its
`z-index` sits above the map panes and popups but **below** Leaflet's controls, so
an expanded dock overlapping it on a short window draws on top rather than hiding
behind it.

## Responsive behaviour

Three regimes, by viewport width and height:

- **Wide** — sidebar beside the map (fixed 260 px, right).
- **Narrow (≤ 720 px wide) but tall** — the layout stacks: map on top, sidebar
  below as a strip capped at `35vh` and internally scrolled. A high-aspect-ratio
  window has the vertical room to spare for it there.
- **Narrow and short (≤ 720 px wide and ≤ 760 px tall)** — the sidebar drops out
  entirely and the map takes the whole viewport. Its detail is reference data, and
  the layer/ship controls remain reachable in the dock. The height cutoff is set
  so the sidebar yields to the map once its strip would eat into a short window,
  rather than clinging on until it owns half the screen.

The header is a single dark strip: the title plus a compact amber **⚠ Unofficial**
tag (full caution text on its tooltip), rather than a full-width banner — the tag
carries the warning at a fraction of the vertical cost.
