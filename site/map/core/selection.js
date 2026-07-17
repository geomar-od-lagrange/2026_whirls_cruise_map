/* One click-highlight selection helper (FS-2).
 *
 * Four near-identical highlight subsystems — instrument tracks, at-time marker sets,
 * virtual-deployment drop-sets, and virtual-deployment tracks — each hand-rolled the same
 * shape: a module-level `let selectedX`, a `selectX(key)` toggle, an `applyXSelection()`
 * restyle loop, and a clear branch in the background-click handler. Four copies quadruple
 * the surface for state bugs (a missed clear, a toggle that forgets to re-apply).
 *
 * `makeSelection(apply)` owns the one piece they share — the currently-selected key and
 * its toggle/clear/refresh lifecycle — while each caller keeps its own `apply(selected)`
 * restyle pass (which reads the passed key to decide each entry's appearance, so a
 * 3-state instrument dim/selected/normal and a 2-state marker both fit unchanged). The
 * background-click handler then clears every subsystem by iterating the instances.
 */
export function makeSelection(apply) {
  let selected = null;
  return {
    /** The currently-selected key, or null. */
    get selected() {
      return selected;
    },
    /** Set the selection without re-applying — for external teardown (e.g. deleting the
     *  selected deployment) where the elements are being removed/re-rendered anyway. */
    set(key) {
      selected = key;
    },
    /** Click a key: selecting the current one clears it, another replaces it; re-applies. */
    toggle(key) {
      selected = key === selected ? null : key;
      apply(selected);
    },
    /** Clear the selection (no-op if already clear); re-applies only when it changes. */
    clear() {
      if (selected == null) return;
      selected = null;
      apply(selected);
    },
    /** Re-run the restyle pass without changing the selection (a new part registered, a
     *  zoom-weight change) — the old standalone `applyXSelection()` call. */
    refresh() {
      apply(selected);
    },
  };
}
