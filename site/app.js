/* 2026 Whirls Cruise — drifter map.
 *
 * Static client. Fetches four artifacts from ./data/ (built by the Python
 * pipeline) and renders them as Leaflet layers:
 *   latest.geojson  -> circle markers (on by default)
 *   tracks.geojson  -> trajectory lines (off by default)
 *   currents.json   -> leaflet-velocity overlay (optional; absent => skipped)
 *   awaiting.json   -> sidebar list, no map geometry
 */

const DATA = {
  latest: "./data/latest.geojson",
  tracks: "./data/tracks.geojson",
  awaiting: "./data/awaiting.json",
  currents: "./data/currents.json",
};

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

  // Latest positions (required, on by default).
  const latest = await fetchJSON(DATA.latest);
  const latestLayer = buildLatestLayer(latest);
  latestLayer.addTo(map);
  overlays["Latest positions"] = latestLayer;

  const bounds = latestLayer.getBounds();
  if (bounds.isValid()) {
    // Cap zoom well out: the drifters are a tight pre-deployment cluster, so a
    // tight fit hides the surrounding currents. Opens on the Cape Basin.
    map.fitBounds(bounds, { padding: [40, 40], maxZoom: 9 });
  }

  // Trajectories (off by default; optional so a missing file can't blank the map).
  const tracks = await fetchJSON(DATA.tracks, { optional: true });
  if (tracks) {
    overlays["Trajectories"] = buildTracksLayer(tracks);
  }

  // Awaiting-first-fix sidebar.
  renderAwaiting(await fetchJSON(DATA.awaiting, { optional: true }));

  // Currents — today's CMEMS surface flow (optional; skip if missing/empty).
  // On by default so it's visible without hunting in the layers control.
  const currents = await fetchJSON(DATA.currents, { optional: true });
  if (currents && currents.length && typeof L.velocityLayer === "function") {
    const currentsLayer = L.velocityLayer({
      displayValues: true,
      displayOptions: {
        velocityType: "Surface current",
        displayPosition: "bottomleft",
        displayEmptyString: "No current data",
        speedUnit: "m/s",
      },
      data: currents,
      // leaflet-velocity defaults assume wind speeds (tens of m/s); ocean
      // surface currents are ~0-1.5 m/s, so scale the motion up and cap the
      // colour ramp low enough that the flow is actually legible.
      maxVelocity: 1.5,
      velocityScale: 0.1,
    });
    currentsLayer.addTo(map);
    overlays["Currents"] = currentsLayer;
  }

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
