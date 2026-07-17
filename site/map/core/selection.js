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
  // `selected` is a plain data property, NOT a getter. It is read once per registered
  // track part inside the restyle sweep (via the caller's `apply` / its `stateFor`) —
  // ~100k reads on a select-all or first paint — and a getter call in that hot loop
  // deoptimises in V8 (megamorphic getters are far slower than a property read), which
  // showed up as a visible stutter. A data property is the fast path; the methods below
  // are the only writers, so the read-anywhere / write-through-methods contract holds.
  const self = {
    /** The currently-selected key, or null. */
    selected: null,
    /** Set the selection without re-applying — for external teardown (e.g. deleting the
     *  selected deployment) where the elements are being removed/re-rendered anyway. */
    set(key) {
      self.selected = key;
    },
    /** Click a key: selecting the current one clears it, another replaces it; re-applies. */
    toggle(key) {
      self.selected = key === self.selected ? null : key;
      apply(self.selected);
    },
    /** Clear the selection (no-op if already clear); re-applies only when it changes. */
    clear() {
      if (self.selected == null) return;
      self.selected = null;
      apply(self.selected);
    },
    /** Re-run the restyle pass without changing the selection (a new part registered, a
     *  zoom-weight change) — the old standalone `applyXSelection()` call. */
    refresh() {
      apply(self.selected);
    },
  };
  return self;
}
