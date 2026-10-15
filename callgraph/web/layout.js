// Graph layout for the call-graph page: ELK "layered" (elkjs) on the visible part of a Cytoscape graph.
//
// Why ELK and not dagre: the views nest functions inside group boxes (one per file or stage), and
// dagre ranks all functions globally and then draws the boxes around wherever their members landed,
// so boxes overlap and everything ends up in one very wide band. ELK lays out the hierarchy itself
// (hierarchyHandling INCLUDE_CHILDREN): every box gets its own region, edges between boxes are
// routed orthogonally around them, the call order of the source is kept left to right
// (considerModelOrder), and disconnected parts are packed to roughly the canvas' aspect ratio.
//
// API (window.CGLayout):
//   available()                          -> is elkjs loaded
//   compute(cy, {direction, aspect})     -> Promise<result>  positions, group boxes, edge routes;
//                                           direction "AUTO" tries DOWN and RIGHT and keeps the better fit
//   apply(cy, result, {animate, fit, padding, onDone})       moves nodes and draws the routes
//   clearRoutes(eles)                    -> plain curves again (e.g. after a node was dragged)
//
// Call numbers: each numbered edge gets an ELK edge label, so ELK reserves room for it where it can
// collide with nothing; apply() draws it as a small node of class "olabel" (data.edge = the edge id)
// at that spot. An edge that loses its route (dragging) gets class "free" and Cytoscape's own
// arrowhead label instead.
//
// Box titles: likewise drawn as opaque nodes of class "otitle" (data.group = the box id) centred in
// the title band ELK reserved, above the edges, while the box carries class "titled" (its own label
// hidden). clearBoxes() (dragging) removes the tag and gives the box its own label back.
//
// Coordinates: ELK returns each shape relative to its parent box and each edge's points relative to
// the box named in its "container" field; collect() turns both into absolute Cytoscape model coordinates.
(function () {
  "use strict";

  const GROUP_PAD = { top: 38, left: 16, bottom: 16, right: 16 }; // top leaves room for the box title
  const LABEL_FONT = '9.5px "IBM Plex Mono", ui-monospace, monospace';
  const LABEL_PAD = { x: 3, y: 1.5 }; // must match the node.olabel style in app.js
  let measureCtx = null;

  function labelSize(text) {
    if (!measureCtx) measureCtx = document.createElement("canvas").getContext("2d");
    measureCtx.font = LABEL_FONT;
    return { width: Math.ceil(measureCtx.measureText(text).width + 2 * LABEL_PAD.x), height: Math.ceil(12 + 2 * LABEL_PAD.y) };
  }
  let elk = null;

  function available() { return typeof window.ELK === "function"; }
  function engine() { if (!elk) elk = new window.ELK(); return elk; }

  // Spacing is read per parent box in a hierarchical layout, so groups get these too.
  const SPACING = {
    "elk.spacing.nodeNode": "22",
    "elk.layered.spacing.nodeNodeBetweenLayers": "44",
    "elk.spacing.edgeNode": "14",
    "elk.spacing.edgeEdge": "9",
    "elk.layered.spacing.edgeNodeBetweenLayers": "16",
    "elk.layered.spacing.edgeEdgeBetweenLayers": "9",
    "elk.spacing.edgeLabel": "2",
    "elk.spacing.labelNode": "4",
  };

  function rootOptions(direction, aspect) {
    return {
      "elk.algorithm": "layered",
      "elk.direction": direction === "RIGHT" ? "RIGHT" : "DOWN",
      "elk.hierarchyHandling": "INCLUDE_CHILDREN",
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
      "elk.layered.crossingMinimization.forceNodeModelOrder": "true", // siblings stay in call order
      "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
      "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
      "elk.layered.nodePlacement.bk.fixedAlignment": "BALANCED",
      "elk.layered.compaction.postCompaction.strategy": "EDGE_LENGTH",
      ...SPACING,
      "elk.spacing.componentComponent": "48",
      "elk.separateConnectedComponents": "true",
      "elk.aspectRatio": String(Math.max(0.6, Math.min(3, aspect || 1.6))),
      "elk.padding": "[top=24,left=24,bottom=24,right=24]",
    };
  }

  // ---------------------------------------------------------------- Cytoscape -> ELK
  function toElk(cy, opts) {
    const visible = cy.nodes().filter((n) => n.visible() && !n.hasClass("olabel") && !n.hasClass("otitle"));
    const byId = {};
    const root = { id: "__root", layoutOptions: rootOptions(opts.direction, opts.aspect), children: [], edges: [] };
    const pad = `[top=${GROUP_PAD.top},left=${GROUP_PAD.left},bottom=${GROUP_PAD.bottom},right=${GROUP_PAD.right}]`;
    // Parents first (in data order), so children can be attached whatever order they come in.
    visible.filter((n) => n.isParent()).forEach((n) => {
      byId[n.id()] = { id: n.id(), children: [], edges: [], layoutOptions: Object.assign({ "elk.padding": pad }, SPACING) };
    });
    visible.forEach((n) => {
      if (n.isParent()) return;
      const d = n.layoutDimensions({ nodeDimensionsIncludeLabels: false });
      byId[n.id()] = { id: n.id(), width: Math.ceil(d.w), height: Math.ceil(d.h) };
    });
    visible.forEach((n) => {
      const p = n.parent();
      const container = p.nonempty() && byId[p.id()] ? byId[p.id()] : root;
      container.children.push(byId[n.id()]);
    });
    // Drop boxes whose members are all hidden, so they do not take up space.
    const prune = (node) => { if (node.children) node.children = node.children.filter((c) => !(c.children && c.children.length === 0 && c.width === undefined)).map((c) => (prune(c), c)); };
    prune(root);
    cy.edges().forEach((e) => {
      if (!e.visible() || !byId[e.source().id()] || !byId[e.target().id()]) return;
      const edge = { id: e.id(), sources: [e.source().id()], targets: [e.target().id()] };
      const order = e.data("order");
      if (order !== "" && order !== undefined && order !== null) {
        edge.labels = [Object.assign({ id: e.id() + "::label", text: String(order),
          layoutOptions: { "elk.edgeLabels.placement": "CENTER", "elk.edgeLabels.inline": "true" } }, labelSize(String(order)))];
      }
      root.edges.push(edge);
    });
    return root;
  }

  // ---------------------------------------------------------------- ELK -> result
  function collect(graph) {
    const positions = {}, boxes = {}, routes = {}, labels = {};
    const origin = { [graph.id]: { x: graph.x || 0, y: graph.y || 0 } }; // absolute top-left of every box
    const edges = [];
    const walk = (node) => {
      const o = origin[node.id];
      for (const c of node.children || []) {
        const x = o.x + c.x, y = o.y + c.y;
        if (c.children && c.children.length) { boxes[c.id] = { x, y, w: c.width, h: c.height }; origin[c.id] = { x, y }; walk(c); }
        else positions[c.id] = { x: x + c.width / 2, y: y + c.height / 2 };
      }
      for (const e of node.edges || []) edges.push(e);
    };
    walk(graph);
    for (const e of edges) {
      const s = e.sections && e.sections[0];
      if (!s) continue;
      const o = origin[e.container] || origin[graph.id];
      const abs = (p) => ({ x: o.x + p.x, y: o.y + p.y });
      routes[e.id] = { start: abs(s.startPoint), end: abs(s.endPoint), bends: (s.bendPoints || []).map(abs) };
      const l = e.labels && e.labels[0];
      if (l && l.x !== undefined) labels[e.id] = { x: o.x + l.x + l.width / 2, y: o.y + l.y + l.height / 2, text: l.text };
    }
    return { positions, boxes, routes, labels, width: graph.width, height: graph.height };
  }

  async function computeOne(cy, opts) {
    const res = collect(await engine().layout(toElk(cy, opts)));
    res.direction = opts.direction;
    return res;
  }

  async function compute(cy, opts) {
    opts = Object.assign({ direction: "DOWN", aspect: 1.6 }, opts || {});
    const t0 = performance.now();
    let res;
    if (opts.direction === "AUTO") {
      // Keep whichever drawing can be shown larger in this canvas; ties go to top-down.
      const [down, right] = await Promise.all(["DOWN", "RIGHT"].map((d) => computeOne(cy, Object.assign({}, opts, { direction: d }))));
      const scale = (r) => Math.min(cy.width() / r.width, cy.height() / r.height);
      res = scale(right) > scale(down) * 1.15 ? right : down;
    } else {
      res = await computeOne(cy, opts);
    }
    res.ms = Math.round(performance.now() - t0);
    return res;
  }

  // ---------------------------------------------------------------- result -> Cytoscape
  // A Cytoscape "segments" edge places its corner points relative to the line between the two node
  // centres (edge-distances: node-position): weight = how far along it, distance = how far off it.
  function segmentStyle(edge, route) {
    const a = edge.source().position(), b = edge.target().position();
    const dx = b.x - a.x, dy = b.y - a.y;
    const len2 = dx * dx + dy * dy;
    const style = {
      "source-endpoint": `${route.start.x - a.x}px ${route.start.y - a.y}px`,
      "target-endpoint": `${route.end.x - b.x}px ${route.end.y - b.y}px`,
    };
    if (!route.bends.length || len2 < 1) return Object.assign(style, { "curve-style": "straight" });
    const len = Math.sqrt(len2);
    const weights = [], distances = [];
    for (const p of route.bends) {
      const px = p.x - a.x, py = p.y - a.y;
      weights.push((px * dx + py * dy) / len2);
      distances.push((py * dx - px * dy) / len); // Cytoscape's positive side is to the left of a→b
    }
    return Object.assign(style, {
      "curve-style": "segments", "edge-distances": "node-position", "segment-radius": 6,
      "segment-weights": weights, "segment-distances": distances,
    });
  }

  const ROUTE_PROPS = ["curve-style", "edge-distances", "segment-weights", "segment-distances", "segment-radius", "source-endpoint", "target-endpoint"];
  const BOX_PROPS = ["min-width", "min-height", "min-width-bias-left", "min-width-bias-right", "min-height-bias-top", "min-height-bias-bottom"];

  function clearRoutes(eles) {
    const edges = eles.edges();
    edges.removeStyle(ROUTE_PROPS.join(" ")).removeClass("routed").addClass("free");
    const ids = new Set(edges.map((e) => e.id()));
    eles.cy().nodes(".olabel").filter((n) => ids.has(n.data("edge"))).remove();
  }

  // Call numbers at the spots ELK reserved for them.
  function drawLabels(cy, res) {
    cy.remove(".olabel");
    const add = [];
    cy.edges(":visible").forEach((e) => {
      const l = res.labels[e.id()];
      if (l) add.push({ group: "nodes", data: { id: "olabel:" + e.id(), label: l.text, edge: e.id() }, position: { x: l.x, y: l.y }, classes: "olabel" });
    });
    cy.add(add);
    cy.edges().removeClass("free");
  }
  function clearBoxes(eles) {
    const boxes = eles.nodes().filter((n) => n.isParent() || n.hasClass("group"));
    eles.nodes().removeStyle(BOX_PROPS.join(" "));
    const ids = new Set(boxes.map((n) => n.id()));
    boxes.removeClass("titled");
    eles.cy().nodes(".otitle").filter((t) => ids.has(t.data("group"))).remove();
  }

  // Box titles centred in the band ELK reserved at the top of each box, drawn above the edges.
  function drawTitles(cy, res) {
    cy.remove(".otitle");
    const add = [];
    for (const [id, b] of Object.entries(res.boxes)) {
      const g = cy.getElementById(id);
      if (g.empty() || !g.visible() || !g.data("label")) continue;
      add.push({ group: "nodes", data: { id: "otitle:" + id, label: g.data("label"), group: id, stage: g.data("stage"), maxw: Math.max(40, b.w - 28) },
        position: { x: b.x + b.w / 2, y: b.y + GROUP_PAD.top / 2 + 1 }, classes: "otitle" });
      g.addClass("titled");
    }
    cy.add(add);
  }

  function drawRoutes(cy, res) {
    cy.batch(() => {
      cy.edges().forEach((e) => {
        const r = res.routes[e.id()];
        if (r && e.visible()) e.style(segmentStyle(e, r)).addClass("routed");
      });
    });
  }

  // Make each Cytoscape box exactly the region ELK reserved for it (including the title band on top),
  // instead of hugging its children with uniform padding. Cytoscape's min-width/min-height size the
  // box's inner area, with its padding and border added on top, so measure that extra once per box and
  // subtract it; then split the slack around the children so the box edges land on ELK's.
  function sizeBoxes(cy, res) {
    const entries = Object.entries(res.boxes).map(([id, box]) => [cy.getElementById(id), box]).filter(([n]) => n.nonempty() && n.children().nonempty());
    const BB = { includeLabels: false, includeOverlays: false };
    cy.batch(() => entries.forEach(([n, box]) => n.style({ "min-width": box.w, "min-height": box.h })));
    const extra = new Map(entries.map(([n, box]) => { const bb = n.boundingBox(BB); return [n.id(), { w: bb.w - box.w, h: bb.h - box.h }]; }));
    cy.batch(() => entries.forEach(([n, box]) => {
      const ex = extra.get(n.id()), kids = n.children().boundingBox(BB);
      const left = Math.max(0, kids.x1 - ex.w / 2 - box.x), right = Math.max(0, box.x + box.w - ex.w / 2 - kids.x2);
      const top = Math.max(0, kids.y1 - ex.h / 2 - box.y), bottom = Math.max(0, box.y + box.h - ex.h / 2 - kids.y2);
      const pct = (a, b) => (a + b > 0.5 ? (100 * a) / (a + b) : 50);
      n.style({
        "min-width": Math.max(0, box.w - ex.w), "min-height": Math.max(0, box.h - ex.h),
        "min-width-bias-left": `${pct(left, right)}%`, "min-width-bias-right": `${100 - pct(left, right)}%`,
        "min-height-bias-top": `${pct(top, bottom)}%`, "min-height-bias-bottom": `${100 - pct(top, bottom)}%`,
      });
    }));
  }

  function apply(cy, res, opts) {
    opts = opts || {};
    cy.remove(".olabel, .otitle");
    clearRoutes(cy.elements());
    clearBoxes(cy.elements());
    const finish = () => {
      sizeBoxes(cy, res);
      drawRoutes(cy, res);
      drawLabels(cy, res);
      drawTitles(cy, res);
      if (opts.onDone) opts.onDone();
    };
    const leaves = cy.nodes().filter((n) => n.visible() && !n.isParent() && res.positions[n.id()]);
    if (opts.animate) {
      const fitTo = opts.fit ? fitViewport(cy, res, opts.padding || 30) : null;
      const anims = leaves.map((n) => n.animation({ position: res.positions[n.id()], duration: 380, easing: "ease-in-out-cubic" }));
      if (fitTo) anims.push(cy.animation({ zoom: fitTo.zoom, pan: fitTo.pan, duration: 380, easing: "ease-in-out-cubic" }));
      Promise.all(anims.map((a) => a.play().promise())).then(finish);
    } else {
      cy.batch(() => leaves.forEach((n) => n.position(res.positions[n.id()])));
      if (opts.fit) { const f = fitViewport(cy, res, opts.padding || 30); cy.viewport(f); }
      finish();
    }
  }

  const READABLE_ZOOM = 0.75; // below this, 11px labels are too small to read
  const START_ZOOM = 0.9; // zoom used instead of fitting when fitting would go below READABLE_ZOOM

  // Zoom/pan that fits the laid-out drawing, computed from the result (nodes may still be moving).
  // When fitting would make the labels unreadable, show the start of the graph (the first node in
  // data order, i.e. the entry point) at a readable zoom instead; the rest is a pan or zoom away.
  function fitViewport(cy, res, padding) {
    const w = cy.width(), h = cy.height();
    let x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;
    const grow = (a, b, c, d) => { x1 = Math.min(x1, a); y1 = Math.min(y1, b); x2 = Math.max(x2, c); y2 = Math.max(y2, d); };
    for (const b of Object.values(res.boxes)) grow(b.x, b.y, b.x + b.w, b.y + b.h);
    cy.nodes().forEach((n) => {
      const p = res.positions[n.id()];
      if (!p || !n.visible()) return;
      const d = n.layoutDimensions({ nodeDimensionsIncludeLabels: false });
      grow(p.x - d.w / 2, p.y - d.h / 2, p.x + d.w / 2, p.y + d.h / 2);
    });
    if (!isFinite(x1)) return { zoom: cy.zoom(), pan: cy.pan() };
    const zoom = Math.max(cy.minZoom(), Math.min(1.25, (w - 2 * padding) / (x2 - x1), (h - 2 * padding) / (y2 - y1)));
    if (zoom >= READABLE_ZOOM) return { zoom, pan: { x: (w - zoom * (x1 + x2)) / 2, y: (h - zoom * (y1 + y2)) / 2 } };
    const z = START_ZOOM;
    const first = cy.nodes().filter((n) => n.visible() && !n.isParent() && res.positions[n.id()])[0];
    const p = first ? res.positions[first.id()] : { x: (x1 + x2) / 2, y: (y1 + y2) / 2 };
    // Entry point near the top-left corner for left-to-right drawings, top-centre for top-down ones;
    // but never scroll past the drawing's own edges.
    const right = res.direction === "RIGHT";
    let panX = right ? padding - z * x1 : w / 2 - z * p.x;
    let panY = right ? h / 3 - z * p.y : padding - z * y1;
    panX = Math.min(padding - z * x1, Math.max(w - padding - z * x2, panX));
    panY = Math.min(padding - z * y1, Math.max(h - padding - z * y2, panY));
    return { zoom: z, pan: { x: panX, y: panY } };
  }

  window.CGLayout = { available, compute, apply, clearRoutes, clearBoxes, fitViewport, LABEL_PAD };
})();
