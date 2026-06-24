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

async function fetchJSON(url, { optional = false } = {}) {
  const resp = await fetch(url);
  if (!resp.ok) {
    if (optional) return null;
    throw new Error(`${url}: HTTP ${resp.status}`);
  }
  return resp.json();
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

  // Layer stack, bottom -> top: speed shading -> FTLE -> flow -> drifter markers.
  map.createPane("shading").style.zIndex = 350;
  map.createPane("ftle").style.zIndex = 360;
  map.createPane("drifters").style.zIndex = 650;

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

  L.control.layers(bases, overlays, { collapsed: false }).addTo(map);
}

main().catch((err) => {
  console.error("Failed to initialise map:", err);
  const el = document.getElementById("map");
  if (el) {
    el.innerHTML =
      '<div class="map-error">Could not load map data. See console for details.</div>';
  }
});
