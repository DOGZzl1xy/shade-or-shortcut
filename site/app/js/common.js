// Shared helpers for the hand-written pages: navigation, basemap, colours, data loading.

// cache key for scripts and data: "dev" in the source; scripts/publish_site.sh writes the commit into the published copy
// (here and in every "?v=dev"), so returning visitors never mix new scripts with cached old data
export const VERSION = "dev";

export const COLORS = { sun: "#ff8a3d", bldg: "#5b8cff", tree: "#43c27a", indoor: "#b48cff", cool: "#35d0e0", dim: "#8b8a84" };
// grid clusters 0-2; colour-blind safe (checked with the dataviz palette validator: CVD ΔE >= 16 on #07080b)
export const CLUSTERS = [[0, "Sunny, no cooler option", "#d47519"], [1, "Sunny but avoidable", "#4398d2"],
  [2, "Cooler, few alternatives", "#1f5fb0"]];
export const DEMO_CENTER = [-75.1932, 39.9522];

// attribution strings for map sources
export const ATTR = {
  base: '© <a href="https://www.esri.com/">Esri</a>, HERE, Garmin, © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors (ODbL)',
  shade: "Shade: this project, from NOAA 2022 LiDAR, City of Philadelphia footprints, " +
    '<a href="https://www.pasda.psu.edu/">PASDA</a> land cover and DVRPC sidewalks',
  chm: "Canopy outside the city: Meta / WRI CHMv2 (CC BY 4.0)",
  osm: "Campus footways © OpenStreetMap contributors (ODbL)",
};

export function timeName(label) {          // "0621_15" -> "June 21, 15:00"
  const m = { "0621": "June 21", "0801": "Aug 1" };
  return `${m[label.slice(0, 4)]}, ${label.slice(5)}:00`;
}

// date toggle + hour slider; time index = day * 12 + (hour - 8), as in the exported "0621_08" ... "0801_19" order
export function timeControl(el, t, onChange) {
  let day = Math.floor(t / 12), hour = 8 + (t % 12);
  el.innerHTML = `<div class="row"><span class="label">Date</span>
      <div class="seg seg-sm"><button data-d="0">June 21</button><button data-d="1">Aug 1</button></div></div>
    <div class="row" style="margin-top:8px"><span class="label">Hour</span><span class="num tc-hour"></span></div>
    <input type="range" min="8" max="19" step="1" value="${hour}" aria-label="hour of day">`;
  const btns = el.querySelectorAll("button"), slider = el.querySelector("input"), lab = el.querySelector(".tc-hour");
  const fire = () => {
    btns.forEach((b) => b.classList.toggle("on", +b.dataset.d === day));
    lab.textContent = `${String(hour).padStart(2, "0")}:00`;
    onChange(day * 12 + hour - 8);
  };
  btns.forEach((b) => b.addEventListener("click", () => { day = +b.dataset.d; fire(); }));
  slider.addEventListener("input", () => { hour = +slider.value; fire(); });
  fire();
  return { get: () => day * 12 + hour - 8, set: (v) => { day = Math.floor(v / 12); hour = 8 + (v % 12); slider.value = hour; fire(); } };
}

// MapLibre 4.7 keeps a 400x300 canvas if the container had no size at construction; resize once loaded
export function newMap(opts) {
  const narrow = window.innerWidth <= 760;
  const map = new maplibregl.Map({ ...opts, attributionControl: { compact: narrow || undefined } });
  map.on("load", () => {
    map.resize();
    // phones: credits start folded behind the (i) button instead of covering the bottom of the page
    if (narrow) document.querySelector(".maplibregl-ctrl-attrib")?.classList.remove("maplibregl-compact-show");
  });
  return map;
}

const PAGES = [["index.html", "Story"], ["tool.html", "Route tool"], ["map.html", "City map"],
  ["../data.html", "Data"], ["../methods.html", "Methods"], ["../results.html", "Results"], ["../technical.html", "Technical notes"]];

export function nav(active) {
  const el = document.createElement("nav");
  el.className = "nav";
  el.innerHTML = `<a class="brand" href="index.html">Shade <span>or</span> Shortcut</a>` +
    `<button class="menu" aria-expanded="false" aria-controls="nav-links">Menu</button>` +
    `<div class="links" id="nav-links">` +
    PAGES.map(([h, t]) => `<a class="link${h === active ? " on" : ""}" href="${h}">${t}</a>`).join("") + `</div>`;
  const btn = el.querySelector(".menu");
  btn.addEventListener("click", () => {
    const open = el.classList.toggle("open");
    btn.setAttribute("aria-expanded", open);
  });
  document.body.prepend(el);
}

// side panel that collapses to its header on small screens, where it starts folded so the map stays visible
export function collapsible(panel) {
  const head = panel.querySelector("h1");
  const btn = document.createElement("button");
  btn.className = "collapse";
  btn.setAttribute("aria-label", "Show or hide the panel");
  head.append(btn);
  const set = (c) => { panel.classList.toggle("collapsed", c); btn.textContent = c ? "+" : "–"; btn.setAttribute("aria-expanded", !c); };
  set(matchMedia("(max-width: 760px)").matches);
  btn.addEventListener("click", () => set(!panel.classList.contains("collapsed")));
}

// labels: false drops the Esri place-name layer; base: opacity of the dark basemap (a number or a zoom expression)
export function baseStyle(extraSources = {}, extraLayers = [], { labels = true, base = 0.85 } = {}) {
  return {
    version: 8,
    sources: {
      dark: { type: "raster", tileSize: 256, maxzoom: 16,
        tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}"],
        attribution: ATTR.base },
      ref: { type: "raster", tileSize: 256, maxzoom: 16,
        tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}"] },
      ...extraSources,
    },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": "#07080b" } },
      { id: "dark", type: "raster", source: "dark", paint: { "raster-opacity": base } },
      ...extraLayers,
      ...(labels ? [{ id: "ref", type: "raster", source: "ref", paint: { "raster-opacity": 0.55 } }] : []),
    ],
  };
}

export async function getJSON(url) {
  const r = await fetch(`${url}?v=${VERSION}`);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

export function shadeRamp(prop) {          // shade share (%) -> sun orange ... cool cyan
  return ["interpolate", ["linear"], ["get", prop], 0, COLORS.sun, 40, "#e9b36a", 70, "#6fb0c8", 100, COLORS.cool];
}

// numbers in text use a true minus sign (U+2212)
export const pct = (x, d = 1) => `${(100 * x).toFixed(d)}%`.replace("-", "\u2212");

// grid colour ramps (value, colour pairs), shared by the home page and the city map
export const GRID_RAMPS = {
  sun_red: { stops: [0, "#1d2733", 0.1, "#2f6f7a", 0.25, COLORS.cool], lo: "0%", hi: "25%+" },
  no_alt_share: { stops: [0.2, COLORS.cool, 0.6, "#c9a36a", 1, COLORS.sun], lo: "20%", hi: "100%" },
  sun_share: { stops: [0.4, COLORS.cool, 0.7, "#c9a36a", 0.95, COLORS.sun], lo: "40%", hi: "95%" },
  network_km_km2: { stops: [10, "#1d2733", 30, "#4b6b8a", 55, "#cfe3ff"], lo: "10", hi: "55 km/km²" },
};

// route lines: a dark casing under every route, shortest = sun-orange solid, coolest = cyan dashes drawn on top
// (overlapping stretches read as orange and cyan), indoor stretches = violet dots. Features carry role = short | cool | indoor.
export function routeLayers(source, w = 4) {
  const role = (r) => ["==", ["get", "role"], r];
  return [
    { id: `${source}-case`, type: "line", source, layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#07080b", "line-width": w + 3.5, "line-opacity": 0.9 } },
    { id: `${source}-short`, type: "line", source, filter: role("short"), layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": COLORS.sun, "line-width": w } },
    { id: `${source}-cool`, type: "line", source, filter: role("cool"), layout: { "line-cap": "butt", "line-join": "round" },
      paint: { "line-color": COLORS.cool, "line-width": w, "line-dasharray": [1.6, 1.2] } },
    // indoor stretches lie on the coolest line: a casing hides its dashes there before the dots are drawn
    { id: `${source}-incase`, type: "line", source, filter: role("indoor"), layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#07080b", "line-width": w + 3.5 } },
    { id: `${source}-indoor`, type: "line", source, filter: role("indoor"), layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": COLORS.indoor, "line-width": w, "line-dasharray": [0, 1.6] } },
  ];
}
export const ROUTE_LAYERS = (source) => ["case", "short", "cool", "incase", "indoor"].map((k) => `${source}-${k}`);
export const m = (x) => `${Math.round(x).toLocaleString()} m`;

// add pmtiles:// protocol once (vendor/pmtiles.js exposes a global `pmtiles`)
export function pmtilesProtocol() {
  if (window.__pm) return;
  const protocol = new pmtiles.Protocol();
  maplibregl.addProtocol("pmtiles", protocol.tile);
  window.__pm = protocol;
}

// network edge geometry (flat lon/lat list) -> coordinates
export function edgeCoords(net, i) {
  const g = net.edges.geom[i], out = [];
  for (let k = 0; k < g.length; k += 2) out.push([g[k], g[k + 1]]);
  return out;
}

// ordered coordinates of a path given file edge indices and the start node
export function pathCoords(net, edges, start) {
  const out = [];
  let node = start;
  for (const i of edges) {
    let c = edgeCoords(net, i);
    const u = net.edges.u[i], v = net.edges.v[i];
    if (u !== node) c = c.reverse();
    node = u === node ? v : u;
    out.push(...(out.length ? c.slice(1) : c));
  }
  return out;
}
