/* The one clock fan-out (FS-3).
 *
 * `updateClock(ms)` is the single tick that drives every time-aware layer as the scrubber
 * moves, but it did so by reaching inline into six independent registries populated by
 * unrelated sections, with a load-bearing ordering (the forecast clip must run LAST so a
 * drifter's forecast overrides its observed clip / head). Any new time-aware layer had to
 * both grow its own registry and be hand-appended in the middle of that callback.
 *
 * This inverts it: each layer registers a tick handler at build time with an explicit
 * PRIORITY, and `tickClock(ms)` runs them in ascending priority. The forecast-last rule
 * is now a declared priority, not a comment about call order. Lower priority runs first.
 */
const handlers = []; // { priority, tick(ms) }, kept sorted by priority ascending

/** Register a per-tick handler at `priority` (lower runs first). Returns an unregister
 *  function. Layers call this once at build time. */
export function registerClockTick(priority, tick) {
  handlers.push({ priority, tick });
  handlers.sort((a, b) => a.priority - b.priority);
  return () => {
    const i = handlers.indexOf(handlers.find((h) => h.tick === tick));
    if (i >= 0) handlers.splice(i, 1);
  };
}

/** Run every registered handler in ascending-priority order for clock time `ms`. */
export function tickClock(ms) {
  for (const h of handlers) h.tick(ms);
}

/** Named priorities for the built-in time-aware layers, so the ordering is one
 *  legible table rather than magic numbers at six scattered registration sites. The
 *  forecast clip is deliberately last (it overrides the observed clip + head). */
export const CLOCK_PRIORITY = {
  atTimeMarkers: 10,
  observedTracks: 20,
  pointHeads: 30,
  deploymentDots: 40,
  deployDrift: 50,
  forecast: 60, // LAST — a drifter's forecast overrides its observed clip / point head
};
