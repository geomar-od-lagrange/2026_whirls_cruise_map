# Relative vorticity overlay (ζ/f)

A toggleable raster of the surface **relative vorticity normalized by the Coriolis
parameter** — the Rossby number ζ/f — drawn over the same map as the current-speed
shading and flow trails. It is the diagnostic that makes the eddy field legible:
cyclones and anticyclones appear as opposite-signed lobes, which the speed
magnitude alone cannot distinguish (both an eddy's core and its rim can be slow or
fast regardless of rotation sense).

## What it shows

- **Relative (vertical) vorticity** `ζ = ∂v/∂x − ∂u/∂y` (s⁻¹) of the CMEMS surface
  current — the local spin of the flow.
- **Normalized by** the planetary vorticity `f = 2Ω sin φ` (s⁻¹). The overlay is
  the dimensionless ratio ζ/f.

Normalizing serves two ends. It renders the field **dimensionless and O(1)** for
mesoscale eddies (|ζ/f| typically 0.1–0.3 here), and it makes the value
**comparable across latitude**: the same physical spin is a larger dynamical
signal where f is weaker. An unnormalized ζ (bare s⁻¹) would carry the latitudinal
trend of f and read as a less intuitive number, so ζ/f is the standard choice.

### Sign convention

`f` is **negative throughout the cruise bbox** (−55…−15° latitude, Southern
Hemisphere). For a Southern-Hemisphere **cyclone** the flow spins clockwise, giving
ζ < 0; with f < 0 the ratio **ζ/f > 0**. So:

- **ζ/f > 0 — cyclonic** (cold-core here),
- **ζ/f < 0 — anticyclonic** (warm-core).

This is the standard Rossby-number sign and is identical in both hemispheres, so
the legend's "cyclonic (+) / anticyclonic (−)" reads the same anywhere. The
diverging colour map is centred on zero (no rotation) with the two senses as its
two arms.

## Source and resolution

Derived from the **same CMEMS forecast window** the speed and flow overlays use
(`_currents.fetch_shading_window`, `cmems_mod_glo_phy-cur_anfc_0.083deg`, 1/12° ≈
8 km). Vorticity is a spatial derivative of the `uo`/`vo` already fetched, so it
adds **no download** and renders at the same near-native grid as the speed frames.
Like the speed shading it is **time-sliced**: one frame per 12 h slider offset
(−12 … +72 h; see [currents.md](currents.md)), each a **snapshot** diagnostic of
the instantaneous field at that step — not an advected or time-integrated quantity.
The frames share one symmetric colour scale so ζ/f reads the same at every time.

Derivatives carry the sphere's metric factors — `∂/∂x = 1/(R cos φ) · ∂/∂λ`,
`∂/∂y = 1/R · ∂/∂φ` (λ, φ in radians, R = 6371 km) — computed with `np.gradient`
along the longitude/latitude axes. `f` is far from zero over this bbox, so the
ratio is well-conditioned everywhere; near the equator it would not be, but the
cruise region never goes there.

## Rendering: a diverging, symmetric raster

`_vorticity.to_vorticity_frames` mirrors the surface-speed shading
(`_currents.to_speed_frames`) — one diagnostic 2-D field per slider frame warped
to Web-Mercator through the shared `_raster.mercator_rgba_webp` helper (lossless
WebP, land transparent; see [currents.md](currents.md) for the transport
rationale). Two choices follow from ζ/f being **signed** rather than a magnitude:

- a **diverging** colour map (cmocean `curl`, built for field curl) instead of the
  sequential `speed` map; and
- a **symmetric clip**: `vmax` is the 98th percentile of |ζ/f| and the field is
  mapped from `[−vmax, +vmax]` so zero lands on the map's neutral midpoint. The
  meta ships `vmin = −vmax` alongside `vmax`, which is the one field the client
  legend needs to render a symmetric −vmax…0…+vmax scale rather than the speed
  bar's 0…vmax. The percentile clip keeps a few grid-scale spikes in the Agulhas
  shear front (where |ζ/f| can exceed 1) from washing out the mesoscale scale.

Like the speed raster, ζ/f is **binned to `N_BINS = 12` discrete colour classes**
before the colour lookup (`_currents._quantize_unit`) — same `curl` palette, no new
colours — for the transport and quantitative-legend reasons spelled out in
[currents.md](currents.md) (*Discrete colour classes*): lossless WebP compresses the
resulting flat regions far better (~−60 % per frame), and the classes make the map ↔
legend lookup exact. The bin count is **even** on purpose so **zero falls on a bin
edge** (the diverging midpoint `0.5 = 6/12` sits between the two central classes),
giving 6 classes per rotation sense with none straddling no-rotation. The meta's
`colorbar` is those 12 class colours, which the client draws as hard-edged legend
swatches rather than a smooth ramp.

The client (`app.js`) registers it as an `L.imageOverlay` in the same `shading`
pane as the speed raster. Because both shadings fill that one pane, only one makes
sense at a time, so in the **Currents** control they are **mutually-exclusive base
layers** (radio buttons: *Current speed* / *Vorticity ζ/f* / *None*), not
independent checkboxes — picking ζ/f swaps it in for the speed raster; speed is
selected by default. Both rasters are drawn **fully opaque** (the overlay carries
no `opacity`, so the ocean shows its true colour rather than a wash over the
basemap; land stays transparent via the PNG's own alpha mask, so the coastline
still shows through). Its legend is a local twin of the speed legend
(`renderVorticityInfo`), symmetric where the speed one is zero-based, and drawn as
the same 12 hard-edged colour classes the raster uses.

### Land and coastal edge

CMEMS land is static NaN, carried through to transparency. Because vorticity is a
finite difference, `np.gradient` propagates a NaN into its immediate neighbour, so
the ocean's coastal ring erodes by **one cell** — the outermost wet cells next to
land go transparent too. This is a cosmetic one-pixel trim of the coastline, the
same order of approximation the Mercator latitude warp already makes, and is
accepted rather than worked around.

## Why ζ/f and not Okubo–Weiss

ζ/f answers "which way, and how strongly, is the flow rotating?" A related eddy
diagnostic, the **Okubo–Weiss parameter** (W = strain² − vorticity²), answers a
different question — "is this a rotation-dominated core or a strain-dominated
filament?" — and does not carry rotation *sign*, so it cannot colour cyclones and
anticyclones apart. For reading the eddy field on a map, the signed ζ/f is the more
direct diagnostic; Okubo–Weiss would be a separate, complementary layer, out of
scope here.

## Not a prediction

Like every current-derived layer this inherits the model's limits: it is the
free-running global model's surface vorticity at 1/12°, which resolves mesoscale
eddies but not the submesoscale, and its strength is only as good as the model's.
It is an indicative diagnostic of the modelled eddy field, not an observation.
