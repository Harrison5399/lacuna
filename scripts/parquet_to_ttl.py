"""Turn a graph folder (nodes.parquet + edges.parquet) into one Turtle file, for an RDF viewer or triple store, or
with --html into one interactive page to look at it in a browser.

Works on a literature graph (runs/<experiment>/05-literature/round_<n>) and an experiment graph
(runs/<experiment>/02-expand/graph_<n>). Concepts get their public IRIs (MeSH's own linked-data IRIs, identifiers.org
for genes and papers), so the file links up with other RDF about them:
    node   <concept> a lac:Disease ; rdfs:label "Glioblastoma" ; lac:n_papers 20 ; ...   (every other column)
    edge   <subject> lac:treats <object> .                                               (what a viewer draws)
           [] a rdf:Statement ; rdf:subject ... ; rdf:predicate lac:treats ; rdf:object ... ;
              lac:n_papers 3 ; lac:n_negated 1 ; ... ; lac:paper <paper>, <paper> .       (its evidence; --plain drops it)
Only edges between two of the folder's nodes are written (an experiment graph also lists literature edges to
concepts not in it yet), and only nodes on a written edge. --min-papers and --relations thin the graph to a size a
viewer can draw; curated edges (MeSH, HGNC) have no paper counts and are always kept.

--html writes graph.html instead: nodes coloured by type and sized by papers (an experiment graph's seeds ringed),
edges coloured by relation, as thick as their paper count, dashed when most of their papers negate them. Click a
node or edge for everything known about it, with links to its papers, trials and MeSH / NCBI Gene records; filter
by relation, node type and papers per edge; find a concept by name. The page loads vis-network from unpkg.com;
the graph itself is inside the file and never leaves the computer. A few thousand edges draw fine, the full
literature graph of a big experiment is better thinned first (--min-papers, --relations).

Usage:
    python scripts/parquet_to_ttl.py runs/examples/05-literature/round_2
    python scripts/parquet_to_ttl.py runs/examples/05-literature/round_2 --plain --min-papers 2 --relations treats inhibits activates causes
    python scripts/parquet_to_ttl.py runs/examples/02-expand/graph_3 --out graph_3.ttl
    python scripts/parquet_to_ttl.py runs/examples/05-literature/round_2 --html
    python scripts/parquet_to_ttl.py runs/examples/02-expand/graph_3 --html --relations broader narrower treats tested_for
    python scripts/parquet_to_ttl.py /N/project/ontology_learning/L665/runs/default/05-literature/round_1 --plain --min-papers 3
"""
import argparse
import html
import json
import re
from pathlib import Path

import pyarrow.parquet as pq

VOCAB = "http://example.org/lacuna/"   # Lacuna's own terms (relations, node types, counts)
PREFIXES = {"mesh": "http://id.nlm.nih.gov/mesh/", "ncbigene": "http://identifiers.org/ncbigene/",
            "pubmed": "http://identifiers.org/pubmed/", "clinicaltrials": "http://identifiers.org/clinicaltrials/",
            "arxiv": "https://arxiv.org/abs/", "lac": VOCAB,
            "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#", "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            "xsd": "http://www.w3.org/2001/XMLSchema#"}
SAFE = re.compile(r"[A-Za-z0-9_]+(?:[.-]+[A-Za-z0-9_]+)*")   # a local name Turtle takes as is after "prefix:"
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Turn a graph folder's nodes.parquet and edges.parquet into Turtle.")
    parser.add_argument("graph", type=Path, help="Folder with nodes.parquet and edges.parquet")
    parser.add_argument("--out", type=Path, help="File to write (default: <graph>/graph.ttl, or graph.html)")
    parser.add_argument("--html", action="store_true", help="Write an interactive page instead of Turtle")
    parser.add_argument("--plain", action="store_true",
                        help="Only the edges themselves, no statement nodes with their counts and papers")
    parser.add_argument("--min-papers", type=int, default=1,
                        help="Keep literature edges stated by at least this many papers (default: 1, all)")
    parser.add_argument("--relations", nargs="+", help="Keep only these relations (default: all)")
    parser.add_argument("--undirected", nargs="+", default=["co_occurs", "associated_with"],
                        help="Relations drawn without an arrow, in --html (default: co_occurs associated_with)")
    return parser.parse_args()


def iri(identifier: str) -> str:
    """mesh:D005909 -> mesh:D005909, arxiv:hep-th/9901001 -> <https://arxiv.org/abs/hep-th/9901001>."""
    prefix, _, local = identifier.partition(":")
    if prefix not in PREFIXES or not local:
        prefix, local = "lac", "concept/" + identifier
    return f"{prefix}:{local}" if SAFE.fullmatch(local) else f"<{PREFIXES[prefix]}{local}>"


def term(name: str) -> str:
    """A column, relation or type as one of Lacuna's terms: "associated_with" -> lac:associated_with."""
    return "lac:" + (re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") or "unnamed")


def literal(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f'"{value!r}"^^xsd:double'
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    return f'"{text}"^^xsd:date' if DATE.fullmatch(text) else f'"{text}"'


def properties(row: dict, skip: set[str]) -> list[str]:
    """ "predicate object" for every filled column; a list becomes one per item, papers become IRIs."""
    out = []
    for column, value in row.items():
        if column in skip or value is None:
            continue
        if column == "papers":
            out += [f"lac:paper {iri(p)}" for p in value]
        elif isinstance(value, list):
            out += [f"{term(column)} {literal(v)}" for v in value]
        else:
            out.append(f"{term(column)} {literal(value)}")
    return out


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<style>
  :root { --bg: #fafaf9; --panel: #ffffff; --text: #1c1917; --muted: #78716c; --line: #e7e5e4; --accent: #2563eb; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #1c1917; --panel: #292524; --text: #f5f5f4; --muted: #a8a29e; --line: #44403c; --accent: #60a5fa; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; font: 14px/1.45 system-ui, sans-serif; background: var(--bg); color: var(--text);
         display: grid; grid-template-columns: 300px 1fr; height: 100vh; }
  aside { background: var(--panel); border-right: 1px solid var(--line); padding: 14px; overflow-y: auto; }
  h1 { font-size: 15px; margin: 0 0 2px; } .sub { color: var(--muted); font-size: 12px; margin-bottom: 12px; }
  h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); margin: 16px 0 6px; }
  label { display: flex; align-items: center; gap: 6px; font-size: 13px; }
  input[type=search] { width: 100%; padding: 6px 8px; border: 1px solid var(--line); border-radius: 6px;
                       background: var(--bg); color: var(--text); }
  input[type=range] { width: 100%; }
  button { padding: 5px 10px; border: 1px solid var(--line); border-radius: 6px; background: var(--bg);
           color: var(--text); cursor: pointer; }
  .swatch { width: 11px; height: 11px; border-radius: 50%; flex: none; }
  #graph { height: 100vh; }
  #details table { border-collapse: collapse; width: 100%; font-size: 12px; }
  #details td { border-top: 1px solid var(--line); padding: 3px 4px; vertical-align: top; word-break: break-word; }
  #details td:first-child { color: var(--muted); white-space: nowrap; }
  #details a { color: var(--accent); }
  .count { color: var(--muted); font-size: 12px; }
  @media (max-width: 700px) { body { grid-template-columns: 1fr; grid-template-rows: auto 70vh; }
                              #graph { height: 70vh; } aside { border-right: 0; border-bottom: 1px solid var(--line); } }
</style>
</head>
<body>
<aside>
  <h1>__TITLE__</h1>
  <div class="sub" id="summary"></div>
  <input type="search" id="search" list="names" placeholder="Find a concept…">
  <datalist id="names"></datalist>
  <h2>Papers per edge, at least <span id="minval">1</span></h2>
  <input type="range" id="min" min="1" value="1">
  <h2>Relations</h2>
  <div id="relations"></div>
  <h2>Node types</h2>
  <div id="types"></div>
  <h2>Selected</h2>
  <div id="details" class="count">Click a node or an edge.</div>
  <h2>Layout</h2>
  <button id="physics">Unfreeze layout</button>
</aside>
<div id="graph"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
const data = JSON.parse(document.getElementById("data").textContent);
const PALETTE = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2", "#ca8a04", "#db2777", "#4b5563",
                 "#65a30d", "#7c3aed", "#0d9488"];
const typeOf = n => (n.types && n.types[0]) || "other";
const types = [...new Set(data.nodes.map(typeOf))].sort();
const relations = [...new Set(data.edges.map(e => e.r))].sort();
const colourOf = (list, x) => PALETTE[list.indexOf(x) % PALETTE.length];
const LINKS = {pubmed: "https://pubmed.ncbi.nlm.nih.gov/", clinicaltrials: "https://clinicaltrials.gov/study/",
               arxiv: "https://arxiv.org/abs/", mesh: "https://meshb.nlm.nih.gov/record/ui?ui=",
               ncbigene: "https://www.ncbi.nlm.nih.gov/gene/"};
const link = id => { const [p, ...rest] = id.split(":"); const local = rest.join(":");
  return LINKS[p] ? `<a href="${LINKS[p]}${encodeURIComponent(local)}" target="_blank" rel="noopener">${id}</a>` : id; };
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const weight = x => x.n_papers ?? x.support ?? 1;
const byId = Object.fromEntries(data.nodes.map(n => [n.id, n]));

const nodes = new vis.DataSet(data.nodes.map(n => ({
  id: n.id, label: n.name || n.id, value: weight(n),
  color: {background: colourOf(types, typeOf(n)), border: n.reason === "seed" ? "#f59e0b" : colourOf(types, typeOf(n))},
  borderWidth: n.reason === "seed" ? 4 : 1, title: `${n.name || n.id} (${typeOf(n)})`})));
const edges = new vis.DataSet(data.edges.map((e, i) => ({
  id: i, from: e.s, to: e.o, value: e.n_papers ?? 1, color: {color: colourOf(relations, e.r), opacity: .55},
  arrows: data.undirected.includes(e.r) ? "" : "to", dashes: e.n_negated > 0 && e.n_negated * 2 >= e.n_papers,
  title: `${byId[e.s]?.name || e.s} ${e.r} ${byId[e.o]?.name || e.o}` +
         (e.n_papers != null ? ` (${e.n_papers} papers, ${e.n_negated} negated)` : ` (${e.source || e.layer})`)})));

const shownRelations = new Set(relations);
let minPapers = 1, visibleNodes = new Set();
const edgeView = new vis.DataView(edges, {filter: x => { const e = data.edges[x.id];
  return shownRelations.has(e.r) && (e.n_papers == null || e.n_papers >= minPapers); }});
const nodeView = new vis.DataView(nodes, {filter: n => visibleNodes.has(n.id)});
function refresh() {
  visibleNodes = new Set(edgeView.get().flatMap(e => [e.from, e.to]));
  edgeView.refresh(); nodeView.refresh();
  document.getElementById("summary").textContent =
    `${nodeView.length.toLocaleString()} of ${nodes.length.toLocaleString()} nodes, ` +
    `${edgeView.length.toLocaleString()} of ${edges.length.toLocaleString()} edges`;
}

const network = new vis.Network(document.getElementById("graph"), {nodes: nodeView, edges: edgeView}, {
  nodes: {shape: "dot", scaling: {min: 6, max: 40, label: {enabled: true, min: 11, max: 22}},
          font: {color: getComputedStyle(document.body).color}},
  edges: {scaling: {min: 1, max: 8}, smooth: false, selectionWidth: 2},
  physics: {solver: "forceAtlas2Based", forceAtlas2Based: {gravitationalConstant: -60, springLength: 120},
            stabilization: {iterations: 400}},
  interaction: {hover: true, tooltipDelay: 120, hideEdgesOnDrag: true}});
let physicsOn = true;
const physicsButton = document.getElementById("physics");
network.on("stabilizationIterationsDone", () => { network.setOptions({physics: false}); physicsOn = false;
                                                  physicsButton.textContent = "Unfreeze layout"; });
physicsButton.onclick = () => { physicsOn = !physicsOn; network.setOptions({physics: physicsOn});
                                physicsButton.textContent = physicsOn ? "Freeze layout" : "Unfreeze layout"; };

function table(row, skip) {
  return "<table>" + Object.entries(row).filter(([k, v]) => !skip.includes(k) && v != null).map(([k, v]) =>
    `<tr><td>${esc(k)}</td><td>${k === "papers" ? v.map(link).join("<br>")
                                 : Array.isArray(v) ? esc(v.join(", ")) : esc(v)}</td></tr>`).join("") + "</table>";
}
function show(params) {
  const box = document.getElementById("details");
  if (params.nodes.length) {
    const n = byId[params.nodes[0]];
    box.innerHTML = `<b>${esc(n.name || n.id)}</b><br>${link(n.id)}` + table(n, ["id", "name"]);
  } else if (params.edges.length) {
    const e = data.edges[params.edges[0]];
    box.innerHTML = `<b>${esc(byId[e.s]?.name || e.s)}</b> ${esc(e.r)} <b>${esc(byId[e.o]?.name || e.o)}</b>` +
                    table(e, ["s", "r", "o"]);
  } else box.innerHTML = "Click a node or an edge.";
}
network.on("click", show);

function checkboxes(id, list, onToggle) {
  document.getElementById(id).innerHTML = list.map(x =>
    `<label><input type="checkbox" checked data-v="${esc(x)}"><span class="swatch" style="background:${colourOf(list, x)}"></span>${esc(x)}</label>`).join("");
  document.getElementById(id).onchange = ev => onToggle(ev.target.dataset.v, ev.target.checked);
}
checkboxes("relations", relations, (r, on) => { on ? shownRelations.add(r) : shownRelations.delete(r); refresh(); });
const hiddenTypes = new Set();
checkboxes("types", types, (t, on) => { on ? hiddenTypes.delete(t) : hiddenTypes.add(t);
  nodes.update(data.nodes.map(n => ({id: n.id, hidden: hiddenTypes.has(typeOf(n))}))); });

const slider = document.getElementById("min");
slider.max = Math.max(1, ...data.edges.map(e => e.n_papers ?? 1));
slider.oninput = () => { minPapers = +slider.value; document.getElementById("minval").textContent = minPapers; refresh(); };
document.getElementById("names").innerHTML = data.nodes.map(n => `<option value="${esc(n.name || n.id)}">`).join("");
document.getElementById("search").onchange = ev => {
  const n = data.nodes.find(n => (n.name || n.id).toLowerCase() === ev.target.value.toLowerCase());
  if (n && visibleNodes.has(n.id)) { network.selectNodes([n.id]); network.focus(n.id, {scale: 1.3, animation: true});
    show({nodes: [n.id], edges: []}); }
};
refresh();
</script>
</body>
</html>
"""


def write_html(out, title: str, nodes: list[dict], edges: list[dict], undirected: list[str]) -> None:
    payload = json.dumps({"nodes": nodes, "edges": edges, "undirected": undirected}, default=str)
    page = PAGE.replace("__TITLE__", html.escape(title)).replace("__DATA__", payload.replace("</", "<\\/"))
    Path(out).write_text(page, encoding="utf-8")


def write_ttl(out: Path, nodes: dict, edges: list[dict], used: set[str], plain: bool) -> None:
    with open(out, "w", encoding="utf-8") as f:
        f.write("".join(f"@prefix {p}: <{u}> .\n" for p, u in PREFIXES.items()) + "\n")
        for i in sorted(used):
            n = nodes[i]
            lines = [f"a {term(t.capitalize())}" for t in n.get("types") or []]
            lines += [f"rdfs:label {literal(n['name'])}"] if n.get("name") else []
            lines += properties(n, {"id", "name", "types"})
            f.write(f"{iri(i)} " + " ;\n    ".join(lines or ["a rdfs:Resource"]) + " .\n")
        f.write("\n")
        for e in edges:
            f.write(f"{iri(e['s'])} {term(e['r'])} {iri(e['o'])} .\n")
            if not plain:
                lines = ["a rdf:Statement", f"rdf:subject {iri(e['s'])}", f"rdf:predicate {term(e['r'])}",
                         f"rdf:object {iri(e['o'])}"] + properties(e, {"s", "r", "o"})
                f.write("[] " + " ;\n    ".join(lines) + " .\n")


def main() -> None:
    args = parse_args()
    nodes = {n["id"]: n for n in pq.read_table(args.graph / "nodes.parquet").to_pylist()}
    edges = pq.read_table(args.graph / "edges.parquet").to_pylist() if (args.graph / "edges.parquet").exists() else []
    kept = [e for e in edges if e["s"] in nodes and e["o"] in nodes
            and (args.relations is None or e["r"] in args.relations)
            and (e.get("n_papers") is None or e["n_papers"] >= args.min_papers)]
    used = {i for e in kept for i in (e["s"], e["o"])}
    out = args.out or args.graph / ("graph.html" if args.html else "graph.ttl")
    if args.html:
        title = " / ".join(args.graph.resolve().parts[-3:])
        write_html(out, title, [nodes[i] for i in sorted(used)], kept, args.undirected)
    else:
        write_ttl(out, nodes, kept, used, args.plain)
    print(f"wrote {out} ({len(used):,} of {len(nodes):,} nodes, {len(kept):,} of {len(edges):,} edges"
          f"{', no statement nodes' if args.plain and not args.html else ''})")


if __name__ == "__main__":
    main()
