// Route review page (PC only): served by `gps_server.py --review`.
// Shows every proposed trolley route from server/routes.json over the aerial
// photo and keep-out map, and records approve/reject with who and when.
(function () {
  "use strict";
  const $ = id => document.getElementById(id);
  const canvas = $("map"), ctx = canvas.getContext("2d");
  const COLORS = { proposed: "#ffd23f", approved: "#8be06b", rejected: "#ff5a5a" };
  let lib = null, routes = [], shown = [], sel = null;
  let G = null;                 // grid/projection from routes.json
  let cam = { x: 0, y: 0, s: 1 }; // world point at the canvas center, px per meter
  const tiles = [], overlay = new Image();
  let overlayReady = false;

  try { $("who").value = localStorage.getItem("reviewer") || ""; } catch (e) { /* storage blocked */ }
  $("who").addEventListener("change", () => {
    try { localStorage.setItem("reviewer", $("who").value.trim()); } catch (e) { /* ignore */ }
  });

  const proj = p => ({ x: (p[1] - G.lon0) * G.kx, y: (p[0] - G.lat0) * G.ky });
  const toScreen = q => ({ x: canvas.width / 2 + (q.x - cam.x) * cam.s, y: canvas.height / 2 - (q.y - cam.y) * cam.s });

  function toast(text) {
    const t = $("toast");
    t.textContent = text;
    t.style.display = "block";
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { t.style.display = "none"; }, 2500);
  }

  async function post(url, body) {
    const r = await fetch(url, { method: "POST", body: JSON.stringify(body),
      headers: { "Content-Type": "application/json", "X-Trolley-Client": "1" } });
    return r.json();
  }

  async function load() {
    const r = await fetch("/api/routes");
    if (!r.ok) {
      $("app").innerHTML = '<div id="error"><h1>Route review is off</h1><p style="margin-top:10px">Start the server on the PC with <code>--review</code>:</p>' +
        '<p style="margin-top:10px"><code>python server/gps_server.py --sim --review</code></p>' +
        '<p style="margin-top:10px">If it says the library is empty, run <code>python tools/build_routes.py</code> first.</p></div>';
      return;
    }
    lib = await r.json();
    routes = lib.routes || [];
    if (!G && lib.grid) {
      G = lib.grid;
      overlay.onload = () => { overlayReady = true; draw(); };
      overlay.src = G.overlay;
      for (const t of (window.AERIAL ? window.AERIAL.tiles : [])) {
        const img = new Image();
        const tile = { img, ready: false, nw: proj([t.north, t.west]), se: proj([t.south, t.east]) };
        img.onload = () => { tile.ready = true; draw(); };
        img.src = t.src;
        tiles.push(tile);
      }
      fitAll();
    }
    render();
  }

  function render() {
    const counts = { proposed: 0, approved: 0, rejected: 0 };
    routes.forEach(r => { counts[r.status]++; });
    const flagged = routes.filter(r => r.flags.length).length;
    $("summary").textContent = `${routes.length} routes, ${flagged} flagged · ${counts.approved} approved, ` +
      `${counts.rejected} rejected, ${counts.proposed} to review · built ${lib.built}` +
      (lib.terrain ? "" : " · NO terrain (trees/slopes not avoided)");
    const f = $("filter").value;
    shown = routes.filter(r => f === "all" || (f === "flagged" ? r.flags.length > 0 : r.status === f));
    const list = $("list");
    list.innerHTML = "";
    for (const r of shown) {
      const row = document.createElement("div");
      row.className = "row" + (sel && sel.id === r.id ? " sel" : "");
      row.innerHTML = `<span class="dot st-${r.status}"></span>` +
        `<div><div class="rid"></div><div class="rsub"></div></div>` +
        (r.flags.length ? `<span class="nflag">${r.flags.length}</span>` : "<span></span>");
      row.querySelector(".rid").textContent = r.id;
      row.querySelector(".rsub").textContent = `${r.from_label} → ${r.to_label} · ${Math.round(r.length_m)} m`;
      row.onclick = () => select(r);
      list.appendChild(row);
    }
    showCard();
    draw();
  }

  function select(r, fit = true) {
    sel = r;
    $("note").value = r ? r.note || "" : "";
    render();                       // shows the card, so the fit can leave room for it
    if (r && fit) { fitRoute(r); draw(); }
    const el = $("list").querySelector(".row.sel");
    if (el) el.scrollIntoView({ block: "nearest" });
  }

  function showCard() {
    const c = $("card");
    if (!sel) { c.hidden = true; return; }
    c.hidden = false;
    const r = sel;
    $("ctitle").textContent = r.id;
    $("cfrom").textContent = `${r.from_label} → ${r.to_label}`;
    const yd = m => Math.round(m * 1.09361);
    const stats = [
      ["Length", `${Math.round(r.length_m)} m (${yd(r.length_m)} yd)`],
      ["Straight line", `${Math.round(r.straight_m)} m`],
      ["On fairway", `${Math.round(r.fairway_m)} m`],
      ["Steepest", `${r.max_slope_pct}% (${Math.round(r.steep_m)} m at 15%+)`],
      ["Closest to blocked", r.clearance_m == null ? "—" : `${r.clearance_m} m`],
      ["Status", r.status],
    ];
    $("cstats").innerHTML = "";
    for (const [k, v] of stats) {
      const a = document.createElement("span"), b = document.createElement("div");
      a.textContent = k; b.textContent = v;
      $("cstats").append(a, b);
    }
    $("cflags").innerHTML = "";
    const items = r.flags.length ? r.flags : ["No flags"];
    for (const f of items) {
      const li = document.createElement("li");
      li.textContent = f;
      if (!r.flags.length) li.className = "ok";
      $("cflags").appendChild(li);
    }
    $("reviewed").textContent = r.by ? `${r.status} by ${r.by}, ${r.at.replace("T", " ")}` :
      (r.note ? "" : "Not reviewed yet");
  }

  async function review(status) {
    if (!sel) return;
    const by = $("who").value.trim();
    if (!by) { toast("Enter your name first"); $("who").focus(); return; }
    const res = await post("/api/routes/review", { id: sel.id, status, by, note: $("note").value });
    if (!res.ok) { toast(res.msg); return; }
    toast(res.msg);
    const id = sel.id;
    await load();
    const r = routes.find(x => x.id === id);
    // move on to the next route in the list after approving/rejecting
    const i = shown.findIndex(x => x.id === id);
    select(status === "proposed" ? r : (shown[i + 1] || shown[i] || r));
  }

  // ---- map --------------------------------------------------------------------
  function resize() {
    const m = $("main");
    canvas.width = m.clientWidth * devicePixelRatio;
    canvas.height = m.clientHeight * devicePixelRatio;
    draw();
  }

  // fit world points qs (plus pad meters) into the canvas, leaving `right` px free
  function fitBox(qs, pad, right = 0) {
    const xs = qs.map(q => q.x), ys = qs.map(q => q.y);
    const w = Math.max(...xs) - Math.min(...xs), h = Math.max(...ys) - Math.min(...ys);
    cam.s = Math.min((canvas.width - right) / (w + pad * 2), canvas.height / (h + pad * 2), 6 * devicePixelRatio);
    cam.x = (Math.max(...xs) + Math.min(...xs)) / 2 + right / 2 / cam.s;
    cam.y = (Math.max(...ys) + Math.min(...ys)) / 2;
  }

  function fitAll() {
    fitBox([{ x: G.x0, y: G.y0 }, { x: G.x0 + G.w * G.cell, y: G.y0 + G.h * G.cell }], 20);
  }

  function fitRoute(r) {
    fitBox(r.pts.map(proj), 30, ($("card").offsetWidth + 28) * devicePixelRatio); // clear of the card
  }

  function line(pts, width, color, dash) {
    ctx.beginPath();
    pts.forEach((p, i) => { const s = toScreen(proj(p)); i ? ctx.lineTo(s.x, s.y) : ctx.moveTo(s.x, s.y); });
    ctx.setLineDash(dash || []);
    ctx.lineWidth = width;
    ctx.strokeStyle = color;
    ctx.stroke();
    ctx.setLineDash([]);
  }

  function dot(p, r, fill, stroke) {
    const s = toScreen(proj(p));
    ctx.beginPath();
    ctx.arc(s.x, s.y, r, 0, Math.PI * 2);
    ctx.fillStyle = fill;
    ctx.fill();
    ctx.lineWidth = 2 * devicePixelRatio;
    ctx.strokeStyle = stroke;
    ctx.stroke();
  }

  function label(text, p, dx, dy) {
    const s = toScreen(proj(p));
    ctx.font = `700 ${12 * devicePixelRatio}px Inter, Segoe UI, sans-serif`;
    ctx.lineWidth = 3 * devicePixelRatio;
    ctx.strokeStyle = "rgba(0,0,0,.8)";
    ctx.strokeText(text, s.x + dx, s.y + dy);
    ctx.fillStyle = "#fff";
    ctx.fillText(text, s.x + dx, s.y + dy);
  }

  function draw() {
    if (!G) return;
    const dpr = devicePixelRatio;
    ctx.fillStyle = "#0a120e";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.imageSmoothingEnabled = true;
    for (const t of tiles) {
      if (!t.ready) continue;
      const a = toScreen(t.nw), b = toScreen(t.se);
      ctx.drawImage(t.img, a.x, a.y, b.x - a.x + 1, b.y - a.y + 1);
    }
    if (overlayReady && $("showko").checked) {
      const a = toScreen({ x: G.x0, y: G.y0 + G.h * G.cell }), b = toScreen({ x: G.x0 + G.w * G.cell, y: G.y0 });
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(overlay, a.x, a.y, b.x - a.x, b.y - a.y);
      ctx.imageSmoothingEnabled = true;
    }
    for (const h of window.COURSE.holes) {
      const tee = h.tee || h.line[0];
      label(String(h.no), tee, 6 * dpr, -6 * dpr);
      if (h.pin) dot(h.pin, 3 * dpr, "#fff", "#000");
    }
    for (const r of routes) {
      if (sel && r.id === sel.id) continue;
      line(r.pts, 2 * dpr, r.status === "approved" ? "rgba(139,224,107,.7)" :
        r.status === "rejected" ? "rgba(255,90,90,.6)" : "rgba(255,255,255,.55)");
    }
    if (sel) {
      line(sel.pts, 8 * dpr, "rgba(0,0,0,.75)");
      line(sel.pts, 4 * dpr, COLORS[sel.status]);
      sel.pts.slice(1, -1).forEach(p => dot(p, 2.5 * dpr, "#fff", "rgba(0,0,0,.6)"));
      dot(sel.pts[0], 7 * dpr, "#ffffff", "#000");
      dot(sel.pts[sel.pts.length - 1], 7 * dpr, COLORS[sel.status], "#000");
      label("start", sel.pts[0], 10 * dpr, 4 * dpr);
      label("end", sel.pts[sel.pts.length - 1], 10 * dpr, 4 * dpr);
    }
  }

  // pan and zoom
  let drag = null;
  canvas.addEventListener("mousedown", e => { drag = { x: e.clientX, y: e.clientY }; canvas.style.cursor = "grabbing"; });
  window.addEventListener("mouseup", () => { drag = null; canvas.style.cursor = "grab"; });
  window.addEventListener("mousemove", e => {
    if (!drag) return;
    cam.x -= (e.clientX - drag.x) * devicePixelRatio / cam.s;
    cam.y += (e.clientY - drag.y) * devicePixelRatio / cam.s;
    drag = { x: e.clientX, y: e.clientY };
    draw();
  });
  canvas.addEventListener("wheel", e => {
    e.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const sx = (e.clientX - rect.left) * devicePixelRatio, sy = (e.clientY - rect.top) * devicePixelRatio;
    const wx = cam.x + (sx - canvas.width / 2) / cam.s, wy = cam.y - (sy - canvas.height / 2) / cam.s;
    cam.s = Math.max(0.2, Math.min(30, cam.s * (e.deltaY < 0 ? 1.2 : 1 / 1.2)));
    cam.x = wx - (sx - canvas.width / 2) / cam.s;
    cam.y = wy + (sy - canvas.height / 2) / cam.s;
    draw();
  }, { passive: false });

  window.addEventListener("keydown", e => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
    const i = sel ? shown.findIndex(r => r.id === sel.id) : -1;
    if ((e.key === "ArrowDown" || e.key === "j") && shown.length) { select(shown[Math.min(i + 1, shown.length - 1)]); e.preventDefault(); }
    else if ((e.key === "ArrowUp" || e.key === "k") && shown.length) { select(shown[Math.max(i - 1, 0)]); e.preventDefault(); }
    else if (e.key === "a") review("approved");
    else if (e.key === "r") review("rejected");
    else if (e.key === "f" && sel) { fitRoute(sel); draw(); }
  });

  $("approve").onclick = () => review("approved");
  $("reject").onclick = () => review("rejected");
  $("reset").onclick = () => review("proposed");
  $("filter").onchange = () => render();
  $("showko").onchange = () => draw();
  $("export").onclick = async () => toast((await post("/api/routes/export", {})).msg);
  window.addEventListener("resize", resize);
  resize();
  load();
})();
