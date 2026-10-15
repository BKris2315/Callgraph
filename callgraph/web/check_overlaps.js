// Overlap check for a laid-out call-graph view. Not part of the page: `callgraph check-layout`
// injects it with a headless browser, but it also works pasted into the browser console.
//
//   CGCheck.run(cy, {tolerance})  ->  [{kind, severity, a, b, overlap: {w, h}, box: {x1, y1, x2, y2}}, ...]
//
// Every box is read from what Cytoscape actually draws (model coordinates, label metrics from the
// canvas), so this checks the rendering, not the layout's intent.
//
// Errors (text that cannot be read):
//   label-overflow       a function's text sticks out of its own box
//   node-overlap         two function boxes overlap
//   node-outside-group   a function box sticks out of the group box it belongs to
//   group-overlap        two group boxes overlap without one containing the other
//   title-overflow       a group title is wider than its group box
//   title-overlap        a group title overlaps a function box, another title, or another group's box
//   order-label-overlap  a call number overlaps a function box, a title or another call number
//   order-label-missing  a numbered call is drawn without its number
//   order-label-detached a call number is not on its own edge line (more than 4px away)
// Warnings (legible, but untidy):
//   edge-through-node    an edge line runs across a function box it does not connect
//   edge-through-title   an edge line runs across a group title drawn under the edges (titles that
//                        layout.js draws as opaque tags above the edges hide the line instead)
(function () {
  "use strict";

  const BODY = { includeNodes: true, includeEdges: false, includeLabels: false, includeOverlays: false };
  const LABEL = { includeNodes: false, includeEdges: false, includeLabels: true, includeMainLabels: true,
    includeSourceLabels: false, includeTargetLabels: false, includeOverlays: false };
  const TARGET_LABEL = { includeNodes: false, includeEdges: false, includeLabels: true, includeMainLabels: false,
    includeSourceLabels: false, includeTargetLabels: true, includeOverlays: false };

  const box = (bb) => ({ x1: bb.x1, y1: bb.y1, x2: bb.x2, y2: bb.y2 });
  const valid = (b) => b && isFinite(b.x1) && b.x2 - b.x1 > 0 && b.y2 - b.y1 > 0;

  function overlap(a, b) {
    const w = Math.min(a.x2, b.x2) - Math.max(a.x1, b.x1), h = Math.min(a.y2, b.y2) - Math.max(a.y1, b.y1);
    return w > 0 && h > 0 ? { w, h, box: { x1: Math.max(a.x1, b.x1), y1: Math.max(a.y1, b.y1), x2: Math.min(a.x2, b.x2), y2: Math.min(a.y2, b.y2) } } : null;
  }
  const contains = (outer, inner, tol) => inner.x1 >= outer.x1 - tol && inner.y1 >= outer.y1 - tol && inner.x2 <= outer.x2 + tol && inner.y2 <= outer.y2 + tol;
  const shrink = (b, d) => ({ x1: b.x1 + d, y1: b.y1 + d, x2: b.x2 - d, y2: b.y2 - d });

  // Does segment p->q pass through the inside of box b (Liang–Barsky clipping)?
  function segmentHitsBox(p, q, b) {
    let t0 = 0, t1 = 1;
    const dx = q.x - p.x, dy = q.y - p.y;
    const clip = (pp, qq) => {
      if (pp === 0) return qq >= 0;
      const r = qq / pp;
      if (pp < 0) { if (r > t1) return false; if (r > t0) t0 = r; } else { if (r < t0) return false; if (r < t1) t1 = r; }
      return true;
    };
    return clip(-dx, p.x - b.x1) && clip(dx, b.x2 - p.x) && clip(-dy, p.y - b.y1) && clip(dy, b.y2 - p.y) && t1 - t0 > 1e-6;
  }

  function distToSegment(c, p, q) {
    const dx = q.x - p.x, dy = q.y - p.y, len2 = dx * dx + dy * dy;
    const t = len2 ? Math.max(0, Math.min(1, ((c.x - p.x) * dx + (c.y - p.y) * dy) / len2)) : 0;
    return Math.hypot(c.x - (p.x + t * dx), c.y - (p.y + t * dy));
  }

  function edgePolyline(e) {
    const pts = [e.sourceEndpoint()];
    const seg = e.segmentPoints && e.segmentPoints();
    if (seg) pts.push(...seg);
    else if (e.controlPoints && e.controlPoints()) pts.push(...e.controlPoints()); // bezier: control points approximate the curve
    pts.push(e.targetEndpoint());
    return pts;
  }

  function run(cy, opts) {
    const tol = (opts && opts.tolerance) ?? 1; // px of overlap ignored (antialiasing, rounding)
    const issues = [];
    const add = (kind, severity, a, b, ov) => issues.push({ kind, severity, a, b, overlap: { w: +ov.w.toFixed(1), h: +ov.h.toFixed(1) }, box: ov.box });

    const leaves = cy.nodes(":visible").filter((n) => !n.isParent() && !n.hasClass("olabel") && !n.hasClass("otitle"));
    const groups = cy.nodes(":visible").filter((n) => n.isParent());
    const L = leaves.map((n) => ({ n, id: n.id(), body: box(n.boundingBox(BODY)), label: n.data("label") ? box(n.boundingBox(LABEL)) : null }));
    // A box's title is its tag (node.otitle, drawn above edges) when there is one, else its own label.
    const tags = new Map(cy.nodes(".otitle:visible").map((t) => [t.data("group"), t]));
    const G = groups.map((g) => {
      const tag = tags.get(g.id());
      const title = tag ? box(tag.boundingBox(LABEL)) : g.data("label") && !g.hasClass("titled") ? box(g.boundingBox(LABEL)) : null;
      return { g, id: g.id(), body: box(g.boundingBox(BODY)), title, onTop: !!tag };
    });
    const isAncestor = (anc, node) => node.ancestors().contains(anc);

    // --- function boxes and their text
    for (const a of L) {
      if (a.label && valid(a.label) && !contains(a.body, a.label, tol)) {
        const out = { w: Math.max(a.body.x1 - a.label.x1, a.label.x2 - a.body.x2, 0), h: Math.max(a.body.y1 - a.label.y1, a.label.y2 - a.body.y2, 0), box: a.label };
        add("label-overflow", "error", a.id, a.id, out);
      }
      const p = a.n.parent();
      if (p.nonempty() && p.visible()) {
        const pb = box(p.boundingBox(BODY));
        if (!contains(pb, a.body, tol)) add("node-outside-group", "error", a.id, p.id(), { w: 0, h: 0, box: a.body });
      }
    }
    for (let i = 0; i < L.length; i++) for (let j = i + 1; j < L.length; j++) {
      const ov = overlap(L[i].body, L[j].body);
      if (ov && ov.w > tol && ov.h > tol) add("node-overlap", "error", L[i].id, L[j].id, ov);
    }

    // --- group boxes and titles
    for (let i = 0; i < G.length; i++) for (let j = i + 1; j < G.length; j++) {
      const a = G[i], b = G[j];
      if (isAncestor(a.g, b.g) || isAncestor(b.g, a.g)) continue;
      const ov = overlap(a.body, b.body);
      if (ov && ov.w > tol && ov.h > tol) add("group-overlap", "error", a.id, b.id, ov);
    }
    for (const a of G) {
      if (!a.title || !valid(a.title)) continue;
      if (a.title.x2 - a.title.x1 > a.body.x2 - a.body.x1 + tol) add("title-overflow", "error", a.id, a.id, { w: (a.title.x2 - a.title.x1) - (a.body.x2 - a.body.x1), h: 0, box: a.title });
      for (const l of L) for (const r of [l.body, l.label]) {
        if (!r) continue;
        const ov = overlap(a.title, r);
        if (ov && ov.w > tol && ov.h > tol) { add("title-overlap", "error", a.id, l.id, ov); break; }
      }
      for (const b of G) {
        if (b === a) continue;
        if (b.title && G.indexOf(b) > G.indexOf(a)) {
          const ov = overlap(a.title, b.title);
          if (ov && ov.w > tol && ov.h > tol) add("title-overlap", "error", a.id, b.id, ov);
        }
        if (isAncestor(b.g, a.g)) continue; // a title always lies inside the boxes that contain its group
        const ov = overlap(a.title, b.body);
        if (ov && ov.w > tol && ov.h > tol) add("title-overlap", "error", a.id, b.id, ov);
      }
    }

    // --- call numbers: the label nodes layout.js places, plus arrowhead labels of dragged ("free") edges
    const E = cy.nodes(".olabel:visible").map((n) => ({ id: n.data("edge"), label: box(n.boundingBox(BODY)) }))
      .concat(cy.edges(".free:visible").filter((e) => e.data("order") !== "" && e.data("order") !== undefined)
        .map((e) => ({ id: e.id(), label: box(e.boundingBox(TARGET_LABEL)) })))
      .filter((x) => valid(x.label));
    for (let i = 0; i < E.length; i++) {
      const a = E[i];
      for (const l of L) for (const r of [l.body, l.label]) {
        if (!r) continue;
        const ov = overlap(a.label, r);
        if (ov && ov.w > tol && ov.h > tol) { add("order-label-overlap", "error", a.id, l.id, ov); break; }
      }
      for (const g of G) {
        const ov = g.title && overlap(a.label, g.title);
        if (ov && ov.w > tol && ov.h > tol) add("order-label-overlap", "error", a.id, g.id, ov);
      }
      for (let j = i + 1; j < E.length; j++) {
        const ov = overlap(a.label, E[j].label);
        if (ov && ov.w > tol && ov.h > tol) add("order-label-overlap", "error", a.id, E[j].id, ov);
      }
    }

    // --- every numbered edge shows its number, on its own line
    const numbered = cy.edges(":visible").filter((e) => e.data("order") !== "" && e.data("order") !== undefined);
    const labelOf = new Map(cy.nodes(".olabel:visible").map((n) => [n.data("edge"), n]));
    numbered.forEach((e) => {
      if (e.hasClass("free")) return; // Cytoscape's own arrowhead label
      const n = labelOf.get(e.id());
      if (!n) { add("order-label-missing", "error", e.id(), e.id(), { w: 0, h: 0, box: box(e.boundingBox(BODY)) }); return; }
      const c = n.position(), pts = edgePolyline(e);
      let best = Infinity;
      for (let k = 0; k + 1 < pts.length; k++) best = Math.min(best, distToSegment(c, pts[k], pts[k + 1]));
      if (best > 4) add("order-label-detached", "error", e.id(), e.id(), { w: +best.toFixed(1), h: 0, box: box(n.boundingBox(BODY)) });
    });

    // --- edge lines across text
    cy.edges(":visible").forEach((e) => {
      const pts = edgePolyline(e);
      const ends = new Set([e.source().id(), e.target().id()]);
      const hit = (r) => { for (let k = 0; k + 1 < pts.length; k++) if (segmentHitsBox(pts[k], pts[k + 1], r)) return true; return false; };
      for (const l of L) {
        if (ends.has(l.id)) continue;
        const inner = shrink(l.body, tol);
        if (valid(inner) && hit(inner)) add("edge-through-node", "warning", e.id(), l.id, { w: 0, h: 0, box: l.body });
      }
      for (const g of G) {
        if (!g.title || !valid(g.title) || g.onTop) continue;
        const inner = shrink(g.title, tol);
        if (valid(inner) && hit(inner)) add("edge-through-title", "warning", e.id(), g.id, { w: 0, h: 0, box: g.title });
      }
    });
    return issues;
  }

  window.CGCheck = { run };
})();
