// Home page: a scroll story over one fixed map; each screen sets the map view and layers for its one sentence.
import { VERSION, nav, baseStyle, getJSON, shadeRamp, pmtilesProtocol, COLORS, CLUSTERS, GRID_RAMPS, ATTR, DEMO_CENTER, pct, newMap,
  routeLayers, ROUTE_LAYERS } from "./common.js?v=dev";

nav("index.html");
pmtilesProtocol();

// create the map first so the basemap loads while the data download
const EMPTY = { type: "FeatureCollection", features: [] };
const map = newMap({
  container: "map", center: DEMO_CENTER, zoom: 15.6, pitch: 0, interactive: false,
  style: baseStyle({
    city: { type: "vector", url: `pmtiles://data/city_shade.pmtiles?v=${VERSION}`, attribution: `${ATTR.shade}; ${ATTR.chm}; ${ATTR.osm}` },
    grid: { type: "geojson", data: EMPTY },
    bld: { type: "geojson", data: EMPTY },
    hero: { type: "geojson", data: EMPTY },
  }, [
    // street-level base: the sidewalks as plain grey lines (the Esri tiles carry street names there and fade out)
    { id: "lines", type: "line", source: "city", "source-layer": "sidewalks", minzoom: 13,
      paint: { "line-color": "#3b3f47", "line-width": ["interpolate", ["linear"], ["zoom"], 13, 0.4, 16, 1.3] } },
    { id: "city", type: "line", source: "city", "source-layer": "sidewalks", minzoom: 13, layout: { visibility: "none", "line-cap": "round" },
      paint: { "line-color": shadeRamp("s7"), "line-width": ["interpolate", ["linear"], ["zoom"], 13, 0.6, 16, 2.4], "line-opacity": 0.9 } },
    { id: "grid", type: "fill", source: "grid", layout: { visibility: "none" },
      paint: { "fill-color": "#555", "fill-opacity": 0.7, "fill-outline-color": "rgba(7,8,11,0.6)" } },
    { id: "bld", type: "line", source: "bld", layout: { visibility: "none" }, paint: { "line-color": "#ffffff", "line-width": 1.2, "line-opacity": 0.5 } },
    ...routeLayers("hero"),
  ], { labels: false, base: ["interpolate", ["linear"], ["zoom"], 12.5, 0.6, 13.5, 0] }),
});
const styleReady = new Promise((res) => map.once("style.load", res));
const [hero, summary, layers, grid] = await Promise.all([
  getJSON("data/hero.json"), getJSON("data/summary.json"), getJSON("data/demo_layers.json"), getJSON("data/grid.geojson")]);
const $ = (id) => document.getElementById(id);
const set = (id, v) => { const el = $(id); if (el) el.textContent = v; };

// ---- headline numbers: walks home from frequent bus stops, 300-800 m, 15:00 (the walks the grid summarises)
const b6 = summary.phase6_at_15h.find((r) => r.sample === "city" && r.mode === "bus" && r.length_bin === "300-800");
const b7 = summary.phase7_by_mode_bin_15h.find((r) => r.mode === "bus" && r.length_bin === "300-800");
const share = summary.phase8_at_15h.find((r) => r.set === "campus" && r.scenario === "all").shade_uses_indoor;
// shares as words: 0.097 -> "about a tenth", 0.61 -> "Six in ten", 0.18 -> "about one in five"
const NUM = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];
const PART = { 2: "half", 3: "a third", 4: "a quarter", 5: "a fifth", 6: "a sixth", 7: "a seventh", 8: "an eighth", 9: "a ninth", 10: "a tenth" };
const part = (x) => { const n = Math.round(1 / x); return PART[n] ? `about ${PART[n]}` : pct(x, 0); };
const inTen = (x) => { const n = Math.round(10 * x); return n >= 1 && n <= 9 ? `${NUM[n][0].toUpperCase()}${NUM[n].slice(1)} in ten` : pct(x, 0); };
const oneIn = (x) => { const n = Math.round(1 / x); return n >= 2 && n <= 10 ? `about one in ${NUM[n]}` : pct(x, 0) + " of"; };
set("n-km", Math.round(summary.sidewalk_km).toLocaleString("en-US"));
set("s-sun", pct(b6.short_sun_share, 0));
set("s-red", "−" + pct(b6.sun_red_mean, 1));
set("s-red-words", part(b6.sun_red_mean));
set("s-det", "+" + pct(b6.detour_mean, 1));
set("s-noalt", pct(b7.no_alt_share, 0));
set("s-noalt-words", inTen(b7.no_alt_share));
set("s-ind-words", oneIn(share));
$("clusters").innerHTML = CLUSTERS.map(([, n, c]) => `<span><span class="legend-swatch" style="background:${c};height:10px"></span>${n.toLowerCase()}</span>`).join("");
for (const [id, key] of [["sunny", "sun_share"], ["cooler", "sun_red"]]) {
  const r = GRID_RAMPS[key];
  $(`r-${id}`).style.background = `linear-gradient(to right,${r.stops.filter((_, i) => i % 2).join(",")})`;
  $(`e-${id}`).innerHTML = `<span>${r.lo}</span><span>${r.hi}</span>`;
}

// ---- walks: shortest / coolest lines with labels written next to each line
const len = (p) => Math.round(p.length), sunPct = (p) => pct(p.sun / p.length, 0);
// a zero-size marker element; the label inside sits to its left or right (class l / r)
function tagEl(cls) { const el = document.createElement("div"); el.className = "tagwrap"; el.innerHTML = `<div class="tag ${cls}"></div>`; return el; }
const tags = { short: new maplibregl.Marker({ element: tagEl("sun") }), cool: new maplibregl.Marker({ element: tagEl("cool") }) };
// the point of each line farthest from the other line, so the two labels never sit on top of each other
function farthest(a, b) {
  const k = Math.cos((a[0][1] * Math.PI) / 180);
  let best = a[Math.floor(a.length / 2)], bd = -1;
  for (const p of a) {
    let d = Infinity;
    for (const q of b) d = Math.min(d, ((p[0] - q[0]) * k) ** 2 + (p[1] - q[1]) ** 2);
    if (d > bd) { bd = d; best = p; }
  }
  return best;
}
function placeTags(p, indoor) {
  const ps = farthest(p.shortest.coords, p.coolest.coords), pc = farthest(p.coolest.coords, p.shortest.coords);
  const left = ps[0] <= pc[0];                         // the shortest label goes on the side its line lies
  const text = { short: `${len(p.shortest)} m · ${sunPct(p.shortest)} sun`,
    cool: `${len(p.coolest)} m · ${sunPct(p.coolest)} sun` + (indoor ? `\n${Math.round(p.coolest.indoor)} m indoors` : "") };
  for (const [k, pt, onLeft] of [["short", ps, left], ["cool", pc, !left]]) {
    const el = tags[k].getElement();
    el.firstChild.textContent = text[k];
    el.classList.toggle("l", onLeft); el.classList.toggle("r", !onLeft);
    tags[k].setLngLat(pt).addTo(map);
  }
}
// after the map moves: a label that would leave the screen goes to the other side of its point
function keepTagsInView() {
  for (const mk of Object.values(tags)) {
    const el = mk.getElement(), r = el.firstChild.getBoundingClientRect();
    if (!r.width) continue;
    if (r.right > window.innerWidth - 6) { el.classList.add("l"); el.classList.remove("r"); }
    else if (r.left < 6) { el.classList.add("r"); el.classList.remove("l"); }
  }
}
map.on("moveend", keepTagsInView);
const showTag = (k, on) => tags[k].getElement().classList.toggle("on", on);
const hideTags = () => { showTag("short", false); showTag("cool", false); };
const routeFeatures = (p, fs = 1, fc = 1, indoor = false) => ({ type: "FeatureCollection", features: [
  { type: "Feature", properties: { role: "short" }, geometry: { type: "LineString", coordinates: partial(p.shortest.coords, fs) } },
  { type: "Feature", properties: { role: "cool" }, geometry: { type: "LineString", coordinates: partial(p.coolest.coords, fc) } },
  ...(indoor ? [{ type: "Feature", properties: { role: "indoor" }, geometry: { type: "MultiLineString", coordinates: p.coolest.indoor_segs } }] : [])] });

// ---- opening animation: draw the shortest walk, then the coolest, then switch to the next station
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
const PAIRS = hero.opener;
let pairIdx = 0, t0 = performance.now(), heroOn = true, current = "hero", drawn = false, raf = 0;
function showCase(p) {
  const top = p.top_share < 0.01 ? "top 1%" : `top ${Math.ceil(100 * p.top_share)}%`;
  const line = p.line.startsWith("Regional") ? "Regional Rail" : p.line[0];
  set("h-case", `${p.station.replace(/-/g, "–")} (${line}) · June 21, 15:00 · ${top} of University City station walks`);
}
// frame the walk in the space the text leaves free; labels need room beside the lines
function fitPair(p, panel, labels = false) {
  const all = [...p.shortest.coords, ...p.coolest.coords];
  const lons = all.map((c) => c[0]), lats = all.map((c) => c[1]);
  const H = window.innerHeight, W = window.innerWidth, side = labels ? (W > 760 ? 150 : 90) : 40;
  const pad = { top: 72, bottom: 40, left: side, right: side };
  if (panel && W > 760) pad.left = Math.min(panel.getBoundingClientRect().right + 30 + (labels ? 110 : 0), W * 0.6);
  else if (panel) pad.bottom = Math.min(H - panel.getBoundingClientRect().top + 20, H * 0.6);
  else {
    const box = document.querySelector(".hero .content").getBoundingClientRect();
    if (W >= 1000) pad.left = box.right + 40;
    else pad.bottom = Math.min(H - box.top + 12, H * 0.7);
  }
  map.setPadding({ top: 0, bottom: 0, left: 0, right: 0 });      // flyTo below leaves its padding on the map
  map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], { padding: pad, duration: 1200, maxZoom: 17 });
}
const examplePanel = () => (current === "example" ? document.querySelector('[data-step="example"] .panel') : null);
function nextPair() {
  const p = PAIRS[pairIdx];
  showCase(p); placeTags(p, false); hideTags();
  fitPair(p, examplePanel(), current === "example");
}
// the loop runs only while the opening or the example screen is on
function frame(now) {
  raf = 0;
  if (!heroOn) return;
  const p = PAIRS[pairIdx];
  const dt = (now - t0) / 1000, D = 3.2;
  const fs = reduce ? 1 : Math.min(dt / D, 1), fc = reduce ? 1 : Math.min(Math.max((dt - D * 0.6) / D, 0), 1);
  if (!drawn) map.getSource("hero").setData(routeFeatures(p, fs, fc));
  drawn = fc >= 1;                      // both lines complete: stop re-sending them until the next pair
  const labels = current === "example";
  showTag("short", labels && fs >= 1); showTag("cool", labels && fc >= 1);
  if (dt > 2 * D + 2.5 && !reduce) { pairIdx = (pairIdx + 1) % PAIRS.length; t0 = now; drawn = false; nextPair(); }
  raf = requestAnimationFrame(frame);
}
const startLoop = () => {
  if (!heroOn) { t0 = performance.now(); drawn = false; }     // coming back from another screen: draw the walk again
  heroOn = true; if (!raf) raf = requestAnimationFrame(frame);
};

// ---- sidewalk shade through the day (screen 02): June 21, 08:00-19:00, one hour every 0.9 s
let hourTimer = null, hour = 15;
const setHour = (h) => { hour = h; map.setPaintProperty("city", "line-color", shadeRamp(`s${h - 8}`)); set("h-hour", `${String(h).padStart(2, "0")}:00`); };
const stopHours = () => { clearInterval(hourTimer); hourTimer = null; };

let ready = false;      // set once the style is in and the data are on the map
styleReady.then(() => {
  map.getSource("grid").setData(grid);
  map.getSource("bld").setData(layers.buildings);
  ready = true;
  showCase(PAIRS[0]); placeTags(PAIRS[0], false); hideTags();
  STEPS[current]();
});

// ---- scroll steps: each screen sets the map view and layers
const vis = (id, on) => map.getLayer(id) && map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
const routes = (on) => ROUTE_LAYERS("hero").forEach((id) => vis(id, on));
const gridBy = (key) => {
  const fill = key === "cluster" ? ["match", ["get", "cluster"], ...CLUSTERS.flatMap(([k, , c]) => [k, c]), "#555"]
    : ["interpolate", ["linear"], ["coalesce", ["get", key], 0], ...GRID_RAMPS[key].stops];
  map.setPaintProperty("grid", "fill-color", fill); vis("grid", true);
};
// centre a view in the part of the screen the step panel leaves free
const fly = (view) => {
  const p = document.querySelector(`[data-step="${current}"] .panel`)?.getBoundingClientRect(), W = window.innerWidth;
  const padding = !p ? {} : W > 760 ? { left: p.right, top: 52, right: 0, bottom: 0 } : { bottom: window.innerHeight - p.top, top: 52, left: 0, right: 0 };
  map.flyTo({ ...view, padding });
};
const CITY = { center: [-75.135, 39.995], zoom: 10.9, duration: 2200 };
const scene = ({ city = false, grid = false, bld = false, route = false, cityOpacity = 0.9 } = {}) => {
  if (current !== "shade") stopHours();
  if (current !== "hero" && current !== "example") heroOn = false;
  vis("city", city); vis("bld", bld); routes(route); if (!grid) vis("grid", false);
  map.setPaintProperty("city", "line-opacity", cityOpacity);
  if (!route || current !== "example") hideTags();
};
const STEPS = {
  hero: () => { scene({ route: true }); drawn = false; startLoop(); fitPair(PAIRS[pairIdx]); },
  example: () => { scene({ route: true }); drawn = false; placeTags(PAIRS[pairIdx], false); startLoop(); fitPair(PAIRS[pairIdx], examplePanel(), true); },
  shade: () => {
    scene({ city: true }); setHour(reduce ? 15 : 8);
    fly({ center: [-75.1652, 39.9502], zoom: 15.2, duration: 2000 });
    if (!reduce && !hourTimer) hourTimer = setInterval(() => setHour(hour >= 19 ? 8 : hour + 1), 900);
  },
  sunny: () => { scene({ grid: true }); gridBy("sun_share"); fly(CITY); },
  cooler: () => { scene({ grid: true }); gridBy("sun_red"); fly(CITY); },
  grid: () => { scene({ grid: true }); gridBy("cluster"); fly(CITY); },
  indoor: () => {
    // the sidewalk shade uses the same orange-cyan colours as the walks, so it is dimmed under them
    scene({ city: true, bld: true, route: true, cityOpacity: 0.35 }); setHour(15);
    const p = hero.indoor;
    map.getSource("hero").setData(routeFeatures(p, 1, 1, true));
    placeTags(p, true); showTag("short", true); showTag("cool", true);
    fitPair(p, document.querySelector('[data-step="indoor"] .panel'), true);
  },
  end: () => { scene({ city: true, bld: true, cityOpacity: 0.6 }); setHour(15); fly({ center: DEMO_CENTER, zoom: 15, duration: 2000 }); },
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

// keys and presentation clickers (PageDown / PageUp) move exactly one screen; a focused map canvas would swallow them otherwise
const SCREENS = [...document.querySelectorAll("section[data-step]")];
let target = null, targetTimer = 0;
const KEYS = { PageDown: 1, ArrowDown: 1, ArrowRight: 1, PageUp: -1, ArrowUp: -1, ArrowLeft: -1, Home: -99, End: 99 };
addEventListener("keydown", (e) => {
  if (e.altKey || e.ctrlKey || e.metaKey || /^(INPUT|TEXTAREA|SELECT|BUTTON|A)$/.test(e.target.tagName)) return;
  const step = e.key === " " ? (e.shiftKey ? -1 : 1) : KEYS[e.key];
  if (!step) return;
  e.preventDefault();
  // a press during the smooth scroll counts from the screen already on its way
  const now = target ?? SCREENS.reduce((b, s, i) => (Math.abs(s.getBoundingClientRect().top) < Math.abs(SCREENS[b].getBoundingClientRect().top) ? i : b), 0);
  target = Math.max(0, Math.min(SCREENS.length - 1, now + step));
  clearTimeout(targetTimer); targetTimer = setTimeout(() => { target = null; }, 1000);
  SCREENS[target].scrollIntoView({ behavior: reduce ? "auto" : "smooth" });
});
