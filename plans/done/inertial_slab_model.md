# Pollard–Millard slab model for near-inertial drift — DROPPED

> **Dropped permanently** (tested 2026-07-03). The slab fails on amplitude —
> closing the gap to the drifters would need an implausibly shallow mixed
> layer (H* ≈ 23–38 m against a modelled ~41–204 m) — and on phase: its NI
> response is in phase with CMEMS's own NI (|ρ| = 0.84–0.88, lag ≈ 0 h), so
> adding it on top of the model field would coherently double-count the same
> wind events. Full investigation record in
> [013-inertial-gain-generalization.md](013-inertial-gain-generalization.md);
> doc counterpart [docs/forecast.md](../../docs/forecast.md) (alternatives
> weighed).

This was the original approach for adding near-inertial (NI) motion to the
drift forecast/hindcast: run a Pollard–Millard slab model on forecast/analysis
winds and add its NI velocity to the CMEMS current before advecting. It was
first parked (2026-07-02) when an empirical check showed the CMEMS current
field *already carries* a clean NI signal that 6-hourly sampling resolves
without aliasing — see
[012-near-inertial-forecast.md](../012-near-inertial-forecast.md) — leaving
one revival condition: that CMEMS under-represents the true NI *amplitude*
badly enough to warrant adding it analytically. That condition was then tested
head-on (below); the test kills the slab rather than reviving it. The method
and wind-source record is kept because it documents that revival was evaluated
with sound machinery, not skipped — and why re-evaluating is pointless.

## The test that dropped it (2026-07-03, offline in `tmp_ni/` — untracked, so recorded here)

**Setup.** Exact-exponential hourly integration of the slab (ε = 1/(5 days))
on GFS-backed hourly 10 m winds (Open-Meteo; the NOMADS OpenDAP service is
retired), spin-up from 06-17 (result insensitive to spin-up start, < 2 %),
τ = ρ_a·C_d·|U|U with C_d = 1.2·10⁻³ (results linear in C_d and 1/H), run at
both drifter-cluster centroids. Compared against CMEMS NI (complex
demodulation of an hourly point series, 06-21 → 07-03) and observed NI
(detrended drifter velocities).

- **Amplitude.** At the CMEMS-model mixed-layer depth, slab/obs = 0.22 at the
  D2 site (11.48°E, −37.36°S; MLD mean 138.8 m, daily range 63–203 m) and
  0.30–0.49 at the D1 centroid (12.41°E, −36.63°S; MLD mean 77.2 m, range
  41–123 m); slab/CMEMS = 0.42 and 0.46–0.99. The H that would close the
  slab–drifter gap is H* ≈ 23–38 m — implausibly shallow for the austral-winter
  Cape Basin, where the model MLD never drops below ~41 m in the window. No
  defensible (H, C_d) pair closes the gap.
- **Phase / double-counting.** Slab NI and CMEMS NI correlate at
  |ρ| = 0.84–0.88 with lag ≈ 0 h during the drifter window: CMEMS's NI *is*
  the wind-forced slab-like response to the same wind events (both envelopes
  rise together at the same stress peaks, e.g. ~0.46–0.48 Pa on 06-27/28 and
  0.22–0.31 Pa in-window on 07-01/02). So the slab cannot be *added* to the
  model current without double-counting — and even that in-phase sum, at a
  defensible H, still under-predicts the observed amplitude.

Both failure modes are structural (unphysical H; in-phase redundancy with the
model's own response), so no wind source, coefficient tuning, or cadence
design revives the approach.

## Method (Pollard & Millard 1970 slab)

A damped slab mixed layer forced by wind stress:

```
∂(u,v)/∂t + f·(−v,u) = (τx, τy)/(Hρ) − ε·(u,v)
```

Prior work `github.com/willirath/nia-prediction-low-latitudes` implements it as
a per-gridpoint **complex IIR filter** of the wind-stress series (the 2026-07-03
test used an exact-exponential hourly integrator of the same equation):

- `T = τx + i·τy`, `q = u + i·v`;
  `q[l] = d1·q[l−1] + d2·q[l−2] + c0·T[l] + c2·T[l−2]`.
- Damping ε ≈ 1/(5 days); `f` from latitude; winds upsampled to hourly (< inertial
  period); `numba`-jitted, `xarray.apply_ufunc` over the grid.
- `H = 1 m` at integration time, scaled by `1/MLD` (MIMOC climatology) afterward.
- Wind stress via bulk `τ = ρ_a C_d |U| U`.
- Our mid-latitude region needs **no equatorial masking** (prior work zeroed
  |lat| < 4°); the inertial peak is clean here.

## The cadence problem this was solving

The slab is a damped oscillator (ε ≈ 1/5 d), so its estimate at "now" depends on
~10–15 days of prior wind forcing — it needs a multi-day spin-up. Re-integrating a
multi-week wind history every 5–10 min (and re-fetching winds that only update
4×/day) is wasteful. The intended fix was a **two-tier cadence**: a slow
(~6-hourly) tier fetches winds + runs the slab into a cached gridded NI time
series; the fast pipeline only *samples* it. That two-tier design survives — it's
now applied to the CMEMS current window instead (see
[012](../012-near-inertial-forecast.md)) — the slab is just no longer the thing
being cached.

## Wind sources (researched 2026-07-02)

Kept for the record; the 2026-07-03 test used Open-Meteo's GFS-backed hourly
10 m winds (the NOMADS OpenDAP service is retired).

- **ERA5** (incl. the new CDS "analysis-ready" tab and Google's ARCO zarr) is
  **reanalysis-only, ~5–7 d latency** — good for spin-up, useless for the forward
  window.
- **All-GFS** from `s3://noaa-gfs-bdp-pds` (anonymous, 0.25°): 4-week
  retention covers spin-up, includes analysis (f000) *and* forecast to 16 d, one
  grid — **no ERA5→GFS seam** (a stress discontinuity would ring the slab
  oscillator). 10 m winds `UGRD`/`VGRD`.
- **ECMWF open-data** (free IFS, 0.25°) is a better forecast but retains only ~4 d,
  so it can't supply the spin-up; forecast-tail swap-in only.
- Stitch: subset the region once (GFS uses 0–360° lon; −10…35°E → 350–360 ∪ 0–35),
  taper any seam so the stress series has no step.
