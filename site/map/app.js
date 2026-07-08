/* 2026 Whirls Cruise — drifter map.
 *
 * Static client. Fetches the build artifacts from ./data/ and renders them as
 * Leaflet layers:
 *   latest.geojson                 -> circle markers (on by default)
 *   tracks.geojson                 -> trajectory lines (off by default)
 *   forecast.geojson               -> per-drifter current-advection track (off)
 *   hindcast.geojson               -> per-drifter current-advection back-track (off)
 *   speed_±NNh.webp + currents_meta.json -> surface-speed shading, one lossless
 *                                           WebP frame per slider offset (imageOverlay)
 *   currents.json                  -> leaflet-velocity flow trails (optional)
 *   inertial_field.json            -> animated near-inertial particle tracks (off)
 *   awaiting.json                  -> sidebar list, no map geometry
 *   build.json                     -> sidebar "data freshness" build time
 */

const DATA = {
  latest: "./data/latest.geojson",
  tracks: "./data/tracks.geojson",
  forecast: "./data/forecast.geojson",
  hindcast: "./data/hindcast.geojson",
  awaiting: "./data/awaiting.json",
  currents: "./data/currents.json",
  // Shading rasters are per-frame files named in the metas' `frames` manifest
  // (speed_±NNh.webp / vorticity_±NNh.webp), resolved under this base.
  dataBase: "./data/",
  meta: "./data/currents_meta.json",
  vorticityMeta: "./data/vorticity_meta.json",
  inertialField: "./data/inertial_field.json",
  build: "./data/build.json",
  gliders: "./data/gliders.geojson",
  agulhas: "./data/agulhas.json",
};

// Flow-trail colour ramp: mostly dark, white only in the fast jet, so the green
// shading carries magnitude and the trails read as texture that brightens at speed.
const FLOW_COLORS = [
  "#101010", "#101010", "#181818", "#242424", "#363636",
  "#4d4d4d", "#6f6f6f", "#9a9a9a", "#cccccc", "#ffffff",
];

// Fallback view if no valid positions are present (cruise staging, Table Bay).
const FALLBACK_CENTER = [-33.9, 18.43];
const FALLBACK_ZOOM = 12;

// R/V Marion Dufresne live track. Fetched client-side from the French
// Oceanographic Fleet (Flotte Océanographique Française) localisation API — the
// same source as the IPSL WHIRLS "platform positions" button. CORS-open, no
// auth. Unlike the other layers this is not a build artifact: it polls live so
// the marker tracks the ship between rebuilds. See docs/ship.md.
const SHIP = {
  positions:
    "https://localisation.flotteoceanographique.fr/api/v2/vessels/MD/positions",
  // Cruise-window start; matches the IPSL WHIRLS operational map. endDate is now.
  cruiseStart: "2026-06-24T00:00:00.000Z",
  refreshMs: 5 * 60 * 1000, // API reports ~every 10 min; poll at 5.
};

// The two cruise vessels share one ship renderer (makeShipLayer), differing only
// in colour, sidebar panel, and the tooltip/readout rows a fix produces — so one
// `rows(fix, prevFix)` per vessel is all that varies. See docs/ship.md.
//
// The Marion Dufresne is live (CORS-open API, polled in the browser) and carries
// no reported speed/course, so its rows *derive* motion from the last track
// segment and add its met data. The Agulhas is baked at build time (its THREDDS
// CSV sends no CORS header, so the browser can't read it — the build writes
// agulhas.json) and carries *reported* speed/course + status/area, but no met.
const VESSELS = {
  md: {
    name: "R/V Marion Dufresne",
    source: "Flotte Océanographique Française",
    trackColor: "#1a1a1a",
    haloColor: "#ffffff",
    markerColor: "#1a1a1a",
    panel: { time: "md-ship-time", readout: "md-ship-readout" },
    rows: (p, prev) => mdRows(p, motionBetween(prev, p)),
  },
  agulhas: {
    name: "R/V S.A. Agulhas II",
    source: "myshiptracking.com (via IPSL WHIRLS)",
    // Deep crimson: reads apart from the MD's near-black, the drifters'
    // blue/teal, and the gliders' amber/sky.
    trackColor: "#9b1c31",
    haloColor: "#ffffff",
    markerColor: "#9b1c31",
    panel: { time: "agulhas-ship-time", readout: "agulhas-ship-readout" },
    rows: (p) => agulhasRows(p),
  },
};

// --- batch styling seam -----------------------------------------------------
// Markers carry a `batch` property. All per-batch appearance decisions funnel
// through styleForBatch(); the batch filter control (below) reads the same
// `batch` property to group markers. Staged (not-yet-deployed) drifters render
// muted grey; each deployment batch gets its own vivid colour so in-water
// drifters stand out and successive deployments read apart. A further deployment
// with no entry falls back to DEPLOYED_STYLE (blue) until given its own colour.
const BATCH_STYLES = {
  pre_deploy: { color: "#7a7a7a", fillColor: "#a8a8a8" },
  deployment_1: { color: "#1f5fa8", fillColor: "#3a8ddb" }, // blue
  deployment_2: { color: "#0d7d72", fillColor: "#17b3a3" }, // teal
  deployment_3: { color: "#b5540e", fillColor: "#e8791f" }, // orange
};
const DEPLOYED_STYLE = { color: "#1f5fa8", fillColor: "#3a8ddb" };
function styleForBatch(batch) {
  return {
    radius: 6,
    weight: 1,
    fillOpacity: 0.85,
    ...(BATCH_STYLES[batch] ?? DEPLOYED_STYLE),
  };
}

// Pretty labels for known batch keys; unknown keys (e.g. a future deployment_2)
// fall back to the raw value, so new batches surface readably with no code change.
const BATCH_LABELS = {
  pre_deploy: "Drifter pre",
  deployment_1: "Drifter batch 1",
  deployment_2: "Drifter batch 2",
  deployment_3: "Drifter batch 3",
};
// Instrument rows share this control: drifter batches use BATCH_LABELS; glider-
// group types (xspar/seaglider/float) fall back to their GLIDER_STYLES label so
// they read as "XSPAR buoy" / "Seagliders" / "Floats" in the same compartment.
const batchLabel = (batch) =>
  BATCH_LABELS[batch] ?? GLIDER_STYLES[batch]?.label ?? batch;

// Instrument row order: alphabetical by key, except the Floats row is pinned to
// the bottom of the list rather than sorting into the middle on its "f" key.
const instrumentOrder = (a, b) => {
  if (a === b) return 0;
  if (a === "float") return 1;
  if (b === "float") return -1;
  return a < b ? -1 : 1;
};
// ---------------------------------------------------------------------------

// `optional: true` means "never throws" — it swallows not just HTTP error
// statuses but also `fetch` rejections (DNS/offline/CORS) and non-JSON bodies,
// returning null. This is the contract every best-effort layer relies on so a
// failed fetch can't bubble out of main() and blank the map; it matters most for
// the one third-party fetch (the ship), but holds same-origin layers too.
async function fetchJSON(url, { optional = false } = {}) {
  try {
    const resp = await fetch(url);
    if (!resp.ok) {
      if (optional) return null;
      throw new Error(`${url}: HTTP ${resp.status}`);
    }
    return await resp.json();
  } catch (err) {
    if (optional) return null;
    throw err;
  }
}

function formatFixTime(iso) {
  if (!iso) return "unknown";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().replace("T", " ").replace(/\.\d+Z$/, "Z");
}

// Data-freshness panel. Build time is static (from build.json, written once per
// build); current time is a live UTC clock so the two read on the same scale and
// the age of the data is obvious at a glance.
function renderBuildTime(build) {
  const el = document.getElementById("build-time");
  if (!el) return;
  el.textContent = build && build.built_at ? formatFixTime(build.built_at) : "unknown";
}

function startClock() {
  const el = document.getElementById("now-time");
  if (!el) return;
  const tick = () => {
    el.textContent = formatFixTime(new Date().toISOString());
  };
  tick();
  setInterval(tick, 1000);
}

// 16-point compass label for a bearing in degrees true. Shared by the drifter
// tooltips and the ship readout.
const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                 "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
const compassPoint = (deg) => COMPASS[Math.round(deg / 22.5) % 16];

const MS_TO_KN = 1.943844;
// Every speed reads in both units (knots and m/s) so the ship (nautical, knots)
// and the drifters (oceanographic, m/s) are directly comparable. Input is m/s.
const speedBoth = (mps) => `${(mps * MS_TO_KN).toFixed(1)} kn / ${mps.toFixed(2)} m/s`;

// Drifter velocity formatters. Direction in degrees(+compass); a dash marks a
// value that is absent (no reported field) or underived (a track's first fix, or
// a zero-length step).
const fmtSpeedMps = (v) => (v != null ? speedBoth(v) : "—");
const fmtDir = (deg) => {
  if (deg == null) return "—";
  const d = ((deg % 360) + 360) % 360; // reported direction can be negative
  return `${Math.round(d) % 360}° ${compassPoint(d)}`;
};

function popupHtml(props, latlng) {
  const p = props || {};
  return `
    <div class="popup">
      <strong>${p.D_number ?? "—"}</strong><br/>
      <span class="popup-label">Last fix:</span> ${formatFixTime(p.date_UTC)}<br/>
      <span class="popup-label">Battery:</span> ${p.batteryState ?? "—"}<br/>
      <span class="popup-label">Speed (derived):</span> ${fmtSpeedMps(p.derived_speed_mps)}<br/>
      <span class="popup-label">Heading (derived):</span> ${fmtDir(p.derived_heading_deg)}<br/>
      <span class="popup-label">Speed (reported):</span> ${fmtSpeedMps(p.U_speed_mps)}<br/>
      <span class="popup-label">Heading (reported):</span> ${fmtDir(p.U_Dir_deg)}<br/>
      <span class="popup-label">Position:</span>
      ${latlng.lat.toFixed(5)}, ${latlng.lng.toFixed(5)}
    </div>`;
}

// --- click-to-highlight: instrument track selection -------------------------
// Every instrument that carries a track — a drifter, a seaglider, the XSPAR — is
// a set of clickable elements: its trajectory line, its per-fix dots, and its
// latest-position head marker (built by buildTrackGroups/buildBatchGroups for
// drifters, buildGliderTrackGroups/buildGliderMarkerGroups for gliders). Each
// element registers a restyle callback here under its instrument key (a drifter
// D_number or a glider id); clicking any of them selects that instrument.
// Selecting *brightens* its line and dots and enlarges its head, and
// *desaturates* every other instrument — greying the rest rather than fading it,
// so one track lifts out of the tangle while the others stay legible. Clicking the
// selection again, or the empty map (via bubblingMouseEvents:false + a map "click"
// handler in main), clears it. Ship tracks are deliberately not registered — they
// carry no selection. (See docs/trajectories.md.)
//
// A part registers a `restyle(state)` closure — state is "selected" | "dim" |
// "normal" — rather than the raw layer, so each element kind (SVG circle/line vs.
// glider divIcon) owns how it renders each state. restyle mutates layer options
// (setStyle / setIcon), so the styling survives a batch toggle's remove/re-add.
// TRACK_COLOR (defined below) is read at call time, never at load.
const SELECTED_COLOR = "#ff8c42"; // brighter than TRACK_COLOR for the picked track

// Mix a hex colour toward its own grey (luminance) by `amount` in [0,1] — reduces
// saturation without touching opacity, which is how un-selected tracks are dimmed.
function desaturate(hex, amount = 0.72) {
  const n = parseInt(hex.slice(1), 16);
  const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  const grey = 0.3 * r + 0.59 * g + 0.11 * b;
  const hx = (c) => Math.round(c + (grey - c) * amount).toString(16).padStart(2, "0");
  return `#${hx(r)}${hx(g)}${hx(b)}`;
}

const trackParts = {}; // instrument key -> [restyle(state), ...]
let selectedInstrument = null;

const stateFor = (key) =>
  key === selectedInstrument ? "selected" : selectedInstrument ? "dim" : "normal";

// Register a freshly-built element's restyler and immediately apply the current
// selection state, so a part built while a selection is active renders correctly.
function registerPart(key, restyle) {
  (trackParts[key] ??= []).push(restyle);
  restyle(stateFor(key));
}

function applySelection() {
  for (const key of Object.keys(trackParts))
    for (const restyle of trackParts[key]) restyle(stateFor(key));
}

// Toggle: clicking the current selection clears it; another instrument replaces it.
function selectInstrument(key) {
  selectedInstrument = key === selectedInstrument ? null : key;
  applySelection();
}

// Shared line/dot restylers — all track lines and dots are TRACK_COLOR, so both
// drifter and glider elements use these. Opacity is held constant across states:
// dimming is by desaturation, not transparency.
const lineStyle = (state) =>
  state === "selected"
    ? { color: SELECTED_COLOR, weight: 5, opacity: 1 }
    : { color: state === "dim" ? desaturate(TRACK_COLOR) : TRACK_COLOR, weight: 2, opacity: 0.85 };
const dotStyle = (state) => {
  const c =
    state === "selected" ? SELECTED_COLOR : state === "dim" ? desaturate(TRACK_COLOR) : TRACK_COLOR;
  return { color: c, fillColor: c, opacity: 1, fillOpacity: state === "selected" ? 1 : 0.9 };
};
// A drifter head is a per-batch circleMarker: hold its batch colour, enlarge it
// when selected, and desaturate that batch colour when another instrument is.
function styleHead(marker, base, state) {
  const dim = state === "dim";
  marker.setStyle({
    color: dim ? desaturate(base.color) : base.color,
    fillColor: dim ? desaturate(base.fillColor) : base.fillColor,
    opacity: 1,
    fillOpacity: base.fillOpacity,
  });
  marker.setRadius(state === "selected" ? base.radius + 3 : base.radius);
}

// Group latest-position markers into one feature group per `batch` value, so the
// batch filter control can toggle each independently. Each marker also registers
// as its drifter's head and selects that drifter on click. Returns { batch: group }.
function buildBatchGroups(geojson) {
  const groups = {};
  for (const feature of geojson.features ?? []) {
    if (feature.geometry?.type !== "Point") continue;
    const [lng, lat] = feature.geometry.coordinates;
    const batch = feature.properties?.batch ?? "unknown";
    const base = styleForBatch(batch);
    const marker = L.circleMarker([lat, lng], {
      ...base,
      pane: "drifters",
      bubblingMouseEvents: false, // background clicks (not this) clear selection
    });
    marker.bindTooltip(popupHtml(feature.properties, marker.getLatLng()));
    const dNumber = feature.properties?.D_number;
    if (dNumber != null) {
      registerPart(dNumber, (s) => styleHead(marker, base, s));
      marker.on("click", () => selectInstrument(dNumber));
    }
    (groups[batch] ??= L.featureGroup()).addLayer(marker);
  }
  return groups;
}

// A checkbox panel governing all instrument visibility — this control, not the
// Leaflet layer control, owns it. Instruments are the drifter batches and the
// glider platforms (XSPAR buoy, seagliders), each one row that shows/hides that
// instrument's markers. Above them, one master row per *overlay* (True track,
// Forecast, Hindcast) turns that overlay's per-instrument layers on or off for
// every instrument at once. Each overlay is `{ label, groups: {key: layer}, on }`
// keyed by the same instrument key as `markerGroups`. They compose with the
// instrument rows: an overlay's layer for an instrument shows only when both that
// instrument's row and the overlay's master row are checked, so unchecking an
// instrument hides its markers *and* every overlay riding on it. Data-driven from
// `markerGroups`, so new batches/gliders appear automatically; adding an overlay
// is one more list entry. (Gliders carry a track but no forecast/hindcast, so the
// Forecast/Hindcast masters simply have no glider layer to act on.) Markers start
// visible; overlays start at their own `on` (off by default).
function buildBatchControl(map, markerGroups, overlays) {
  // Pre-deployment drifters are staged (still aboard, not in the water), so they
  // start hidden; deployment batches start visible. sync() (called at build) then
  // reconciles the map to this initial state.
  const batchOn = {};
  for (const batch of Object.keys(markerGroups))
    batchOn[batch] = batch !== "pre_deploy";

  // Only the overlays with layers to show get a master row, so a missing/empty
  // artifact (no tracks, no forecast) doesn't leave a dead checkbox.
  const activeOverlays = overlays.filter((o) => Object.keys(o.groups).length);

  const toggle = (layer, show) =>
    layer && (show ? layer.addTo(map) : map.removeLayer(layer));

  function sync() {
    for (const batch of Object.keys(markerGroups)) {
      toggle(markerGroups[batch], batchOn[batch]);
      for (const overlay of activeOverlays) {
        toggle(overlay.groups[batch], batchOn[batch] && overlay.on);
      }
    }
  }

  const control = L.control({ position: "topright" });
  control.onAdd = () => {
    const div = L.DomUtil.create("div", "map-control batch-control");
    L.DomEvent.disableClickPropagation(div);
    L.DomEvent.disableScrollPropagation(div);
    const title = L.DomUtil.create("h4", "", div);
    title.textContent = "Instruments";

    // Master row per overlay, above the batch rows. A short line swatch in the
    // overlay's own colour keys the checkbox to the lines it draws on the map.
    for (const overlay of activeOverlays) {
      const row = L.DomUtil.create("label", "batch-row", div);
      const cb = L.DomUtil.create("input", "", row);
      cb.type = "checkbox";
      cb.checked = overlay.on;
      const swatch = L.DomUtil.create("span", "batch-line-swatch", row);
      swatch.style.background = overlay.color;
      const text = L.DomUtil.create("span", "batch-text", row);
      text.textContent = overlay.label;
      cb.addEventListener("change", () => {
        overlay.on = cb.checked;
        sync();
      });
    }

    // Divider separating the overlay (line) rows above from the batch (marker)
    // rows below.
    if (activeOverlays.length) L.DomUtil.create("hr", "batch-divider", div);

    for (const batch of Object.keys(markerGroups).sort(instrumentOrder)) {
      const group = markerGroups[batch];
      const row = L.DomUtil.create("label", "batch-row", div);
      const cb = L.DomUtil.create("input", "", row);
      cb.type = "checkbox";
      cb.checked = batchOn[batch];
      const swatch = L.DomUtil.create("span", "batch-swatch", row);
      // Glider rows key to their instrument colour; drifter batches to their
      // marker fill.
      swatch.style.background =
        GLIDER_STYLES[batch]?.color ?? styleForBatch(batch).fillColor;
      const text = L.DomUtil.create("span", "batch-text", row);
      text.textContent = `${batchLabel(batch)} (${group.getLayers().length})`;
      cb.addEventListener("change", () => {
        batchOn[batch] = cb.checked;
        sync();
      });
    }

    // Apply the initial visibility (hides the default-off pre-deployment batch,
    // which main() added to the map before this control was built).
    sync();
    return div;
  };
  return control;
}

// A Leaflet layer control that matches the Instruments box: the shared
// `map-control` class gives it the same styling, and a prepended <h4> gives it a
// title (Leaflet's layer control has neither natively). This is what lets the
// Currents and Ships controls read as titled boxes alongside the custom
// "Instruments" control. `baseLayers` (radios, mutually exclusive) and `overlays`
// (checkboxes) follow L.control.layers; either may be null/empty (the Ships
// control starts bare and gets its vessels via addOverlay on their first fix).
function titledLayerControl(map, baseLayers, overlays, title) {
  const control = L.control.layers(baseLayers, overlays, { collapsed: false }).addTo(map);
  const container = control.getContainer();
  container.classList.add("map-control");
  const heading = L.DomUtil.create("h4", "");
  heading.textContent = title;
  container.insertBefore(heading, container.firstChild);
  return control;
}

// Short offset label for a slider frame: "now", "+12h", "-12h".
function frameOffsetLabel(offsetH) {
  if (offsetH === 0) return "now";
  return (offsetH > 0 ? "+" : "") + offsetH + "h";
}

// Time-slider control (bottom-centre): scrubs the surface-current speed / ζ/f
// shadings through the CMEMS forecast at 12 h steps (-12 … now … +72 h). `frames`
// is the metas' shared manifest [{offset_h, valid_time, file}]; `onChange(i)`
// swaps every registered shading overlay to that frame. It is a plain positioned
// element (not an L.control) so it can centre and span the map width; Leaflet
// mouse propagation is disabled so dragging the handle never pans the map.
function buildTimeSlider(map, frames, nowIdx, onChange) {
  const el = L.DomUtil.create("div", "time-slider-control");
  const label = L.DomUtil.create("div", "ts-label", el);
  const input = L.DomUtil.create("input", "ts-range", el);
  input.type = "range";
  input.min = "0";
  input.max = String(frames.length - 1);
  input.step = "1";
  input.value = String(nowIdx);
  input.setAttribute("aria-label", "CMEMS forecast time");

  const ticks = L.DomUtil.create("div", "ts-ticks", el);
  frames.forEach((f) => {
    const t = L.DomUtil.create("span", "ts-tick" + (f.offset_h === 0 ? " ts-now" : ""), ticks);
    t.textContent = frameOffsetLabel(f.offset_h);
  });

  const setLabel = (i) => {
    const f = frames[i];
    const off = f.offset_h === 0 ? "now" : (f.offset_h > 0 ? "+" : "") + f.offset_h + " h";
    label.innerHTML =
      `<span class="ts-title">CMEMS field</span> ` +
      `<strong>${off}</strong> · ${formatFixTime(f.valid_time)}`;
  };
  setLabel(nowIdx);

  L.DomEvent.on(input, "input", () => {
    const i = Number(input.value);
    setLabel(i);
    onChange(i);
  });
  L.DomEvent.disableClickPropagation(el);
  L.DomEvent.disableScrollPropagation(el);
  map.getContainer().appendChild(el);
  return { el, setLabel };
}

// Track colour for the trajectory lines and the intermediate-fix dots that ride
// them — distinct from the blue latest-position markers, so the dots read as
// part of the trajectory rather than as separate platforms.
const TRACK_COLOR = "#e07b39";

// Forecast colour for the current-advection lines and their 1/3/6 h dots — a
// violet distinct from the orange past track and the blue head, so a glance
// separates "where it's been" from "where the field carries it next".
const FORECAST_COLOR = "#8e44ad";

// Hindcast colour — a magenta distinct from the forecast violet, the orange past
// track and the blue head, so the current-only back-track reads apart from the
// drifter's *observed* orange trajectory it sits near.
const HINDCAST_COLOR = "#d81b8c";

// --- interactive deploy endpoint (PoC) --------------------------------------
// One dynamic endpoint backs the deploy tool: `POST /api/forecast` takes a
// sequence of (lon, lat, start) seeds — the equally-spaced drops the client lays
// along a clicked path, each with its staggered water-entry time — and advects
// every one through the CMEMS window server-side (one GeoJSON LineString per seed,
// synced-t0 dots). The map and this API are separate endpoints served under one
// origin (the plan-017 gateway: /map and /api as sibling backends), so the base is
// resolved (not hardcoded) by two same-origin rules — no client-controlled override,
// so a crafted `?api=` link can't retarget the seed POST at a hostile host:
//   - in the two-port dev flow (static on :8000), auto-target the API on :8001, so
//     `pixi run serve` + `pixi run serve-api` needs no configuration;
//   - else same-origin `/api/forecast` — the only real deployment.
function resolveApi(path) {
  if (location.port === "8000")
    return `${location.protocol}//${location.hostname}:8001${path}`;
  return path;
}
const FORECAST_API = resolveApi("/api/forecast");

// Green — distinct from the orange track, violet forecast, magenta hindcast, and
// cyan inertial — because these lines are ad-hoc, user-placed, and never persisted.
const DEPLOY_COLOR = "#16a34a";

// Tangent-plane km per degree of latitude (R·π/180, R = 6371 km) and knots→km/h.
// The client owns the deployment geometry now: it resamples the clicked path into
// equally-spaced drops (cos-lat tangent plane) and staggers each drop's water-entry
// time by the ship-speed knob, so the API just advects the seeds it is handed.
const KM_PER_DEG = 111.19492664455873;
const KN_TO_KMH = 1.852;

// Trajectories, grouped by `batch` so each batch's lines+dots toggle with that
// batch's markers (see buildBatchControl). For each drifter: one line, plus a
// small dot at every fix. Each dot carries the same hover tooltip as the drifter's
// main marker, but filled with *that fix's* own time, battery, and reported/derived
// velocity — read from the per-vertex `fixes` array that rides parallel to
// `coordinates`. Tolerates a
// `fixes`-less artifact from an older build: the dot then falls back to the
// line-level identity (D_number/batch) with an unknown time. The line and dots
// are interactive: clicking either selects the drifter (see selectInstrument);
// both resolve to the same drifter, so there is no click to "swallow". Returns
// { batch: featureGroup }.
function buildTrackGroups(geojson) {
  const groups = {};
  for (const feature of geojson.features ?? []) {
    if (feature.geometry?.type !== "LineString") continue;
    const { D_number, batch, fixes } = feature.properties ?? {};
    const key = batch ?? "unknown";
    const group = (groups[key] ??= L.featureGroup());
    const coords = feature.geometry.coordinates;
    const line = L.polyline(coords.map(([lng, lat]) => [lat, lng]), {
      color: TRACK_COLOR,
      weight: 2,
      opacity: 0.85,
      bubblingMouseEvents: false, // background clicks (not this) clear selection
    }).addTo(group);
    if (D_number != null) {
      registerPart(D_number, (s) => line.setStyle(lineStyle(s)));
      line.on("click", () => selectInstrument(D_number));
    }
    coords.forEach(([lng, lat], i) => {
      const fix = fixes?.[i] ?? {};
      const dot = L.circleMarker([lat, lng], {
        radius: 3,
        color: TRACK_COLOR,
        weight: 1,
        fillColor: TRACK_COLOR,
        fillOpacity: 0.9,
        bubblingMouseEvents: false, // background clicks (not this) clear selection
      });
      // The fix record already carries date/battery/reported+derived velocity;
      // add the line-level identity for the same tooltip as the main marker.
      dot.bindTooltip(popupHtml({ D_number, batch, ...fix }, dot.getLatLng()));
      if (D_number != null) {
        registerPart(D_number, (s) => dot.setStyle(dotStyle(s)));
        dot.on("click", () => selectInstrument(D_number));
      }
      group.addLayer(dot);
    });
  }
  return groups;
}

// Current-advection line (forecast forward, or hindcast backward), grouped by
// `batch` so each batch's lines+dots toggle with that batch's markers and the
// master Forecast/Hindcast row (see buildBatchControl). For each drifter: one
// solid line from its head — the time-dependent-current advection path, which
// curls into the model's inertial loop — in `color`, plus a small dot at each
// `marks` entry (1/3/6 h). Dots carry no
// popup — they are plain position marks (the line and dots are non-interactive so
// they never swallow a click meant for a marker beneath them). Returns
// { batch: featureGroup }.
function buildAdvectionGroups(geojson, color) {
  const groups = {};
  for (const feature of geojson.features ?? []) {
    if (feature.geometry?.type !== "LineString") continue;
    const { batch, marks } = feature.properties ?? {};
    const key = batch ?? "unknown";
    const group = (groups[key] ??= L.featureGroup());
    const coords = feature.geometry.coordinates;
    L.polyline(
      coords.map(([lng, lat]) => [lat, lng]),
      {
        color,
        weight: 2,
        opacity: 0.9,
        interactive: false,
      }
    ).addTo(group);
    for (const m of marks ?? []) {
      L.circleMarker([m.lat, m.lon], {
        radius: 3,
        color,
        weight: 1,
        fillColor: color,
        fillOpacity: 0.9,
        interactive: false,
      }).addTo(group);
    }
  }
  return groups;
}

// --- interactive deployment planner (PoC) -----------------------------------
// One top-right "Deploy" control arms a multi-click placement mode: click a path
// (2+ vertices; double-click to finish), and the client lays drifter drops at
// equal spacing along it — the drop spacing (km) and ship speed (kn) are knobs. A
// live preview (redrawn on mousemove, no fetch) foretells the polyline, the
// equally-spaced drops it implies, and the transit time. On finish placeDeployment
// resamples the path, staggers each drop's water-entry time by the ship speed, and
// POSTs the (lon, lat, start) seeds to /api/forecast; the returned per-drop
// advection lines + synced-t0 dots are drawn over the drops. These are ad-hoc,
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
  const path = cursor ? [...vertices, cursor] : vertices.slice();
  if (path.length >= 2) {
    L.polyline(path, {
      pane: "deployTracks", color: DEPLOY_COLOR, weight: 2, opacity: 0.85,
      dashArray: "5 4", interactive: false,
    }).addTo(previewLayer);
  }
  const { drops, totalKm } = resamplePolyline(path, opts.spacing);
  for (const d of drops) {
    L.circleMarker(d.latlng, {
      pane: "deployDrops", radius: 3, color: "#fff", weight: 1,
      fillColor: DEPLOY_COLOR, fillOpacity: 0.9, interactive: false,
    }).addTo(previewLayer);
  }
  for (const v of vertices) {
    L.circleMarker(v, {
      pane: "deployDrops", radius: 4, color: DEPLOY_COLOR, weight: 2,
      fill: false, interactive: false,
    }).addTo(previewLayer);
  }
  const transitH = totalKm / (opts.shipKn * KN_TO_KMH);
  L.tooltip({ permanent: true, direction: "top", className: "pt-preview-label", offset: [0, -8] })
    .setLatLng(cursor ?? path[path.length - 1])
    .setContent(`${drops.length} drops · ${totalKm.toFixed(1)} km · ~${transitH.toFixed(1)} h`)
    .addTo(previewLayer);
}

// The committed ship track: a dashed grey line through the clicked vertices (the
// route the ship steams), drawn below the drops and forecasts.
function drawShipTrack(vertices, deployLayer) {
  if (vertices.length < 2) return;
  L.polyline(vertices, {
    pane: "deployTracks", color: "#555", weight: 1.5, opacity: 0.7,
    dashArray: "4 3", interactive: false,
  }).addTo(deployLayer);
}

// The committed drop discs: a white-ringed green disc per drop, tooltip = deploy
// order + water-entry ETA. `drops` is [{ latlng, start }]. Rendered in the
// deployDrops pane, so they sit above the tracks but below the +Δt mark dots
// regardless of draw order. Each disc is clickable: it highlights the whole deployment's
// drop set (every disc, enlarged + dark-ringed — see selectDropSet). Its click is
// swallowed (bubblingMouseEvents:false) so it highlights rather than reaching the map
// (which would clear selections or add a path vertex).
function drawDrops(drops, deployLayer, deploymentId) {
  const set = (deployDropSets[deploymentId] ??= []);
  const selected = String(deploymentId) === selectedDropSet;
  drops.forEach((d, i) => {
    const disc = L.circleMarker(d.latlng, {
      pane: "deployDrops", radius: DEPLOY_DROP_RADIUS, color: "#fff", weight: 1,
      fillColor: DEPLOY_COLOR, fillOpacity: 1,
      bubblingMouseEvents: false,
    });
    disc.bindTooltip(`#${i + 1} · ${d.start}`, { direction: "top" });
    disc.on("click", () => selectDropSet(deploymentId));
    set.push(disc);
    restyleDropDisc(disc, selected);
    disc.addTo(deployLayer);
  });
}

// The Deploy tool: a multi-click polyline placement mode. Click adds a vertex;
// double-click finishes (Leaflet fires two clicks before a dblclick, so the
// near-duplicate tail vertex is dropped and doubleClickZoom is disabled while
// armed). The knobs (drop spacing km, ship speed kn, forecast horizon h) bind
// straight onto `state`. Returns { control, state, handleClick, handleDblClick,
// handleMove }; main() routes background events through the handlers.
function buildDeployTool(deployLayer) {
  const state = {
    on: false,
    vertices: [],   // LatLng[] — the clicked path, grows per click
    spacing: 5,     // km between drops along the path
    shipKn: 10,     // ship transit speed, knots
    horizonH: 48,   // forecast horizon from the run start, hours
    forecast: true, // request per-drop drift (else draw geometry only)
  };
  let statusEl = null;
  let mapRef = null;
  const setStatus = (msg) => {
    if (statusEl) statusEl.textContent = msg;
  };

  // previewLayer holds the ephemeral path/drops preview (cleared + redrawn per
  // move/click); wiped on reset (disarm / finish / clear).
  const previewLayer = L.featureGroup();
  const resetPath = () => {
    state.vertices = [];
    previewLayer.clearLayers();
  };

  // Two clicked vertices are "the same" (a dblclick's duplicate) when within a few
  // screen pixels — so finishing on a double-click doesn't add a spurious vertex.
  const isDuplicate = (a, b) =>
    mapRef &&
    mapRef.latLngToContainerPoint(a).distanceTo(mapRef.latLngToContainerPoint(b)) < 8;

  const handleClick = (latlng) => {
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
  };

  // Abort an in-progress path (right-click / Escape): discard the clicked vertices and
  // wipe the preview without committing. Stays armed, so the next click starts fresh.
  // Returns whether a path was actually in progress, so the caller only swallows the
  // event (browser context menu) when it consumed one.
  const handleAbort = () => {
    if (!state.on || !state.vertices.length) return false;
    resetPath();
    setStatus("cancelled — click a path · double-click to finish");
    return true;
  };

  const control = L.control({ position: "topright" });
  control.onAdd = (map) => {
    mapRef = map;
    previewLayer.addTo(map);

    const div = L.DomUtil.create("div", "map-control deploy-tool");
    L.DomEvent.disableClickPropagation(div);
    L.DomEvent.disableScrollPropagation(div);

    const title = L.DomUtil.create("h4", "", div);
    title.textContent = "Deploy ";
    L.DomUtil.create("span", "ft-poc", title).textContent = "PoC";

    const toggle = L.DomUtil.create("button", "ft-btn ft-toggle", div);
    toggle.type = "button";
    const paint = () => {
      toggle.textContent = state.on ? "Click-to-place: ON" : "Click-to-place: OFF";
      toggle.classList.toggle("on", state.on);
      map.getContainer().classList.toggle("deploy-cursor", state.on);
      // Suppress double-click zoom while armed, so a finishing dbl-click doesn't
      // also zoom the map.
      if (state.on) map.doubleClickZoom.disable();
      else map.doubleClickZoom.enable();
    };
    toggle.addEventListener("click", () => {
      state.on = !state.on;
      if (!state.on) {
        resetPath();
        setStatus("");
      }
      paint();
    });
    paint();

    // Compact number rows binding onto state: one labelled <input type=number> per
    // knob; a change writes the parsed value straight back.
    const numRow = (label, key, step) => {
      const row = L.DomUtil.create("label", "pt-row", div);
      L.DomUtil.create("span", "pt-label", row).textContent = label;
      const input = L.DomUtil.create("input", "pt-num", row);
      input.type = "number";
      input.step = step;
      input.min = step; // all three knobs must be positive (spacing 0 would hang)
      input.value = state[key];
      input.addEventListener("change", () => {
        const val = parseFloat(input.value);
        if (!Number.isNaN(val) && val > 0) state[key] = val;
      });
    };
    numRow("Drop spacing (km)", "spacing", "0.5");
    numRow("Ship speed (kn)", "shipKn", "0.5");
    numRow("Forecast (h)", "horizonH", "6");

    const checkRow = L.DomUtil.create("label", "pt-row", div);
    const check = L.DomUtil.create("input", "pt-check", checkRow);
    check.type = "checkbox";
    check.checked = state.forecast;
    check.addEventListener("change", () => {
      state.forecast = check.checked;
    });
    L.DomUtil.create("span", "pt-label", checkRow).textContent = "Forecast drift";

    const clear = L.DomUtil.create("button", "ft-btn", div);
    clear.type = "button";
    clear.textContent = "Clear";
    clear.addEventListener("click", () => {
      deployLayer.clearLayers();
      resetDeployHighlights(); // drop the highlight registries along with the layers they point at
      resetPath();
      setStatus("");
    });

    L.DomUtil.create("p", "ft-hint", div).textContent =
      "click a path · double-click to finish · right-click / Esc to cancel";
    buildDeployLegend(div);
    statusEl = L.DomUtil.create("p", "ft-status", div);

    return div;
  };
  return { control, state, handleClick, handleDblClick, handleMove, handleAbort };
}

// Resample the finished path into equally-spaced drops, stagger each drop's
// water-entry time by the ship speed, draw the ship track + drops, and (if the
// forecast checkbox is on) POST the seeds to /api/forecast and draw the returned
// per-drop advection lines + synced-t0 dots. `startTime` (the displayed field's
// valid time) is the run start, so drop #1 enters at the field's instant.
async function placeDeployment(vertices, deployLayer, setStatus, startTime, opts) {
  const deploymentId = ++deployCounter; // namespaces this placement's dot columns + drop rows
  const { drops, totalKm } = resamplePolyline(vertices, opts.spacing);
  const seeds = drops.map((d) => ({
    lon: Number(d.latlng.lng.toFixed(5)),
    lat: Number(d.latlng.lat.toFixed(5)),
    start: seedTime(startTime, d.cumKm, opts.shipKn),
  }));
  const dropRecords = drops.map((d, i) => ({ latlng: d.latlng, start: seeds[i].start }));
  drawShipTrack(vertices, deployLayer);

  const transitH = totalKm / (opts.shipKn * KN_TO_KMH);
  const geom = `${drops.length} drops · ${totalKm.toFixed(1)} km · ~${transitH.toFixed(1)} h transit`;
  if (!opts.forecast) {
    drawDrops(dropRecords, deployLayer, deploymentId);
    setStatus(`${geom} · drift off`);
    return;
  }

  setStatus(`${geom} · forecasting…`);
  try {
    const resp = await fetch(FORECAST_API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ seeds, horizon_h: opts.horizonH }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      drawDrops(dropRecords, deployLayer, deploymentId);
      setStatus(`${geom} · ${data.detail || data.error || `error ${resp.status}`}`);
      return;
    }
    const horizonH = data.properties?.horizon_h ?? opts.horizonH;
    const markStepH = data.properties?.mark_step_h ?? DEPLOY_MARK_STEP_H;
    const runStart = data.properties?.run_start ?? startTime;
    drawDeployForecastLines(data.features ?? [], deployLayer, markStepH, runStart, deploymentId);
    updateDeployLegend(horizonH, markStepH); // ticks + bands match the grid these dots used
    // Drops and mark dots live in stacked panes (deployDrops < deployDots), so the
    // draw order here doesn't matter — the discs sit below the delayed dots either way.
    drawDrops(dropRecords, deployLayer, deploymentId);
    const p = data.properties ?? {};
    // A plan whose run start predates (or postdates) the loaded field window gets
    // every seed skipped — say so, rather than a bare "0/N drift" that reads as a bug.
    if (p.forecasts === 0 && p.n_seeds > 0) {
      const w = p.window ? ` (field ${p.window[0]}…${p.window[1]})` : "";
      setStatus(`${geom} · 0 drift — run start outside the field window${w}`);
    } else {
      setStatus(`${geom} · ${p.forecasts}/${p.n_seeds} drift`);
    }
  } catch (err) {
    drawDrops(dropRecords, deployLayer, deploymentId);
    setStatus(`${geom} · request failed — is \`pixi run serve-api\` running?`);
  }
}

// Synced-snapshot dot colours. Every drop is dotted at the *same* wall-clock times
// (run start + k·mark_step h), so a dot's colour — keyed to its absolute time since
// the run start — makes one colour the whole array at one t0: read a pattern off the
// map by picking a colour. The palette is matplotlib's **tab20c**: a categorical map
// of five hue families (blue, orange, green, purple, grey), each in four shades from
// dark to light. It's indexed by mark ordinal (k = hours / mark_step), so consecutive
// marks step through it — every fourth mark opens a new hue family, the three between
// are lightness steps of that hue. Adjacent marks stay easy to tell apart, and the
// family boundary gives a coarse "which quarter of the run" read. Used discrete, not
// interpolated: blending across a qualitative palette's entries muddies it. The index
// wraps past 20 marks (rare at the default 3 h / 48 h = 16). mark_step falls back to
// the server's _DEFAULT_MARK_STEP_H, the horizon to _DEFAULT_HORIZON_H.
const DEPLOY_HORIZON_H = 48;
const DEPLOY_MARK_STEP_H = 3;
const DEPLOY_TAB20C = [
  "#3182bd", "#6baed6", "#9ecae1", "#c6dbef", // blues
  "#e6550d", "#fd8d3c", "#fdae6b", "#fdd0a2", // oranges
  "#31a354", "#74c476", "#a1d99b", "#c7e9c0", // greens
  "#756bb1", "#9e9ac8", "#bcbddc", "#dadaeb", // purples
  "#636363", "#969696", "#bdbdbd", "#d9d9d9", // greys
];

// The tab20c swatch for the mark at absolute run-relative `hours`: its ordinal
// k = round(hours / mark_step) (marks sit on run_start + k·step, so this is exact),
// mapped 1-based into the palette and wrapped, so the first mark takes the first
// swatch.
function deployMarkColor(hours, markStepH) {
  const step = markStepH > 0 ? markStepH : DEPLOY_MARK_STEP_H;
  const k = Math.max(0, Math.round(hours / step) - 1);
  return DEPLOY_TAB20C[k % DEPLOY_TAB20C.length];
}

// Shared synced-snapshot dot legend: a caption, the tab20c band bar from the run
// start to the forecast horizon (the same swatch sequence deployMarkColor steps
// through), and three ticks (run · mid · horizon) marking the span's midpoint and end.
// Appended to the Deploy control, so a dot's colour reads as its t0 — the array's
// shape at one instant since the run began — and the caption points at the
// click-to-highlight interaction. Repainted per placement (updateDeployLegend) so both
// the hour ticks and the number of colour bands track the horizon + mark step the dots
// were actually coloured over — the "Forecast (h)" knob / the server-echoed horizon_h
// and mark_step_h — not a fixed 48 h / 3 h.
let deployLegendMidTick = null;
let deployLegendEndTick = null;
let deployLegendBar = null;

function updateDeployLegend(horizonH, markStepH) {
  const h = horizonH > 0 ? horizonH : DEPLOY_HORIZON_H;
  const step = markStepH > 0 ? markStepH : DEPLOY_MARK_STEP_H;
  if (deployLegendMidTick) deployLegendMidTick.textContent = `+${+(h / 2).toFixed(1)} h`;
  if (deployLegendEndTick) deployLegendEndTick.textContent = `+${+h.toFixed(1)} h`;
  if (deployLegendBar) {
    // One hard-edged band per mark over the horizon (left = run, right = horizon), so
    // the bar shows the discrete swatch sequence, not a smooth gradient.
    const n = Math.max(1, Math.round(h / step));
    const bands = [];
    for (let k = 0; k < n; k++) {
      const c = deployMarkColor((k + 1) * step, step);
      bands.push(`${c} ${((k / n) * 100).toFixed(2)}% ${(((k + 1) / n) * 100).toFixed(2)}%`);
    }
    deployLegendBar.style.background = `linear-gradient(to right, ${bands.join(", ")})`;
  }
}

function buildDeployLegend(div) {
  const legend = L.DomUtil.create("div", "pt-legend", div);
  L.DomUtil.create("span", "pt-legend-cap", legend).textContent =
    "dot = array at run +Δt · click one → its whole Δt";
  deployLegendBar = L.DomUtil.create("div", "pt-legend-bar", legend);
  const ticks = L.DomUtil.create("div", "pt-legend-ticks", legend);
  L.DomUtil.create("span", "", ticks).textContent = "run";
  deployLegendMidTick = L.DomUtil.create("span", "", ticks);
  deployLegendEndTick = L.DomUtil.create("span", "", ticks);
  updateDeployLegend(DEPLOY_HORIZON_H, DEPLOY_MARK_STEP_H); // default until first placement
}

// --- deploy-dot highlight ----------------------------------------------------
// The synced-t0 dots ARE the array's shape at one instant: every "+3 h" dot of a
// deployment shares one wall-clock time. Clicking one dot highlights EVERY dot at
// that same mark hour of that same deployment (enlarged + dark-ringed), so the array
// at one t0 is read by click, not just by colour. Each placed deployment gets its own
// id (deployCounter), so a +3 h click lifts only that run's +3 h dots and leaves
// another deployment's alone. Cleared by re-clicking the group, a background click, or
// Clear (resetDeployHighlights). A dot's own click is swallowed (bubblingMouseEvents:
// false) so it highlights rather than adding a path vertex / clearing a track selection.
let deployCounter = 0;
const deployDotGroups = {}; // `${deploymentId}|${hours}` -> [{ marker, base }]
let selectedDeployDot = null; // the selected group key, or null

function restyleDeployDot(marker, base, selected) {
  marker.setStyle({
    color: selected ? "#111827" : "#fff",
    weight: selected ? 2 : 1,
    fillColor: base.fillColor,
    fillOpacity: 0.95,
  });
  marker.setRadius(selected ? base.radius + 3 : base.radius);
  if (selected) marker.bringToFront();
}

function applyDeployDotSelection() {
  for (const key of Object.keys(deployDotGroups))
    for (const { marker, base } of deployDotGroups[key])
      restyleDeployDot(marker, base, key === selectedDeployDot);
}

// Restack the +Δt dots within the deployDots pane so later marks paint above earlier
// ones (DOM order is paint order there): bring every group to front in ascending
// hour, so a +3 h dot never hides the +48 h dot that overtook it. Sorts across all
// placed deployments; the selection pass afterwards lifts any highlighted group back
// on top.
function restackDeployDots() {
  const keys = Object.keys(deployDotGroups).sort(
    (a, b) => Number(a.split("|")[1]) - Number(b.split("|")[1]),
  );
  for (const key of keys)
    for (const { marker } of deployDotGroups[key]) marker.bringToFront();
}

// Toggle: clicking the selected group clears it; another group replaces it.
function selectDeployDot(key) {
  selectedDeployDot = key === selectedDeployDot ? null : key;
  applyDeployDotSelection();
}

// --- deploy drop-set + track highlight ---------------------------------------
// Two more read-by-click axes beside the dot columns (which lift all dots at one Δt):
//   • DROP SET — clicking any drop disc lifts EVERY drop disc of that deployment
//     (enlarged + dark-ringed), so the whole array of water-entry points reads at once.
//   • TRACK — clicking a forecast line, on bare track between the markers, lifts that
//     ONE drifter's trajectory (thickened + recoloured magenta).
// The drop set keys off the deployment id; the track off deployment id + drop index.
// All three axes (dot column, drop set, track) are independent — a disc / line / dot
// click is swallowed so it toggles its own axis without disturbing the others. Cleared
// by re-clicking, a background click, or Clear (resetDeployHighlights).
const DEPLOY_DROP_RADIUS = 4;
const deployDropSets = {}; // deploymentId -> [disc markers]
const deployTracks = {};   // `${deploymentId}#${index}` -> line
let selectedDropSet = null; // deploymentId (string) or null
let selectedTrack = null;   // track key or null

function restyleDropDisc(disc, selected) {
  disc.setStyle({ color: selected ? "#111827" : "#fff", weight: selected ? 2 : 1 });
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

function restyleTrack(line, selected) {
  line.setStyle({
    color: selected ? "#d81b8c" : DEPLOY_COLOR, // magenta pops off the green track set
    weight: selected ? 4 : 2,
    opacity: selected ? 1 : 0.9,
  });
  if (selected) line.bringToFront();
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

// Clear every deploy highlight — dot columns, drop sets, and tracks — and forget their
// registries. Called by the Deploy tool's Clear button along with the layers.
function resetDeployHighlights() {
  for (const key of Object.keys(deployDotGroups)) delete deployDotGroups[key];
  for (const id of Object.keys(deployDropSets)) delete deployDropSets[id];
  for (const key of Object.keys(deployTracks)) delete deployTracks[key];
  selectedDeployDot = null;
  selectedDropSet = null;
  selectedTrack = null;
}

// A mark's absolute wall-clock time (ISO-8601, whole seconds — the drop discs' ETA
// format): the run start plus its run-relative `hours`. Returns null when the run
// start is unknown (no field window echoed), so the caller falls back to the
// hours-only label.
function markIso(runStartISO, hours) {
  if (!runStartISO) return null;
  const ms = new Date(runStartISO).getTime() + hours * 3600 * 1000;
  return new Date(ms).toISOString().replace(/\.\d+Z$/, "Z");
}

// Draw one deployment's per-drop drift: a green line per forecast feature (in the
// deployTracks pane, click-highlightable as a single trajectory via a fat transparent
// hit-line), plus its synced-t0 dots (in the deployDots pane above the tracks and drop
// discs) — coloured by absolute mark hour (deployMarkColor) and clickable to highlight
// every same-hour dot of this deployment (`deploymentId`). A
// thin white ring keeps the colour legible over the currents overlay. Each dot's
// tooltip pairs the run-relative hours with the mark's absolute ISO time (from
// `runStart`). GeoJSON coords are [lon,lat]; Leaflet wants [lat,lng]. (The ship
// track + drops are drawn client-side by the caller; the API returns only forecast
// features.) A closing restack keeps later marks painted above earlier ones.
function drawDeployForecastLines(features, deployLayer, markStepH, runStart, deploymentId) {
  const ll = ([lon, lat]) => [lat, lon];
  for (const f of features) {
    if (f.properties?.role !== "forecast") continue;
    const coords = f.geometry?.coordinates ?? [];
    if (coords.length < 2) continue;
    const latlngs = coords.map(ll);
    const trackKey = `${deploymentId}#${f.properties.index}`;
    // The visible drift line (non-interactive), plus a fat transparent hit-line over the
    // same path so its thin stroke is easy to click on bare track. A track click lifts
    // this one trajectory (selectDeployTrack); the hit-line's click is swallowed so it
    // doesn't reach the map. Both sit in the lowest pane, so a click where a disc/dot
    // overlaps hits that marker instead of the track.
    const line = L.polyline(latlngs, {
      pane: "deployTracks", color: DEPLOY_COLOR, weight: 2, opacity: 0.9,
      interactive: false,
    }).addTo(deployLayer);
    L.polyline(latlngs, {
      pane: "deployTracks", color: "#000", weight: 12, opacity: 0,
      bubblingMouseEvents: false,
    })
      .on("click", () => selectDeployTrack(trackKey))
      .addTo(deployLayer);
    deployTracks[trackKey] = line;
    restyleTrack(line, trackKey === selectedTrack);
    for (const m of f.properties?.marks ?? []) {
      const base = { radius: 4, fillColor: deployMarkColor(m.hours, markStepH) };
      const key = `${deploymentId}|${m.hours}`;
      const dot = L.circleMarker([m.lat, m.lon], {
        pane: "deployDots",
        radius: base.radius,
        color: "#fff",
        weight: 1,
        fillColor: base.fillColor,
        fillOpacity: 0.95,
        bubblingMouseEvents: false, // its click highlights, doesn't reach the map
      });
      const iso = markIso(runStart, m.hours);
      dot.bindTooltip(iso ? `+${m.hours} h · ${iso}` : `+${m.hours} h`, { direction: "top" });
      dot.on("click", () => selectDeployDot(key));
      (deployDotGroups[key] ??= []).push({ marker: dot, base });
      restyleDeployDot(dot, base, key === selectedDeployDot);
      dot.addTo(deployLayer);
    }
  }
  // DOM/paint order within deployDots must run late-marks-on-top; the incremental
  // per-feature draw above interleaves hours, so restack once the dots are all in.
  restackDeployDots();
  applyDeployDotSelection();
}

// --- near-inertial animation -------------------------------------------------
// Animates the near-inertial (NI) rotary current the CMEMS field carries as
// particle TRACKS that trace the local inertial circle — moving dots leaving
// fading trails, reconstructed analytically on the client from a per-cell
// (mean, amplitude, phase) decomposition shipped in inertial_field.json (see
// plans/014-near-inertial-animation.md). The per-cell velocity is
//   u(t) + i·v(t) = (mean_u + i·mean_v) + amp · exp(i·(phase − f·dt))
//   f = 2·Ω·sin(lat)     (Ω = omega, from the header; f < 0 in the SH)
// but advection uses the NI term ALONE — u = amp·cos(phase−f·dt),
// v = amp·sin(phase−f·dt) — so a particle circles in place at the inertial
// frequency instead of being swept away by the background current. A single
// wall clock sweeps dt over [0, 24h) every INERTIAL_LOOP_S seconds; a canvas
// in the "inertial" pane redraws every frame. This is *not* the dropped
// animated drift dot (a marker walking the forecast/hindcast polylines,
// removed in e9b339c) — it animates a standalone particle field.
//
// Excluding the mean is deliberate: it isolates the pure inertial rotation.
// The true orbit is only ~350 m across (sub-pixel), so INERTIAL_ADVECT_SCALE
// magnifies it into a visible circle whose on-screen radius grows with zoom —
// legible when zoomed into the drifter cloud, vanishingly small zoomed out.
// The shipped mean_u/mean_v go unused by advection but stay in the artifact as
// the decomposition's documented seam (a future mean+NI mode is a one-liner).

const INERTIAL_LOOP_S = 12; // wall-clock seconds per animation loop
const INERTIAL_SPAN_S = 24 * 3600; // dt sweeps [0, 24h) per loop

// Particle system, classic wind-map style: a fixed pool of particles, each a
// plain {lon, lat, age} record, reseeded at a fresh random in-bounds position
// (with age reset) once it goes stale.
const INERTIAL_MAX_PARTICLES = 1200; // pool size, tuned for a legible density
const INERTIAL_MAX_AGE = 300; // frames before forced respawn (~5s @ 60fps)
// Per-frame alpha of the destination-out canvas clear — low -> long fading
// trails, high -> short ones. Purely cosmetic.
const INERTIAL_FADE_ALPHA = 0.05;
// Degrees of geographic displacement per (m/s) of NI speed, per frame.
// Decouples the *visual* advection from real time (like leaflet-velocity's
// velocityScale). With NI-only advection this doubles as the orbit magnifier:
// the ~350 m physical inertial circle is sub-pixel, so this scales it up to a
// visible loop. The orbital *angular* rate is fixed by the 24 h loop, so this
// sets the circle's RADIUS (and thus the tangential speed), not its period —
// larger = bigger, faster circles. Nudge to taste.
const INERTIAL_ADVECT_SCALE = 0.0072;
const INERTIAL_LINE_WIDTH = 1.3; // thin, so overlapping trails don't clump

// Cyan, distinct from the orange true track, the violet forecast / magenta
// hindcast advection lines, and the dark->white flow-trail ramp.
const INERTIAL_COLOR = "#22d3ee";

// Precomputed ONCE from the fetched artifact: the grid geometry (header) plus
// the four flat mean_u/mean_v/amp/phase arrays, kept addressable by
// row*nx+col so sampleInertialField can bilinearly interpolate the
// reconstructed velocity at an arbitrary particle position rather than
// snapping to cell centers. Row 0 is the northmost row (la1 = north edge),
// row-major from the NW corner, land = null — identical convention to the
// currents/speed artifacts.
function buildInertialField(inertialField) {
  const { header, mean_u, mean_v, amp, phase } = inertialField;
  return { layer: new InertialLayer(), grid: { header, mean_u, mean_v, amp, phase } };
}

// Bilinearly reconstruct the near-inertial (u, v), in m/s, at an arbitrary
// (lon, lat) and animation time dt (seconds). The MEAN current is deliberately
// excluded (see the layer header) so particles trace the pure inertial circle;
// only the rotary NI term amp·exp(i(phase−f·dt)) is reconstructed. Evaluates
// it at each of the 4 surrounding grid corners first, then bilinearly blends
// the resulting u/v — NOT phase interpolation, which would not commute with
// the cos/sin reconstruction. Returns null if any of the 4 corners is off-grid
// or land (null in the source arrays): the caller treats that as no-data and
// respawns the particle rather than advecting it across a coastline.
function sampleInertialField(grid, lon, lat, dt) {
  const { nx, ny, lo1, la1, dx, dy, omega } = grid.header;
  const colF = (lon - lo1) / dx;
  const rowF = (la1 - lat) / dy;
  const col0 = Math.floor(colF);
  const row0 = Math.floor(rowF);
  const col1 = col0 + 1;
  const row1 = row0 + 1;
  if (col0 < 0 || row0 < 0 || col1 >= nx || row1 >= ny) return null;
  const fx = colF - col0;
  const fy = rowF - row0;

  const corner = (row, col) => {
    const idx = row * nx + col;
    const a = grid.amp[idx];
    if (a == null) return null; // land / no-data
    const clat = la1 - row * dy;
    const f = 2 * omega * Math.sin((clat * Math.PI) / 180);
    const theta = grid.phase[idx] - f * dt;
    return {
      u: a * Math.cos(theta), // near-inertial only; mean excluded (see header)
      v: a * Math.sin(theta),
    };
  };

  const c00 = corner(row0, col0);
  const c01 = corner(row0, col1);
  const c10 = corner(row1, col0);
  const c11 = corner(row1, col1);
  if (!c00 || !c01 || !c10 || !c11) return null;

  const w00 = (1 - fx) * (1 - fy);
  const w01 = fx * (1 - fy);
  const w10 = (1 - fx) * fy;
  const w11 = fx * fy;
  return {
    u: c00.u * w00 + c01.u * w01 + c10.u * w10 + c11.u * w11,
    v: c00.v * w00 + c01.v * w01 + c10.v * w10 + c11.v * w11,
  };
}

// A plain canvas living in the "inertial" pane. Sized to the map viewport and
// repositioned on every move/zoom/viewreset/resize so its top-left cancels the
// pane's live drag transform: `containerPointToLayerPoint([0,0])` is exactly
// the negative of that transform (both public Leaflet APIs), so setting the
// canvas's own position to it keeps the canvas glued to the container's
// (0,0) regardless of an in-progress drag — which in turn keeps every frame's
// `map.latLngToContainerPoint(...)` draw call lined up with the canvas pixels
// it draws into. Default OFF (never `addTo(map)` here) — the layer-control row
// is its only way onto the map. Carries no animation state itself: the cell
// records (see buildInertialField) are kept in a plain array owned by
// startInertialClock, not attached to this — or any — Leaflet object.
class InertialLayer extends L.Layer {
  onAdd(map) {
    this._canvas = L.DomUtil.create("canvas", "inertial-canvas");
    // Decorative, non-interactive: let drags, clicks, and the grab cursor pass
    // straight through to the map and the markers above it (plan 014).
    this._canvas.style.pointerEvents = "none";
    map.getPane("inertial").appendChild(this._canvas);
    this._reset = this._reset.bind(this);
    map.on("move zoom viewreset resize", this._reset);
    this._reset();
    return this;
  }

  onRemove(map) {
    map.off("move zoom viewreset resize", this._reset);
    L.DomUtil.remove(this._canvas);
    this._canvas = null;
  }

  getContext() {
    return this._canvas ? this._canvas.getContext("2d") : null;
  }

  _reset() {
    if (!this._canvas || !this._map) return;
    const size = this._map.getSize();
    if (this._canvas.width !== size.x || this._canvas.height !== size.y) {
      this._canvas.width = size.x;
      this._canvas.height = size.y;
    }
    L.DomUtil.setPosition(this._canvas, this._map.containerPointToLayerPoint([0, 0]));
    // Trails accumulate in screen space (fading, not reprojected per pixel),
    // so a pan/zoom/resize would otherwise smear old trails across the new
    // view — the same failure mode the "Current flow" layer avoids by
    // clearing on interaction (see main()). Particles themselves live in
    // geographic coordinates and simply re-trail from the new view.
    const ctx = this.getContext();
    if (ctx) ctx.clearRect(0, 0, this._canvas.width, this._canvas.height);
  }
}

// The shared animation clock — modeled on the reverted startDriftDotClock
// (commit 60c82db): wall-clock phase via performance.now() (a dropped frame
// skips ahead instead of drifting), requestAnimationFrame gives a free
// document.hidden pause (rAF does not fire in hidden tabs), and the tick
// self-gates on map.hasLayer so the idle cost is a hash lookup. Started once
// in main(), never stopped; with no grid (missing artifact) it never starts.
//
// Particle state is a plain array owned by this closure, not attached to any
// Leaflet object: each particle is {lon, lat, age}. A particle respawns at a
// fresh random position inside the current VIEWPORT (age reset to 0) when it
// goes stale (age > INERTIAL_MAX_AGE), when its velocity sample is no-data
// (land/off-grid), or when advection carries it out of the viewport. Seeding
// and culling within the viewport (clamped to the field) — rather than across
// the whole field — is what scales the on-screen trace density with zoom: the
// fixed pool always populates what you're looking at, so it packs denser as
// you zoom in instead of thinning to a handful of particles. Initial ages are
// randomized so the whole pool doesn't restart in lockstep.
function startInertialClock(map, grid, layer) {
  if (!grid) return;
  const { lo1, lo2, la1, la2 } = grid.header;
  const fieldLonMin = Math.min(lo1, lo2);
  const fieldLonMax = Math.max(lo1, lo2);
  const fieldLatMin = Math.min(la1, la2);
  const fieldLatMax = Math.max(la1, la2);

  // The sampling/culling box for this frame: the map viewport intersected with
  // the field's coverage. `empty` when the viewport is entirely off the field
  // (panned away from the cruise bbox) — nothing to seed or draw.
  const viewBounds = () => {
    const b = map.getBounds();
    const lonMin = Math.max(fieldLonMin, b.getWest());
    const lonMax = Math.min(fieldLonMax, b.getEast());
    const latMin = Math.max(fieldLatMin, b.getSouth());
    const latMax = Math.min(fieldLatMax, b.getNorth());
    return { lonMin, lonMax, latMin, latMax, empty: lonMin >= lonMax || latMin >= latMax };
  };

  const randomPosition = (vb) => ({
    lon: vb.lonMin + Math.random() * (vb.lonMax - vb.lonMin),
    lat: vb.latMin + Math.random() * (vb.latMax - vb.latMin),
  });

  const particles = [];
  {
    const vb0 = viewBounds();
    const seed = vb0.empty
      ? { lonMin: fieldLonMin, lonMax: fieldLonMax, latMin: fieldLatMin, latMax: fieldLatMax }
      : vb0;
    for (let i = 0; i < INERTIAL_MAX_PARTICLES; i++) {
      particles.push({ ...randomPosition(seed), age: Math.floor(Math.random() * INERTIAL_MAX_AGE) });
    }
  }

  const tick = () => {
    if (!map.hasLayer(layer)) {
      requestAnimationFrame(tick);
      return;
    }
    const ctx = layer.getContext();
    if (!ctx) {
      requestAnimationFrame(tick);
      return;
    }
    const { width, height } = ctx.canvas;

    // Fade the previous frame instead of hard-clearing, so particles leave
    // fading tails: destination-out erases a fraction of the existing
    // pixels' alpha each frame rather than wiping the canvas outright.
    ctx.globalCompositeOperation = "destination-out";
    ctx.fillStyle = `rgba(0, 0, 0, ${INERTIAL_FADE_ALPHA})`;
    ctx.fillRect(0, 0, width, height);
    ctx.globalCompositeOperation = "source-over";

    const vb = viewBounds();
    if (vb.empty) {
      requestAnimationFrame(tick); // viewport is off the field — nothing to draw
      return;
    }

    const tau01 = ((performance.now() / 1000) % INERTIAL_LOOP_S) / INERTIAL_LOOP_S;
    const dt = tau01 * INERTIAL_SPAN_S;

    ctx.strokeStyle = INERTIAL_COLOR;
    ctx.lineWidth = INERTIAL_LINE_WIDTH;
    ctx.beginPath();
    for (const p of particles) {
      const sample = sampleInertialField(grid, p.lon, p.lat, dt);
      if (!sample) {
        Object.assign(p, randomPosition(vb), { age: 0 });
        continue; // no-data (land/off-grid): respawn, draw nothing this frame
      }
      const { u, v } = sample;
      const newLat = p.lat + v * INERTIAL_ADVECT_SCALE;
      const newLon = p.lon + (u * INERTIAL_ADVECT_SCALE) / Math.cos((p.lat * Math.PI) / 180);
      const stale =
        p.age + 1 > INERTIAL_MAX_AGE ||
        newLon < vb.lonMin ||
        newLon > vb.lonMax ||
        newLat < vb.latMin ||
        newLat > vb.latMax;
      if (stale) {
        Object.assign(p, randomPosition(vb), { age: 0 });
        continue; // aged out or left the viewport: respawn, draw nothing this frame
      }
      const p0 = map.latLngToContainerPoint([p.lat, p.lon]);
      const p1 = map.latLngToContainerPoint([newLat, newLon]);
      ctx.moveTo(p0.x, p0.y);
      ctx.lineTo(p1.x, p1.y);
      p.lon = newLon;
      p.lat = newLat;
      p.age += 1;
    }
    ctx.stroke();

    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}
// ---------------------------------------------------------------------------

// --- gliders ----------------------------------------------------------------
// The WHIRLS glider-group platforms (see docs/gliders.md): the XSPAR spar buoy,
// the seagliders, and the profiling floats, built server-side into
// gliders.geojson (a latest Point + a track LineString per platform). Coloured
// by `type` — the operational map's own amber (XSPAR) / blue (seaglider) /
// purple (float) — and drawn with a diamond marker so they read apart from the
// drifters' circles. Not batch-driven, so they ride the layer control, not the
// batch filter. Rows are keyed by `type`, so the two floats collapse into one
// "Floats" instrument row (like the two seagliders), each still selectable by id.
const GLIDER_STYLES = {
  xspar: { color: "#f59e0b", label: "XSPAR buoy" },
  seaglider: { color: "#38bdf8", label: "Seagliders" },
  float: { color: "#a855f7", label: "Floats" },
};
const gliderStyle = (type) =>
  GLIDER_STYLES[type] ?? { color: "#38bdf8", label: type ?? "Glider" };

// `state` ("normal" | "selected" | "dim") drives the click-to-highlight look: a
// selected glider gets a `-selected` class (CSS scales its diamond up); a dimmed
// one desaturates its fill, mirroring the drifter heads. Size is constant so the
// icon's anchor never shifts — the scale-up is CSS transform only.
function gliderIcon(type, state = "normal") {
  const base = gliderStyle(type).color;
  const color = state === "dim" ? desaturate(base) : base;
  const cls = state === "selected" ? "glider-marker glider-marker-selected" : "glider-marker";
  return L.divIcon({
    className: cls,
    html: `<span style="background:${color}"></span>`,
    iconSize: [16, 16],
    iconAnchor: [8, 8],
  });
}

// Popup for a glider fix. Gliders carry no reported velocity or battery, so —
// unlike the drifter popup — only the derived velocity is shown; `id`/`type`
// head it. Shared by the latest marker and each track dot.
function gliderPopupHtml(props, latlng) {
  const p = props || {};
  return `
    <div class="popup">
      <strong>${p.id ?? "—"}</strong> <span class="popup-label">${gliderStyle(p.type).label}</span><br/>
      <span class="popup-label">Last fix:</span> ${formatFixTime(p.date_UTC)}<br/>
      <span class="popup-label">Speed (derived):</span> ${fmtSpeedMps(p.derived_speed_mps)}<br/>
      <span class="popup-label">Heading (derived):</span> ${fmtDir(p.derived_heading_deg)}<br/>
      <span class="popup-label">Position:</span>
      ${latlng.lat.toFixed(5)}, ${latlng.lng.toFixed(5)}
    </div>`;
}

// Latest-position markers, one feature group per glider `type`, so each platform
// class is an instrument row in the batch control (see buildBatchControl) — the
// same shape as buildBatchGroups for drifters. Diamond marker so gliders read
// apart from the drifters' circles. Returns { type: featureGroup }.
function buildGliderMarkerGroups(geojson) {
  const groups = {};
  for (const feature of geojson.features ?? []) {
    if (feature.geometry?.type !== "Point") continue;
    const { id, type } = feature.properties ?? {};
    const [lng, lat] = feature.geometry.coordinates;
    const marker = L.marker([lat, lng], {
      icon: gliderIcon(type),
      zIndexOffset: 500,
    }).bindTooltip(gliderPopupHtml(feature.properties, { lat, lng }));
    if (id != null) {
      registerPart(id, (s) => marker.setIcon(gliderIcon(type, s)));
      marker.on("click", () => selectInstrument(id));
    }
    (groups[type] ??= L.featureGroup()).addLayer(marker);
  }
  return groups;
}

// Glider tracks, one feature group per `type`, keyed like buildGliderMarkerGroups
// so they ride the "True track" overlay against the matching instrument row. Per
// platform (from its track LineString): a line plus a tooltip-bearing dot per fix —
// mirroring buildTrackGroups, and (like it) registered for click-to-highlight
// under the platform `id`, so clicking a glider's line, a dot or its head selects
// it. Drawn in TRACK_COLOR, the single true-track colour shared with the drifters
// (the instrument identity stays on the coloured marker); this keeps every past
// track reading as one layer. A platform with a single deployed fix has no
// LineString and so no track group, only its marker. Returns
// { type: featureGroup }.
function buildGliderTrackGroups(geojson) {
  const groups = {};
  for (const feature of geojson.features ?? []) {
    if (feature.geometry?.type !== "LineString") continue;
    const { id, type, fixes } = feature.properties ?? {};
    const group = (groups[type] ??= L.featureGroup());
    const coords = feature.geometry.coordinates;
    const line = L.polyline(coords.map(([lng, lat]) => [lat, lng]), {
      color: TRACK_COLOR,
      weight: 2,
      opacity: 0.85,
      bubblingMouseEvents: false, // background clicks (not this) clear selection
    }).addTo(group);
    if (id != null) {
      registerPart(id, (s) => line.setStyle(lineStyle(s)));
      line.on("click", () => selectInstrument(id));
    }
    coords.forEach(([lng, lat], i) => {
      const dot = L.circleMarker([lat, lng], {
        radius: 3,
        color: TRACK_COLOR,
        weight: 1,
        fillColor: TRACK_COLOR,
        fillOpacity: 0.9,
        bubblingMouseEvents: false, // background clicks (not this) clear selection
      });
      dot.bindTooltip(gliderPopupHtml({ id, type, ...(fixes?.[i] ?? {}) }, dot.getLatLng()));
      if (id != null) {
        registerPart(id, (s) => dot.setStyle(dotStyle(s)));
        dot.on("click", () => selectInstrument(id));
      }
      group.addLayer(dot);
    });
  }
  return groups;
}
// ---------------------------------------------------------------------------

function renderAwaiting(ids) {
  const list = ids || [];
  const countEl = document.getElementById("awaiting-count");
  const listEl = document.getElementById("awaiting-list");
  countEl.textContent = `(${list.length})`;
  listEl.innerHTML = "";
  if (list.length === 0) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "None — all drifters have reported.";
    listEl.appendChild(li);
    return;
  }
  for (const id of list) {
    const li = document.createElement("li");
    li.textContent = id;
    listEl.appendChild(li);
  }
}

// The legend (colour bar + shared vmax) is constant across the slider; only the
// displayed *time* changes. Pass the selected `frame` ({offset_h, valid_time}) to
// show which forecast step is on the map; without it, the now frame.
function renderCurrentsInfo(meta, frame) {
  const timeEl = document.getElementById("currents-time");
  const legendEl = document.getElementById("speed-legend");
  if (!meta) {
    timeEl.textContent = "Surface currents unavailable.";
    legendEl.innerHTML = "";
    return;
  }
  const shown = frame ?? meta.frames?.find((f) => f.offset_h === (meta.now_offset_h ?? 0));
  const at = shown ? formatFixTime(shown.valid_time) : formatFixTime(meta.valid_time);
  const off = shown && shown.offset_h !== 0 ? ` (${frameOffsetLabel(shown.offset_h)})` : "";
  timeEl.textContent = `Showing ${at}${off} — CMEMS analysis/forecast.`;
  const gradient = meta.colorbar.join(", ");
  legendEl.innerHTML =
    `<div class="legend-bar" style="background:linear-gradient(to right, ${gradient})"></div>` +
    `<div class="legend-scale"><span>0</span>` +
    `<span>speed (${meta.units})</span>` +
    `<span>${meta.vmax.toFixed(2)}</span></div>`;
}

// Legend for the ζ/f overlay. A local twin of renderCurrentsInfo: the field is
// signed, so the scale is symmetric (vmin…0…vmax) over a diverging bar, not the
// 0→vmax speed ramp. vmin is negative; the sign convention (cyclonic +,
// anticyclonic −) is stated in the panel's static hint.
function renderVorticityInfo(meta) {
  const legendEl = document.getElementById("vorticity-legend");
  if (!legendEl) return;
  if (!meta) {
    legendEl.innerHTML = "";
    return;
  }
  const gradient = meta.colorbar.join(", ");
  legendEl.innerHTML =
    `<div class="legend-bar" style="background:linear-gradient(to right, ${gradient})"></div>` +
    `<div class="legend-scale"><span>${meta.vmin.toFixed(2)}</span>` +
    `<span>${meta.units}</span>` +
    `<span>+${meta.vmax.toFixed(2)}</span></div>`;
}

// Renderer for the combined forecast+hindcast sidebar panel. Both advect through
// the same time-dependent hourly field and share every caveat (spelled out in the
// panel's static note), so one status line covers them. valid_time (the t=0 the
// integration is anchored to) is baked into every feature,
// read off the first available (forecast, else hindcast). Three states: no
// artifact (CMEMS down / not built), built but empty (every instrument head sits
// in a coastal NaN cell — the pre-deployment cluster at port does this), and
// built with lines.
function renderDriftInfo(forecast, hindcast) {
  const timeEl = document.getElementById("drift-time");
  if (!timeEl) return;
  const fFeatures = forecast?.features;
  const hFeatures = hindcast?.features;
  if (!fFeatures && !hFeatures) {
    timeEl.textContent = "Drift forecast & hindcast unavailable.";
    return;
  }
  const valid =
    fFeatures?.[0]?.properties?.valid_time ?? hFeatures?.[0]?.properties?.valid_time;
  if (!valid) {
    timeEl.textContent =
      "No drift lines — every instrument head is on land or off-grid.";
    return;
  }
  timeEl.textContent = `Current-advection through the time-dependent field, anchored at ${formatFixTime(valid)}.`;
}

// --- ship tracks (R/V Marion Dufresne live + R/V S.A. Agulhas II baked) ------

function shipUrl(sinceISO) {
  const u = new URL(SHIP.positions);
  // Normalise startDate to a Z-offset ISO string; the API accepts either, but the
  // latest fix's own `date` comes back with a +0000 offset. endDate runs to the
  // end of the current UTC day (as the IPSL map does): a forward buffer so a
  // viewer clock running behind the server can't place the newest fix past the
  // window and stall the marker.
  const now = new Date();
  const endOfDay = new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), 23, 59, 59)
  );
  u.searchParams.set("startDate", new Date(sinceISO).toISOString());
  u.searchParams.set("endDate", endOfDay.toISOString());
  u.searchParams.set("cb", Date.now()); // cache-buster
  return u.toString();
}

// Fetch positions since `sinceISO` (inclusive). Best-effort: fetchJSON's
// `optional` swallows every failure to null, and a non-array body coerces to [],
// so an outage just stops the marker advancing and never throws.
async function fetchShip(sinceISO) {
  const data = await fetchJSON(shipUrl(sinceISO), { optional: true });
  return Array.isArray(data) ? data : [];
}

// A coloured disc with a white ring and a boat glyph — distinct from the small
// blue drifter circles, and per-vessel `bg` so the two ships read apart. Inline
// SVG (not an emoji) so it renders identically anywhere; the disc background is
// set inline on the inner span (overriding the .ship-disc CSS default).
function shipIcon(bg) {
  return L.divIcon({
    className: "ship-marker",
    html:
      `<span class="ship-disc" style="background:${bg}">` +
      '<svg viewBox="0 0 24 24" width="15" height="15" fill="#fff" aria-hidden="true">' +
      '<path d="M4 15h16l-2.2 5H6.2L4 15zm2-2V6.5L12 4l6 2.5V13H6z"/></svg>' +
      "</span>",
    iconSize: [26, 26],
    iconAnchor: [13, 13],
  });
}

// --- derived course/speed over ground --------------------------------------
// The API carries no reported SOG/COG (only lat/lon/date + met fields), so speed
// and heading are derived from the last track segment.

function haversineMeters(a, b) {
  const R = 6371000;
  const rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad;
  const dLon = (b.lon - a.lon) * rad;
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

function initialBearingDeg(a, b) {
  const rad = Math.PI / 180;
  const lat1 = a.lat * rad;
  const lat2 = b.lat * rad;
  const dLon = (b.lon - a.lon) * rad;
  const y = Math.sin(dLon) * Math.cos(lat2);
  const x =
    Math.cos(lat1) * Math.sin(lat2) -
    Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

// Speed/heading from fix `a` to fix `b`. Null if either is missing or the time
// gap is non-positive. Below ~0.5 kn (≈150 m over a 10-min step) the
// displacement is comparable to GPS scatter, so the bearing is noise — speed is
// still returned but heading is suppressed (ship moored/maneuvering). Computed
// per-segment so any fix on the track — not just the latest — can show its own
// derived motion.
const MIN_HEADING_KN = 0.5;
function motionBetween(a, b) {
  if (!a || !b) return null;
  const dt = (new Date(b.date).getTime() - new Date(a.date).getTime()) / 1000;
  if (!(dt > 0)) return null;
  const speedKn = (haversineMeters(a, b) / dt) * MS_TO_KN;
  return {
    speedKn,
    heading: speedKn >= MIN_HEADING_KN ? initialBearingDeg(a, b) : null,
  };
}

const fmtSpeed = (m) => (m ? speedBoth(m.speedKn / MS_TO_KN) : null);
const fmtHeading = (m) =>
  m && m.heading != null
    ? `${Math.round(m.heading) % 360}° ${compassPoint(m.heading)}`
    : null;
// ---------------------------------------------------------------------------

// A vessel's fix -> [label, value] rows (nulls dropped) is the one thing that
// differs between the two ships; each `VESSELS[*].rows` produces its own, and the
// popup and sidebar both render from it so a vessel's two readouts can't drift.

// Marion Dufresne rows: derived motion (the API reports none) + its met data.
function mdRows(p, motion) {
  const d = p.data || {};
  // Wind-speed unit is unspecified by the API (see docs/ship.md), so it is shown
  // without one; direction is degrees, temps °C, pressure hPa.
  const wind =
    d.truewindspeed != null
      ? `${d.truewindspeed}${d.truewinddir != null ? ` @ ${d.truewinddir}°` : ""}`
      : null;
  return [
    ["Last fix", formatFixTime(p.date)],
    ["Position", `${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}`],
    ["Speed (derived)", fmtSpeed(motion)],
    // Heading is always shown; "NA" stands in when it is unavailable (speed
    // below MIN_HEADING_KN, or no prior fix to derive a bearing from).
    ["Heading (derived)", fmtHeading(motion) ?? "NA"],
    ["Sea temp", d.seatemp != null ? `${d.seatemp} °C` : null],
    ["Air temp", d.airtemp != null ? `${d.airtemp} °C` : null],
    ["Pressure", d.pressure != null ? `${d.pressure} hPa` : null],
    ["Wind", wind],
  ].filter(([, v]) => v != null);
}

// Agulhas II rows: speed/course are *reported* by the source (so no derivation),
// plus its moving/stopped status and area; it carries no met data. Speed reads in
// both kn and m/s like the MD; course gets a compass point like a heading.
function agulhasRows(p) {
  const speed = p.speed_kn != null ? speedBoth(p.speed_kn / MS_TO_KN) : null;
  // Course is a *reported* direction, so it shares fmtDir with the drifters'
  // reported heading — same degrees+compass formatting and negative-degree guard.
  const course = p.course_deg != null ? fmtDir(p.course_deg) : null;
  return [
    ["Last fix", formatFixTime(p.date)],
    ["Position", `${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}`],
    ["Speed (reported)", speed],
    ["Course (reported)", course],
    ["Status", p.status],
    ["Area", p.area],
  ].filter(([, v]) => v != null);
}

// Popup for a `vessel`'s fix `p`; `prev` is the preceding fix (the MD derives its
// motion from that segment, the Agulhas ignores it). Shared by the latest-position
// marker and every track dot.
function shipPopupHtml(vessel, p, prev) {
  const rows = vessel
    .rows(p, prev)
    .map(([k, v]) => `<span class="popup-label">${k}:</span> ${v}<br/>`)
    .join("");
  return `<div class="popup"><strong>${vessel.name}</strong><br/>${rows}</div>`;
}

// A fix is usable only with finite coordinates and a timestamp. The API can emit
// a partial record, and an unguarded `p.lat.toFixed()` downstream would throw and
// (via main's catch) blank the map — so filter at ingestion and the render path
// only ever sees clean fixes.
const isValidFix = (p) =>
  p && Number.isFinite(p.lat) && Number.isFinite(p.lon) && !!p.date;
const byDate = (a, b) => new Date(a.date).getTime() - new Date(b.date).getTime();

// Cased polyline (white halo + coloured core, legible on any basemap), a dot at
// each fix, and a ship marker, in one feature group — for the given `vessel`
// spec (colours, name, and per-fix rows). Holds the time-sorted position list so
// live polling can append only the new tail: setPositions replaces the whole
// track; append extends it past the last fix, drawing only the fresh points.
function makeShipLayer(vessel) {
  // The cased track is non-interactive: it carries no popup, and an interactive
  // polyline would swallow clicks meant for the dots painted on top of it.
  const opts = (color, weight) => ({
    pane: "shipTrack",
    color,
    weight,
    opacity: 0.95,
    interactive: false,
  });
  const halo = L.polyline([], opts(vessel.haloColor, 5));
  const core = L.polyline([], opts(vessel.trackColor, 2.5));
  // Per-fix dots use the pane's default SVG renderer, not a canvas: a canvas
  // renderer spans the whole viewport and would intercept every click across the
  // map. SVG keeps each dot an individually hit-testable element, and empty pane
  // area stays click-through (so the drifters above this pane stay clickable).
  // Added after the lines, so the dots paint on top of the cased track.
  const dots = L.layerGroup();
  const marker = L.marker([0, 0], {
    pane: "ship",
    icon: shipIcon(vessel.markerColor),
    opacity: 0, // hidden until the first fix lands
  }).bindTooltip("");
  const group = L.featureGroup([halo, core, dots, marker]);
  let positions = [];

  // A small dot at fix `p`, sharing the latest-position tooltip but filled with
  // this fix's own data and its motion relative to the preceding fix `prev`.
  function dotFor(p, prev) {
    const dot = L.circleMarker([p.lat, p.lon], {
      pane: "shipTrack",
      radius: 2.5,
      color: vessel.haloColor,
      weight: 1,
      fillColor: vessel.trackColor,
      fillOpacity: 1,
    });
    dot.bindTooltip(shipPopupHtml(vessel, p, prev));
    return dot;
  }

  function showLatest() {
    const last = positions[positions.length - 1];
    if (!last) return;
    const prev = positions[positions.length - 2];
    marker.setLatLng([last.lat, last.lon]).setOpacity(1);
    marker.setTooltipContent(shipPopupHtml(vessel, last, prev));
    renderShipInfo(vessel, last, prev);
  }

  function setPositions(next) {
    positions = next.filter(isValidFix).sort(byDate);
    const latlngs = positions.map((p) => [p.lat, p.lon]);
    halo.setLatLngs(latlngs);
    core.setLatLngs(latlngs);
    dots.clearLayers();
    positions.forEach((p, i) => dots.addLayer(dotFor(p, positions[i - 1])));
    showLatest();
  }

  function append(newer) {
    const valid = newer.filter(isValidFix).sort(byDate);
    if (!positions.length) return setPositions(valid);
    const lastT = new Date(positions[positions.length - 1].date).getTime();
    const fresh = valid.filter((p) => new Date(p.date).getTime() > lastT);
    if (!fresh.length) return;
    for (const p of fresh) {
      const prev = positions[positions.length - 1];
      positions.push(p);
      halo.addLatLng([p.lat, p.lon]); // extend in place, no full-track rebuild
      core.addLatLng([p.lat, p.lon]);
      dots.addLayer(dotFor(p, prev)); // one fresh dot, no full-track rebuild
    }
    showLatest();
  }

  return {
    group,
    append,
    lastDate: () => positions[positions.length - 1]?.date,
  };
}

function renderShipInfo(vessel, p, prev) {
  const timeEl = document.getElementById(vessel.panel.time);
  const readEl = document.getElementById(vessel.panel.readout);
  if (!timeEl) return;
  if (!p) {
    timeEl.textContent = `${vessel.name} position unavailable.`;
    if (readEl) readEl.innerHTML = "";
    return;
  }
  timeEl.textContent = `Last fix ${formatFixTime(p.date)} — ${vessel.source}.`;
  // "Last fix" already shows in the hint line above, so drop it from the rows.
  if (readEl)
    readEl.innerHTML = vessel
      .rows(p, prev)
      .filter(([k]) => k !== "Last fix")
      .map(
        ([k, v]) =>
          `<div class="ship-row"><span class="popup-label">${k}</span><span>${v}</span></div>`
      )
      .join("");
}

function osmLayer() {
  return L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
  });
}

async function main() {
  // Data-freshness panel: start the live clock immediately, and fill in the
  // build time out of band so a slow/missing build.json can't hold up the map.
  startClock();
  fetchJSON(DATA.build, { optional: true }).then(renderBuildTime);

  const map = L.map("map", {
    center: FALLBACK_CENTER,
    zoom: FALLBACK_ZOOM,
    layers: [osmLayer()],
  });

  const currentOverlays = {};

  // Layer stack, bottom -> top: speed shading -> near-inertial animation ->
  // flow -> ship track+dots -> drifters -> ship marker. The ship track sits
  // *below* the drifters so its per-fix dots can't intercept clicks meant for
  // the drifter markers where the two overlap — the cruise starts from the
  // drifters' staging port, so the early ship track runs right through the
  // pre-deploy cluster. The ship's current position marker stays on top.
  map.createPane("shading").style.zIndex = 350;
  map.createPane("inertial").style.zIndex = 360;
  map.createPane("shipTrack").style.zIndex = 640;
  map.createPane("drifters").style.zIndex = 650;
  map.createPane("ship").style.zIndex = 660;
  // PoC interactive-forecast geometry, above the instruments so a placed forecast
  // is never occluded, below the tooltip pane (680) so its marks label. Split into
  // three stacked panes so the layering is by-element, not by draw order: tracks
  // (ship route + drift lines) lowest, deployment drop discs above them, and the
  // +Δt mark dots on top — so a drop never hides a delayed dot and a dot never
  // hides under a line.
  map.createPane("deployTracks").style.zIndex = 663;
  map.createPane("deployDrops").style.zIndex = 664;
  map.createPane("deployDots").style.zIndex = 665;

  // Hover tooltips must float above every marker. Leaflet's default tooltipPane is
  // z-index 650 — tied with the drifters pane and *below* the ship pane (660) — so
  // heads would otherwise paint over the tooltip. Lift it above both (still below
  // the 700 popupPane) so a fix's tooltip is never occluded by a marker.
  map.getPane("tooltipPane").style.zIndex = 680;

  // PoC interactive deployment planner: its own layer + a top-right "Deploy"
  // toggle (see buildDeployTool). Background clicks/moves are routed to it below
  // when armed. `displayedFieldTime` is the valid time of the CMEMS snapshot shown
  // on the map (set once the currents meta loads, below); it is the run start, so a
  // placed deployment's drift begins at the same instant as the field.
  const deployLayer = L.featureGroup().addTo(map);
  const deployTool = buildDeployTool(deployLayer);
  deployTool.control.addTo(map);
  let displayedFieldTime = null;

  // Background map clicks: in Deploy mode a click adds a vertex to the path and a
  // double-click finishes it (committing the deployment, its drift locked to
  // displayedFieldTime); otherwise a click clears any track selection or synced-dot
  // highlight. Track elements and deploy dots set bubblingMouseEvents:false, so their
  // clicks don't reach here — only genuine background clicks do.
  map.on("click", (e) => {
    if (deployTool.state.on) {
      deployTool.handleClick(e.latlng);
      return;
    }
    if (selectedInstrument != null) {
      selectedInstrument = null;
      applySelection();
    }
    if (selectedDeployDot != null) {
      selectedDeployDot = null;
      applyDeployDotSelection();
    }
    if (selectedDropSet != null) {
      selectedDropSet = null;
      applyDropSetSelection();
    }
    if (selectedTrack != null) {
      selectedTrack = null;
      applyTrackSelection();
    }
  });
  map.on("dblclick", (e) => {
    if (deployTool.state.on) deployTool.handleDblClick(e.latlng, displayedFieldTime);
  });

  // Right-click aborts an in-progress deploy path (and suppresses the browser context
  // menu while the tool is armed, so a cancelling right-click doesn't also pop it).
  map.on("contextmenu", (e) => {
    if (!deployTool.state.on) return; // tool off: leave the normal browser menu alone
    L.DomEvent.preventDefault(e.originalEvent);
    deployTool.handleAbort();
  });

  // Escape aborts an in-progress deploy path too — same as a right-click, from the
  // keyboard. (Bound on the document since the map container isn't reliably focused.)
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") deployTool.handleAbort();
  });

  // Live placement preview: while Deploy mode is armed and a path is being drawn,
  // redraw the polyline + the equally-spaced drops it implies (rubber-banding to the
  // cursor). Cheap client geometry, no fetch on move (see drawDeployPreview).
  map.on("mousemove", (e) => {
    if (deployTool.state.on) deployTool.handleMove(e.latlng);
  });

  // Latest positions (required). Group by batch first to drive the fit; the
  // groups are added near the end so the markers sit above the shading and flow
  // layers (the drifters pane also enforces this via z-index).
  const latest = await fetchJSON(DATA.latest);
  const batchGroups = buildBatchGroups(latest);

  const bounds = L.featureGroup(Object.values(batchGroups)).getBounds();
  if (bounds.isValid()) {
    // Cap zoom well out: the drifters are a tight pre-deployment cluster, so a
    // tight fit hides the surrounding currents. Opens on the Cape Basin.
    map.fitBounds(bounds, { padding: [40, 40], maxZoom: 9 });
  }

  // Surface currents, from one CMEMS forecast window: speed + ζ/f shadings — one
  // lossless-WebP frame per 12 h slider offset (-12 … +72 h) sharing one colour
  // scale — plus the flow trails (the now slice; a one-instant texture the slider
  // does not scrub).
  const meta = await fetchJSON(DATA.meta, { optional: true });
  // Lock the interactive forecast's start time to the displayed field's instant
  // (the now frame at load; the slider updates it as the displayed field changes).
  displayedFieldTime = meta?.valid_time ?? null;
  const currents = await fetchJSON(DATA.currents, { optional: true });
  const vorticityMeta = await fetchJSON(DATA.vorticityMeta, { optional: true });
  const inertialField = await fetchJSON(DATA.inertialField, { optional: true });

  // Resolve a frame file under the data dir, and the index of a meta's `now` frame.
  const frameUrl = (file) => DATA.dataBase + file;
  const nowIndex = (m) =>
    Math.max(0, (m?.frames ?? []).findIndex((f) => f.offset_h === (m.now_offset_h ?? 0)));

  // The two shadings (speed, ζ/f) both fill the same `shading` pane, so only one
  // makes sense at a time — they are mutually exclusive **base layers** (radio
  // buttons) in the Currents control, not overlays. Fully opaque: the raster is
  // the ocean's true colour, not a wash over the basemap (land stays transparent
  // via the PNG's own alpha mask, so the coastline still shows through).
  const currentShading = {};
  // Shading overlays the time slider re-points frame-by-frame: {layer, frames}.
  const shadingLayers = [];

  // Speed shading: a lossless-WebP Mercator raster in the bottom data pane, shown
  // by default, initialised at the `now` frame. The image is at the native CMEMS
  // grid resolution (one pixel per cell); `crisp` disables the browser's default
  // bilinear upscaling so the cells render as sharp pixels instead of a smooth blur.
  if (meta && meta.bounds && meta.frames?.length) {
    const speedLayer = L.imageOverlay(frameUrl(meta.frames[nowIndex(meta)].file), meta.bounds, {
      pane: "shading",
      className: "crisp-raster",
    });
    speedLayer.addTo(map); // the default-selected shading radio
    currentShading["Current speed"] = speedLayer;
    shadingLayers.push({ layer: speedLayer, frames: meta.frames });
  }
  renderCurrentsInfo(meta);

  // Vorticity ζ/f: the alternative shading in the same pane, off by default (its
  // radio unselected until picked, which swaps it in for the speed raster).
  if (vorticityMeta && vorticityMeta.bounds && vorticityMeta.frames?.length) {
    const vorticityLayer = L.imageOverlay(
      frameUrl(vorticityMeta.frames[nowIndex(vorticityMeta)].file),
      vorticityMeta.bounds,
      { pane: "shading", className: "crisp-raster" }
    );
    currentShading["Vorticity ζ/f"] = vorticityLayer;
    shadingLayers.push({ layer: vorticityLayer, frames: vorticityMeta.frames });
  }
  renderVorticityInfo(vorticityMeta);

  // Time slider: scrub both shadings through the forecast together. It drives the
  // speed frames' offsets/times (vorticity shares the same offsets); moving it
  // re-points every registered shading overlay, updates the sidebar displayed-time
  // line, and re-locks the deploy tool's start to the displayed field. Only built
  // when there is more than one frame to move between.
  if (meta?.frames?.length > 1) {
    const frames = meta.frames;
    let vortPrefetched = false;
    const prefetch = (fs) => fs.forEach((f) => { new Image().src = frameUrl(f.file); });
    buildTimeSlider(map, frames, nowIndex(meta), (i) => {
      for (const s of shadingLayers) {
        const file = s.frames[i]?.file;
        if (file) s.layer.setUrl(frameUrl(file));
      }
      const f = frames[i];
      displayedFieldTime = f.valid_time;
      renderCurrentsInfo(meta, f);
    });
    // Prefetch the speed frames once the map is idle (the now frame is already
    // loaded); prefetch the ζ/f frames only once vorticity is first selected, so an
    // untouched layer costs no bytes. Both keep the slider smooth without inflating
    // the critical-path load.
    (window.requestIdleCallback || ((cb) => setTimeout(cb, 1500)))(() => prefetch(frames));
    map.on("baselayerchange", (e) => {
      if (!vortPrefetched && e.name === "Vorticity ζ/f" && vorticityMeta?.frames) {
        vortPrefetched = true;
        prefetch(vorticityMeta.frames);
      }
    });
  }

  // Flow trails: dark->white ramp keyed to speed, so the bright jet pops over the
  // shading. The magnitude is sqrt-compressed server-side so slow eddies animate,
  // which means the readout would show scaled m/s — so displayValues is off and
  // true speed is read from the shading legend instead.
  if (currents && currents.length && typeof L.velocityLayer === "function") {
    const flowLayer = L.velocityLayer({
      displayValues: false,
      data: currents,
      colorScale: FLOW_COLORS,
      maxVelocity: meta?.vmax ?? 1.5,
      velocityScale: 0.1,
      lineWidth: 1.2,
    });
    flowLayer.addTo(map);
    currentOverlays["Current flow"] = flowLayer;

    // leaflet-velocity paints faded trails in screen space and does not
    // reproject the existing frame on pan/zoom, so old trails ghost in place.
    // Clear its canvas when interaction starts, and re-seed the field for the
    // new view when it ends, so the flow tracks the map.
    map.on("movestart zoomstart", () => {
      const cv = flowLayer._canvasLayer?._canvas;
      if (cv) cv.getContext("2d").clearRect(0, 0, cv.width, cv.height);
    });
    map.on("moveend zoomend", () => flowLayer.setData(currents));
  }

  // Near-inertial animation: flowing particle tracks reconstructed client-side
  // from inertial_field.json (see the "near-inertial animation" block above).
  // Default OFF (buildInertialField never addTo(map)s it) — missing artifact
  // means no layer and no control row. The clock starts once, immediately;
  // it self-gates on map.hasLayer so it costs nothing while the layer is off.
  if (inertialField) {
    const { layer: inertialLayer, grid: inertialGrid } = buildInertialField(inertialField);
    currentOverlays["Near-inertial animation"] = inertialLayer;
    startInertialClock(map, inertialGrid, inertialLayer);
  }

  // Trajectories and the current-advection forecast/hindcast, each grouped by
  // batch (off by default; optional so a missing file can't blank the map). Not
  // layer-control overlays: the batch control governs them, so unchecking a batch
  // hides its track, forecast and hindcast along with its markers.
  const tracks = await fetchJSON(DATA.tracks, { optional: true });
  const trackGroups = tracks ? buildTrackGroups(tracks) : {};
  const forecast = await fetchJSON(DATA.forecast, { optional: true });
  const forecastGroups = forecast ? buildAdvectionGroups(forecast, FORECAST_COLOR) : {};
  const hindcast = await fetchJSON(DATA.hindcast, { optional: true });
  const hindcastGroups = hindcast ? buildAdvectionGroups(hindcast, HINDCAST_COLOR) : {};
  renderDriftInfo(forecast, hindcast);

  // Glider-group platforms (XSPAR buoy + seagliders + floats) are instruments in
  // the same control as the drifter batches: their latest markers join the
  // instrument rows, their tracks the "True track" overlay, and their
  // current-advection lines the Forecast/Hindcast overlays (keyed by `type`, from
  // forecast.geojson's per-instrument heads). Optional so a missing file can't
  // blank the map.
  const gliders = await fetchJSON(DATA.gliders, { optional: true });
  const gliderMarkerGroups = gliders ? buildGliderMarkerGroups(gliders) : {};
  const gliderTrackGroups = gliders ? buildGliderTrackGroups(gliders) : {};

  // One instrument control governs drifter batches *and* gliders. Marker rows =
  // drifter batches + glider platforms; the True-track overlay carries both the
  // drifter trajectories and the glider tracks, so its master toggle acts on
  // every instrument at once.
  const markerGroups = { ...batchGroups, ...gliderMarkerGroups };

  // Markers last, so they sit on top of the shading and flow layers. Added
  // directly; the instrument control (not the layer control) governs their
  // visibility — and the tracks', forecast's, and hindcast's. (sync() in the
  // control reconciles the initial checkbox state, e.g. hiding pre-deploy.)
  for (const group of Object.values(markerGroups)) {
    group.addTo(map);
  }
  buildBatchControl(map, markerGroups, [
    {
      label: "True track",
      groups: { ...trackGroups, ...gliderTrackGroups },
      on: false,
      color: TRACK_COLOR,
    },
    { label: "Forecast (1/3/6 h)", groups: forecastGroups, on: false, color: FORECAST_COLOR },
    { label: "Hindcast (1/3/6 h)", groups: hindcastGroups, on: false, color: HINDCAST_COLOR },
  ]).addTo(map);

  // Awaiting-first-fix sidebar.
  renderAwaiting(await fetchJSON(DATA.awaiting, { optional: true }));

  // Map layers are split into two titled controls that sit below the custom
  // "Instruments" control, so the three read as one set: **Currents** and
  // **Ships** (the vessels). OpenStreetMap is the sole basemap — there is no
  // basemap radio.
  //
  // In Currents the two shadings are mutually-exclusive **base layers** (radios)
  // plus a "None" radio to turn shading off; the flow trails and near-inertial
  // animation, which can coexist with either shading, are **overlays**
  // (checkboxes). The control is built only when it has something to show (CMEMS
  // up), and Ships is created lazily on the first vessel fix (below), so neither
  // ever shows a dead-empty box.
  if (Object.keys(currentShading).length || Object.keys(currentOverlays).length) {
    if (Object.keys(currentShading).length) currentShading["None"] = L.layerGroup();
    titledLayerControl(map, currentShading, currentOverlays, "Currents");
  }
  let shipsControl = null;
  const ensureShipsControl = () =>
    (shipsControl ??= titledLayerControl(map, null, {}, "Ships"));

  // R/V Marion Dufresne live track (client-side; Flotte Océanographique Française
  // API). Last, and deliberately not awaited: it is the one third-party fetch, so
  // blocking on it would stall the same-origin layers and controls above behind a
  // slow host. Each poll requests only the window since the last fix and appends.
  // The overlay is added to the layer control on the first fix (not before, so an
  // empty/failed start never shows a dead toggle) and the marker reveals then; the
  // interval keeps trying, so a later poll revives the layer once the API recovers.
  // Polls are skipped while the tab is hidden — and resumed on return — to avoid
  // hammering a third-party host in the background.
  const ship = makeShipLayer(VESSELS.md);
  let shipShown = false;
  async function pollShip() {
    if (document.hidden) return;
    ship.append(await fetchShip(ship.lastDate() ?? SHIP.cruiseStart));
    if (!ship.lastDate()) {
      renderShipInfo(VESSELS.md, null);
    } else if (!shipShown) {
      ship.group.addTo(map);
      ensureShipsControl().addOverlay(ship.group, VESSELS.md.name);
      shipShown = true;
    }
  }
  pollShip();
  setInterval(pollShip, SHIP.refreshMs);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) pollShip();
  });

  // R/V S.A. Agulhas II baked track (same-origin agulhas.json; see docs/ship.md).
  // Unlike the MD it is a build artifact — its THREDDS CSV source is not
  // CORS-open, so it can't be polled live in the browser — but it shares the ship
  // renderer and the same "no fix ⇒ no dead toggle" contract. Re-fetched on the
  // MD's cadence so a rebuild's new fixes appear without a page reload; append
  // (the whole file each time, seeding on the first and adding only fresh fixes
  // after) keeps that flat as the track grows over the cruise, as it does for MD.
  const agulhas = makeShipLayer(VESSELS.agulhas);
  let agulhasShown = false;
  async function loadAgulhas() {
    if (document.hidden) return;
    const fixes = await fetchJSON(DATA.agulhas, { optional: true });
    if (!Array.isArray(fixes) || !fixes.length) {
      if (!agulhasShown) renderShipInfo(VESSELS.agulhas, null);
      return;
    }
    agulhas.append(fixes);
    if (!agulhasShown) {
      agulhas.group.addTo(map);
      ensureShipsControl().addOverlay(agulhas.group, VESSELS.agulhas.name);
      agulhasShown = true;
    }
  }
  loadAgulhas();
  setInterval(loadAgulhas, SHIP.refreshMs);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) loadAgulhas();
  });
}

main().catch((err) => {
  console.error("Failed to initialise map:", err);
  const el = document.getElementById("map");
  if (el) {
    el.innerHTML =
      '<div class="map-error">Could not load map data. See console for details.</div>';
  }
});
