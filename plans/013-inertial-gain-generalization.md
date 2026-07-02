# Does the inertial-amplitude gain generalize?

**Status: DRAFT / open investigation.** Gates the "gain" half of Phase 2 in
[012-near-inertial-forecast.md](012-near-inertial-forecast.md). Offline only
(`tmp_ni/`); no `src/` change until this is answered.

## The question

Phase 0 (see 012) found that time-dependent CMEMS advection gets the near-inertial
(NI) **phase and rotation sense right** but only **~0.31–0.45 of the amplitude** at
the three Deployment-2 drifters, over one ~20 h window at one place. The tempting
fix is a single scalar gain (~2.3) on the CMEMS-derived inertial component. **But
Deployment 1 has corners too, elsewhere and at other times — so the load-bearing
question is whether one scalar generalizes.** If it doesn't, a global gain would
*over*-correct some drifters and *under*-correct others, arguably worse than the
honest un-gained field.

A single number would have to hold across three axes at once, and there is reason
to doubt each:

- **Space / deployment.** D1 sits in a different part of the Cape Basin (different
  eddy field, mixed-layer depth, distance to the Agulhas jet). Model NI
  under-energization is not spatially uniform — it tracks resolution, MLD, and how
  each wind event projected onto the local inertial band.
- **Time.** NI is episodic — pumped by wind events and decaying over days. The
  sim/obs ratio at a quiet time (both small, noisy) need not match a ratio right
  after a strong event. A gain fit in one window may not transfer to another.
- **Physics the scalar lumps together.** The "gain" silently conflates (a) the
  model's NI under-energization, (b) the drifter drogue sampling *below* the
  surface level the model uses (NI is surface-intensified, so this pushes the ratio
  the *other* way and is itself depth/MLD-dependent), and (c) any non-inertial
  sub-mesoscale in the observed residual (D-559's cusp looked less circular than
  D-531/543's clean loops). A constant multiplier cannot represent all three.

## What un-gained already gives (the safe default)

The shipped Phase 1 field is **phase-correct and directionally honest** with **no
calibration** — "more correct than frozen," carrying ~40 % of the inertial
excursion. That is the fallback the whole investigation is measured against: a gain
earns its place only if it demonstrably improves skill *without* a fragile
per-drifter fit. If generalization fails, we keep un-gained CMEMS and simply state
the amplitude caveat.

## Investigation (all offline in `tmp_ni/`)

Reuse the Phase-0 machinery (`phase0_compare.py`, `phase0_inertial.py`: seed at the
observed position, advect ±12 h, remove the mean drift, compare the residual
inertial loop).

1. **Broaden the sample.** Run the corner comparison for **every drifter with
   enough free-drift track — D1 and D2 — over several ~1-inertial-period windows**
   spread across each record (not just one anchor). Per (drifter, window) record:
   the **amplitude ratio** sim/obs and the **phase lag** of the inertial residual.
2. **Look at the distribution, not the mean.** Is the ratio tightly clustered (→ a
   single global gain is defensible) or spread (→ it isn't)? Same for phase lag — a
   gain only helps if the phase stays right; if phase drifts window-to-window, no
   scalar rescue works.
3. **Regress the ratio on candidate drivers** — latitude / local *f*, mixed-layer
   depth (MIMOC or a CMEMS MLD field), time since the last strong wind event, eddy
   strain / distance to the jet, distance to coast. A driver that explains the
   spread turns a fragile scalar into a **physically parameterized gain** (still
   cheap; no wind model, no slab).
4. **Confirm we're scaling the right thing.** Rotary-decompose the observed
   residual and verify the energy we intend to amplify is the counter-clockwise
   (SH) inertial band, not sub-mesoscale contamination — so the gain doesn't inflate
   non-inertial motion (relevant for D-559-type cusps).

## Decision branches

- **A — ratio clusters tight (say within ±25 %) and phase is stable.** Ship a
  **single global gain**, value + uncertainty stated, as a tunable parameter (never
  a silent constant). Applied via the Phase-2 `(mean, A, φ)` decomposition:
  `u = mean + gain·A·(rotating vector)`.
- **B — ratio varies but tracks an identifiable driver** (e.g. MLD or *f*). Use a
  **parameterized gain** `gain(x, y)` from that relation — a cheap field, no wind
  forcing.
- **C — ratio scattered or phase unstable.** **No gain.** Keep the un-gained
  time-dependent field (phase-right, honest), document the amplitude shortfall. The
  parked Pollard–Millard slab ([inertial_slab_model.md](inertial_slab_model.md))
  returns to the table only if a *physical* amplitude model is genuinely wanted over
  an empirical drifter fit — and even then it faces the same generalization test.

## Guardrails

- Never let a gain fit on a handful of drifters at one time drive an operational
  multiplier unseen: expose it, log the value each build, and default to un-gained
  if it hasn't been validated for the current period/region.
- Define skill as **reduction in track separation vs the un-gained baseline**, not
  amplitude-match alone — a gain that matches amplitude but worsens the actual
  forecast position is not an improvement.
- This is a drifter calibration of a model field, not a physical NI prediction;
  present it as such in the docs/sidebar (don't over-claim once a gain is in).
