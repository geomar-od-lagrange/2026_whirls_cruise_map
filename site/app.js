/* 2026 Whirls Cruise — drifter map.
 *
 * Static client. Fetches the build artifacts from ./data/ and renders them as
 * Leaflet layers:
 *   latest.geojson                 -> circle markers (on by default)
 *   tracks.geojson                 -> trajectory lines (off by default)
 *   speed.png + currents_meta.json -> surface-speed shading (imageOverlay)
 *   currents.json                  -> leaflet-velocity flow trails (optional)
 *   awaiting.json                  -> sidebar list, no map geometry
 */

const DATA = {
  latest: "./data/latest.geojson",
  tracks: "./data/tracks.geojson",
  awaiting: "./data/awaiting.json",
  currents: "./data/currents.json",
  meta: "./data/currents_meta.json",
  speed: "./data/speed.png",
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
// Markers carry a `batch` property. All per-batch appearance/filtering decisions
// funnel through styleForBatch(); a future batch-filter control hooks in here
// (and reads `feature.properties.batch`) without restructuring the layers.
function styleForBatch(batch) {
  return {
    radius: 6,
    color: "#1f5fa8",
    weight: 1,
    fillColor: "#3a8ddb",
    fillOpacity: 0.85,
  };
}
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

function buildLatestLayer(geojson) {
  return L.geoJSON(geojson, {
    pointToLayer: (feature, latlng) =>
      L.circleMarker(latlng, styleForBatch(feature.properties?.batch)),
    onEachFeature: (feature, layer) => {
      layer.bindPopup(popupHtml(feature.properties, layer.getLatLng()));
    },
  });
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

  // Latest positions (required). Build first to drive the fit, add last so the
  // markers sit above the shading and flow layers.
  const latest = await fetchJSON(DATA.latest);
  const latestLayer = buildLatestLayer(latest);

  const bounds = latestLayer.getBounds();
  if (bounds.isValid()) {
    // Cap zoom well out: the drifters are a tight pre-deployment cluster, so a
    // tight fit hides the surrounding currents. Opens on the Cape Basin.
    map.fitBounds(bounds, { padding: [40, 40], maxZoom: 9 });
  }

  // Surface currents, from one CMEMS field: speed shading + flow trails.
  const meta = await fetchJSON(DATA.meta, { optional: true });
  const currents = await fetchJSON(DATA.currents, { optional: true });

  // Speed shading: a Mercator-warped PNG in a pane below the flow and markers.
  if (meta && meta.bounds) {
    map.createPane("shading");
    map.getPane("shading").style.zIndex = 350;
    const speedLayer = L.imageOverlay(DATA.speed, meta.bounds, {
      pane: "shading",
      opacity: 0.85,
    });
    speedLayer.addTo(map);
    overlays["Current speed"] = speedLayer;
  }
  renderCurrentsInfo(meta);

  // Flow trails: dark->white ramp keyed to speed, so the bright jet pops over the
  // shading. leaflet-velocity defaults assume wind speeds (tens of m/s), so the
  // motion is scaled up and the colour ramp capped at the field's vmax.
  if (currents && currents.length && typeof L.velocityLayer === "function") {
    const flowLayer = L.velocityLayer({
      displayValues: true,
      displayOptions: {
        velocityType: "Surface current",
        displayPosition: "bottomleft",
        displayEmptyString: "No current data",
        speedUnit: "m/s",
      },
      data: currents,
      colorScale: FLOW_COLORS,
      maxVelocity: meta?.vmax ?? 1.5,
      velocityScale: 0.1,
      lineWidth: 1.2,
    });
    flowLayer.addTo(map);
    overlays["Current flow"] = flowLayer;
  }

  // Trajectories (off by default; optional so a missing file can't blank the map).
  const tracks = await fetchJSON(DATA.tracks, { optional: true });
  if (tracks) {
    overlays["Trajectories"] = buildTracksLayer(tracks);
  }

  // Markers last, so they sit on top of the shading and flow layers.
  latestLayer.addTo(map);
  overlays["Latest positions"] = latestLayer;

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
