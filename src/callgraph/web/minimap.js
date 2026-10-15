// Minimap for the call-graph page: a small overview of the whole drawing with the visible area
// outlined. Click or drag in it to move the main view there. It hides itself whenever the whole
// drawing already fits on screen.
//
// API (window.CGMinimap):
//   attach(cy, canvas, colorOf)  -> detach()   colorOf(node) gives a node's stroke/fill colour
(function () {
  "use strict";

  function attach(cy, canvas, colorOf) {
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    let frame = 0, map = null;

    function bounds() {
      const eles = cy.elements(":visible");
      return eles.nonempty() ? eles.boundingBox({ includeLabels: false }) : null;
    }

    function draw() {
      frame = 0;
      if (cy.destroyed()) return;
      const bb = bounds();
      const ext = cy.extent();
      const fits = !bb || (bb.x1 >= ext.x1 && bb.y1 >= ext.y1 && bb.x2 <= ext.x2 && bb.y2 <= ext.y2);
      canvas.hidden = fits;
      if (fits) return;
      const W = canvas.clientWidth, H = canvas.clientHeight;
      if (canvas.width !== W * dpr) { canvas.width = W * dpr; canvas.height = H * dpr; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      const pad = 6;
      const s = Math.min((W - 2 * pad) / bb.w, (H - 2 * pad) / bb.h);
      const ox = pad + (W - 2 * pad - s * bb.w) / 2 - s * bb.x1, oy = pad + (H - 2 * pad - s * bb.h) / 2 - s * bb.y1;
      map = { s, ox, oy };
      cy.nodes(":visible").forEach((n) => {
        if (n.hasClass("olabel") || n.hasClass("otitle")) return;
        const b = n.boundingBox({ includeLabels: false });
        const x = ox + s * b.x1, y = oy + s * b.y1, w = Math.max(1.5, s * b.w), h = Math.max(1.5, s * b.h);
        ctx.globalAlpha = n.isParent() ? 0.55 : 0.9;
        if (n.isParent()) { ctx.strokeStyle = colorOf(n); ctx.lineWidth = 1; ctx.strokeRect(x, y, w, h); }
        else { ctx.fillStyle = colorOf(n); ctx.fillRect(x, y, w, h); }
      });
      ctx.globalAlpha = 1;
      const vx = ox + s * ext.x1, vy = oy + s * ext.y1;
      ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue("--focus").trim() || "#c2410c";
      ctx.lineWidth = 1.5;
      ctx.strokeRect(vx, vy, s * ext.w, s * ext.h);
    }

    function schedule() { if (!frame) frame = requestAnimationFrame(draw); }

    // Click / drag: centre the main view on the point under the pointer.
    function moveTo(ev) {
      if (!map) return;
      const r = canvas.getBoundingClientRect();
      const mx = (ev.clientX - r.left - map.ox) / map.s, my = (ev.clientY - r.top - map.oy) / map.s;
      const z = cy.zoom();
      cy.pan({ x: cy.width() / 2 - z * mx, y: cy.height() / 2 - z * my });
    }
    let dragging = false;
    const down = (ev) => { dragging = true; canvas.setPointerCapture(ev.pointerId); moveTo(ev); };
    const move = (ev) => { if (dragging) moveTo(ev); };
    const up = () => { dragging = false; };
    canvas.addEventListener("pointerdown", down);
    canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerup", up);
    cy.on("render viewport resize", schedule);
    schedule();

    return function detach() {
      cy.removeListener("render viewport resize", schedule);
      canvas.removeEventListener("pointerdown", down);
      canvas.removeEventListener("pointermove", move);
      canvas.removeEventListener("pointerup", up);
      if (frame) cancelAnimationFrame(frame);
      canvas.hidden = true;
    };
  }

  window.CGMinimap = { attach };
})();
