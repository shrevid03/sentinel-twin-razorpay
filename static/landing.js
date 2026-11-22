/* Landing page: hero network, replay scrubber, and (html.motion only) the GSAP + ScrollTrigger + Lenis scroll story.
   Everything is visible without this file; it only adds motion and interaction. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const NS = "http://www.w3.org/2000/svg";
  const motion = document.documentElement.classList.contains("motion") && window.gsap && window.ScrollTrigger;
  const el = (tag, attrs, parent) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); parent && parent.appendChild(e); return e; };

  // ---------------------------------------------------------------- hero network (deterministic)
  const W = 1600, H = 1000, TARGET = { x: 1030, y: 610 };
  const net = $("#net");
  let world = null;
  if (net) {
    net.setAttribute("viewBox", `0 0 ${W} ${H}`);
    let s = 7;
    const rnd = () => ((s = (s * 16807) % 2147483647) - 1) / 2147483646;
    const nodes = [];
    for (let i = 0; i < 150; i++) nodes.push({ x: rnd() * W, y: rnd() * H, r: 2.5 + rnd() * 4, hot: false });
    const ring = [[1150, 520], [1210, 590], [1180, 680], [1100, 700], [1225, 470]];
    ring.forEach(([x, y]) => nodes.push({ x, y, r: 6, hot: true }));
    nodes.push({ x: TARGET.x, y: TARGET.y, r: 7, target: true });
    world = el("g", { id: "world" }, net);
    const edges = el("g", {}, world);
    const d2 = (a, b) => (a.x - b.x) ** 2 + (a.y - b.y) ** 2;
    const lines = [];
    nodes.forEach((a, i) => {
      nodes.map((b, j) => [d2(a, b), j]).filter(([, j]) => j > i).sort((p, q) => p[0] - q[0]).slice(0, 2).forEach(([d, j]) => {
        if (d < 190 ** 2) lines.push(el("line", { class: "e", x1: a.x, y1: a.y, x2: nodes[j].x, y2: nodes[j].y }, edges));
      });
    });
    ring.forEach(([x, y]) => el("line", { class: "e", x1: TARGET.x, y1: TARGET.y, x2: x, y2: y, "stroke-opacity": .5, stroke: "#FF5C5C" }, edges));
    const dots = el("g", {}, world);
    nodes.forEach((n) => el("circle", { cx: n.x, cy: n.y, r: n.r, class: n.target ? "target" : n.hot ? "n hot" : "n" }, dots));
    // a few pulses travelling along edges (paused when the hero is off screen)
    if (motion) {
      const pulses = el("g", {}, world);
      lines.filter((_, i) => i % 9 === 0).slice(0, 14).forEach((ln, k) => {
        const c = el("circle", { r: 2.6, class: "travel", cx: ln.getAttribute("x1"), cy: ln.getAttribute("y1") }, pulses);
        gsap.to(c, { attr: { cx: ln.getAttribute("x2"), cy: ln.getAttribute("y2") }, duration: 2.2 + (k % 5) * .4, repeat: -1, ease: "none", delay: k * .3 });
      });
      gsap.to($$("#net .n").filter((_, i) => i % 4 === 0), { opacity: .35, duration: 1.6, stagger: { each: .07, repeat: -1, yoyo: true }, ease: "sine.inOut" });
      // whimsy: nodes bob softly on their own little clocks
      $$("#net circle.n, #net circle.target").forEach((c, i) => {
        if (i % 2) return;
        gsap.to(c, { attr: { cy: +c.getAttribute("cy") + (i % 3 ? 5 : -6) }, duration: 2.4 + (i % 7) * .35, repeat: -1, yoyo: true, ease: "sine.inOut", delay: (i % 11) * .2 });
      });
    }
  }

  // ---------------------------------------------------------------- replay scrubber (works everywhere)
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
      line.innerHTML = T < DEC ? `t=${Math.round(T)}: the decision has not happened yet.`
        : `Replay at t=${DEC} uses <b>${used} items</b> → <b>review 0.63, identical</b>.${later ? ` ${later} later item${later > 1 ? "s" : ""} ignored.` : ""}`;
    };
    scrub.addEventListener("input", () => render(+scrub.value));
    render(+scrub.value);
    window.__renderReplay = (T) => { scrub.value = T; render(T); };
  }

  // ---------------------------------------------------------------- header gets a glass backing once you scroll
  const top = $(".top");
  const onTop = () => top && top.classList.toggle("scrolled", scrollY > 40);
  addEventListener("scroll", onTop, { passive: true }); onTop();

  // ---------------------------------------------------------------- a tiny sparkle burst (when the ring is caught)
  const sparkle = (host, x, y) => {
    if (!window.gsap || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    for (let i = 0; i < 16; i++) {
      const d = document.createElement("i"); d.className = "sparkle"; d.style.left = x + "px"; d.style.top = y + "px"; host.appendChild(d);
      const ang = (i / 16) * Math.PI * 2, dist = 40 + (i % 4) * 18;
      gsap.fromTo(d, { x: 0, y: 0, scale: 1.4, opacity: 1 }, { x: Math.cos(ang) * dist, y: Math.sin(ang) * dist, scale: 0, opacity: 0,
        duration: .9 + (i % 3) * .15, ease: "power3.out", onComplete: () => host.removeChild(d) });
    }
    if (window.__smoke) window.__smoke.glint = 1;
  };

  // ---------------------------------------------------------------- magnetic buttons (any pointer device)
  if (!matchMedia("(prefers-reduced-motion: reduce)").matches && matchMedia("(hover: hover)").matches) {
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
  $$('a[href^="#"]').forEach((a) => a.addEventListener("click", (e) => { const t = $(a.getAttribute("href")); if (t) { e.preventDefault(); lenis.scrollTo(t, { offset: 0 }); } }));
  const scrubbed = (trigger, extra = {}) => gsap.timeline({ scrollTrigger: { trigger, start: "top top", end: "bottom bottom", scrub: .6, ...extra } });

  // parallax glows
  gsap.to(".glows .g1", { yPercent: 40, ease: "none", scrollTrigger: { start: 0, end: "max", scrub: true } });
  gsap.to(".glows .g2", { yPercent: -60, ease: "none", scrollTrigger: { start: 0, end: "max", scrub: true } });
  gsap.to(".glows .g3", { yPercent: -120, ease: "none", scrollTrigger: { start: 0, end: "max", scrub: true } });

  // 1. hero: zoom the camera into one order; it grows into the order card
  const card = $("#orderCard"), copy = $("#heroCopy");
  const targetOffset = () => {  // where TARGET sits on screen (preserveAspectRatio slice), relative to the centre
    const sc = Math.max(innerWidth / W, innerHeight / H);
    return { x: (TARGET.x - W / 2) * sc, y: (TARGET.y - H / 2) * sc };
  };
  const o = targetOffset();
  gsap.set(card, { x: o.x, y: o.y, scale: .16, opacity: 0, transformOrigin: "50% 50%" });
  scrubbed(".hero")
    .to(copy, { y: -120, opacity: 0, scale: .94, duration: .35, ease: "power2.in" }, 0)
    .to(".scrollhint", { opacity: 0, duration: .1 }, 0)
    .to(world, { scale: 7, svgOrigin: `${TARGET.x} ${TARGET.y}`, duration: 1, ease: "power2.inOut" }, 0)
    .to(net, { opacity: .25, duration: .4 }, .6)
    .to(card, { opacity: 1, duration: .15 }, .3)
    .to(card, { x: 0, y: 0, scale: 1, duration: .6, ease: "power3.out" }, .3);

  // 2. ring: the shared links light up, the halo closes in, the score climbs from 0.09 to 0.63
  let caught = false;
  const sc = { v: .09 }, scoreEl = $("#ringScore"), verdict = $("#ringVerdict");
  gsap.set("#ring .l.lit", { opacity: .12 }); gsap.set("#ring .halo", { scale: .55, opacity: 0, transformOrigin: "50% 50%" });
  gsap.set("#ring .fraud", { opacity: .35 }); scoreEl.textContent = "0.09"; verdict.className = "verdict approve"; verdict.textContent = "approve";
  scrubbed("#story")
    .to("#ring .l.lit", { opacity: 1, stagger: .12, duration: .3 }, .25)
    .to("#ring .fraud", { opacity: 1, stagger: .1, duration: .25 }, .3)
    .to("#ring .halo", { scale: 1, opacity: 1, duration: .35, ease: "back.out(1.6)" }, .5)
    .to(sc, { v: .63, duration: .3, onUpdate: () => {
      scoreEl.textContent = sc.v.toFixed(2);
      const r = sc.v >= .5;
      if (r && !caught) { caught = true; const host = $("#ring"), h = host.getBoundingClientRect(), hb = $("#ring .halo").getBoundingClientRect();
        sparkle(host, hb.left - h.left + hb.width / 2, hb.top - h.top + hb.height / 2); }
      if (!r) caught = false;
      verdict.className = "verdict " + (r ? "review" : "approve"); verdict.textContent = r ? "review · caught" : "approve";
    } }, .55)
    .to("#ring .l.lit", { strokeDashoffset: -60, duration: .5, ease: "none" }, .5);

  // 3. mandate: the cart builds, the gift card is struck out, the meter fills
  const mv = { v: 15302 }, mval = $("#meterVal");
  gsap.set("#cartPanel .item", { x: 50, opacity: .25 }); gsap.set("#meterFill", { scaleX: .44 });
  scrubbed("#mandate")
    .to("#cartPanel .item", { x: 0, opacity: 1, stagger: .18, duration: .3, ease: "power2.out" }, .15)
    .fromTo("#cartPanel .item.bad", { scale: 1 }, { scale: .97, duration: .1, yoyo: true, repeat: 1 }, .62)
    .to("#meterFill", { scaleX: 1, duration: .35, ease: "power1.inOut" }, .5)
    .to(mv, { v: 28000, duration: .35, ease: "power1.inOut", onUpdate: () => { mval.textContent = "₹" + Math.round(mv.v).toLocaleString("en-IN"); } }, .5);

  // 4. pipeline: the good payment runs to capture, the risky one stops at the pre-capture check
  const st = $("#stCheck");
  scrubbed("#pipeline")
    .fromTo("#tokGood", { left: "12.5%" }, { left: "87.5%", duration: .7, ease: "none" }, .1)
    .fromTo("#tokBad", { left: "12.5%" }, { left: "62.5%", duration: .45, ease: "none" }, .3)
    .call(() => st.classList.toggle("alarm"), null, .75)
    .to("#tokBad", { scale: 1.6, duration: .08, yoyo: true, repeat: 3 }, .76)
    .from("#pipePanel .outcome", { y: 24, opacity: .3, stagger: .12, duration: .2 }, .6);

  // 5. red team: attack waves hit the shield; catch rates fill and count up
  const waves = $("#waves");
  for (let i = 0; i < 26; i++) el("circle", { class: "wave", r: 3 + (i % 3), cx: -10, cy: 30 + ((i * 37) % 120) }, waves);
  const vals = $$("#attack .v").map((v) => ({ el: v, to: +v.dataset.to, cur: { v: 0 } }));
  gsap.set("#attack .f", { scaleX: 0 });
  const rt = scrubbed("#redteam");
  rt.to("#waves circle", { attr: { cx: 425 }, opacity: (i) => (i % 7 === 0 ? 1 : 0), stagger: .02, duration: .25, ease: "power1.in" }, .05);
  rt.to("#attack .f", { scaleX: 1, stagger: .15, duration: .3, ease: "power2.out" }, .35);
  vals.forEach((v, i) => rt.to(v.cur, { v: v.to, duration: .3, onUpdate: () => { v.el.textContent = v.cur.v.toFixed(1) + "%"; } }, .35 + i * .15));
  rt.to("#attack .shield", { attr: { "stroke-width": 5 }, duration: .1, yoyo: true, repeat: 3 }, .3);

  // 6. replay: scrolling scrubs the timeline (dragging the slider still works)
  if (window.__renderReplay) {
    const rp = { t: 0 };
    window.__renderReplay(0);
    scrubbed("#replay").to(rp, { t: 200, duration: 1, ease: "none", onUpdate: () => window.__renderReplay(Math.round(rp.t)) });
  }

  // 7. proof: count up once on entry
  $$("[data-count]").forEach((n) => {
    const to = +n.dataset.count, dec = +(n.dataset.dec || 0), c = { v: 0 };
    ScrollTrigger.create({ trigger: n, start: "top 85%", once: true, onEnter: () => {
      gsap.fromTo(c, { v: 0 }, { v: to, duration: 1.6, ease: "power2.out", onUpdate: () => { n.textContent = c.v.toFixed(dec); } });
    } });
  });
  // headline lift as each step enters (transform only: the text is always readable)
  $$(".story .step h2, .proof h2, .cta-band h2").forEach((h) => gsap.from(h, { y: 40, scale: .97, ease: "power2.out",
    scrollTrigger: { trigger: h, start: "top 95%", end: "top 55%", scrub: .5 } }));
  addEventListener("resize", () => ScrollTrigger.refresh());
})();
