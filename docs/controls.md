# Map controls, sidebar, and responsive layout

The map page is a full-height flex column: a slim header, then the map + sidebar
row. Interactive controls live in one **control dock** floating over the map;
read-only reference lives in the **sidebar**. This split — controls on the map,
reference beside it — keeps the two from competing for the same corner.

## The control dock

Every interactive control is a tab in a single top-right box, rather than a
column of separate Leaflet controls. The reason is vertical budget: four stacked
boxes (instruments, currents, ships, deploy) summed to ~800 px, more than a 13″
laptop leaves below the header, so the lowest overflowed the map and collided
with the time slider. One dock shows **one tab body at a time**, so its footprint
is the tallest single tab (~a few hundred px, capped and internally scrollable at
`min(60vh, 460px)`), never the sum.

Tabs:

- **Instruments** — the overlay masters (True track / Forecast / Hindcast) and one
  row per drifter batch and glider platform. Unchecking an instrument hides its
  markers *and* every overlay riding on it; an overlay master toggles that line
  for all instruments at once.
- **Currents** — the surface shadings as mutually-exclusive radios (None / Current
  speed / Vorticity ζ·f) plus the flow-trail and near-inertial overlays as
  independent checkboxes. Present only when the CMEMS field is available.
- **Ships** — one checkbox per vessel, added on its first fix (so an absent or
  failed feed never shows a dead toggle); a placeholder until then.
- **Deploy** — the PoC deployment-placement tool.

Each tab body is built once and shown/hidden by `display`, so a tab keeps its
state across switches (the deploy tool stays armed, the ships list persists).

The dock **collapses** to a compact `Controls ⌄` pill via a chevron in its header
row. Open, the header shows only the chevron at the end of the tab strip (the tabs
name themselves); collapsed, it shows the "Controls" label and shrinks to fit, so
it can be tucked away to clear the map. The chevron is an SVG that rotates —
pointing up to collapse, down to expand.

## The sidebar

Reference read-outs, each a collapsible `<details>` section: data freshness, the
two ship read-outs, surface-currents and vorticity notes, the drift explainer, and
the awaiting-first-fix list. Freshness, ships and currents open by default; the
read-once/occasional panels (vorticity, drift, awaiting) start collapsed to keep
the column short.

**Legends are contextual.** The speed colour bar shows only while the Current
speed shading is active, the ζ·f bar only while Vorticity is — a legend never sits
open for a shading that is off. The active shading radio drives this directly.

## Time slider

The forecast time slider sits bottom-centre over the map. It carries no header
line: the offset (now / +12 h …) is already the selected tick's label, and that it
is the *shading* that scrubs — not the tracks — is apparent from watching the map.
Only the selected frame's valid time is shown, small, in the box's lower-right
corner. Its `z-index` sits above the map panes and popups but **below** Leaflet's
controls, so an expanded dock overlapping it on a short window draws on top rather
than hiding behind it.

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
