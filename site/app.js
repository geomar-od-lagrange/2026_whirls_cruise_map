/* 2026 Whirls Cruise — drifter map.
 *
 * Static client. Fetches the build artifacts from ./data/ and renders them as
 * Leaflet layers:
 *   latest.geojson                 -> circle markers (on by default)
 *   tracks.geojson                 -> trajectory lines (off by default)
 *   speed.png + currents_meta.json -> surface-speed shading (imageOverlay)
 *   currents.json                  -> leaflet-velocity flow trails (optional)
 *   ftle.geojson + ftle_meta.json  -> FTLE/LCS ridge contour (vector lines)
 *   awaiting.json                  -> sidebar list, no map geometry
 */

const DATA = {
  latest: "./data/latest.geojson",
  tracks: "./data/tracks.geojson",
  awaiting: "./data/awaiting.json",
  currents: "./data/currents.json",
  meta: "./data/currents_meta.json",
  speed: "./data/speed.png",
  ftleGeo: "./data/ftle.geojson",
  ftleMeta: "./data/ftle_meta.json",
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
// `batch` property to group markers. Per-batch colours are still deferred — one
// style for now — so when batch assignment lands, differentiating is a single
// change here.
function styleForBatch(batch) {
  return {
    radius: 6,
    color: "#1f5fa8",
    weight: 1,
    fillColor: "#3a8ddb",
    fillOpacity: 0.85,
  };
}

// Pretty labels for known batch keys; unknown keys (e.g. a future "deployment")
// fall back to the raw value, so new batches surface readably with no code change.
const BATCH_LABELS = {
  pre_deploy: "Pre-deployment",
  deployment: "Deployment",
};
const batchLabel = (batch) => BATCH_LABELS[batch] ?? batch;
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

function popupHtml(props, latlng) {
  const p = props || {};
  return `
    <div class="popup">
      <strong>${p.D_number ?? "—"}</strong><br/>
      <span class="popup-label">Last fix:</span> ${formatFixTime(p.date_UTC)}<br/>
      <span class="popup-label">Battery:</span> ${p.batteryState ?? "—"}<br/>
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

// A checkbox panel (one row per batch) that shows/hides each batch's markers.
// Data-driven from `groups`, so new batches appear automatically. All on by
// default; this control — not the Leaflet layer control — governs drifter
// visibility.
function buildBatchControl(map, groups) {
  const control = L.control({ position: "topright" });
  control.onAdd = () => {
    const div = L.DomUtil.create("div", "batch-control");
    L.DomEvent.disableClickPropagation(div);
    L.DomEvent.disableScrollPropagation(div);
    const title = L.DomUtil.create("h4", "", div);
    title.textContent = "Drifter batches";
    for (const batch of Object.keys(groups).sort()) {
      const group = groups[batch];
      const row = L.DomUtil.create("label", "batch-row", div);
      const cb = L.DomUtil.create("input", "", row);
      cb.type = "checkbox";
      cb.checked = true;
      const swatch = L.DomUtil.create("span", "batch-swatch", row);
      swatch.style.background = styleForBatch(batch).fillColor;
      const text = L.DomUtil.create("span", "batch-text", row);
      text.textContent = `${batchLabel(batch)} (${group.getLayers().length})`;
      cb.addEventListener("change", () => {
        if (cb.checked) group.addTo(map);
        else map.removeLayer(group);
      });
    }
    return div;
  };
  return control;
}

function buildTracksLayer(geojson) {
  return L.geoJSON(geojson, {
    style: { color: "#e07b39", weight: 2, opacity: 0.8 },
  });
}

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

function renderFtleInfo(meta) {
  const timeEl = document.getElementById("ftle-time");
  const legendEl = document.getElementById("ftle-legend");
  if (!timeEl) return;
  if (!meta) {
    timeEl.textContent = "FTLE unavailable.";
    legendEl.innerHTML = "";
    return;
  }
  timeEl.textContent = `Valid ${formatFixTime(meta.valid_time)} — SPASSO backward FTLE.`;
  const lvl = meta.levels && meta.levels[0];
  const color = (lvl && lvl.color) || "#cb181d";
  const value = lvl ? lvl.value.toFixed(2) : "?";
  legendEl.innerHTML =
    '<div class="legend-scale"><span style="display:inline-block;width:20px;' +
    `border-top:2px solid ${color};vertical-align:middle;margin-right:6px"></span>` +
    `<span>LCS ridge (FTLE ≥ ${value} ${meta.units})</span></div>`;
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

const MS_TO_KN = 1.943844;
const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                 "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
const compassPoint = (deg) => COMPASS[Math.round(deg / 22.5) % 16];

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

// Speed/heading of the latest fix relative to the prior one. Null if there is no
// prior fix or a zero time gap. Below ~0.5 kn (≈150 m over a 10-min step) the
// displacement is comparable to GPS scatter, so the bearing is noise — speed is
// still returned but heading is suppressed (ship moored/maneuvering).
const MIN_HEADING_KN = 0.5;
function deriveMotion(positions) {
  if (positions.length < 2) return null;
  const a = positions[positions.length - 2];
  const b = positions[positions.length - 1];
  const dt = (new Date(b.date).getTime() - new Date(a.date).getTime()) / 1000;
  if (!(dt > 0)) return null;
  const speedKn = (haversineMeters(a, b) / dt) * MS_TO_KN;
  return {
    speedKn,
    heading: speedKn >= MIN_HEADING_KN ? initialBearingDeg(a, b) : null,
  };
}

const fmtSpeed = (m) => (m ? `${m.speedKn.toFixed(1)} kn` : null);
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
    ["Heading (derived)", fmtHeading(motion)],
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

// Cased polyline (white halo + dark core, legible on any basemap) plus a ship
// marker, in one feature group. Holds the time-sorted position list so live
// polling can append only the new tail: setPositions replaces the whole track;
// append extends it past the last fix, drawing only the fresh points.
function makeShipLayer() {
  const opts = (color, weight) => ({ pane: "ship", color, weight, opacity: 0.95 });
  const halo = L.polyline([], opts(SHIP.haloColor, 5));
  const core = L.polyline([], opts(SHIP.trackColor, 2.5));
  const marker = L.marker([0, 0], {
    pane: "ship",
    icon: shipIcon(),
    opacity: 0, // hidden until the first fix lands
  }).bindPopup("");
  const group = L.featureGroup([halo, core, marker]);
  let positions = [];

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
    showLatest();
  }

  function append(newer) {
    const valid = newer.filter(isValidFix).sort(byDate);
    if (!positions.length) return setPositions(valid);
    const lastT = new Date(positions[positions.length - 1].date).getTime();
    const fresh = valid.filter((p) => new Date(p.date).getTime() > lastT);
    if (!fresh.length) return;
    for (const p of fresh) {
      positions.push(p);
      halo.addLatLng([p.lat, p.lon]); // extend in place, no full-track rebuild
      core.addLatLng([p.lat, p.lon]);
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

function baseLayers() {
  const esriOcean = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}",
    {
      maxZoom: 13,
      attribution:
        "Tiles &copy; Esri — Sources: Esri, GEBCO, NOAA, National Geographic, and other contributors",
    }
  );
  const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
  });
  return { OpenStreetMap: osm, "Esri Ocean": esriOcean };
}

async function main() {
  const bases = baseLayers();

  const map = L.map("map", {
    center: FALLBACK_CENTER,
    zoom: FALLBACK_ZOOM,
    layers: [bases["OpenStreetMap"]],
  });

  const overlays = {};

  // Layer stack, bottom -> top: speed shading -> FTLE -> flow -> drifters -> ship.
  map.createPane("shading").style.zIndex = 350;
  map.createPane("ftle").style.zIndex = 360;
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

  // FTLE / LCS ridges: a vector iso-FTLE contour over the Cape Basin, above the
  // shading and below the flow. Lon/lat geometry projected by Leaflet (no manual
  // warp), drawn on a canvas renderer in the ftle pane. On by default.
  const ftleGeo = await fetchJSON(DATA.ftleGeo, { optional: true });
  const ftleMeta = await fetchJSON(DATA.ftleMeta, { optional: true });
  if (ftleGeo) {
    const color = ftleMeta?.levels?.[0]?.color ?? "#cb181d";
    const ftleLayer = L.geoJSON(ftleGeo, {
      renderer: L.canvas({ pane: "ftle" }),
      style: { color, weight: 0.8, opacity: 0.85 },
    });
    ftleLayer.addTo(map);
    overlays["FTLE / LCS ridges"] = ftleLayer;
  }
  renderFtleInfo(ftleMeta);

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

  // Trajectories (off by default; optional so a missing file can't blank the map).
  const tracks = await fetchJSON(DATA.tracks, { optional: true });
  if (tracks) {
    overlays["Trajectories"] = buildTracksLayer(tracks);
  }

  // Markers last, so they sit on top of the shading and flow layers. Each batch
  // group is added directly; the batch filter control (not the layer control)
  // governs their visibility.
  for (const group of Object.values(batchGroups)) {
    group.addTo(map);
  }
  buildBatchControl(map, batchGroups).addTo(map);

  // Awaiting-first-fix sidebar.
  renderAwaiting(await fetchJSON(DATA.awaiting, { optional: true }));

  const layersControl = L.control.layers(bases, overlays, { collapsed: false }).addTo(map);

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
