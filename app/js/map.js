// City map: sidewalk shade by hour (PMTiles), 500 m grid indicators and clusters (bus walks), rapid-transit stations.
import { VERSION, handoff, nav, collapsible, baseStyle, getJSON, shadeRamp, pmtilesProtocol, COLORS, CLUSTERS, GRID_RAMPS, ATTR, pct, timeControl, newMap } from "./common.js?v=94fd121";

nav("map.html");
pmtilesProtocol();
const $ = (id) => document.getElementById(id);
collapsible($("side"));
// start the map right away; the layers fill in when their data arrive
const DATA = Promise.all([getJSON("data/grid.geojson"), getJSON("data/stations.geojson"), getJSON("data/summary.json")]);
const EMPTY = { type: "FeatureCollection", features: [] };
let summary;                                   // filled with the station data

const RAMPS = GRID_RAMPS;
const stColor = ["interpolate", ["linear"], ["coalesce", ["get", "sun_red_300-800"], 0], 0, "#1d2733", 0.15, "#2f8f9a", 0.4, COLORS.cool];

const start = handoff();                // arriving from the story: open on its last view
const map = newMap({
  container: "map", ...(start ? { bounds: start } : { center: [-75.135, 39.995], zoom: 10.6 }),
  style: baseStyle({
    city: { type: "vector", url: `pmtiles://data/city_shade.pmtiles?v=${VERSION}`, attribution: `${ATTR.shade}; ${ATTR.chm}` },
    grid: { type: "geojson", data: EMPTY, attribution: "Transit: SEPTA GTFS (fall 2026)" },
    st: { type: "geojson", data: EMPTY },
    mask: { type: "geojson", data: EMPTY },
    ring: { type: "geojson", data: EMPTY },
  }, [
    { id: "grid", type: "fill", source: "grid", paint: { "fill-opacity": ["interpolate", ["linear"], ["zoom"], 12, 0.7, 13.5, 0.15] } },
    { id: "city", type: "line", source: "city", "source-layer": "sidewalks", minzoom: 13, layout: { "line-cap": "round" },
      paint: { "line-color": shadeRamp("s7"), "line-width": ["interpolate", ["linear"], ["zoom"], 13, 0.6, 17, 3] } },
    { id: "st", type: "circle", source: "st",
      paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 3.5, 15, 8],
        "circle-color": stColor, "circle-opacity": ["case", ["get", "small"], 0, 1],
        "circle-stroke-color": ["case", ["get", "small"], stColor, "#e8e6df"], "circle-stroke-width": ["case", ["get", "small"], 1.6, 1] } },
    // station focus: everything beyond 1.2 km of the clicked station is masked, the circle drawn as a thin dashed line
    { id: "mask", type: "fill", source: "mask", paint: { "fill-color": "#07080b", "fill-opacity": 0.85 } },
    { id: "ring", type: "line", source: "ring", paint: { "line-color": "#ffffff", "line-width": 1.2, "line-dasharray": [3, 3], "line-opacity": 0.9 } },
  ]),
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");

function gridStyle() {
  const v = $("gridvar").value;
  map.setLayoutProperty("grid", "visibility", v === "none" ? "none" : "visible");
  if (v === "none") { $("glegend").innerHTML = ""; return; }
  if (v === "cluster") {
    map.setPaintProperty("grid", "fill-color", ["match", ["get", "cluster"], ...CLUSTERS.flatMap(([k, , c]) => [k, c]), "#444"]);
    $("glegend").innerHTML = CLUSTERS.map(([, n, c]) => `<div style="font-size:12px"><span class="legend-swatch" style="background:${c};height:10px"></span>${n}</div>`).join("");
    return;
  }
  const r = RAMPS[v];
  map.setPaintProperty("grid", "fill-color", ["interpolate", ["linear"], ["coalesce", ["get", v], 0], ...r.stops]);
  const cols = r.stops.filter((_, i) => i % 2 === 1);
  $("glegend").innerHTML = `<div class="ramp" style="background:linear-gradient(to right,${cols.join(",")})"></div><div class="ends"><span>${r.lo}</span><span>${r.hi}</span></div>`;
}
let t = 7;
const setTime = () => { if (map.getLayer("city")) map.setPaintProperty("city", "line-color", shadeRamp(`s${t}`)); };
map.once("style.load", async () => {
  gridStyle(); setTime();
  const [grid, stations, sm] = await DATA;
  summary = sm;
  $("minst").textContent = summary.min_trips_station_rank;
  map.getSource("grid").setData(grid);
  map.getSource("st").setData(stations);
});
const tc = timeControl($("time"), t, (v) => { t = v; setTime(); });
// play through the hours of the selected day
let timer = null;
$("play").addEventListener("click", () => {
  if (timer) { clearInterval(timer); timer = null; $("play").textContent = "Play the day"; return; }
  $("play").textContent = "Stop";
  if (map.getZoom() < 13.5) map.flyTo({ center: [-75.1652, 39.9502], zoom: 15, duration: 1500 });   // shade shows at street level
  const day = Math.floor(tc.get() / 12);
  let h = tc.get() % 12 === 11 ? 0 : tc.get() % 12;
  tc.set(day * 12 + h);
  timer = setInterval(() => {
    h += 1;
    if (h > 11) { clearInterval(timer); timer = null; $("play").textContent = "Play the day"; return; }
    tc.set(day * 12 + h);
  }, 900);
});
$("gridvar").addEventListener("change", gridStyle);
$("stations").addEventListener("change", (e) => map.setLayoutProperty("st", "visibility", e.target.checked ? "visible" : "none"));

const popup = new maplibregl.Popup({ closeButton: false, maxWidth: "290px" });
map.on("click", "st", (e) => {
  const p = e.features[0].properties;
  const row = (b) => p[`trips_${b}`] ? `<tr><td>${b} m</td><td>${Math.round(p[`trips_${b}`])}</td><td>${pct(p[`sun_red_${b}`] ?? 0, 0)}</td><td>${pct(p[`no_alt_share_${b}`] ?? 0, 0)}</td></tr>` : "";
  const small = p.small === true || p.small === "true";
  // the station's numbers go into the panel, so nothing covers its 1.2 km circle
  $("focus-info").innerHTML = (`<b>${p.name}</b> <span class="label">${p.line}</span>
    <table style="width:100%;font-size:11px;margin-top:6px"><tr class="label"><td>walk</td><td>walks</td><td>less sun</td><td>no cooler option</td></tr>${row("<300")}${row("300-800")}${row("800-1200")}</table>
    ${small ? `<div style="font-size:11px;color:var(--sun-soft);margin-top:4px">Fewer than ${summary.min_trips_station_rank} walks of 300–800 m: small sample.</div>` : ""}`);
});
map.on("click", "grid", (e) => {
  if (map.queryRenderedFeatures(e.point, { layers: ["st"] }).length || outside(e.lngLat)) return;
  const p = e.features[0].properties;
  popup.setLngLat(e.lngLat).setHTML(`<b>${p.cluster_name}</b><div style="margin-top:6px;font-size:12px">
    ${p.trips} walks home from high-frequency bus stops (300–800 m) · sun on shortest ${pct(p.sun_share, 0)} · achievable reduction ${pct(p.sun_red, 0)} ·
    no cooler option ${pct(p.no_alt_share, 0)}<br>building height ${p.bldg_height_m} m · canopy ${pct(p.canopy, 0)} · sidewalks ${p.network_km_km2} km/km²</div>`).addTo(map);
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
  ringLabel.setLngLat(ring[0]).addTo(map);
  popup.remove(); map.setLayoutProperty("grid", "visibility", "none");     // only the sidewalks inside the circle
  const lons = ring.map((q) => q[0]), lats = ring.map((q) => q[1]), side = $("side").getBoundingClientRect(), narrow = window.innerWidth <= 760;
  map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], { duration: 1400,
    padding: narrow ? { top: 60, left: 20, right: 20, bottom: window.innerHeight - side.top + 20 } : { top: 60, bottom: 40, left: side.right + 40, right: 40 } });
  $("focus").hidden = false;
}
function unfocus() {
  if (!focus) return;
  focus = null;
  map.getSource("ring").setData(EMPTY); map.getSource("mask").setData(EMPTY); ringLabel.remove(); popup.remove(); gridStyle();
  $("focus").hidden = true;
  if (before) map.flyTo({ ...before, duration: 1200 });
}
map.on("click", "st", (e) => focusOn(e.features[0].geometry.coordinates));
map.on("click", (e) => { if (outside(e.lngLat) && !map.queryRenderedFeatures(e.point, { layers: ["st"] }).length) unfocus(); });
$("unfocus").addEventListener("click", unfocus);
addEventListener("keydown", (e) => { if (e.key === "Escape") unfocus(); });
