# Pollard–Millard slab model for near-inertial drift — DEFERRED / NOT PURSUED

**Status: deferred, effectively obsolete.** This was the original approach for
adding near-inertial (NI) motion to the drift forecast/hindcast: run a
Pollard–Millard slab model on forecast/analysis winds and add its NI velocity to
the CMEMS current before advecting. **It was dropped** once an empirical check
(2026-07-02) showed the CMEMS current field *already carries* a clean near-inertial
signal that 6-hourly sampling resolves without aliasing — see
[012-near-inertial-forecast.md](012-near-inertial-forecast.md). The oscillation
was being lost only because the forecast advected through a **frozen** field;
un-freezing it (time-dependent CMEMS) recovers the loop with no slab.

A slab would only be justified on a **realism** argument — that CMEMS
under-represents the true NI *amplitude* (its modelled NI at the Deployment-2 site
was ~2.5 cm/s) relative to what the drifters actually experience. That question was
not pursued: the time-dependent-CMEMS path is far cheaper and is the current plan.
Revisit this file only if the drifters' observed NI turns out to dwarf the CMEMS
NI and adding amplitude analytically becomes worthwhile.

Kept for the record because the method and the operational-cadence design are
sound and reusable, and because prior work already implements the whole chain.

## Method (Pollard & Millard 1970 slab)

A damped slab mixed layer forced by wind stress:

```
∂(u,v)/∂t + f·(−v,u) = (τx, τy)/(Hρ) − ε·(u,v)
```

Prior work `github.com/willirath/nia-prediction-low-latitudes` implements it as a
per-gridpoint **complex IIR filter** of the wind-stress series (near-verbatim
reusable):

- `T = τx + i·τy`, `q = u + i·v`;
  `q[l] = d1·q[l−1] + d2·q[l−2] + c0·T[l] + c2·T[l−2]`.
- Damping ε ≈ 1/(5 days); `f` from latitude; winds upsampled to hourly (< inertial
  period); `numba`-jitted, `xarray.apply_ufunc` over the grid.
- `H = 1 m` at integration time, scaled by `1/MLD` (MIMOC climatology) afterward.
- Wind stress via bulk `τ = ρ_a C_d |U| U`, `C_d = 1e-3`.
- Our mid-latitude region needs **no equatorial masking** (prior work zeroed
  |lat| < 4°); the inertial peak is clean here.

## The cadence problem this was solving

The slab is a damped oscillator (ε ≈ 1/5 d), so its estimate at "now" depends on
~10–15 days of prior wind forcing — it needs a multi-day spin-up. Re-integrating a
multi-week wind history every 5–10 min (and re-fetching winds that only update
4×/day) is wasteful. The intended fix was a **two-tier cadence**: a slow
(~6-hourly) tier fetches winds + runs the slab into a cached gridded NI time
series; the fast pipeline only *samples* it. That two-tier design survives — it's
now applied to the CMEMS current window instead (see 012) — the slab is just no
longer the thing being cached.

## Wind sources (researched 2026-07-02), if ever revived

- **ERA5** (incl. the new CDS "analysis-ready" tab and Google's ARCO zarr) is
  **reanalysis-only, ~5–7 d latency** — good for spin-up, useless for the forward
  window.
- **Recommended: all-GFS** from `s3://noaa-gfs-bdp-pds` (anonymous, 0.25°): 4-week
  retention covers spin-up, includes analysis (f000) *and* forecast to 16 d, one
  grid — **no ERA5→GFS seam** (a stress discontinuity would ring the slab
  oscillator). 10 m winds `UGRD`/`VGRD`.
- **ECMWF open-data** (free IFS, 0.25°) is a better forecast but retains only ~4 d,
  so it can't supply the spin-up; forecast-tail swap-in only.
- Stitch: subset the region once (GFS uses 0–360° lon; −10…35°E → 350–360 ∪ 0–35),
  taper any seam so the stress series has no step.

## Why it's parked

Time-dependent CMEMS advection reproduces the inertial loop from the model's own
NI, at a fraction of the cost (no wind fetch, no spin-up, no second data source),
and reuses the existing CMEMS plumbing. The slab is strictly additive realism
insurance whose need is unproven. If proven later, this file plus the prior-work
repo are enough to build it.
