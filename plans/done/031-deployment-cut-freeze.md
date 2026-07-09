> Implemented. See [docs/trajectories.md](../../docs/trajectories.md)
> ("Truncation at deployment") and [docs/data.md](../../docs/data.md) for the
> current rule.

# Plan 031 — Freeze the deployment cut at the first clear departure

Fixes issue #10: a deployed drifter loses its whole track when the ship passes
back within `NEAR_SHIP_KM` (1 km) of it (deployment_4: D-433/D-434).

## Problem

`_deploy.deployment_starts()` places the cut after the **last** fix within
`NEAR_SHIP_KM` of the vessel, scanning the drifter's **entire** history. That
rule was designed to be conservative against leaking vessel-following fixes
into the free track, but it makes the cut non-monotonic in a bad way: when the
ship later re-approaches an already-drifting drifter (station-keeping,
recovery, deploying the next batch nearby), the "last near-ship fix" jumps
forward to the re-approach and the whole established free-drift track is
blanked (`tracks_geojson` keeps fixes `>= deployed_at`; fewer than 2 fixes →
no LineString). The ship works *among* its own drifters routinely, so tracks
blink out/in for most of the cruise unless this recurrence is removed.

## Rule change

Bound the attachment scan by the drifter's **first clear departure** from the
vessel, then keep the existing conservative rule *inside that window*:

1. Compute the drifter–vessel distance for every fix (as today).
2. Find `first_far` — the index of the first fix farther than `DETACHED_KM`
   (new constant, **5.0 km**) from the vessel. Once a drifter has been 5 km
   from the ship it is deployed for good; nothing at or after `first_far` can
   ever count as "attached".
3. Within `rows[:first_far]`, place the cut after the **last** fix within
   `NEAR_SHIP_KM` — unchanged conservative behaviour: brief 1–5 km excursions
   during the transit leg (ship-track interpolation scatter) are still
   swallowed, and the kept track still starts beyond 1 km by construction.
4. If no fix is ever beyond `DETACHED_KM`, the window is the whole history and
   behaviour is exactly today's (including the "still attached at the latest
   fix" → empty-track branch).

Why 5 km: the existing `NEAR_SHIP_KM` comment already anchors it — "far below
deployed separations (5+ km)". It is comfortably above any plausible
ship-track interpolation error while the drifter is aboard, and comfortably
below the separation a genuinely deployed drifter reaches.

Properties:

- **The reported bug**: D-433/D-434 detached ~a week ago and exceeded 5 km;
  `first_far` sits back at the true deployment, so today's re-approach cannot
  move the cut. Their tracks come back.
- **Recurrence removed**: no later close pass can re-truncate any drifter that
  has once been clearly away — the cut is frozen by construction, no
  persistence of previous builds' values needed (`build.py` keeps recomputing
  from scratch).
- **Symmetric fix for the pre-history case**: a drifter whose recorded history
  starts already >5 km from the ship (`first_far == 0`) now keeps its full
  track even if the ship later passes within 1 km. Under the old rule that
  chance encounter would have truncated everything before it — same bug class.
- **Still-attached drifters** (on deck, awaiting deployment): unchanged, empty
  free track.
- **Trade-off accepted**: if the vessel-position interpolation ever put an
  *attached* drifter spuriously >5 km from the ship, the window would close too
  early and transit fixes could leak into the free track. The old rule was
  robust to that at any error magnitude, the new one up to 5 km. A 5 km
  interpolation error over the MD's fix cadence has not been observed, and the
  alternative (whole tracks vanishing whenever the ship revisits its array) is
  strictly worse.

## Changes

- `src/whirls_cruise_map/_deploy.py` — add `DETACHED_KM = 5.0`; in
  `deployment_starts`, compute distances once per fix, derive `first_far`,
  restrict the `last_attached` scan to `rows[:first_far]`. Update the module
  and function docstrings (the "last fix" language must say *within the
  initial attachment window*, and document the freeze).
- `tests/test_deploy.py` — new; direct unit tests of `deployment_starts`:
  - normal deployment → cut at the first fix after the attached leg;
  - **regression #10**: long free drift (>5 km reached), then ship re-approach
    within 1 km → cut unchanged, track keeps all free-drift fixes;
  - still attached (all fixes near) → cut past the last fix (empty track);
  - never near the vessel → absent from the map (full track kept);
  - 1–5 km excursion during the attached leg, then return near, then deploy →
    excursion swallowed, cut after the *last* near fix (conservative rule
    survives inside the window);
  - history starts far (>5 km), later 1 km chance encounter → absent (no cut);
  - empty ship track → `{}`.
- `docs/` — update the docs that describe the truncation rule (grep for
  `NEAR_SHIP` / "last fix" / deployment truncation: `docs/data.md`,
  `docs/trajectories.md`, `docs/features.md` as applicable) to describe the
  windowed rule. Describe what *is*, no changelog narration.

## Revision after real-data verification

Running the rule above on the real `site/data` snapshot falsified two of its
assumptions, so the implemented rule differs from steps 2–3:

- **"History starts >5 km ⇒ already deployed" is false.** Drifter histories
  open in the Cape Town staging port while the MD track starts ~885 km away
  (ship still en route), so `first_far == 0` emptied the attachment window for
  52 drifters and their cuts vanished — transit and port fixes drew as free
  drift. A departure can only freeze the cut *after* the drifter has been
  within `NEAR_SHIP_KM`; far fixes before any near fix are inert. Consequence:
  a drifter whose history opens far and later passes within 1 km is treated as
  attached at that pass (indistinguishable from port staging by distance
  alone; the conservative choice), instead of keeping its full track — the
  "symmetric fix for the pre-history case" above is dropped.
- **A lone far GPS outlier must not count as the departure.** D-546 has one
  fix 31 km from the vessel between neighbours at 0.25 km; freezing there
  moved the cut 11.5 h early and leaked the overnight vessel-following leg
  into the free track. A clear departure therefore requires *consecutive*
  fixes beyond `DETACHED_KM`.

Implemented rule: walk the fixes in time order, tracking the last fix within
`NEAR_SHIP_KM`; stop at the first pair of consecutive fixes beyond
`DETACHED_KM` that follows any near fix; cut after the last near fix seen.
On the verification snapshot this reproduces every published `deployed_at`.

## Verification (beyond unit tests)

Run the rule on the real `site/data/drifters.csv` + `site/data/ship_marion_dufresne.csv`:

- D-433 / D-434 get a `deployed_at` near their true deployment (days ago, not
  `last_fix + 1s`), and `tracks_geojson` emits deployment_4 LineStrings again;
- deployment_1/2/3 cuts match the currently-published `platforms.csv`
  `deployed_at` values (no collateral movement).
