/* 2026 Whirls Cruise — drifter map.
 *
 * Static client. Fetches the build artifacts from ./data/ and renders them as
 * Leaflet layers:
 *   latest.geojson                 -> circle markers (on by default)
 *   tracks.geojson                 -> trajectory lines (off by default)
 *   speed_<t>Z.webp + currents_meta.json -> surface-speed shading, one lossless
 *                                           WebP frame per valid time (imageOverlay)
 *   flowvis_<t>Z.webp              -> static streamline flow overlay, one lossless
 *                                     WebP frame per valid time (meta.flow_frames;
 *                                     imageOverlay, swapped on scrub; optional)
 *   inertial_field.json            -> animated near-inertial particle tracks (off)
 *   awaiting.json                  -> sidebar list, no map geometry
 *   build.json                     -> sidebar "data freshness" build time
 */

const DATA = {
  latest: "./data/latest.geojson",
  tracks: "./data/tracks.geojson",
  awaiting: "./data/awaiting.json",
  // Shading + flow rasters are per-frame files named in the metas' `frames` /
  // `flow_frames` manifests (speed_<t>Z.webp / vorticity_<t>Z.webp /
  // flowvis_<t>Z.webp), resolved under this base.
  dataBase: "./data/",
  meta: "./data/currents_meta.json",
  vorticityMeta: "./data/vorticity_meta.json",
  inertialField: "./data/inertial_field.json",
  build: "./data/build.json",
  gliders: "./data/gliders.geojson",
  agulhas: "./data/agulhas.json",
};

// --- instrument palette (#35) -----------------------------------------------
// Every per-class identity colour funnels through one named palette, selectable
// at load via ?palette=<name> for side-by-side review. A palette maps each
// instrument CLASS to one identity colour; the drifter marker's darker stroke is
// derived from its fill, so a palette carries only one colour per class. Classes:
// drifter batches (deployment_N, ORDINAL) + the staged pre_deploy; the virtual
// deployments (deploy_N, also ordinal — a run can grow to 2-3); the glider-group
// types; and the two ships. The two ordinal ramps sit on opposite warm/cool ends
// so each stays legible over BOTH surface shadings (speed=green, vorticity=blue↔
// magenta) — the hard constraint (see tmp_palettes/ for the clash analysis).
// Default is `ember` (warm drifters / cool virtual — the chosen scheme, #35);
// `?palette=azure|vivid|current` still switches for review (`current` = pre-#35).
const PALETTES = {
  current: {
    deployment_1: "#3a8ddb", deployment_2: "#17b3a3", deployment_3: "#e8791f",
    deployment_4: "#9b6fd4", deployment_5: "#d6339c", deployment_6: "#eab308",
    deployment_7: "#64748b", deployment_8: "#0ea5e9",
    deploy_1: "#16a34a", deploy_2: "#16a34a", deploy_3: "#16a34a",
    pre_deploy: "#a8a8a8", seaglider: "#38bdf8", waveglider: "#ec4899",
    xspar: "#f59e0b", float: "#a855f7", ship_md: "#1e40af", ship_ag: "#9b1c31",
  },
  ember: {
    deployment_1: "#fbb43e", deployment_2: "#f89f24", deployment_3: "#f68221",
    deployment_4: "#f3661f", deployment_5: "#df4a23", deployment_6: "#cb2e27",
    deployment_7: "#af2121", deployment_8: "#901919",
    deploy_1: "#60abfa", deploy_2: "#2c76e6", deploy_3: "#1c46a9",
    pre_deploy: "#8a94a3", seaglider: "#7c4dff", waveglider: "#e6299a",
    xspar: "#111827", float: "#00d68f", ship_md: "#12408f", ship_ag: "#8a1030",
  },
  azure: {
    deployment_1: "#64b3ec", deployment_2: "#449be5", deployment_3: "#3185dd",
    deployment_4: "#1f6ed5", deployment_5: "#1b5cbd", deployment_6: "#184aa5",
    deployment_7: "#133b8b", deployment_8: "#0e2d6f",
    deploy_1: "#faa339", deploy_2: "#e87713", deploy_3: "#b94203",
    pre_deploy: "#8a94a3", seaglider: "#7c4dff", waveglider: "#e6299a",
    xspar: "#111827", float: "#00d68f", ship_md: "#12408f", ship_ag: "#8a1030",
  },
  vivid: {
    deployment_1: "#e6194b", deployment_2: "#f58231", deployment_3: "#ffca3a",
    deployment_4: "#12d6a0", deployment_5: "#3fc5f0", deployment_6: "#4363d8",
    deployment_7: "#a034d0", deployment_8: "#ff5ec2",
    deploy_1: "#ff9ad5", deploy_2: "#e83fae", deploy_3: "#8e1a6d",
    pre_deploy: "#8a94a3", seaglider: "#7c4dff", waveglider: "#e6299a",
    xspar: "#111827", float: "#00d68f", ship_md: "#12408f", ship_ag: "#8a1030",
  },
};
const PALETTE =
  PALETTES[new URLSearchParams(location.search).get("palette")] ?? PALETTES.ember;

// Darken an identity fill to the drifter circle's thin outline stroke.
function paletteStroke(hex, f = 0.72) {
  const n = parseInt(hex.slice(1), 16);
  const r = Math.round(((n >> 16) & 255) * f);
  const g = Math.round(((n >> 8) & 255) * f);
  const b = Math.round((n & 255) * f);
  return `#${((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1)}`;
}

// Fallback view if no valid positions are present (cruise staging, Table Bay).
const FALLBACK_CENTER = [-33.9, 18.43];
const FALLBACK_ZOOM = 12;
// Deepest zoom (bounded — past the CMEMS 1/12° raster resolution there's no more
// detail, only enlarged pixels, so this is a legibility cap not a data one; #27
// lifts it a little to read dense drops/tracks). Also the top of the track
// line-weight ramp (see trackWeight); passed to L.map so the two stay in sync.
const MAX_ZOOM = 14;

// R/V Marion Dufresne live track. Fetched client-side from the French
// Oceanographic Fleet (Flotte Océanographique Française) localisation API — the
// same source as the IPSL WHIRLS "platform positions" button. CORS-open, no
// auth. Unlike the other layers this is not a build artifact: it polls live so
// the marker tracks the ship between rebuilds. See docs/ship.md.
const SHIP = {
  positions:
    "https://localisation.flotteoceanographique.fr/api/v2/vessels/MD/positions",
  // Start of the data period: the MD track is cropped here so it doesn't run back
  // through the pre-cruise transit. endDate is now.
  cruiseStart: "2026-06-28T00:00:00.000Z",
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
    // Ship identity colour from the active PALETTE (ship_md); a white halo keeps
    // the line crisp over the shading overlays. Ships are large icons, so they
    // read apart from the drifters/gliders by shape as well as colour.
    trackColor: PALETTE.ship_md,
    haloColor: "#ffffff",
    markerColor: PALETTE.ship_md,
    panel: { time: "md-ship-time", readout: "md-ship-readout" },
    rows: (p, prev) => mdRows(p, motionBetween(prev, p)),
  },
  agulhas: {
    name: "R/V S.A. Agulhas II",
    source: "myshiptracking.com (via IPSL WHIRLS)",
    // Ship identity colour from the active PALETTE (ship_ag).
    trackColor: PALETTE.ship_ag,
    haloColor: "#ffffff",
    markerColor: PALETTE.ship_ag,
    panel: { time: "agulhas-ship-time", readout: "agulhas-ship-readout" },
    rows: (p) => agulhasRows(p),
  },
};

// --- batch styling seam -----------------------------------------------------
// Markers carry a `batch` property. All per-batch appearance decisions funnel
// through styleForBatch(); the batch filter control (below) reads the same
// `batch` property to group markers. Staged (not-yet-deployed) drifters render
// muted grey; each deployment batch gets its own colour along the active
// PALETTE's ordinal drifter ramp, so successive deployments read apart. A further
// deployment past the ramp falls back to DEPLOYED_STYLE until given its own step.
// Fill = the palette identity colour; the thin outline is a darker derived stroke.
const BATCH_STYLES = Object.fromEntries(
  ["pre_deploy", "deployment_1", "deployment_2", "deployment_3", "deployment_4",
   "deployment_5", "deployment_6", "deployment_7", "deployment_8"].map((k) => [
    k, { color: paletteStroke(PALETTE[k]), fillColor: PALETTE[k] },
  ]),
);
const DEPLOYED_STYLE = {
  color: paletteStroke(PALETTE.deployment_1), fillColor: PALETTE.deployment_1,
};
function styleForBatch(batch) {
  return {
    radius: 6,
    weight: 1,
    fillOpacity: 1, // opaque fill — the identity colour reads undiluted over the shadings
    ...(BATCH_STYLES[batch] ?? DEPLOYED_STYLE),
  };
}

// Pretty label for a batch/instrument row key. Drifter batches read "batch N",
// DERIVED from the `deployment_N` key so any future deployment surfaces correctly
// with no code change (the whole ordinal ramp, not just 1..5); the staged
// pre-deployment pool reads the catch-all "batch X", sitting alongside the numbered
// batches. Glider-group types (xspar/seaglider/waveglider/float) aren't batches —
// they fall through to their GLIDER_STYLES label so they read "XSPAR buoy" /
// "Glider" / "Wave gliders" / "Floats" in the same compartment. Anything else falls
// back to the raw key.
const batchLabel = (batch) => {
  if (batch === "pre_deploy") return "batch X";
  const m = /^deployment_(\d+)$/.exec(batch);
  if (m) return `batch ${m[1]}`;
  return GLIDER_STYLES[batch]?.label ?? batch;
};

// How many instruments a marker group holds, for the row count "(N)". A group holds
// one HEAD marker per instrument PLUS one clock-gated deployment dot per instrument
// (addDeploymentDot, tagged `_deploymentDot`), so a raw getLayers().length reads
// double once the dots exist. Gliders build their tracks — and thus their dots —
// synchronously before the dock, so their rows would double; drifters load tracks
// later, so only timing spared them. Counting heads only fixes it for both.
const instrumentCount = (group) =>
  group.getLayers().filter((l) => !l._deploymentDot).length;

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

// Single source of truth for how a coordinate is written for humans, shared by
// every popup, the ship readouts, and the cursor readout so all locations match.
// Latitude first, then longitude — the geographic/nautical convention (charts,
// GPS, Google Maps all lead with latitude) — with N/S and E/W hemisphere letters
// instead of signed degrees, at 4-decimal precision (~11 m). Longitude is wrapped
// to (-180, 180] so a pan across the antimeridian still reads as a normal
// coordinate rather than an accumulating one.
function formatLatLon(lat, lon) {
  const hemi = (v, pos, neg) => `${Math.abs(v).toFixed(4)}° ${v >= 0 ? pos : neg}`;
  const lonWrapped = L.Util.wrapNum(lon, [-180, 180], true);
  return `${hemi(lat, "N", "S")}, ${hemi(lonWrapped, "E", "W")}`;
}

// Escape a value before it is interpolated into an HTML-string sink (innerHTML, a
// Leaflet popup/tooltip's HTML content). The acute case (SEC-3) is the ship met
// fields — `truewindspeed`, `seatemp`, … — which come straight from the live,
// browser-polled third-party localisation API that is explicitly outside the trust
// boundary; a compromised source returning `"<img src=x onerror=…>"` in any field would
// otherwise run script on the map origin (the same origin as `/api`). The instrument
// popups interpolate build-baked third-party strings (`D_number`, `batteryState`,
// glider `id`) the same way, so they are escaped too. `String(value)` coerces first so a
// number/null renders as text, never as markup. The CSP in `index.html` is the backstop;
// escaping at the sink is the actual fix.
function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function popupHtml(props, latlng) {
  const p = props || {};
  return `
    <div class="popup">
      <strong>${escapeHtml(p.D_number ?? "—")}</strong><br/>
      <span class="popup-label">Last fix:</span> ${formatFixTime(p.date_UTC)}<br/>
      <span class="popup-label">Battery:</span> ${escapeHtml(p.batteryState ?? "—")}<br/>
      <span class="popup-label">Speed (derived):</span> ${fmtSpeedMps(p.derived_speed_mps)}<br/>
      <span class="popup-label">Heading (derived):</span> ${fmtDir(p.derived_heading_deg)}<br/>
      <span class="popup-label">Speed (reported):</span> ${fmtSpeedMps(p.U_speed_mps)}<br/>
      <span class="popup-label">Heading (reported):</span> ${fmtDir(p.U_Dir_deg)}<br/>
      <span class="popup-label">Position:</span>
      ${formatLatLon(latlng.lat, latlng.lng)}
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
// The picked track is highlighted in its OWN identity colour — a wider line, with
// every OTHER instrument desaturated — rather than a separate accent colour (#35).
// So a highlight never changes hue, only weight + the surrounding contrast.

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
// Current map zoom, kept live by a `zoomend` handler in main(). Track line
// weight scales with it (trackWeight) so tracks stay distinct when zoomed out.
let trackZoom = FALLBACK_ZOOM;

// Thin lines when zoomed out so overlapping tracks stay separable, a touch
// heavier zoomed in. The selected track keeps a fixed extra weight so it still
// reads as picked at any zoom.
function trackWeight(zoom, selected) {
  const base = zoom >= MAX_ZOOM - 2 ? 2 : zoom >= MAX_ZOOM - 5 ? 1.5 : 1;
  return selected ? base + 2.5 : base;
}

const stateFor = (key) =>
  key === selectedInstrument ? "selected" : selectedInstrument ? "dim" : "normal";

// Register a freshly-built element's restyler and immediately apply the current
// selection state, so a part built while a selection is active renders correctly.
// `owner` tags a family that may be torn down and rebuilt together (track segments,
// owner "trackseg", on the outlier toggle); untagged parts (heads) persist.
function registerPart(key, restyle, owner) {
  (trackParts[key] ??= []).push({ fn: restyle, owner });
  restyle(stateFor(key));
}

function applySelection() {
  for (const key of Object.keys(trackParts))
    for (const p of trackParts[key]) p.fn(stateFor(key));
}

// Drop every restyler tagged with `owner` across all keys — used when a part family is
// rebuilt (the outlier-toggle track rebuild), so stale restylers pointing at removed
// polylines don't accumulate. Heads (untagged) survive.
function dropPartsByOwner(owner) {
  for (const key of Object.keys(trackParts))
    trackParts[key] = trackParts[key].filter((p) => p.owner !== owner);
}

// Toggle: clicking the current selection clears it; another instrument replaces it.
function selectInstrument(key) {
  selectedInstrument = key === selectedInstrument ? null : key;
  applySelection();
}

// --- at-time position markers ------------------------------------------------
// Each virtual drift track carries ONE marker at the position it occupies at the
// app clock's instant, interpolated between the track's bracketing vertices and
// hidden when the clock falls outside the track's span — the moving head of the
// clock-clipped trail (plan 035). The observed layers (drifter/glider/ship tracks)
// don't register here any more: their own latest-position head markers ride the
// clock instead (see the clock-following block below), so a second dot on the same
// moving spot would only z-fight.
//
// A marker rides the same Leaflet group as its track (so it shows/hides with that
// layer's toggle) but renders in the top `atTime` pane, so it never hides under a
// line. Each entry keeps ascending-time samples {times[], lats[], lngs[]}; a clock
// change repositions or hides every marker, throttled to one rAF per scrub. Clicking
// a marker highlights its whole SET (setKey: `deploy:<id>` — the deployment's whole
// array at this instant, the marker click axis of decision 7).
const AT_TIME_RADIUS = 5;
const atTimeEntries = [];      // every registered marker entry
const atTimeSets = {};         // setKey -> [entry, ...]
let selectedAtTimeSet = null;  // the highlighted set's key, or null
let atTimeClockMs = null;      // the app clock as epoch ms (null until set in main)
let atTimeRaf = 0;             // pending rAF handle for a throttled repaint

const atTimeIso = (ms) => new Date(ms).toISOString().replace(/\.\d+Z$/, "Z");

// Interpolated position on an entry's polyline at epoch `ms`, or null when the clock
// is outside [first, last] sample time. Samples ascend in time, so a forward scan
// finds the bracketing pair and lerps between them.
function sampleAtTime(entry, ms) {
  const { times, lats, lngs } = entry;
  if (ms < times[0] || ms > times[times.length - 1]) return null;
  let i = 0;
  while (i < times.length - 1 && times[i + 1] < ms) i++;
  const t0 = times[i], t1 = times[i + 1] ?? t0;
  const f = t1 === t0 ? 0 : (ms - t0) / (t1 - t0);
  return {
    lat: lats[i] + f * ((lats[i + 1] ?? lats[i]) - lats[i]),
    lng: lngs[i] + f * ((lngs[i + 1] ?? lngs[i]) - lngs[i]),
  };
}

// Paint one visible marker: a small filled circle in its layer's colour with a white
// halo, enlarged + dark-ringed when its set is highlighted.
function restyleAtTimeMarker(entry, selected) {
  entry.marker.setStyle({
    color: selected ? "#111827" : "#fff",
    weight: selected ? 2 : 1,
    opacity: 1,
    fillColor: entry.color,
    fillOpacity: 1,
  });
  entry.marker.setRadius(selected ? AT_TIME_RADIUS + 3 : AT_TIME_RADIUS);
  if (selected) entry.marker.bringToFront();
}

// Position/refresh one marker to clock `ms`: move + label it when the clock is inside
// the track's span, else fully hide it (radius 0, transparent) so it neither shows
// nor catches a click while its layer is still on the map.
function updateAtTimeMarker(entry, ms) {
  const s = ms == null ? null : sampleAtTime(entry, ms);
  if (!s) {
    entry.onMap = false;
    entry.marker.setStyle({ opacity: 0, fillOpacity: 0 });
    entry.marker.setRadius(0);
    return;
  }
  entry.onMap = true;
  entry.marker.setLatLng([s.lat, s.lng]);
  entry.marker.setTooltipContent(`${entry.label} · ${atTimeIso(ms)}`);
  restyleAtTimeMarker(entry, entry.setKey === selectedAtTimeSet);
}

// Drive every clock-aware element to `ms`, throttled to one animation frame so a
// fast scrub coalesces into a single repaint: the at-time markers (the deployments'
// moving heads walk their scrubber-cropped drift lines), the observed tracks (segments
// clipped at the clock, heads moved — see clipTrack), and the single-fix heads
// (updatePointHeads).
function updateClock(ms) {
  atTimeClockMs = ms;
  if (atTimeRaf) return;
  atTimeRaf = requestAnimationFrame(() => {
    atTimeRaf = 0;
    for (const entry of atTimeEntries) updateAtTimeMarker(entry, atTimeClockMs);
    for (const entry of trackClockEntries) clipTrack(entry, atTimeClockMs);
    updatePointHeads(atTimeClockMs);
    updateDeploymentDots(atTimeClockMs); // reveal each deployment dot once the clock passes its deploy time

    // Crop the virtual deployment drift lines to the clock (forward + backward runs).
    for (const key of Object.keys(deployTracks))
      clipDeployTrack(deployTracks[key], atTimeClockMs, key === selectedTrack);
    // Last, so a drifter's forecast (when the clock is past now) overrides its observed
    // clip / point head and walks the marker along the violet forecast.
    for (const entry of forecastClockEntries) clipForecast(entry, atTimeClockMs);
  });
}

// Re-apply the set highlight to every on-map marker (a hidden, out-of-span marker
// keeps its zero radius until the clock brings it back).
function applyAtTimeSelection() {
  for (const entry of atTimeEntries)
    if (entry.onMap) restyleAtTimeMarker(entry, entry.setKey === selectedAtTimeSet);
}

// Toggle the highlighted set: clicking a marker of the current set clears it, another
// set replaces it (decision 7 — a deployment's whole array at this instant).
function selectAtTimeSet(setKey) {
  selectedAtTimeSet = setKey === selectedAtTimeSet ? null : setKey;
  applyAtTimeSelection();
}

// Register a time-aware track's at-time marker. `owner` is the Leaflet group the
// track rides (so the marker shows/hides with that layer); `times/lats/lngs` are
// parallel sample arrays (normalised here to ascending time — a backward run's
// vertices descend). Returns the entry so a growing track (the ships) can swap its
// samples in place. Fewer than two samples can't be interpolated, so no marker.
function registerAtTimeMarker(owner, { color, label, setKey, times, lats, lngs }) {
  if (times.length < 2) return null;
  if (times[0] > times[times.length - 1]) {
    times.reverse();
    lats.reverse();
    lngs.reverse();
  }
  const marker = L.circleMarker([lats[0], lngs[0]], {
    pane: "atTime",
    radius: 0,
    color: "#fff",
    weight: 1,
    fillColor: color,
    fillOpacity: 0,
    opacity: 0,
    bubblingMouseEvents: false, // its click highlights, doesn't reach the map
  });
  marker.bindTooltip("", { direction: "top" });
  marker.on("click", () => selectAtTimeSet(setKey));
  const entry = { marker, color, label, setKey, times, lats, lngs, onMap: false };
  atTimeEntries.push(entry);
  (atTimeSets[setKey] ??= []).push(entry);
  owner.addLayer(marker);
  updateAtTimeMarker(entry, atTimeClockMs);
  return entry;
}

// --- clock-following tracks + heads (plan 035) --------------------------------
// One rule for every observed time-aware layer (drifter true tracks, glider tracks,
// ship tracks): at clock t the track shows only what has happened BY t — segments
// whose span lies at or before the clock, the crossing segment trimmed to the
// interpolated position — and the layer's HEAD MARKER (batch-coloured circle, glider
// diamond, ship disc) rides that clipped end. Past the track's last sample the full
// track shows and the head parks at the latest position (the untouched-clock view at
// load); before its first sample the layer hides entirely (the instrument didn't
// exist yet). The heads replace the small per-track at-time dots plan 034 added —
// two markers on the same moving spot would z-fight; the at-time machinery above
// stays for the virtual deployments (whose marker IS the track's head).

// Head-marker controllers, keyed like the selection registry (drifter D_number,
// glider id, or a ship name): { at(latlng, html), latest(), hide() }. Registered by
// the latest-marker builders; looked up by the track builders, so a head only moves
// once its track's time series is loaded (the lazy drifter tracks) and stays put
// otherwise.
const trackHeads = {};
const registerHead = (key, ctl) => {
  trackHeads[key] = ctl;
};

// The single "Show tracks" master (lives in the scrubber box). Governs the *observed*
// track LINES — drifter, glider, ship — plus the real-drifter forecast lines (#22), but
// never the heads/at-time markers (which are positions, not tracks) nor the
// virtual-deployment drift lines (which the deploy tool owns; they are scrubber-cropped
// but not gated by this master). The
// observed heads follow the app clock regardless of this toggle (plan 035): their
// track clips are registered eagerly at load, so scrubbing walks every head even with
// the lines hidden.
let tracksOn = true;

// Set once the observed tracks (drifter tracks.geojson + gliders) are built, so the
// full multi-fix head set is known. Until then the point-head clock stays quiet
// (heads park at their latest position); after, it owns the single-fix heads.
let tracksLoaded = false;

const trackClockEntries = [];

// Outlier toggle (#30): hide out-and-back GPS spikes in the observed drifter/glider
// tracks, computed client-side from the `derived_speed_mps` already in tracks.geojson
// (no extra download). Default hide. `observedTrackSources` keeps each track's raw
// geometry so the toggle can rebuild the segments + clock entries in place (see
// rebuildObservedTracks); `observedTrackHandles` are the live builds to tear down.
let hideOutliers = true;
const observedTrackSources = [];
const observedTrackHandles = [];

// Fixed per-instrument deployment dots (#33), each { marker, t, shown }: shown only once
// the app clock reaches the deployment time `t` (see updateDeploymentDots). Built once
// with the tracks, outside the outlier rebuild.
const deploymentDots = [];

// Real-drifter forecast clips (#22). Each is one violet polyline per deployed drifter,
// its vertices timed from the /api/forecast advection, driving the SAME head as the
// drifter's observed track. Processed after the observed clips + point heads in
// updateClock, so in the future (clock past the forecast start ≈ now) the forecast wins
// the head and walks the drifter marker forward; at/before now it doesn't touch the head
// (the observed/point clock owns the real position) and hides its line. See clipForecast.
const forecastClockEntries = [];

// Whether a real-drifter forecast line is visible for its batch — set by the Instruments
// panel (buildInstrumentRows) so unchecking a drifter batch also drops its forecast
// trajectories, exactly as it drops that batch's markers and observed tracks. Defaults to
// "all visible" until the panel wires it. The drifter's marker (its head) is already
// governed by the batch's marker group, so this gates only the forecast LINE.
let forecastBatchVisible = () => true;

// Head keys that a track-clip entry drives (registerTrackClock / the ship's
// syncClock add here). A single-fix instrument never gets one, so the point-head
// clock below owns its head instead.
const trackedHeadKeys = new Set();

// Point-in-time head clocks: one per latest marker, keyed on that instrument's own
// `date_UTC`. Fixes single-fix instruments (e.g. D-509) that, lacking a track
// LineString, were never clock-clipped and so stayed on the map at every clock. A
// multi-fix head is driven by clipTrack instead (its key is in trackedHeadKeys), so
// its point clock is skipped.
const headPointClocks = []; // { t, headKey }
const registerPointHead = (headKey, iso) => {
  const t = Date.parse(iso);
  if (Number.isFinite(t)) headPointClocks.push({ t, headKey });
};

// Drive the single-fix point-clocked heads to clock `ms`: a head with no track-clip
// entry hides before its (only) fix and parks at its latest position at/after it —
// the same clock rule clipTrack applies to multi-fix heads. Quiet until tracksLoaded,
// so we never mistake a not-yet-built multi-fix head for a single-fix one. Runs inside
// updateClock (every scrub) and once tracks finish loading.
function updatePointHeads(ms) {
  if (!tracksLoaded) return;
  for (const { t, headKey } of headPointClocks) {
    if (trackedHeadKeys.has(headKey)) continue;
    const head = trackHeads[headKey];
    if (!head) continue;
    if (ms == null || ms >= t) head.latest();
    else head.hide();
  }
}

// Register an observed track for clock clipping. `segs` are the per-fix-pair
// polylines ({ line, t0, t1, a, b } — see addTrackSegments); `coords`/`fixes` give
// the vertex samples (a vertex with no finite date_UTC can't be placed on the clock
// and is skipped); `headKey` names the layer's head controller; `tip(fix, latlng, i,
// fixes)` renders the head's tooltip for the bracketing fix while scrubbed back (the
// index + array let the ships derive motion from the preceding fix). Under two timed
// samples there is nothing to scrub. Applies the current clock immediately.
function registerTrackClock(group, segs, coords, fixes, headKey, tip) {
  const times = [], lats = [], lngs = [], timedFixes = [];
  (coords ?? []).forEach(([lng, lat], i) => {
    const t = Date.parse(fixes?.[i]?.date_UTC);
    if (Number.isFinite(t)) {
      times.push(t);
      lats.push(lat);
      lngs.push(lng);
      timedFixes.push(fixes[i]);
    }
  });
  if (times.length < 2) return null;
  const entry = { group, segs, times, lats, lngs, fixes: timedFixes, headKey, tip, trimmed: null };
  trackClockEntries.push(entry);
  if (headKey != null) trackedHeadKeys.add(headKey);
  clipTrack(entry, atTimeClockMs);
  return entry;
}

// Show/hide one segment by group membership (cheap when nothing changes; works
// whether or not the group itself is on the map).
function setSegShown(entry, seg, on) {
  if (seg.on === on) return;
  seg.on = on;
  if (on) entry.group.addLayer(seg.line);
  else entry.group.removeLayer(seg.line);
}

// Clip one observed track to clock `ms` and move its head. A segment with a
// non-finite time can't be placed on the clock, so it stays shown whenever any of
// the track shows (only the before-first-sample branch hides it, with everything
// else).
function clipTrack(entry, ms) {
  if (ms == null) return;
  const { segs, times, lats, lngs, headKey } = entry;
  const head = trackHeads[headKey];
  const last = times[times.length - 1];

  // Restore a previously trimmed crossing segment before re-deciding membership.
  if (entry.trimmed) {
    entry.trimmed.line.setLatLngs([entry.trimmed.a, entry.trimmed.b]);
    entry.trimmed = null;
  }

  if (ms >= last) {
    for (const seg of segs) setSegShown(entry, seg, true);
    head?.latest();
    return;
  }
  if (ms < times[0]) {
    for (const seg of segs) setSegShown(entry, seg, false);
    head?.hide();
    return;
  }

  // Bracketing sample pair and the interpolated at-clock position.
  let i = 0;
  while (i < times.length - 1 && times[i + 1] < ms) i++;
  const t0 = times[i], t1 = times[i + 1];
  const f = t1 === t0 ? 0 : (ms - t0) / (t1 - t0);
  const pos = L.latLng(
    lats[i] + f * (lats[i + 1] - lats[i]),
    lngs[i] + f * (lngs[i + 1] - lngs[i])
  );

  for (const seg of segs) {
    const timed = Number.isFinite(seg.t0) && Number.isFinite(seg.t1);
    if (!timed || seg.t1 <= ms) {
      setSegShown(entry, seg, true);
    } else if (seg.t0 <= ms) {
      // The crossing segment: shown, trimmed to the at-clock position.
      setSegShown(entry, seg, true);
      seg.line.setLatLngs([seg.a, pos]);
      entry.trimmed = seg;
    } else {
      setSegShown(entry, seg, false);
    }
  }
  head?.at(pos, entry.tip ? entry.tip(entry.fixes[i], pos, i, entry.fixes) : null);
}

// Interpolate the [lat,lng] position at time `t` along the ascending `times[]`
// (clamped to the endpoints). Returns null for an empty series.
function interpAtTime(times, lats, lngs, t) {
  const n = times.length;
  if (n === 0) return null;
  if (t <= times[0]) return [lats[0], lngs[0]];
  if (t >= times[n - 1]) return [lats[n - 1], lngs[n - 1]];
  let i = 0;
  while (i < n - 1 && times[i + 1] < t) i++;
  const t0 = times[i], t1 = times[i + 1];
  const f = t1 === t0 ? 0 : (t - t0) / (t1 - t0);
  return [lats[i] + f * (lats[i + 1] - lats[i]), lngs[i] + f * (lngs[i + 1] - lngs[i])];
}

// Leaflet [lat,lng][] of the path clipped to the time window [tA, tB], with both
// endpoints interpolated. Returns null when the window is empty.
function clipPathToWindow(times, lats, lngs, tA, tB) {
  if (!(tB > tA)) return null;
  const coords = [interpAtTime(times, lats, lngs, tA)];
  for (let k = 0; k < times.length; k++)
    if (times[k] > tA && times[k] < tB) coords.push([lats[k], lngs[k]]);
  coords.push(interpAtTime(times, lats, lngs, tB));
  return coords;
}

// Clip one real-drifter forecast to clock `ms` and walk its head (#22, #34). The
// forecast is seeded at the drifter's last fix, so its full path spans [last fix →
// field end]; the entry holds two non-interactive violet polylines — a DASHED `bridge`
// (last fix → now: the un-transmitted reporting-lag gap, #34) and the SOLID `line`
// (now → field end: the forecast). At/before the last fix the entry is inactive (both
// lines hidden, head owned by the observed/point clock). Otherwise it walks the drifter's
// own head to the clock along the modeled path REGARDLESS of the "Show tracks" master,
// and — while that master is on and the batch is selected — draws the modeled path
// CLIPPED to the clock: the dashed bridge up to min(clock, now) and the solid forecast
// from now up to the clock, so the trail unfolds continuously (observed → dashed → solid)
// as the scrubber advances and nothing shows ahead of the head (#34). Runs last in
// updateClock, so an active forecast wins the head over the observed clip.
function clipForecast(entry, ms) {
  if (ms == null) return;
  const { line, bridge, nowGhost, times, lats, lngs, headKey, group, nowMs } = entry;
  const start = times[0]; // the drifter's last observed fix (the forecast seed time)
  // The dimmed now-ghost is a POSITION (like a head), not a track line: revealed once
  // the clock scrubs past now, batch-gated, and INDEPENDENT of "Show tracks". Toggle it
  // up front so every early return below leaves it correct.
  if (nowGhost) {
    const on = ms > nowMs && forecastBatchVisible(entry.batch);
    if (on !== entry.ghostShown) {
      nowGhost.setStyle({ fillOpacity: on ? 1 : 0 });
      entry.ghostShown = on;
    }
  }
  const show = (obj, coords, flag) => {
    if (coords && coords.length >= 2) {
      obj.setLatLngs(coords);
      if (!entry[flag]) { group.addLayer(obj); entry[flag] = true; }
    } else if (entry[flag]) {
      group.removeLayer(obj); entry[flag] = false;
    }
  };
  if (ms <= start) {
    show(bridge, null, "bridgeShown");
    show(line, null, "lineShown");
    return; // at/before the last fix: the real last-known position is owned by the observed clip
  }
  const head = trackHeads[headKey];
  const clockT = Math.min(ms, times[times.length - 1]);
  const pos = interpAtTime(times, lats, lngs, clockT);
  // The head walks the modeled path to the clock; the lines show only while "Show tracks"
  // is on AND this drifter's batch is selected in the Instruments panel.
  head?.at(L.latLng(pos[0], pos[1]), entry.tip);
  if (tracksOn && forecastBatchVisible(entry.batch)) {
    // Unfold with the scrubber (clock-clipped, nothing ahead of the head): the dashed
    // bridge grows obs-end → min(clock, now), then the solid forecast grows now → clock
    // (only once the clock passes now). The trail stays continuous — observed → dashed →
    // solid → head — because the bridge starts at the observed track's last fix and the
    // bridge/solid meet at now (#34).
    show(bridge, clipPathToWindow(times, lats, lngs, start, Math.min(clockT, nowMs)), "bridgeShown");
    show(line, clockT > nowMs ? clipPathToWindow(times, lats, lngs, nowMs, clockT) : null, "lineShown");
  } else {
    show(bridge, null, "bridgeShown");
    show(line, null, "lineShown");
  }
}

// Forget at-time markers by setKey. By default `key` is a prefix (Deploy "Clear all"
// wipes every "deploy:" marker along with the layers they ride); with `exact` it
// matches one setKey verbatim (deleting a single deployment — "deploy:2" must not also
// catch "deploy:20"). Removes them from the map, the registry, the set index, and
// clears a selection pointing at them.
function removeAtTimeSet(key, exact = false) {
  const hit = (k) => (exact ? k === key : k.startsWith(key));
  for (let i = atTimeEntries.length - 1; i >= 0; i--) {
    if (hit(atTimeEntries[i].setKey)) {
      atTimeEntries[i].marker.remove();
      atTimeEntries.splice(i, 1);
    }
  }
  for (const k of Object.keys(atTimeSets)) if (hit(k)) delete atTimeSets[k];
  if (selectedAtTimeSet && hit(selectedAtTimeSet)) selectedAtTimeSet = null;
}

// Shared line style. Each track line carries its instrument's identity colour (`base`
// — the batch/glider head colour; a per-instrument request, the #35 seam), defaulting
// to TRACK_COLOR for any caller that doesn't pass one. Opacity is held constant across
// states: dimming is by desaturation, not transparency. Weight follows the live zoom
// (see trackWeight).
const trackColor = (state, base = TRACK_COLOR) =>
  state === "dim" ? desaturate(base) : base; // selected/normal keep the identity colour
const lineStyle = (state, base = TRACK_COLOR) => ({
  color: trackColor(state, base),
  weight: trackWeight(trackZoom, state === "selected"),
  opacity: 1, // opaque line — the identity colour reads undiluted; dim is by desaturation
});

// Restyle a track line for the current selection state and zoom, and lift the
// selected instrument's segments to the front of the shared track renderer
// (overlayPane) so the picked track sits above every *other track* — but still
// below the head/ship marker panes (issue #11). Both the drifter and glider
// builders route through this so front-raising is defined once. `base` is the line's
// identity colour (see lineStyle).
function restyleLine(line, state, base) {
  line.setStyle(lineStyle(state, base));
  if (state === "selected") line.bringToFront();
}

// The drifter + glider true tracks render on a shared **canvas** renderer, not the
// default SVG one. "Show tracks" reveals ~100k fix-to-fix segments; as SVG that is
// ~100k <path> DOM nodes the browser must lay out, composite, and hit-test on every
// pan/zoom — the "Show tracks" lag. A canvas renderer draws them all in one redraw
// with no DOM, so pan/zoom stays smooth (hover/click hit-testing and bringToFront
// still work, done by the renderer). They share the default overlayPane (400).
//
// Only ONE full-viewport track canvas may exist: a canvas hit-tests its whole
// rectangle (transparent or not), so a second track canvas above this one would
// swallow every hover/click meant for the tracks below. The ship tracks therefore
// stay on SVG (few segments — see makeShipLayer). Created lazily on first use — by
// then main() has created the panes.
const _trackRenderers = {};
const trackRenderer = (pane) => (_trackRenderers[pane] ??= L.canvas({ pane }));

// Build a track as one polyline *per fix-to-fix segment* rather than a single
// line plus a dot at every fix. The segments abut into one continuous line, but
// each carries its own hover tooltip — the tooltip that used to live on the
// per-fix dot — so hovering anywhere along the track shows that leg's fix
// (no separate dot markers; the whole line is the hover target). `tip(fix, latlng)`
// renders the fix tooltip; each segment registers for click-to-highlight under
// `key` (a drifter D_number or glider id) and selects it on click. Segment i
// (coords[i] -> coords[i+1]) is tagged with fix i at its start; the final fix is
// the instrument's latest-position head marker, so every fix stays reachable.
// Returns the segments with their endpoints + time span ({ line, t0, t1, a, b, on })
// so registerTrackClock can clip the track at the app clock.
// A drifter/glider fix is an out-and-back GPS spike when the implied speed is anomalous
// on BOTH the segment arriving at it and the one leaving it (a genuine fast leg trips
// only one). `derived_speed_mps[i]` is the speed INTO fix i, so fix i is a spike when
// fixes[i] and fixes[i+1] both exceed the threshold. Nulls (the first fix, coincident
// fixes) and the last fix (no outgoing segment) are never outliers. The threshold sits
// well above the drift regime (<~2 m/s) and the 4-dp rounding floor — real spikes imply
// 15+ m/s (#30).
const OUTLIER_SPEED_MPS = 5;
const OUTLIER_MAX_GAP_MS = 24 * 3600 * 1000; // bridge a de-spiked gap up to 24 h; blank beyond
function outlierFlags(fixes) {
  const n = fixes?.length ?? 0;
  const flags = new Array(n).fill(false);
  for (let i = 0; i < n - 1; i++) {
    const a = fixes[i]?.derived_speed_mps, b = fixes[i + 1]?.derived_speed_mps;
    if (Number.isFinite(a) && Number.isFinite(b) && a > OUTLIER_SPEED_MPS && b > OUTLIER_SPEED_MPS)
      flags[i] = true;
  }
  return flags;
}

// Resolve a track's display geometry for the current "hide outliers" state. Showing:
// the raw vertices, nothing skipped. Hiding: drop the spike fixes, then for each
// consecutive kept pair that spans a removed spike, bridge it with a straight segment
// when the gap is ≤ 24 h, else blank it (skip that segment). The kept vertices feed both
// the drawn segments and the clock entry, so the head interpolates the cleaned path.
function displayTrack(coords, fixes, hide) {
  if (!hide) return { coords, fixes, skipSeg: null };
  const flags = outlierFlags(fixes);
  const keep = [];
  for (let i = 0; i < (coords?.length ?? 0); i++) if (!flags[i]) keep.push(i);
  const skipSeg = new Array(Math.max(0, keep.length - 1)).fill(false);
  for (let j = 0; j < keep.length - 1; j++) {
    if (keep[j + 1] === keep[j] + 1) continue; // no spike removed between them — a normal leg
    const t0 = Date.parse(fixes[keep[j]]?.date_UTC);
    const t1 = Date.parse(fixes[keep[j + 1]]?.date_UTC);
    if (!(Number.isFinite(t0) && Number.isFinite(t1)) || t1 - t0 > OUTLIER_MAX_GAP_MS)
      skipSeg[j] = true; // blank a > 24 h gap (or an unknowable span)
  }
  return { coords: keep.map((i) => coords[i]), fixes: keep.map((i) => fixes[i]), skipSeg };
}

function addTrackSegments(group, coords, fixes, key, tip, skipSeg, color) {
  const base = color ?? TRACK_COLOR; // per-instrument identity colour for this track's lines
  const pts = coords.map(([lng, lat]) => [lat, lng]);
  const segs = [];
  for (let i = 0; i < pts.length - 1; i++) {
    if (skipSeg?.[i]) continue; // blanked de-spiked gap (> 24 h) — draw no segment (#30)
    const seg = L.polyline([pts[i], pts[i + 1]], {
      renderer: trackRenderer("overlayPane"), // canvas, not one SVG <path> per segment
      color: base,
      weight: 2,
      opacity: 1,
      bubblingMouseEvents: false, // background clicks (not this) clear selection
    }).addTo(group);
    seg.bindTooltip(tip(fixes?.[i] ?? {}, L.latLng(pts[i])), { sticky: true });
    if (key != null) {
      registerPart(key, (s) => restyleLine(seg, s, base), "trackseg");
      seg.on("click", () => selectInstrument(key));
    }
    segs.push({
      line: seg,
      t0: Date.parse(fixes?.[i]?.date_UTC),
      t1: Date.parse(fixes?.[i + 1]?.date_UTC),
      a: pts[i],
      b: pts[i + 1],
      on: true,
    });
  }
  return segs;
}

// A fixed "deployment dot" at a track's deployment point (its first free-drift fix —
// true tracks are truncated at deployment, plan 010): a filled disc in the
// instrument's identity colour, no outline, radius 2.4 (a small filled disc). The
// real-instrument counterpart of a virtual deployment's drop disc (#33). It is
// non-interactive so it never steals a hover/click from the track canvas beneath it
// (plan 039), lives in the deployDrops pane (above the track canvas, below the heads),
// and is added to the instrument's MARKER group — so the Instruments row governs it
// like the head, independent of the "Show tracks" master (matching the virtual drops).
// It is clock-gated: shown only once the app clock reaches the deployment time
// (`depTimeMs`, the first-fix time) — "only after it is actually deployed".
function addDeploymentDot(markerGroup, lng, lat, color, depTimeMs) {
  if (markerGroup == null || !Number.isFinite(lat) || !Number.isFinite(lng)) return;
  const t = Number.isFinite(depTimeMs) ? depTimeMs : -Infinity;
  const shown = atTimeClockMs != null && atTimeClockMs >= t;
  const marker = L.circleMarker([lat, lng], {
    pane: "deployDrops",
    radius: DEPLOY_DROP_RADIUS, // same size as the virtual-deployment drops — one "deployment mark" size
    weight: 0,
    fillColor: color,
    fillOpacity: shown ? 1 : 0, // hidden until the clock reaches the deployment time
    interactive: false,
  }).addTo(markerGroup);
  // Flag so the Instruments-row count (instrumentCount) can tell this fixed dot apart
  // from the platform's head marker: a marker group holds one head PLUS one dot per
  // instrument, so an unfiltered layer count would read double (see instrumentCount).
  marker._deploymentDot = true;
  deploymentDots.push({ marker, t, shown });
}

// Show each deployment dot only once the app clock has reached its deployment time
// (#33). Cheap and idempotent (only touches dots whose visibility flips); called from
// updateClock so scrubbing reveals/hides the dots in step with the tracks.
function updateDeploymentDots(ms) {
  if (ms == null) return;
  for (const d of deploymentDots) {
    const show = ms >= d.t;
    if (show !== d.shown) {
      d.marker.setStyle({ fillOpacity: show ? 1 : 0 });
      d.shown = show;
    }
  }
}

// Build one observed track (drifter or glider) from its raw geometry, honouring the
// current "hide outliers" state (#30): resolve the display geometry, draw the segments,
// and register the clock entry over the same (possibly de-spiked) vertices so the head
// walks the cleaned path. Records a handle so the outlier toggle can tear it down and
// rebuild it. `src` = { group, coords, fixes, key, tip }.
function buildObservedTrack(src) {
  const { group, coords, fixes, key, tip } = src;
  const d = displayTrack(coords, fixes, hideOutliers);
  const segs = addTrackSegments(group, d.coords, d.fixes, key, tip, d.skipSeg, src.color);
  const entry = registerTrackClock(group, segs, d.coords, d.fixes, key, tip);
  const handle = { group, segs, entry };
  observedTrackHandles.push(handle);
  return handle;
}

// Rebuild every observed track for the current `hideOutliers` state (#30): drop the old
// segment polylines, clock entries, and their selection restylers, then rebuild from the
// stored raw sources and re-apply the clock. Cheap on the canvas renderer; the heads and
// deployment dots are untouched (built once, outside this path).
function rebuildObservedTracks() {
  for (const h of observedTrackHandles) {
    for (const seg of h.segs) h.group.removeLayer(seg.line);
    if (h.entry) {
      const idx = trackClockEntries.indexOf(h.entry);
      if (idx >= 0) trackClockEntries.splice(idx, 1);
    }
  }
  dropPartsByOwner("trackseg");
  observedTrackHandles.length = 0;
  for (const src of observedTrackSources) buildObservedTrack(src);
  updateClock(atTimeClockMs);
}
// A drifter head is a per-batch circleMarker: a white outline (matching the glider,
// ship, and virtual-deployment heads — #35) over the batch's fill colour, enlarged
// when selected and desaturated when another instrument is (the fill carries the dim;
// the ring stays white). `_clockHidden` (set by the head's clock controller while the
// app clock predates the track's first fix) wins over every selection state, so a
// selection change can't resurface a head that doesn't exist yet at the displayed instant.
function styleHead(marker, base, state) {
  if (marker._clockHidden) {
    marker.setStyle({ opacity: 0, fillOpacity: 0 });
    marker.setRadius(0);
    return;
  }
  const dim = state === "dim";
  marker.setStyle({
    color: "#fff",
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
    const latestHtml = popupHtml(feature.properties, marker.getLatLng());
    marker.bindTooltip(latestHtml);
    const dNumber = feature.properties?.D_number;
    if (dNumber != null) {
      registerPart(dNumber, (s) => styleHead(marker, base, s));
      marker.on("click", () => selectInstrument(dNumber));
      // Head controller: once this drifter's track is loaded (registerTrackClock),
      // the head rides the app clock — at the interpolated position with that fix's
      // tooltip, parked at the latest fix past the track's end, hidden before its
      // first (the drifter wasn't in the water yet).
      const home = marker.getLatLng();
      registerHead(dNumber, {
        at(latlng, html) {
          marker._clockHidden = false;
          marker.setLatLng(latlng);
          if (html) marker.setTooltipContent(html);
          styleHead(marker, base, stateFor(dNumber));
        },
        latest() {
          this.at(home, latestHtml);
        },
        hide() {
          marker._clockHidden = true;
          styleHead(marker, base, stateFor(dNumber));
        },
      });
      // Single-fix drifters (no track LineString) are clock-driven by this point
      // clock instead of clipTrack (fixes D-509 staying on the map at every clock).
      registerPointHead(dNumber, feature.properties?.date_UTC);
    }
    (groups[batch] ??= L.featureGroup()).addLayer(marker);
  }
  return groups;
}

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
function buildInstrumentRows(div, map, markerGroups, tracksOverlay, vessels = []) {
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
  forecastBatchVisible = (batch) => batchOn[batch] !== false;

  function sync() {
    for (const batch of Object.keys(markerGroups)) {
      toggle(markerGroups[batch], batchOn[batch]);
      toggle(tracksOverlay.groups[batch], batchOn[batch] && tracksMasterOn);
    }
    updateClock(atTimeClockMs); // reconcile the forecast lines to the new selection
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
  outCb.checked = hideOutliers;
  L.DomUtil.create("span", "batch-text", outRow).textContent = "Hide GPS outliers";
  outCb.addEventListener("change", () => {
    hideOutliers = outCb.checked;
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
function buildShadingRows(div, map, shadings, overlays, onShadingChange, overlayAnimation) {
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
function buildControlDock(map, tabs, initialId) {
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
function formatClock(ms) {
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
function buildTimeSlider(map, { t0Ms, spanHours, value, nowMs, onChange, tracks, stepH = 1 }) {
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

// The "Show tracks" master's fallback home when there is no scrubber to host it (no
// CMEMS field): a small standalone chip bottom-centre over the map, same checkbox as
// the scrubber's inline one. `onToggle(on)` fires on every change.
function buildTracksChip(map, { initial, onToggle }) {
  const el = L.DomUtil.create("div", "tracks-chip-control");
  const label = L.DomUtil.create("label", "ts-tracks", el);
  const cb = L.DomUtil.create("input", "", label);
  cb.type = "checkbox";
  cb.checked = !!initial;
  L.DomUtil.create("span", "", label).textContent = "Show tracks";
  cb.addEventListener("change", () => onToggle?.(cb.checked));
  L.DomEvent.disableClickPropagation(el);
  map.getContainer().appendChild(el);
  return el;
}

// Cursor coordinate readout (lower-left): a plain-text chip showing the
// pointer's position in the shared formatLatLon style (lat first, N/S · E/W),
// updated on mousemove. Like the time slider it's a positioned element inside
// the map container (not an L.control), so it hugs the corner. Hidden until the
// pointer enters the map and again on leave.
function buildCursorReadout(map) {
  const el = L.DomUtil.create("div", "cursor-readout hidden");
  map.on("mousemove", (e) => {
    el.textContent = formatLatLon(e.latlng.lat, e.latlng.lng);
    el.classList.remove("hidden");
  });
  map.on("mouseout", () => el.classList.add("hidden"));
  map.getContainer().appendChild(el);
  return el;
}

// Fallback track-line colour, used ONLY when a caller omits the per-instrument
// identity colour. Every real track passes one — the batch / glider / ship colour —
// so a track's line matches its dot and head under the active palette (line = dot =
// head; #35, see docs/palette.md). Kept as a safe default for an identity-less track.
const TRACK_COLOR = "#e07b39";

// --- interactive deploy endpoint (PoC) --------------------------------------
// One dynamic endpoint backs the deploy tool: `POST /api/forecast` takes a
// sequence of (lon, lat, start) seeds — the equally-spaced drops the client lays
// along a clicked path, each with its staggered water-entry time — and advects
// every one through the CMEMS window server-side (one GeoJSON LineString per seed).
// The map and this API are separate endpoints served under one
// origin (the plan-017 gateway: /map and /api as sibling backends), so the base is
// resolved (not hardcoded) by two same-origin rules — no client-controlled override,
// so a crafted `?api=` link can't retarget the seed POST at a hostile host:
//   - in the two-port dev flow (static on :8000), auto-target the API on :8001, so
//     `pixi run serve` + `pixi run serve-api` needs no configuration;
//   - else same-origin, relative to where the map is served. A gateway may mount
//     the instance under a subpath (…/live-test/map/ → …/live-test/api/), so strip
//     the trailing "map/…" and re-root the API alongside it — no origin-root
//     assumption, still crafted-`?api`-proof.
function resolveApi(path) {
  if (location.port === "8000")
    return `${location.protocol}//${location.hostname}:8001${path}`;
  const m = location.pathname.match(/^(.*\/)map\//);
  const prefix = m ? m[1].replace(/\/$/, "") : "";
  return `${prefix}${path}`;
}
const FORECAST_API = resolveApi("/api/forecast");

// The per-request seed cap lives server-side (the /api/forecast request model). The
// client asks the API for it — GET /api/forecast/limits — rather than hardcoding a
// copy, so the cap has one source of truth. Memoised: fetched once, lazily, on the
// first forecasting placement. Any failure resolves to null and the client skips its
// proactive over-cap check, letting the server's bounded request model reject the
// POST instead (rendered by placeDeployment's error path via `apiErrorText`).
let deployLimitsPromise = null;
function getDeployLimits() {
  deployLimitsPromise ??= fetch(resolveApi("/api/forecast/limits"))
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);
  return deployLimitsPromise;
}

// Render a failed /api/forecast response as one status string. Our own HTTPExceptions
// carry a string `detail`; FastAPI's request-validation 422 (e.g. an over-cap seed
// list that slips past the client check) carries an *array* of {loc, msg, …} error
// objects — interpolated raw that reads as the useless "[object Object]", so join
// their messages instead.
function apiErrorText(data, status) {
  const d = data.detail;
  if (Array.isArray(d)) return d.map((e) => e.msg || JSON.stringify(e)).join("; ");
  return d || data.error || `error ${status}`;
}

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

// Trajectories, grouped by `batch` so each batch's lines toggle with that batch's
// markers (see buildInstrumentRows). Each drifter's track is drawn as one polyline
// per fix-to-fix segment (see addTrackSegments), so the whole line is a hover
// target: hovering a segment shows *that fix's* own time, battery, and
// reported/derived velocity — read from the per-vertex `fixes` array that rides
// parallel to `coordinates`, and filled into the same popup as the drifter's main
// marker. Tolerates a `fixes`-less artifact from an older build: the tooltip then
// falls back to the line-level identity (D_number/batch) with an unknown time.
// Segments are interactive: clicking one selects the drifter (see selectInstrument).
// Returns { batch: featureGroup }.
function buildTrackGroups(geojson, markerGroups) {
  const groups = {};
  for (const feature of geojson.features ?? []) {
    if (feature.geometry?.type !== "LineString") continue;
    const { D_number, batch, fixes } = feature.properties ?? {};
    const key = batch ?? "unknown";
    const group = (groups[key] ??= L.featureGroup());
    const tip = (fix, latlng) => popupHtml({ D_number, batch, ...fix }, latlng);
    const coords = feature.geometry.coordinates;
    // Record the raw source + build the track (clock-clipped, outlier-aware). The
    // outlier toggle rebuilds from these sources (plan 035 clock; #30 despike).
    const src = { group, coords, fixes, key: D_number, tip, color: styleForBatch(batch).fillColor };
    observedTrackSources.push(src);
    buildObservedTrack(src);
    // A fixed deployment dot at the track's first fix, in the batch colour (#33) —
    // added to this batch's always-on marker group so the Instruments row governs it.
    // Built once (the first fix is never an outlier), so the outlier toggle leaves it.
    // Clock-gated on the first-fix time so it appears only once deployed.
    addDeploymentDot(
      markerGroups?.[key], coords[0]?.[0], coords[0]?.[1], src.color, Date.parse(fixes?.[0]?.date_UTC)
    );
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
function buildDeployTool(deployLayer, getStartTime, getSpanHours) {
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
  return { state, handleClick, handleDblClick, handleMove, handleAbort, renderBody, setRelease };
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
// The one "deployment mark" radius, shared so real and virtual deployment points read
// at the same size: virtual-deployment drops (drawDrops), real drifter deployment dots
// (addDeploymentDot), and the forecast now-ghost. A selected drop set enlarges by +3.
const DEPLOY_DROP_RADIUS = 3.0;
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
    clipDeployTrack(entry, atTimeClockMs, trackKey === selectedTrack); // initial crop

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

// Register one CLOCK-CLIPPED forecast per real deployed drifter (#22, #34). Each
// role:"track" feature carries `index` (its seed's slot in the POSTed batch) and a
// `start`/`cadence_s`, so vertex i sits at `start + i·cadence`; `seeds[index].dNumber`
// maps the track back to its drifter's head. The forecast is seeded at the drifter's
// last fix, so we keep the FULL advected path [last fix → field end] and let
// clipForecast split it at `nowMs`: the [last fix → now] segment renders DASHED (the
// un-transmitted reporting-lag gap, #34), the [now → end] segment SOLID (the forecast).
// Both carry the drifter's own IDENTITY colour — the same as its observed track and
// head (#35) — so a track reads observed→dashed→solid in one colour; the dash (not a
// hue change) is what marks where the forecast begins. They register for click-to-
// highlight under the drifter's key, so a forecast follows the same select/dim rule as
// its track. Non-interactive polylines in the driftForecast pane (below every marker),
// clock-clipped and shown only while "Show tracks" is on. Each entry also carries a
// small NOW-GHOST dot (see clipForecast): a deployment-dot-sized identity-colour dot
// parked at the drifter's now-position, revealed once the clock scrubs past now — it
// marks the observed→forecast hand-off so the drifter's present position stays fixed
// while its bright head walks the forecast forward. GeoJSON coords are [lon,lat].
function drawDrifterForecastLines(features, layer, seeds, nowMs) {
  for (const f of features) {
    const props = f.properties ?? {};
    if (props.role !== "track") continue;
    const coords = f.geometry?.coordinates ?? [];
    if (coords.length < 2) continue;
    const seed = seeds[props.index];
    const headKey = seed?.dNumber;
    if (headKey == null) continue;
    const startMs = Date.parse(props.start);
    const cadenceMs = (props.cadence_s ?? 0) * 1000;
    if (!Number.isFinite(startMs) || cadenceMs <= 0) continue;
    // Keep the full advected path; clipForecast splits it at now into the dashed bridge
    // (past-of-now) and the solid forecast (future-of-now).
    const lats = [], lngs = [], times = [];
    coords.forEach(([lon, lat], i) => {
      lats.push(lat);
      lngs.push(lon);
      times.push(startMs + i * cadenceMs);
    });
    if (times.length < 2) continue;
    const base = styleForBatch(seed.batch); // identity style (= the observed track/head)
    const mkLine = (dashed) =>
      L.polyline([], {
        pane: "driftForecast",
        color: base.fillColor,
        weight: 2,
        opacity: 1,
        interactive: false,
        ...(dashed ? { dashArray: "6 4" } : {}),
      });
    const line = mkLine(false), bridge = mkLine(true);
    // Same select/dim rule as the observed track: identity colour + wider when this
    // drifter is picked, desaturated when another is. setStyle keeps the bridge's dash.
    registerPart(headKey, (s) => {
      const st = lineStyle(s, base.fillColor);
      line.setStyle(st);
      bridge.setStyle(st);
      if (s === "selected") { line.bringToFront(); bridge.bringToFront(); }
    });
    // Now-ghost: a small deployment-dot-sized dot in the drifter's identity colour,
    // parked at the fixed now-position and hidden until the clock scrubs past now
    // (clipForecast). It marks where the track hands off from observed to forecast, so
    // the bright head can walk on into the forecast without losing the present position.
    // Rides `layer` so it clears with the forecast group.
    const nowPos = interpAtTime(times, lats, lngs, nowMs) ?? [lats[0], lngs[0]];
    const nowGhost = L.circleMarker([nowPos[0], nowPos[1]], {
      pane: "deployDrops", // above the track canvas, below the heads (like the deploy dots)
      radius: DEPLOY_DROP_RADIUS, // = the deployment-mark size
      weight: 0,
      fillColor: base.fillColor,
      fillOpacity: 0,      // revealed once the clock passes now
      interactive: false,
    });
    layer.addLayer(nowGhost);
    forecastClockEntries.push({
      line, bridge, nowGhost, ghostShown: false,
      times, lats, lngs, headKey, batch: seed.batch, group: layer, nowMs,
      tip: `${headKey} · forecast`, lineShown: false, bridgeShown: false,
    });
  }
  // Apply the current clock so a forecast landing mid-scrub places itself at once.
  updateClock(atTimeClockMs);
}

// Fire-and-forget forecast of the real deployed drifters (#22). Seeds every
// in-water drifter (latest.geojson batch === deployment_*, so gliders/floats/xspar/
// waveglider are excluded) at its last fix and forward-advects to the end of the
// CMEMS field via the same /api/forecast endpoint the deploy tool uses. NOT awaited
// by main — the map is already live; the violet forecasts register when the POST
// resolves and then behave under the clock (future-only, clipped to the scrubber, gated
// by "Show tracks", walking each drifter's own head — see drawDrifterForecastLines /
// clipForecast). Gated on getDeployLimits(): a static-only deploy with no /api server
// returns null there, so we add nothing and stay silent (no error on the map).
async function kickDrifterForecasts(latest, map, spanHours, nowMs) {
  if (!latest || !spanHours) return; // no field to advect through
  const limits = await getDeployLimits();
  if (limits == null) return; // dynamic /api/forecast absent — no forecasts, silently

  // `dNumber` rides each seed (client-side only) so the returned track — keyed by its
  // seed `index` — maps back to the drifter's head; it is stripped before the POST.
  let seeds = (latest.features ?? [])
    .filter(
      (f) =>
        f.geometry?.type === "Point" &&
        String(f.properties?.batch).startsWith("deployment_")
    )
    .map((f) => ({
      lon: f.geometry.coordinates[0],
      lat: f.geometry.coordinates[1],
      start: f.properties.date_UTC,
      dNumber: f.properties.D_number,
      batch: f.properties.batch, // so the Instruments row that governs this batch also governs its forecast line
    }))
    .filter((s) => s.start != null && Number.isFinite(Date.parse(s.start)));
  if (!seeds.length) return; // no in-water drifters yet
  // Stay under the per-request seed cap so the whole POST isn't rejected once a large
  // batch is in the water (a full deployment can outnumber the cap). A truncated set
  // just leaves some drifters without a forecast — the same partial-coverage property
  // window-skipped seeds already have — rather than losing every forecast to a 4xx.
  if (limits.max_seeds && seeds.length > limits.max_seeds)
    seeds = seeds.slice(0, limits.max_seeds);

  // One horizon large enough that every seed reaches end-of-data: the server advects
  // each seed from its own start and truncates at the field's last frame, so no track
  // overshoots. Start from the full field span, then clamp to the API's caps so the
  // whole POST isn't rejected: an explicit horizon cap if present, and the seeds ×
  // duration budget (max_seed_hours) divided across our seed count. A clamped horizon
  // just means the longest-lived forecasts stop short of the field edge — a graceful
  // degradation, far better than an all-or-nothing rejection. Seeds whose start
  // predates the window are silently skipped server-side.
  let horizonH = spanHours;
  if (limits.max_horizon_h) horizonH = Math.min(horizonH, limits.max_horizon_h);
  if (limits.max_seed_hours)
    horizonH = Math.min(horizonH, Math.floor(limits.max_seed_hours / seeds.length));
  if (horizonH <= 0) return; // budget too tight for even one hour — nothing to draw

  // A dedicated group so the layer drops in atomically when the response lands.
  const group = L.featureGroup().addTo(map);
  try {
    const resp = await fetch(FORECAST_API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        seeds: seeds.map(({ lon, lat, start }) => ({ lon, lat, start })), // drop dNumber
        horizon_h: horizonH,
        direction: "forward",
      }),
    });
    if (!resp.ok) return; // best-effort: leave the empty group, draw nothing
    const data = await resp.json().catch(() => ({}));
    drawDrifterForecastLines(data.features ?? [], group, seeds, nowMs);
  } catch (err) {
    // Network/API failure is non-fatal — the map stands without violet forecasts.
    console.warn("Drifter forecast unavailable:", err);
  }
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
// frequency instead of being swept away by the background current. dt =
// (displayedFieldTime − t_ref) + a wall-clock loop over [0, 24h): the offset
// anchors the field to the slider's instant (so it scrubs with the shadings),
// the loop sweeps INERTIAL_SPAN_S every INERTIAL_LOOP_S seconds for visual life
// (see startInertialClock). A canvas in the "inertial" pane redraws every frame.
// This is *not* the dropped animated drift dot (a marker walking the
// forecast/hindcast polylines, removed in e9b339c) — it animates a standalone
// particle field.
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
// Decouples the *visual* advection from real time (a visual advection scale).
// With NI-only advection this doubles as the orbit magnifier:
// the ~350 m physical inertial circle is sub-pixel, so this scales it up to a
// visible loop. The orbital *angular* rate is fixed by the 24 h loop, so this
// sets the circle's RADIUS (and thus the tangential speed), not its period —
// larger = bigger, faster circles. Nudge to taste.
const INERTIAL_ADVECT_SCALE = 0.0072;
const INERTIAL_LINE_WIDTH = 1.3; // thin, so overlapping trails don't clump
// Static-snapshot mode (#17): when overlay animation is off, each particle is
// integrated forward this many steps at the *frozen* displayed field time to draw
// its streamline in one pass (the animated mode instead builds trails over frames).
// Enough steps to read as a flow line, few enough to redraw cheaply on scrub/pan.
const INERTIAL_STILL_STEPS = 24;

// Cyan — a non-instrument accent kept clear of the warm-drifter / cool-virtual
// identity palette (docs/palette.md) and the dark flow-overlay streamlines, so the
// near-inertial animation reads as its own layer.
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
function startInertialClock(map, grid, layer, displayedFieldTime) {
  if (!grid) return { setAnimated() {}, refresh() {} };
  const { lo1, lo2, la1, la2 } = grid.header;

  // Anchor the reconstruction's phase to the *displayed* field time rather than
  // free-running from now. The field at absolute time T is amp·exp(i(phase − f·(T −
  // t_ref))); the loop below sweeps its own dt on top for visual life, so the total
  // phase argument uses dt = (displayed − t_ref) + loop. `displayedFieldTime` is a
  // getter read every frame (the slider mutates it), and a +NNh slider step rotates
  // every arrow by f·NNh to that instant. At the now frame the offset is small but
  // not exactly 0: `t_ref` is the hourly (PT1H-m) nearest-now while the displayed
  // now instant is the 6-hourly (PT6H-i) nearest-now, so they can differ by up to
  // ~3 h — a fixed f·Δ rotation vs the old free-run-from-t_ref, not a moving look.
  // With no field time (no slider/meta) the offset is 0.
  const tRefMs = Date.parse(grid.header.t_ref);
  const refOffsetS = () => {
    const displayed = displayedFieldTime?.();
    const ms = displayed ? Date.parse(displayed) : NaN;
    return Number.isFinite(ms) && Number.isFinite(tRefMs) ? (ms - tRefMs) / 1000 : 0;
  };
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

  // Animation gate (#17). When `animated`, the rAF loop below free-runs, building
  // fading trails frame by frame. When off, the loop parks (no rAF) and the field
  // shows a STILL streamline snapshot re-drawn only on discrete state changes
  // (clock scrub, pan/zoom) — so scrubbing no longer competes with a continuous
  // per-frame repaint. `scheduled` keeps at most one rAF in flight either way.
  let animated = true;
  let scheduled = false;
  const schedule = () => {
    if (!scheduled) {
      scheduled = true;
      requestAnimationFrame(tick);
    }
  };

  function tick() {
    scheduled = false;
    if (!animated) return; // static: loop is parked; stills are drawn on demand
    if (!map.hasLayer(layer)) {
      schedule();
      return;
    }
    const ctx = layer.getContext();
    if (!ctx) {
      schedule();
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
      schedule(); // viewport is off the field — nothing to draw
      return;
    }

    const tau01 = ((performance.now() / 1000) % INERTIAL_LOOP_S) / INERTIAL_LOOP_S;
    const dt = refOffsetS() + tau01 * INERTIAL_SPAN_S;

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

    schedule();
  }

  // The frozen snapshot: hard-clear, then draw each particle's instantaneous
  // streamline by integrating it forward INERTIAL_STILL_STEPS at the DISPLAYED
  // field time (dt with no loop sweep). The pool is read, never mutated, so
  // toggling animation back on resumes from the live particle positions.
  const renderStill = () => {
    if (!map.hasLayer(layer)) return;
    const ctx = layer.getContext();
    if (!ctx) return;
    ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
    const vb = viewBounds();
    if (vb.empty) return;
    const dt = refOffsetS();
    ctx.strokeStyle = INERTIAL_COLOR;
    ctx.lineWidth = INERTIAL_LINE_WIDTH;
    ctx.beginPath();
    for (const p of particles) {
      let lon = p.lon;
      let lat = p.lat;
      for (let step = 0; step < INERTIAL_STILL_STEPS; step++) {
        const sample = sampleInertialField(grid, lon, lat, dt);
        if (!sample) break; // ran onto land/off-grid: end this streamline
        const newLat = lat + sample.v * INERTIAL_ADVECT_SCALE;
        const newLon = lon + (sample.u * INERTIAL_ADVECT_SCALE) / Math.cos((lat * Math.PI) / 180);
        const a = map.latLngToContainerPoint([lat, lon]);
        const b = map.latLngToContainerPoint([newLat, newLon]);
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        lon = newLon;
        lat = newLat;
      }
    }
    ctx.stroke();
  };

  // Coalesced still redraw for discrete state changes (clock scrub, pan/zoom): at
  // most one raster per frame, and a no-op while animating (the loop owns the canvas).
  let stillPending = false;
  const refresh = () => {
    if (animated || stillPending) return;
    stillPending = true;
    requestAnimationFrame(() => {
      stillPending = false;
      renderStill();
    });
  };
  map.on("moveend zoomend", refresh);
  // Turning the layer on while static would otherwise show a blank canvas until the
  // next scrub/pan — draw the still as soon as it lands.
  map.on("layeradd", (e) => {
    if (e.layer === layer && !animated) renderStill();
  });

  const setAnimated = (on) => {
    if (on === animated) return;
    animated = on;
    if (on) schedule(); // resume the free-running loop
    else renderStill(); // freeze to a still at the current instant
  };

  schedule(); // animated by default
  return { setAnimated, refresh };
}
// ---------------------------------------------------------------------------

// --- gliders ----------------------------------------------------------------
// The WHIRLS glider-group platforms (see docs/gliders.md): the XSPAR spar buoy,
// the seagliders, the wave gliders, and the profiling floats, built server-side
// into gliders.geojson (a latest Point + a track LineString per platform).
// Coloured by `type` — the operational map's own amber (XSPAR) / blue
// (seaglider) / pink (waveglider) / purple (float) — and drawn with a diamond
// marker so they read apart from the drifters' circles. Not batch-driven, so
// they ride the layer control, not the batch filter. Rows are keyed by `type`,
// so the two floats collapse into one "Floats" instrument row (like the two
// seagliders share theirs), each still selectable by id.
// Colours from the active PALETTE; labels are fixed. Data key stays `seaglider`
// (baked into gliders.geojson + the build pipeline); only the visible row label
// reads "Glider" (#24). See docs/gliders.md.
const GLIDER_STYLES = {
  xspar: { color: PALETTE.xspar, label: "XSPAR" },
  seaglider: { color: PALETTE.seaglider, label: "Glider" },
  waveglider: { color: PALETTE.waveglider, label: "Waveglider" },
  float: { color: PALETTE.float, label: "Float" },
};
const gliderStyle = (type) =>
  GLIDER_STYLES[type] ?? { color: PALETTE.seaglider, label: type ?? "Glider" };

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
      <strong>${escapeHtml(p.id ?? "—")}</strong> <span class="popup-label">${escapeHtml(gliderStyle(p.type).label)}</span><br/>
      <span class="popup-label">Last fix:</span> ${formatFixTime(p.date_UTC)}<br/>
      <span class="popup-label">Speed (derived):</span> ${fmtSpeedMps(p.derived_speed_mps)}<br/>
      <span class="popup-label">Heading (derived):</span> ${fmtDir(p.derived_heading_deg)}<br/>
      <span class="popup-label">Position:</span>
      ${formatLatLon(latlng.lat, latlng.lng)}
    </div>`;
}

// Latest-position markers, one feature group per glider `type`, so each platform
// class is an instrument row in the batch control (see buildInstrumentRows) — the
// same shape as buildBatchGroups for drifters. Diamond marker so gliders read
// apart from the drifters' circles. Returns { type: featureGroup }.
function buildGliderMarkerGroups(geojson) {
  const groups = {};
  for (const feature of geojson.features ?? []) {
    if (feature.geometry?.type !== "Point") continue;
    const { id, type } = feature.properties ?? {};
    const [lng, lat] = feature.geometry.coordinates;
    const latestHtml = gliderPopupHtml(feature.properties, { lat, lng });
    const marker = L.marker([lat, lng], {
      icon: gliderIcon(type),
      zIndexOffset: 500,
    }).bindTooltip(latestHtml);
    if (id != null) {
      // Clock-hide by opacity (the option survives the selection restyle's setIcon)
      // PLUS pointer-events off — an invisible diamond must not hover its tooltip or
      // swallow a click meant for the map (e.g. a deploy-path vertex).
      const setHidden = (hidden) => {
        marker._clockHidden = hidden;
        marker.setOpacity(hidden ? 0 : 1);
        const el = marker.getElement();
        if (el) el.style.pointerEvents = hidden ? "none" : "";
      };
      registerPart(id, (s) => {
        marker.setIcon(gliderIcon(type, s)); // recreates the DOM element…
        if (marker._clockHidden) setHidden(true); // …so re-assert hidden on it
      });
      marker.on("click", () => selectInstrument(id));
      // Head controller (see buildBatchGroups): the diamond rides the app clock along
      // the platform's track.
      const home = L.latLng(lat, lng);
      registerHead(id, {
        at(latlng, html) {
          marker.setLatLng(latlng);
          if (html) marker.setTooltipContent(html);
          setHidden(false);
        },
        latest() {
          this.at(home, latestHtml);
        },
        hide() {
          setHidden(true);
        },
      });
      // Single-fix platforms (no track LineString) ride this point clock, like the
      // single-fix drifters above.
      registerPointHead(id, feature.properties?.date_UTC);
    }
    (groups[type] ??= L.featureGroup()).addLayer(marker);
  }
  return groups;
}

// Glider tracks, one feature group per `type`, keyed like buildGliderMarkerGroups
// so they ride the "True track" overlay against the matching instrument row. Per
// platform (from its track LineString): a per-segment line whose segments each
// carry that fix's hover tooltip — mirroring buildTrackGroups (see
// addTrackSegments), and (like it) registered for click-to-highlight under the
// platform `id`, so clicking a glider's line or its head selects it. Drawn in the
// platform's own identity colour (gliderStyle(type).color) — the same colour as its
// marker and head, so line = dot = head like the drifters (#35, docs/palette.md).
// A platform with a single deployed fix has no LineString and so no track group,
// only its marker. Returns { type: featureGroup }.
function buildGliderTrackGroups(geojson, markerGroups) {
  const groups = {};
  for (const feature of geojson.features ?? []) {
    if (feature.geometry?.type !== "LineString") continue;
    const { id, type, fixes } = feature.properties ?? {};
    const group = (groups[type] ??= L.featureGroup());
    const tip = (fix, latlng) => gliderPopupHtml({ id, type, ...fix }, latlng);
    const coords = feature.geometry.coordinates;
    // Record the raw source + build the track (clock-clipped, outlier-aware); the
    // outlier toggle rebuilds from these sources (plan 035 clock; #30 despike).
    const src = { group, coords, fixes, key: id, tip, color: gliderStyle(type).color };
    observedTrackSources.push(src);
    buildObservedTrack(src);
    // A fixed deployment dot at the track's first fix, in the platform-type colour
    // (#33) — added to this type's always-on marker group (governed by its row).
    // Clock-gated on the first-fix time so it appears only once deployed.
    addDeploymentDot(
      markerGroups?.[type], coords[0]?.[0], coords[0]?.[1], src.color, Date.parse(fixes?.[0]?.date_UTC)
    );
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

// A discrete colour list as a hard-edged (banded) CSS gradient: each colour fills
// an equal 1/n slice with a hard stop at both ends (c₀ 0% 8.33%, c₁ 8.33% 16.67%,
// …), so neighbours never interpolate. The shading rasters snap to N_BINS flat
// colour classes (see N_BINS in _currents.py) and ship those class colours as
// `colorbar`; rendering them banded makes the legend show the raster's exact
// classes, not a smooth ramp.
function hardStopBand(colors) {
  const n = colors.length;
  const stops = [];
  for (let i = 0; i < n; i++) {
    const a = ((i / n) * 100).toFixed(2);
    const b = (((i + 1) / n) * 100).toFixed(2);
    stops.push(`${colors[i]} ${a}% ${b}%`);
  }
  return `linear-gradient(to right, ${stops.join(", ")})`;
}

// The active shading's colour-class legend, as HTML for the Currents dock (below
// the shading radios — moved there from the sidebar, where it was easy to overlook,
// so the scale sits with the control that picks it). `meta.colorbar` is the raster's
// discrete classes (see hardStopBand); `diverging` picks the ζ/f scale (vmin…0…+vmax
// over the signed field) over the speed ramp (0→vmax). Returns "" (which
// `.dock-legend:empty` collapses) when there is no meta — e.g. the "None" shading.
// The legend is constant across the slider; only the displayed time (currents-time,
// in the sidebar) changes as you scrub, so this need only render on shading change.
function shadingLegendHtml(meta, diverging) {
  if (!meta) return "";
  const bar = `<div class="legend-bar" style="background:${hardStopBand(meta.colorbar)}"></div>`;
  const scale = diverging
    ? `<div class="legend-scale"><span>${meta.vmin.toFixed(2)}</span>` +
      `<span>${meta.units}</span><span>+${meta.vmax.toFixed(2)}</span></div>`
    : `<div class="legend-scale"><span>0</span>` +
      `<span>speed (${meta.units})</span><span>${meta.vmax.toFixed(2)}</span></div>`;
  return bar + scale;
}

// The sidebar "Surface currents" panel keeps only the displayed-time readout (the
// colour scale now lives in the Currents dock). Shows the **clock** time; when the
// shown raster/flow frame (snapped to the nearest 12 h) differs from the clock, it
// also names that frame's valid time. `clockIso` is the exact clock instant and
// `frame` the shown frame ({valid_time, file}); both come from the caller's clock.
// Re-called on every scrub.
function renderCurrentsInfo(meta, clockIso, frame) {
  const timeEl = document.getElementById("currents-time");
  if (!meta) {
    timeEl.textContent = "Surface currents unavailable.";
    return;
  }
  const clock = clockIso ?? meta.valid_time;
  let msg = `Showing ${formatFixTime(clock)}`;
  if (frame && Date.parse(frame.valid_time) !== Date.parse(clock)) {
    msg += ` — field frame ${formatFixTime(frame.valid_time)}`;
  }
  timeEl.textContent = msg + " — CMEMS analysis/forecast.";
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
    ["Position", formatLatLon(p.lat, p.lon)],
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
    ["Position", formatLatLon(p.lat, p.lon)],
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
    .map(([k, v]) => `<span class="popup-label">${escapeHtml(k)}:</span> ${escapeHtml(v)}<br/>`)
    .join("");
  return `<div class="popup"><strong>${escapeHtml(vessel.name)}</strong><br/>${rows}</div>`;
}

// A fix is usable only with finite coordinates and a timestamp. The API can emit
// a partial record, and an unguarded `p.lat.toFixed()` downstream would throw and
// (via main's catch) blank the map — so filter at ingestion and the render path
// only ever sees clean fixes.
const isValidFix = (p) =>
  p && Number.isFinite(p.lat) && Number.isFinite(p.lon) && !!p.date;
const byDate = (a, b) => new Date(a.date).getTime() - new Date(b.date).getTime();

// A plain track at the drifter-track width in the vessel's own colour (no cased halo,
// no per-fix dots — plan 034, decision 8), plus a ship marker, in one feature group
// for the given `vessel` spec (colours, name, per-fix rows). The track is drawn as one
// polyline per fix-to-fix segment (like the drifter tracks — addTrackSegments), each
// segment carrying that fix's hover tooltip, so the along-track times stay readable
// without dots. The track and the ship marker follow the app clock (plan 035): the
// track clips to the fixes at or before the clock and the marker rides its clipped
// end (showing the bracketing fix's tooltip), parking at the latest fix when the
// clock is at or past it. Holds the time-sorted position list so live polling appends
// only the new tail: setPositions replaces the whole track, append extends it past
// the last fix.
function makeShipLayer(vessel) {
  const track = L.layerGroup();
  const marker = L.marker([0, 0], {
    pane: "ship",
    icon: shipIcon(vessel.markerColor),
    opacity: 0, // hidden until the first fix lands
  }).bindTooltip("");
  const group = L.featureGroup([track, marker]);
  let positions = [];
  const segs = []; // { line, t0, t1, a, b, on } — parallel to the fix pairs
  let clockEntry = null; // the clock-clipping entry; created once ≥ 2 fixes are in

  // The ship marker doubles as the track's head: moved by clipTrack via this
  // controller, so scrubbing walks the vessel along its own track. Clock-hiding
  // also turns pointer events off — an invisible disc must not hover its tooltip.
  const setHidden = (hidden) => {
    marker.setOpacity(hidden ? 0 : 1);
    const el = marker.getElement();
    if (el) el.style.pointerEvents = hidden ? "none" : "";
  };
  const headKey = `ship:${vessel.name}`;
  registerHead(headKey, {
    at(latlng, html) {
      marker.setLatLng(latlng);
      if (html) marker.setTooltipContent(html);
      setHidden(false);
    },
    latest() {
      const last = positions[positions.length - 1];
      if (!last) return;
      this.at(
        [last.lat, last.lon],
        shipPopupHtml(vessel, last, positions[positions.length - 2])
      );
    },
    hide() {
      setHidden(true);
    },
  });

  // One track segment (prev → p) in the vessel's colour at the drifter-track weight,
  // carrying fix `p`'s tooltip. Interactive for the hover, but its click is swallowed
  // (a ship has no highlight axis) so it doesn't clear a selection.
  function segFor(p, prev) {
    const seg = L.polyline([[prev.lat, prev.lon], [p.lat, p.lon]], {
      // SVG (the default), NOT the drifter/glider track canvas. A second full-viewport
      // canvas above the overlay-pane track canvas would sit on top and swallow every
      // hover/click meant for the drifter/glider tracks below it (a canvas hit-tests
      // its whole rect, transparent or not). The ship tracks are few segments, so SVG
      // costs nothing here and keeps the overlay-pane canvas the topmost track layer.
      pane: "shipTrack",
      color: vessel.trackColor,
      weight: 2,
      opacity: 1,
      bubblingMouseEvents: false,
    });
    seg.bindTooltip(shipPopupHtml(vessel, p, prev), { sticky: true });
    track.addLayer(seg);
    segs.push({
      line: seg,
      t0: Date.parse(prev.date),
      t1: Date.parse(p.date),
      a: [prev.lat, prev.lon],
      b: [p.lat, p.lon],
      on: true,
    });
  }

  // (Re)build the clock-clipping entry from the current fixes (ascending by
  // construction) and re-apply the clock. Created on the first call with ≥ 2 timed
  // fixes, its sample arrays swapped in place after (the ships grow over the cruise).
  function syncClock() {
    const times = [], lats = [], lngs = [], fixes = [];
    for (const p of positions) {
      const t = Date.parse(p.date);
      if (Number.isFinite(t)) {
        times.push(t);
        lats.push(p.lat);
        lngs.push(p.lon);
        fixes.push(p);
      }
    }
    if (times.length < 2) return;
    if (!clockEntry) {
      clockEntry = {
        group: track,
        segs,
        times,
        lats,
        lngs,
        fixes,
        headKey,
        tip: (fix, latlng, i, fs) => shipPopupHtml(vessel, fix, fs[i - 1]),
        trimmed: null,
      };
      trackClockEntries.push(clockEntry);
      trackedHeadKeys.add(headKey);
    } else {
      Object.assign(clockEntry, { times, lats, lngs, fixes });
    }
    clipTrack(clockEntry, atTimeClockMs);
  }

  function showLatest() {
    const last = positions[positions.length - 1];
    if (!last) return;
    const prev = positions[positions.length - 2];
    renderShipInfo(vessel, last, prev);
    // Under two timed fixes there is no clock entry driving the marker yet — show
    // the lone fix directly (the pre-035 behaviour). After that, clipTrack owns it.
    if (!clockEntry) {
      marker.setLatLng([last.lat, last.lon]).setOpacity(1);
      marker.setTooltipContent(shipPopupHtml(vessel, last, prev));
    }
  }

  function setPositions(next) {
    positions = next.filter(isValidFix).sort(byDate);
    track.clearLayers();
    segs.length = 0;
    if (clockEntry) clockEntry.trimmed = null; // its trimmed segment was discarded
    for (let i = 1; i < positions.length; i++)
      segFor(positions[i], positions[i - 1]);
    showLatest();
    syncClock();
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
      segFor(p, prev); // one fresh segment, no full-track rebuild
    }
    showLatest();
    syncClock();
  }

  // The "Show tracks" master toggles the track lines without touching the marker/
  // head: add or remove the `track` layerGroup from the vessel group. clipTrack still
  // walks the (detached) segments so re-showing lands them at the right clock.
  const setTrackShown = (on) => {
    if (on) group.addLayer(track);
    else group.removeLayer(track);
  };

  return {
    group,
    append,
    setTrackShown,
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
          `<div class="ship-row"><span class="popup-label">${escapeHtml(k)}</span><span>${escapeHtml(v)}</span></div>`
      )
      .join("");
}

async function main() {
  // Data-freshness panel: start the live clock immediately, and fill in the
  // build time out of band so a slow/missing build.json can't hold up the map.
  startClock();
  fetchJSON(DATA.build, { optional: true }).then(renderBuildTime);

  // No basemap tiles: the CMEMS current shading covers the ocean the cruise works
  // in, and a slippy-tile basemap is a substantial repeated transfer over the ship's
  // at-sea VSAT link (see data.md) for a backdrop we don't need. The map is the data
  // layers over a plain sea-tone background (styled on #map); maxZoom is bounded so
  // there's no zooming into empty space past the field's resolution.
  const map = L.map("map", {
    center: FALLBACK_CENTER,
    zoom: FALLBACK_ZOOM,
    maxZoom: MAX_ZOOM,
    // Half-level zoom so the wheel/buttons can settle between the old integer
    // stops — the CMEMS pixels upscale crisply, so intermediate scales are useful
    // for reading dense drops/tracks (#27).
    zoomSnap: 0.5,
    zoomDelta: 0.5,
  });

  // Track line weight scales with zoom (see trackWeight): thin lines when zoomed
  // out so overlapping tracks stay separable, a touch heavier zoomed in. Re-run
  // the registered restylers whenever the zoom lands so every segment picks up
  // the new weight.
  trackZoom = map.getZoom();
  map.on("zoomend", () => {
    trackZoom = map.getZoom();
    applySelection();
  });

  const currentOverlays = {};

  // Layer stack, bottom -> top. The governing rule (#20): EVERY line/track pane
  // sits BELOW EVERY marker pane, so no marker is ever occluded by a track — e.g.
  // sg284's glider diamond must never hide under the MD ship track.
  //
  // Below Leaflet's default markerPane (600) — all tracks and forecast lines:
  //   shading 350, flow 355 (static streamline overlay), inertial 360 (raster/animation underlays)
  //   observed drifter/glider track lines: default overlayPane 400 (unchanged)
  //   shipTrack 410      — the ship route + its per-fix dots
  //   driftForecast 420  — the violet real-drifter forecast lines (#22)
  //   deployTracks 430   — the PoC deploy tool's drift lines
  //   deployDrops 440    — the deploy tool's drop discs (placement points of the
  //                        PoC tool, part of the drift geometry — kept just above
  //                        the deploy lines but still below every real marker; if
  //                        they should ever read as markers instead, raise > 600)
  // Marker panes, all above every line above (600 and up):
  //   glider diamonds: default markerPane 600 (buildGliderMarkerGroups gives no
  //                    pane, so they land here — now strictly above all tracks)
  //   drifters 650, ship 660, atTime 670 (moving heads), tooltipPane 680, popup 700
  //
  // The old order interleaved track panes among the marker panes (shipTrack 640 >
  // markerPane 600; deployTracks/Drops 663/664 > drifters 650 / ship 660), which is
  // exactly what let the MD track paint over sg284. Lowering every line pane below
  // 600 fixes it and keeps the pre-deploy-cluster property that motivated shipTrack
  // < drifters (the ship track's early dots still can't intercept marker clicks —
  // it is now below every marker pane, not just the drifters').
  map.createPane("basemap").style.zIndex = 300; // static land/sea mask, below every shading (#29)
  map.createPane("shading").style.zIndex = 350;
  map.createPane("flow").style.zIndex = 355; // static streamline overlay, over the shading colour
  map.createPane("inertial").style.zIndex = 360;
  map.createPane("shipTrack").style.zIndex = 410;
  map.createPane("driftForecast").style.zIndex = 420;
  map.createPane("deployTracks").style.zIndex = 430;
  map.createPane("deployDrops").style.zIndex = 440;
  map.createPane("drifters").style.zIndex = 650;
  map.createPane("ship").style.zIndex = 660;
  // At-time position markers (the virtual drift tracks' moving heads) ride a single
  // top marker pane above every track/disc/marker but below the tooltip pane, so a
  // marker never hides under a line and its own tooltip still floats over it.
  map.createPane("atTime").style.zIndex = 670;

  // Hover tooltips must float above every marker. Leaflet's default tooltipPane is
  // z-index 650 — tied with the drifters pane and *below* the ship pane (660) — so
  // heads would otherwise paint over the tooltip. Lift it above both (still below
  // the 700 popupPane) so a fix's tooltip is never occluded by a marker.
  map.getPane("tooltipPane").style.zIndex = 680;

  // PoC interactive deployment planner: its own layer + a "Deploy" tab in the
  // control dock (built below; its body is deployTool.renderBody). Background
  // clicks/moves are routed to it when armed. `displayedFieldTime` is the valid
  // time of the CMEMS snapshot shown on the map (set once the currents meta loads,
  // below); it is the run start, so a placed deployment's drift begins at the same
  // instant as the field.
  const deployLayer = L.featureGroup().addTo(map);
  // The run start for a placed deployment — the valid time of the displayed CMEMS
  // snapshot (set when the currents meta loads and re-set by the time slider). The
  // Deploy tool reads it live: the dblclick handler passes it, and the CSV-import
  // button pulls it through the getStartTime getter, so both start at the shown field.
  let displayedFieldTime = null;
  const deployTool = buildDeployTool(
    deployLayer, () => displayedFieldTime, () => spanHours
  );

  // Lower-left cursor lon/lat readout (decimal degrees), independent of any data.
  buildCursorReadout(map);

  // Background map clicks: in Deploy mode a click adds a vertex to the path and a
  // double-click finishes it (committing the deployment, its drift locked to
  // displayedFieldTime); otherwise a click clears any track / at-time-marker / drop-set
  // highlight. Track elements, at-time markers, and drop discs set
  // bubblingMouseEvents:false, so their clicks don't reach here — only genuine
  // background clicks do.
  map.on("click", (e) => {
    if (deployTool.state.on) {
      deployTool.handleClick(e.latlng);
      return;
    }
    if (selectedInstrument != null) {
      selectedInstrument = null;
      applySelection();
    }
    if (selectedAtTimeSet != null) {
      selectedAtTimeSet = null;
      applyAtTimeSelection();
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

  // Surface currents, from the absolute-time CMEMS frames: speed + ζ/f shadings and
  // the flow overlay — one frame each per 12 h step across the covered span, the
  // shadings as lossless-WebP rasters on a frozen colour scale, the flow as pre-rendered
  // static streamline WebP (meta.flow_frames). All snap to the app clock together.
  const meta = await fetchJSON(DATA.meta, { optional: true });
  const vorticityMeta = await fetchJSON(DATA.vorticityMeta, { optional: true });
  const inertialField = await fetchJSON(DATA.inertialField, { optional: true });

  // Resolve a frame file under the data dir.
  const frameUrl = (file) => DATA.dataBase + file;

  // Static continent basemap (#29): a highly-compressed gray-land/blue-sea WebP baked at
  // CMEMS resolution, drawn once in the `basemap` pane below every shading. It reuses the
  // shadings' bounds (same grid, co-registered) and needs no scrubbing (land is time-
  // invariant). Gated on its presence: a static/no-CMEMS deploy has no meta, so the map
  // falls back to the CSS #map sea-tone. A shading paints over it; picking "None" reveals
  // the continent underneath.
  if (meta?.landmask && meta?.bounds) {
    L.imageOverlay(frameUrl(meta.landmask), meta.bounds, {
      pane: "basemap",
      className: "crisp-raster",
    }).addTo(map);
  }

  // The index of the frame in a manifest whose valid_time is nearest a given instant
  // (epoch ms). The frames carry no offset any more — the anchor is computed here from
  // their absolute valid_times.
  const nearestFrameIndex = (frames, ms) => {
    let best = 0;
    let bestD = Infinity;
    for (let i = 0; i < (frames?.length ?? 0); i++) {
      const d = Math.abs(Date.parse(frames[i].valid_time) - ms);
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    }
    return best;
  };

  // The app clock spans the shading frames' full range [first valid_time, last
  // valid_time] at 10-minute granularity (the slider steps 10 min; rasters/flow snap to
  // the nearest 12 h frame). It opens at the **now instant** so the scrubber thumb sits
  // exactly under the wall-clock "now" dot (the nearest-frame open used to leave it a
  // little off the dot — #36 follow-up). The shading still snaps to the now-nearest
  // frame; only the clock cursor is the exact hour. `meta.frames` is the canonical span
  // (vorticity/flow share the same times).
  const HOUR_MS = 3600000;
  const clockFrames = meta?.frames ?? [];
  const clockT0 = clockFrames.length ? Date.parse(clockFrames[0].valid_time) : 0;
  const clockTN = clockFrames.length
    ? Date.parse(clockFrames[clockFrames.length - 1].valid_time)
    : 0;
  const spanHours = Math.max(0, Math.round((clockTN - clockT0) / HOUR_MS));
  const nowMs = Date.now();
  const nowFrameIdx = nearestFrameIndex(clockFrames, nowMs);
  // The clock instant as a clean ISO-Z string (no millis), matching the frame shape.
  const clockIso = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");
  // The now instant, clamped into the span and snapped to the scrubber's 10-minute grid
  // (#36 follow-up) — the clock cursor + scrubber thumb both open here (matching the now
  // dot), and the scrubber resolves to 10 minutes, not a whole hour.
  const STEP_H = 1 / 6; // 10-minute scrubber resolution
  const nowOffsetH = clockFrames.length
    ? Math.min(spanHours, Math.max(0, Math.round((nowMs - clockT0) / HOUR_MS / STEP_H) * STEP_H))
    : 0;
  const nowClockMs = clockT0 + nowOffsetH * HOUR_MS;
  // Lock the interactive forecast's start time to the displayed field's instant: the
  // now hour at load, then the exact clock time as the slider moves.
  displayedFieldTime = clockFrames.length ? clockIso(nowClockMs) : null;
  // Seed the app clock so clock-aware elements registered later (drifter tracks on
  // the True-track tick, deployments on placement, ships on first fix) clip and
  // place themselves at the displayed instant right away. Falls back to now when
  // there is no currents field to scrub.
  atTimeClockMs = clockFrames.length ? nowClockMs : Date.now();

  // Warm the browser cache with a **band** of frames around now (±FRAME_PREFETCH
  // indices) so nearby scrubbing is smooth without bulk-prefetching the whole (now
  // ~50, later ~140) frame set. Anything outside the band loads on demand (both a
  // shading raster and the flow overlay fetch naturally on setUrl). Lifted out of the slider
  // block because the shading radios trigger the ζ/f band prefetch on first selection
  // (see onShadingChange), not a Leaflet baselayerchange event.
  const FRAME_PREFETCH = 8;
  const prefetchBand = (fs, center, radius = FRAME_PREFETCH) => {
    const lo = Math.max(0, center - radius);
    const hi = Math.min((fs?.length ?? 0) - 1, center + radius);
    for (let i = lo; i <= hi; i++) new Image().src = frameUrl(fs[i].file);
  };
  let vortPrefetched = false;

  // The two shadings (speed, ζ/f) both fill the same `shading` pane, so only one
  // makes sense at a time — they are mutually exclusive **base layers** (radio
  // buttons) in the Currents control, not overlays. Fully opaque: the raster is
  // the ocean's true colour, not a wash over the basemap (land stays transparent
  // via the PNG's own alpha mask, so the coastline still shows through).
  const currentShading = {};
  // Shading overlays the time slider re-points frame-by-frame: {layer, frames}.
  const shadingLayers = [];

  // Speed shading: a lossless-WebP Mercator raster in the bottom data pane, shown
  // by default, initialised at the now-nearest frame. The image is at the native CMEMS
  // grid resolution (one pixel per cell); `crisp` disables the browser's default
  // bilinear upscaling so the cells render as sharp pixels instead of a smooth blur.
  if (meta && meta.bounds && meta.frames?.length) {
    const speedLayer = L.imageOverlay(frameUrl(meta.frames[nowFrameIdx].file), meta.bounds, {
      pane: "shading",
      className: "crisp-raster",
    });
    speedLayer.addTo(map); // the default-selected shading radio
    currentShading["Current speed"] = speedLayer;
    shadingLayers.push({ layer: speedLayer, frames: meta.frames });
  }
  renderCurrentsInfo(meta, displayedFieldTime, clockFrames[nowFrameIdx]);

  // Vorticity ζ/f: the alternative shading in the same pane, off by default (its
  // radio unselected until picked, which swaps it in for the speed raster).
  if (vorticityMeta && vorticityMeta.bounds && vorticityMeta.frames?.length) {
    const vortNowIdx = nearestFrameIndex(vorticityMeta.frames, nowMs);
    const vorticityLayer = L.imageOverlay(
      frameUrl(vorticityMeta.frames[vortNowIdx].file),
      vorticityMeta.bounds,
      { pane: "shading", className: "crisp-raster" }
    );
    currentShading["Vorticity ζ/f"] = vorticityLayer;
    shadingLayers.push({ layer: vorticityLayer, frames: vorticityMeta.frames });
  }

  // Re-point the flow overlay to frame index `i`; assigned by the flow block below
  // (null until then, and if there is no flow data). Declared here so the clock's
  // onChange can call it even though the flow layer is built further down.
  let scrubFlow = null;
  // The frame index currently shown (the nearest 12 h frame to the clock). Tracked
  // live so the flow block can re-sync once `scrubFlow` is assigned, and so onChange
  // only re-points overlays when the snapped frame actually changes (the clock steps
  // 1 h; the frame changes every 12 h).
  let displayedFrameIndex = nowFrameIdx;

  // Overlay animation gate (#17). The near-inertial canvas runs a continuous rAF loop
  // that repaints every frame regardless of the clock, so during a scrub it competes
  // with the raster/track work and stutters. The "Animate overlays" toggle (in the
  // Currents tab) governs it: off = freeze it to a STILL snapshot of the current frame,
  // redrawn only on discrete changes, no free-running rAF. Default OFF. (The current
  // flow is now a pre-rendered static streamline raster swapped per frame — plan 038 —
  // so it is always fluent and no longer part of this toggle.) `overlayAnimators`
  // collects each animated overlay's setter; `overlayInertial` also feeds the clock's
  // still-refresh.
  let overlaysAnimated = false;
  const overlayAnimators = [];
  let overlayInertial = null; // the near-inertial controller (assigned in its block below)
  const setOverlaysAnimated = (on) => {
    overlaysAnimated = on;
    for (const fn of overlayAnimators) fn(on);
  };

  // One-shot band prefetch of shading + flow frames around now, fired on the clock's
  // *first move* (below) instead of on idle — so a viewer who never touches the clock
  // pays only the now frame, and even then only a ±band, never the whole growing set.
  // `flowPrefetch` is wired up when the flow block runs, null till then.
  let framesPrefetched = false;
  let flowPrefetch = null;

  // The single "Show tracks" master (its checkbox lives in the scrubber below).
  // Flipping it sets module-wide `tracksOn` and reconciles every track LINE at once —
  // drifter + glider (through the Instruments rows, composed with each batch toggle),
  // each vessel's track, and the virtual deployment drift lines — while leaving the
  // heads, drops, and at-time markers alone. `setInstrumentTracks` is wired when the
  // Instruments tab renders; `shipLayers` fills as vessels report. Re-runs the clock so
  // freshly loaded (lazy) track clips place themselves at the displayed instant.
  let setInstrumentTracks = () => {};
  const shipLayers = [];
  // Eventual consistency (#18): the checkbox must flip instantly, never blocking on the
  // heavy line add/remove + re-clip it triggers. So the handler only records the desired
  // state in `tracksOn` and returns — the checkbox repaints immediately. The reconcile
  // (adding/removing many polylines across every batch + glider + ship, then re-clipping
  // via updateClock) is deferred off the event and coalesced: at most one reconcile in
  // flight, and it reads `tracksOn` LIVE at run time, so a rapid on→off→on collapses to
  // the final value (last-write-wins). `setTimeout(0)` — not rAF/microtask — because rAF
  // and microtasks both run before the next paint, which would re-block the very repaint
  // we want; a macrotask yields a paint of the flipped checkbox first, then reconciles.
  let tracksReconcileScheduled = false;
  const setTracksVisible = (on) => {
    tracksOn = on; // control state leads — instantly consistent
    if (tracksReconcileScheduled) return;
    tracksReconcileScheduled = true;
    setTimeout(() => {
      tracksReconcileScheduled = false;
      const desired = tracksOn; // reconcile to whatever the control last said
      for (const s of shipLayers) s.setTrackShown(desired);
      setInstrumentTracks(desired);
      updateClock(atTimeClockMs); // re-apply the clock (heads/lines) for the new state
    }, 0);
  };

  // The app clock: a 1 h-granularity datetime scrubber over the frames' full span.
  // Its value is the hour offset from clockT0; the clock instant is displayedFieldTime
  // exactly. Moving it snaps every registered shading overlay and the flow overlay to
  // the nearest 12 h frame (only when that snapped frame changes), updates the sidebar
  // displayed-time line, and re-locks the deploy tool's start to the displayed field.
  // The near-inertial animation follows too — it reads displayedFieldTime live (see
  // startInertialClock). It also hosts the "Show tracks" master (plan 036). Only built
  // when there is more than one frame to move between; with no field, a standalone chip
  // hosts the master instead (below).
  if (meta?.frames?.length > 1) {
    buildTimeSlider(map, {
      t0Ms: clockT0,
      spanHours,
      stepH: STEP_H, // 10-minute scrubber resolution (#36 follow-up)
      value: nowOffsetH, // open the thumb on the now instant (under the now dot) — #36 follow-up
      nowMs,
      tracks: { initial: true, onToggle: setTracksVisible },
      onChange: (value) => {
        const ms = clockT0 + value * HOUR_MS;
        displayedFieldTime = clockIso(ms);
        // Drive every clock-aware element to the new instant (rAF-throttled, so a
        // fast scrub stays continuous): tracks clip to what has happened by the
        // clock, head markers ride the clipped ends, the deployments' at-time
        // markers move, and the virtual drift lines grow up to the clock.
        updateClock(ms);
        // While overlays are static (#17), the near-inertial snapshot reads the
        // displayed field time — re-draw it for the new instant (coalesced, no-op when
        // animating). The flow snapshot follows its own scrubFlow debounce below.
        overlayInertial?.refresh();
        // The Deploy tab's read-only release readout follows the clock (one clock: the
        // release time IS the displayed field time — plan 034, decision 5).
        deployTool.setRelease(displayedFieldTime);
        // First move: warm a band of shading + flow frames around now (one-shot).
        if (!framesPrefetched) {
          framesPrefetched = true;
          prefetchBand(clockFrames, nowFrameIdx);
          flowPrefetch?.();
        }
        const idx = nearestFrameIndex(clockFrames, ms);
        if (idx !== displayedFrameIndex) {
          displayedFrameIndex = idx;
          for (const s of shadingLayers) {
            const file = s.frames[idx]?.file;
            if (file) s.layer.setUrl(frameUrl(file));
          }
          scrubFlow?.(idx);
        }
        renderCurrentsInfo(meta, displayedFieldTime, clockFrames[idx]);
      },
    });
  } else {
    // No scrubber (no currents field) → host the "Show tracks" master on a standalone
    // chip so tracks stay toggleable in the static/no-CMEMS fallback.
    buildTracksChip(map, { initial: true, onToggle: setTracksVisible });
  }

  // Current flow: a **pre-rendered static streamline** raster per frame
  // (meta.flow_frames — flowvis_<t>Z.webp), Mercator-warped to the same bounds as the
  // speed shading, so it swaps frame-by-frame as a plain imageOverlay exactly like the
  // shadings — fluent time-scrubbing with no client-side particle animation (plan 038,
  // replacing the leaflet-velocity trails). Off by default (its Currents-tab checkbox
  // turns it on); it lives in its own `flow` pane just above the shading, so the dark
  // streamlines read as texture over the colour.
  const flowFrames = meta?.flow_frames ?? [];
  if (flowFrames.length && meta?.bounds) {
    // No `crisp-raster` (unlike the shadings): the streamlines are anti-aliased lines,
    // so the browser's default smooth scaling reads better than nearest-neighbour.
    const flowLayer = L.imageOverlay(frameUrl(flowFrames[nowFrameIdx].file), meta.bounds, {
      pane: "flow",
    });
    currentOverlays["Current flow"] = flowLayer;

    // Re-point to frame `i` — a bare setUrl swap, the same fluent path the shadings use;
    // the browser shows the (prefetched) image instantly, so scrubbing never stalls. The
    // swap is harmless while the overlay is toggled off — it just updates the url the
    // layer shows once added.
    scrubFlow = (i) => {
      const file = flowFrames[i]?.file;
      if (file) flowLayer.setUrl(frameUrl(file));
    };
    // Re-sync to the clock in case the user scrubbed before this block ran.
    if (displayedFrameIndex !== nowFrameIdx) scrubFlow(displayedFrameIndex);

    // Warm a band of flow frames around now on the clock's first move (one-shot, wired to
    // the slider block above) — like the shadings, so an untouched clock costs only the
    // now frame, and even a touched one only a ±band, never the whole growing set.
    flowPrefetch = () => {
      const lo = Math.max(0, nowFrameIdx - FRAME_PREFETCH);
      const hi = Math.min(flowFrames.length - 1, nowFrameIdx + FRAME_PREFETCH);
      for (let i = lo; i <= hi; i++) {
        const file = flowFrames[i]?.file;
        if (file) new Image().src = frameUrl(file);
      }
    };
    if (framesPrefetched) flowPrefetch();
  }

  // Near-inertial animation: flowing particle tracks reconstructed client-side
  // from inertial_field.json (see the "near-inertial animation" block above).
  // Default OFF (buildInertialField never addTo(map)s it) — missing artifact
  // means no layer and no control row. The clock starts once, immediately;
  // it self-gates on map.hasLayer so it costs nothing while the layer is off.
  if (inertialField) {
    const { layer: inertialLayer, grid: inertialGrid } = buildInertialField(inertialField);
    currentOverlays["Near-inertial animation"] = inertialLayer;
    // Follow the slider: the animation anchors its phase to the displayed field time
    // (read live) instead of free-running from "now" — see startInertialClock. The
    // returned controller feeds the "Animate overlays" toggle (#17): setAnimated flips
    // between the free-running loop and a still snapshot, and `refresh` (via
    // overlayInertial) redraws that still on each clock scrub.
    overlayInertial = startInertialClock(map, inertialGrid, inertialLayer, () => displayedFieldTime);
    overlayAnimators.push(overlayInertial.setAnimated);
    // startInertialClock defaults to `animated` (a running rAF loop); sync it to the
    // overlaysAnimated default (off) so the loop parks instead of spinning forever while
    // the layer is off the map. (The overlay + its toggle are disabled pending issue #25;
    // this also stops the wasted background loop until they are re-enabled.)
    overlayInertial.setAnimated(overlaysAnimated);
  }

  // Glider-group platforms (XSPAR buoy + seagliders + wave gliders + floats) are
  // instruments in the same control as the drifter batches: their latest markers join
  // the instrument rows and their tracks ride the "Show tracks" master (keyed by
  // `type`). Optional so a missing file can't blank the map.
  const gliders = await fetchJSON(DATA.gliders, { optional: true });
  const gliderMarkerGroups = gliders ? buildGliderMarkerGroups(gliders) : {};
  const gliderTrackGroups = gliders ? buildGliderTrackGroups(gliders, gliderMarkerGroups) : {};

  // One instrument list governs drifter batches *and* gliders. Marker rows =
  // drifter batches + glider platforms; the "Show tracks" master carries both the
  // drifter trajectories and the glider tracks, so it acts on every instrument at once.
  const markerGroups = { ...batchGroups, ...gliderMarkerGroups };

  // The track groups the "Show tracks" master governs: glider tracks ride
  // gliders.geojson (already fetched for the markers); the drifter true tracks
  // (tracks.geojson) are merged in when they load (eager, below). The master only
  // toggles their line VISIBILITY — the heads follow the clock regardless.
  const tracksOverlay = { groups: { ...gliderTrackGroups } };

  // Markers last, so they sit on top of the shading and flow layers. Added
  // directly; the Instruments tab's rows (not a layer control) govern their
  // visibility, the "Show tracks" master governs their tracks. (sync() reconciles the
  // initial checkbox state, e.g. hiding pre-deploy.)
  for (const group of Object.values(markerGroups)) {
    group.addTo(map);
  }

  // Awaiting-first-fix sidebar.
  renderAwaiting(await fetchJSON(DATA.awaiting, { optional: true }));

  // Render the active shading's colour scale into the Currents dock legend (below
  // the radios), so only the on-map shading shows a scale and it sits with its
  // control instead of in the easy-to-miss sidebar. This is also the seam for the
  // lazy ζ/f-frame prefetch (first time that shading is picked), which used to hang
  // off the removed layer control's baselayerchange event.
  const onShadingChange = (name, legendEl) => {
    if (legendEl) {
      legendEl.innerHTML =
        name === "Current speed"
          ? shadingLegendHtml(meta, false)
          : name === "Vorticity ζ/f"
            ? shadingLegendHtml(vorticityMeta, true)
            : ""; // "None" — collapsed by .dock-legend:empty
    }
    if (name === "Vorticity ζ/f" && !vortPrefetched && vorticityMeta?.frames) {
      vortPrefetched = true;
      prefetchBand(vorticityMeta.frames, nearestFrameIndex(vorticityMeta.frames, nowMs));
    }
  };

  // The two cruise vessels, built eagerly here (before the dock) so their rows can
  // render up front in the merged instrument panel (#24) rather than a separate Ships
  // tab. `makeShipLayer` is a pure constructor — the group exists immediately but is
  // held OFF the map until the vessel's first fix lands (the "no fix ⇒ no dead toggle"
  // contract). Each vessel exposes `setVisible(on)` for its instrument-row checkbox and
  // `reveal()` for the pollers below: `setVisible` records the desired state and applies
  // it only once a fix exists (no-ops before), so toggling the row pre-fix is safe;
  // `reveal` marks the first fix in and applies the last checkbox state. The pollers
  // (pollShip / loadAgulhas) reference these consts, defined further down.
  const ship = makeShipLayer(VESSELS.md);
  const agulhas = makeShipLayer(VESSELS.agulhas);
  ship.setTrackShown(tracksOn); // adopt the current master state (off by default)
  agulhas.setTrackShown(tracksOn);
  shipLayers.push(ship, agulhas);
  const mkVessel = (name, color, shipLayer) => {
    const v = { name, color, wantVisible: true, hasFix: false, ship: shipLayer };
    v.setVisible = (on) => {
      v.wantVisible = on;
      if (on && v.hasFix) v.ship.group.addTo(map);
      else map.removeLayer(v.ship.group);
    };
    v.reveal = () => {
      v.hasFix = true;
      if (v.wantVisible) v.ship.group.addTo(map);
    };
    return v;
  };
  const vessels = [
    mkVessel("M. Dufresne", VESSELS.md.markerColor, ship),
    mkVessel("Agulhas II", VESSELS.agulhas.markerColor, agulhas),
  ];

  // The former top-right boxes are now one tabbed dock (buildControlDock):
  //   • Deploy — the deployment planner + its per-deployment manager;
  //   • Instruments — one panel of marker rows for every platform: drifter batches,
  //     glider-group platforms, AND the two vessels (the former Ships tab, merged in —
  //     #24); tracks ride the scrubber's "Show tracks" master, not a per-tab row;
  //   • Currents — the mutually-exclusive shading radios (None / speed / ζ·f, the
  //     "None" added only when there is a shading to turn off) plus the flow /
  //     near-inertial overlay checkboxes; present only when CMEMS is up.
  // Only one body shows at a time, so the top-right footprint stays bounded on a
  // 13" laptop instead of overflowing into the time slider.
  //
  // Deploy is the app's primary capability (plan 034, D1), so it leads the strip and
  // opens by default — chosen synchronously at build, so the dock never flashes another
  // tab first. The /limits probe runs off the critical path and only DOWNGRADES: when
  // the API is unreachable (getDeployLimits resolves null — the static/Pages fallback),
  // it re-selects Instruments. The Deploy tab stays present either way (placing drops +
  // exporting CSV works without the drift API).
  const hasCurrents =
    Object.keys(currentShading).length || Object.keys(currentOverlays).length;
  if (Object.keys(currentShading).length) currentShading["None"] = L.layerGroup();
  const deployTab = { id: "deploy", label: "Deploy", render: (div) => deployTool.renderBody(div, map) };
  const otherTabs = [
    {
      id: "instruments",
      label: "Instruments",
      render: (div) => {
        // The tracks master (scrubber checkbox) drives this tab's track composition
        // via the returned setTracksOn.
        const inst = buildInstrumentRows(div, map, markerGroups, tracksOverlay, vessels);
        setInstrumentTracks = inst.setTracksOn;
      },
    },
    ...(hasCurrents
      ? [
          {
            id: "currents",
            label: "Currents",
            render: (div) =>
              buildShadingRows(div, map, currentShading, currentOverlays, onShadingChange, {
                initial: overlaysAnimated,
                onToggle: setOverlaysAnimated,
              }),
          },
        ]
      : []),
  ];
  // Build the dock with Deploy leading and open. Deploy is the app's primary
  // capability, so it is the default tab **unconditionally** — the tab is never
  // auto-switched based on API availability (a static/no-API deploy still places
  // drops + exports CSV; only the drift compute needs the forecast API, which the
  // tool reports inline on placement). We only WARM the memoized /limits probe here,
  // off the critical path (a cold/hanging API pod must not stall the dock), so the
  // deploy tool's own getDeployLimits() reuses the fetch.
  const dock = buildControlDock(map, [deployTab, ...otherTabs], "deploy");
  dock.addTo(map);
  getDeployLimits(); // warm the probe; no tab downgrade (#28 follow-up)

  // Eager-load the drifter true tracks so every drifter head follows the app clock
  // from the start (plan 035): the clock clips register on build regardless of the
  // "Show tracks" master (which governs only line visibility), and knowing the full
  // track set is what lets the point-head clock tell single- from multi-fix (fixes
  // D-509 riding the map at every clock). Kept off the critical path (not awaited) —
  // when it lands, merge the groups, reconcile line visibility, mark the point-head
  // set complete, and re-apply the clock.
  fetchJSON(DATA.tracks, { optional: true }).then((tracks) => {
    if (tracks) Object.assign(tracksOverlay.groups, buildTrackGroups(tracks, batchGroups));
    tracksLoaded = true;
    setInstrumentTracks(tracksOn); // reconcile visibility for the merged track groups
    updateClock(atTimeClockMs);    // drive the freshly-registered clips + point heads
  });

  // Violet forecast drift for the real deployed drifters (#22): fire once, async,
  // now that the map + clock span are live. Not awaited — the layer appears when the
  // /api/forecast POST resolves, and silently no-ops if the dynamic API is absent.
  // Pass the clock's "now" (nowClockMs, the scrubber's default slot) as the bridge/
  // forecast split, so the head sits exactly at their junction at the default view (#34).
  kickDrifterForecasts(latest, map, spanHours, nowClockMs || nowMs);

  // R/V Marion Dufresne live track (client-side; Flotte Océanographique Française
  // API). Last, and deliberately not awaited: it is the one third-party fetch, so
  // blocking on it would stall the same-origin layers and controls above behind a
  // slow host. Each poll requests only the window since the last fix and appends.
  // The vessel's marker reveals on the first fix (not before, so an empty/failed start
  // never shows a dead marker) via vessels[0].reveal(), which applies the instrument
  // row's last checkbox state; the interval keeps trying, so a later poll revives the
  // layer once the API recovers. `ship`/`vessels` are built above (before the dock).
  // Polls are skipped while the tab is hidden — and resumed on return — to avoid
  // hammering a third-party host in the background.
  let shipShown = false;
  async function pollShip() {
    if (document.hidden) return;
    ship.append(await fetchShip(ship.lastDate() ?? SHIP.cruiseStart));
    if (!ship.lastDate()) {
      renderShipInfo(VESSELS.md, null);
    } else if (!shipShown) {
      vessels[0].reveal();
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
  // `agulhas`/`vessels` are built above (before the dock); reveal on the first fix.
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
      vessels[1].reveal();
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
