// Call-graph page renderer. Reads window.CALLGRAPH (schema: callgraph/views/builder.py) and
// draws one view at a time with Cytoscape, laid out by layout.js (ELK): rail of views on the left,
// zoomable graph in the middle, inspector on the right. Collapsing a group, switching direction and
// focusing on a function re-run the layout and animate from the old drawing to the new one.
// Per-viewer settings (current view, collapsed groups, toggles) are kept in localStorage under "cg:*"
// and are optional.
(function () {
  "use strict";
  const V = window.CALLGRAPH;

  const $ = (id) => document.getElementById(id);
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  // Cytoscape does not take rgba() colours: split them into an rgb() colour and an opacity.
  const cssRgba = (name) => {
    const v = css(name), m = v.match(/^rgba?\(([^)]+)\)$/);
    if (!m) return { color: v, alpha: 1 };
    const p = m[1].split(/[\s,\/]+/).filter(Boolean);
    return { color: `rgb(${p[0]}, ${p[1]}, ${p[2]})`, alpha: p[3] !== undefined ? parseFloat(p[3]) : 1 };
  };
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  const store = {
    get(k, d) { try { const v = localStorage.getItem("cg:" + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem("cg:" + k, JSON.stringify(v)); } catch (e) { /* storage unavailable */ } },
  };

  const STAGE_VAR = V.stageColors;
  let current = null;
  let collapsed = new Set();
  let cy = null;
  let selectedId = null;
  let focusId = null; // function the graph is currently narrowed to (focus mode)
  let layoutToken = 0; // bumps on every layout request; stale results are dropped
  let detachMinimap = null;
  const FOCUS_UP = 1, FOCUS_DOWN = 2; // focus mode keeps callers 1 level up and callees 2 levels down

  // ---------- rail ----------
  function buildRail() {
    const rail = $("rail");
    rail.innerHTML = "";
    for (const sec of V.sections) {
      const s = document.createElement("section");
      s.innerHTML = `<h2>${esc(sec.title)}</h2><div class="list"></div>`;
      const list = s.querySelector(".list");
      for (const vid of sec.views) {
        const v = V.views[vid];
        const b = document.createElement("button");
        b.dataset.view = vid;
        b.innerHTML = `<i class="sw" style="background:${css(v.color || "--accent")}"></i><span><span class="t">${esc(v.title)}</span><span class="s">${esc(v.tag || "")}</span></span>`;
        b.addEventListener("click", () => show(vid));
        list.appendChild(b);
      }
      rail.appendChild(s);
    }
  }

  // ---------- elements ----------
  // Nodes in call order: depth first from the entry points, following each caller's calls in the
  // order they are made. The layout keeps this order, so siblings read 1, 2, 3 … across the page.
  function callOrder(view) {
    const out = new Map(), indeg = {};
    view.nodes.forEach((n) => { out.set(n.id, []); indeg[n.id] = 0; });
    view.edges.forEach((e) => { if (out.has(e[0]) && out.has(e[1])) { out.get(e[0]).push(e); indeg[e[1]] += 1; } });
    out.forEach((lst) => lst.sort((a, b) => (Number(a[2]) || 1e9) - (Number(b[2]) || 1e9)));
    const seen = new Set(), order = [];
    const visit = (id) => {
      const stack = [id];
      while (stack.length) {
        const cur = stack.pop();
        if (seen.has(cur)) continue;
        seen.add(cur); order.push(cur);
        const next = out.get(cur).map((e) => e[1]).filter((t) => !seen.has(t));
        for (let i = next.length - 1; i >= 0; i--) stack.push(next[i]);
      }
    };
    view.nodes.filter((n) => indeg[n.id] === 0).forEach((n) => visit(n.id));
    view.nodes.forEach((n) => visit(n.id)); // cycles without an entry point
    const rank = Object.fromEntries(order.map((id, i) => [id, i]));
    return view.nodes.slice().sort((a, b) => rank[a.id] - rank[b.id]);
  }

  function elementsFor(view) {
    let groups = view.groups || [];
    const gById = Object.fromEntries(groups.map((g) => [g.id, g]));
    const els = [];
    const rep = {}; // node id -> id rendered (itself, or its collapsed group)
    const count = {};
    for (const n of view.nodes) if (n.group) count[n.group] = (count[n.group] || 0) + 1;
    // A file box around a single function is mostly padding: draw the function alone, file name under it.
    const solo = new Set(groups.filter((g) => g.kind === "file" && count[g.id] === 1 && !collapsed.has(g.id)).map((g) => g.id));
    const nodes = callOrder(view);
    const firstSeen = {};
    nodes.forEach((n, i) => { if (n.group && !(n.group in firstSeen)) firstSeen[n.group] = i; });
    groups = groups.slice().sort((a, b) => (firstSeen[a.id] ?? 1e9) - (firstSeen[b.id] ?? 1e9));
    for (const g of groups) {
      if (solo.has(g.id)) continue;
      if (collapsed.has(g.id)) {
        const count = view.nodes.filter((n) => n.group === g.id).length;
        els.push({ data: { id: g.id, label: `${g.label}\n(${count} functions, collapsed)`, kind: "collapsed", stage: g.stage || g.id }, classes: "collapsed" });
      } else {
        // File boxes are titled with the last two path parts, which fit; the full path is in the inspector.
        const title = g.kind === "file" ? g.label.split("/").slice(-2).join("/") : g.label;
        els.push({ data: { id: g.id, label: title, kind: "group", stage: g.stage || g.id }, classes: "group" });
      }
    }
    for (const n of nodes) {
      const g = n.group && gById[n.group];
      if (g && collapsed.has(g.id)) { rep[n.id] = g.id; continue; }
      rep[n.id] = n.id;
      const stage = n.stage || (g && (g.stage || g.id)) || "config";
      const alone = g && solo.has(g.id);
      els.push({
        data: { id: n.id, parent: g && !alone ? g.id : undefined, label: alone && n.label !== g.label.split("/").pop() ? `${n.label}\n${g.label.split("/").pop()}` : n.label, kind: "fn", stage, goto: n.goto || "" },
        classes: ["fn", n.goto ? "goto" : "", n.ext ? "ext" : "", alone ? "solo" : ""].join(" "),
      });
    }
    const seen = new Map();
    view.edges.forEach((e, i) => {
      const [s, t, order, data, cond] = e;
      const rs = rep[s], rt = rep[t];
      if (!rs || !rt) { console.warn("edge with unknown node", view.id, e); return; }
      if (rs === rt) return;
      const key = rs + ">" + rt;
      if (seen.has(key)) { const ed = seen.get(key); ed.data.count += 1; ed.data.full += "\n" + (data || ""); return; }
      const ed = { data: { id: "e" + i, source: rs, target: rt, order: order ?? "", dlabel: data || "", full: data || "", cond: cond || "", count: 1, orig: i }, classes: cond ? "cond" : "" };
      seen.set(key, ed);
      els.push(ed);
    });
    return els;
  }

  function stageColor(stage) { return css(STAGE_VAR[stage] || "--ink-3"); }

  function style() {
    const ink = css("--ink"), ink2 = css("--ink-2"), ink3 = css("--ink-3"), nodeBg = css("--node-bg"), focus = css("--focus"), paper = css("--paper");
    const groupBg = cssRgba("--group-bg");
    const display = "IBM Plex Sans Condensed, system-ui, sans-serif";
    return [
      { selector: "node.fn", style: {
        shape: "round-rectangle", "background-color": nodeBg, "border-width": 1.5, "border-color": (el) => stageColor(el.data("stage")),
        label: "data(label)", "font-family": "IBM Plex Mono, ui-monospace, monospace", "font-size": 11, color: ink,
        "text-wrap": "wrap", "text-max-width": 190, "text-valign": "center", "text-halign": "center",
        width: "label", height: "label", padding: "9px",
      } },
      { selector: "node.goto", style: { "border-width": 2.5, "font-weight": 600, "font-family": display, "font-size": 13, padding: "13px", "text-max-width": 210 } },
      { selector: "node.ext", style: { "border-style": "dashed" } },
      { selector: "node.solo", style: { "line-height": 1.35 } },
      { selector: "node.group", style: {
        shape: "round-rectangle", "corner-radius": 10, "background-color": groupBg.color, "background-opacity": groupBg.alpha,
        "border-width": 1, "border-style": "solid", "border-color": (el) => stageColor(el.data("stage")), "border-opacity": 0.5,
        label: "data(label)", "text-valign": "top", "text-halign": "center", "text-margin-y": 26,
        "text-wrap": "ellipsis", "text-max-width": (el) => Math.max(60, el.width() - 24),
        "font-family": display, "font-weight": 600, "font-size": 12.5, color: (el) => stageColor(el.data("stage")),
        "text-transform": "uppercase", padding: "16px",
      } },
      // After a layout the title is drawn by layout.js as an opaque tag above the edges (node.otitle),
      // so lines entering the box pass behind it; the box's own label is only a fallback (dragging).
      { selector: "node.group.titled", style: { label: "" } },
      { selector: "node.otitle", style: {
        shape: "round-rectangle", "background-color": paper, "background-opacity": 0.94, "border-width": 0,
        label: "data(label)", "text-valign": "center", "text-halign": "center", "text-wrap": "ellipsis", "text-max-width": "data(maxw)",
        "font-family": display, "font-weight": 600, "font-size": 12.5, color: (el) => stageColor(el.data("stage")), "text-transform": "uppercase",
        width: "label", height: "label", "padding-left": 6, "padding-right": 6, "padding-top": 2, "padding-bottom": 2, padding: 0,
        events: "no", "z-compound-depth": "top", "z-index": 15,
      } },
      { selector: "node.collapsed", style: {
        shape: "round-rectangle", "background-color": groupBg.color, "background-opacity": Math.min(1, groupBg.alpha * 3),
        "border-width": 1.5, "border-style": "dashed",
        "border-color": (el) => stageColor(el.data("stage")), label: "data(label)", "text-wrap": "wrap",
        "font-family": display, "font-weight": 600, "font-size": 13,
        color: (el) => stageColor(el.data("stage")), width: "label", height: "label", padding: "14px", "text-valign": "center",
      } },
      { selector: "edge", style: {
        width: 1.2, "line-color": ink3, "target-arrow-color": ink3, "target-arrow-shape": "triangle", "arrow-scale": 0.8,
        "curve-style": "bezier", "font-family": "IBM Plex Mono, monospace", color: ink2,
        "font-size": 9.5,
        "text-background-color": paper, "text-background-opacity": 1, "text-background-padding": 1.5, "text-background-shape": "roundrectangle",
      } },
      { selector: "edge.cond", style: { "line-style": "dashed", "line-dash-pattern": [5, 4] } },
      // Call numbers: drawn by layout.js as small nodes where ELK reserved room; a dragged ("free")
      // edge falls back to a label by its arrowhead.
      { selector: "node.olabel", style: {
        shape: "round-rectangle", "background-color": paper, "background-opacity": 1, "border-width": 0,
        label: "data(label)", "font-family": "IBM Plex Mono, ui-monospace, monospace", "font-size": 9.5, color: ink2,
        "text-valign": "center", "text-halign": "center", width: "label", height: "label",
        "padding-left": 3, "padding-right": 3, "padding-top": 1.5, "padding-bottom": 1.5, padding: 0,
        events: "no", "z-compound-depth": "top", "z-index": 20,
      } },
      { selector: "edge.free", style: { "target-label": "data(order)", "target-text-offset": 15 } },
      { selector: "edge.showdata", style: { label: "data(dlabel)", "text-wrap": "wrap", "text-max-width": 220, "font-size": 9 } },
      { selector: ".hidden", style: { display: "none" } },
      { selector: ".dim", style: { opacity: 0.16 } },
      { selector: "node.hl", style: { "border-width": 3, "border-color": focus } },
      { selector: "node.focus-root", style: { "border-width": 3, "border-color": focus, "background-color": css("--accent-soft") } },
      { selector: "node.olabel.hl-label", style: { color: ink, "font-weight": 600 } },
      { selector: "edge.hl-out", style: { width: 2.2, "line-color": focus, "target-arrow-color": focus, color: ink, "z-index": 10 } },
      { selector: "edge.hl-in", style: { width: 2, "line-color": css("--accent"), "target-arrow-color": css("--accent"), color: ink, "z-index": 10 } },
    ];
  }

  // ---------- layout ----------
  // Positions of the current drawing, so the next one can animate from it.
  function snapshot() {
    const pos = {};
    if (cy && !cy.destroyed()) cy.nodes().forEach((n) => { if (!n.isParent()) pos[n.id()] = Object.assign({}, n.position()); });
    return pos;
  }

  // Start every node where it (or what stood for it) was drawn before: a member of a group that was
  // just expanded starts on the collapsed box, a freshly collapsed box starts on its members' centre.
  function seed(prev) {
    const view = V.views[current];
    const members = {};
    for (const n of view.nodes) if (n.group) (members[n.group] = members[n.group] || []).push(n.id);
    cy.nodes().forEach((n) => {
      if (n.isParent()) return;
      let p = prev[n.id()];
      if (!p && n.hasClass("collapsed")) {
        const pts = (members[n.id()] || []).map((id) => prev[id]).filter(Boolean);
        if (pts.length) p = { x: pts.reduce((a, q) => a + q.x, 0) / pts.length, y: pts.reduce((a, q) => a + q.y, 0) / pts.length };
      }
      if (!p) { const g = n.data("parent"); if (g) p = prev[g]; }
      if (p) n.position(p);
    });
  }

  async function relayout(animate) {
    const token = ++layoutToken, c = cy;
    $("cy").dataset.layout = "running";
    const done = () => {
      if (c.destroyed()) return;
      $("cy").classList.remove("pending");
      if (token === layoutToken) $("cy").dataset.layout = "done"; // tools (e.g. `callgraph check-layout`) wait on this
    };
    if (!window.CGLayout || !CGLayout.available()) {
      c.layout({ name: "breadthfirst", directed: true, animate, fit: true, padding: 30, spacingFactor: 0.9 }).run();
      return done();
    }
    try {
      const res = await CGLayout.compute(c, { direction: $("t-dir").value, aspect: c.width() / Math.max(1, c.height()) });
      if (token !== layoutToken || c.destroyed()) return;
      window.__layoutMs = res.ms;
      $("cy").dataset.direction = res.direction; // what "auto" resolved to
      await new Promise((resolve) => CGLayout.apply(c, res, { animate, fit: true, padding: 30, onDone: resolve }));
    } catch (e) {
      console.error("ELK layout failed, falling back", e);
      if (!c.destroyed()) c.layout({ name: "breadthfirst", directed: true, fit: true, padding: 30 }).run();
    }
    done();
  }

  // Focus mode: keep the function, its callers FOCUS_UP levels up and its callees FOCUS_DOWN levels down.
  function applyFocus(id) {
    cy.elements().removeClass("hidden focus-root");
    focusId = null;
    const root = id && cy.getElementById(id);
    if (!root || root.empty() || !$("t-focus").checked) return false;
    let keep = root, frontier = root;
    for (let i = 0; i < FOCUS_DOWN; i++) { frontier = frontier.outgoers("node"); keep = keep.union(frontier); }
    frontier = root;
    for (let i = 0; i < FOCUS_UP; i++) { frontier = frontier.incomers("node"); keep = keep.union(frontier); }
    keep = keep.union(keep.ancestors());
    cy.nodes().difference(keep).addClass("hidden");
    root.addClass("focus-root");
    focusId = id;
    return true;
  }

  // Rebuild the graph for the current view. With `morph`, nodes start where they were drawn and
  // animate to the new layout (collapse/expand, direction change); otherwise the view fades in.
  async function render({ keepSelection = false, morph = false } = {}) {
    const view = V.views[current];
    const prev = morph ? snapshot() : null;
    if (detachMinimap) { detachMinimap(); detachMinimap = null; }
    if (cy) cy.destroy();
    if (!prev) $("cy").classList.add("pending");
    cy = cytoscape({
      container: $("cy"), elements: elementsFor(view), style: style(), layout: { name: "preset" },
      minZoom: 0.05, maxZoom: 3, boxSelectionEnabled: false,
    });
    if (prev) seed(prev);
    applyDataToggle();
    wireGraph();
    if (window.CGMinimap) detachMinimap = CGMinimap.attach(cy, $("minimap"), (n) => stageColor(n.data("stage")));
    const keep = keepSelection && selectedId && cy.getElementById(selectedId).nonempty();
    if (keep && focusId) applyFocus(selectedId);
    await relayout(!!prev);
    if (keep) select(selectedId, { silent: true, relayout: false });
    else if (!keepSelection) { selectedId = null; showViewInfo(); }
  }

  function wireGraph() {
    cy.on("tap", "node.fn", (ev) => select(ev.target.id()));
    cy.on("tap", "node.group", (ev) => { const g = ev.target; if (ev.originalEvent && ev.originalEvent.detail > 1) return; cy.animate({ fit: { eles: g, padding: 40 }, duration: 260 }); showGroup(g.id()); });
    cy.on("dbltap", "node.group", (ev) => toggleGroup(ev.target.id()));
    cy.on("tap", "node.collapsed", (ev) => toggleGroup(ev.target.id()));
    cy.on("dbltap", "node.goto", (ev) => show(ev.target.data("goto")));
    cy.on("tap", (ev) => { if (ev.target === cy) clearSel(); });
    // A dragged node no longer matches the computed routes: fall back to plain curves around it.
    cy.on("grab", "node", (ev) => {
      const n = ev.target, moved = n.union(n.descendants());
      CGLayout.clearRoutes(moved.connectedEdges());
      CGLayout.clearBoxes(n.ancestors().union(n));
    });
    const tip = $("tip");
    cy.on("mouseover", "edge", (ev) => {
      const d = ev.target.data();
      tip.textContent = `${d.order !== "" ? "#" + d.order + "  " : ""}${d.full}${d.cond ? "\nwhen: " + d.cond : ""}${d.count > 1 ? `\n(${d.count} calls merged)` : ""}`;
      tip.style.whiteSpace = "pre-wrap";
      tip.hidden = false;
      const p = ev.renderedPosition; tip.style.left = Math.min(p.x + 14, $("cy").clientWidth - 370) + "px"; tip.style.top = (p.y + 60) + "px";
    });
    cy.on("mouseout", "edge", () => { tip.hidden = true; });
    cy.on("mouseover", "node.fn", () => { document.body.style.cursor = "pointer"; });
    cy.on("mouseout", "node", () => { document.body.style.cursor = ""; });
  }

  function applyDataToggle() {
    if (!cy) return;
    const on = $("t-data").checked;
    cy.edges().toggleClass("showdata", on);
  }

  function clearSel() {
    selectedId = null;
    cy.elements().removeClass("dim hl hl-out hl-in hl-label");
    if (focusId) { applyFocus(null); relayout(true); }
    showViewInfo();
  }

  function highlight(node) {
    cy.batch(() => {
      cy.elements().removeClass("dim hl hl-out hl-in hl-label").addClass("dim");
      const outE = node.outgoers("edge"), inE = node.incomers("edge");
      node.removeClass("dim").addClass("hl");
      node.ancestors().removeClass("dim");
      outE.removeClass("dim").addClass("hl-out"); inE.removeClass("dim").addClass("hl-in");
      outE.targets().removeClass("dim").ancestors().removeClass("dim");
      inE.sources().removeClass("dim").ancestors().removeClass("dim");
      const lit = new Set(outE.union(inE).map((e) => e.id()));
      cy.nodes(".olabel").filter((n) => lit.has(n.data("edge"))).removeClass("dim").addClass("hl-label");
      cy.nodes(".otitle").filter((t) => !cy.getElementById(t.data("group")).hasClass("dim")).removeClass("dim");
    });
  }

  // Select a function: highlight its calls, show it in the inspector and, in focus mode, narrow the
  // graph to its neighbourhood and lay that out.
  async function select(id, { silent = false, relayout: doLayout = true } = {}) {
    let node = cy.getElementById(id);
    if (node.empty()) return;
    selectedId = id;
    showNode(id);
    if (doLayout && $("t-focus").checked && focusId !== id) {
      applyFocus(id);
      await relayout(true);
      node = cy.getElementById(id);
      if (node.empty() || selectedId !== id) return;
    }
    highlight(node);
    if (!silent && !focusId) cy.animate({ center: { eles: node }, zoom: Math.max(cy.zoom(), 0.9), duration: 260 });
  }

  // ---------- inspector ----------
  function nodeById(view, id) { return view.nodes.find((n) => n.id === id); }
  function groupOf(view, n) { return (view.groups || []).find((g) => g.id === n.group); }

  function showNode(id) {
    const view = V.views[current];
    const n = nodeById(view, id);
    if (!n) return;
    const g = groupOf(view, n);
    const stage = n.stage || (g && (g.stage || g.id));
    const outs = view.edges.map((e, i) => [e, i]).filter(([e]) => e[0] === id).sort((a, b) => (Number(a[0][2]) || 999) - (Number(b[0][2]) || 999));
    const ins = view.edges.filter((e) => e[1] === id);
    const li = (otherId, e, dir) => {
      const o = nodeById(view, otherId);
      return `<li><button data-sel="${esc(otherId)}"><span class="n">${dir === "out" && e[2] !== undefined && e[2] !== null && e[2] !== "" ? esc(e[2]) : dir === "in" ? "←" : "→"}</span><span class="nm">${esc(o ? o.label.replace(/\n/g, " ") : otherId)}</span><span class="d">${esc(e[3] || "")}${e[4] ? ` <span class="cond">when ${esc(e[4])}</span>` : ""}</span></button></li>`;
    };
    const loc = n.loc ? `<div class="loc"><span>${esc(n.loc)}</span><button id="copyloc" type="button">Copy</button></div>` : "";
    $("inspect").innerHTML = `
      <div class="kicker" style="color:${stageColor(stage)}">${esc(g ? g.label : "")}</div>
      <h3 class="fn">${esc(n.label.replace(/\n/g, " "))}</h3>
      ${n.q ? `<div class="qual">${esc(n.q)}</div>` : ""}
      ${loc}
      <p class="desc">${esc(n.desc || "")}</p>
      ${n.note ? `<div class="note">${esc(n.note)}</div>` : ""}
      ${n.goto ? `<button class="goto" data-goto="${esc(n.goto)}">Open the ${esc(V.views[n.goto].title)} graph</button>` : ""}
      ${outs.length ? `<h4>Calls, in order (${outs.length})</h4><ul class="calls">${outs.map(([e]) => li(e[1], e, "out")).join("")}</ul>` : ""}
      ${ins.length ? `<h4>Called by (${ins.length})</h4><ul class="calls">${ins.map((e) => li(e[0], e, "in")).join("")}</ul>` : ""}
    `;
    wireInspector(n);
  }

  function wireInspector(n) {
    const ins = $("inspect");
    ins.querySelectorAll("[data-sel]").forEach((b) => b.addEventListener("click", () => {
      const id = b.dataset.sel;
      const view = V.views[current];
      const tn = nodeById(view, id);
      if (tn && tn.group && collapsed.has(tn.group)) { collapsed.delete(tn.group); saveCollapsed(); buildStagebar(); render({ keepSelection: true, morph: true }).then(() => select(id)); return; }
      select(id);
    }));
    ins.querySelectorAll("[data-goto]").forEach((b) => b.addEventListener("click", () => show(b.dataset.goto)));
    const c = $("copyloc");
    if (c && n) c.addEventListener("click", () => {
      const done = () => { c.textContent = "Copied"; setTimeout(() => (c.textContent = "Copy"), 1200); };
      try { navigator.clipboard.writeText(n.loc).then(done, () => selectText(c.previousElementSibling)); } catch (e) { selectText(c.previousElementSibling); }
    });
  }
  function selectText(el) { const r = document.createRange(); r.selectNodeContents(el); const s = getSelection(); s.removeAllRanges(); s.addRange(r); }

  function showGroup(gid) {
    const view = V.views[current];
    const g = (view.groups || []).find((x) => x.id === gid);
    if (!g) return;
    const members = view.nodes.filter((n) => n.group === gid);
    $("inspect").innerHTML = `
      <div class="kicker" style="color:${stageColor(g.stage || g.id)}">Group</div>
      <h3 class="fn" style="font-family:var(--f-display)">${esc(g.label)}</h3>
      <p class="desc">${esc(g.desc || "")}</p>
      <p>Double-click the box, or its chip above the graph, to collapse it into one node.</p>
      <h4>Functions (${members.length})</h4>
      <ul class="calls">${members.map((m) => `<li><button data-sel="${esc(m.id)}"><span class="n">·</span><span class="nm">${esc(m.label.replace(/\n/g, " "))}</span><span class="d">${esc(m.loc || "")}</span></button></li>`).join("")}</ul>`;
    wireInspector(null);
  }

  function showViewInfo() {
    const view = V.views[current];
    $("inspect").innerHTML = `
      <div class="empty">
        <div class="kicker" style="color:${css(view.color || "--accent")}">${esc(view.tag || "")}</div>
        <h3>${esc(view.title)}</h3>
        ${(view.intro || []).map((p, i) => `<p>${esc(p)}</p>${i === 0 && view.intro_by ? `<div class="note">Written by the language model ${esc(view.intro_by)}, not by a person.</div>` : ""}`).join("")}
        ${(view.notes || []).map((p) => `<div class="note">${esc(p)}</div>`).join("")}
        <h4>How to read it</h4>
        <div class="legend">
          <div><svg width="44" height="12"><line x1="2" y1="6" x2="36" y2="6" stroke="${css("--ink-3")}" stroke-width="1.5"/><path d="M36 2l7 4-7 4z" fill="${css("--ink-3")}"/></svg>Calls. The number by the arrowhead is the call's position inside the caller.</div>
          <div><svg width="44" height="12"><line x1="2" y1="6" x2="36" y2="6" stroke="${css("--ink-3")}" stroke-width="1.5" stroke-dasharray="5 4"/><path d="M36 2l7 4-7 4z" fill="${css("--ink-3")}"/></svg>Conditional call (inside an if, else or except).</div>
          <div><svg width="44" height="14"><rect x="2" y="1" width="40" height="12" rx="3" fill="${css("--node-bg")}" stroke="${css("--c-red")}" stroke-width="1.5" stroke-dasharray="4 3"/></svg>Dashed box: lives outside the files this view walks (another package or view).</div>
          <div><svg width="44" height="14"><rect x="2" y="1" width="40" height="12" rx="3" fill="${css("--node-bg")}" stroke="${css("--accent")}" stroke-width="2.5"/></svg>Thick box: double-click to open its own graph.</div>
        </div>
        <h4>Controls</h4>
        <p>Scroll or pinch to zoom, drag to pan. Click a function to see its file and line, what it calls in order and who calls it. Click a box to zoom to it; double-click it to collapse. Hover an edge for the data passed along it.</p>
        <p>With <b>Focus on selection</b> on, clicking a function narrows the graph to its callers and callees and lays that out; click the background to see everything again. Dragged boxes keep their place until you press the re-layout button.</p>
      </div>`;
  }

  // ---------- stagebar ----------
  function buildStagebar() {
    const view = V.views[current];
    const bar = $("stagebar");
    bar.innerHTML = `<span class="vt">${esc(view.title)}</span><div class="chips" role="toolbar" aria-label="Groups"></div>`;
    const chips = bar.querySelector(".chips");
    for (const g of view.groups || []) {
      const count = view.nodes.filter((n) => n.group === g.id).length;
      const b = document.createElement("button");
      b.className = "chip";
      b.dataset.collapsed = collapsed.has(g.id);
      const short = g.label.includes("/") ? g.label.split("/").pop() : g.label;
      b.title = `${g.label}\nClick to zoom to it, double-click to collapse or expand it`;
      b.innerHTML = `<i class="dot" style="background:${stageColor(g.stage || g.id)}"></i>${esc(short)} <span class="c">${count}</span>`;
      let t = null;
      b.addEventListener("click", () => { clearTimeout(t); t = setTimeout(() => {
        let el = cy.getElementById(g.id);
        if (el.empty()) el = cy.nodes().filter((n) => (view.nodes.find((x) => x.id === n.id()) || {}).group === g.id);
        if (el.nonempty()) { cy.animate({ fit: { eles: el, padding: 60 }, duration: 260 }); showGroup(g.id); }
      }, 220); });
      b.addEventListener("dblclick", () => { clearTimeout(t); toggleGroup(g.id); });
      chips.appendChild(b);
    }
    if (view.subtitle) { const s = document.createElement("span"); s.className = "vs"; s.textContent = view.subtitle; bar.appendChild(s); }
  }

  function saveCollapsed() { const all = store.get("collapsed", {}); all[current] = [...collapsed]; store.set("collapsed", all); }
  function toggleGroup(gid) {
    if (collapsed.has(gid)) collapsed.delete(gid); else collapsed.add(gid);
    saveCollapsed();
    buildStagebar();
    render({ keepSelection: true, morph: true });
  }

  function show(vid) {
    if (!V.views[vid]) return Promise.resolve();
    current = vid;
    selectedId = null;
    focusId = null;
    const all = store.get("collapsed", {});
    collapsed = new Set(all[vid] || V.views[vid].collapsed || []);
    document.querySelectorAll(".rail button").forEach((b) => b.setAttribute("aria-current", String(b.dataset.view === vid)));
    store.set("view", vid);
    try { history.replaceState(null, "", "#" + vid); } catch (e) { /* ignore */ }
    buildStagebar();
    return render();
  }

  // ---------- search ----------
  const index = [];
  for (const [vid, v] of Object.entries(V.views)) for (const n of v.nodes) index.push({ vid, n, hay: (n.label + " " + (n.q || "") + " " + (n.loc || "")).toLowerCase() });
  const q = $("q"), results = $("results");
  q.addEventListener("input", () => {
    const s = q.value.trim().toLowerCase();
    if (s.length < 2) { results.hidden = true; return; }
    const hits = index.filter((x) => x.hay.includes(s)).slice(0, 40);
    results.innerHTML = hits.length ? hits.map((h, i) => `<button data-i="${index.indexOf(h)}"><div class="q">${esc(h.n.q || h.n.label.replace(/\n/g, " "))}</div><div class="v">${esc(V.views[h.vid].title)} · ${esc(h.n.loc || "")}</div></button>`).join("") : `<div style="padding:8px 10px;color:var(--ink-3)">No function matches “${esc(q.value)}”.</div>`;
    results.hidden = false;
    results.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
      const h = index[Number(b.dataset.i)];
      results.hidden = true; q.value = "";
      const go = () => select(h.n.id);
      const ready = current !== h.vid ? show(h.vid) : Promise.resolve();
      ready.then(() => {
        if (h.n.group && collapsed.has(h.n.group)) { collapsed.delete(h.n.group); saveCollapsed(); buildStagebar(); render({ keepSelection: true, morph: true }).then(go); }
        else go();
      });
    }));
  });
  document.addEventListener("click", (e) => { if (!e.target.closest(".search")) results.hidden = true; });
  q.addEventListener("keydown", (e) => { if (e.key === "Escape") { results.hidden = true; q.blur(); } if (e.key === "Enter") { const b = results.querySelector("button"); if (b) b.click(); } });

  // ---------- controls ----------
  $("z-in").addEventListener("click", () => cy.animate({ zoom: { level: cy.zoom() * 1.3, renderedPosition: { x: $("cy").clientWidth / 2, y: $("cy").clientHeight / 2 } }, duration: 150 }));
  $("z-out").addEventListener("click", () => cy.animate({ zoom: { level: cy.zoom() / 1.3, renderedPosition: { x: $("cy").clientWidth / 2, y: $("cy").clientHeight / 2 } }, duration: 150 }));
  $("z-fit").addEventListener("click", () => cy.animate({ fit: { eles: cy.elements(":visible"), padding: 30 }, duration: 220 }));
  $("z-relayout").addEventListener("click", () => relayout(true));
  $("t-data").checked = store.get("data", false);
  $("t-dir").value = ["AUTO", "DOWN", "RIGHT"].includes(store.get("dir")) ? store.get("dir") : "AUTO";
  $("t-focus").checked = store.get("focus", false);
  $("t-data").addEventListener("change", () => { store.set("data", $("t-data").checked); applyDataToggle(); });
  $("t-dir").addEventListener("change", () => { store.set("dir", $("t-dir").value); relayout(true); });
  $("t-focus").addEventListener("change", () => {
    store.set("focus", $("t-focus").checked);
    if ($("t-focus").checked && selectedId) select(selectedId);
    else if (!$("t-focus").checked && focusId) { applyFocus(null); relayout(true).then(() => { if (selectedId) highlight(cy.getElementById(selectedId)); }); }
  });
  let resizeTimer = null;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => cy && cy.resize(), 150); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && cy && document.activeElement !== q) clearSel(); });

  // re-style on theme change
  const mq = matchMedia("(prefers-color-scheme: dark)");
  const restyle = () => { if (cy) { cy.style(style()); applyDataToggle(); } buildRail(); document.querySelectorAll(".rail button").forEach((b) => b.setAttribute("aria-current", String(b.dataset.view === current))); buildStagebar(); };
  try { mq.addEventListener("change", restyle); } catch (e) { /* old browsers */ }
  new MutationObserver(restyle).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

  buildRail();
  const fromHash = (location.hash || "").slice(1);
  const first = V.views[fromHash] ? fromHash : store.get("view", V.start) in V.views ? store.get("view", V.start) : V.start;
  // Node sizes come from label metrics, so wait (briefly) for the web fonts before the first layout.
  const fontsReady = document.fonts && document.fonts.ready ? Promise.race([document.fonts.ready, new Promise((r) => setTimeout(r, 1500))]) : Promise.resolve();
  fontsReady.then(() => show(first));

  // Read-only handle for tools and the browser console: the live graph and the view switcher.
  window.CG = { cy: () => cy, show, views: () => Object.keys(V.views), current: () => current };
  window.addEventListener("hashchange", () => { const v = (location.hash || "").slice(1); if (V.views[v] && v !== current) show(v); });
})();
