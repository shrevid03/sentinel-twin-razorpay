/* App shell behaviour: phone drawer, page title, toasts, staged progress for long runs, page-change entrances.
   Purely presentational: it never calls the API or changes what a button does. Loaded after fx.js. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const LABELS = Object.fromEntries([...document.querySelectorAll("#tabs button")].map((b) => [b.dataset.tab, b.textContent.trim()]));

  // ------------------------------------------------------------ drawer (phones) + page title
  const menu = $("#menu-btn"), scrim = $("#scrim");
  const setNav = (open) => { document.body.classList.toggle("nav-open", open); menu?.setAttribute("aria-expanded", String(open)); };
  menu?.addEventListener("click", () => setNav(!document.body.classList.contains("nav-open")));
  scrim?.addEventListener("click", () => setNav(false));
  addEventListener("keydown", (e) => { if (e.key === "Escape") setNav(false); });
  const _show = window.showTab;
  window.showTab = showTab = (t) => {
    _show(t);
    setNav(false);
    const title = $("#mobile-title"); if (title) title.textContent = LABELS[t] || "Sentinel Twin";
    if (!reduce && window.gsap) {  // cards rise in, a few at a time (they are visible again within half a second)
      const cards = [...document.querySelectorAll(`#tab-${t} > .card, #tab-${t} > .grid2 > * , #tab-${t} > .pulse-row > .card, #tab-${t} > .kpis`)].slice(0, 10);
      gsap.fromTo(cards, { y: 18, opacity: 0 }, { y: 0, opacity: 1, duration: .5, stagger: .05, ease: "power3.out", clearProps: "transform,opacity" });
    }
  };
  // the compact phone header mirrors the payment-mode badge
  const pay = $("#b-pay"), mini = $("#b-pay-mini");
  if (pay && mini) {
    const sync = () => { mini.textContent = pay.textContent.replace(/^Razorpay\s*·?\s*/i, "") ; mini.className = pay.className; };
    new MutationObserver(sync).observe(pay, { childList: true, characterData: true, subtree: true, attributes: true }); sync();
  }

  // ------------------------------------------------------------ toasts (errors that used to be alert() boxes)
  const host = $("#toasts");
  window.toast = (msg, kind = "ok", ms = 5200) => {
    if (!host) return;
    const t = document.createElement("div"); t.className = "toast " + kind; t.setAttribute("role", kind === "bad" ? "alert" : "status");
    t.textContent = String(msg).slice(0, 400); host.appendChild(t);
    setTimeout(() => { t.classList.add("out"); setTimeout(() => t.parentNode && t.parentNode.removeChild(t), 320); }, ms);
  };
  window.alert = (m) => window.toast(m, /error|fail|refus|cannot|not /i.test(String(m)) ? "bad" : "ok");

  // ------------------------------------------------------------ staged progress for the long runs
  // Shown while the button is busy. Steps are the stages the server runs, in order; timing is the usual duration.
  const STAGES = {
    "btn-drill": { secs: 40, steps: ["Red team attacks a frozen copy", "Searching 216 policies", "Replaying the same attacks", "Testing unseen attacks", "Scoring"] },
    "btn-scn": { secs: 14, steps: ["Copying the twin", "Pre-checkout decision", "Payment authorized", "Pre-capture decision", "Replaying both decisions"] },
    "btn-mrt": { secs: 25, steps: ["Perturbing the rings", "Retraining GraphSAGE", "Testing a fresh perturbation", "Scoring 3 seeds"] },
    "btn-campaign": { secs: 18, steps: ["Probing the twin", "Watching what got caught", "Adapting tactics", "Scoring the campaign"] },
    "btn-inj": { secs: 10, steps: ["Direct attempts", "Multilingual and encoded", "Indirect via listings and names", "Checking for leaks"] },
    "btn-investigate-top": { secs: 12, steps: ["Pulling the account", "Following shared links", "Gathering evidence", "Writing the case"] },
  };
  Object.entries(STAGES).forEach(([id, cfg]) => {
    const btn = document.getElementById(id); if (!btn) return;
    let panel = null, timer = 0, t0 = 0;
    const anchor = btn.closest(".cardhead") || btn.closest(".row") || btn;
    const start = () => {
      if (panel) return;
      t0 = performance.now();
      panel = document.createElement("div"); panel.className = "stages"; panel.setAttribute("role", "status");
      panel.innerHTML = `<span class="st-time">0s · usually about ${cfg.secs}s</span><b>Working…</b><div class="st-bar"><i></i></div><ol>${cfg.steps.map((s) => `<li>${s}</li>`).join("")}</ol>`;
      anchor.insertAdjacentElement("afterend", panel);
      const bar = panel.querySelector(".st-bar i"), lis = [...panel.querySelectorAll("li")], time = panel.querySelector(".st-time");
      const tick = () => {
        const el = (performance.now() - t0) / 1000, frac = 1 - Math.exp(-el / cfg.secs * 1.6);
        bar.style.width = (Math.min(.95, frac) * 100).toFixed(1) + "%";
        const k = Math.min(cfg.steps.length - 1, Math.floor(Math.min(.999, el / cfg.secs) * cfg.steps.length));
        lis.forEach((li, i) => { li.className = i < k ? "done" : i === k ? "now" : ""; });
        time.textContent = `${Math.round(el)}s · usually about ${cfg.secs}s`;
      };
      tick(); timer = setInterval(tick, 400);
    };
    const stop = () => {
      if (!panel) return;
      clearInterval(timer);
      const p = panel; panel = null;
      p.querySelector(".st-bar i").style.width = "100%";
      p.querySelectorAll("li").forEach((li) => { li.className = "done"; });
      p.querySelector("b").textContent = "Done";
      setTimeout(() => { p.style.transition = "opacity .4s"; p.style.opacity = "0"; setTimeout(() => p.parentNode && p.parentNode.removeChild(p), 420); }, 700);
    };
    new MutationObserver(() => (btn.disabled ? start() : stop())).observe(btn, { attributes: true, attributeFilter: ["disabled"] });
  });
})();
