# 044 — Outlier toggle (client-side, no extra download)

> Implemented. See [docs/controls.md](../../docs/controls.md) (the "Hide GPS outliers" row) and [docs/trajectories.md](../../docs/trajectories.md) (the client-side outlier test).

**#30** — a toggle to not display outlier fixes, "without duplicating the
download volume for raw vs. qc tracks." Achievable entirely in `site/map/app.js`:
the data needed to flag outliers **already ships**.

## Why no new download is needed

`tracks.geojson` (the one ~18 MB file, fetched once, `app.js:4084`) already
carries `derived_speed_mps` in every element of each track's per-fix `fixes[]`
array (`_geojson.py:_fix_record` `86–100`), and `addTrackSegments` (`730`)
already receives that array — segment `i` is tagged with `fixes[i]` and its
`t0/t1` from `fixes[i].date_UTC` / `fixes[i+1].date_UTC`. So a speed-based
outlier test and the gap policy run purely on data already in the browser. No
server QC flag exists today (grep of the pipeline for `outlier|qc|flag|spike`
finds only comments; see BACKLOG "GPS despike at ingestion"), so the threshold
decision is ours in JS.

## Outlier definition (client)

Mirror the BACKLOG despike heuristic: a **spike** fix is one whose implied speed
is anomalous on **both** adjacent segments (a lone out-and-back GPS jump), not a
genuine fast leg. `derived_speed_mps[i]` is the mean speed over the segment
**into** fix `i`; the segment **out of** `i` is `derived_speed_mps[i+1]`. So flag
fix `i` as an outlier when **both** `fixes[i].derived_speed_mps > T` **and**
`fixes[i+1].derived_speed_mps > T`.

- Threshold `T`: drifters realistically move < ~2 m/s; observed spikes imply
  15–140 m/s (BACKLOG, D-611/D-612 worst). Pick `T` well above the drift regime
  and the 4-dp quantisation floor — start around **5 m/s**, expose as a constant,
  tune against the real data in the served app.
- Guards (from the data's shape): `derived_speed_mps` is `null` on each track's
  first fix and on coincident/zero-dt fixes — treat `null` as **not** an
  outlier. Values are 4-dp rounded (coords too, ~11 m), so short hops carry
  quantisation noise — `T` well above the floor avoids false positives.

## Gap policy (user decision)

When an outlier fix is hidden, its two touching segments are removed, leaving a
gap between the surrounding kept fixes. Then:

- **Bridge (interpolate) if the gap spans ≤ 24 h** — draw one straight segment
  from the previous kept fix to the next kept fix (Leaflet segments are straight
  lines anyway; "interpolate" = connect the neighbours directly). Position-at-
  time along the bridge stays linear in time, so the clock head still walks it.
- **Blank if the gap spans > 24 h** — draw no bridging segment; the track is
  simply broken there.

The span is `next_kept.date_UTC − prev_kept.date_UTC`. Consecutive outliers
collapse to a single gap between the nearest kept fixes on each side; apply the
same 24 h rule to that combined span.

## Implementation

The toggle rebuilds each affected track from the `fixes[]` array. **Crucially,
it must rebuild the whole clock entry, not just the drawn segments** (review
catch): `addTrackSegments` registers a `registerTrackClock` entry (`537–554`)
that builds its **own** `times/lats/lngs` head-interpolation arrays from the full
`coords`/`fixes` (`538–547`), independent of the visible segments. If we rebuild
only the visible segments but leave the clock entry on the full fixes, the clock
**head still detours to the spike** while scrubbing, and `clipTrack`'s
crossing-segment trim (`602–615`, matching `seg.t0/t1` against the interpolated
position) desyncs. So:

1. Compute the per-fix outlier flag once from `fixes[]` (both-adjacent-segments
   speed test above).
2. Build the **kept** fix series (drop flagged fixes). From it, build BOTH the
   segment set AND the clock entry's `times/lats/lngs` — for each adjacent pair
   of kept fixes emit a segment/leg; where a gap was created by dropped
   outlier(s), include the bridging leg only when the span ≤ 24 h (else omit —
   blank, and the head simply has no position in that interval, same as any
   track gap today).
3. A **"Hide outliers"** master toggle (near the "Show tracks" master in the
   scrubber, or an Instruments-panel control) flips a module flag and, per
   affected track, replaces its `trackClockEntries[]` entry + redraws its canvas
   segments, then re-runs `updateClock`. Recompute on toggle (cheap on canvas)
   rather than shipping two segment sets. The last fix (`i = n−1`) has no
   `fixes[i+1]`, so a terminal spike can't meet the both-segments test — note it.

Default state: **on** (hide outliers) is the cleaner first view; the toggle
reveals the raw fixes. Confirm the default when verifying against real tracks.

Popups/heads: the hidden outlier fixes should also not be the target of a
mid-line hover tooltip when hidden (they have no segment), and the clock head
interpolates across a bridged gap normally; across a blanked (>24 h) gap the head
behaviour is the same as any track gap today.

## Not in scope (noted)

A **server-side** despike (`_clean.py`, a `despiked` flag in the published
`drifters.csv`) would also clean the popup speeds and `latest.geojson` markers —
that's the BACKLOG "GPS despike at ingestion" item, a separate, larger change.
#30 as specced is display-only and client-only; if we later want the server flag,
`_fix_record` (`_geojson.py:86`) is the home for an explicit `outlier` boolean
riding the same single file, and the client would consume that instead of
recomputing.

## Verify

Served app: toggling "Hide outliers" removes the visible GPS spikes (e.g. the
D-611/D-612 out-and-back jumps) and either bridges or blanks each gap per the
24 h rule; toggling back restores them; the clock head walks bridged gaps
smoothly; no second network fetch occurs on toggle.
