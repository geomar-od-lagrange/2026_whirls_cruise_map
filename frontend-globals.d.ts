// Ambient declarations for the browser globals the static map relies on but that
// aren't ES-module imports. Dev-only: consumed by `tsconfig.json`'s `tsc --checkJs`
// pass (see `pixi run check-frontend`); never bundled or served — the site is
// no-bundler and ships the `.js` verbatim. `L` is the vendored Leaflet global
// (site/map/vendor/leaflet-1.9.4/leaflet.js, loaded by a plain <script> before the
// app module), plus its leaflet-velocity plugin surface hung off it.
declare const L: any;
