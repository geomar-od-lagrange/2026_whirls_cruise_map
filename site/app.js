/* 2026 Whirls Cruise — drifter map.
 *
 * Static client. Fetches the build artifacts from ./data/ and renders them as
 * Leaflet layers:
 *   latest.geojson                 -> circle markers (on by default)
 *   tracks.geojson                 -> trajectory lines (off by default)
 *   forecast.geojson               -> per-drifter current-advection track (off)
 *   hindcast.geojson               -> per-drifter current-advection back-track (off)
 *   speed.png + currents_meta.json -> surface-speed shading (imageOverlay)
 *   currents.json                  -> leaflet-velocity flow trails (optional)
 *   inertial_field.json            -> animated near-inertial vector field (off)
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
  meta: "./data/currents_meta.json",
  speed: "./data/speed.png",
  inertialField: "./data/inertial_field.json",
  build: "./data/build.json",
  gliders: "./data/gliders.geojson",
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
  trackColor: "#1a1a1a",
  haloColor: "#ffffff",
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
};
// Instrument rows share this control: drifter batches use BATCH_LABELS; glider
// types (xspar/seaglider) fall back to their GLIDER_STYLES label so they read as
// "XSPAR buoy" / "Seagliders" in the same compartment.
const batchLabel = (batch) =>
  BATCH_LABELS[batch] ?? GLIDER_STYLES[batch]?.label ?? batch;
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
// popups and the ship readout.
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

// Group latest-position markers into one feature group per `batch` value, so the
// batch filter control can toggle each independently. Returns { batch: group }.
function buildBatchGroups(geojson) {
  const groups = {};
  for (const feature of geojson.features ?? []) {
    if (feature.geometry?.type !== "Point") continue;
    const [lng, lat] = feature.geometry.coordinates;
    const batch = feature.properties?.batch ?? "unknown";
    const marker = L.circleMarker([lat, lng], {
      ...styleForBatch(batch),
      pane: "drifters",
    });
    marker.bindPopup(popupHtml(feature.properties, marker.getLatLng()));
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
    const div = L.DomUtil.create("div", "batch-control");
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

    for (const batch of Object.keys(markerGroups).sort()) {
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

// Trajectories, grouped by `batch` so each batch's lines+dots toggle with that
// batch's markers (see buildBatchControl). For each drifter: one line, plus a
// small dot at every fix. Each dot carries the same popup as the drifter's main
// marker, but filled with *that fix's* own time, battery, and reported/derived
// velocity — read from the per-vertex `fixes` array that rides parallel to
// `coordinates`. Tolerates a
// `fixes`-less artifact from an older build: the dot then falls back to the
// line-level identity (D_number/batch) with an unknown time. The line is
// non-interactive so it never swallows a click meant for a dot or a marker
// below it. Returns { batch: featureGroup }.
function buildTrackGroups(geojson) {
  const groups = {};
  for (const feature of geojson.features ?? []) {
    if (feature.geometry?.type !== "LineString") continue;
    const { D_number, batch, fixes } = feature.properties ?? {};
    const key = batch ?? "unknown";
    const group = (groups[key] ??= L.featureGroup());
    const coords = feature.geometry.coordinates;
    L.polyline(
      coords.map(([lng, lat]) => [lat, lng]),
      { color: TRACK_COLOR, weight: 2, opacity: 0.8, interactive: false }
    ).addTo(group);
    coords.forEach(([lng, lat], i) => {
      const fix = fixes?.[i] ?? {};
      const dot = L.circleMarker([lat, lng], {
        radius: 3,
        color: TRACK_COLOR,
        weight: 1,
        fillColor: TRACK_COLOR,
        fillOpacity: 0.9,
      });
      // The fix record already carries date/battery/reported+derived velocity;
      // add the line-level identity for the same popup as the main marker.
      dot.bindPopup(popupHtml({ D_number, batch, ...fix }, dot.getLatLng()));
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

// --- near-inertial animation -------------------------------------------------
// Animates the near-inertial (NI) rotary current the CMEMS field carries,
// reconstructed analytically on the client from a per-cell (mean, amplitude,
// phase) decomposition shipped in inertial_field.json (see
// plans/014-near-inertial-animation.md):
//   u(t) + i·v(t) = (mean_u + i·mean_v) + amp · exp(i·(phase − f·dt))
//   f = 2·Ω·sin(lat)     (Ω = omega, from the header; f < 0 in the SH)
// A single wall clock sweeps dt over [0, 24h) every INERTIAL_LOOP_S seconds; a
// canvas in the "inertial" pane redraws every animation frame. This is *not*
// the previously-dropped animated drift dot (a marker walking the
// forecast/hindcast polylines, removed in e9b339c) — this animates the field.

// The mean current dominates the NI amplitude ~10-20x here, so mean+NI would
// read as near-static vectors with an imperceptible wobble. NI-only (mean
// subtracted) is what makes the rotation legible, hence the default false;
// flipping this to true is the only change needed to show mean+NI (both
// fields ship regardless, so no rebuild is needed to try it).
const SHOW_MEAN = false;

const INERTIAL_LOOP_S = 12; // wall-clock seconds per animation loop
const INERTIAL_SPAN_S = 24 * 3600; // dt sweeps [0, 24h) per loop
// Glyph length per unit speed, clamped so a fast cell doesn't dominate the
// canvas — cosmetic only, the underlying amp/phase reconstruction is
// unscaled true m/s (see plan 014, "no gamma" / plan 013's no-gain finding).
const INERTIAL_PX_PER_MPS = 30;
const INERTIAL_MAX_PX = 22;
// Cyan, distinct from the orange true track, the violet forecast / magenta
// hindcast advection lines, and the dark->white flow-trail ramp.
const INERTIAL_COLOR = "#22d3ee";

// Precomputed ONCE from the header geometry: a flat array of plain records
// {lat, lon, mean_u, mean_v, amp, phase, f}, skipping null (land) cells. `f`
// keeps its southern-hemisphere sign so the rotation direction is physical.
// Row 0 is the northmost row (la1 = north edge), row-major from the NW
// corner — identical convention to the currents/speed artifacts.
function buildInertialField(inertialField) {
  const { header, mean_u, mean_v, amp, phase } = inertialField;
  const { nx, ny, lo1, la1, dx, dy, omega } = header;
  const cells = [];
  for (let row = 0; row < ny; row++) {
    const lat = la1 - row * dy;
    const f = 2 * omega * Math.sin((lat * Math.PI) / 180);
    for (let col = 0; col < nx; col++) {
      const idx = row * nx + col;
      const a = amp[idx];
      if (a == null) continue; // land
      cells.push({
        lat,
        lon: lo1 + col * dx,
        mean_u: mean_u[idx] ?? 0,
        mean_v: mean_v[idx] ?? 0,
        amp: a,
        phase: phase[idx] ?? 0,
        f,
      });
    }
  }
  return { layer: new InertialLayer(), cells };
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
  }
}

// The shared animation clock — modeled on the reverted startDriftDotClock
// (commit 60c82db): wall-clock phase via performance.now() (a dropped frame
// skips ahead instead of drifting), requestAnimationFrame gives a free
// document.hidden pause (rAF does not fire in hidden tabs), and the tick
// self-gates on map.hasLayer so the idle cost is a hash lookup. Started once
// in main(), never stopped; with no cells (missing artifact) it never starts.
function startInertialClock(map, cells, layer) {
  if (!cells.length) return;
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
    ctx.clearRect(0, 0, width, height);

    const tau01 = ((performance.now() / 1000) % INERTIAL_LOOP_S) / INERTIAL_LOOP_S;
    const dt = tau01 * INERTIAL_SPAN_S;

    ctx.strokeStyle = INERTIAL_COLOR;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    for (const cell of cells) {
      const theta = cell.phase - cell.f * dt;
      let u = cell.amp * Math.cos(theta);
      let v = cell.amp * Math.sin(theta);
      if (SHOW_MEAN) {
        u += cell.mean_u;
        v += cell.mean_v;
      }
      const speed = Math.hypot(u, v);
      const len = Math.min(speed * INERTIAL_PX_PER_MPS, INERTIAL_MAX_PX);
      if (len < 1) continue; // too short to read — also guards the /speed below
      const p = map.latLngToContainerPoint([cell.lat, cell.lon]);
      // Screen space: east = +x (matches u); north = -y (screen y grows down).
      const x2 = p.x + (u / speed) * len;
      const y2 = p.y - (v / speed) * len;
      ctx.moveTo(p.x, p.y);
      ctx.lineTo(x2, y2);
    }
    ctx.stroke();

    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}
// ---------------------------------------------------------------------------

// --- gliders ----------------------------------------------------------------
// The WHIRLS glider platforms (see docs/gliders.md): the XSPAR spar buoy and the
// seagliders, built server-side into gliders.geojson (a latest Point + a track
// LineString per platform). Coloured by `type` — the operational map's own
// amber (XSPAR) / blue (seaglider) — and drawn with a diamond marker so they
// read apart from the drifters' circles. Not batch-driven (gliders aren't
// deployment batches), so they ride the layer control, not the batch filter.
const GLIDER_STYLES = {
  xspar: { color: "#f59e0b", label: "XSPAR buoy" },
  seaglider: { color: "#38bdf8", label: "Seagliders" },
};
const gliderStyle = (type) =>
  GLIDER_STYLES[type] ?? { color: "#38bdf8", label: type ?? "Glider" };

function gliderIcon(type) {
  return L.divIcon({
    className: "glider-marker",
    html: `<span style="background:${gliderStyle(type).color}"></span>`,
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
    const { type } = feature.properties ?? {};
    const [lng, lat] = feature.geometry.coordinates;
    const marker = L.marker([lat, lng], {
      icon: gliderIcon(type),
      zIndexOffset: 500,
    }).bindPopup(gliderPopupHtml(feature.properties, { lat, lng }));
    (groups[type] ??= L.featureGroup()).addLayer(marker);
  }
  return groups;
}

// Glider tracks, one feature group per `type`, keyed like buildGliderMarkerGroups
// so they ride the "True track" overlay against the matching instrument row. Per
// platform (from its track LineString): a non-interactive line plus a
// popup-bearing dot per fix — mirroring buildTrackGroups. Drawn in TRACK_COLOR,
// the single true-track colour shared with the drifters (the instrument identity
// stays on the coloured marker); this keeps every past track reading as one
// layer. A single-fix platform (e.g. the XSPAR with one report) has no LineString
// and so no track group, only its marker. Returns { type: featureGroup }.
function buildGliderTrackGroups(geojson) {
  const groups = {};
  for (const feature of geojson.features ?? []) {
    if (feature.geometry?.type !== "LineString") continue;
    const { id, type, fixes } = feature.properties ?? {};
    const group = (groups[type] ??= L.featureGroup());
    const coords = feature.geometry.coordinates;
    L.polyline(
      coords.map(([lng, lat]) => [lat, lng]),
      { color: TRACK_COLOR, weight: 2, opacity: 0.85, interactive: false }
    ).addTo(group);
    coords.forEach(([lng, lat], i) => {
      const dot = L.circleMarker([lat, lng], {
        radius: 3,
        color: TRACK_COLOR,
        weight: 1,
        fillColor: TRACK_COLOR,
        fillOpacity: 0.9,
      });
      dot.bindPopup(gliderPopupHtml({ id, type, ...(fixes?.[i] ?? {}) }, dot.getLatLng()));
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

function renderCurrentsInfo(meta) {
  const timeEl = document.getElementById("currents-time");
  const legendEl = document.getElementById("speed-legend");
  if (!meta) {
    timeEl.textContent = "Surface currents unavailable.";
    legendEl.innerHTML = "";
    return;
  }
  timeEl.textContent = `Valid ${formatFixTime(meta.valid_time)} — CMEMS analysis/forecast.`;
  const gradient = meta.colorbar.join(", ");
  legendEl.innerHTML =
    `<div class="legend-bar" style="background:linear-gradient(to right, ${gradient})"></div>` +
    `<div class="legend-scale"><span>0</span>` +
    `<span>speed (${meta.units})</span>` +
    `<span>${meta.vmax.toFixed(2)}</span></div>`;
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

// --- ship (R/V Marion Dufresne) live track ---------------------------------

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

// A dark disc with a white ring and a boat glyph — distinct from the small blue
// drifter circles. Inline SVG (not an emoji) so it renders identically anywhere.
function shipIcon() {
  return L.divIcon({
    className: "ship-marker",
    html:
      '<svg viewBox="0 0 24 24" width="15" height="15" fill="#fff" aria-hidden="true">' +
      '<path d="M4 15h16l-2.2 5H6.2L4 15zm2-2V6.5L12 4l6 2.5V13H6z"/></svg>',
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

// Motion of the latest fix relative to the prior one.
const deriveMotion = (positions) =>
  positions.length < 2
    ? null
    : motionBetween(positions[positions.length - 2], positions[positions.length - 1]);

const fmtSpeed = (m) => (m ? speedBoth(m.speedKn / MS_TO_KN) : null);
const fmtHeading = (m) =>
  m && m.heading != null
    ? `${Math.round(m.heading) % 360}° ${compassPoint(m.heading)}`
    : null;
// ---------------------------------------------------------------------------

// One row model — [label, value] pairs with nulls dropped — shared by the popup
// and the sidebar so the two readouts can never drift. Each caller wraps the
// pairs in its own markup.
function shipRows(p, motion) {
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

function shipPopupHtml(p, motion) {
  const rows = shipRows(p, motion)
    .map(([k, v]) => `<span class="popup-label">${k}:</span> ${v}<br/>`)
    .join("");
  return `<div class="popup"><strong>R/V Marion Dufresne</strong><br/>${rows}</div>`;
}

// A fix is usable only with finite coordinates and a timestamp. The API can emit
// a partial record, and an unguarded `p.lat.toFixed()` downstream would throw and
// (via main's catch) blank the map — so filter at ingestion and the render path
// only ever sees clean fixes.
const isValidFix = (p) =>
  p && Number.isFinite(p.lat) && Number.isFinite(p.lon) && !!p.date;
const byDate = (a, b) => new Date(a.date).getTime() - new Date(b.date).getTime();

// Cased polyline (white halo + dark core, legible on any basemap), a dot at each
// 10-minute fix, and a ship marker, in one feature group. Holds the time-sorted
// position list so live polling can append only the new tail: setPositions
// replaces the whole track; append extends it past the last fix, drawing only
// the fresh points.
function makeShipLayer() {
  // The cased track is non-interactive: it carries no popup, and an interactive
  // polyline would swallow clicks meant for the dots painted on top of it.
  const opts = (color, weight) => ({
    pane: "shipTrack",
    color,
    weight,
    opacity: 0.95,
    interactive: false,
  });
  const halo = L.polyline([], opts(SHIP.haloColor, 5));
  const core = L.polyline([], opts(SHIP.trackColor, 2.5));
  // Per-fix dots use the pane's default SVG renderer, not a canvas: a canvas
  // renderer spans the whole viewport and would intercept every click across the
  // map. SVG keeps each dot an individually hit-testable element, and empty pane
  // area stays click-through (so the drifters above this pane stay clickable).
  // Added after the lines, so the dots paint on top of the cased track.
  const dots = L.layerGroup();
  const marker = L.marker([0, 0], {
    pane: "ship",
    icon: shipIcon(),
    opacity: 0, // hidden until the first fix lands
  }).bindPopup("");
  const group = L.featureGroup([halo, core, dots, marker]);
  let positions = [];

  // A small dot at fix `p`, sharing the latest-position popup but filled with
  // this fix's own met data and its motion relative to the preceding fix `prev`.
  function dotFor(p, prev) {
    const dot = L.circleMarker([p.lat, p.lon], {
      pane: "shipTrack",
      radius: 2.5,
      color: SHIP.haloColor,
      weight: 1,
      fillColor: SHIP.trackColor,
      fillOpacity: 1,
    });
    dot.bindPopup(shipPopupHtml(p, motionBetween(prev, p)));
    return dot;
  }

  function showLatest() {
    const last = positions[positions.length - 1];
    if (!last) return;
    const motion = deriveMotion(positions);
    marker.setLatLng([last.lat, last.lon]).setOpacity(1);
    marker.setPopupContent(shipPopupHtml(last, motion));
    renderShipInfo(last, motion);
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

function renderShipInfo(p, motion) {
  const timeEl = document.getElementById("ship-time");
  const readEl = document.getElementById("ship-readout");
  if (!timeEl) return;
  if (!p) {
    timeEl.textContent = "Ship position unavailable.";
    if (readEl) readEl.innerHTML = "";
    return;
  }
  timeEl.textContent = `Last fix ${formatFixTime(p.date)} — Flotte Océanographique Française.`;
  // "Last fix" already shows in the hint line above, so drop it from the rows.
  readEl.innerHTML = shipRows(p, motion)
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

  const overlays = {};

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

  // Surface currents, from one CMEMS field: speed shading + flow trails.
  const meta = await fetchJSON(DATA.meta, { optional: true });
  const currents = await fetchJSON(DATA.currents, { optional: true });
  const inertialField = await fetchJSON(DATA.inertialField, { optional: true });

  // Speed shading: a Mercator-warped PNG in the bottom data pane. The PNG is at
  // the native CMEMS grid resolution (one pixel per cell); `crisp` disables the
  // browser's default bilinear upscaling so the cells render as sharp pixels
  // instead of a smooth blur.
  if (meta && meta.bounds) {
    const speedLayer = L.imageOverlay(DATA.speed, meta.bounds, {
      pane: "shading",
      opacity: 0.85,
      className: "crisp-raster",
    });
    speedLayer.addTo(map);
    overlays["Current speed"] = speedLayer;
  }
  renderCurrentsInfo(meta);

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
    overlays["Current flow"] = flowLayer;

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

  // Near-inertial animation: a rotating vector field reconstructed client-side
  // from inertial_field.json (see the "near-inertial animation" block above).
  // Default OFF (buildInertialField never addTo(map)s it) — missing artifact
  // means no layer and no control row. The clock starts once, immediately;
  // it self-gates on map.hasLayer so it costs nothing while the layer is off.
  if (inertialField) {
    const { layer: inertialLayer, cells: inertialCells } = buildInertialField(inertialField);
    overlays["Near-inertial animation"] = inertialLayer;
    startInertialClock(map, inertialCells, inertialLayer);
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

  // Glider platforms (XSPAR buoy + seagliders) are instruments in the same
  // control as the drifter batches: their latest markers join the instrument
  // rows, their tracks the "True track" overlay. Optional so a missing file can't
  // blank the map. (Gliders have no forecast/hindcast, so those overlays get no
  // glider group.)
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

  // No base-layer selector — OpenStreetMap is the sole basemap; the control lists
  // only the overlays (and the ship, added on its first fix).
  const layersControl = L.control.layers(null, overlays, { collapsed: false }).addTo(map);

  // R/V Marion Dufresne live track (client-side; Flotte Océanographique Française
  // API). Last, and deliberately not awaited: it is the one third-party fetch, so
  // blocking on it would stall the same-origin layers and controls above behind a
  // slow host. Each poll requests only the window since the last fix and appends.
  // The overlay is added to the layer control on the first fix (not before, so an
  // empty/failed start never shows a dead toggle) and the marker reveals then; the
  // interval keeps trying, so a later poll revives the layer once the API recovers.
  // Polls are skipped while the tab is hidden — and resumed on return — to avoid
  // hammering a third-party host in the background.
  const ship = makeShipLayer();
  let shipShown = false;
  async function pollShip() {
    if (document.hidden) return;
    ship.append(await fetchShip(ship.lastDate() ?? SHIP.cruiseStart));
    if (!ship.lastDate()) {
      renderShipInfo(null);
    } else if (!shipShown) {
      ship.group.addTo(map);
      layersControl.addOverlay(ship.group, "R/V Marion Dufresne");
      shipShown = true;
    }
  }
  pollShip();
  setInterval(pollShip, SHIP.refreshMs);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) pollShip();
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
