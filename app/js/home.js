// Story page: one fixed map under 17 screens in 6 chapters. Each screen sets the map (view, layers, labels) for its one
// sentence; on two screens the day plays by itself (08:00 -> 19:00).
// Colours, ramps, legends and route styles are the shared ones of common.js, as on the route tool and the city map.
import { VERSION, nav, baseStyle, getJSON, pmtilesProtocol, COLORS, CLUSTERS, GRID_RAMPS, ATTR, pct, degC, newMap, routeLayers,
  ROUTE_LAYERS, sidewalkColor, sidewalkLegend, SIDEWALK_WIDTH, rampHTML, rampExpr, UTCI_STOPS } from "./common.js?v=00c01c4";
import { createWalk } from "./walk.js?v=00c01c4";

nav("index.html");
pmtilesProtocol();
const $ = (id) => document.getElementById(id);
const set = (id, v) => { const el = $(id); if (el) el.textContent = v; };
const EMPTY = { type: "FeatureCollection", features: [] };
const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const T15 = 7;                                  // time index of June 24, 15:00 (0624_08 ... 0624_19, 0729_08 ...)

// ---- the map first, so the basemap loads while the data download. Every paint change cross-fades over FADE ms (the
// style's transition), so layers fade in and out instead of popping
const FADE = 700;
const map = newMap({
  container: "map", center: [-75.1932, 39.9522], zoom: 15.6, pitch: 0, interactive: false,
  style: { transition: { duration: FADE, delay: 0 }, ...baseStyle({
    city: { type: "vector", url: `pmtiles://data/city_shade.pmtiles?v=${VERSION}`, attribution: `${ATTR.shade}; ${ATTR.utci}; ${ATTR.chm}; ${ATTR.osm}; ${ATTR.heat}` },
    grid: { type: "geojson", data: EMPTY, attribution: ATTR.transit },
    bld: { type: "geojson", data: EMPTY },
    hero: { type: "geojson", data: EMPTY },
    mass: { type: "geojson", data: EMPTY, attribution: "Buildings: City of Philadelphia footprints, heights from NOAA 2022 LiDAR" },
    types: { type: "geojson", data: EMPTY },
    st: { type: "geojson", data: EMPTY },
    ring: { type: "geojson", data: EMPTY },
    spots: { type: "geojson", data: EMPTY },
  }, [
    // street level: the sidewalks as thin grey lines over the dimmed Esri base, which carries the street names
    { id: "lines", type: "line", source: "city", "source-layer": "sidewalks", minzoom: 13, layout: { visibility: "none" },
      paint: { "line-color": "#3b3f47", "line-width": ["interpolate", ["linear"], ["zoom"], 13, 0.4, 16, 1.3], "line-opacity": 0 } },
    { id: "city", type: "line", source: "city", "source-layer": "sidewalks", minzoom: 13, layout: { visibility: "none", "line-cap": "round" },
      paint: { "line-color": sidewalkColor("shade", T15), "line-width": SIDEWALK_WIDTH, "line-opacity": 0 } },
    ...["grid-a", "grid-b"].map((id) => ({ id, type: "fill", source: "grid", layout: { visibility: "none" },
      paint: { "fill-color": "#555", "fill-opacity": 0, "fill-outline-color": "rgba(7,8,11,0.6)" } })),
    { id: "bld", type: "fill", source: "bld", layout: { visibility: "none" },
      paint: { "fill-color": "rgba(180,140,255,0.22)", "fill-outline-color": "rgba(180,140,255,0.55)", "fill-opacity": 0 } },
    // what shades each campus sidewalk at 15:00; it fades in, "spread onto the map"
    { id: "types", type: "line", source: "types", filter: ["!=", ["get", "t"], "indoor"], layout: { visibility: "none", "line-cap": "round" },
      paint: { "line-color": ["match", ["get", "t"], "tree", COLORS.tree, "bldg", COLORS.bldg, COLORS.sun], "line-width": 2.6,
        "line-opacity": 0, "line-opacity-transition": { duration: 1500 } } },
    { id: "types-in", type: "line", source: "types", filter: ["==", ["get", "t"], "indoor"], layout: { visibility: "none", "line-cap": "round" },
      paint: { "line-color": COLORS.indoor, "line-width": 3.2, "line-dasharray": [0, 1.8], "line-opacity": 0, "line-opacity-transition": { duration: 1500 } } },
    { id: "st", type: "circle", source: "st", layout: { visibility: "none" },
      paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 2.6, 14, 5], "circle-color": "#e8e6df",
        "circle-stroke-color": "#07080b", "circle-stroke-width": 1, "circle-opacity": 0, "circle-stroke-opacity": 0 } },
    { id: "ring", type: "line", source: "ring", layout: { visibility: "none" },
      paint: { "line-color": "#ffffff", "line-width": 1.2, "line-dasharray": [3, 3], "line-opacity": 0 } },
    { id: "spots", type: "circle", source: "spots", layout: { visibility: "none" },
      paint: { "circle-radius": 7, "circle-color": ["get", "c"], "circle-stroke-color": "#ffffff", "circle-stroke-width": 2,
        "circle-opacity": 0, "circle-stroke-opacity": 0 } },
    // white-box model of the campus (indoor screen); buildings opened to walkers in pale violet
    { id: "mass", type: "fill-extrusion", source: "mass", layout: { visibility: "none" },
      paint: { "fill-extrusion-color": ["case", ["get", "open"], "#c9b6f2", "#e8e6df"], "fill-extrusion-height": ["get", "h"],
        "fill-extrusion-opacity": 0, "fill-extrusion-vertical-gradient": true } },
    ...routeLayers("hero"),
  ], { base: 0.55, labelOpacity: 0.45 }) },
});
const styleReady = new Promise((res) => map.once("style.load", res));
getJSON("data/massing.geojson").then((d) => styleReady.then(() => map.getSource("mass").setData(d)));
getJSON("data/stations.geojson").then((d) => styleReady.then(() => map.getSource("st").setData(d)));
getJSON("data/shade_types.geojson").then((d) => styleReady.then(() => map.getSource("types").setData(d)));
const [hero, summary, layers, grid, heat, story, walkData] = await Promise.all([
  getJSON("data/hero.json"), getJSON("data/summary.json"), getJSON("data/demo_layers.json"), getJSON("data/grid.geojson"),
  getJSON("data/heat.json"), getJSON("data/story.json"), getJSON("data/walk.json")]);

// ---- numbers in the text
const NUM = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];
const PART = { 2: "half", 3: "a third", 4: "a quarter", 5: "a fifth", 6: "a sixth", 7: "a seventh", 8: "an eighth", 9: "a ninth", 10: "a tenth" };
const part = (x) => { const n = Math.round(1 / x); return PART[n] ? `about ${PART[n]}` : pct(x, 0); };
const inTen = (x) => { const n = Math.round(10 * x); return n >= 1 && n <= 9 ? `${NUM[n]} in ten` : pct(x, 0); };
const oneIn = (x) => { const n = Math.round(1 / x); return n >= 2 && n <= 10 ? `about one in ${NUM[n]}` : pct(x, 0) + " of"; };
const m = (x) => `${Math.round(x).toLocaleString("en-US")} m`;
const b6 = summary.phase6_at_15h.find((r) => r.sample === "city" && r.mode === "bus" && r.length_bin === "300-800");
const b7 = summary.phase7_by_mode_bin_15h.find((r) => r.mode === "bus" && r.length_bin === "300-800");
const h10 = summary.phase10.by_mode_bin_key_hour.find((r) => r.mode === "bus" && r.length_bin === "300-800");
const byHour = Object.fromEntries(summary.phase10.by_hour.map((r) => [r.label, r]));
const H24 = summary.phase10.by_hour.filter((r) => r.label.startsWith("0624"));
const r15 = byHour["0624_15"];
const indoorShare = summary.phase8_at_15h.find((r) => r.set === "campus" && r.scenario === "all").shade_uses_indoor;
const PERSONA = hero.opener[0], PW = walkData.hours.find((h) => h.time === "0624_15");
const stationName = (p) => `${p.station.replace(/-/g, "–")} (${p.line.startsWith("Regional") ? "Regional Rail" : p.line[0]})`;
const sunPct = (p) => pct(p.sun / p.length, 0);

// chapter 1
const fToC = (f) => Math.round(((f - 32) * 5) / 9);
const day = (iso) => new Date(`${iso}T12:00`).toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
const A = heat.air, L = heat.lst;
set("h-hot", `${A.hottest_f} °F`);
set("h-hot-cap", `${fToC(A.hottest_f)} °C at the airport on ${day(A.hottest_day)}, the hottest day of the summer`);
set("h-days", A.hot_days);
set("h-days-cap", `days at ${A.days_at_or_above} °F or above, summer ${A.summer[0].slice(0, 4)}`);
$("lst-legend").innerHTML = rampHTML(L.ramp.flatMap(([v, c]) => [L.ramp_c[0] + v * (L.ramp_c[1] - L.ramp_c[0]), c]),
  [[L.ramp_c[0], `${L.ramp_c[0]}`], [L.ramp_c[1], `${L.ramp_c[1]} °C`]], { title: "Ground surface temperature (Landsat)" });
set("lst-cap", `Ground surface on ${day(L.date)}, ${L.time_local} (air ${A.scene_day_max_f} °F): a median of ${Math.round(L.median_c)} °C.`);
set("n-trips", summary.city_trips.toLocaleString("en-US"));
set("n-homes", summary.city_trips.toLocaleString("en-US"));
set("p-station", stationName(PERSONA));
set("p-len", `${(PERSONA.shortest.length / 1000).toFixed(1)} km`);
// chapter 2
const ss = summary.sidewalk_share_15;
document.querySelectorAll(".n-elev").forEach((el) => { el.textContent = `${Math.round(summary.sun_elevation_15)}°`; });
set("s-sunny", pct(ss.sun, 0)); set("d-tree", pct(ss.tree, 0)); set("d-bldg", pct(ss.bldg, 0)); set("d-indoor", summary.annotated_buildings);
set("n-km", Math.round(summary.sidewalk_km).toLocaleString("en-US"));
document.querySelector('[data-legend="shade"]').innerHTML = sidewalkLegend("shade", { note: false });
document.querySelector('[data-legend="heat"]').innerHTML = sidewalkLegend("heat", { note: false });
// chapter 3
set("ex-label", `${stationName(PERSONA)} · June 24, 15:00`);
set("ex-short", m(PERSONA.shortest.length)); set("ex-short-cap", `shortest · ${sunPct(PERSONA.shortest)} in the sun`);
set("ex-cool", m(PERSONA.coolest.length)); set("ex-cool-cap", `shadiest · ${sunPct(PERSONA.coolest)} in the sun`);
$("ex-detail").innerHTML = `${Math.round(PERSONA.coolest.length - PERSONA.shortest.length)} m more walking, <b>${m(PERSONA.shortest.sun - PERSONA.coolest.sun)}</b> less in the sun: the shadiest walk keeps to the building-shaded side of Chestnut Street.`;
set("s-sun", pct(b6.short_sun_share, 0));
set("w-noalt", inTen(b7.no_alt_share));
const WS = story.waffle.sun_red.shares;
$("w-detail").innerHTML = `For the others a shadier street is close, and it can help a lot: for <b class="cool">${Math.round(100 * WS[3])} in 100</b>
  the shadiest walk avoids a quarter of the sun or more. Averaged over all walks, the shadiest walk has <b>${pct(b6.sun_red_mean, 1)} less sun</b>
  for <b>${pct(b6.detour_mean, 1)} more distance</b>.`;
const prof = Object.fromEntries(CLUSTERS.map(([k]) => [k, grid.features.filter((f) => f.properties.cluster === k)]));
const avg = (fs, k) => fs.reduce((a, f) => a + f.properties[k], 0) / fs.length;
$("cl-detail").innerHTML = `In <b class="c-choice">${prof[1].length} cells</b> a shadier walk is usually within reach: about ${Math.round(avg(prof[1], "network_km_km2"))} km of sidewalk per km² against ${Math.round(avg(prof[0], "network_km_km2"))} where there is <b class="c-noalt">no cooler option</b>.`;
$("clusters").innerHTML = CLUSTERS.map(([, n, c]) => `<span><span class="legend-swatch" style="background:${c};height:10px"></span>${n.toLowerCase()}</span>`).join("");
document.querySelectorAll("[data-grid-legend]").forEach((el) => {
  const k = el.dataset.gridLegend, r = GRID_RAMPS[k];
  el.innerHTML = rampHTML(r.stops, r.ticks, { title: k === "sun_share" ? "Sun on the shortest walk" : "How much cooler the coolest walk is (UTCI)", ends: r.ends });
});
// chapter 4
set("f-drop", `${Math.round(r15.u_sun - (r15.u_bldg + r15.u_tree) / 2)} °C`);
const vs = H24.filter((r) => r.u_bldg > 38).map((r) => +r.label.slice(5));
set("hd-from", Math.min(...vs)); set("hd-to", Math.max(...vs));
const SP = story.spots.points.reduce((o, p) => ({ ...o, [p.kind]: p }), {});
$("tr-detail").innerHTML = `The shade cost of the shadiest walk counts a metre under trees as half as good as building shade. In felt
  temperature it is as good: across the city at 15:00, sidewalks under trees average <b class="t-tree">${degC(r15.u_tree)}</b>, under
  buildings <b class="t-bldg">${degC(r15.u_bldg)}</b>, in the sun <b class="sun">${degC(r15.u_sun)}</b>. Here, a block apart:
  <b class="sun">${degC(SP.sun.u)}</b>, <b class="t-tree">${degC(SP.tree.u)}</b> and <b class="t-bldg">${degC(SP.bldg.u)}</b>.`;
set("ch-du", degC(h10.heat_dutci, 2)); set("ch-ext", "−" + pct(h10.heat_extreme_red, 0)); set("ch-det", "+" + pct(h10.heat_detour, 1));
set("ch-ext-w", part(h10.heat_extreme_red));
$("ch-detail").innerHTML = `For ${pct(h10.heat_eq_shade, 0)} of walks the coolest walk is the shadiest walk. Where they differ, it takes the tree-lined street the shade cost undervalues; the shadiest walk alone cuts the extreme-heat metres by ${pct(h10.shade_extreme_red, 0)}.`;
// chapter 5, 6
set("s-ind-words", oneIn(indoorShare));
{
  const c0 = prof[0], all = grid.features, wsum = (fs, k) => fs.reduce((a, f) => a + f.properties.trips * (k ? f.properties[k] : 1), 0);
  const n0 = wsum(c0);
  $("end-detail").innerHTML = `In <b class="c-noalt">${c0.length} of ${all.length}</b> half-kilometre cells, the walk home from a frequent bus
    stop is <b>${pct(wsum(c0, "sun_share") / n0, 0)}</b> in the sun, and for <b>${pct(wsum(c0, "no_alt_share") / n0, 0)}</b> of these walks no
    street nearby is shadier: ${pct(n0 / wsum(all), 0)} of all walks of 300–800 m. Choosing a route cannot help here; shade has to be added,
    by street trees, awnings or shelters.`;
}

// ---- small graphics: sun dial, felt temperature, UTCI by hour, waffle
const SUN = Object.fromEntries(story.sun.map((s) => [s.label, s]));
function dial(svg, lab) {
  const xy = (s) => [46 + 40 * Math.sin(((s.azimuth - 180) * Math.PI) / 180), 50 - 44 * Math.sin((s.elevation * Math.PI) / 180)];
  const path = story.sun.filter((s) => s.label.startsWith("0624")).map((s, i) => `${i ? "L" : "M"}${xy(s).map((v) => v.toFixed(1)).join(",")}`).join("");
  const [x, y] = xy(SUN[lab]);
  svg.innerHTML = `<line class="ground" x1="0" y1="50" x2="92" y2="50"/><path class="arc" d="${path}"/><circle class="sunpos" cx="${x}" cy="${y}" r="5"/>
    <text x="0" y="62" font-size="8" fill="#8b8a84" font-family="Space Mono">E</text><text x="86" y="62" font-size="8" fill="#8b8a84" font-family="Space Mono">W</text>`;
}
const uCol = (u) => {                        // UTCI (C) -> colour on the shared ramp
  const s = UTCI_STOPS, v = Math.max(s[0], Math.min(s[s.length - 2], u * 10));
  for (let i = 2; i < s.length; i += 2) if (v <= s[i]) {
    const f = (v - s[i - 2]) / (s[i] - s[i - 2]), a = s[i - 1], b = s[i + 1];
    const c = (h, k) => parseInt(h.slice(1 + 2 * k, 3 + 2 * k), 16);
    return `rgb(${[0, 1, 2].map((k) => Math.round(c(a, k) + f * (c(b, k) - c(a, k)))).join(",")})`;
  }
  return s[s.length - 1];
};
(function felt() {
  // three walkers at 15:00: in the sun, in building shade, under a tree; their UTCI above them, and where those values
  // sit on the UTCI heat-stress scale below
  const g = 150, VY = 30, xs = { sun: 70, bldg: 206, tree: 340 };
  const person = (x) => `<g stroke="#e8e6df" stroke-width="2" stroke-linecap="round" fill="none"><circle cx="${x}" cy="${g - 40}" r="5" fill="#e8e6df" stroke="none"/>
    <path d="M${x} ${g - 34}v18m0 0l-6 16m6-16l6 16M${x - 8} ${g - 27}l8 4 8-4"/></g>`;
  const val = (k, u, n) => `<text x="${xs[k]}" y="${VY}" text-anchor="middle" class="val" fill="${uCol(u)}">${u.toFixed(1)} °C</text>
    <text x="${xs[k]}" y="${g + 20}" text-anchor="middle" class="lab">${n}</text>`;
  const lo = 34, hi = 48, X = (u) => 20 + (360 * (u - lo)) / (hi - lo), sy = 206;
  const bands = [[32, 38, "strong", 0.05], [38, 46, "very strong", 0.12], [46, 48, "extreme", 0.26]].map(([a, b, n, o]) =>
    `<rect x="${X(Math.max(a, lo))}" y="${sy - 9}" width="${X(b) - X(Math.max(a, lo))}" height="18" fill="rgba(255,138,61,${o})"/>
     <text x="${(X(Math.max(a, lo)) + X(b)) / 2}" y="${sy + 40}" text-anchor="middle" class="lab">${n}</text>`).join("");
  const mark = (k, u, c) => `<path d="M${X(u)} ${sy - 4}l-5 -8h10z" fill="${c}"/>`;
  $("felt").innerHTML = `<defs><linearGradient id="fg">${[36, 40.5, 43.5, 46].map((u) => `<stop offset="${(u - lo) / (hi - lo)}" stop-color="${uCol(u)}"/>`).join("")}</linearGradient></defs>
    <g class="fig f1"><circle cx="22" cy="58" r="9" fill="#ffd27a"/><g stroke="#ffd27a" stroke-dasharray="3 3" opacity="0.7"><line x1="31" y1="66" x2="62" y2="${g - 46}"/><line x1="34" y1="60" x2="80" y2="${g - 46}"/></g>
      <rect x="34" y="${g}" width="72" height="4" fill="${COLORS.sun}" opacity="0.7"/>${person(xs.sun)}${val("sun", r15.u_sun, "in the sun")}</g>
    <g class="fig f2"><rect x="150" y="56" width="32" height="${g - 56}" fill="rgba(232,230,223,0.1)" stroke="#8b8a84"/>
      <rect x="182" y="${g}" width="52" height="4" fill="${COLORS.bldg}"/>${person(xs.bldg)}${val("bldg", r15.u_bldg, "building shade")}</g>
    <g class="fig f3"><line x1="318" y1="${g}" x2="318" y2="${g - 44}" stroke="${COLORS.tree}" stroke-width="3"/><circle cx="318" cy="${g - 66}" r="24" fill="rgba(67,194,122,0.35)" stroke="${COLORS.tree}"/>
      <rect x="298" y="${g}" width="70" height="4" fill="${COLORS.tree}"/>${person(xs.tree)}${val("tree", r15.u_tree, "tree shade")}</g>
    <line x1="0" y1="${g}" x2="400" y2="${g}" stroke="#8b8a84"/>
    ${bands}<rect x="20" y="${sy - 3}" width="360" height="6" rx="3" fill="url(#fg)"/>
    ${mark("sun", r15.u_sun, COLORS.sun)}${mark("bldg", r15.u_bldg, COLORS.bldg)}${mark("tree", r15.u_tree, COLORS.tree)}
    ${[34, 38, 42, 46].map((u) => `<text x="${X(u)}" y="${sy + 22}" text-anchor="middle" class="lab" style="font-family:'Space Mono'">${u}${u === 46 ? " °C" : ""}</text>`).join("")}`;
})();
function hourChart(h) {
  const lo = 32, hi = 48, X = (hr) => 30 + (300 * (hr - 8)) / 11, Y = (u) => 10 + (120 * (hi - u)) / (hi - lo);
  const line = (k) => H24.map((r, i) => `${i ? "L" : "M"}${X(+r.label.slice(5)).toFixed(1)},${Y(r[k]).toFixed(1)}`).join("");
  const end = H24[H24.length - 1];
  $("hourchart").innerHTML = `<rect x="30" y="${Y(hi)}" width="300" height="${Y(46) - Y(hi)}" fill="rgba(255,138,61,0.2)"/>
    <rect x="30" y="${Y(46)}" width="300" height="${Y(38) - Y(46)}" fill="rgba(255,138,61,0.07)"/>
    ${[34, 38, 42, 46].map((u) => `<text x="24" y="${Y(u) + 3}" text-anchor="end">${u}</text>`).join("")}
    ${[8, 11, 14, 17].map((x) => `<text x="${X(x)}" y="146" text-anchor="middle">${x}:00</text>`).join("")}
    <line x1="${X(h)}" x2="${X(h)}" y1="6" y2="132" stroke="#e8e6df" stroke-width="1" opacity="0.6"/>
    ${[["u_sun", COLORS.sun, "sun"], ["u_bldg", COLORS.bldg, "building"], ["u_tree", COLORS.tree, "tree"]].map(([k, c, n], j) =>
      `<path d="${line(k)}" fill="none" stroke="${c}" stroke-width="2"/><text x="${X(19) + 6}" y="${Y(end[k]) + 3 + (j - 1) * 9}" fill="${c}">${n}</text>
       <circle cx="${X(h)}" cy="${Y(H24[h - 8][k])}" r="3.5" fill="${c}"/>`).join("")}`;
}
(function waffle() {
  // 100 walks in four groups by how much sun the shadiest walk avoids; one square = one walk
  const W = story.waffle.sun_red, cols = ["#d47519", "#2f6f7a", "#3fa3ae", COLORS.cool];
  const text = ["<b>No shadier way home</b>: the shadiest walk avoids less than 2% of the sun", "<b>A little shade</b>: 2–10% less sun",
    "<b>Noticeably shadier</b>: 10–25% less sun", "<b>Much shadier</b>: a quarter of the sun or more avoided"];
  const raw = W.shares.map((x) => 100 * x), n = raw.map(Math.floor);
  let rest = 100 - n.reduce((a, b) => a + b, 0);
  raw.map((v, i) => [v - Math.floor(v), i]).sort((a, b) => b[0] - a[0]).forEach(([, i]) => { if (rest > 0) { n[i] += 1; rest -= 1; } });
  let k = 0;
  $("waffle").innerHTML = `<div class="label" style="margin-bottom:4px">100 walks home · one square = one walk</div>` + n.map((c, i) =>
    `<div class="wrow"><div class="wn" style="color:${cols[i]}">${c}</div><div><div class="wt">${text[i]}</div>
      <div class="sq">${Array.from({ length: c }, () => `<i style="background:${cols[i]};transition-delay:${(k++ * 12) / 1000}s"></i>`).join("")}</div></div></div>`).join("");
})();

// ---- labels on the map
const PILL = { sun: ["sun", COLORS.sun], tree: ["tree shade", COLORS.tree], bldg: ["building shade", COLORS.bldg], indoor: ["indoors", COLORS.indoor] };
function pills(list, withValue = false) {     // [{t, at, u?}] -> markers whose label fades in
  return list.map(({ t, at, u }) => {
    const el = document.createElement("div");
    el.innerHTML = `<div class="pill" style="color:${PILL[t][1]}">${PILL[t][0]}${withValue ? `<span class="v">${degC(u)}</span>` : ""}</div>`;
    return new maplibregl.Marker({ element: el, anchor: withValue ? "bottom" : "center", offset: withValue ? [0, -12] : [0, 0] }).setLngLat(at);
  });
}
const typePills = pills(hero.types_focus.labels);
const spotPills = pills(story.spots.points.map((p) => ({ t: p.kind, at: [p.lon, p.lat], u: p.u })), true);
const showPills = (list, on) => list.forEach((mk) => { if (on) mk.addTo(map); mk.getElement().firstChild.classList.toggle("on", on); });
const pinEl = (txt) => { const el = document.createElement("div"); el.className = "pinlab"; el.textContent = txt; return el; };
const P0 = PERSONA.shortest.coords[0], P1 = PERSONA.shortest.coords[PERSONA.shortest.coords.length - 1];
const pins = [new maplibregl.Marker({ element: pinEl(stationName(PERSONA)), anchor: "bottom-left", offset: [4, -8] }).setLngLat(P0),
  new maplibregl.Marker({ element: pinEl("home"), anchor: "bottom", offset: [0, -8] }).setLngLat(P1)];
const showPins = (on) => pins.forEach((p) => (on ? p.addTo(map) : p.remove()));
function circle([lon, lat], R = 1200, n = 96) {
  const ky = 111320, kx = ky * Math.cos((lat * Math.PI) / 180), out = [];
  for (let i = 0; i <= n; i++) { const a = (2 * Math.PI * i) / n; out.push([lon + (R * Math.sin(a)) / kx, lat + (R * Math.cos(a)) / ky]); }
  return out;
}

// ---- walks: shortest / shadiest lines, each labelled next to its own line where the label covers neither walk,
// the other label nor the text panel
const SIDES = ["l", "r", "u", "d"];
function tagEl(cls) { const el = document.createElement("div"); el.className = "tagwrap"; el.innerHTML = `<div class="tag ${cls}"></div>`; return el; }
const tags = { short: new maplibregl.Marker({ element: tagEl("sun") }), cool: new maplibregl.Marker({ element: tagEl("cool") }) };
let tagLines = null;
function placeTags(p, indoor) {
  tagLines = { short: p.shortest.coords, cool: p.coolest.coords };
  const text = { short: `${m(p.shortest.length)} · ${sunPct(p.shortest)} sun`,
    cool: `${m(p.coolest.length)} · ${sunPct(p.coolest)} sun` + (indoor ? `\n${m(p.coolest.indoor)} indoors` : "") };
  for (const k of ["short", "cool"]) { tags[k].getElement().firstChild.textContent = text[k]; tags[k].setLngLat(tagLines[k][0]).addTo(map); }
  arrangeTags();
}
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
  const W = window.innerWidth, H = window.innerHeight;
  // the station and home labels are obstacles too
  const placed = pins.filter((mk) => mk.getElement().isConnected).map((mk) => {
    const r = mk.getElement().getBoundingClientRect();
    return { x: r.left - 6, y: r.top - 6, w: r.width + 12, h: r.height + 12 };
  });
  for (const k of ["short", "cool"]) {
    const el = tags[k].getElement(), w = el.firstChild.offsetWidth + 8, h = el.firstChild.offsetHeight + 8;
    const own = pts[k], other = pts[k === "short" ? "cool" : "short"];
    let best = null;
    for (let i = 1; i < 12; i++) {
      const a = own[Math.floor((i * (own.length - 1)) / 12)];
      if (!a) continue;
      const near = other.some((o) => Math.hypot(o.x - a.x, o.y - a.y) < 14);
      for (const s of SIDES) {
        const r = s === "l" ? { x: a.x - 12 - w, y: a.y - h / 2 } : s === "r" ? { x: a.x + 12, y: a.y - h / 2 }
          : s === "u" ? { x: a.x - w / 2, y: a.y - 12 - h } : { x: a.x - w / 2, y: a.y + 12 };
        Object.assign(r, { w, h });
        const out = r.x < 6 || r.y < 96 || r.x + w > W - 6 || r.y + h > H - 30;
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
function along(segs, path) {
  const at = (q) => path.reduce((b, c, i) => ((c[0] - q[0]) ** 2 + (c[1] - q[1]) ** 2 < (path[b][0] - q[0]) ** 2 + (path[b][1] - q[1]) ** 2 ? i : b), 0);
  return segs.map((g) => (at(g[0]) > at(g[g.length - 1]) ? [...g].reverse() : g));
}
const routeFeatures = (p, fs = 1, fc = 1, indoor = false) => ({ type: "FeatureCollection", features: [
  { type: "Feature", properties: { role: "short" }, geometry: { type: "LineString", coordinates: partial(p.shortest.coords, fs) } },
  { type: "Feature", properties: { role: "cool" }, geometry: { type: "LineString", coordinates: partial(p.coolest.coords, fc) } },
  ...(indoor ? along(p.coolest.indoor_segs, p.coolest.coords).map((g) => (
    { type: "Feature", properties: { role: "indoor" }, geometry: { type: "LineString", coordinates: g } })) : [])] });
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

// ---- drawing walks: the opening loops through three stations; the example screen draws the story's walk once
const PAIRS = hero.opener;
let pairIdx = 0, t0 = performance.now(), loop = null, drawn = false, raf = 0;
function frame(now) {
  raf = 0;
  if (!loop) return;
  const p = loop === "hero" ? PAIRS[pairIdx] : PERSONA;
  const dt = (now - t0) / 1000, D = 3.2;
  const fs = reduce ? 1 : Math.min(dt / D, 1), fc = reduce ? 1 : Math.min(Math.max((dt - D * 0.6) / D, 0), 1);
  if (!drawn) map.getSource("hero").setData(routeFeatures(p, fs, fc));
  drawn = fc >= 1;
  if (loop === "example") { showTag("short", fs >= 1); showTag("cool", fc >= 1); }
  if (loop === "hero" && dt > 2 * D + 2.5 && !reduce) { pairIdx = (pairIdx + 1) % PAIRS.length; t0 = now; drawn = false; fitPair(PAIRS[pairIdx]); }
  if (loop === "example" && drawn) return;
  raf = requestAnimationFrame(frame);
}
function draw(kind) { loop = kind; t0 = performance.now(); drawn = false; if (!raf) raf = requestAnimationFrame(frame); }
const stopDraw = () => { loop = null; };
function fitPair(p, panel = null, labels = false, tilt = false) {
  const all = [...p.shortest.coords, ...p.coolest.coords];
  const lons = all.map((c) => c[0]), lats = all.map((c) => c[1]);
  const W = window.innerWidth, side = labels ? 150 : 40;
  const pad = { top: 110, bottom: 72, left: side, right: side };
  if (panel) pad.left = Math.min(panel.getBoundingClientRect().right + 30 + (labels ? 110 : 0), W * 0.6);
  else pad.left = Math.min(document.querySelector(".hero .content").getBoundingClientRect().right + 40, W * 0.6);
  map.setPadding({ top: 0, bottom: 0, left: 0, right: 0 });
  const view = tilt ? { pitch: 55, bearing: -28 } : { pitch: 0, bearing: 0 };
  const bounds = [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]];
  const cam = map.cameraForBounds(bounds, { padding: pad, maxZoom: 17, bearing: view.bearing })
    ?? map.cameraForBounds(bounds, { padding: 20, maxZoom: 17, bearing: view.bearing });
  if (cam) map.flyTo({ ...cam, ...view, zoom: cam.zoom + (tilt ? 0.1 : 0), duration: 1200 });
}

// ---- sources added once the style is in: the Landsat image, the heat images (one per hour), the walk
let walk = null, ready = false, heatLoaded = false;
const TH = Object.keys(story.thermal.wide.images).sort();
function loadHeat() {                       // 12 hourly images of about 1.5 MB: only when the reader gets near them
  if (heatLoaded || !ready) return;
  heatLoaded = true;
  const order = [T15, ...TH.keys()].filter((v, i, a) => a.indexOf(v) === i);       // 15:00 first
  for (const i of order) {
    map.addSource(`th${i}`, { type: "image", url: `data/${story.thermal.wide.images[TH[i]]}?v=${VERSION}`, coordinates: story.thermal.wide.corners });
    map.addLayer({ id: `th${i}`, type: "raster", source: `th${i}`, layout: { visibility: "none" },
      paint: { "raster-opacity": 0, "raster-opacity-transition": { duration: 350 }, "raster-fade-duration": 0 } }, "lines");
  }
  map.addSource("thd", { type: "image", url: `data/${story.thermal.detail.image}?v=${VERSION}`, coordinates: story.thermal.detail.corners });
  map.addLayer({ id: "thd", type: "raster", source: "thd", layout: { visibility: "none" }, paint: { "raster-opacity": 0, "raster-fade-duration": 0 } }, "lines");
}
styleReady.then(() => {
  map.addSource("lst", { type: "image", url: `data/${L.image}?v=${VERSION}`, coordinates: L.coordinates });
  map.addLayer({ id: "lst", type: "raster", source: "lst", layout: { visibility: "none" }, paint: { "raster-opacity": 0, "raster-fade-duration": 0 } }, "lines");
  map.moveLayer("ref", "mass");
  map.getSource("grid").setData(grid);
  map.getSource("bld").setData(layers.buildings);
  map.getSource("ring").setData({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: circle(P0) } });
  map.getSource("spots").setData({ type: "FeatureCollection", features: story.spots.points.map((p) => (
    { type: "Feature", properties: { c: PILL[p.kind][1] }, geometry: { type: "Point", coordinates: [p.lon, p.lat] } })) });
  walk = createWalk(map, walkData, $("wk-chart"), { height: 150,
    onStep: (s) => {
      for (const r of ["short", "cool"]) {
        set(`wk-n-${r}`, s[r].now === null ? "–" : degC(s[r].now));
        $(`wk-s-${r}`).innerHTML = `walked <b>${m(s[r].walked)}</b><br><b>${m(s[r].ext)}</b> above 46 °C so far`;
      }
    },
    onEnd: (W) => {
      $("wk-end").innerHTML = `The coolest walk is <b>${m(W.cool.total - W.short.total)}</b> longer and feels <b class="cool">${degC(W.short.mean_u - W.cool.mean_u)}</b> cooler on average.`;
      $("wk-end").classList.add("on");
    },
  });
  ready = true;
  enter(current, true);
});

// ---- screens: each sets the map for its sentence. Layers fade (opacity) rather than switch, the grid cross-fades
// between two layers, and consecutive screens share their place where they can, so one screen grows out of the last.
const BASE = { lst: [["raster-opacity", 0.9]], lines: [["line-opacity", 1]], city: [["line-opacity", 0.9]],
  "grid-a": [["fill-opacity", 0.8]], "grid-b": [["fill-opacity", 0.8]], bld: [["fill-opacity", 1]],
  types: [["line-opacity", 1]], "types-in": [["line-opacity", 1]], st: [["circle-opacity", 1], ["circle-stroke-opacity", 1]],
  ring: [["line-opacity", 0.85]], spots: [["circle-opacity", 1], ["circle-stroke-opacity", 1]], mass: [["fill-extrusion-opacity", 0.55]],
  thd: [["raster-opacity", 0.95]] };
ROUTE_LAYERS("hero").forEach((id) => { BASE[id] = [["line-opacity", id.includes("case") ? 0.85 : 1]]; });
const hideT = {};
function fade(id, on, value) {
  if (!map.getLayer(id)) return;
  const shown = map.getLayoutProperty(id, "visibility") !== "none";
  clearTimeout(hideT[id]);
  if (on) {
    if (!shown) map.setLayoutProperty(id, "visibility", "visible");
    for (const [p, v] of BASE[id]) map.setPaintProperty(id, p, value ?? v);
  } else if (shown) {
    for (const [p] of BASE[id]) map.setPaintProperty(id, p, 0);
    hideT[id] = setTimeout(() => map.setLayoutProperty(id, "visibility", "none"), FADE + 100);
  }
}
let gridOn = null, gridKey = null;
function gridFill(key) {
  const dim = (c, a) => `rgba(${parseInt(c.slice(1, 3), 16)},${parseInt(c.slice(3, 5), 16)},${parseInt(c.slice(5, 7), 16)},${a})`;
  if (key === "cluster") return ["match", ["get", "cluster"], 1, CLUSTERS[1][2], 0, dim(CLUSTERS[0][2], 0.3), 2, dim(CLUSTERS[2][2], 0.3), "#555"];
  if (key === "end") return ["match", ["get", "cluster"], 0, CLUSTERS[0][2], "rgba(42,51,64,0.55)"];
  return rampExpr(key);
}
function showGrid(key, opacity) {
  if (!key) { if (gridOn) fade(gridOn, false); gridOn = null; gridKey = null; return; }
  if (key === gridKey) { fade(gridOn, true, opacity); return; }       // same colours: only the opacity changes
  const next = gridOn === "grid-a" ? "grid-b" : "grid-a";
  map.setPaintProperty(next, "fill-color", gridFill(key));
  fade(next, true, opacity); if (gridOn) fade(gridOn, false);
  gridOn = next; gridKey = key;
}
let thOn = false, thT = 0;
const showThermal = (i, detail = false) => {
  if (i >= 0) loadHeat();
  if (!heatLoaded) return;
  clearTimeout(thT);
  if (i >= 0) {
    TH.forEach((_, j) => { map.setLayoutProperty(`th${j}`, "visibility", "visible"); map.setPaintProperty(`th${j}`, "raster-opacity", j === i ? 0.92 : 0); });
    thOn = true;
  } else if (thOn) {
    TH.forEach((_, j) => map.setPaintProperty(`th${j}`, "raster-opacity", 0));
    thOn = false;
    thT = setTimeout(() => TH.forEach((_, j) => map.setLayoutProperty(`th${j}`, "visibility", "none")), FADE + 100);
  }
  fade("thd", detail);
};
// heat screens: the wide image fills the window; at least zoom z, centred on the image
const [[wx0, wy1], , [wx1, wy0]] = story.thermal.wide.corners;
function heatView(z = 15.3) {
  const lat = (wy0 + wy1) / 2, C = 156543.03 * Math.cos((lat * Math.PI) / 180);
  const mpp = Math.min(story.thermal.wide.size_m[0] / window.innerWidth, story.thermal.wide.size_m[1] / window.innerHeight) * 0.92;
  return { center: [(wx0 + wx1) / 2, (wy0 + wy1) / 2], zoom: Math.max(z, Math.log2(C / mpp)), padding: { left: 0, top: 0, right: 0, bottom: 0 } };
}
const fly = (view) => {                      // centre a view in the part of the screen the text panel leaves free
  const p = document.querySelector(`[data-step="${current}"] .panel`)?.getBoundingClientRect();
  map.flyTo({ pitch: 0, bearing: 0, ...view, padding: p ? { left: p.right + 40, top: 90, right: 0, bottom: 0 } : {} });
};
const CITY = { center: [-75.135, 39.995], zoom: 10.9, duration: 2200 };
const SHADE_AT = hero.types_focus.center;     // the shade chapter stays in one place, next to the story's station
function scene({ city = false, lines = false, grid = null, gridOpacity = undefined, bld = false, route = false, mass = false, types = false,
  st = false, lst = false, ring = false, pins = false, th = -1, detail = false, spots = false, cityOpacity = 0.9, t = T15 } = {}) {
  if (city) map.setPaintProperty("city", "line-color", sidewalkColor("shade", t));
  fade("lines", lines); fade("city", city, cityOpacity); fade("bld", bld); fade("mass", mass); fade("st", st); fade("ring", ring);
  fade("spots", spots); fade("lst", lst);
  ROUTE_LAYERS("hero").forEach((id) => fade(id, route));
  for (const id of ["types", "types-in"]) fade(id, types);
  showGrid(grid, gridOpacity);
  showPills(typePills, types); showPills(spotPills, spots); showPins(pins);
  showThermal(th, detail);
  if (current !== "hours" && current !== "heatday") stopDay();
  if (current !== "example" && current !== "indoor") hideTags();
  if (current !== "walk") walk?.clear();
  if (current !== "hero" && current !== "example") stopDraw();
}
const STEPS = {
  hero: () => { scene({ route: true }); pairIdx = 0; draw("hero"); fitPair(PAIRS[0]); },
  heat: () => { scene({ lst: true }); fly({ ...CITY, zoom: 10.35 }); },
  persona: () => {
    // from the city's heat to the stations, then down to one of them: the camera stays put while the layers change
    scene({ st: true });
    const go = () => { if (current !== "persona") return; fade("ring", true); showPins(true); fly({ center: P0, zoom: 14.4, duration: 2800 }); };
    if (map.getZoom() < 12) setTimeout(go, 1200); else go();
  },
  sources: () => { scene({ lines: true }); fly({ center: SHADE_AT, zoom: 17, duration: 2600 }); },
  types: () => { scene({ lines: true, types: true }); fly({ center: SHADE_AT, zoom: 17, duration: 900 }); },
  hours: () => { scene({ lines: true, city: true, t: 0 }); fly({ center: SHADE_AT, zoom: 16.1, duration: 2000 }); playDay(dayHours); },
  example: () => {
    scene({ lines: true, route: true, pins: true }); placeTags(PERSONA, false); hideTags(); draw("example");
    fitPair(PERSONA, document.querySelector('[data-step="example"] .panel'), true);
  },
  sunny: () => { scene({ grid: "sun_share" }); fly(CITY); },
  waffle: () => { scene({ grid: "sun_share", gridOpacity: 0.12 }); fly(CITY); },
  clusters: () => { scene({ grid: "cluster" }); fly(CITY); },
  felt: () => { scene({ th: T15 }); map.flyTo({ ...heatView(), duration: 2400 }); },
  heatday: () => { scene({ th: 0 }); map.flyTo({ ...heatView(15.5), duration: 1200 }); playDay(dayHeat); },
  trees: () => {
    scene({ th: T15, detail: true, spots: true });
    const c = story.spots.points.reduce((a, p) => [a[0] + p.lon / 3, a[1] + p.lat / 3], [0, 0]);
    fly({ center: c, zoom: 17.6, duration: 2000 });
  },
  walk: () => {
    scene({ lines: true });
    const e = PW, b = walk.bounds(e), panel = document.querySelector('[data-step="walk"] .panel').getBoundingClientRect();
    map.setPadding({ top: 0, bottom: 0, left: 0, right: 0 });       // a flyTo before left its padding on the map
    map.fitBounds(b, { padding: { left: panel.right + 50, right: 70, top: 120, bottom: 70 }, duration: 1600, maxZoom: 17 });
    $("wk-end").classList.remove("on");
    const W = walk.play(e);
    $("wk-head").innerHTML = `On the shortest walk home, <b class="sun">${m(W.short.ext)}</b> are in extreme heat stress. On the coolest, <b class="cool">${m(W.cool.ext)}</b>.`;
    set("wk-label", `${stationName(PERSONA)} → home · June 24, 15:00`);
  },
  cityheat: () => { scene({ grid: "u_cool" }); fly(CITY); },
  indoor: () => {
    scene({ city: true, route: true, mass: true, cityOpacity: 0.35 });
    const p = hero.indoor;
    map.getSource("hero").setData(routeFeatures(p, 1, 1, true));
    placeTags(p, true); showTag("short", true); showTag("cool", true);
    fitPair(p, document.querySelector('[data-step="indoor"] .panel'), true, true);
  },
  end: () => { scene({ grid: "end" }); fly(CITY); },
};
$("wk-replay").addEventListener("click", () => { if (current === "walk") STEPS.walk(); });

// ---- the day plays by itself on two screens: 08:00 -> 19:00, one hour every 0.9 s, a pause, again
function setClock(sec, h) {
  sec.querySelector("[data-clock]").textContent = `${String(h).padStart(2, "0")}:00`;
  dial(sec.querySelector("[data-dial]"), `0624_${String(h).padStart(2, "0")}`);
  sec.querySelector("[data-hours]")?.querySelectorAll("i").forEach((el, i) => el.classList.toggle("on", i <= h - 8));
}
document.querySelectorAll("[data-hours]").forEach((el) => { el.innerHTML = "<i></i>".repeat(12); });
const dayHours = (i) => {
  setClock(document.querySelector('[data-step="hours"]'), 8 + i);
  if (ready) map.setPaintProperty("city", "line-color", sidewalkColor("shade", i));
};
const dayHeat = (i) => { setClock(document.querySelector('[data-step="heatday"]'), 8 + i); hourChart(8 + i); if (ready) showThermal(i); };
let dayTimer = null;
function playDay(show) {
  stopDay();
  let i = 0;
  show(i);
  if (reduce) { show(T15); return; }
  dayTimer = setInterval(() => { i = i >= 13 ? 0 : i + 1; if (i <= 11) show(i); }, 900);   // 12-13: hold 19:00
}
function stopDay() { clearInterval(dayTimer); dayTimer = null; }
setClock(document.querySelector('[data-step="hours"]'), 8); setClock(document.querySelector('[data-step="heatday"]'), 8); hourChart(8);

// ---- chapters and the scroll controller
const CHAPTERS = ["The heat", "The shade", "Sun", "Heat", "Indoors", "Your walk"];
const SECS = [...document.querySelectorAll("section[data-step]")];
const chap = $("chapters");
chap.innerHTML = CHAPTERS.map((n, i) => `<button data-c="${i + 1}"><b>${String(i + 1).padStart(2, "0")}</b>${n}<span class="fill"></span></button>`).join("");
chap.addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  SECS.find((s) => s.dataset.ch === b.dataset.c).scrollIntoView({ behavior: reduce ? "auto" : "smooth" });
});
function chapterBar(sec) {
  const c = sec.dataset.ch;
  chap.classList.toggle("hidden", c === "0");
  chap.querySelectorAll("button").forEach((b) => {
    const mine = SECS.filter((s) => s.dataset.ch === b.dataset.c), k = mine.indexOf(sec);
    b.classList.toggle("on", b.dataset.c === c);
    b.querySelector(".fill").style.width = +b.dataset.c < +c ? "calc(100% - 20px)" : b.dataset.c === c ? `calc((100% - 20px) * ${(k + 1) / mine.length})` : "0";
  });
}
// the big numbers of a screen count up when it comes on
function countUp(sec) {
  if (reduce) return;
  sec.querySelectorAll(".big.num:not([data-clock])").forEach((el) => {
    const final = el.dataset.final ?? (el.dataset.final = el.textContent);
    const mm = final.match(/[\d,]*\.?\d+/);
    if (!mm) return;
    const target = parseFloat(mm[0].replace(/,/g, "")), dec = (mm[0].split(".")[1] || "").length, t0 = performance.now();
    const fmt = (v) => v.toLocaleString("en-US", { minimumFractionDigits: dec, maximumFractionDigits: dec, useGrouping: mm[0].includes(",") });
    const step = (now) => {
      const f = Math.min(1, (now - t0) / 900);
      el.textContent = final.replace(mm[0], fmt(target * (1 - (1 - f) ** 3)));
      if (f < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });
}
let current = "hero";
function enter(step, force = false) {
  if (step === current && !force) return;
  current = step;
  const sec = SECS.find((s) => s.dataset.step === step);
  SECS.forEach((s) => s.classList.toggle("active", s === sec));
  chapterBar(sec);
  countUp(sec);
  if (ready) STEPS[step]();
}
function onScroll() {
  const y = window.innerHeight * 0.45;
  const sec = SECS.find((s) => { const r = s.getBoundingClientRect(); return r.top <= y && r.bottom > y; }) ?? SECS[0];
  enter(sec.dataset.step);
  if (+sec.dataset.ch >= 3) loadHeat();        // the heat images load while the reader is in the sun chapter
}
let ticking = false;
addEventListener("scroll", () => { if (!ticking) { ticking = true; requestAnimationFrame(() => { ticking = false; onScroll(); }); } }, { passive: true });
onScroll();

// the buttons on the last screen hand the current view, time and measure to the tool and the city map
document.querySelectorAll(".cta a").forEach((a) => a.addEventListener("click", () => {
  const b = map.getBounds(), q = new URLSearchParams({ t: T15, m: "shade" });
  if (a.dataset.to === "map") q.set("b", [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => v.toFixed(5)).join(","));
  a.href = `${a.getAttribute("href").split("#")[0]}#${q}`;
}));

// keys and presentation clickers move one screen
const KEYS = { PageDown: 1, ArrowDown: 1, ArrowRight: 1, PageUp: -1, ArrowUp: -1, ArrowLeft: -1 };
addEventListener("keydown", (e) => {
  if (e.altKey || e.ctrlKey || e.metaKey || /^(INPUT|TEXTAREA|SELECT|BUTTON|A)$/.test(e.target.tagName)) return;
  const step = e.key === " " ? (e.shiftKey ? -1 : 1) : KEYS[e.key];
  if (!step) return;
  e.preventDefault();
  const sec = SECS.find((s) => s.dataset.step === current);
  const next = SECS[Math.max(0, Math.min(SECS.length - 1, SECS.indexOf(sec) + step))];
  window.scrollTo({ top: next.getBoundingClientRect().top + window.scrollY, behavior: reduce ? "auto" : "smooth" });
});
