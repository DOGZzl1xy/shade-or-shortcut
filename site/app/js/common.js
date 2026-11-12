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
  heat: "Surface temperature: USGS Landsat 8; air temperature: NOAA ACIS",
  utci: 'Heat (UTCI): this project, SOLWEIG 2022a (solweig-gpu) with ERA5 weather via <a href="https://open-meteo.com/">Open-Meteo</a> (CC BY 4.0)',
  transit: "Transit: SEPTA GTFS (fall 2026)",
};

// ---- state shared between the story, the route tool and the city map, kept in the URL hash
// ("#t=7&m=heat&b=west,south,east,north"): t = time index, m = measure, b = the view (story -> next page)
export const DAYS = [["0624", "June 24"], ["0729", "July 29"]];
export const DEFAULT_T = 7;                     // June 24, 15:00
export function readState() {
  const q = new URLSearchParams(location.hash.slice(1));
  const b = (q.get("b") || "").split(",").map(Number);
  const t = q.has("t") ? Number(q.get("t")) : NaN;
  return {
    bounds: b.length === 4 && b.every(Number.isFinite) ? [[b[0], b[1]], [b[2], b[3]]] : null,
    t: Number.isInteger(t) && t >= 0 && t < 12 * DAYS.length ? t : DEFAULT_T,
    m: q.get("m") === "heat" ? "heat" : "shade",
  };
}
let shared = {};
// remember the page's time and measure in its own URL and in the links to the other two pages
export function writeState(st) {
  shared = { ...shared, ...st };
  const q = new URLSearchParams({ t: shared.t, m: shared.m });
  history.replaceState(null, "", `#${q}`);
}
export const stateHash = (bounds) => {
  const q = new URLSearchParams({ t: shared.t ?? DEFAULT_T, m: shared.m ?? "shade" });
  if (bounds) q.set("b", [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()].map((v) => v.toFixed(5)).join(","));
  return `#${q}`;
};

export function timeName(label) {          // "0624_15" -> "June 24, 15:00"
  const m = Object.fromEntries(DAYS);
  return `${m[label.slice(0, 4)]}, ${label.slice(5)}:00`;
}

// ---- the time bar: date, hour track 08-19 and "play the day", floating at the bottom of the map (top on phones).
// Time index = day * 12 + (hour - 8), as in the exported "0624_08" ... "0729_19" order.
export function timeBar(t, onChange, { onPlay = null } = {}) {
  let day = Math.floor(t / 12), hour = 8 + (t % 12), timer = null;
  const el = document.createElement("div");
  el.className = "timebar panel";
  el.innerHTML = `<div class="seg seg-sm tb-day">${DAYS.map(([, n], i) => `<button data-d="${i}">${n}</button>`).join("")}</div>
    <button class="tb-play" aria-label="Play the day" title="Play the day">${PLAY}</button>
    <div class="tb-track"><input type="range" min="8" max="19" step="1" value="${hour}" aria-label="hour of day">
      <div class="tb-ticks">${Array.from({ length: 12 }, (_, i) => `<span>${8 + i}</span>`).join("")}</div></div>
    <div class="tb-now num"></div>`;
  document.body.append(el);
  const btns = el.querySelectorAll(".tb-day button"), slider = el.querySelector("input"), now = el.querySelector(".tb-now");
  const play = el.querySelector(".tb-play"), ticks = el.querySelectorAll(".tb-ticks span");
  const fire = () => {
    btns.forEach((b) => b.classList.toggle("on", +b.dataset.d === day));
    ticks.forEach((s, i) => s.classList.toggle("on", 8 + i === hour));
    slider.value = hour;
    now.textContent = `${String(hour).padStart(2, "0")}:00`;
    onChange(day * 12 + hour - 8);
  };
  const stop = () => { clearInterval(timer); timer = null; play.innerHTML = PLAY; play.classList.remove("on"); };
  btns.forEach((b) => b.addEventListener("click", () => { day = +b.dataset.d; fire(); }));
  slider.addEventListener("input", () => { stop(); hour = +slider.value; fire(); });
  ticks.forEach((s, i) => s.addEventListener("click", () => { stop(); hour = 8 + i; fire(); }));
  play.addEventListener("click", () => {
    if (timer) { stop(); return; }
    if (hour === 19) hour = 8;
    play.innerHTML = PAUSE; play.classList.add("on"); onPlay?.(); fire();
    timer = setInterval(() => { if (hour >= 19) { stop(); return; } hour += 1; fire(); }, 900);
  });
  fire();
  return { el, get: () => day * 12 + hour - 8, set: (v) => { day = Math.floor(v / 12); hour = 8 + (v % 12); fire(); } };
}
const PLAY = '<svg viewBox="0 0 12 12" width="12" height="12"><path d="M3 1.5v9l7.5-4.5z" fill="currentColor"/></svg>';
const PAUSE = '<svg viewBox="0 0 12 12" width="12" height="12"><path d="M2.5 1.5h2.5v9H2.5zM7 1.5h2.5v9H7z" fill="currentColor"/></svg>';

// ---- measure: sun (shade share; the shadiest walk) or heat (UTCI; the coolest walk), one toggle on every page
export const MEASURES = {
  shade: { label: "Sun", route: "Shadiest", help: "Sun: shade along each sidewalk. Routes take the <b>shadiest walk</b> (shade cost of Wen et al. 2025)." },
  heat: { label: "Heat (UTCI)", route: "Coolest", help: "Heat: the UTCI, how hot it feels. Routes take the <b>coolest walk</b> (UTCI cost)." },
};
export function measureControl(el, m, onChange, { help = true } = {}) {
  el.innerHTML = `<div class="label">Measure</div>
    <div class="seg">${Object.entries(MEASURES).map(([k, v]) => `<button data-m="${k}">${v.label}</button>`).join("")}</div>
    ${help ? '<p class="hint mhelp"></p>' : ""}`;
  const btns = el.querySelectorAll("button"), h = el.querySelector(".mhelp");
  const fire = () => { btns.forEach((b) => b.classList.toggle("on", b.dataset.m === m)); if (h) h.innerHTML = MEASURES[m].help; onChange(m); };
  btns.forEach((b) => b.addEventListener("click", () => { m = b.dataset.m; fire(); }));
  fire();
  return { get: () => m };
}

// MapLibre 4.7 keeps a 400x300 canvas if the container had no size at construction; resize once loaded
// Credits sit behind the (i) button, folded at start, so they do not run under the time bar or the story text.
export function newMap(opts) {
  const map = new maplibregl.Map({ ...opts, attributionControl: { compact: true } });
  map.on("load", () => {
    map.resize();
    document.querySelector(".maplibregl-ctrl-attrib")?.classList.remove("maplibregl-compact-show");
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
  el.querySelectorAll("a.link").forEach((a) => a.addEventListener("click", () => {
    const h = a.getAttribute("href");
    if (h === "tool.html" || h === "map.html") a.href = h + stateHash(h === "map.html" && window.__map ? window.__map.getBounds() : null);
  }));
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
export function baseStyle(extraSources = {}, extraLayers = [], { labels = true, base = 0.85, labelOpacity = 0.55 } = {}) {
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
      ...(labels ? [{ id: "ref", type: "raster", source: "ref", paint: { "raster-opacity": labelOpacity } }] : []),
    ],
  };
}

export async function getJSON(url) {
  const r = await fetch(`${url}?v=${VERSION}`);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

// ---- sidewalk colours. Shade share (%) runs sun orange -> cool cyan; UTCI (x 10) cool cyan -> sun orange over 34-46 C:
// shade on a summer morning (about 34 C) to full sun in the afternoon (46 C = extreme heat stress), one scale for every
// hour so hours compare. Legends always read low -> high, left to right.
export const SHADE_STOPS = [0, COLORS.sun, 40, "#e9b36a", 70, "#6fb0c8", 100, COLORS.cool];
export const UTCI_STOPS = [340, COLORS.cool, 390, "#6fb0c8", 430, "#e9b36a", 460, COLORS.sun];
export const shadeRamp = (prop) => ["interpolate", ["linear"], ["get", prop], ...SHADE_STOPS];
export const utciRamp = (prop) => ["interpolate", ["linear"], ["get", prop], ...UTCI_STOPS];
export const sidewalkColor = (m, t) => (m === "heat" ? utciRamp(`u${t}`) : shadeRamp(`s${t}`));
export const SIDEWALK_WIDTH = ["interpolate", ["linear"], ["zoom"], 13, 0.6, 15, 1.4, 17, 3];
export const SIDEWALK_OPACITY = 0.85;
export const SIDEWALK_DIM = 0.4;           // under drawn walks, which use the same orange and cyan
export const UTCI_NOTE = "How hot it feels (UTCI, SOLWEIG model, 2025 weather). 38–46 °C is very strong heat stress, 46+ extreme.";
export const SHADE_NOTE = "Share of each sidewalk in tree or building shade (2022 LiDAR heights).";

const gradient = (stops, lo, hi) => {     // CSS gradient of a value -> colour list, positioned between lo and hi
  const out = [];
  for (let i = 0; i < stops.length; i += 2) out.push(`${stops[i + 1]} ${((100 * (stops[i] - lo)) / (hi - lo)).toFixed(1)}%`);
  return `linear-gradient(to right, ${out.join(", ")})`;
};
// a colour bar with ticks: stops = [value, colour, ...]; ticks = [[value, text], ...]
export function rampHTML(stops, ticks, { lo = stops[0], hi = stops[stops.length - 2], title = "", note = "" } = {}) {
  return `${title ? `<div class="legend-title">${title}</div>` : ""}<div class="ramp" style="background:${gradient(stops, lo, hi)}"></div>
    <div class="ticks">${ticks.map(([v, s]) => `<span style="left:${(100 * (v - lo)) / (hi - lo)}%">${s}</span>`).join("")}</div>
    ${note ? `<p class="note">${note}</p>` : ""}`;
}
export function sidewalkLegend(m, { note = true } = {}) {
  return m === "heat"
    ? rampHTML(UTCI_STOPS, [[340, "34"], [380, "38"], [420, "42"], [460, "46 °C+"]],
      { title: "Sidewalk heat · UTCI", note: note ? UTCI_NOTE : "" })
    : rampHTML(SHADE_STOPS, [[0, "0%"], [50, "50%"], [100, "100% shaded"]],
      { title: "Sidewalk shade · share of length", note: note ? SHADE_NOTE : "" });
}

// numbers in text use a true minus sign (U+2212)
export const pct = (x, d = 1) => `${(100 * x).toFixed(d)}%`.replace("-", "−");
export const degC = (x, d = 1) => `${x.toFixed(d)} °C`.replace("-", "−");

// grid and station colour ramps (value, colour pairs), shared by the story and the city map. "How much it helps"
// (less sun, cooler, extreme heat avoided) always runs dark -> cyan; "how exposed" runs cyan -> orange.
const HELP = ["#1d2733", "#2f6f7a"];
export const GRID_RAMPS = {
  sun_red: { stops: [0, HELP[0], 0.1, HELP[1], 0.25, COLORS.cool], ticks: [[0, "0%"], [0.1, "10%"], [0.25, "25%+"]] },
  no_alt_share: { stops: [0.2, COLORS.cool, 0.6, "#c9a36a", 1, COLORS.sun], ticks: [[0.2, "20%"], [0.6, "60%"], [1, "100%"]] },
  sun_share: { stops: [0.4, COLORS.cool, 0.7, "#c9a36a", 0.95, COLORS.sun], ticks: [[0.4, "40%"], [0.7, "70%"], [0.95, "95%"]] },
  network_km_km2: { stops: [10, "#1d2733", 30, "#4b6b8a", 55, "#cfe3ff"], ticks: [[10, "10"], [30, "30"], [55, "55 km/km²"]] },
  u_short: { stops: [42.5, COLORS.cool, 44, "#e9b36a", 45.5, COLORS.sun], ticks: [[42.5, "42.5"], [44, "44"], [45.5, "45.5 °C"]] },
  u_cool: { stops: [0, HELP[0], 0.3, HELP[1], 1, COLORS.cool], ticks: [[0, "0"], [0.3, "0.3"], [1, "1 °C+"]] },
  ext_red: { stops: [0, HELP[0], 0.2, HELP[1], 0.5, COLORS.cool], ticks: [[0, "0%"], [0.2, "20%"], [0.5, "50%+"]] },
};
export const rampExpr = (key, prop = key) =>
  ["interpolate", ["linear"], ["coalesce", ["get", prop], GRID_RAMPS[key].stops[0]], ...GRID_RAMPS[key].stops];

// route lines: shortest = sun-orange solid, coolest = cyan dashes over a dim cyan track, indoor stretches = violet dots
// over a dim violet track. Where the two walks share a street they run side by side (offset to either side of the
// sidewalk, like lines on a transit map), so neither hides the other. A thin dark casing sets them off the map.
// Features carry role = short | cool | indoor; all three run from the walk's start, so the offsets stay on their side.
const DIM = { cool: "#1d6b73", indoor: "#4b3d73" };
export function routeLayers(source, w = 4) {
  const role = (r) => ["==", ["get", "role"], r], off = w * 0.65, round = { "line-cap": "round", "line-join": "round" };
  const line = (id, r, color, width, o, extra = {}) => ({ id: `${source}-${id}`, type: "line", source, filter: role(r),
    layout: { ...round, ...(extra.layout || {}) }, paint: { "line-color": color, "line-width": width, "line-offset": o, ...(extra.paint || {}) } });
  return [
    line("case-s", "short", "#07080b", w + 2.5, -off, { paint: { "line-opacity": 0.85 } }),
    line("case-c", "cool", "#07080b", w + 2.5, off, { paint: { "line-opacity": 0.85 } }),
    line("short", "short", COLORS.sun, w, -off),
    line("track", "cool", DIM.cool, w, off),
    line("cool", "cool", COLORS.cool, w, off, { layout: { "line-cap": "butt" }, paint: { "line-dasharray": [1.6, 1.2] } }),
    line("incase", "indoor", DIM.indoor, w, off),
    line("indoor", "indoor", COLORS.indoor, w, off, { paint: { "line-dasharray": [0, 1.6] } }),
  ];
}
export const ROUTE_LAYERS = (source) => ["case-s", "case-c", "short", "track", "cool", "incase", "indoor"].map((k) => `${source}-${k}`);
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
