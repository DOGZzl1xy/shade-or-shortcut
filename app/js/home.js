// Home page: opening animation (shortest vs coolest walk) and a scroll story over one fixed map.
import { nav, baseStyle, getJSON, shadeRamp, pmtilesProtocol, COLORS, CLUSTERS, ATTR, DEMO_CENTER, pct, newMap } from "./common.js";

nav("index.html");
pmtilesProtocol();

// create the map first so the basemap loads while the data download
const EMPTY = { type: "FeatureCollection", features: [] };
const map = newMap({
  container: "map", center: DEMO_CENTER, zoom: 15.6, pitch: 0, interactive: false,
  style: baseStyle({
    city: { type: "vector", url: "pmtiles://data/city_shade.pmtiles", attribution: `${ATTR.shade}; ${ATTR.chm}; ${ATTR.osm}` },
    grid: { type: "geojson", data: EMPTY },
    bld: { type: "geojson", data: EMPTY },
    hero: { type: "geojson", data: EMPTY },
    indoor: { type: "geojson", data: EMPTY },
  }, [
    { id: "city", type: "line", source: "city", "source-layer": "sidewalks", minzoom: 13, layout: { visibility: "none", "line-cap": "round" },
      paint: { "line-color": shadeRamp("s7"), "line-width": ["interpolate", ["linear"], ["zoom"], 13, 0.6, 16, 2.4], "line-opacity": 0.9 } },
    { id: "grid", type: "fill", source: "grid", layout: { visibility: "none" },
      paint: { "fill-color": ["match", ["get", "cluster"], ...CLUSTERS.flatMap(([k, , c]) => [k, c]), "#555"], "fill-opacity": 0.7,
        "fill-outline-color": "rgba(7,8,11,0.6)" } },
    { id: "bld", type: "line", source: "bld", layout: { visibility: "none" }, paint: { "line-color": "#ffffff", "line-width": 1.2, "line-opacity": 0.7 } },
    { id: "hero-glow", type: "line", source: "hero", layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": ["get", "color"], "line-width": 14, "line-opacity": 0.18, "line-blur": 6 } },
    { id: "hero", type: "line", source: "hero", layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": ["get", "color"], "line-width": 4 } },
    { id: "indoor", type: "line", source: "indoor", layout: { "line-cap": "round" }, paint: { "line-color": COLORS.indoor, "line-width": 6 } },
  ]),
});
const styleReady = new Promise((res) => map.once("style.load", res));
const [hero, summary, layers, grid] = await Promise.all([
  getJSON("data/hero.json"), getJSON("data/summary.json"), getJSON("data/demo_layers.json"), getJSON("data/grid.geojson")]);

// ---- headline numbers from the exported summaries (phases 6-8)
const p6 = (mode, bin) => summary.phase6_at_15h.find((r) => r.sample === "city" && r.mode === mode && r.length_bin === bin);
const p7 = (mode, bin) => summary.phase7_by_mode_bin_15h.find((r) => r.mode === mode && r.length_bin === bin);
const p8 = (set, sc) => summary.phase8_at_15h.find((r) => r.set === set && r.scenario === sc);
const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
set("s-bus-sun", pct(p6("bus", "300-800").short_sun_share, 0));
set("s-rap-sun", pct(p6("rapid", "300-800").short_sun_share, 0));
set("s-red", "−" + pct(p6("rapid", "300-800").sun_red_mean, 0));
set("s-det", "+" + pct(p6("rapid", "300-800").detour_mean, 1));
set("s-noalt", pct(p7("rapid", "300-800").no_alt_share, 0));
set("s-ind", pct(p8("campus", "all").shade_uses_indoor, 0));
set("n-km", Math.round(summary.sidewalk_km).toLocaleString("en-US"));
set("n-trips", summary.city_trips.toLocaleString("en-US"));
set("n-bld", summary.annotated_buildings);
document.getElementById("h-city").innerHTML = `Across Philadelphia, a 300–800 m walk from a rapid-transit station avoids
  <b class="cool">${pct(p6("rapid", "300-800").sun_red_mean, 0)}</b> of its sun on average, and
  <b>${pct(p7("rapid", "300-800").no_alt_share, 0)}</b> of these walks have no cooler option.`;
// "about one in five" from a share
const WORDS = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];
const share = p8("campus", "all").shade_uses_indoor, n = Math.round(1 / share);
set("s-ind-words", n >= 2 && n <= 10 ? `about one in ${WORDS[n]}` : pct(share, 0) + " of");
const hi = hero.indoor;
set("s-ind-case", `Shown: a campus walk, June 21 15:00. Shortest (outdoors) ${Math.round(hi.shortest.length)} m, ${pct(hi.shortest.sun / hi.shortest.length, 0)} in sun; ` +
  `coolest ${Math.round(hi.coolest.length)} m, ${pct(hi.coolest.sun / hi.coolest.length, 0)} in sun, ${Math.round(hi.coolest.indoor)} m indoors.`);

// ---- opening animation: draw the shortest walk, then the coolest, then switch pair
const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
function partial(coords, f) {
  if (f >= 1) return coords;
  const seg = [];
  let total = 0;
  for (let i = 1; i < coords.length; i++) seg.push(total += Math.hypot(coords[i][0] - coords[i - 1][0], coords[i][1] - coords[i - 1][1]));
  const target = f * total, out = [coords[0]];
  for (let i = 1; i < coords.length; i++) {
    if (seg[i - 1] <= target) { out.push(coords[i]); continue; }
    const prev = i > 1 ? seg[i - 2] : 0, t = (target - prev) / (seg[i - 1] - prev);
    out.push([coords[i - 1][0] + t * (coords[i][0] - coords[i - 1][0]), coords[i - 1][1] + t * (coords[i][1] - coords[i - 1][1])]);
    break;
  }
  return out.length > 1 ? out : [coords[0], coords[0]];
}
let pairIdx = 0, t0 = performance.now(), heroOn = true, current = "hero", drawn = false;
function showNumbers(p) {
  const top = p.top_share < 0.01 ? "top 1%" : `top ${Math.ceil(100 * p.top_share)}%`;
  set("h-case", `From ${p.station} (${p.line.startsWith("Regional") ? "Regional Rail" : p.line[0]}) · June 21, 15:00 · one of the larger gains, ${top} of station walks`);
  set("h-short", `${Math.round(p.shortest.length)} m`);
  set("h-cool", `${Math.round(p.coolest.length)} m`);
  set("h-short-sub", `${pct(p.shortest.sun / p.shortest.length, 0)} in sun`);
  set("h-cool-sub", `${pct(p.coolest.sun / p.coolest.length, 0)} in sun`);
}
const PAIRS = hero.opener;
// frame the walk in the space the text leaves free: above the hero text, or right of a step panel
function fitPair(p, panel) {
  const all = [...p.shortest.coords, ...p.coolest.coords];
  const lons = all.map((c) => c[0]), lats = all.map((c) => c[1]);
  const H = window.innerHeight, W = window.innerWidth, pad = { top: 72, bottom: 40, left: 40, right: 40 };
  if (panel && W > 760) pad.left = Math.min(panel.getBoundingClientRect().right + 30, W * 0.55);
  else if (panel) pad.bottom = Math.min(H - panel.getBoundingClientRect().top + 20, H * 0.6);
  else {
    const box = document.querySelector(".hero .content").getBoundingClientRect();
    if (W >= 1000) pad.left = box.right + 40;
    else pad.bottom = Math.min(H - box.top + 12, H * 0.7);
  }
  map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], { padding: pad, duration: 1200, maxZoom: 17 });
}
function frame(now) {
  if (heroOn && map.getSource("hero")) {
    const p = PAIRS[pairIdx];
    const dt = (now - t0) / 1000, D = 3.2;
    const fs = reduce ? 1 : Math.min(dt / D, 1), fc = reduce ? 1 : Math.min(Math.max((dt - D * 0.6) / D, 0), 1);
    if (!drawn) map.getSource("hero").setData({ type: "FeatureCollection", features: [
      { type: "Feature", properties: { color: COLORS.sun }, geometry: { type: "LineString", coordinates: partial(p.shortest.coords, fs) } },
      { type: "Feature", properties: { color: COLORS.cool }, geometry: { type: "LineString", coordinates: partial(p.coolest.coords, fc) } }] });
    drawn = fc >= 1;                      // both lines complete: stop re-sending them until the next pair
    if (dt > 2 * D + 2.5 && !reduce) {
      pairIdx = (pairIdx + 1) % PAIRS.length; t0 = now; drawn = false;
      fitPair(PAIRS[pairIdx]); showNumbers(PAIRS[pairIdx]);
    }
  }
  requestAnimationFrame(frame);
}
let ready = false;      // set once the style is in and the data are on the map
styleReady.then(() => {
  map.getSource("grid").setData(grid);
  map.getSource("bld").setData(layers.buildings);
  ready = true; showNumbers(PAIRS[0]); fitPair(PAIRS[0]); requestAnimationFrame(frame);
  if (current !== "hero") STEPS[current]();
});

// ---- scroll steps: each section sets the map view and layers
const vis = (id, on) => map.getLayer(id) && map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
const STEPS = {
  hero: () => { heroOn = true; drawn = false; vis("indoor", false); vis("hero", true); vis("hero-glow", true); vis("city", false); vis("grid", false); vis("bld", false); fitPair(PAIRS[pairIdx]); },
  shade: () => { heroOn = false; vis("indoor", false); vis("hero", false); vis("hero-glow", false); vis("grid", false); vis("bld", false); vis("city", true);
    map.flyTo({ center: [-75.1652, 39.9502], zoom: 15.2, duration: 2000 }); },
  sunny: () => { vis("city", true); vis("grid", false); map.flyTo({ center: [-75.1690, 39.9330], zoom: 14.6, duration: 2000 }); },
  cooler: () => { vis("city", true); vis("grid", false); map.flyTo({ center: [-75.1470, 39.9690], zoom: 14.4, duration: 2000 }); },
  grid: () => { vis("indoor", false); vis("city", false); vis("bld", false); vis("hero", false); vis("hero-glow", false); vis("grid", true);
    map.flyTo({ center: [-75.135, 39.995], zoom: 10.9, duration: 2200 }); },
  indoor: () => { vis("grid", false); vis("city", true); vis("bld", true); heroOn = false; vis("hero", true); vis("hero-glow", true);
    const p = hero.indoor;
    map.getSource("hero").setData({ type: "FeatureCollection", features: [
      { type: "Feature", properties: { color: COLORS.sun }, geometry: { type: "LineString", coordinates: p.shortest.coords } },
      { type: "Feature", properties: { color: COLORS.cool }, geometry: { type: "LineString", coordinates: p.coolest.coords } }] });
    map.getSource("indoor").setData({ type: "FeatureCollection", features: [{ type: "Feature", properties: {},
      geometry: { type: "MultiLineString", coordinates: p.coolest.indoor_segs } }] });
    vis("indoor", true);
    fitPair(p, document.querySelector('[data-step="indoor"] .panel')); },
  end: () => { vis("indoor", false); vis("grid", false); vis("bld", true); map.flyTo({ center: DEMO_CENTER, zoom: 15, duration: 2000 }); },
};
const io = new IntersectionObserver((entries) => {
  for (const en of entries) {
    if (!en.isIntersecting) continue;
    const s = en.target.dataset.step;
    if (s === current) continue;
    current = s;
    if (ready) STEPS[s]();
  }
}, { threshold: 0.55 });
document.querySelectorAll("section[data-step]").forEach((s) => io.observe(s));
