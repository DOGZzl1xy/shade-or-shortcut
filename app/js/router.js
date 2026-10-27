// Shortest and shade-weighted routing in the browser: a line-by-line port of coolpath.routing
// (src/coolpath/routing.py). Same CSR construction, same (cost, node) heap order, same cost formula
// C = L_sun + (1 - 0.5θ) L_tree + (1 - θ) L_bldg + (1 - 1.5θ) L_indoor with θ from the distance walked,
// so tests/test_js_consistency.py can require identical paths.

export function theta(l, p) {
  return p.a / (1 + Math.exp(-p.k * (l - p.x0))) + p.b;
}

// edge kinds in demo_network.json: 0 outdoor, 1 entrance connector, 2 indoor, 3 open-air part of a passage
export function scenarioEdges(net, scenario) {
  const { kind, penncard } = net.edges;
  const sel = [];
  for (let i = 0; i < kind.length; i++) {
    const indoorSide = kind[i] > 0;
    if (scenario === "outdoor" ? !indoorSide : scenario === "no_penncard" ? !indoorSide || !penncard[i] : true) sel.push(i);
  }
  return sel;
}

// CSR over the selected edges, in file order: every edge in both directions with the same local id,
// neighbours ordered by a stable sort on the source node (numpy argsort kind="stable").
export function buildCSR(net, sel) {
  const n = net.nodes.lon.length, m = sel.length;
  const src = new Int32Array(2 * m), dst = new Int32Array(2 * m), eid = new Int32Array(2 * m);
  for (let j = 0; j < m; j++) {
    const i = sel[j];
    src[j] = net.edges.u[i]; dst[j] = net.edges.v[i]; eid[j] = j;
    src[m + j] = net.edges.v[i]; dst[m + j] = net.edges.u[i]; eid[m + j] = j;
  }
  const order = Array.from({ length: 2 * m }, (_, k) => k).sort((a, b) => src[a] - src[b]);   // stable
  const indptr = new Int32Array(n + 1);
  for (let k = 0; k < 2 * m; k++) indptr[src[k] + 1]++;
  for (let k = 0; k < n; k++) indptr[k + 1] += indptr[k];
  const nbr = new Int32Array(2 * m), eidS = new Int32Array(2 * m);
  order.forEach((k, pos) => { nbr[pos] = dst[k]; eidS[pos] = eid[k]; });
  return { n, indptr, nbr, eid: eidS, sel };
}

// per-scenario edge arrays (local ids) for one time index
export function edgeArrays(net, csr, t) {
  const pick = (a) => Float64Array.from(csr.sel, (i) => a[i]);
  return {
    length: pick(net.edges.len), sun: pick(net.L.sun[t]), tree: pick(net.L.tree[t]),
    bldg: pick(net.L.bldg[t]), indoor: pick(net.L.indoor[t]),
  };
}

class Heap {                        // binary min-heap of [cost, node], ordered like Python tuples
  constructor() { this.a = []; }
  less(x, y) { return x[0] < y[0] || (x[0] === y[0] && x[1] < y[1]); }
  push(x) {
    const a = this.a; a.push(x);
    let i = a.length - 1;
    while (i > 0) { const p = (i - 1) >> 1; if (!this.less(a[i], a[p])) break; [a[i], a[p]] = [a[p], a[i]]; i = p; }
  }
  pop() {
    const a = this.a, top = a[0], last = a.pop();
    if (a.length) {
      a[0] = last;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1, r = l + 1;
        let s = i;
        if (l < a.length && this.less(a[l], a[s])) s = l;
        if (r < a.length && this.less(a[r], a[s])) s = r;
        if (s === i) break;
        [a[i], a[s]] = [a[s], a[i]]; i = s;
      }
    }
    return top;
  }
  get size() { return this.a.length; }
}

// mode "shortest" | "shade"; opts: {theta: {a,k,x0,b}, thetaFixed (<0 = dynamic), treeFactor, indoorFactor, maxDist}
export function search(csr, arr, src, mode, opts = {}) {
  const { n, indptr, nbr, eid } = csr;
  const tp = opts.theta, thF = opts.thetaFixed ?? -1, tf = opts.treeFactor ?? 0.5, inf = opts.indoorFactor ?? 1.5;
  const maxDist = opts.maxDist ?? Infinity;
  const cost = new Float64Array(n).fill(Infinity), dist = new Float64Array(n).fill(Infinity);
  const pred = new Int32Array(n).fill(-1), predE = new Int32Array(n).fill(-1), done = new Uint8Array(n);
  cost[src] = 0; dist[src] = 0;
  const heap = new Heap();
  heap.push([0, src]);
  while (heap.size) {
    const [c, a] = heap.pop();
    if (done[a]) continue;
    done[a] = 1;
    let th = 0;
    if (mode === "shade") th = thF < 0 ? theta(dist[a], tp) : thF;
    for (let j = indptr[a]; j < indptr[a + 1]; j++) {
      const b = nbr[j];
      if (done[b]) continue;
      const e = eid[j];
      const nd = dist[a] + arr.length[e];
      if (nd > maxDist) continue;
      const w = mode === "shortest" ? arr.length[e]
        : arr.sun[e] + (1.0 - tf * th) * arr.tree[e] + (1.0 - th) * arr.bldg[e] + (1.0 - inf * th) * arr.indoor[e];
      const nc = c + w;
      if (nc < cost[b]) { cost[b] = nc; dist[b] = nd; pred[b] = a; predE[b] = e; heap.push([nc, b]); }
    }
  }
  return { cost, dist, pred, predE };
}

export function pathEdges(res, dst) {   // local edge ids from the source to dst
  const out = [];
  let b = dst;
  while (res.pred[b] >= 0) { out.push(res.predE[b]); b = res.pred[b]; }
  return out.reverse();
}

// weighted exposure of a path walked in order (coolpath.routing.path_cost)
export function pathTotals(arr, local, opts) {
  const tf = opts.treeFactor ?? 0.5, inf = opts.indoorFactor ?? 1.5;
  let L = 0, sun = 0, tree = 0, bldg = 0, indoor = 0, C = 0;
  for (const e of local) {
    const th = theta(L, opts.theta);
    C += arr.sun[e] + (1 - tf * th) * arr.tree[e] + (1 - th) * arr.bldg[e] + (1 - inf * th) * arr.indoor[e];
    L += arr.length[e]; sun += arr.sun[e]; tree += arr.tree[e]; bldg += arr.bldg[e]; indoor += arr.indoor[e];
  }
  return { length: L, sun, tree, bldg, indoor, C };
}

// shortest walk on the outdoor-only network (the baseline used throughout the analysis) and coolest walk on the
// scenario network, between two nodes at time index t; edges as file indices
export function route(net, scenario, t, o, d, cache = {}) {
  const graph = (sc) => cache[sc] ?? (cache[sc] = buildCSR(net, scenarioEdges(net, sc)));
  const p = net.params;
  const opts = { theta: p.theta, treeFactor: p.tree_factor, indoorFactor: p.indoor_factor };
  const csrO = graph("outdoor"), arrO = edgeArrays(net, csrO, t);
  const csrS = graph(scenario), arrS = scenario === "outdoor" ? arrO : edgeArrays(net, csrS, t);
  const ls = pathEdges(search(csrO, arrO, o, "shortest"), d);
  const lh = pathEdges(search(csrS, arrS, o, "shade", opts), d);
  return {
    shortest: { edges: ls.map((e) => csrO.sel[e]), ...pathTotals(arrO, ls, opts) },
    coolest: { edges: lh.map((e) => csrS.sel[e]), ...pathTotals(arrS, lh, opts) },
  };
}
