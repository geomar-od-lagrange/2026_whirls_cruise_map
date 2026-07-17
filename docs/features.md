# features

- a map rendered as static HTML (served by the OpenShift gateway, see deploy.md)
- an unofficial / work-in-progress caution banner pinned above the header
- a sidebar "data freshness" panel: the UTC build time and a live UTC clock
- showing all drifter locations
- showing the glider instruments — the XSPAR spar buoy and the seagliders — each with its latest position and track
- a single **Show tracks** master (in the time-slider box) that shows or hides every observed track line at once — drifter, glider, and ship — drifters truncated at deployment
- **forecast tracks** for the real deployed drifters — each in-water drifter's last fix advected forward through the CMEMS field (via the same `/api/forecast` engine as the deploy tool). Drawn in the drifter's **own identity colour**, styled like its observed track so the two read as one path (only the dashed reporting-lag **bridge** marks where observed hands off to forecast). Shown as the clock-driven continuation of the observed track: only when the scrubber is past *now*, with the drifter's own marker walking the forecast into the future and a small **now-ghost** dot left at its present position. The **track line** is governed by the **Show tracks** master (hidden when tracks are off — but the marker still walks the forecast); it is clipped to the scrubber position. Shown only when that dynamic API is reachable.
- click any instrument's track, one of its fix dots, or its latest-position marker (drifters and gliders alike) to highlight it — brightening it and desaturating the rest (click the empty map to clear)
- a top-right control dock: **Instruments** (show/hide each drifter batch, glider platform, and the two vessels independently in one merged panel, with a select-all / deselect-all shortcut), **Currents** (a shading radio — speed / ζ/f vorticity / none — plus flow and near-inertial overlay checkboxes and an **Animate overlays** toggle that freezes the near-inertial animation to a static snapshot while scrubbing; the flow overlay is a pre-rendered static streamline raster, always fluent), and **Deploy** (placing virtual deployments)
- overlay of today's cmems surface currents (analysis / forecast t=0)
- toggle for a normalized relative-vorticity (ζ/f) overlay from the same field — cyclonic (+) / anticyclonic (−) eddies as opposite-signed lobes (off by default)