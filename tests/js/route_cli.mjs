// node tests/js/route_cli.mjs <demo_network.json> <ods.json>  ->  JSON results on stdout
// ods.json: [{o, d, scenario, t}, ...]; used by tests/test_js_consistency.py
import { readFileSync } from "node:fs";
import { route } from "../../site/app/js/router.js";

const net = JSON.parse(readFileSync(process.argv[2], "utf8"));
const ods = JSON.parse(readFileSync(process.argv[3], "utf8"));
const caches = {};
const out = ods.map(({ o, d, scenario, t }) => {
  const r = route(net, scenario, t, o, d, caches);
  return { shortest: r.shortest.edges, coolest: r.coolest.edges, shortest_len: r.shortest.length, coolest_len: r.coolest.length };
});
process.stdout.write(JSON.stringify(out));
