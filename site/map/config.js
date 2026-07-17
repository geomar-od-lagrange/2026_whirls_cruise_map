/* Static configuration + the instrument palette — pure constants and colour helpers
 * with no dependency on the rest of the app, so every module can import them without a
 * cycle. (ES-module split of the former single-scope app.js; FS-1.)
 */

// Build artifacts the client fetches from ./data/.
export const DATA = {
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
export const PALETTES = {
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
export const PALETTE =
  PALETTES[new URLSearchParams(location.search).get("palette")] ?? PALETTES.ember;

// Darken an identity fill to the drifter circle's thin outline stroke.
export function paletteStroke(hex, f = 0.72) {
  const n = parseInt(hex.slice(1), 16);
  const r = Math.round(((n >> 16) & 255) * f);
  const g = Math.round(((n >> 8) & 255) * f);
  const b = Math.round((n & 255) * f);
  return `#${((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1)}`;
}

// Fallback view if no valid positions are present (cruise staging, Table Bay).
export const FALLBACK_CENTER = [-33.9, 18.43];
export const FALLBACK_ZOOM = 12;
// Deepest zoom (bounded — past the CMEMS 1/12° raster resolution there's no more
// detail, only enlarged pixels, so this is a legibility cap not a data one; #27
// lifts it a little to read dense drops/tracks). Also the top of the track
// line-weight ramp (see trackWeight); passed to L.map so the two stay in sync.
export const MAX_ZOOM = 14;

// The one "deployment mark" radius, shared so real and virtual deployment points read
// at the same size: virtual-deployment drops (drawDrops), real drifter deployment dots
// (addDeploymentDot), and the forecast now-ghost. A selected drop set enlarges by +3.
// Lives here (not in the deploy module) because the observed-drifter layers in app.js
// draw the same mark, so both sides import it rather than one reaching into the other.
export const DEPLOY_DROP_RADIUS = 3.0;

// R/V Marion Dufresne live track. Fetched client-side from the French
// Oceanographic Fleet (Flotte Océanographique Française) localisation API — the
// same source as the IPSL WHIRLS "platform positions" button. CORS-open, no
// auth. Unlike the other layers this is not a build artifact: it polls live so
// the marker tracks the ship between rebuilds. See docs/ship.md.
export const SHIP = {
  positions:
    "https://localisation.flotteoceanographique.fr/api/v2/vessels/MD/positions",
  // Start of the data period: the MD track is cropped here so it doesn't run back
  // through the pre-cruise transit. endDate is now.
  cruiseStart: "2026-06-28T00:00:00.000Z",
  refreshMs: 5 * 60 * 1000, // API reports ~every 10 min; poll at 5.
};
