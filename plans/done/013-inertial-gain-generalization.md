# Does the inertial-amplitude gain generalize?

> **Resolved: Branch C — no gain** (2026-07-03). Across all 23 deployed
> drifters the sim/obs near-inertial amplitude ratio spreads by a factor ~3
> and tracks no driver usable at forecast time, and the slab alternative fails
> independently — so the un-gained time-dependent field ships, with the gain
> exposed as a parameter defaulting to 1.0 (`_inertial.GAIN`). Doc
> counterpart: [docs/forecast.md](../../docs/forecast.md). The analysis lived
> in untracked `tmp_ni/`; the Findings section below is the durable record.

## The question

Phase 0 (see [012](../012-near-inertial-forecast.md)) found that
time-dependent CMEMS advection gets the near-inertial (NI) **phase and
rotation sense right** but only **~0.31–0.45 of the amplitude** at the three
Deployment-2 drifters, over one ~20 h window at one place. The tempting fix
was a single scalar gain (~2.3) on the CMEMS-derived inertial component. **But
Deployment 1 has corners too, elsewhere and at other times — so the
load-bearing question was whether one scalar generalizes.** If it doesn't, a
global gain would *over*-correct some drifters and *under*-correct others,
arguably worse than the honest un-gained field.

A single number would have to hold across three axes at once, and there was
reason to doubt each:

- **Space / deployment.** D1 sits in a different part of the Cape Basin
  (different eddy field, mixed-layer depth, distance to the Agulhas jet).
  Model NI under-energization is not spatially uniform — it tracks resolution,
  MLD, and how each wind event projected onto the local inertial band.
- **Time.** NI is episodic — pumped by wind events and decaying over days. The
  sim/obs ratio at a quiet time (both small, noisy) need not match a ratio
  right after a strong event.
- **Physics the scalar lumps together.** The "gain" silently conflates (a) the
  model's NI under-energization, (b) the drifter drogue sampling *below* the
  surface level the model uses (NI is surface-intensified, so this pushes the
  ratio the *other* way and is itself depth/MLD-dependent), and (c) any
  non-inertial sub-mesoscale in the observed residual. A constant multiplier
  cannot represent all three.

## Decision branches (as posed)

- **A — ratio clusters tight (say within ±25 %) and phase is stable** → a
  single global gain, value + uncertainty stated, applied via the Phase-2
  `(mean, A, φ)` decomposition.
- **B — ratio varies but tracks an identifiable driver** (e.g. MLD or *f*) → a
  parameterized `gain(x, y)` — a cheap field, no wind forcing.
- **C — ratio scattered or phase unstable** → **no gain**; keep the un-gained
  time-dependent field (phase-right, honest), document the amplitude
  shortfall.

## Findings (2026-07-03; offline in `tmp_ni/`, untracked — numbers recorded here)

### Survey: CMEMS time-dependent advection vs all 23 deployed drifters

**Method.** For every Deployment-1 (20) and Deployment-2 (3) drifter, sliding
windows of ~1 local inertial period (T_f ≈ 20 h, 4 h stride) across the
free-drift record (2026-07-01 → 07-02; ~24–34 h per drifter). Per window: seed
at the observed position at window center, advect ±T_f/2 through the hourly
CMEMS window (the same `_forecast` machinery the site uses), detrend both
tracks by a best-fit constant velocity, compare the residual (inertial) loops.
67 (drifter, window) samples; 58 kept at complex coherence |c| ≥ 0.6.

- **Amplitude ratio sim/obs:** overall median **0.65**, IQR [0.46, 0.83];
  **D1 median 0.66 vs D2 median 0.40**; clean-sample range 0.37–1.25 — a
  factor ~3 (the strict |c| ≥ 0.9 subset, n = 31, shows the same spread).
  Phase-0's gain ~2.3 came from the most under-energized site/time sampled;
  applied globally it would over-correct the median D1 window by ~50 % and
  push ~1/6 of windows to 2–3× the observed amplitude.
- **Phase lag:** circular mean +6.1°, circular std 26.8° (sim leads); rotation
  sense correct (obs CCW in 61/67 windows, sim in 67/67).
- **Drivers:** pooled correlations of the ratio with latitude, longitude, and
  CMEMS MLD are all |r| ≤ 0.05. Within D1: lat −0.48, lon −0.40, MLD +0.39 —
  but D2 has the deepest MLD (~190–204 m vs D1's ~62–143 m) *and* the lowest
  ratio, so the MLD relation has the wrong sign to generalize. What does
  correlate: the observed amplitude itself (r ≈ −0.6) and time (r ≈ −0.4; the
  ratio declines within-record for 13/14 drifters) — CMEMS carries a roughly
  flat NI floor while the real NI is episodic and patchy. Neither is usable as
  a forecast-time gain field.
- **Data quality:** single-fix out-and-back GPS spikes (implied speeds
  15–140 m/s) found and despiked in D-577, D-602, D-606, D-610, D-611 (4),
  D-612 (4), D-630 (2); D-570, D-603 and D-606 dropped for 5–21 h telemetry
  holes. (→ [BACKLOG](../BACKLOG.md): despike at ingestion.)
- **Caveats:** one ~38 h span, one ~1°×1.5° corner of the Cape Basin, heavily
  overlapping windows (effective N ≪ 58), and D2 is only 3 drifters.

### Slab test: Pollard–Millard slab vs CMEMS NI vs drifters

**Method.** Exact-exponential hourly integration of the Pollard–Millard slab
(ε = 1/(5 d)) on GFS-backed hourly 10 m winds (Open-Meteo; the NOMADS OpenDAP
service is retired), spin-up from 06-17 (result insensitive to spin-up start,
< 2 %), τ = ρ_a·C_d·|U|U with C_d = 1.2·10⁻³ (results linear in C_d and 1/H),
run at both cluster centroids. CMEMS NI via complex demodulation of an hourly
point series (06-21 → 07-03); observed NI from detrended drifter velocities.

- **D2 (11.48°E, −37.36°S):** CMEMS-model MLD mean 138.8 m (daily range
  63–203 m). slab/obs = 0.22 at that H; slab/CMEMS = 0.42; CMEMS/obs = 0.35
  (demodulation convention) / 0.53 (rotary-RMS). The mixed-layer depth that
  would close the gap: H* = 30–31 m.
- **D1 centroid (12.41°E, −36.63°S):** MLD mean 77.2 m (41–123 m). slab/obs =
  0.30–0.49; slab/CMEMS = 0.46–0.99. H* = 23–38 m.
- **H* ≈ 23–38 m is implausibly shallow** for the austral-winter Cape Basin
  (the model MLD never drops below ~41 m in the window) — no defensible
  (H, C_d) pair closes the amplitude gap.
- **Phase / double-counting:** slab NI vs CMEMS NI during the drifter window
  correlate at |ρ| = 0.84–0.88 with lag ≈ 0 h — CMEMS's NI *is* the
  wind-forced slab-like response to the same wind events (both envelopes rise
  together at the same stress peaks, e.g. ~0.46–0.48 Pa on 06-27/28 and
  0.22–0.31 Pa in-window on 07-01/02). Adding slab velocities on top of CMEMS
  would coherently double-count — and even that in-phase sum at a defensible H
  still under-predicts.
- The implied obs/CMEMS gain itself differs by site: ~2.9 (D2) vs ~2.0 (D1) by
  demodulation, ~1.9 vs ~1.5 by rotary-RMS — the same non-generalization the
  survey shows.

## Resolution — Branch C, no gain

The ratio is scattered (C), not clustered (A), and its only correlates are not
knowable at forecast time (not B). The un-gained time-dependent field stays:
phase-correct, carrying ~0.4–0.65 of the observed NI amplitude depending on
site. The slab is dropped permanently — it fails on amplitude *and* would
double-count in phase ([inertial_slab_model.md](inertial_slab_model.md)).
Phase 2 of [012](../012-near-inertial-forecast.md) ships with the per-cell
decomposition and the gain exposed as a parameter defaulting to **1.0**
(`_inertial.GAIN`) — the seam where a validated gain would plug in.

**Possible follow-ups** (a separate future branch, not now):

- A **track-separation skill test** of a modest gain (~1.5, the value the D1
  median would imply) against the un-gained baseline — per the guardrail
  below, a gain must *reduce track separation* to earn its place, not merely
  match amplitude.
- **Re-run the survey as the record grows** — the next strong wind event and
  the drifters spreading across the basin would relax the one-window /
  one-corner caveats.

## Guardrails (kept — they bind any future gain attempt)

- Never let a gain fit on a handful of drifters at one time drive an
  operational multiplier unseen: expose it, log the value each build, and
  default to un-gained if it hasn't been validated for the current
  period/region.
- Define skill as **reduction in track separation vs the un-gained baseline**,
  not amplitude-match alone — a gain that matches amplitude but worsens the
  actual forecast position is not an improvement.
- This is a drifter calibration of a model field, not a physical NI
  prediction; present it as such in the docs/sidebar.
