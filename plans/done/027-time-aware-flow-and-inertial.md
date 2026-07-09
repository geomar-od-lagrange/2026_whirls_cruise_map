# 027 — Time-aware flow trails & near-inertial animation

> **Implemented.** See [docs/currents.md](../../docs/currents.md) (flow frames +
> client scrub) and [docs/forecast.md](../../docs/forecast.md) (near-inertial phase
> anchor). This plan is kept for intent/history.

Closes gitlab issue #5. The forecast time slider (`displayedFieldTime`) scrubs the
speed and ζ/f shadings (and locks the deploy run start), but the **current-flow
animation** and the **near-inertial animation** stay pinned to the "now" instant.
Scrubbing to +48 h then mixes two reference times on one map. Make both overlays
share `displayedFieldTime` so every time-dependent layer moves together.

## Near-inertial animation — client only, no pipeline change

The animation already reconstructs `amp·exp(i(phase − f·dt))` analytically, sweeping
`dt` over a free-running loop from `performance.now()`. The decomposition's reference
time `t_ref` already rides in `inertial_field.json`'s header. So the field at absolute
time `T` is just `phase − f·(T − t_ref)` — anchor the loop to the displayed field time
by adding a constant offset:

    refOffsetS = (Date.parse(displayedFieldTime) − Date.parse(header.t_ref)) / 1000
    dt = refOffsetS + tau01 * INERTIAL_SPAN_S

At the now frame `refOffsetS ≈ 0` (behaviour unchanged); at +48 h every arrow's phase
advances by `f·48h` — the field at that instant — while the loop keeps it live. A 12 h
slider step snaps the arrows to the new instant (correct: the field *is* different
there) and animation continues smoothly. `startInertialClock` takes a live
`() => displayedFieldTime` getter; with no slider/meta the getter returns null and the
offset is 0.

## Flow trails — per-frame data + lazy client load

Today `currents.json` is the single now slice. Ship one leaflet-velocity grid per
slider offset (same 8 offsets as the shadings, all from the one window already
fetched — no extra download in the build):

- `_currents.to_velocity_frames(window)` → `(frames, manifest)`, mirroring
  `to_speed_frames`. Files `currents_-12h.json … currents_+72h.json`
  (`frame_filename("currents", off, ext="json")`). Manifest
  `[{offset_h, valid_time, file}]` is merged into `currents_meta.json` as
  `flow_frames`.
- Round velocity values to 4 dp in `_component` (17-sig-digit floats were wasteful):
  the now frame drops ~1 MB → ~0.45 MB raw, and 8 frames ≈ 0.94 MB gzipped —
  comparable to the shading-frame budget. Strictly smaller critical path than before.
- Drop `now_field` (only caller was the build) and the standalone `currents.json`.

Client: load the now flow frame first (critical path), prefetch the rest on idle,
fetch on demand when the user scrubs to an un-prefetched frame. The slider `onChange`
calls a `scrubFlow(i)` that loads frame `i` and `flowLayer.setData`s it; a request
token drops stale late-arriving frames. The pan/zoom re-seed reads the *currently
displayed* frame, not always the now slice.

`site/map/data/` is gitignored (rebuilt each CI run), so there is no orphan
`currents.json` to migrate — the next slow-cron build emits the new artifacts.

## Tests

- `to_velocity_frames`: frame set/names/valid-times match the offsets; manifest shape;
  values rounded.
- Existing inertial/shading contracts unchanged.
