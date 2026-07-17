/* The dynamic forecast endpoint the client talks to — base resolution, the memoised
 * limits probe, and error rendering. A neutral module (no app state, no DOM) so both
 * the deploy tool and the observed-drifter forecasts import it without cycling through
 * app.js. (ES-module split of app.js; FS-1.)
 *
 * One dynamic endpoint backs the deploy tool: `POST /api/forecast` takes a sequence of
 * (lon, lat, start) seeds — the equally-spaced drops the client lays along a clicked
 * path, each with its staggered water-entry time — and advects every one through the
 * CMEMS window server-side (one GeoJSON LineString per seed). The map and this API are
 * separate endpoints served under one origin (the plan-017 gateway: /map and /api as
 * sibling backends), so the base is resolved (not hardcoded) by two same-origin rules —
 * no client-controlled override, so a crafted `?api=` link can't retarget the seed POST
 * at a hostile host:
 *   - in the two-port dev flow (static on :8000), auto-target the API on :8001, so
 *     `pixi run serve` + `pixi run serve-api` needs no configuration;
 *   - else same-origin, relative to where the map is served. A gateway may mount the
 *     instance under a subpath (…/live-test/map/ → …/live-test/api/), so strip the
 *     trailing "map/…" and re-root the API alongside it — no origin-root assumption,
 *     still crafted-`?api`-proof.
 */

function resolveApi(path) {
  if (location.port === "8000")
    return `${location.protocol}//${location.hostname}:8001${path}`;
  const m = location.pathname.match(/^(.*\/)map\//);
  const prefix = m ? m[1].replace(/\/$/, "") : "";
  return `${prefix}${path}`;
}
export const FORECAST_API = resolveApi("/api/forecast");

// The per-request seed cap lives server-side (the /api/forecast request model). The
// client asks the API for it — GET /api/forecast/limits — rather than hardcoding a
// copy, so the cap has one source of truth. Memoised: fetched once, lazily, on the
// first forecasting placement. Any failure resolves to null and the client skips its
// proactive over-cap check, letting the server's bounded request model reject the
// POST instead (rendered by placeDeployment's error path via `apiErrorText`).
let deployLimitsPromise = null;
export function getDeployLimits() {
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
export function apiErrorText(data, status) {
  const d = data.detail;
  if (Array.isArray(d)) return d.map((e) => e.msg || JSON.stringify(e)).join("; ");
  return d || data.error || `error ${status}`;
}
