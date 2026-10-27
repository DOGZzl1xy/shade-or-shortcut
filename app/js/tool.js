// Route tool: two clicks -> shortest walk (outdoor network) and coolest walk (selected scenario), recomputed in the browser.
import { handoff, nav, collapsible, baseStyle, getJSON, shadeRamp, COLORS, DEMO_CENTER, ATTR, pct, timeControl, newMap, edgeCoords, pathCoords,
  routeLayers } from "./common.js?v=510bf10";
import { route } from "./router.js?v=510bf10";

nav("tool.html");
const $ = (id) => document.getElementById(id);
collapsible($("side"));
const [net, layers, hero] = await Promise.all([getJSON("data/demo_network.json"), getJSON("data/demo_layers.json"), getJSON("data/hero.json")]);
$("status").hidden = true;

let t = 7, scenario = "all", pts = [];
const caches = {};
const MAX_SNAP_M = 60;          // a click farther than this from any sidewalk node is rejected, not snapped

// sidewalk network as GeoJSON once, with shade % for every time
const E = net.edges, T = net.times.length;
const features = [];
for (let i = 0; i < E.u.length; i++) {
  const props = { kind: E.kind[i], pc: E.penncard[i] };
  for (let k = 0; k < T; k++) {
    const L = E.len[i] || 1e-6;
    props[`s${k}`] = E.kind[i] === 2 ? 100 : Math.round(100 * (1 - net.L.sun[k][i] / L));
  }
  features.push({ type: "Feature", properties: props, geometry: { type: "LineString", coordinates: edgeCoords(net, i) } });
}
const network = { type: "FeatureCollection", features };

// outdoor nodes that a click may snap to
const outdoorNode = new Uint8Array(net.nodes.lon.length);
for (let i = 0; i < E.u.length; i++) if (E.kind[i] === 0) { outdoorNode[E.u[i]] = 1; outdoorNode[E.v[i]] = 1; }
const kx = Math.cos((DEMO_CENTER[1] * Math.PI) / 180), M_PER_DEG = 111320;
function snap(lng, lat) {
  let best = -1, bd = Infinity;
  for (let i = 0; i < outdoorNode.length; i++) {
    if (!outdoorNode[i]) continue;
    const d = ((net.nodes.lon[i] - lng) * kx) ** 2 + (net.nodes.lat[i] - lat) ** 2;
    if (d < bd) { bd = d; best = i; }
  }
  return { node: best, dist: Math.sqrt(bd) * M_PER_DEG };
}
let lon0 = Infinity, lon1 = -Infinity, lat0 = Infinity, lat1 = -Infinity;
net.nodes.lon.forEach((x, i) => { lon0 = Math.min(lon0, x); lon1 = Math.max(lon1, x); lat0 = Math.min(lat0, net.nodes.lat[i]); lat1 = Math.max(lat1, net.nodes.lat[i]); });

const start = handoff();                // arriving from the story: open on its last view, then move to the walk
const map = newMap({
  container: "map", ...(start ? { bounds: start } : { center: DEMO_CENTER, zoom: 15.3 }),
  style: baseStyle({
    net: { type: "geojson", data: network, attribution: `${ATTR.shade}; ${ATTR.osm}` },
    bld: { type: "geojson", data: layers.buildings },
    demo: { type: "geojson", data: layers.demo },
    paths: { type: "geojson", data: { type: "FeatureCollection", features: [] } },
  }, [
    { id: "demo", type: "line", source: "demo", paint: { "line-color": "rgba(255,255,255,0.35)", "line-width": 1, "line-dasharray": [3, 3] } },
    { id: "bld", type: "fill", source: "bld", paint: { "fill-color": ["case", ["get", "needs_penncard"], "rgba(180,140,255,0.10)", "rgba(180,140,255,0.22)"],
      "fill-outline-color": "rgba(180,140,255,0.55)" } },
    { id: "net", type: "line", source: "net", filter: ["==", ["get", "kind"], 0],
      paint: { "line-color": shadeRamp("s7"), "line-width": ["interpolate", ["linear"], ["zoom"], 14, 0.7, 17, 2.4], "line-opacity": 0.75 } },
    { id: "indoor", type: "line", source: "net", filter: ["==", ["get", "kind"], 2],
      paint: { "line-color": COLORS.indoor, "line-width": 1.6, "line-dasharray": [2, 1.5], "line-opacity": 0.8 } },
    ...routeLayers("paths"),
  ]),
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");

const msg = (s) => { $("msg").textContent = s; };

// draggable start (A) and end (B) markers
function pin(label) {
  const el = document.createElement("div");
  el.className = "pin";
  el.textContent = label;
  return new maplibregl.Marker({ element: el, draggable: true });
}
const markers = [pin("A"), pin("B")];
markers.forEach((mk, i) => mk.on("dragend", () => {
  const { lng, lat } = mk.getLngLat();
  const s = snap(lng, lat);
  if (s.dist > MAX_SNAP_M) msg(`No sidewalk within ${MAX_SNAP_M} m of that point.`);
  else if (s.node === pts[1 - i]) msg("Start and end cannot be the same point.");
  else { msg(""); pts[i] = s.node; }
  render();
}));
const mp = (x, len) => `${Math.round(x)} m<small>${pct(x / len, 0)}</small>`;

function render() {
  if (map.getLayer("net")) map.setPaintProperty("net", "line-color", shadeRamp(`s${t}`));
  if (map.getLayer("indoor")) {
    const f = scenario === "outdoor" ? ["==", ["get", "kind"], -1] : scenario === "no_penncard" ? ["all", ["==", ["get", "kind"], 2], ["==", ["get", "pc"], 0]] : ["==", ["get", "kind"], 2];
    map.setFilter("indoor", f);
  }
  markers.forEach((mk, i) => {
    if (pts[i] === undefined) mk.remove();
    else mk.setLngLat([net.nodes.lon[pts[i]], net.nodes.lat[pts[i]]]).addTo(map);
  });
  // the sidewalk shade shares the orange-cyan colours of the walks, so it is dimmed while walks are shown
  if (map.getLayer("net")) map.setPaintProperty("net", "line-opacity", pts.length < 2 ? 0.75 : 0.4);
  if (pts.length < 2) { map.getSource("paths")?.setData({ type: "FeatureCollection", features: [] }); $("result").hidden = true; return; }
  const r = route(net, scenario, t, pts[0], pts[1], caches);
  const feat = (p, role) => ({ type: "Feature", properties: { role }, geometry: { type: "LineString", coordinates: pathCoords(net, p.edges, pts[0]) } });
  // indoor edges of the coolest walk, each in the walking direction (the line offset depends on it)
  const indoor = [];
  let node = pts[0];
  for (const i of r.coolest.edges) {
    const fwd = E.u[i] === node, c = edgeCoords(net, i);
    if (E.kind[i] === 2) indoor.push({ type: "Feature", properties: { role: "indoor" }, geometry: { type: "LineString", coordinates: fwd ? c : c.reverse() } });
    node = fwd ? E.v[i] : E.u[i];
  }
  map.getSource("paths").setData({ type: "FeatureCollection", features: [feat(r.shortest, "short"), feat(r.coolest, "cool"), ...indoor] });
  const fill = (p, pre) => {
    $(`${pre}-len`).textContent = `${Math.round(p.length)} m`;
    $(`${pre}-sun`).innerHTML = mp(p.sun, p.length);
    $(`${pre}-sh`).innerHTML = mp(p.tree + p.bldg, p.length);
    $(`${pre}-in`).innerHTML = p.indoor > 0 ? mp(p.indoor, p.length) : "–";
  };
  fill(r.shortest, "s"); fill(r.coolest, "c");
  const c = r.coolest, seg = [["sun", COLORS.sun], ["tree", COLORS.tree], ["bldg", COLORS.bldg], ["indoor", COLORS.indoor]];
  $("cbar").innerHTML = seg.map(([k, col]) => `<span title="${k}" style="width:${(100 * c[k]) / c.length}%;background:${col}"></span>`).join("");
  const dSun = r.shortest.sun - c.sun, dLen = c.length - r.shortest.length;
  const dC = (r.shortest.C - c.C) / r.shortest.C;
  $("verdict").innerHTML = dSun <= 0.05
    ? (c.indoor > 0 && dC > 0.005
      ? `No route with less sun, but the coolest walk spends <b class="indoor">${Math.round(c.indoor)} m</b> indoors: ${pct(dC, 0)} lower heat exposure.`
      : "No cooler option on this trip.")
    : `The coolest walk avoids <b class="cool">${Math.round(dSun)} m</b> of sun (${pct(dSun / r.shortest.sun, 0)}) for <b>${dLen >= 0 ? "+" : "−"}${Math.abs(Math.round(dLen))} m</b> of walking.`;
  $("result").hidden = false;
}

map.once("style.load", () => {            // sources exist from here; no need to wait for basemap tiles
  const p = hero.opener[0];
  const a = p.shortest.coords[0], b = p.shortest.coords[p.shortest.coords.length - 1];
  pts = [snap(a[0], a[1]).node, snap(b[0], b[1]).node];
  render();
  // frame the first walk in the part of the map the panel leaves free
  const all = [...p.shortest.coords, ...p.coolest.coords], side = $("side").getBoundingClientRect();
  const lons = all.map((c) => c[0]), lats = all.map((c) => c[1]), narrow = window.innerWidth <= 760;
  map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], { duration: start ? 1600 : 0, maxZoom: 16.5,
    padding: narrow ? { top: 40, left: 30, right: 30, bottom: window.innerHeight - side.top + 20 }
      : { top: 50, bottom: 50, left: side.right + 40, right: 60 } });
});
map.on("click", (e) => {
  const { lng, lat } = e.lngLat;
  const s = snap(lng, lat);
  if (s.dist > MAX_SNAP_M) {
    const outside = lng < lon0 || lng > lon1 || lat < lat0 || lat > lat1;
    msg(outside ? "That point is outside the University City demo network. Pick a point on the coloured sidewalks."
      : `No sidewalk within ${MAX_SNAP_M} m of that point. Pick a point closer to a coloured sidewalk.`);
    return;
  }
  if (pts.length === 1 && s.node === pts[0]) { msg("The end point is the same as the start. Pick a different end point."); return; }
  msg("");
  pts = pts.length >= 2 ? [s.node] : [...pts, s.node];
  if (pts.length === 1) msg("Start set. Now click an end point.");
  render();
});
timeControl($("time"), t, (v) => { t = v; render(); });
$("scen").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  scenario = b.dataset.s;
  document.querySelectorAll("#scen button").forEach((x) => x.classList.toggle("on", x === b));
  render();
});
$("reset").addEventListener("click", () => { pts = []; msg(""); render(); });
$("swap").addEventListener("click", () => { if (pts.length === 2) { pts = [pts[1], pts[0]]; render(); } });
