// City map: sidewalk sun or heat by hour (PMTiles), the 500 m grid of walks home from frequent bus stops and the
// rapid-transit stations. Panel, legend, time bar and sidewalk colours are the shared components of common.js, as on
// the route tool; one measure toggle (sun / heat) switches the sidewalks, the grid and the stations together.
import { VERSION, readState, writeState, nav, collapsible, baseStyle, getJSON, pmtilesProtocol, COLORS, CLUSTERS, GRID_RAMPS, ATTR,
  pct, degC, timeBar, newMap, sidewalkColor, sidewalkLegend, SIDEWALK_WIDTH, SIDEWALK_OPACITY, measureControl, rampHTML, rampExpr } from "./common.js?v=dev";

nav("map.html");
pmtilesProtocol();
const $ = (id) => document.getElementById(id);
collapsible($("side"));
// start the map right away; the layers fill in when their data arrive
const DATA = Promise.all([getJSON("data/grid.geojson"), getJSON("data/stations.geojson"), getJSON("data/summary.json")]);
const EMPTY = { type: "FeatureCollection", features: [] };
const BIN = "300-800";                          // the walk band of the grid, the station colours and the story

// grid layers per measure: [property, button text, legend title]
const GRID = {
  shade: [["cluster", "Clusters", "Three kinds of cell"], ["sun_share", "Sun on shortest", "Sun on the shortest walk"],
    ["sun_red", "Less sun", "Less sun on the shadiest walk"], ["no_alt_share", "No cooler option", "Walks with no cooler option"],
    ["network_km_km2", "Sidewalk density", "Sidewalks per km²"], ["none", "Off", ""]],
  heat: [["u_short", "UTCI on shortest", "UTCI along the shortest walk"], ["u_cool", "Cooler by", "How much cooler the coolest walk is"],
    ["ext_red", "Extreme heat avoided", "Extreme heat (UTCI ≥ 46 °C) the coolest walk avoids"],
    ["network_km_km2", "Sidewalk density", "Sidewalks per km²"], ["none", "Off", ""]],
};
// the same question in the other measure, so switching keeps the reader's place
const PAIR = { cluster: "u_cool", sun_share: "u_short", sun_red: "u_cool", no_alt_share: "ext_red", u_short: "sun_share",
  u_cool: "sun_red", ext_red: "no_alt_share", network_km_km2: "network_km_km2", none: "none" };
const STATION = { shade: ["sun_red", "Stations · less sun on the shadiest walk"], heat: ["u_cool", "Stations · how much cooler the coolest walk is"] };

const st0 = readState();
let t = st0.t, measure = st0.m, gridvar = measure === "heat" ? "u_cool" : "cluster", summary = null, ready = false;

const map = newMap({
  container: "map", ...(st0.bounds ? { bounds: st0.bounds } : { center: [-75.135, 39.995], zoom: 10.6 }),
  style: baseStyle({
    city: { type: "vector", url: `pmtiles://data/city_shade.pmtiles?v=${VERSION}`, attribution: `${ATTR.shade}; ${ATTR.utci}; ${ATTR.chm}` },
    grid: { type: "geojson", data: EMPTY, attribution: ATTR.transit },
    sel: { type: "geojson", data: EMPTY },
    st: { type: "geojson", data: EMPTY },
    mask: { type: "geojson", data: EMPTY },
    ring: { type: "geojson", data: EMPTY },
  }, [
    { id: "grid", type: "fill", source: "grid", paint: { "fill-opacity": ["interpolate", ["linear"], ["zoom"], 12, 0.75, 13.5, 0.12, 14.5, 0],
      "fill-outline-color": "rgba(7,8,11,0.5)" } },
    { id: "city", type: "line", source: "city", "source-layer": "sidewalks", minzoom: 13, layout: { "line-cap": "round" },
      paint: { "line-color": sidewalkColor(measure, t), "line-width": SIDEWALK_WIDTH, "line-opacity": SIDEWALK_OPACITY } },
    { id: "sel", type: "line", source: "sel", paint: { "line-color": "#ffffff", "line-width": 2 } },
    { id: "st", type: "circle", source: "st",
      paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 3.5, 15, 8], "circle-opacity": ["case", ["get", "small"], 0, 1],
        "circle-stroke-width": ["case", ["get", "small"], 1.6, 1] } },
    // station focus: everything beyond 1.2 km of the clicked station is masked, the circle drawn as a thin dashed line
    { id: "mask", type: "fill", source: "mask", paint: { "fill-color": "#07080b", "fill-opacity": 0.85 } },
    { id: "ring", type: "line", source: "ring", paint: { "line-color": "#ffffff", "line-width": 1.2, "line-dasharray": [3, 3], "line-opacity": 0.9 } },
  ]),
});
window.__map = map;
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");

// ---- panel: measure, sidewalk legend, grid chips and legend, station legend
const zoomNote = () => (map.getZoom() < 13 ? '<p class="note">Zoom in to see the sidewalks.</p>' : "");
const legend = () => { $("legend").innerHTML = sidewalkLegend(measure) + zoomNote(); };
map.on("zoomend", legend);
function chips() {
  $("gridvar").innerHTML = GRID[measure].map(([k, n]) => `<button data-g="${k}" class="${k === gridvar ? "on" : ""}">${n}</button>`).join("");
}
function gridStyle() {
  if (!ready) return;
  const def = GRID[measure].find(([k]) => k === gridvar);
  map.setLayoutProperty("grid", "visibility", gridvar === "none" || focus ? "none" : "visible");
  if (gridvar === "none") { $("glegend").innerHTML = ""; return; }
  if (gridvar === "cluster") {
    map.setPaintProperty("grid", "fill-color", ["match", ["get", "cluster"], ...CLUSTERS.flatMap(([k, , c]) => [k, c]), "#444"]);
    $("glegend").innerHTML = `<div class="legend-title">${def[2]}</div><div class="swatches">${CLUSTERS.map(([, n, c]) => `<div><i style="background:${c}"></i>${n}</div>`).join("")}</div>`;
    return;
  }
  const r = GRID_RAMPS[gridvar];
  map.setPaintProperty("grid", "fill-color", rampExpr(gridvar));
  $("glegend").innerHTML = rampHTML(r.stops, r.ticks, { title: def[2] });
}
function stationStyle() {
  if (!ready) return;
  const [key, title] = STATION[measure], r = GRID_RAMPS[key], col = rampExpr(key, `${key}_${BIN}`);
  map.setPaintProperty("st", "circle-color", col);
  map.setPaintProperty("st", "circle-stroke-color", ["case", ["get", "small"], col, "#e8e6df"]);
  $("slegend").innerHTML = rampHTML(r.stops, r.ticks, { title: title.replace("Stations · ", "") }) +
    `<p class="note"><span class="hollow"></span>fewer than ${summary.min_trips_station_rank} walks: small sample</p>`;
}
const setTime = () => { if (map.getLayer("city")) map.setPaintProperty("city", "line-color", sidewalkColor(measure, t)); writeState({ t, m: measure }); };
$("gridvar").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  gridvar = b.dataset.g; chips(); gridStyle();
});
$("stations").addEventListener("change", (e) => map.setLayoutProperty("st", "visibility", e.target.checked ? "visible" : "none"));
measureControl($("measure"), measure, (m) => {
  if (m !== measure) gridvar = PAIR[gridvar];
  measure = m; legend(); chips(); gridStyle(); stationStyle(); setTime();
});
timeBar(t, (v) => { t = v; setTime(); }, {
  onPlay: () => { if (map.getZoom() < 13.5) map.flyTo({ center: [-75.1652, 39.9502], zoom: 15, duration: 1500 }); },   // sidewalks show at street level
});

map.once("style.load", async () => {
  const [grid, stations, sm] = await DATA;
  summary = sm; ready = true;
  map.getSource("grid").setData(grid);
  map.getSource("st").setData(stations);
  gridStyle(); stationStyle(); setTime();
});

// ---- details in the panel (cells and stations alike), the clicked cell outlined
const v = (x, f) => (x === null || x === undefined ? "–" : f(x));
const cellTable = (p) => `<h2>${p.cluster_name}</h2>
  <div class="sub">500 m cell · ${p.trips} walks home from frequent bus stops, 300–800 m, 15:00</div>
  <table class="cmp"><thead><tr><th></th><th>sun</th><th>heat</th></tr></thead><tbody>
    <tr><td>Shortest walk</td><td>${v(p.sun_share, (x) => `${pct(x, 0)} sunny`)}</td><td>${v(p.u_short, (x) => degC(x))}</td></tr>
    <tr><td>Shadiest / coolest</td><td class="cool-t">${v(p.sun_red, (x) => `−${pct(x, 0)} sun`)}</td><td class="cool-t">${v(p.u_cool, (x) => degC(-x))}</td></tr>
    <tr><td>No cooler option</td><td>${v(p.no_alt_share, (x) => pct(x, 0))}</td><td></td></tr>
    <tr><td>Extreme heat avoided</td><td></td><td>${v(p.ext_red, (x) => pct(Math.max(x, 0), 0))}</td></tr>
  </tbody></table>
  <p class="note">Buildings ${p.bldg_height_m} m tall on average · tree canopy ${pct(p.canopy, 0)} · ${p.network_km_km2} km of sidewalk per km²</p>`;
const stationTable = (p) => {
  const row = (b) => p[`trips_${b}`] ? `<tr><td>${b.replace("-", "–")} m</td><td>${Math.round(p[`trips_${b}`])}</td>
    <td class="cool-t">${v(p[`sun_red_${b}`], (x) => `−${pct(x, 0)}`)}</td><td>${v(p[`no_alt_share_${b}`], (x) => pct(x, 0))}</td>
    <td class="cool-t">${v(p[`u_cool_${b}`], (x) => degC(-x))}</td></tr>` : "";
  const small = p.small === true || p.small === "true";
  return `<h2>${p.name}</h2><div class="sub">${p.line} · walks home within 1.2 km, 15:00</div>
    <table class="cmp"><thead><tr><th>walk</th><th>walks</th><th>sun</th><th>no option</th><th>heat</th></tr></thead>
    <tbody>${row("<300")}${row("300-800")}${row("800-1200")}</tbody></table>
    <p class="note">sun: less sun on the shadiest walk · heat: how much cooler the coolest walk is (UTCI)</p>
    ${small ? `<p class="note" style="color:var(--sun-soft)">Fewer than ${summary.min_trips_station_rank} walks of 300–800 m: small sample.</p>` : ""}`;
};
const showDetails = (html, back) => { $("focus-info").innerHTML = html; $("unfocus").hidden = !back; $("focus").hidden = false; };
map.on("click", "grid", (e) => {
  if (map.queryRenderedFeatures(e.point, { layers: ["st"] }).length || outside(e.lngLat)) return;
  const f = e.features[0];
  map.getSource("sel").setData({ type: "Feature", properties: {}, geometry: f.geometry });
  showDetails(cellTable(f.properties), false);
});
for (const l of ["st", "grid"]) {
  map.on("mouseenter", l, () => { map.getCanvas().style.cursor = "pointer"; });
  map.on("mouseleave", l, () => { map.getCanvas().style.cursor = ""; });
}

// ---- station focus: click a station to see what lies within 1.2 km of it (the longest walk home in the study, about
// 15 minutes on foot at 4.8 km/h). The circle is straight-line distance, so it bounds the walks, which follow sidewalks.
const R = 1200;
let focus = null, before = null;
function circle([lon, lat], n = 96) {
  const ky = 111320, kx = ky * Math.cos((lat * Math.PI) / 180), out = [];
  for (let i = 0; i <= n; i++) { const a = (2 * Math.PI * i) / n; out.push([lon + (R * Math.sin(a)) / kx, lat + (R * Math.cos(a)) / ky]); }
  return out;
}
const outside = (ll) => focus && Math.hypot((ll.lng - focus[0]) * 111320 * Math.cos((focus[1] * Math.PI) / 180), (ll.lat - focus[1]) * 111320) > R;
const ringEl = document.createElement("div");
ringEl.innerHTML = '<div class="ringlabel">1.2 km · about 15 min walk</div>';
const ringLabel = new maplibregl.Marker({ element: ringEl, anchor: "bottom" });
function focusOn(c) {
  if (!focus) before = { center: map.getCenter(), zoom: map.getZoom() };
  focus = c;
  const ring = circle(c);
  map.getSource("ring").setData({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: ring } });
  map.getSource("mask").setData({ type: "Feature", properties: {}, geometry: { type: "Polygon",
    coordinates: [[[-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85]], ring] } });
  map.getSource("sel").setData(EMPTY);
  ringLabel.setLngLat(ring[0]).addTo(map);
  map.setLayoutProperty("grid", "visibility", "none");     // only the sidewalks inside the circle
  const lons = ring.map((q) => q[0]), lats = ring.map((q) => q[1]), side = $("side").getBoundingClientRect(), narrow = window.innerWidth <= 760;
  map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], { duration: 1400,
    padding: narrow ? { top: 120, left: 20, right: 20, bottom: window.innerHeight - side.top + 20 } : { top: 60, bottom: 110, left: side.right + 40, right: 40 } });
}
function unfocus() {
  if (!focus) { map.getSource("sel").setData(EMPTY); $("focus").hidden = true; return; }
  focus = null;
  map.getSource("ring").setData(EMPTY); map.getSource("mask").setData(EMPTY); ringLabel.remove(); gridStyle();
  $("focus").hidden = true;
  if (before) map.flyTo({ ...before, duration: 1200 });
}
map.on("click", "st", (e) => { focusOn(e.features[0].geometry.coordinates); showDetails(stationTable(e.features[0].properties), true); });
map.on("click", (e) => { if (outside(e.lngLat) && !map.queryRenderedFeatures(e.point, { layers: ["st"] }).length) unfocus(); });
$("unfocus").addEventListener("click", unfocus);
addEventListener("keydown", (e) => { if (e.key === "Escape") unfocus(); });
