// "The walk, felt": the story's walk home as the shortest and the coolest walk (UTCI cost), two walkers moving at the
// same pace; the walked part of each route turns the colour of its UTCI, and a profile below draws UTCI against the
// distance walked, with the extreme heat-stress band (UTCI >= 46 C). Data: data/walk.json (phase 10 web-walk), one
// entry per hour of the first study day, every walk as its 2 m sample points (lon, lat, UTCI, length per point).
import { COLORS, UTCI_STOPS } from "./common.js?v=dev";

const C_STOPS = UTCI_STOPS.map((v, i) => (i % 2 ? v : v / 10));          // UTCI in C, not x 10
const uColor = ["interpolate", ["linear"], ["get", "u"], ...C_STOPS];
const ROLES = { short: { name: "Shortest walk", color: COLORS.sun, off: -3.2 }, cool: { name: "Coolest walk", color: COLORS.cool, off: 3.2 } };
const SPEED = 95;                     // metres of walk per second of animation (1.2 km in about 13 s)
const EMPTY = { type: "FeatureCollection", features: [] };
const EXTREME = 46;                   // UTCI heat-stress class 'extreme' (Bröde et al. 2012)

// cumulative distance at each point (the point sits in the middle of the length it stands for)
function prep(w) {
  const d = [];
  let s = 0;
  w.seg.forEach((g, i) => { d.push(s + g / 2); s += g; });
  // display line: 5-point running mean of UTCI (the 2 m values flicker between sun and shade pixels)
  const sm = w.u.map((_, i) => {
    const win = w.u.slice(Math.max(0, i - 2), i + 3).filter((x) => x !== null);
    return win.length ? win.reduce((a, b) => a + b, 0) / win.length : null;
  });
  // metres in extreme heat stress, from the same (rounded) values the walkers report
  const ext = w.u.reduce((a, u, i) => a + (u !== null && u >= EXTREME ? w.seg[i] : 0), 0);
  return { ...w, d, sm, total: s, ext };
}
const at = (w, dist) => {                // index of the last point walked at distance dist
  let lo = 0, hi = w.d.length - 1;
  while (lo < hi) { const m = (lo + hi + 1) >> 1; if (w.d[m] <= dist) lo = m; else hi = m - 1; }
  return lo;
};

export function createWalk(map, data, chart, { onStep = () => {}, onEnd = () => {} } = {}) {
  map.addSource("walk-track", { type: "geojson", data: EMPTY });
  map.addSource("walk-trail", { type: "geojson", data: EMPTY });
  map.addSource("walk-now", { type: "geojson", data: EMPTY });
  for (const r of Object.keys(ROLES)) {
    const o = ROLES[r].off, f = ["==", ["get", "r"], r];
    map.addLayer({ id: `walk-track-${r}`, type: "line", source: "walk-track", filter: f, layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": ROLES[r].color, "line-width": 2, "line-opacity": 0.35, "line-offset": o,
        ...(r === "cool" ? { "line-dasharray": [1.5, 1.5] } : {}) } });
    map.addLayer({ id: `walk-case-${r}`, type: "line", source: "walk-trail", filter: ["all", f, ["<=", ["get", "i"], -1]],
      layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": "#07080b", "line-width": 8, "line-offset": o, "line-opacity": 0.8 } });
    map.addLayer({ id: `walk-trail-${r}`, type: "line", source: "walk-trail", filter: ["all", f, ["<=", ["get", "i"], -1]],
      layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": uColor, "line-width": 5.5, "line-offset": o } });
  }
  map.addLayer({ id: "walk-glow", type: "circle", source: "walk-now",
    paint: { "circle-radius": 16, "circle-blur": 0.9, "circle-color": ["get", "c"], "circle-opacity": 0.55 } });
  map.addLayer({ id: "walk-dot", type: "circle", source: "walk-now",
    paint: { "circle-radius": 6, "circle-color": "#ffffff", "circle-stroke-color": ["get", "c"], "circle-stroke-width": 3 } });
  // the current UTCI beside each walker
  const tags = Object.fromEntries(Object.keys(ROLES).map((r) => {
    const el = document.createElement("div");
    el.className = `walk-tag walk-tag-${r}`;
    return [r, { el, mk: new maplibregl.Marker({ element: el, anchor: r === "short" ? "right" : "left", offset: [r === "short" ? -14 : 14, 0] }) }];
  }));

  let W = null, t0 = 0, raf = 0, done = false;
  function load(entry) {
    W = { short: prep(entry.short), cool: prep(entry.cool), time: entry.time };
    const track = [], trail = [];
    for (const r of Object.keys(ROLES)) {
      const w = W[r];
      track.push({ type: "Feature", properties: { r }, geometry: { type: "LineString", coordinates: w.lon.map((x, i) => [x, w.lat[i]]) } });
      for (let i = 0; i + 1 < w.lon.length; i++) {
        trail.push({ type: "Feature", properties: { r, i, u: w.u[i] ?? w.u[i + 1] ?? 40 },
          geometry: { type: "LineString", coordinates: [[w.lon[i], w.lat[i]], [w.lon[i + 1], w.lat[i + 1]]] } });
      }
    }
    map.getSource("walk-track").setData({ type: "FeatureCollection", features: track });
    map.getSource("walk-trail").setData({ type: "FeatureCollection", features: trail });
    drawChart();
  }

  // ---- profile chart (SVG): UTCI against distance walked, both walks, the heat-stress bands behind
  const WD = 760, HT = 214, M = { l: 40, r: 92, t: 22, b: 26 };
  let x, y, lines = {}, dots = {}, clip;
  function drawChart() {
    const maxD = Math.max(W.short.total, W.cool.total);
    const all = [...W.short.sm, ...W.cool.sm].filter((v) => v !== null);
    const lo = Math.floor(Math.min(34, ...all) - 0.5), hi = Math.ceil(Math.max(47, ...all) + 0.5);
    x = (d) => M.l + ((WD - M.l - M.r) * d) / maxD;
    y = (u) => M.t + ((HT - M.t - M.b) * (hi - u)) / (hi - lo);
    const band = (a, b, fill, text) => `<rect x="${M.l}" y="${y(Math.min(b, hi))}" width="${WD - M.l - M.r}" height="${y(Math.max(a, lo)) - y(Math.min(b, hi))}" fill="${fill}"/>
      <text x="${WD - M.r + 8}" y="${(y(Math.max(a, lo)) + y(Math.min(b, hi))) / 2 + 4}" class="wc-band">${text}</text>`;
    const yt = [], xt = [];
    for (let u = Math.ceil(lo / 2) * 2; u <= hi; u += 2) yt.push(`<text x="${M.l - 8}" y="${y(u) + 4}" text-anchor="end">${u}</text><line x1="${M.l}" x2="${WD - M.r}" y1="${y(u)}" y2="${y(u)}" class="wc-grid"/>`);
    for (let d = 0; d <= maxD; d += 200) xt.push(`<text x="${x(d)}" y="${HT - 6}" text-anchor="middle">${d === 0 ? "0 m" : d}</text>`);
    const path = (w) => w.sm.map((u, i) => (u === null ? "" : `${i && w.sm[i - 1] !== null ? "L" : "M"}${x(w.d[i]).toFixed(1)},${y(u).toFixed(1)}`)).join("");
    chart.innerHTML = `<svg viewBox="0 0 ${WD} ${HT}" width="100%" role="img" aria-label="UTCI along the shortest and the coolest walk">
      <defs><clipPath id="wc-clip"><rect x="0" y="0" width="0" height="${HT}"/></clipPath></defs>
      ${band(46, 99, "rgba(255,138,61,0.20)", "extreme")}${band(38, 46, "rgba(255,138,61,0.07)", "very strong")}${band(32, 38, "rgba(255,255,255,0.02)", "strong")}
      <g class="wc-axis">${yt.join("")}${xt.join("")}<text x="${M.l - 8}" y="10" text-anchor="end">°C</text></g>
      <g clip-path="url(#wc-clip)">
        <path d="${path(W.short)}" class="wc-line" stroke="${ROLES.short.color}"/>
        <path d="${path(W.cool)}" class="wc-line" stroke="${ROLES.cool.color}"/>
      </g>
      <circle r="5" class="wc-dot" stroke="${ROLES.short.color}" id="wc-dot-short"/><circle r="5" class="wc-dot" stroke="${ROLES.cool.color}" id="wc-dot-cool"/>
    </svg>`;
    clip = chart.querySelector("#wc-clip rect");
    dots = { short: chart.querySelector("#wc-dot-short"), cool: chart.querySelector("#wc-dot-cool") };
  }

  function frame(now) {
    raf = 0;
    const dist = ((now - t0) / 1000) * SPEED;
    const pos = [], state = {};
    for (const r of Object.keys(ROLES)) {
      const w = W[r], walked = Math.min(dist, w.total), k = at(w, walked);
      const f = ["all", ["==", ["get", "r"], r], ["<", ["get", "i"], k]];
      map.setFilter(`walk-trail-${r}`, f); map.setFilter(`walk-case-${r}`, f);
      pos.push({ type: "Feature", properties: { c: ROLES[r].color }, geometry: { type: "Point", coordinates: [w.lon[k], w.lat[k]] } });
      let ext = 0;
      for (let i = 0; i <= k; i++) if (w.u[i] !== null && w.u[i] >= EXTREME) ext += w.seg[i];
      state[r] = { walked, now: w.u[k], ext, finished: dist >= w.total };
      tags[r].el.textContent = w.u[k] === null ? "" : `${w.u[k].toFixed(1)} °C`;
      tags[r].el.style.setProperty("--c", ROLES[r].color);
      tags[r].mk.setLngLat([w.lon[k], w.lat[k]]).addTo(map);
      if (w.sm[k] !== null) { dots[r].setAttribute("cx", x(w.d[k])); dots[r].setAttribute("cy", y(w.sm[k])); }
    }
    clip.setAttribute("width", x(dist));
    map.getSource("walk-now").setData({ type: "FeatureCollection", features: pos });
    onStep(state);
    if (state.short.finished && state.cool.finished) {
      if (!done) { done = true; Object.values(tags).forEach((t) => t.mk.remove()); onEnd(W); }
      return;
    }
    raf = requestAnimationFrame(frame);
  }
  return {
    play(entry) {
      cancelAnimationFrame(raf);
      load(entry); done = false; t0 = performance.now();
      raf = requestAnimationFrame(frame);
      return W;
    },
    stop() { cancelAnimationFrame(raf); raf = 0; },
    bounds(entry) {
      const xs = [...entry.short.lon, ...entry.cool.lon], ys = [...entry.short.lat, ...entry.cool.lat];
      return [[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]];
    },
    // take everything off the map (the story leaves the screen)
    clear() {
      this.stop();
      Object.values(tags).forEach((t) => t.mk.remove());
      for (const id of ["walk-track", "walk-trail", "walk-now"]) map.getSource(id).setData(EMPTY);
    },
    remove() { this.clear(); },
  };
}
