// Control-dock / UI-chrome builders: the tabbed top-right dock and its tab
// bodies (Instruments rows, Currents shading rows), the bottom-centre time
// slider, and the lower-left cursor readout. Behaviour-preserving relocation
// from app.js — the builders receive their app-level collaborators (map,
// layer groups, and a `deps` bag of app functions) so this module stays a
// leaf: it imports only from leaf modules and never back from app.js.
//
// `L` (Leaflet) is a page global here, exactly as in app.js.

import { formatLatLon } from "../format.js";

// The one instrument panel — this control, not the Leaflet layer control, owns
// marker visibility for every platform. Three families read top-to-bottom, each a
// two-column grid set off by a divider (plan 037 / #24):
//   • drifter batches (round swatch, marker fill colour) — "batch 1"…"batch X";
//   • glider-group platforms (diamond swatch) — Glider / Float / XSPAR / Waveglider;
//   • the two cruise vessels (round swatch, track colour) — M. Dufresne / Agulhas II.
// A small "select all / deselect all" text control at the bottom drives every row.
//
// The instruments' *track lines* ride the single "Show tracks" master in the scrubber
// box (plan 036), not a per-tab row. `tracksOverlay.groups` holds the drifter+glider
// track groups keyed by the same instrument key as `markerGroups` (filled in as they
// load — see main). A track group shows only when both its instrument row and the
// master are on, so unchecking an instrument hides its markers *and* its track.
//
// `vessels` are eager descriptors `{ name, color, setVisible(on), wantVisible }`
// built by main(): the two ships are known at config time, so their rows render up
// front; the row's checkbox drives `setVisible`, which no-ops the map add until the
// vessel's first fix lands (main's reveal applies the last checkbox state then).
// Returns `{ setTracksOn(on) }` for the external master (initial state off).
//
// `deps` carries the app-level collaborators the rows reconcile against (threaded so
// this module stays a leaf, not importing back from app.js): updateClock, getClockMs,
// tracksOn, setForecastBatchVisible, getHideOutliers, setHideOutliers,
// rebuildObservedTracks, GLIDER_STYLES, instrumentOrder, styleForBatch, batchLabel,
// instrumentCount.
export function buildInstrumentRows(div, map, markerGroups, tracksOverlay, vessels = [], deps) {
  const {
    updateClock,
    getClockMs,
    tracksOn,
    setForecastBatchVisible,
    getHideOutliers,
    setHideOutliers,
    rebuildObservedTracks,
    GLIDER_STYLES,
    instrumentOrder,
    styleForBatch,
    batchLabel,
    instrumentCount,
  } = deps;

  // Pre-deployment drifters are staged (still aboard, not in the water), so they
  // start hidden; deployment batches start visible. sync() (called at build) then
  // reconciles the map to this initial state.
  const batchOn = {};
  for (const batch of Object.keys(markerGroups))
    batchOn[batch] = batch !== "pre_deploy";

  // Adopt the module master so tracks show at first paint when defaulted on (#28):
  // sync() at build then lights the glider groups immediately instead of waiting on
  // the eager 18 MB drifter fetch to flip it (that fetch still lights the drifters
  // on arrival). Ships adopt it directly via ship.setTrackShown(tracksOn) in main.
  let tracksMasterOn = tracksOn;

  const toggle = (layer, show) =>
    layer && (show ? layer.addTo(map) : map.removeLayer(layer));

  // This panel also governs the real-drifter forecast lines: a forecast shows only for a
  // batch that is checked here (clipForecast reads this live). Re-run the clock after each
  // sync so a batch toggle re-clips the forecasts at once, like the observed tracks.
  setForecastBatchVisible((batch) => batchOn[batch] !== false);

  function sync() {
    for (const batch of Object.keys(markerGroups)) {
      toggle(markerGroups[batch], batchOn[batch]);
      toggle(tracksOverlay.groups[batch], batchOn[batch] && tracksMasterOn);
    }
    updateClock(getClockMs()); // reconcile the forecast lines to the new selection
  }

  // The scrubber's "Show tracks" master flips this; sync() reconciles line visibility.
  // Reads tracksOverlay.groups live, so track groups that arrive after build (the
  // eager drifter tracks) reconcile on the next call.
  function setTracksOn(on) {
    tracksMasterOn = on;
    sync();
  }

  // Every checkable row registers here so the bottom "select all / deselect all"
  // control can drive them through their REAL change handlers (markers + tracks
  // reconcile, not just the box visual) — last-write-wins across all three families.
  const rows = [];
  const addRow = (parent, { checked, color, diamond, label, onChange }) => {
    const row = L.DomUtil.create("label", "batch-row", parent);
    const cb = L.DomUtil.create("input", "", row);
    cb.type = "checkbox";
    cb.checked = checked;
    // Glider rows draw a diamond swatch matching their map markers; drifters and
    // ships keep the round swatch (marker fill / track colour respectively).
    const swatch = L.DomUtil.create(
      "span", diamond ? "batch-swatch batch-swatch-diamond" : "batch-swatch", row
    );
    swatch.style.background = color;
    L.DomUtil.create("span", "batch-text", row).textContent = label;
    const apply = () => onChange(cb.checked);
    cb.addEventListener("change", apply);
    rows.push({ cb, apply });
  };

  // Hide-outliers toggle (#30): drop out-and-back GPS spikes from the observed tracks
  // (client-side, from the derived speeds already downloaded). Rebuilds the tracks'
  // segments + clock entries in place. Default on (a clean view); flip to see raw fixes.
  const outRow = L.DomUtil.create("label", "batch-row batch-outlier", div);
  const outCb = L.DomUtil.create("input", "", outRow);
  outCb.type = "checkbox";
  outCb.checked = getHideOutliers();
  L.DomUtil.create("span", "batch-text", outRow).textContent = "Hide GPS outliers";
  outCb.addEventListener("change", () => {
    setHideOutliers(outCb.checked);
    rebuildObservedTracks();
  });

  // Drifter batches sort ahead of the glider-group types (float pinned last), so the
  // family split falls between them: one two-column grid for the drifter batches, a
  // divider, one grid for the gliders. Rows wrap to one column at the dock's narrow
  // width (CSS .batch-grid). Only the drifters carry a family header — "batch 1..5" is
  // not self-explaining, whereas Glider/Float/XSPAR/Waveglider and the two ships name
  // themselves, so they lean on the plain dividers instead (#24 follow-up).
  L.DomUtil.create("span", "batch-family-head", div).textContent = "Drifters";
  const drifterGrid = L.DomUtil.create("div", "batch-grid", div);
  let gliderGrid = null;
  for (const batch of Object.keys(markerGroups).sort(instrumentOrder)) {
    const isGlider = GLIDER_STYLES[batch] != null;
    if (isGlider && !gliderGrid) {
      L.DomUtil.create("hr", "batch-divider", div);
      gliderGrid = L.DomUtil.create("div", "batch-grid", div);
    }
    const group = markerGroups[batch];
    addRow(isGlider ? gliderGrid : drifterGrid, {
      checked: batchOn[batch],
      color: GLIDER_STYLES[batch]?.color ?? styleForBatch(batch).fillColor,
      diamond: isGlider,
      label: `${batchLabel(batch)} (${instrumentCount(group)})`,
      onChange: (on) => { batchOn[batch] = on; sync(); },
    });
  }

  // Ships: the third family, folded in from the former separate Ships tab (#24).
  // Rendered eagerly from config; each row's checkbox toggles that vessel's group
  // visibility via setVisible, which no-ops until the vessel's first fix lands.
  if (vessels.length) {
    L.DomUtil.create("hr", "batch-divider", div);
    const shipGrid = L.DomUtil.create("div", "batch-grid", div);
    for (const v of vessels)
      addRow(shipGrid, {
        checked: v.wantVisible,
        color: v.color,
        diamond: false,
        label: v.name,
        onChange: (on) => v.setVisible(on),
      });
  }

  // Select all / deselect all: small text links at the bottom that flip every row's
  // checkbox and fire its handler, so a bulk toggle reconciles markers and tracks
  // exactly as clicking each box would.
  const setAll = (on) => { for (const r of rows) { r.cb.checked = on; r.apply(); } };
  const selRow = L.DomUtil.create("div", "batch-selectall", div);
  const mkLink = (text, on) => {
    const b = L.DomUtil.create("button", "batch-selectall-link", selRow);
    b.type = "button";
    b.textContent = text;
    b.addEventListener("click", () => setAll(on));
  };
  mkLink("select all", true);
  L.DomUtil.create("span", "batch-selectall-sep", selRow).textContent = "/";
  mkLink("deselect all", false);

  // Apply the initial visibility (hides the default-off pre-deployment batch,
  // which main() adds to the map before this dock is built).
  sync();
  return { setTracksOn };
}

// Surface-currents rows for the dock's Layers tab. Replaces the old
// L.control.layers box: the shadings (`shadings`, e.g. speed / ζ·f / None) are
// mutually-exclusive **radios** — selecting one adds its layer and removes the
// others; the flow / near-inertial layers (`overlays`) are independent
// **checkboxes**. Rows are appended to `div` (the dock owns the box). The
// initially-active shading is whichever `shadings` layer is already on the map
// (speed, added by default), else "None". `onShadingChange(name, legendEl)` fires on
// every selection and once at build, so the caller can render the active shading's
// colour scale into `legendEl` (the dock legend below the radios) and key the lazy
// ζ·f prefetch to the active shading.
export function buildShadingRows(div, map, shadings, overlays, onShadingChange, overlayAnimation) {
  const names = Object.keys(shadings);
  let active = names.find((n) => n !== "None" && map.hasLayer(shadings[n])) ?? "None";
  // The active shading's colour-class legend, right under the radios (null when
  // there is no shading to show one for). onShadingChange fills it per selection.
  let legendEl = null;
  const select = (name) => {
    active = name;
    for (const n of names) {
      if (n === name) shadings[n].addTo(map);
      else map.removeLayer(shadings[n]);
    }
    onShadingChange?.(name, legendEl);
  };

  if (names.length) {
    L.DomUtil.create("span", "dock-cap", div).textContent = "Surface shading";
    for (const name of names) {
      const row = L.DomUtil.create("label", "batch-row", div);
      const rb = L.DomUtil.create("input", "", row);
      rb.type = "radio";
      rb.name = "dock-shading";
      rb.checked = name === active;
      L.DomUtil.create("span", "batch-text", row).textContent = name;
      rb.addEventListener("change", () => rb.checked && select(name));
    }
    legendEl = L.DomUtil.create("div", "dock-legend", div);
  }

  const overlayNames = Object.keys(overlays);
  if (overlayNames.length) {
    if (names.length) L.DomUtil.create("hr", "batch-divider", div);
    // TEMPORARILY DISABLED (issue #25): the Current-flow overlay and the near-inertial
    // animation are broken, so their checkboxes — and the "Animate overlays" toggle that
    // governs the animation — are rendered greyed-out and inert (never added to the map)
    // pending a follow-up MR. To re-enable, restore the change handlers below and drop
    // the `disabled` flag + `batch-row-disabled` class (and this note).
    const disabledRow = (parent, label, subtle) => {
      const row = L.DomUtil.create(
        "label", `batch-row${subtle ? " batch-subtle" : ""} batch-row-disabled`, parent
      );
      const cb = L.DomUtil.create("input", "", row);
      cb.type = "checkbox";
      cb.checked = false;
      cb.disabled = true;
      L.DomUtil.create("span", "batch-text", row).textContent = label;
      row.title = "Temporarily disabled — see issue #25";
    };
    for (const name of overlayNames) disabledRow(div, name, false);
    if (overlayAnimation) disabledRow(div, "Animate overlays", true);
    L.DomUtil.create("p", "ft-hint", div).textContent =
      "Flow & animation temporarily disabled (issue #25).";
  }

  onShadingChange?.(active, legendEl); // render the initial legend for the default shading
}

// One top-right control housing every map control as tabs, so the footprint is
// bounded by the tallest single tab instead of the sum of stacked boxes (which
// overflow a 13" laptop and collide with the time slider). `tabs` is
// `[{ id, label, render(bodyDiv) }]`; each body is built once and shown or hidden
// by `display`, so tabs keep their state (the deploy tool's arming, the ships
// list) across switches. A header caret collapses the whole box to just its bar so
// the controls can be tucked away to clear the map. Click propagation is disabled
// on the whole box, so no per-tab guard is needed. `initialId` names the tab open on
// first paint — chosen synchronously so the dock never visibly flips tabs after load;
// it falls back to the first tab when absent or unmatched.
export function buildControlDock(map, tabs, initialId) {
  const control = L.control({ position: "topright" });
  // Populated in onAdd; lets a late async probe re-select a tab (the reachability
  // downgrade to Instruments) without the dock having blocked on that network call.
  const handle = {};
  control.onAdd = () => {
    const div = L.DomUtil.create("div", "map-control control-dock");
    L.DomEvent.disableClickPropagation(div);
    L.DomEvent.disableScrollPropagation(div);

    // Top bar: the tab strip and a collapse caret, plus a "Controls" label that
    // shows only when collapsed (open, the tabs speak for themselves, so the caret
    // just sits at the end of the tab row). Everything below the bar lives in
    // `panel`, so one display toggle hides tabs' bodies, leaving the bar as the
    // re-open handle.
    const bar = L.DomUtil.create("div", "dock-bar", div);
    const strip = L.DomUtil.create("div", "dock-tabs", bar);
    L.DomUtil.create("span", "dock-title", bar).textContent = "Controls";
    const caret = L.DomUtil.create("button", "dock-collapse", bar);
    caret.type = "button";
    // A single chevron glyph; CSS rotates it to point up when expanded (click to
    // collapse) and down when collapsed (click to expand) — cleaner than swapping
    // ^ / v text glyphs, which render unevenly across fonts.
    caret.innerHTML =
      '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">' +
      '<path d="M3.5 6.25 8 10.75l4.5-4.5" fill="none" stroke="currentColor" ' +
      'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"/></svg>';

    const panel = L.DomUtil.create("div", "dock-panel", div);
    const buttons = [];
    const bodies = [];
    const show = (i) => {
      bodies.forEach((b, j) => (b.style.display = j === i ? "" : "none"));
      buttons.forEach((b, j) => b.classList.toggle("active", j === i));
    };

    tabs.forEach((tab, i) => {
      const btn = L.DomUtil.create("button", "dock-tab", strip);
      btn.type = "button";
      btn.textContent = tab.label;
      btn.addEventListener("click", () => show(i));
      buttons.push(btn);

      const body = L.DomUtil.create("div", "dock-body", panel);
      tab.render(body);
      bodies.push(body);
    });

    let collapsed = false;
    const paintCollapse = () => {
      panel.style.display = collapsed ? "none" : "";
      div.classList.toggle("collapsed", collapsed);
      caret.setAttribute("aria-expanded", String(!collapsed));
      caret.setAttribute("aria-label", collapsed ? "Expand controls" : "Collapse controls");
    };
    caret.addEventListener("click", () => {
      collapsed = !collapsed;
      paintCollapse();
    });

    const initialIdx = Math.max(0, tabs.findIndex((t) => t.id === initialId));
    show(initialIdx);
    paintCollapse();

    handle.show = show;
    handle.idToIndex = new Map(tabs.map((t, i) => [t.id, i]));
    return div;
  };
  // Select a tab by id after first paint — used only by the async reachability probe to
  // downgrade to Instruments when the forecast API is absent. A no-op for an unknown id.
  control.select = (id) => {
    const i = handle.idToIndex?.get(id);
    if (i != null) handle.show(i);
  };
  return control;
}

// Compact UTC readout for the clock, e.g. "2026-07-13 18:00Z" (the clock is hourly,
// so seconds are always 00 and dropped).
export function formatClock(ms) {
  return new Date(ms).toISOString().replace("T", " ").replace(/:\d{2}\.\d+Z$/, "Z");
}

// UTC short-month names for the slider's day-tick labels ("Jul 14" reads faster than
// a bare "07-14").
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// The app's single clock (bottom-centre): a datetime scrubber at **1 h granularity**
// over the shading frames' full span `[first valid_time, last valid_time]`. The clock
// time is `displayedFieldTime` exactly (an ISO string); the caller snaps rasters/flow
// to the nearest 12 h frame, while the near-inertial animation and the deploy tool's
// run start consume the exact clock. A drag therefore moves the clock in 1 h steps
// while the shown field frame changes every 12 h.
//
// Options: `t0Ms` (span start, epoch ms), `spanHours` (integer span length),
// `value` (initial hour offset from t0), `nowMs` (wall-clock, for the now marker),
// and `onChange(value)` — called with the hour offset on every drag and on a valid
// type-in jump. The widget carries day tick marks with sparse date labels, a wall-
// clock "now" marker, a live clock readout, and a type-in datetime jump box (Enter
// jumps; unparseable input is refused visibly). Plain positioned element (not an
// L.control) so it can centre and span the map width; Leaflet mouse propagation is
// disabled so dragging never pans the map.
export function buildTimeSlider(map, { t0Ms, spanHours, value, nowMs, onChange, tracks, stepH = 1 }) {
  const HOUR_MS = 3600000;
  const DAY_MS = 86400000;
  const lastMs = t0Ms + spanHours * HOUR_MS;
  const el = L.DomUtil.create("div", "time-slider-control");

  // Head row: the "Show tracks" master (left) and the live clock readout (right). The
  // tracks master lives here — not in the Instruments tab — because the observed
  // tracks clip to *this* clock (plan 036): drifter, glider, and ship track lines
  // show/hide together (heads and the deploy tool's own drift lines excepted — the
  // latter are scrubber-cropped but not gated by this master).
  const head = L.DomUtil.create("div", "ts-head", el);
  if (tracks) {
    const tracksLabel = L.DomUtil.create("label", "ts-tracks", head);
    const tracksCb = L.DomUtil.create("input", "", tracksLabel);
    tracksCb.type = "checkbox";
    tracksCb.checked = !!tracks.initial;
    L.DomUtil.create("span", "", tracksLabel).textContent = "Show tracks";
    tracksCb.addEventListener("change", () => tracks.onToggle?.(tracksCb.checked));
  }

  // Right side of the head row: just the live clock readout. (The "now" jump chip
  // was removed — #36.) The blue now-dot on the scrub line still marks where
  // wall-clock "now" falls; it is non-interactive so it never blocks grabbing a
  // thumb parked near it.
  const headRight = L.DomUtil.create("div", "ts-head-right", head);
  const timeEl = L.DomUtil.create("div", "ts-time", headRight);

  const pctOf = (ms) => (spanHours ? ((ms - t0Ms) / (spanHours * HOUR_MS)) * 100 : 0);

  // The range rides in a positioned wrapper so the wall-clock "now" marker — a small
  // blue dot sitting on the scrub line itself — can be percent-placed over it. On the
  // line rather than in the tick lane below, where its old caption collided with the
  // date labels; the dot ignores the pointer (CSS) so it never blocks grabbing a
  // thumb parked at/near now.
  const rangeWrap = L.DomUtil.create("div", "ts-rangewrap", el);
  const input = L.DomUtil.create("input", "ts-range", rangeWrap);
  input.type = "range";
  input.min = "0";
  input.max = String(spanHours);
  input.step = String(stepH); // slider unit is hours; a fractional step gives sub-hour resolution
  input.value = String(value);
  input.setAttribute("aria-label", "Field clock time (UTC)");
  if (nowMs >= t0Ms && nowMs <= lastMs) {
    // The wall-clock "now" dot doubles as the jump-to-now control (#36 follow-up):
    // clicking it snaps the scrubber to the now hour via the same input→onChange path a
    // drag uses. nowOffset is now, clamped into the span and snapped to the slider step.
    const nowOffset =
      Math.min(spanHours, Math.max(0, Math.round((nowMs - t0Ms) / HOUR_MS / stepH) * stepH));
    const nowDot = L.DomUtil.create("div", "ts-nowdot", rangeWrap);
    nowDot.style.left = pctOf(nowMs) + "%";
    nowDot.title = "Jump the clock to now";
    L.DomEvent.on(nowDot, "click", (e) => {
      L.DomEvent.stop(e);
      input.value = String(nowOffset);
      input.dispatchEvent(new Event("input")); // reuse the drag path (setTime + onChange)
    });
  }

  // Absolutely-positioned tick lane under the range: a mark at each 00Z day boundary
  // with sparse "Jul 14"-style UTC date labels so they don't collide. Percent
  // positions map the clock's [t0, last] span onto the track (approximate at the
  // thumb insets, like the prior flex ticks).
  const ticks = L.DomUtil.create("div", "ts-ticks", el);
  const days = [];
  for (let d = Math.ceil(t0Ms / DAY_MS) * DAY_MS; d <= lastMs; d += DAY_MS) days.push(d);
  const labelEvery = Math.max(1, Math.ceil(days.length / 8)); // <= ~8 labels
  days.forEach((d, i) => {
    const t = L.DomUtil.create("div", "ts-tick", ticks);
    const pct = pctOf(d);
    t.style.left = pct + "%";
    if (i % labelEvery === 0) {
      const dt = new Date(d);
      const label = L.DomUtil.create("span", "ts-tick-label", t);
      label.textContent = `${MONTHS[dt.getUTCMonth()]} ${dt.getUTCDate()}`;
      // Keep the end labels inside the box: left-anchor the first, right-anchor the
      // last, centre the rest (the default). A centred label at pct 0 / 100 would
      // otherwise spill half its width past the rounded control edge (bug fix).
      if (pct <= 6) label.style.transform = "translateX(0)";
      else if (pct >= 94) label.style.transform = "translateX(-100%)";
    }
  });

  const setTime = (v) => {
    timeEl.textContent = formatClock(t0Ms + v * HOUR_MS);
  };
  setTime(value);

  L.DomEvent.on(input, "input", () => {
    const v = Number(input.value);
    setTime(v);
    onChange(v);
  });

  L.DomEvent.disableClickPropagation(el);
  L.DomEvent.disableScrollPropagation(el);
  map.getContainer().appendChild(el);
  return { el, setTime };
}

// Cursor coordinate readout (lower-left): a plain-text chip showing the
// pointer's position in the shared formatLatLon style (lat first, N/S · E/W),
// updated on mousemove. Like the time slider it's a positioned element inside
// the map container (not an L.control), so it hugs the corner. Hidden until the
// pointer enters the map and again on leave.
export function buildCursorReadout(map) {
  const el = L.DomUtil.create("div", "cursor-readout hidden");
  map.on("mousemove", (e) => {
    el.textContent = formatLatLon(e.latlng.lat, e.latlng.lng);
    el.classList.remove("hidden");
  });
  map.on("mouseout", () => el.classList.add("hidden"));
  map.getContainer().appendChild(el);
  return el;
}
