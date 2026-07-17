/* Pure human-facing formatters — time, compass, speed, coordinate, HTML-escape.
 * No app state, no DOM: shared by every popup/readout so all of them render a value
 * the same way. (ES-module split of app.js; FS-1.)
 */

export function formatFixTime(iso) {
  if (!iso) return "unknown";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().replace("T", " ").replace(/\.\d+Z$/, "Z");
}

// 16-point compass label for a bearing in degrees true. Shared by the drifter
// tooltips and the ship readout.
const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                 "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
export const compassPoint = (deg) => COMPASS[Math.round(deg / 22.5) % 16];

export const MS_TO_KN = 1.943844;
// Every speed reads in both units (knots and m/s) so the ship (nautical, knots)
// and the drifters (oceanographic, m/s) are directly comparable. Input is m/s.
export const speedBoth = (mps) => `${(mps * MS_TO_KN).toFixed(1)} kn / ${mps.toFixed(2)} m/s`;

// Drifter velocity formatters. Direction in degrees(+compass); a dash marks a
// value that is absent (no reported field) or underived (a track's first fix, or
// a zero-length step).
export const fmtSpeedMps = (v) => (v != null ? speedBoth(v) : "—");
export const fmtDir = (deg) => {
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
export function formatLatLon(lat, lon) {
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
export function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
