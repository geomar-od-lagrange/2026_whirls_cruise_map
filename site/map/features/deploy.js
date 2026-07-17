/* The interactive deploy tool — the top-right "Deploy" control's whole subsystem:
 * placement geometry, the multi-click planner factory, the deployment manager, the
 * drop-set + track click highlights, and the waypoint CSV export. Extracted from the
 * single-scope app.js (FS-1). The tool is a page singleton, so its state lives at
 * module scope and buildDeployTool(deps) injects the app-provided dependencies once.
 *
 * The observed-drifter forecasts (drawDrifterForecastLines / kickDrifterForecasts) and
 * the observed-track groups (buildTrackGroups) are NOT here — they merely sat in the
 * same banner span; they stay in app.js with the rest of the observed layers.
 */

import { PALETTE, DEPLOY_DROP_RADIUS } from "../config.js";
import { FORECAST_API, getDeployLimits, apiErrorText } from "../api.js";

// App-provided dependencies, injected once by buildDeployTool(deps):
//   deployLayer          — the umbrella Leaflet featureGroup every placement's group rides
//   getStartTime()       — live getter for the run start (the displayed field's valid time)
//   getSpanHours()       — live getter for the loaded field's span in hours (the "∞" stop)
//   getClockMs()         — live getter for the app clock as epoch ms (the drift crop clock)
//   formatClock(ms)      — compact UTC clock formatter (manager rows + the release readout)
//   registerAtTimeMarker — register a track's clock-following at-time marker (app.js)
//   removeAtTimeSet      — forget at-time markers by setKey prefix/exact (app.js)
let deployLayer, getStartTime, getSpanHours, getClockMs, formatClock,
    registerAtTimeMarker, removeAtTimeSet;


// Virtual deployments cycle through the palette's three virtual-deployment colours
// (deploy_1..3) by placement order, wrapping after three, so successive runs placed in
// a session read apart from each other (and from the observed drifter/glider families —
// the virtual ramp sits off them). `deployColor(id)` maps the 1-based placement id to
// its colour; the preview uses the NEXT id's colour so it foretells the committed run.
const deployColor = (id) => PALETTE[`deploy_${((Number(id) - 1) % 3) + 1}`];

// Tangent-plane km per degree of latitude (R·π/180, R = 6371 km) and knots→km/h.
// The client owns the deployment geometry now: it resamples the clicked path into
// equally-spaced drops (cos-lat tangent plane) and staggers each drop's water-entry
// time by the ship-speed knob, so the API just advects the seeds it is handed.
const KM_PER_DEG = 111.19492664455873;
const KN_TO_KMH = 1.852;

// --- interactive deployment planner (PoC) -----------------------------------
// One top-right "Deploy" control arms a multi-click placement mode: click a path
// (2+ vertices; double-click to finish), and the client lays drifter drops at
// equal spacing along it — the drop spacing (km) and ship speed (kn) are knobs. A
// live preview (redrawn on mousemove, no fetch) foretells the polyline, the
// equally-spaced drops it implies, and the transit time. On finish placeDeployment
// resamples the path, staggers each drop's water-entry time by the ship speed, and
// POSTs the (lon, lat, start) seeds to /api/forecast; the returned per-drop
// advection lines + at-time markers are drawn over the drops. These are ad-hoc,
// user-placed, ephemeral lines in their own green + `deploy` pane — never a build
// artifact. A single tool replaces the old click-forecast / jet-fence / Z tools:
// a free polyline is the general case those special-cased (a Z is four clicks).

// Resample a clicked polyline (LatLng vertices) into equally-spaced drops, both
// ends included, spacing ~= spacingKm (count = round(total / spacing), min 1). Uses
// the cos-lat tangent plane anchored at the path's mean latitude — the same
// convention the RK4 field uses — so the arc lengths are geographically honest.
// Returns { drops: [{ latlng, cumKm }], totalKm }; a single vertex (or a zero-length
// path) yields one drop at that point.
function resamplePolyline(vertices, spacingKm) {
  if (vertices.length < 2) return { drops: [{ latlng: vertices[0], cumKm: 0 }], totalKm: 0 };
  const meanLat = vertices.reduce((acc, v) => acc + v.lat, 0) / vertices.length;
  const cos = Math.cos((meanLat * Math.PI) / 180);
  const segKm = (a, b) =>
    Math.hypot((b.lng - a.lng) * cos * KM_PER_DEG, (b.lat - a.lat) * KM_PER_DEG);
  const cum = [0];
  for (let i = 0; i < vertices.length - 1; i++)
    cum.push(cum[i] + segKm(vertices[i], vertices[i + 1]));
  const total = cum[cum.length - 1];
  if (total === 0) return { drops: [{ latlng: vertices[0], cumKm: 0 }], totalKm: 0 };
  // Clamp the spacing to a positive floor and cap the count, so a stray 0 / tiny
  // spacing can't spin an unbounded loop (the knob is guarded too, but defend here).
  const n = Math.max(1, Math.min(2000, Math.round(total / Math.max(spacingKm, 0.01))));
  const drops = [];
  for (let i = 0; i <= n; i++) {
    const arc = (total * i) / n; // target arc length from the start (endpoints included)
    let j = 0;
    while (j < vertices.length - 2 && arc > cum[j + 1]) j++;
    const seg = cum[j + 1] - cum[j];
    const t = seg === 0 ? 0 : (arc - cum[j]) / seg;
    const a = vertices[j], b = vertices[j + 1];
    drops.push({
      latlng: L.latLng(a.lat + t * (b.lat - a.lat), a.lng + t * (b.lng - a.lng)),
      cumKm: arc,
    });
  }
  return { drops, totalKm: total };
}

// A drop's absolute water-entry time (ISO-8601): the run start plus the ship-track
// time to reach it at `shipKn` knots (cum_km / (kn·1.852) hours). `runStartISO` is
// the displayed field's valid time, so drop #1 (cum_km 0) enters at the field's
// instant; omitted, it falls back to now (which the loaded window covers).
function seedTime(runStartISO, cumKm, shipKn) {
  const base = runStartISO ? new Date(runStartISO).getTime() : Date.now();
  const kmh = shipKn * KN_TO_KMH;
  const offsetMs = kmh > 0 ? (cumKm / kmh) * 3600 * 1000 : 0; // guard a 0 ship-speed knob
  return new Date(base + offsetMs).toISOString().replace(/\.\d+Z$/, "Z");
}

// Redraw the ephemeral placement preview into previewLayer — the polyline the
// current path + cursor imply, the equally-spaced drops on it, hollow rings at the
// clicked vertices, and a floating label (drop count · length · transit time). No
// fetch: pure client geometry (resamplePolyline), the same math placeDeployment
// commits, so the preview foretells the committed drops.
function drawDeployPreview(previewLayer, vertices, cursor, opts) {
  previewLayer.clearLayers();
  const color = deployColor(deployCounter + 1); // the colour the next placement will take
  const path = cursor ? [...vertices, cursor] : vertices.slice();
  if (path.length >= 2) {
    L.polyline(path, {
      pane: "deployTracks", color, weight: 2, opacity: 1,
      dashArray: "5 4", interactive: false,
    }).addTo(previewLayer);
  }
  const { drops, totalKm } = resamplePolyline(path, opts.spacing);
  for (const d of drops) {
    L.circleMarker(d.latlng, {
      pane: "deployDrops", radius: 3, color: "#fff", weight: 1,
      fillColor: color, fillOpacity: 1, interactive: false,
    }).addTo(previewLayer);
  }
  for (const v of vertices) {
    L.circleMarker(v, {
      pane: "deployDrops", radius: 4, color, weight: 2,
      fill: false, interactive: false,
    }).addTo(previewLayer);
  }
  // The transit estimate only means something along-track; an instantaneous release
  // has no water-entry stagger to foretell.
  const tail =
    opts.timing === "instant"
      ? "instant"
      : `~${(totalKm / (opts.shipKn * KN_TO_KMH)).toFixed(1)} h`;
  L.tooltip({ permanent: true, direction: "top", className: "pt-preview-label", offset: [0, -8] })
    .setLatLng(cursor ?? path[path.length - 1])
    .setContent(`${drops.length} drops · ${totalKm.toFixed(1)} km · ${tail}`)
    .addTo(previewLayer);
}

// The committed drop discs: a white-ringed green disc per drop, tooltip = deploy
// order + water-entry ETA. `drops` is [{ latlng, start }]. Rendered in the
// deployDrops pane, so they sit above the tracks but below the at-time markers
// regardless of draw order. Each disc is clickable: it highlights the whole deployment's
// drop set (every disc, enlarged + dark-ringed — see selectDropSet). Its click is
// swallowed (bubblingMouseEvents:false) so it highlights rather than reaching the map
// (which would clear selections or add a path vertex). Drawn into the deployment's own
// group (`layer`) so it hides/deletes with the deployment.
function drawDrops(drops, layer, deploymentId) {
  const set = (deployDropSets[deploymentId] ??= []);
  const selected = String(deploymentId) === selectedDropSet;
  const color = deployColor(deploymentId);
  drops.forEach((d, i) => {
    const disc = L.circleMarker(d.latlng, {
      // No outline (#33): a filled disc of this deployment's colour, matching the real
      // instruments' deployment dots. The selection restyle re-adds a ring.
      pane: "deployDrops", radius: DEPLOY_DROP_RADIUS, weight: 0,
      fillColor: color, fillOpacity: 1,
      bubblingMouseEvents: false,
    });
    disc.bindTooltip(`#${i + 1} · ${d.start}`, { direction: "top" });
    disc.on("click", () => selectDropSet(deploymentId));
    set.push(disc);
    restyleDropDisc(disc, selected);
    disc.addTo(layer);
  });
}

// The Deploy tool: a multi-click polyline placement mode. Click adds a vertex;
// double-click finishes (Leaflet fires two clicks before a dblclick, so the
// near-duplicate tail vertex is dropped and doubleClickZoom is disabled while
// armed). The knobs (drop spacing km, ship speed kn, forecast horizon h) bind
// straight onto `state`. Returns { state, handleClick, handleDblClick, handleMove,
// handleAbort, renderBody }; the dock renders renderBody into its Deploy tab and
// main() routes background events through the handlers.
export function buildDeployTool(deps) {
  ({ deployLayer, getStartTime, getSpanHours, getClockMs, formatClock,
     registerAtTimeMarker, removeAtTimeSet } = deps);
  // The "∞" duration stop advects to the end of the loaded field: the server truncates
  // at the field edge, so any horizon ≥ the field span reaches it. getSpanHours() is
  // that span (from main's clock block, resolved lazily since it is computed after this
  // tool is constructed); fall back to a generous month if there is no field.
  const infHorizonH = () => (getSpanHours?.() || 0) || 24 * 30;
  const state = {
    on: false,
    vertices: [],            // LatLng[] — the clicked path, grows per click
    spacing: 10,             // km between drops along the path (#26)
    shipKn: 6.5,             // ship transit speed, knots
    horizonH: 120,           // run duration from the release time, hours (5 d default)
    runForward: true,        // advection directions to run — both may run at once (#32)
    runBackward: false,
    // Water-entry timing: "alongtrack" staggers each drop by the ship's transit to
    // it (a real vessel steaming the route); "instant" puts every drop in the water
    // at the release time (an idealised simultaneous release, e.g. to read pure
    // flow-field deformation of the line).
    timing: "instant",       // default to a simultaneous release (#26)
  };
  let statusEl = null;
  let mapRef = null;
  let releaseEl = null; // the read-only release-time readout in Settings
  const setStatus = (msg) => {
    if (statusEl) statusEl.textContent = msg;
  };
  // Update the read-only release-time readout. Called by main() on every scrubber
  // move (one clock: the release time IS displayedFieldTime, never overridden here —
  // plan 034, decision 5), and once at render from the current clock.
  const setRelease = (iso) => {
    if (releaseEl) releaseEl.textContent = iso ? formatClock(Date.parse(iso)) : "—";
  };

  // previewLayer holds the ephemeral path/drops preview (cleared + redrawn per
  // move/click); wiped on reset (disarm / finish / clear).
  const previewLayer = L.featureGroup();
  const resetPath = () => {
    state.vertices = [];
    previewLayer.clearLayers();
  };

  // A one-shot "now scrub the clock" tooltip pinned at the finishing double-click (#38):
  // a fresh deployment's drift is cropped at the clock, which sits on the release edge
  // at placement, so the line is zero-length until the user scrubs. Anchored to the map
  // point the eye is already on (the route's end), map-anchored so it survives pan/zoom
  // and works on touch. Auto-dismisses on a timer and is cleared the moment the user does
  // anything else (new path, abort, disarm, Clear) so it never lingers over fresh work.
  let finishTip = null;
  let finishTipTimer = null;
  const clearFinishHint = () => {
    if (finishTipTimer) { clearTimeout(finishTipTimer); finishTipTimer = null; }
    if (finishTip) { finishTip.remove(); finishTip = null; }
  };
  const showFinishHint = (latlng) => {
    if (!mapRef) return;
    clearFinishHint();
    finishTip = L.tooltip({
      permanent: true,
      direction: "top",
      offset: [0, -6],
      className: "deploy-finish-hint",
      interactive: false,
    })
      .setLatLng(latlng)
      .setContent(`drops placed · ${SCRUB_HINT}`)
      .addTo(mapRef);
    finishTipTimer = setTimeout(clearFinishHint, 5000);
  };

  // Two clicked vertices are "the same" (a dblclick's duplicate) when within a few
  // screen pixels — so finishing on a double-click doesn't add a spurious vertex.
  const isDuplicate = (a, b) =>
    mapRef &&
    mapRef.latLngToContainerPoint(a).distanceTo(mapRef.latLngToContainerPoint(b)) < 8;

  const handleClick = (latlng) => {
    clearFinishHint(); // a new path supersedes the last finish's hint
    state.vertices.push(latlng);
    drawDeployPreview(previewLayer, state.vertices, null, state);
    setStatus(`${state.vertices.length} point(s) — double-click to finish`);
  };

  const handleMove = (latlng) => {
    if (!state.on || !state.vertices.length) return;
    drawDeployPreview(previewLayer, state.vertices, latlng, state);
  };

  // Double-click finishes: drop the dblclick's duplicate tail vertex, then commit.
  const handleDblClick = (latlng, startTime) => {
    const v = state.vertices;
    if (v.length >= 2 && isDuplicate(v[v.length - 1], v[v.length - 2])) v.pop();
    const path = v.slice();
    resetPath();
    if (!path.length) return;
    placeDeployment(path, deployLayer, setStatus, startTime, state);
    showFinishHint(latlng); // pin the "now scrub" nudge at the route's end (#38)
  };

  // Abort an in-progress path (right-click / Escape): discard the clicked vertices and
  // wipe the preview without committing. Stays armed, so the next click starts fresh.
  // Returns whether a path was actually in progress, so the caller only swallows the
  // event (browser context menu) when it consumed one.
  const handleAbort = () => {
    clearFinishHint();
    if (!state.on || !state.vertices.length) return false;
    resetPath();
    setStatus("cancelled — click a path · double-click to finish");
    return true;
  };

  // Render the deploy tool's body into the dock's Deploy tab, laid out top-to-bottom
  // (plan 037 / #23): **Settings** (release · direction · timing · a duration slider ·
  // the drop-every/at-speed line), then the **Deployments** manager list, then the
  // **Deploy** arm toggle beside a **Clear** button, then the collapsible **CSV
  // import / export** at the very bottom, then the status line. Captures the map and
  // arms the preview layer on first render.
  const renderBody = (div, map) => {
    div.classList.add("deploy-tool");
    mapRef = map;
    previewLayer.addTo(map);

    // A guarded decimal <input> bound to state[key], no label — composed inline into
    // the drop-line sentence below. Plain type=text (not type=number) with a beforeinput
    // guard admitting only digits and a single dot, so the decimal separator is a dot
    // regardless of the browser's locale — a comma (or a pasted "0,5") is refused whole
    // rather than silently blanked by a de-locale type=number field. The change handler
    // still rejects zero/negatives (spacing 0 would hang the resample).
    const numInput = (parent, key) => {
      const input = L.DomUtil.create("input", "pt-num pt-num-inline", parent);
      input.type = "text";
      input.inputMode = "decimal"; // mobile keypad; the guard is what enforces dot-only
      input.autocomplete = "off";
      input.value = state[key];
      input.addEventListener("beforeinput", (e) => {
        if (e.data == null) return; // deletions, cut, and the like always pass
        const next =
          input.value.slice(0, input.selectionStart) +
          e.data +
          input.value.slice(input.selectionEnd);
        if (!/^\d*\.?\d*$/.test(next)) e.preventDefault();
      });
      input.addEventListener("change", () => {
        const val = parseFloat(input.value);
        if (!Number.isNaN(val) && val > 0) state[key] = val;
      });
      return input;
    };

    // A sliding two-state switch binding onto state[key]: the left and right option
    // texts flank a knob that sits on the active side (so the two options name
    // themselves — no separate caption). Clicking either label, or the knob, selects
    // that side; `onChange` fires after a change. `title` (optional) explains the
    // control on hover.
    const switchRow = (parent, key, left, right, onChange, title) => {
      const row = L.DomUtil.create("div", "pt-switchrow", parent);
      const leftEl = L.DomUtil.create("span", "pt-switch-label", row);
      leftEl.textContent = left.text;
      const sw = L.DomUtil.create("button", "pt-switch", row);
      sw.type = "button";
      sw.setAttribute("role", "switch");
      if (title) sw.title = title;
      L.DomUtil.create("span", "pt-switch-knob", sw);
      const rightEl = L.DomUtil.create("span", "pt-switch-label", row);
      rightEl.textContent = right.text;
      const paint = () => {
        const isRight = state[key] === right.value;
        sw.classList.toggle("on", isRight);
        sw.setAttribute("aria-checked", String(isRight));
        leftEl.classList.toggle("active", !isRight);
        rightEl.classList.toggle("active", isRight);
      };
      const setVal = (v) => {
        if (state[key] === v) return;
        state[key] = v;
        paint();
        onChange?.();
      };
      sw.addEventListener("click", () =>
        setVal(state[key] === left.value ? right.value : left.value)
      );
      leftEl.addEventListener("click", () => setVal(left.value));
      rightEl.addEventListener("click", () => setVal(right.value));
      paint();
      return { paint };
    };

    // --- compartment 1: the run knobs (no caption — the controls speak for
    // themselves). A run is release time + direction + timing + duration (plan 034,
    // decision 4): the release time is read-only and follows the app clock (one clock,
    // no override); direction and timing are sliding two-state switches; duration is
    // the run length in hours. Drift is always computed.
    const settings = L.DomUtil.create("div", "pt-settings", div);

    // Release time: read-only, live-following the scrubber (setRelease). No input —
    // "release at t" means jump the whole map to t (the scrubber), not type it here.
    const releaseRow = L.DomUtil.create("div", "pt-row", settings);
    L.DomUtil.create("span", "pt-label", releaseRow).textContent = "Release";
    releaseEl = L.DomUtil.create("span", "pt-readonly", releaseRow);
    setRelease(getStartTime ? getStartTime() : null);

    // Direction: forward advects downstream from the release time, backward walks the
    // field in reverse (where did water here come from). Both can run at once (#32) —
    // two independent toggles, not an exclusive switch; at least one stays on.
    const dirRow = L.DomUtil.create("div", "pt-dirrow", settings);
    const dirToggle = (key, otherKey, text) => {
      const b = L.DomUtil.create("button", "pt-dir-toggle", dirRow);
      b.type = "button";
      b.textContent = text;
      const paint = () => {
        b.classList.toggle("on", !!state[key]);
        b.setAttribute("aria-pressed", String(!!state[key]));
      };
      b.addEventListener("click", () => {
        // Never leave both off: refuse to turn off the last remaining direction.
        if (state[key] && !state[otherKey]) return;
        state[key] = !state[key];
        paint();
      });
      paint();
    };
    dirToggle("runForward", "runBackward", "Forward");
    dirToggle("runBackward", "runForward", "Backward");

    // Timing: instantaneous (every drop enters at the release time — an idealised
    // simultaneous release) vs along-track (each drop enters as the ship reaches it,
    // staggered by ship speed). Instantaneous greys the ship-speed knob, which then
    // shapes nothing; `paintTiming` is wired after the knob exists.
    let paintTiming = () => {};
    switchRow(
      settings, "timing",
      { value: "instant", text: "Instantaneous" },
      { value: "alongtrack", text: "Along track" },
      () => paintTiming(),
      "Instantaneous: all drops enter at the release time. " +
        "Along track: drops enter as the ship reaches them (staggered by ship speed)."
    );

    // Duration: a 4-stop segmented slider (1d / 2d / 5d / ∞) writing state.horizonH in
    // hours (24 / 48 / 120 / the field span). No caption — the stops name the run length
    // themselves. "∞" advects to the end of the loaded field: the server truncates at the
    // field edge, so infHorizonH() (≥ the span) reaches it. Defaults to 5d (state.horizonH
    // starts at 120).
    const durStops = [
      { label: "1d", h: () => 24 },
      { label: "2d", h: () => 48 },
      { label: "5d", h: () => 120 },
      { label: "∞", h: infHorizonH },
    ];
    const durWrap = L.DomUtil.create("div", "pt-duration", settings);
    const durBtns = durStops.map((stop) => {
      const b = L.DomUtil.create("button", "pt-duration-stop", durWrap);
      b.type = "button";
      b.textContent = stop.label;
      b.addEventListener("click", () => {
        state.horizonH = stop.h();
        paintDuration();
      });
      return b;
    });
    const paintDuration = () => {
      // Active = the stop matching the current horizon; an unmatched value (a prior ∞
      // pick, whose hour count is the field span) lights the ∞ stop.
      let active = durStops.findIndex((s) => s.h() === state.horizonH);
      if (active < 0) active = durStops.length - 1;
      durBtns.forEach((b, i) => b.classList.toggle("on", i === active));
    };
    paintDuration();

    // Drop spacing + ship speed on one short line: "Every [ ] km at [ ] kn" — terse so it
    // never wraps at the dock width. Under Instantaneous timing the ship speed shapes
    // nothing, so its input is greyed and swapped for an ∞ glyph (the km spacing stays
    // editable — instantaneous drops still have a spacing along the path).
    const dropLine = L.DomUtil.create("div", "pt-row pt-dropline", settings);
    L.DomUtil.create("span", "", dropLine).textContent = "Every";
    numInput(dropLine, "spacing");
    L.DomUtil.create("span", "", dropLine).textContent = "km at";
    const speedWrap = L.DomUtil.create("span", "pt-speed-wrap", dropLine);
    const speedInput = numInput(speedWrap, "shipKn");
    const speedInf = L.DomUtil.create("span", "pt-speed-inf", speedWrap);
    speedInf.textContent = "∞";
    L.DomUtil.create("span", "", dropLine).textContent = "kn";
    paintTiming = () => {
      const instant = state.timing === "instant";
      speedWrap.classList.toggle("pt-disabled", instant);
      speedInput.disabled = instant;
      speedInput.style.display = instant ? "none" : "";
      speedInf.style.display = instant ? "" : "none";
    };
    paintTiming();

    // --- the deployment manager (moved above the arm toggle — #23) ---
    // One row per placed deployment (id · release · direction · duration · N drops)
    // with a per-row visibility toggle, CSV export, and delete (plan 034, decision D2).
    // renderManager is registered module-wide (deployManagerRefresh) so a placement or
    // a delete repaints the list; it reads the live deployments registry each time.
    L.DomUtil.create("hr", "pt-hr", div);
    L.DomUtil.create("span", "pt-section-cap", div).textContent = "Deployments";
    const managerList = L.DomUtil.create("div", "pt-manager", div);
    const renderManager = () => {
      managerList.replaceChildren();
      const ids = Object.keys(deployments).map(Number).sort((a, b) => a - b);
      if (!ids.length) {
        L.DomUtil.create("p", "ft-hint", managerList).textContent = "No deployments placed.";
        return;
      }
      for (const id of ids) {
        const d = deployments[id];
        const row = L.DomUtil.create("div", "pt-manage-row", managerList);
        const toggle = L.DomUtil.create("label", "pt-manage-vis", row);
        const vis = L.DomUtil.create("input", "pt-check", toggle);
        vis.type = "checkbox";
        vis.checked = d.visible;
        vis.title = "Show / hide this deployment";
        vis.addEventListener("change", () => setDeploymentVisible(id, vis.checked));
        // Colour indicator: matches this deployment's drops / drift lines / at-time
        // markers on the map (the cycling deploy_1..3 colour).
        L.DomUtil.create("span", "batch-swatch", toggle).style.background =
          d.color ?? deployColor(id);
        const arrow =
          d.directions?.length === 2 ? "⇄" : d.directions?.[0] === "backward" ? "←" : "→";
        const rel = d.release ? formatClock(Date.parse(d.release)) : "—";
        const timing = d.timing === "instant" ? " · instant" : "";
        L.DomUtil.create("span", "pt-manage-label", toggle).textContent =
          `#${id} · ${rel} · ${arrow} ${d.durationH}h · ${d.nDrops} drop${d.nDrops === 1 ? "" : "s"}${timing}`;
        const csv = L.DomUtil.create("button", "pt-manage-btn", row);
        csv.type = "button";
        csv.textContent = "CSV";
        csv.title = "Export this deployment's waypoints";
        csv.addEventListener("click", () => {
          const n = downloadOneDeployment(id);
          setStatus(n ? `downloaded #${id} (${n} waypoint${n === 1 ? "" : "s"})` : "no drops");
        });
        const del = L.DomUtil.create("button", "pt-manage-btn pt-manage-del", row);
        del.type = "button";
        del.textContent = "✕";
        del.title = "Delete this deployment";
        del.addEventListener("click", () => {
          deleteDeployment(id);
          renderManager();
          setStatus(`deleted #${id}`);
        });
      }
    };
    deployManagerRefresh = renderManager;
    renderManager();

    // --- Deploy arm toggle + Clear, side by side (#23) ---
    // The toggle arms click-to-place: it sets the map crosshair cursor and suppresses
    // double-click zoom (so a finishing dbl-click doesn't also zoom). Clear wipes every
    // placed deployment (was the footer's "Clear all"). The hint below explains the
    // click gesture.
    const armRow = L.DomUtil.create("div", "pt-arm-row", div);
    const toggle = L.DomUtil.create("button", "ft-btn ft-toggle", armRow);
    toggle.type = "button";
    const paint = () => {
      toggle.textContent = state.on ? "Deploy: ON" : "Deploy: OFF";
      toggle.classList.toggle("on", state.on);
      map.getContainer().classList.toggle("deploy-cursor", state.on);
      if (state.on) map.doubleClickZoom.disable();
      else map.doubleClickZoom.enable();
    };
    toggle.addEventListener("click", () => {
      state.on = !state.on;
      if (!state.on) {
        resetPath();
        clearFinishHint();
        setStatus("");
      }
      paint();
    });
    paint();
    const clear = L.DomUtil.create("button", "ft-btn", armRow);
    clear.type = "button";
    clear.textContent = "Clear";
    clear.addEventListener("click", () => {
      clearAllDeployments(deployLayer);
      resetPath();
      clearFinishHint();
      renderManager();
      setStatus("");
    });
    L.DomUtil.create("p", "ft-hint", div).textContent =
      "click a path · double-click to finish · right-click / Esc to cancel";

    // --- CSV import / export (collapsible, at the very bottom — #23) ---
    // Import a vessel route from a waypoint list, and export placed deployments. Kept
    // behind a `⌄` menu (collapsed by default) so the common path — settings, the
    // manager, the arm toggle — stays uncluttered.
    //
    // Import: the parsed rows are the ship *route* (like a clicked path), so the drops
    // — hence the number of drifters — follow from the route length and the drop
    // spacing / ship speed knobs, not from the row count. Start time is pulled live
    // from the time scrubber (getStartTime). The textarea is the source of truth (the
    // input "mask"); "Upload file" only reads a .csv into it, so one parseWaypoints
    // serves paste and upload alike (see docs/deployment.md).
    const csvSec = L.DomUtil.create("details", "pt-csv", div);
    L.DomUtil.create("summary", "pt-csv-summary", csvSec).textContent =
      "CSV import / export";

    const fileInput = L.DomUtil.create("input", "", csvSec);
    fileInput.type = "file";
    fileInput.accept = ".csv,.txt,text/csv,text/plain";
    fileInput.style.display = "none";

    const upload = L.DomUtil.create("button", "ft-btn", csvSec);
    upload.type = "button";
    upload.textContent = "Upload file";
    upload.addEventListener("click", () => fileInput.click());

    const importBox = L.DomUtil.create("textarea", "pt-import", csvSec);
    importBox.rows = 3;
    importBox.placeholder = "vessel waypoints: lon,lat per line (decimal °, negative = S/W) · header optional";
    // Keep keystrokes local: Leaflet would otherwise treat typing over the map as map
    // interaction (e.g. a space/'-' shortcut), and drag would pan under the textarea.
    L.DomEvent.disableClickPropagation(importBox);
    L.DomEvent.disableScrollPropagation(importBox);

    fileInput.addEventListener("change", () => {
      const file = fileInput.files && fileInput.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        importBox.value = String(reader.result || "");
        setStatus(`loaded ${file.name} — review, then place`);
      };
      reader.readAsText(file);
      fileInput.value = ""; // let the same file re-trigger change next time
    });

    const place = L.DomUtil.create("button", "ft-btn", csvSec);
    place.type = "button";
    place.textContent = "Place using these waypoints";
    place.addEventListener("click", () => {
      const { latlngs, skipped, error } = parseWaypoints(importBox.value);
      if (error) {
        setStatus(error);
        return;
      }
      const startTime = getStartTime ? getStartTime() : null;
      const note = skipped ? ` (${skipped} row(s) skipped)` : "";
      setStatus(`routing ${latlngs.length} waypoint(s)${note}…`);
      // The rows are the vessel route; placeDeployment resamples it at the spacing knob
      // into drops (same as a clicked path).
      placeDeployment(latlngs, deployLayer, setStatus, startTime, state);
    });
    L.DomUtil.create("p", "ft-hint", csvSec).textContent =
      "rows are the vessel route · drops follow from spacing + speed";

    // Export: every placed deployment's drops as one flat waypoint CSV. Lives in the
    // same menu as the import; a per-deployment CSV export stays inline in each manager
    // row above.
    const download = L.DomUtil.create("button", "ft-btn", csvSec);
    download.type = "button";
    download.textContent = "Download all CSV";
    download.addEventListener("click", () => {
      const n = downloadDeployWaypoints();
      setStatus(n ? `downloaded ${n} waypoint(s)` : "no drops placed yet");
    });

    statusEl = L.DomUtil.create("p", "ft-status", div);
  };
  return { state, handleClick, handleDblClick, handleMove, handleAbort, renderBody,
           setRelease, clipAllDeployTracks, clearSelections };
}

// Parse a block of waypoint text into LatLngs (decimal degrees, negative = S/W).
// Tolerant by design so a pasted cruise-plan block or a re-imported Download CSV both
// work: blank lines and `#` comments are dropped; the delimiter is comma / semicolon /
// tab / whitespace; a header row (any non-numeric token in the first data line) maps
// columns by name (`lat*` and `lon*`|`lng`), so the export's `…,latitude,longitude,…`
// round-trips; headerless rows are read as `lon,lat` (GeoJSON x,y, matching the seed
// object). Rows that aren't two finite in-range numbers are skipped and counted.
// Returns { latlngs, skipped, error } — `error` set (and latlngs empty) only when the
// input has no usable rows at all.
function parseWaypoints(text) {
  const split = (line) => line.trim().split(/[\s,;]+/).filter((t) => t.length);
  const lines = text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith("#"));
  if (!lines.length) return { latlngs: [], skipped: 0, error: "no waypoints found" };

  // A header is present when the first non-comment line has a non-numeric token.
  let lonCol = 0, latCol = 1, start = 0;
  const first = split(lines[0]);
  if (first.some((t) => !Number.isFinite(Number(t)))) {
    const lc = first.findIndex((t) => /^lon|^lng/i.test(t));
    const ltc = first.findIndex((t) => /^lat/i.test(t));
    if (lc >= 0 && ltc >= 0) { lonCol = lc; latCol = ltc; }
    start = 1; // skip the header row
  }

  const latlngs = [];
  let skipped = 0;
  for (let i = start; i < lines.length; i++) {
    const cols = split(lines[i]);
    const lon = Number(cols[lonCol]);
    const lat = Number(cols[latCol]);
    if (
      cols.length < 2 || !Number.isFinite(lon) || !Number.isFinite(lat) ||
      lat < -90 || lat > 90 || lon < -360 || lon > 360
    ) {
      skipped++;
      continue;
    }
    latlngs.push(L.latLng(lat, lon));
  }
  if (!latlngs.length) return { latlngs: [], skipped, error: "no valid lon,lat rows" };
  return { latlngs, skipped, error: null };
}

// Resample a route into equally-spaced drops, then commit. `vertices` is the ship
// route — the clicked polyline, or the vessel waypoints parsed from a CSV/paste; both
// are the *route*, and resamplePolyline lays the drops on it at the spacing knob, so
// the number of drifters follows from the route length and spacing (not the number of
// waypoints). `startTime` (the displayed field's valid time) is the run start, so
// drop #1 enters at the field's instant.
async function placeDeployment(vertices, deployLayer, setStatus, startTime, opts) {
  const { drops, totalKm } = resamplePolyline(vertices, opts.spacing);
  return commitDeployment(drops, totalKm, deployLayer, setStatus, startTime, opts);
}

// Pre-flight a placement against the advertised /limits before any POST (plan 034,
// decision D3). Returns a human-readable rejection string, or null when the placement
// is servable — or when limits are unknown (the probe failed), in which case the
// server's own bounded request model is left to enforce them via the error path.
//   nSeeds     — number of drops (each a seed)
//   durationH  — run length in hours (opts.horizonH)
//   direction  — "forward" | "backward"
//   releaseIso — the release time (app clock at placement); may be null
function validatePlacement(limits, nSeeds, durationH, direction, releaseIso) {
  if (!limits) return null;
  if (limits.max_seeds && nSeeds > limits.max_seeds)
    return `too many drops for one request (${nSeeds} > ${limits.max_seeds} max) — increase spacing or shorten the path`;
  const budget = nSeeds * durationH;
  if (limits.max_seed_hours && budget > limits.max_seed_hours)
    return `drops × duration too large (${nSeeds} × ${durationH} h = ${budget.toFixed(0)} > ${limits.max_seed_hours} seed-hours) — fewer drops or a shorter duration`;
  const win = limits.window;
  const rel = Date.parse(releaseIso);
  if (win && win.length === 2 && Number.isFinite(rel)) {
    const lo = Date.parse(win[0]), hi = Date.parse(win[1]);
    const end = rel + (direction === "backward" ? -1 : 1) * durationH * 3600e3;
    if (Math.max(rel, end) < lo || Math.min(rel, end) > hi)
      return `release + ${durationH} h ${direction} lies outside the field window (${win[0]}…${win[1]})`;
  }
  return null;
}

// The scrub-me nudge for a fresh deployment (#38): its drift is cropped at the app
// clock, which sits on the release edge at placement, so the line is zero-length until
// the clock moves. Direction-agnostic on purpose — either way reveals the drift, and a
// run can go both ways. Shared by the dock status clause and the map finish tooltip so
// they read identically.
const SCRUB_HINT = "drag the clock to draw the drift";

// Stagger each drop's water-entry time by the ship speed, draw the drops into this
// deployment's own map group, POST the seeds to /api/forecast, and draw the returned
// per-drop drift lines + at-time markers. Shared by the clicked path and the CSV/paste
// import; `drops` are the committed drops. The run's direction + duration come from
// `opts`; the release time is the app clock (`startTime`, drop #1's water entry).
// Registers the deployment in the manager (its own group + metadata) and repaints the
// list on every exit path.
async function commitDeployment(drops, totalKm, deployLayer, setStatus, startTime, opts) {
  const deploymentId = ++deployCounter; // namespaces this placement's drops, tracks, markers
  // Which advection directions to run — forward and/or backward, both allowed (#32).
  const directions = [];
  if (opts.runForward) directions.push("forward");
  if (opts.runBackward) directions.push("backward");
  if (!directions.length) directions.push("forward"); // never run nothing
  // This deployment owns a child group of the umbrella deployLayer, holding every
  // element it draws (drops, drift lines, at-time markers), so the manager can hide or
  // delete it wholesale (setDeploymentVisible / deleteDeployment).
  const group = L.featureGroup().addTo(deployLayer);
  // Along-track timing staggers each drop's water entry by the ship's transit to it;
  // instantaneous puts every drop in the water at the release time (cum_km 0).
  const instant = opts.timing === "instant";
  const seeds = drops.map((d) => ({
    lon: Number(d.latlng.lng.toFixed(5)),
    lat: Number(d.latlng.lat.toFixed(5)),
    start: seedTime(startTime, instant ? 0 : d.cumKm, opts.shipKn),
  }));
  const dropRecords = drops.map((d, i) => ({ latlng: d.latlng, start: seeds[i].start }));
  // The drops ARE this deployment's waypoints: capture them (5-decimal lon/lat +
  // absolute water-entry time, matching the seeds) so the manager's per-row and the
  // "Download all CSV" exports need no geometry re-derivation.
  deployWaypoints[deploymentId] = drops.map((d, i) => ({
    deployment: deploymentId,
    drop: i + 1,
    lat: seeds[i].lat,
    lon: seeds[i].lon,
    start: seeds[i].start,
    cumKm: d.cumKm,
  }));
  // Register for the manager. Release is the app clock at placement (drop #1's water
  // entry); repaint after each terminal state so the row appears immediately.
  deployments[deploymentId] = {
    group,
    parent: deployLayer,
    release: startTime,
    directions,
    durationH: opts.horizonH,
    nDrops: drops.length,
    timing: instant ? "instant" : "alongtrack",
    visible: true,
    color: deployColor(deploymentId), // the manager row's swatch (matches its map elements)
  };
  const done = (status) => {
    setStatus(status);
    deployManagerRefresh?.();
  };

  const transitH = totalKm / (opts.shipKn * KN_TO_KMH);
  const geom = instant
    ? `${drops.length} drops · ${totalKm.toFixed(1)} km · instant release`
    : `${drops.length} drops · ${totalKm.toFixed(1)} km · ~${transitH.toFixed(1)} h transit`;

  // The drops are shared by both directions — draw them once, up front, so they show
  // even if every run is rejected or fails (they stay exportable — decision D3).
  drawDrops(dropRecords, group, deploymentId);
  done(`${geom} · computing drift…`);

  // Run each selected direction independently (#32): pre-validate against /limits (seed
  // cap, seeds×duration budget, release+duration vs the window — decision D3), then POST
  // + draw. Both direction line sets ride the same group and share the drops;
  // drawDeployForecastLines keys each track by direction so forward and backward never
  // collide. One bad direction (invalid or failed) doesn't sink the other — collect a
  // note per run and report them together. The batch advects server-side in ~1–2 s even
  // at the seed cap (vectorized RK4), well inside the gateway's 60 s timeout.
  const limits = await getDeployLimits();
  const notes = [];
  const drew = []; // directions that actually produced a track — drive the scrub hint (#38)
  for (const direction of directions) {
    const invalid = validatePlacement(limits, drops.length, opts.horizonH, direction, startTime);
    if (invalid) { notes.push(`${direction}: ${invalid}`); continue; }
    try {
      const resp = await fetch(FORECAST_API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ seeds, horizon_h: opts.horizonH, direction }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) { notes.push(`${direction}: ${apiErrorText(data, resp.status)}`); continue; }
      const p = data.properties ?? {};
      const runStart = p.run_start ?? startTime;
      // One green drift line per drop, clipped to the app clock (plan 036), plus an
      // at-time marker per drop keyed to this deployment (decisions 6/7).
      drawDeployForecastLines(data.features ?? [], group, runStart, deploymentId, direction);
      if (p.tracks === 0 && p.n_seeds > 0) {
        const w = p.window ? ` (field ${p.window[0]}…${p.window[1]})` : "";
        notes.push(`${direction}: 0 tracks — release outside the field window${w}`);
      } else {
        const skipped = p.skipped ? `, ${p.skipped} skipped` : "";
        notes.push(`${direction}: ${p.tracks}/${p.n_seeds}${skipped}`);
        if (p.tracks > 0) drew.push(direction);
      }
    } catch (err) {
      notes.push(`${direction}: request failed — is \`pixi run serve-api\` running?`);
    }
  }
  // The drift is cropped at the clock and the clock sits on the release edge at
  // placement, so a fresh line is zero-length until the user scrubs (#38). When a run
  // drew a track, prompt the scrub — the durable dock-side twin of the finish tooltip.
  // Self-clears on the next status write.
  const hint = drew.length ? ` · ${SCRUB_HINT}` : "";
  done(`${geom} · ${notes.join(" · ")}${hint}`);
}

// Each placed deployment gets its own id (deployCounter), namespacing its drop set,
// tracks, at-time markers, and waypoint rows so one deployment's highlights and
// exports never bleed into another's.
let deployCounter = 0;

// --- deployment manager ------------------------------------------------------
// The per-deployment manager (plan 034, decision D2) replaces the single global
// Clear: one row per placed deployment with a visibility toggle, CSV export, and
// delete. Each placement records its metadata here and owns a Leaflet featureGroup
// (a child of the umbrella deployLayer) holding all its map elements — ship track,
// drops, drift lines, and at-time markers — so hiding or deleting one deployment is a
// single group add/remove on the umbrella parent without disturbing the others.
//   id -> { group, parent, release, direction, durationH, nDrops, visible }
const deployments = {};
// Set by the Deploy tab's renderBody so a placement/delete from module scope repaints
// the manager list; null until the tab is first rendered (deploys can't be placed
// before then, since the tool arms in renderBody).
let deployManagerRefresh = null;

// Drop one deployment's registries + selections, without touching its Leaflet group
// (the caller removes/clears that). Shared by delete and clear-all.
function forgetDeployment(id) {
  delete deployDropSets[id];
  for (const key of Object.keys(deployTracks))
    if (key.startsWith(`${id}#`)) delete deployTracks[key];
  delete deployWaypoints[id];
  delete deployments[id];
  removeAtTimeSet(`deploy:${id}`, true); // exact — "deploy:2" must not catch "deploy:20"
  if (selectedDropSet === String(id)) selectedDropSet = null;
  if (selectedTrack && selectedTrack.startsWith(`${id}#`)) selectedTrack = null;
}

// Delete one deployment: remove its group from the umbrella parent, forget its
// registries, and drop any selection pointing at it. The caller repaints the manager.
function deleteDeployment(id) {
  const d = deployments[id];
  if (d) d.parent.removeLayer(d.group);
  forgetDeployment(id);
}

// Show/hide one deployment by adding/removing its group from the umbrella parent. The
// at-time markers ride the group, so they hide with it; the scrubber still repositions
// the (detached) markers harmlessly, so they reappear in place when shown again.
function setDeploymentVisible(id, on) {
  const d = deployments[id];
  if (!d || d.visible === on) return;
  d.visible = on;
  if (on) d.parent.addLayer(d.group);
  else d.parent.removeLayer(d.group);
}

// Wipe every placed deployment: remove all their groups from the umbrella layer and
// forget all registries + selections. The Deploy tab's "Clear all" (decision D2).
function clearAllDeployments(deployLayer) {
  deployLayer.clearLayers();
  resetDeployHighlights(); // clears the id-keyed registries + the "deploy:" markers
  for (const id of Object.keys(deployments)) delete deployments[id];
}

// --- deploy drop-set + track highlight ---------------------------------------
// Two read-by-click axes beside the at-time markers (which lift a deployment's whole
// array at the clock's instant):
//   • DROP SET — clicking any drop disc lifts EVERY drop disc of that deployment
//     (enlarged + dark-ringed), so the whole array of water-entry points reads at once.
//   • TRACK — clicking a forecast line, on bare track between the markers, lifts that
//     ONE drifter's trajectory (thickened + recoloured magenta).
// The drop set keys off the deployment id; the track off deployment id + drop index.
// The axes (at-time marker set, drop set, track) are independent — a marker / disc /
// line click is swallowed so it toggles its own axis without disturbing the others.
// Cleared by re-clicking, a background click, or Clear (resetDeployHighlights).

const deployDropSets = {}; // deploymentId -> [disc markers]
// `${deploymentId}#${index}` -> { line, hitLine, times, lats, lngs, forward }: one green
// polyline per drift, CROPPED at the scrubber (clipDeployTrack — release→clock forward,
// clock→release backward) with the moving at-time head at its clipped end. No analysed/
// forecast dash split; it restyles + rises to the front on selection (restyleTrack), and
// the crop re-raises a selected track after each redraw.
const deployTracks = {};
// The drop data behind the discs, kept for the CSV export (see deployWaypointsCsv):
// deploymentId -> [{ deployment, drop, lat, lon, start, cumKm }]. Populated in
// placeDeployment, cleared with the rest in resetDeployHighlights.
const deployWaypoints = {};
let selectedDropSet = null; // deploymentId (string) or null
let selectedTrack = null;   // track key or null

function restyleDropDisc(disc, selected) {
  // Selected: a dark ring as the selection affordance. Unselected: no outline (#33).
  disc.setStyle({ color: "#111827", weight: selected ? 2 : 0 });
  disc.setRadius(selected ? DEPLOY_DROP_RADIUS + 3 : DEPLOY_DROP_RADIUS);
  if (selected) disc.bringToFront();
}

function applyDropSetSelection() {
  for (const id of Object.keys(deployDropSets))
    for (const disc of deployDropSets[id]) restyleDropDisc(disc, id === selectedDropSet);
}

// Toggle: clicking a disc of the selected deployment clears it; another replaces it.
function selectDropSet(deploymentId) {
  const id = String(deploymentId);
  selectedDropSet = id === selectedDropSet ? null : id;
  applyDropSetSelection();
}

// Restyle one virtual track's line: magenta + thicker when its drifter is picked, its
// deployment's own cycling colour otherwise.
function restyleTrack(entry, selected) {
  const color = selected ? "#d81b8c" : entry.color; // magenta pops off the blue deploy colours
  entry.line.setStyle({ color, weight: selected ? 4 : 2, opacity: 1 });
  if (selected) entry.line.bringToFront();
}

// Crop one virtual deployment's drift line + hit-line to clock `ms` (the drops' own
// clock). A forward run shows release→clock (grows as the clock advances); a backward
// run shows clock→release (grows as the clock rewinds); the line hides entirely outside
// the run's span on its not-yet-happened side. A selected track is re-raised after the
// redraw — setLatLngs drops a prior bringToFront — so the highlight stays on top.
function clipDeployTrack(entry, ms, selected) {
  const { line, hitLine, times, lats, lngs, forward } = entry;
  if (!times || times.length < 2) return;
  const n = times.length;
  const earliest = times[0], latest = times[n - 1];
  const full = () => lats.map((la, i) => [la, lngs[i]]);
  let coords;
  if (ms == null) {
    coords = full();
  } else if (forward ? ms < earliest : ms > latest) {
    coords = []; // the run hasn't started at this clock — hide the line
  } else if (forward ? ms >= latest : ms <= earliest) {
    coords = full(); // the whole run has happened by this clock
  } else {
    let i = 0;
    while (i < n - 1 && times[i + 1] < ms) i++;
    const t0 = times[i], t1 = times[i + 1];
    const f = t1 === t0 ? 0 : (ms - t0) / (t1 - t0);
    const pos = [lats[i] + f * (lats[i + 1] - lats[i]), lngs[i] + f * (lngs[i + 1] - lngs[i])];
    if (forward) {
      coords = [];
      for (let k = 0; k <= i; k++) coords.push([lats[k], lngs[k]]);
      coords.push(pos); // release → clock
    } else {
      coords = [pos];
      for (let k = i + 1; k < n; k++) coords.push([lats[k], lngs[k]]); // clock → release
    }
  }
  line.setLatLngs(coords);
  hitLine.setLatLngs(coords);
  if (selected && coords.length) line.bringToFront();
}

function applyTrackSelection() {
  for (const key of Object.keys(deployTracks))
    restyleTrack(deployTracks[key], key === selectedTrack);
}

// Toggle: clicking the selected track clears it; another track replaces it.
function selectDeployTrack(key) {
  selectedTrack = key === selectedTrack ? null : key;
  applyTrackSelection();
}

// Clear every deploy highlight — at-time marker sets, drop sets, and tracks — and
// forget their id-keyed registries. Called by clearAllDeployments (the Deploy tab's
// "Clear all") along with wiping the layers; removeAtTimeSet drops every "deploy:"
// marker by prefix.
function resetDeployHighlights() {
  for (const id of Object.keys(deployDropSets)) delete deployDropSets[id];
  for (const key of Object.keys(deployTracks)) delete deployTracks[key];
  for (const id of Object.keys(deployWaypoints)) delete deployWaypoints[id];
  removeAtTimeSet("deploy:");
  selectedDropSet = null;
  selectedTrack = null;
}

// --- waypoint CSV export -----------------------------------------------------
// The placed drops ARE the deployment waypoints — where each drifter enters the
// water and when (the staggered ship-transit ETA) — so the Deploy tool dumps them flat
// for the ship, no server round-trip and no re-derivation (deployWaypoints already
// mirrors the drawn drops). The manager row exports one deployment; "Download all CSV"
// exports every placed one. Columns are identical either way (plan 034, decision 5).
const DEPLOY_CSV_COLUMNS = ["deployment", "drop", "latitude", "longitude", "water_entry_utc", "cum_km"];

// Total placed drops across every deployment (0 when nothing is placed yet).
function deployWaypointCount() {
  return Object.values(deployWaypoints).reduce((n, rows) => n + rows.length, 0);
}

// Flatten the given deployment ids' drops into one CSV string (ordered by deployment
// then drop), or null when none of them have drops. `ids` defaults to every placed
// deployment. Values are plain numbers / ISO strings with no separators, so no quoting
// is needed.
function deployWaypointsCsv(ids = Object.keys(deployWaypoints).map(Number)) {
  const ordered = ids.filter((id) => deployWaypoints[id]?.length).sort((a, b) => a - b);
  if (!ordered.length) return null;
  const lines = [DEPLOY_CSV_COLUMNS.join(",")];
  for (const id of ordered)
    for (const w of deployWaypoints[id])
      lines.push([w.deployment, w.drop, w.lat, w.lon, w.start, +w.cumKm.toFixed(3)].join(","));
  return lines.join("\n") + "\n";
}

// Trigger a client-side download of a CSV string as `filename` (an ephemeral object URL
// + a synthetic anchor click). A null csv is a no-op.
function downloadCsv(csv, filename) {
  if (!csv) return;
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// Download every placed deployment's waypoints as deploy_waypoints.csv. Returns the
// drop count so the caller can report it; 0 means nothing was placed (no file offered).
function downloadDeployWaypoints() {
  const csv = deployWaypointsCsv();
  downloadCsv(csv, "deploy_waypoints.csv");
  return deployWaypointCount();
}

// Download one deployment's waypoints as deploy_<id>_waypoints.csv (the manager row's
// CSV button). Returns that deployment's drop count (0 = nothing to export).
function downloadOneDeployment(id) {
  const csv = deployWaypointsCsv([id]);
  downloadCsv(csv, `deploy_${id}_waypoints.csv`);
  return deployWaypoints[id]?.length ?? 0;
}

// Draw one deployment's per-drop drift: per `role: "track"` feature, one green
// polyline CROPPED at the scrubber by the clock (clipDeployTrack — release→clock for a
// forward run, clock→release for a backward one, matching the observed tracks and
// drifter forecasts), plus a fat transparent hit-line for click-to-highlight of that one
// trajectory (selectDeployTrack; a selected track is raised to the front), and an at-time
// marker at the clock instant keyed to this deployment (decisions 6/7). Each feature carries its own
// `start`, `direction`, and `cadence_s`, so vertex i sits at start + direction·i·
// cadence_s — the per-vertex times the at-time marker reads off; a backward run's
// samples are normalised to ascending time. GeoJSON coords are [lon,lat]; Leaflet
// wants [lat,lng]. (The drops are drawn client-side by the caller; the API returns
// only track features.) Everything is drawn into `layer` — the deployment's own
// group — so it hides/deletes with it.
function drawDeployForecastLines(features, layer, runStart, deploymentId, direction) {
  const ll = ([lon, lat]) => [lat, lon];
  const color = deployColor(deploymentId); // this deployment's cycling colour
  // The whole call is one run in one direction (#32), so the growth sign is per-call.
  const dir = direction === "backward" ? -1 : 1;
  for (const f of features) {
    const props = f.properties ?? {};
    if (props.role !== "track") continue;
    const coords = f.geometry?.coordinates ?? [];
    if (coords.length < 2) continue;
    let latlngs = coords.map(ll);
    // Key by direction too: forward and backward share this deploymentId and both run
    // index 0..N-1, so without the direction they'd overwrite each other in deployTracks
    // (orphaning one set from the clock clip + selection). forgetDeployment's
    // `${id}#`-prefix match still catches both for cleanup.
    const trackKey = `${deploymentId}#${direction}#${props.index}`;
    const startMs = Date.parse(props.start ?? runStart);
    const cadenceMs = (props.cadence_s ?? 0) * 1000;
    let times = latlngs.map((_, i) => startMs + dir * i * cadenceMs);
    if (dir < 0) {
      latlngs = latlngs.slice().reverse();
      times = times.slice().reverse();
    }

    // The drift line, drawn empty and grown by the clock (clipDeployTrack): it is
    // CROPPED at the scrubber like the observed tracks and drifter forecasts — a forward
    // run shows release→clock, a backward run shows clock→release. Non-interactive, in the
    // lowest deploy pane, so a click where a disc/marker overlaps hits that instead; the
    // hit-line covers the same clipped path so the thin stroke stays easy to click.
    const line = L.polyline([], {
      pane: "deployTracks", color, weight: 2, opacity: 1,
      interactive: false,
    }).addTo(layer);
    const hitLine = L.polyline([], {
      pane: "deployTracks", color: "#000", weight: 12, opacity: 0,
      bubblingMouseEvents: false,
    })
      .on("click", () => selectDeployTrack(trackKey))
      .addTo(layer);
    const entry = {
      line, hitLine, forward: dir > 0, color,
      times, lats: latlngs.map((p) => p[0]), lngs: latlngs.map((p) => p[1]),
    };
    deployTracks[trackKey] = entry;
    restyleTrack(entry, trackKey === selectedTrack);
    clipDeployTrack(entry, getClockMs(), trackKey === selectedTrack); // initial crop

    // At-time marker: rides the deployment's group, coloured with this deployment's
    // cycling colour, click highlights the whole deployment's array at the clock's
    // instant (setKey `deploy:<id>`).
    registerAtTimeMarker(layer, {
      color,
      label: `#${(props.index ?? 0) + 1}`,
      setKey: `deploy:${deploymentId}`,
      times,
      lats: latlngs.map((p) => p[0]),
      lngs: latlngs.map((p) => p[1]),
    });
  }
}


// Crop every virtual deployment's drift line to clock `ms` (release→clock forward,
// clock→release backward), re-raising a selected track. This is the loop app.js's
// updateClock used to run inline; it now calls this each scrub so the deploy internals
// (deployTracks / selectedTrack) stay owned here.
function clipAllDeployTracks(ms) {
  for (const key of Object.keys(deployTracks))
    clipDeployTrack(deployTracks[key], ms, key === selectedTrack);
}

// Clear the drop-set + track highlights — the background-click handler's block, moved
// here so app.js's map "click" never reaches into the deploy selection state.
function clearSelections() {
  if (selectedDropSet != null) { selectedDropSet = null; applyDropSetSelection(); }
  if (selectedTrack != null) { selectedTrack = null; applyTrackSelection(); }
}

