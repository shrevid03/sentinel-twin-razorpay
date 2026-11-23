/* Landing page artwork and motion. All artwork is generated here (stone, ivy, leaves, blooms, stairs) and is drawn
   in its finished state first; html.motion then adds the GSAP + ScrollTrigger + Lenis scroll story on top.
   Without this file the page is still complete, just plainer. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const NS = "http://www.w3.org/2000/svg";
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const motion = document.documentElement.classList.contains("motion") && window.gsap && window.ScrollTrigger;
  const el = (tag, attrs, parent) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); parent && parent.appendChild(e); return e; };
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646;

  // ---------------------------------------------------------------- weathered stone (value-noise fbm, drawn once)
  const stone = (() => {
    const N = 256, c = document.createElement("canvas"); c.width = c.height = N;
    const x = c.getContext("2d"), img = x.createImageData(N, N);
    const G = 32, g = Array.from({ length: (G + 1) * (G + 1) }, () => rnd());
    const vn = (px, py, f) => {  // tileable value noise
      const X = (px / N) * f, Y = (py / N) * f, xi = Math.floor(X), yi = Math.floor(Y), xf = X - xi, yf = Y - yi;
      const at = (i, j) => g[((j % f) * (G + 1) + (i % f)) % g.length];
      const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf);
      return (at(xi, yi) * (1 - u) + at(xi + 1, yi) * u) * (1 - v) + (at(xi, yi + 1) * (1 - u) + at(xi + 1, yi + 1) * u) * v;
    };
    for (let py = 0; py < N; py++) for (let px = 0; px < N; px++) {
      const n = vn(px, py, 4) * .5 + vn(px, py, 8) * .25 + vn(px, py, 16) * .15 + vn(px, py, 32) * .1;
      const row = Math.floor(py / 32), bx = Math.floor((px + (row % 2) * 32) / 64), tone = g[(row * 7 + bx * 13) % g.length] * 10;
      const mortar = (py % 32 < 2 || (px + (row % 2) * 32) % 64 < 2) ? -12 : 0;
      const k = (py * N + px) * 4, l = 20 + n * 22 + tone + mortar;
      img.data[k] = l * .78; img.data[k + 1] = l * 1.02; img.data[k + 2] = l * .88; img.data[k + 3] = 255;
    }
    x.putImageData(img, 0, 0);
    return c.toDataURL("image/png");
  })();
  const si = $("#stoneImg"); if (si) si.setAttribute("href", stone);
  $$(".stonewall").forEach((p) => p.style.setProperty("--stone", `url(${stone})`));

  // ---------------------------------------------------------------- ivy
  const LEAF = "M0 0C-4-1.5-9-3-10-8C-6-8.5-4.5-10.5-3.5-14.5C-1.5-12.5 1.5-12.5 3.5-14.5C4.5-10.5 6-8.5 10-8C9-3 4-1.5 0 0Z";
  const leafAt = (g, x, y, rot, s, cls = "leaf") => el("path", { d: LEAF, class: cls, transform: `translate(${x.toFixed(1)} ${y.toFixed(1)}) rotate(${rot.toFixed(0)}) scale(${s.toFixed(2)})` }, g);
  const vineAlong = (g, pts, wob, leafEvery, scale) => {  // a wobbly vine through points, with leaves on alternating sides
    let d = `M${pts[0][0]} ${pts[0][1]}`;
    for (let i = 1; i < pts.length; i++) {
      const [x0, y0] = pts[i - 1], [x1, y1] = pts[i], mx = (x0 + x1) / 2 + (rnd() - .5) * wob, my = (y0 + y1) / 2 + (rnd() - .5) * wob;
      d += ` Q${mx.toFixed(1)} ${my.toFixed(1)} ${x1} ${y1}`;
    }
    const p = el("path", { d }, g), L = p.getTotalLength();
    const leaves = [];
    for (let s = 6, side = 1; s < L; s += leafEvery * (.7 + rnd() * .6), side *= -1) {
      const a = p.getPointAtLength(s), b = p.getPointAtLength(Math.min(L, s + 1));
      const ang = Math.atan2(b.y - a.y, b.x - a.x) * 57.3 + 90 * side + (rnd() - .5) * 30;
      leaves.push(leafAt(g, a.x, a.y, ang, scale * (.7 + rnd() * .6), "leaf" + (rnd() < .25 ? " lit" : "")));
    }
    return { path: p, leaves, L };
  };
  const wIvy = $("#windowIvy");
  if (wIvy) {
    const archPts = (side) => {  // up the jamb and over the arch's outer moulding
      const pts = [];
      for (let y = 1000; y >= 520; y -= 60) pts.push([800 + side * (270 + (rnd() - .5) * 14), y]);
      for (let a = 0; a <= 1; a += .12) { const th = (Math.PI) * (1 - a * .55), r = 545; pts.push([side < 0 ? 1080 + r * Math.cos(th) : 520 - r * Math.cos(th), 520 - r * Math.sin(Math.PI * a * .55)]); }
      return pts;
    };
    vineAlong(wIvy, archPts(-1), 40, 26, 1.7);
    vineAlong(wIvy, archPts(1).slice(0, 9), 40, 24, 1.6);
  }
  // ivy up the page edges (grows with the scroll on wide screens)
  const edgeVines = $$(".edge-ivy").map((svg) => {
    const g = svg.querySelector(".lv"), p = svg.querySelector(".vine");
    let d = "M40 1000"; for (let y = 1000; y > 0; y -= 70) d += ` Q${(20 + rnd() * 40).toFixed(0)} ${y - 35} ${(30 + rnd() * 25).toFixed(0)} ${y - 70}`;
    p.setAttribute("d", d);
    const L = p.getTotalLength(), leaves = [];
    for (let s = 10, side = 1; s < L; s += 34, side *= -1) {
      const a = p.getPointAtLength(s); leaves.push({ s, node: leafAt(g, a.x, a.y, side * 70 + (rnd() - .5) * 30, 1.3 + rnd() * .5) });
    }
    return { p, L, leaves };
  });

  // ---------------------------------------------------------------- ring: vines are edges, leaves along them, coral blooms
  const ringLeaves = $("#ring .leaves");
  if (ringLeaves) {
    $$("#ring .vine").forEach((v) => {
      const L = v.getTotalLength(), lit = v.classList.contains("lit");
      for (let s = 12; s < L - 6; s += 22) {
        const a = v.getPointAtLength(s), b = v.getPointAtLength(s + 1), side = (s / 22) % 2 ? 1 : -1;
        el("path", { d: LEAF, class: lit ? "lit" : "", transform: `translate(${a.x.toFixed(1)} ${a.y.toFixed(1)}) rotate(${(Math.atan2(b.y - a.y, b.x - a.x) * 57.3 + 90 * side).toFixed(0)}) scale(.55)` }, ringLeaves);
      }
    });
    $$("#ring .bloom").forEach((b) => {
      for (let i = 0; i < 6; i++) el("path", { d: "M0 0C-6-8-6-20 0-26C6-20 6-8 0 0Z", transform: `rotate(${i * 60})` }, b);
      el("circle", { r: 4 }, b);
    });
  }

  // ---------------------------------------------------------------- giant leaves (mandate)
  $$(".leaves-bg").forEach((svg, k) => {
    seed = 31 + k * 17;
    const defs = el("defs", {}, svg), id = "lg" + k;
    const gr = el("linearGradient", { id, x1: 0, y1: 1, x2: 0, y2: 0 }, defs);
    el("stop", { offset: 0, "stop-color": "#0B1F1A" }, gr); el("stop", { offset: .6, "stop-color": "#1E5A4C" }, gr); el("stop", { offset: 1, "stop-color": "#2E7D6B" }, gr);
    const holder = svg.classList.contains("right") ? el("g", { class: "flip" }, svg) : svg;
    for (let i = 0; i < 6; i++) {
      const x = 40 + rnd() * 200, y = 200 + i * 120 + rnd() * 60, s = .55 + rnd() * .55, rot = -50 + rnd() * 100;
      const pos = el("g", { transform: `translate(${x.toFixed(0)} ${y.toFixed(0)}) rotate(${rot.toFixed(0)}) scale(${s.toFixed(2)})` }, holder);
      const g = el("g", { class: "leaf" }, pos);  // the sway animation lives on this inner group, so it never overrides the placement
      el("path", { d: "M0 0C-70-50-90-190 0-300C90-190 70-50 0 0Z", fill: `url(#${id})`, stroke: "#0B1F1A", "stroke-width": 2 }, g);
      el("path", { d: "M0 0V-290", stroke: "#3FA58A", "stroke-opacity": .35, "stroke-width": 2.2, fill: "none" }, g);
      for (let v = 1; v < 7; v++) {
        const yy = -v * 40;
        el("path", { d: `M0 ${yy}Q-40 ${yy - 20} -${52 - v * 4} ${yy - 52}M0 ${yy}Q40 ${yy - 20} ${52 - v * 4} ${yy - 52}`, stroke: "#3FA58A", "stroke-opacity": .18, "stroke-width": 1.4, fill: "none" }, g);
      }
      if (rnd() < .7) el("circle", { class: "dew", cx: (rnd() - .5) * 40, cy: -80 - rnd() * 150, r: 2 + rnd() * 2.5 }, g);
    }
  });

  // ---------------------------------------------------------------- replay: the staircase + the scrubber
  const DEC = 120;
  const EV = [
    { t: 0, item: "Account created", sub: "signup" },
    { t: 0, item: "Phone verified by OTP", sub: "signup · shared with 3 known-fraud accounts" },
    { t: 24, item: "Prior order ₹1,899", sub: "order history" },
    { t: 61, item: "Prior order ₹2,499", sub: "order history" },
    { t: 88, item: "A-117 labelled fraud", sub: "training label" },
    { t: 136, item: "UPI ID mule.pool.17", sub: "a later payment" },
    { t: 172, item: "A-203 restricted", sub: "investigator freeze" },
  ];
  const steps = $("#stairSteps"), stepEls = [];
  if (steps) {  // a curved stair climbing toward the moonlit window; one step per piece of evidence, plus the decision
    const n = EV.length + 1;
    for (let i = 0; i < n; i++) {
      const k = i / (n - 1), x = 30 + k * 400 + Math.sin(k * Math.PI) * 20, y = 190 - k * 140, w = 70 - k * 22, h = 14 - k * 5, sk = 18 - k * 8;
      stepEls.push(el("path", { class: "stp", d: `M${x} ${y}h${w}l${sk} -${h}h-${w}z` }, steps));
    }
  }
  const tl = $("#tl"), list = $("#evlist"), scrub = $("#scrub"), line = $("#replayLine"), past = $("#tlPast");
  if (tl && list && scrub) {
    const pct = (t) => (t / 200) * 100;
    EV.forEach((e) => { const k = document.createElement("div"); k.className = "tick"; k.style.left = pct(e.t) + "%"; tl.appendChild(k); e.tick = k; });
    const dm = document.createElement("div"); dm.className = "tick dec"; dm.style.left = pct(DEC) + "%"; tl.appendChild(dm);
    const dl = document.createElement("div"); dl.className = "declbl"; dl.style.left = pct(DEC) + "%"; dl.textContent = "decision t=120"; tl.appendChild(dl);
    EV.forEach((e) => {
      const r = document.createElement("div"); r.className = "ev";
      r.innerHTML = `<time>t=${e.t}</time><div>${e.item}<small>${e.sub}</small></div><span class="st"></span>`;
      list.appendChild(r); e.row = r;
    });
    const stairOrder = [...EV.slice(0, 5), { t: DEC, dec: true }, ...EV.slice(5)];
    const render = (T) => {
      past.style.width = pct(T) + "%";
      let used = 0, later = 0;
      EV.forEach((e) => {
        const st = e.row.querySelector(".st");
        e.tick.classList.toggle("on", e.t <= T);
        if (e.t > T) { e.row.className = "ev future"; st.textContent = "not known yet"; }
        else if (e.t <= DEC) { e.row.className = "ev used"; st.textContent = "used by the decision"; used++; }
        else { e.row.className = "ev later"; st.textContent = "arrived later · ignored"; later++; }
      });
      stairOrder.forEach((e, i) => {
        const s = stepEls[i]; if (!s) return;
        s.setAttribute("class", "stp" + (e.t > T ? "" : e.dec ? " dec" : e.t <= DEC ? " used" : " later"));
      });
      line.innerHTML = T < DEC ? `t=${Math.round(T)}: the decision has not happened yet.`
        : `Replay at t=${DEC} uses <b>${used} items</b> → <b class="gold">review 0.63, identical</b>.${later ? ` ${later} later item${later > 1 ? "s" : ""} ignored.` : ""}`;
    };
    scrub.addEventListener("input", () => render(+scrub.value));
    render(+scrub.value);
    window.__renderReplay = (T) => { scrub.value = T; render(T); };
  }

  // ---------------------------------------------------------------- the launch moment
  $$('a[href="/app"]').forEach((a) => a.addEventListener("click", (e) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;  // new tab / window: just open it
    const played = window.STSound && window.STSound.launch();
    if (reduce && !played) return;
    e.preventDefault();
    if (!reduce) { $("#launchFlash")?.classList.add("go"); if (window.__smoke) window.__smoke.scatter = 1; }
    try { sessionStorage.setItem("st-arrive", String(Date.now())); } catch {}
    setTimeout(() => { location.href = a.href; }, played ? 1500 : 650);
  }));
  const S = (k) => window.STSound && window.STSound[k] && window.STSound[k]();

  // ---------------------------------------------------------------- small things: header, sparkles, magnetic buttons
  const top = $(".top");
  const onTop = () => top && top.classList.toggle("scrolled", scrollY > 40);
  addEventListener("scroll", onTop, { passive: true }); onTop();
  const sparkle = (host, x, y) => {
    if (!window.gsap || reduce) return;
    for (let i = 0; i < 18; i++) {
      const d = document.createElement("i"); d.className = "sparkle"; d.style.left = x + "px"; d.style.top = y + "px"; host.appendChild(d);
      const ang = (i / 18) * Math.PI * 2, dist = 40 + (i % 4) * 20;
      gsap.fromTo(d, { x: 0, y: 0, scale: 1.4, opacity: 1 }, { x: Math.cos(ang) * dist, y: Math.sin(ang) * dist, scale: 0, opacity: 0,
        duration: .9 + (i % 3) * .15, ease: "power3.out", onComplete: () => host.removeChild(d) });
    }
    if (window.__smoke) window.__smoke.glint = 1;
  };
  if (!reduce && matchMedia("(hover: hover)").matches) {
    $$(".magnetic").forEach((b) => {
      b.addEventListener("pointermove", (e) => {
        const r = b.getBoundingClientRect();
        b.style.transform = `translate(${((e.clientX - r.left) / r.width - .5) * 14}px, ${((e.clientY - r.top) / r.height - .5) * 12}px)`;
      });
      b.addEventListener("pointerleave", () => { b.style.transform = ""; });
    });
  }

  if (!motion) return;
  // ================================================================ the scroll story
  gsap.registerPlugin(ScrollTrigger);
  const lenis = new Lenis({ duration: 1.15, smoothWheel: true });
  lenis.on("scroll", ScrollTrigger.update);
  gsap.ticker.add((t) => lenis.raf(t * 1000));
  gsap.ticker.lagSmoothing(0);
  $$('a[href^="#"]').forEach((a) => a.addEventListener("click", (e) => { const t = $(a.getAttribute("href")); if (t) { e.preventDefault(); lenis.scrollTo(t); } }));
  const scrubbed = (trigger, extra = {}) => gsap.timeline({ scrollTrigger: { trigger, start: "top top", end: "bottom bottom", scrub: .6, ...extra } });

  // ivy grows in around the window on arrival, and up the page edges as you scroll
  $$("#windowIvy path:not(.leaf)").forEach((p) => { const L = p.getTotalLength(); gsap.fromTo(p, { strokeDasharray: L, strokeDashoffset: L }, { strokeDashoffset: 0, duration: 2.6, ease: "power2.out" }); });
  gsap.from("#windowIvy .leaf", { scale: 0, transformOrigin: "50% 100%", duration: .7, stagger: .015, delay: .5, ease: "back.out(2)" });
  edgeVines.forEach(({ p, L, leaves }) => {
    gsap.set(p, { strokeDasharray: L, strokeDashoffset: L });
    leaves.forEach((lf) => gsap.set(lf.node, { opacity: 0 }));
    ScrollTrigger.create({ start: 0, end: "max", onUpdate: (st) => {
      const grown = st.progress * L * 1.05; p.style.strokeDashoffset = Math.max(0, L - grown);
      leaves.forEach((lf) => { lf.node.style.opacity = lf.s < grown ? 1 : 0; });
    } });
  });

  // 1. hero: push through the window; the order emerges from the moon
  const card = $("#orderCard"), copy = $("#heroCopy"), moon = $(".moon"), win = $("#window");
  const mr = moon.getBoundingClientRect();
  gsap.set(card, { xPercent: -50, yPercent: -50, x: mr.left + mr.width / 2 - innerWidth / 2, y: mr.top + mr.height / 2 - innerHeight / 2, scale: .12, opacity: 0 });
  scrubbed(".hero")
    .to(copy, { y: -60, opacity: 0, duration: .3, ease: "power2.in" }, 0)
    .to(".scrollhint", { opacity: 0, duration: .1 }, 0)
    .to(win, { scale: 9, duration: 1, ease: "power2.in" }, 0)
    .call(() => { if (!window.__whooshed) { window.__whooshed = true; S("whoosh"); } }, null, .18)
    .to(win, { opacity: 0, duration: .2 }, .8)
    .to(moon, { scale: 1.6, opacity: .55, duration: 1, ease: "power1.in" }, 0)
    .to(card, { opacity: 1, duration: .15 }, .45)
    .to(card, { x: 0, y: 0, scale: 1, duration: .5, ease: "power3.out" }, .45);

  // 2. ring: the ivy creeps along the edges, the ring blooms coral, the score climbs 0.09 -> 0.63
  let caught = false;
  const sc = { v: .09 }, scoreEl = $("#ringScore"), verdict = $("#ringVerdict");
  $$("#ring .vine").forEach((v) => { const L = v.getTotalLength(); gsap.set(v, { strokeDasharray: L, strokeDashoffset: v.classList.contains("lit") ? L : 0 }); });
  gsap.set("#ring .leaves path.lit", { scale: 0, transformOrigin: "50% 100%" });
  gsap.set("#ring .bloom", { scale: 0, transformOrigin: "50% 50%" });
  gsap.set("#ring .halo", { scale: .55, opacity: 0, transformOrigin: "50% 50%" });
  gsap.set("#ring .fraud", { opacity: .4 });
  scoreEl.textContent = "0.09"; verdict.className = "verdict approve"; verdict.textContent = "approve";
  scrubbed("#story")
    .to("#ring .vine.lit", { strokeDashoffset: 0, stagger: .1, duration: .35, ease: "none" }, .62)
    .to("#ring .leaves path.lit", { scale: .55, stagger: .01, duration: .2, ease: "back.out(2)" }, .68)
    .to("#ring .fraud", { opacity: 1, stagger: .08, duration: .2 }, .76)
    .to("#ring .bloom", { scale: 1, rotation: 20, stagger: .08, duration: .3, ease: "back.out(2.2)" }, .8)
    .to("#ring .halo", { scale: 1, opacity: 1, duration: .3, ease: "back.out(1.6)" }, .84)
    .to(sc, { v: .63, duration: .3, onUpdate: () => {
      scoreEl.textContent = sc.v.toFixed(2);
      const r = sc.v >= .5;
      if (r && !caught) { caught = true; S("chime"); const host = $("#ring"), h = host.getBoundingClientRect(), hb = $("#ring .halo").getBoundingClientRect();
        sparkle(host, hb.left - h.left + hb.width / 2, hb.top - h.top + hb.height / 2); }
      if (!r) caught = false;
      verdict.className = "verdict " + (r ? "review" : "approve"); verdict.textContent = r ? "review · caught" : "approve";
    } }, .84);

  // 3. mandate: leaves drift by in parallax, the cart builds, the meter fills
  gsap.fromTo("#mandate .scenery", { scale: 1.12 }, { scale: 1, ease: "none", scrollTrigger: { trigger: "#mandate", start: "top bottom", end: "bottom top", scrub: true } });
  const mv = { v: 15302 }, mval = $("#meterVal");
  gsap.set("#cartPanel .item", { x: 50, opacity: .3 }); gsap.set("#meterFill", { scaleX: .44 });
  scrubbed("#mandate")
    .to("#cartPanel .item", { x: 0, opacity: 1, stagger: .18, duration: .3, ease: "power2.out" }, .15)
    .fromTo("#cartPanel .item.bad", { scale: 1 }, { scale: .97, duration: .1, yoyo: true, repeat: 1 }, .62)
    .to("#meterFill", { scaleX: 1, duration: .35, ease: "power1.inOut" }, .5)
    .to(mv, { v: 28000, duration: .35, ease: "power1.inOut", onUpdate: () => { mval.textContent = "₹" + Math.round(mv.v).toLocaleString("en-IN"); } }, .5);

  // 4. pipeline: silk billows; the good payment runs to capture, the risky one stops at pre-capture
  gsap.fromTo(".silk", { scale: 1.15, yPercent: 6 }, { scale: 1, yPercent: -6, ease: "none", scrollTrigger: { trigger: "#pipeline", start: "top bottom", end: "bottom top", scrub: true } });
  const st = $("#stCheck");
  scrubbed("#pipeline")
    .fromTo("#tokGood", { left: "12.5%" }, { left: "87.5%", duration: .7, ease: "none" }, .1)
    .fromTo("#tokBad", { left: "12.5%" }, { left: "62.5%", duration: .45, ease: "none" }, .3)
    .call(() => { st.classList.toggle("alarm"); if (st.classList.contains("alarm")) S("hold"); }, null, .75)
    .to("#tokBad", { scale: 1.6, duration: .08, yoyo: true, repeat: 3 }, .76)
    .from("#pipePanel .outcome", { y: 24, opacity: .3, stagger: .12, duration: .2 }, .6);

  // 5. lake: attack waves skim the water toward the shield; ripples spread; catch rates count up
  const waves = $("#waves"), rip = $("#ripples");
  for (let i = 0; i < 22; i++) el("circle", { class: "wave", r: 2 + (i % 3), cx: -10, cy: 135 + ((i * 29) % 80) }, waves);
  for (let i = 0; i < 4; i++) el("ellipse", { cx: 470, cy: 168 + i * 6, rx: 6, ry: 2, "stroke-width": 1.2 }, rip);
  const vals = $$("#attack .v").map((v) => ({ el: v, to: +v.dataset.to, cur: { v: 0 } }));
  gsap.set("#attack .f", { scaleX: 0 });
  const rt = scrubbed("#redteam");
  rt.to("#waves circle", { attr: { cx: 470 }, opacity: (i) => (i % 7 === 0 ? 1 : 0), stagger: .02, duration: .25, ease: "power1.in" }, .05);
  rt.to("#ripples ellipse", { attr: { rx: 120, ry: 14 }, opacity: 0, stagger: .06, duration: .4, ease: "power2.out" }, .2);
  rt.to("#attack .f", { scaleX: 1, stagger: .15, duration: .3, ease: "power2.out" }, .35);
  vals.forEach((v, i) => rt.to(v.cur, { v: v.to, duration: .3, onUpdate: () => { v.el.textContent = v.cur.v.toFixed(1) + "%"; } }, .35 + i * .15));
  rt.to("#attack .shield", { attr: { "stroke-width": 4.5 }, duration: .1, yoyo: true, repeat: 3 }, .3);

  // 6. replay: climbing the stairs scrubs the timeline (the slider still works by hand)
  if (window.__renderReplay) {
    const rp = { t: 0 };
    window.__renderReplay(0);
    let crossed = false;
    scrubbed("#replay").to({}, { duration: 1 }, 0).to(rp, { t: 200, duration: .36, ease: "none", onUpdate: () => {
      window.__renderReplay(Math.round(rp.t));
      if (rp.t >= DEC && !crossed) { crossed = true; S("sparkle"); } else if (rp.t < DEC) crossed = false;
    } }, .26);
  }

  // 7. proof: count up once; gold veins in the marble dividers draw in as they pass
  $$("[data-count]").forEach((n) => {
    const to = +n.dataset.count, dec = +(n.dataset.dec || 0), c = { v: 0 };
    ScrollTrigger.create({ trigger: n, start: "top 85%", once: true, onEnter: () => {
      gsap.fromTo(c, { v: 0 }, { v: to, duration: 1.6, ease: "power2.out", onUpdate: () => { n.textContent = c.v.toFixed(dec); } });
    } });
  });
  $$(".marble .vein").forEach((v) => gsap.fromTo(v, { strokeDashoffset: 1600 }, { strokeDashoffset: 0, ease: "none",
    scrollTrigger: { trigger: v.closest(".marble"), start: "top 95%", end: "top 40%", scrub: .5 } }));
  $$(".story .step h2, .proof h2, .cta-band h2").forEach((h) => gsap.from(h, { y: 40, scale: .97, ease: "power2.out",
    scrollTrigger: { trigger: h, start: "top 95%", end: "top 55%", scrub: .5 } }));
  addEventListener("resize", () => ScrollTrigger.refresh());
})();
