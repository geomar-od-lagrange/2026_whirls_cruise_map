# 048 — Hint that a fresh deployment's drift needs a scrub (#38)

> **Done.** Implemented; behaviour documented in
> [docs/deploy_tool.md](../../docs/deploy_tool.md) (*A fresh deployment needs a scrub*)
> and [docs/controls.md](../../docs/controls.md) (Deploy tab).

## Problem

A placed virtual deployment draws its drop discs immediately but its drift lines
are **cropped at the app clock** — a forward run grows release→clock, a backward
run clock→release. At placement the clock is *always* on the release edge (the
release time is read-only and locked to the displayed field instant, and the
seed's `start` is that same instant), so every fresh deployment shows drops but a
**zero-length line and no at-time head** until the user scrubs the clock away from
release. Users overlook this and read the tool as having done nothing.

We keep this cropping behaviour (it is what lets a scrub *animate* the drift). We
only add a hint that points the user at the time slider.

## Two hints, two gaze locations

The failure is deterministic — release == clock holds at every commit — so neither
hint needs runtime detection. The wording is deliberately direction-agnostic (either
scrub reveals the drift, and a run can go both ways), shared from one `SCRUB_HINT`
constant. Both are pure additions; neither touches `clipDeployTrack`.

1. **Status-line clause (dock).** `commitDeployment`'s final `done()` appends
   `· drag the clock to draw the drift` whenever a run produced a track. It is the
   durable record; it self-clears on the next status write. Hook: `commitDeployment`
   notes loop + `done()` (`app.js`).

2. **Finish tooltip (map).** On the finishing double-click, a transient Leaflet
   tooltip anchored at the finish `latlng` reads `drops placed · drag the clock to
   draw the drift`. It lands where the eye already is (the last vertex), covering the
   status line's one weakness (eye-on-map). Map-anchored (not cursor-hover) so it
   survives pan/zoom and works on touch; auto-dismisses on a timer and is cleared when
   a new path starts, on abort, on disarm, and on Clear. Hook: `handleDblClick`
   (`app.js`) + a small `.deploy-finish-hint` style.

Wording is about the *model* (drops placed, drift plays out over time), not the
result count — at the instant of finish the POST hasn't returned and the line is
zero-length regardless.

## Not doing (this pass)

Auto-nudging the scrubber, thumb glow/pulse, first-run localStorage coach mark,
per-manager-row scrub buttons — all need a scrubber *setter* plumbed into the
deploy tool (it only reads the clock today) or new persistence. Deferred; the two
text/tooltip hints above are the cheap, always-correct first pass.

## Docs

Update `docs/deploy_tool.md` (the clock-clips-the-trail section) and
`docs/controls.md` (Deploy tab) to describe both hints. Also fix the stale
`controls.md` line describing a "slow pulsing ring" on the now-dot — that pulse
was removed in #36 and no `@keyframes` remain in the stylesheet.
