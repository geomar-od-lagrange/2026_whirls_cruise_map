# 006 — Drifter batch filter control

> **Done.** Implemented — see [docs/batches.md](../../docs/batches.md).

## Intent

Give the map a GUI control to show/hide drifters by deployment batch. Today
every drifter carries `batch = "pre_deploy"` (assigned in `_clean.py`); during
the cruise drifters will be reassigned to deployment batches as they go in the
water. The control should surface whatever batches are present in the data
without code changes — wire the GUI to the existing `batch` property now, and it
picks up new batches automatically once the assignment logic lands.

Roadmap item 6. The batch *source* (which drifter belongs to which batch) is
deferred — TBD during the cruise. This plan covers only the client GUI.

## Design

Data-driven, client-only. No build/pipeline change; `latest.geojson` already
carries `batch` on every feature.

- **Group markers by batch.** Build one `L.featureGroup` per distinct `batch`
  value found in `latest.geojson`, replacing the single `buildLatestLayer`. A
  combined `L.featureGroup` over all groups drives the initial `fitBounds`.
- **Checkbox filter control.** A custom `L.control` ("Drifter batches",
  top-right) with one checkbox per batch (all checked by default). Toggling a
  box adds/removes that batch's group from the map. Each row shows a colour
  swatch (from `styleForBatch`) and the batch's marker count.
- **Layer control.** Drop the single "Latest positions" overlay from
  `L.control.layers` — the batch control now governs drifter visibility. Tracks,
  speed, flow, and FTLE overlays are unchanged.
- **Labels.** A small `BATCH_LABELS` map prettifies known batch keys
  (`pre_deploy` → "Pre-deployment"); unknown keys fall back to the raw value, so
  a future `deployment` batch shows up readably without a code change (and is
  trivially nameable when it does).

Per-batch *colours* stay deferred: `styleForBatch` keeps returning one style, so
the swatches are honest about today's single style. When batch assignment lands,
colour differentiation is a one-function change there.

## Files

- `site/app.js` — `buildBatchGroups`, batch control, wire-up; remove
  `buildLatestLayer`.
- `site/style.css` — `.batch-control` styling.

## Verification

Serve `site/` and confirm: a "Drifter batches" panel lists "Pre-deployment (N)";
unchecking hides those markers, rechecking restores them; the map still fits to
the drifter cluster on load.
