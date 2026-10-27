// Home page: a scroll story over one fixed map; each screen sets the map view and layers for its one sentence.
import { VERSION, nav, baseStyle, getJSON, shadeRamp, pmtilesProtocol, COLORS, CLUSTERS, GRID_RAMPS, ATTR, DEMO_CENTER, pct, newMap,
  routeLayers, ROUTE_LAYERS } from "./common.js?v=94fd121";

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
    mass: { type: "geojson", data: EMPTY, attribution: "Buildings: City of Philadelphia footprints, heights from NOAA 2022 LiDAR" },
    types: { type: "geojson", data: EMPTY },
    st: { type: "geojson", data: EMPTY },
  }, [
    // street level: the sidewalks as thin grey lines over the dimmed Esri base, which carries the street names
    { id: "lines", type: "line", source: "city", "source-layer": "sidewalks", minzoom: 13,
      paint: { "line-color": "#3b3f47", "line-width": ["interpolate", ["linear"], ["zoom"], 13, 0.4, 16, 1.3] } },
    { id: "city", type: "line", source: "city", "source-layer": "sidewalks", minzoom: 13, layout: { visibility: "none", "line-cap": "round" },
      paint: { "line-color": shadeRamp("s7"), "line-width": ["interpolate", ["linear"], ["zoom"], 13, 0.6, 16, 2.4], "line-opacity": 0.9 } },
    { id: "grid", type: "fill", source: "grid", layout: { visibility: "none" },
      paint: { "fill-color": "#555", "fill-opacity": 0.7, "fill-outline-color": "rgba(7,8,11,0.6)" } },
    // annotated buildings, filled as on the route tool so the last screen leads straight into it
    { id: "bld", type: "fill", source: "bld", layout: { visibility: "none" },
      paint: { "fill-color": "rgba(180,140,255,0.22)", "fill-outline-color": "rgba(180,140,255,0.55)" } },
    // what shades each campus sidewalk at 15:00 (opening screens); it fades in, "spread onto the map"
    { id: "types", type: "line", source: "types", filter: ["!=", ["get", "t"], "indoor"], layout: { visibility: "none", "line-cap": "round" },
      paint: { "line-color": ["match", ["get", "t"], "tree", COLORS.tree, "bldg", COLORS.bldg, COLORS.sun], "line-width": 2.6,
        "line-opacity": 0, "line-opacity-transition": { duration: 1500 } } },
    { id: "types-in", type: "line", source: "types", filter: ["==", ["get", "t"], "indoor"], layout: { visibility: "none", "line-cap": "round" },
      paint: { "line-color": COLORS.indoor, "line-width": 3.2, "line-dasharray": [0, 1.8], "line-opacity": 0, "line-opacity-transition": { duration: 1500 } } },
    { id: "st", type: "circle", source: "st", layout: { visibility: "none" },
      paint: { "circle-radius": 3.2, "circle-color": "#e8e6df", "circle-stroke-color": "#07080b", "circle-stroke-width": 1 } },
    // white-box model of the campus (screen 10); buildings opened to walkers in pale violet
    { id: "mass", type: "fill-extrusion", source: "mass", layout: { visibility: "none" },
      paint: { "fill-extrusion-color": ["case", ["get", "open"], "#c9b6f2", "#e8e6df"], "fill-extrusion-height": ["get", "h"],
        "fill-extrusion-opacity": 0.55, "fill-extrusion-vertical-gradient": true } },
    ...routeLayers("hero"),
  ], { base: 0.55, labelOpacity: 0.45 }),
});
const styleReady = new Promise((res) => map.once("style.load", res));
// layers needed only further down the story load in the background
getJSON("data/massing.geojson").then((d) => styleReady.then(() => map.getSource("mass").setData(d)));
getJSON("data/stations.geojson").then((d) => styleReady.then(() => map.getSource("st").setData(d)));
getJSON("data/shade_types.geojson").then((d) => styleReady.then(() => map.getSource("types").setData(d)));
const [hero, summary, layers, grid] = await Promise.all([
  getJSON("data/hero.json"), getJSON("data/summary.json"), getJSON("data/demo_layers.json"), getJSON("data/grid.geojson")]);
const $ = (id) => document.getElementById(id);
// shade-type close-up: a spot with all four states within a block, one labelled example of each
const TF = hero.types_focus, TYPES_AT = TF.center;
const PILL = { sun: ["sun", COLORS.sun], tree: ["tree shade", COLORS.tree], bldg: ["building shade", COLORS.bldg], indoor: ["indoors", COLORS.indoor] };
// (the fade sits on an inner element: MapLibre sets an inline opacity on the marker element itself)
const pills = TF.labels.map(({ t, at }) => {
  const el = document.createElement("div");
  el.innerHTML = `<div class="pill" style="color:${PILL[t][1]}">${PILL[t][0]}</div>`;
  return new maplibregl.Marker({ element: el }).setLngLat(at);
});
const showPills = (on) => pills.forEach((m) => { if (on) m.addTo(map); m.getElement().firstChild.classList.toggle("on", on); });
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
// opening screens: sun height and the city's sidewalks in sun / tree shade / building shade at 15:00 on June 21
const elev = `${Math.round(summary.sun_elevation_15)}°`, ss = summary.sidewalk_share_15;
set("s-elev", elev); document.querySelectorAll(".n-elev").forEach((el) => { el.textContent = elev; });
set("s-sunny", pct(ss.sun, 0)); set("d-tree", pct(ss.tree, 0)); set("d-bldg", pct(ss.bldg, 0)); set("d-indoor", summary.annotated_buildings);
set("n-trips", summary.city_trips.toLocaleString("en-US"));
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

// ---- walks: shortest / coolest lines, each labelled next to its own line where the label covers neither walk,
// the other label nor the text panel
const len = (p) => Math.round(p.length), sunPct = (p) => pct(p.sun / p.length, 0);
const SIDES = ["l", "r", "u", "d"];             // label left of, right of, above or below its point
function tagEl(cls) { const el = document.createElement("div"); el.className = "tagwrap"; el.innerHTML = `<div class="tag ${cls}"></div>`; return el; }
const tags = { short: new maplibregl.Marker({ element: tagEl("sun") }), cool: new maplibregl.Marker({ element: tagEl("cool") }) };
let tagLines = null;
function placeTags(p, indoor) {
  tagLines = { short: p.shortest.coords, cool: p.coolest.coords };
  const text = { short: `${len(p.shortest)} m · ${sunPct(p.shortest)} sun`,
    cool: `${len(p.coolest)} m · ${sunPct(p.coolest)} sun` + (indoor ? `\n${Math.round(p.coolest.indoor)} m indoors` : "") };
  for (const k of ["short", "cool"]) {
    tags[k].getElement().firstChild.textContent = text[k];
    tags[k].setLngLat(tagLines[k][0]).addTo(map);
  }
  arrangeTags();
}
// a line in screen pixels, sampled every 4 px
function screenPoints(coords) {
  const q = coords.map((c) => map.project(c)), out = [];
  for (let i = 1; i < q.length; i++) {
    const n = Math.max(1, Math.ceil(Math.hypot(q[i].x - q[i - 1].x, q[i].y - q[i - 1].y) / 4));
    for (let j = 0; j < n; j++) out.push({ x: q[i - 1].x + ((q[i].x - q[i - 1].x) * j) / n, y: q[i - 1].y + ((q[i].y - q[i - 1].y) * j) / n });
  }
  return out;
}
const hit = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
function arrangeTags() {
  if (!tagLines) return;
  const pts = { short: screenPoints(tagLines.short), cool: screenPoints(tagLines.cool) }, all = [...pts.short, ...pts.cool];
  const pr = document.querySelector(`[data-step="${current}"] .panel`)?.getBoundingClientRect();
  const panel = pr && { x: pr.left - 8, y: pr.top - 8, w: pr.width + 16, h: pr.height + 16 };
  const W = window.innerWidth, H = window.innerHeight, placed = [];
  for (const k of ["short", "cool"]) {
    const el = tags[k].getElement(), w = el.firstChild.offsetWidth + 8, h = el.firstChild.offsetHeight + 8;
    const own = pts[k], other = pts[k === "short" ? "cool" : "short"];
    let best = null;
    for (let i = 1; i < 12; i++) {
      const a = own[Math.floor((i * (own.length - 1)) / 12)];
      if (!a) continue;
      const near = other.some((o) => Math.hypot(o.x - a.x, o.y - a.y) < 14);   // a shared stretch would not say which walk
      for (const s of SIDES) {
        const r = s === "l" ? { x: a.x - 12 - w, y: a.y - h / 2 } : s === "r" ? { x: a.x + 12, y: a.y - h / 2 }
          : s === "u" ? { x: a.x - w / 2, y: a.y - 12 - h } : { x: a.x - w / 2, y: a.y + 12 };
        Object.assign(r, { w, h });
        const out = r.x < 6 || r.y < 56 || r.x + w > W - 6 || r.y + h > H - 30;
        const score = all.filter((q) => q.x > r.x && q.x < r.x + w && q.y > r.y && q.y < r.y + h).length
          + (near ? 60 : 0) + (out ? 1000 : 0) + (panel && hit(r, panel) ? 500 : 0) + (placed.some((o) => hit(r, o)) ? 800 : 0);
        if (!best || score < best.score) best = { score, a, s, r };
      }
    }
    if (!best) continue;
    placed.push(best.r);
    tags[k].setLngLat(map.unproject([best.a.x, best.a.y]));
    el.classList.remove(...SIDES); el.classList.add(best.s);
  }
}
map.on("moveend", arrangeTags);
const showTag = (k, on) => tags[k].getElement().classList.toggle("on", on);
const hideTags = () => { showTag("short", false); showTag("cool", false); };
// indoor pieces in the walking direction (their side offset follows the line direction, like the coolest walk's)
function along(segs, path) {
  const at = (q) => path.reduce((b, c, i) => ((c[0] - q[0]) ** 2 + (c[1] - q[1]) ** 2 < (path[b][0] - q[0]) ** 2 + (path[b][1] - q[1]) ** 2 ? i : b), 0);
  return segs.map((g) => (at(g[0]) > at(g[g.length - 1]) ? [...g].reverse() : g));
}
const routeFeatures = (p, fs = 1, fc = 1, indoor = false) => ({ type: "FeatureCollection", features: [
  { type: "Feature", properties: { role: "short" }, geometry: { type: "LineString", coordinates: partial(p.shortest.coords, fs) } },
  { type: "Feature", properties: { role: "cool" }, geometry: { type: "LineString", coordinates: partial(p.coolest.coords, fc) } },
  ...(indoor ? along(p.coolest.indoor_segs, p.coolest.coords).map((g) => (
    { type: "Feature", properties: { role: "indoor" }, geometry: { type: "LineString", coordinates: g } })) : [])] });

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
function fitPair(p, panel, labels = false, tilt = false) {
  const all = [...p.shortest.coords, ...p.coolest.coords];
  const lons = all.map((c) => c[0]), lats = all.map((c) => c[1]);
  const H = window.innerHeight, W = window.innerWidth, side = labels ? (W > 760 ? 150 : 90) : 40;
  const pad = { top: 72, bottom: W > 760 ? 72 : 40, left: side, right: side };   // wide screens: room for the credits line
  if (panel && W > 760) pad.left = Math.min(panel.getBoundingClientRect().right + 30 + (labels ? 110 : 0), W * 0.6);
  else if (panel) pad.bottom = Math.min(H - panel.getBoundingClientRect().top + 20, H * 0.6);
  else {
    const box = document.querySelector(".hero .content").getBoundingClientRect();
    if (W >= 1000) pad.left = box.right + 40;
    else pad.bottom = Math.min(H - box.top + 12, H * 0.7);
  }
  map.setPadding({ top: 0, bottom: 0, left: 0, right: 0 });      // flyTo below leaves its padding on the map
  const view = tilt ? { pitch: 55, bearing: -28 } : { pitch: 0, bearing: 0 };
  const bounds = [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]];
  // no camera fits when the padding is larger than the canvas (e.g. before the first resize): fall back to small margins
  const cam = map.cameraForBounds(bounds, { padding: pad, maxZoom: 17, bearing: view.bearing })
    ?? map.cameraForBounds(bounds, { padding: 20, maxZoom: 17, bearing: view.bearing });
  if (cam) map.flyTo({ ...cam, ...view, zoom: cam.zoom + (tilt ? 0.1 : 0), duration: 1200 });   // tilted, the near half of the walk needs less room
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
$("hours").innerHTML = "<i></i>".repeat(12);
const setHour = (h) => {
  hour = h; map.setPaintProperty("city", "line-color", shadeRamp(`s${h - 8}`)); set("h-hour", `${String(h).padStart(2, "0")}:00`);
  $("hours").querySelectorAll("i").forEach((el, i) => el.classList.toggle("on", i <= h - 8));
};
const stopHours = () => { clearInterval(hourTimer); hourTimer = null; };

let ready = false;      // set once the style is in and the data are on the map
styleReady.then(() => {
  map.moveLayer("ref", "mass");          // street and place names under the white boxes and the walks
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
  const padding = !p ? {} : W > 760 ? { left: p.right + 40, top: 52, right: 0, bottom: 0 } : { bottom: window.innerHeight - p.top, top: 52, left: 0, right: 0 };
  map.flyTo({ pitch: 0, bearing: 0, ...view, padding });
};
const CITY = { center: [-75.135, 39.995], zoom: 10.9, duration: 2200 };
const scene = ({ city = false, grid = false, bld = false, route = false, mass = false, types = false, st = false, cityOpacity = 0.9 } = {}) => {
  if (current !== "shade") stopHours();
  if (current !== "hero" && current !== "example") heroOn = false;
  vis("city", city); vis("bld", bld); vis("mass", mass); vis("st", st); routes(route); if (!grid) vis("grid", false);
  for (const id of ["types", "types-in"]) { vis(id, true); map.setPaintProperty(id, "line-opacity", types ? 1 : 0); }
  showPills(types);
  map.setPaintProperty("city", "line-opacity", cityOpacity);
  if (!route || current !== "example") hideTags();
};
const STEPS = {
  hero: () => { scene({ route: true }); drawn = false; startLoop(); fitPair(PAIRS[pairIdx]); },
  heat: () => {
    // sidewalks mostly in the sun (under half shaded) at 15:00, the rest dark
    scene({ city: true }); map.setPaintProperty("city", "line-color", ["step", ["get", "s7"], COLORS.sun, 50, "#2a2e36"]);
    fly({ center: [-75.1652, 39.9502], zoom: 14.6, duration: 2000 });
  },
  sources: () => { scene(); fly({ center: TYPES_AT, zoom: 17, duration: 2000 }); },
  types: () => { scene({ types: true }); fly({ center: TYPES_AT, zoom: 17, duration: 1200 }); },
  question: () => { scene({ st: true }); fly(CITY); },
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
    scene({ city: true, route: true, mass: true, cityOpacity: 0.35 }); setHour(15);
    const p = hero.indoor;
    map.getSource("hero").setData(routeFeatures(p, 1, 1, true));
    placeTags(p, true); showTag("short", true); showTag("cool", true);
    fitPair(p, document.querySelector('[data-step="indoor"] .panel'), true, true);
  },
  end: () => { scene({ city: true, bld: true, cityOpacity: 0.75 }); setHour(15); fly({ center: DEMO_CENTER, zoom: 15, duration: 2000 }); },
};
const io = new IntersectionObserver((entries) => {
  for (const en of entries) {
    en.target.classList.toggle("active", en.isIntersecting);       // starts the screen's own CSS animation
    if (!en.isIntersecting) continue;
    const s = en.target.dataset.step;
    if (s === current) continue;
    current = s;
    if (ready) STEPS[s]();
  }
}, { threshold: 0.55 });
document.querySelectorAll("section[data-step]").forEach((s) => io.observe(s));
// the buttons on the last screen hand the current view to the tool and the city map, which open where the story ends
document.querySelectorAll(".cta a").forEach((a) => a.addEventListener("click", () => {
  const b = map.getBounds();
  a.href = `${a.getAttribute("href").split("#")[0]}#b=${[b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => v.toFixed(5)).join(",")}`;
}));

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
