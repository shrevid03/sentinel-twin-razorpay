/* "Try it in about a minute": a calm guided path for reviewers. Each step does one real thing by pressing the app's own
   buttons (the same requests a person would make), highlights where the answer appears, and explains it in a line.
   Shown once on the first visit (or with ?tour=1); the "Guided tour" button reopens it. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const wait = (test, ms = 45000) => new Promise((ok, no) => { const t0 = Date.now(); const tick = () => { const v = test(); if (v) return ok(v); if (Date.now() - t0 > ms) return no(new Error("timeout")); setTimeout(tick, 250); }; tick(); });
  const go = (tab) => (window.showTab || (() => {}))(tab);
  const pctOf = (d, ev) => (d && d.attempted_inr ? (100 * (1 - ev / d.attempted_inr)).toFixed(1) + "%" : "–");
  const STEPS = [
    { title: "The shopping agent buys something", tab: "concierge", focus: "#pay-slot", act: "Buy it",
      text: "The agent buys a ₹2,699 coffee set for you. The server checks your spending limit in code, scores the risk, creates the order and you approve a simulated payment (demo gateway: no Razorpay keys, no real money).",
      run: async () => {
        const r = await api("/api/tour/checkout", {});
        if (!r.checkout) {  // e.g. the month's budget is used up: the mandate guard stops it before any order exists
          addMsg("sys", "The mandate guard stopped this checkout before any order was created: " + esc(((r.result || {}).reasons || ["not allowed"]).join("; ")) + ".");
          STEPS[0].after = "The spending limit is enforced in code: this order was stopped before any payment (Reset demo restores the month's budget). The next step is the interesting one."; return; }
        addMsg("bot", "I picked a Ceramic pour-over dripper set for ₹2,699. Limit checked, risk checked: approve the simulated payment below."); await renderPay(r.checkout);
        await wait(() => $("#pay-slot #demo-approve, #pay-slot #rzp-pay"), 15000);
        const pay = $("#pay-slot #demo-approve"); if (pay) { pay.click(); await wait(() => $("#pay-slot .paybox.done"), 30000); }
      },
      after: "Spending limit checked in code (₹2,699 within ₹12,500 per order), risk approved, payment authorized, checked again before capture, then captured exactly once. \"Why? (receipt)\" shows every step." },
    { title: "A clean-looking account that belongs to a ring", tab: "drill", focus: "#scn-card", act: "Run it",
      text: "A new, KYC-verified account checks out a ₹9,999 keyboard. Rules see nothing wrong. Press Run it: the twin scores it three ways.",
      run: async () => { $("#btn-scn").click(); await wait(() => $("#scn-out .scn-grid")); },
      after: "Rules alone approve it (0.09). The GAT with rules still approves (0.08; the GAT alone scores 0.02): the account looks clean by itself. The combined engine adds the known-fraud-neighbour signal (its phone number is shared with three known-fraud accounts) and sends it to review (0.63). A second ring account that slips past checkout is held again before capture (0.61): no money captured." },
    { title: "The evidence, and a person decides", tab: "investigator", focus: "#ev-card", act: "Open the riskiest case",
      run: async () => { $("#btn-ev-top").click(); await wait(() => $("#ev-out h4"), 20000); },
      after: "Each claim cites records in the graph; the copilot only explains them. The model recommends, and a named person confirms, monitors or clears the case, with a reason.",
      text: "The Investigator opens the riskiest case: every claim points at records in the graph, the copilot only explains them, and a named person confirms, monitors or clears it, with a reason." },
    { title: "Replay the decision", tab: "drill", focus: () => $("#scn-out [data-replay]")?.closest(".scn-col"), act: "Replay",
      text: "Each decision is stored with the evidence it was allowed to see. Press Replay: the twin rebuilds the graph as it was at decision time and re-runs the stored model.",
      run: async () => { const b = $("#scn-out [data-replay]"); b?.click(); await wait(() => { const o = $("#scn-out .replay-out"); return o && o.textContent.trim().length > 10; }, 20000); },
      after: "Same score, same decision. Evidence that arrived later is listed and ignored." },
    { title: "Optional: attack, harden, verify", tab: "drill", focus: "#tab-drill .card", act: "Run the drill (about 40 s)", optional: true,
      text: "The red team attacks a frozen copy of the twin, the simulator searches 216 policies (pricing false positives), and the same attacks are replayed under the winner.",
      run: async () => { const b = $("#btn-drill"); b?.click(); await wait(() => b?.disabled, 3000).catch(() => {}); await wait(() => !b?.disabled, 150000);
        try { const d = await (await fetch("/api/drill/latest")).json(); const s = d.summary || {};
          STEPS[4].after = `Attack ₹ intercepted: ${pctOf(d, s.breach_evaded_before_inr)} before, ${pctOf(d, s.breach_evaded_after_inr)} after hardening, on the same seeded attacks. That's the tour: the sidebar has the rest.`; } catch {} },
      after: "Done. That's the tour: the sidebar has the rest." },
  ];
  let i = 0, box, focused = null, busy = false;
  const clearFocus = () => { focused?.classList.remove("tour-focus"); focused = null; };
  const focus = (sel) => { clearFocus(); const el = typeof sel === "function" ? sel() : $(sel); if (!el) return;
    el.classList.add("tour-focus"); focused = el; el.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" }); };
  const render = (note) => {
    const s = STEPS[i];
    box.innerHTML = `<div class="tour-head"><span class="tour-step">~ step ${i + 1} of ${STEPS.length}${s.optional ? " · optional" : ""} ~</span><button class="tour-x" type="button" aria-label="Close the tour">×</button></div>
      <h3>${s.title}</h3><p>${note || s.text}</p>
      <div class="tour-dots">${STEPS.map((_, k) => `<i class="${k === i ? "on" : k < i ? "done" : ""}"></i>`).join("")}</div>
      <div class="tour-row">${i > 0 ? `<button class="ghost" data-t="back" type="button">Back</button>` : "<span></span>"}
        <span>${s.act && !note ? `<button data-t="act" type="button">${s.act}</button>` : ""}
        <button class="${s.act && !note ? "ghost" : ""}" data-t="next" type="button">${i === STEPS.length - 1 ? "Finish" : s.act && !note ? "Skip" : "Next"}</button></span></div>`;
  };
  const show = (k) => { i = Math.max(0, Math.min(STEPS.length - 1, k)); const s = STEPS[i]; go(s.tab); render(); setTimeout(() => focus(s.focus), 120); };
  const close = () => { clearFocus(); box?.classList.remove("open"); try { localStorage.setItem("st-tour-seen", "1"); } catch {} };
  const open = () => { if (!box) build(); box.classList.add("open"); show(0); };
  function build() {
    box = document.createElement("aside"); box.className = "tour"; box.setAttribute("role", "dialog"); box.setAttribute("aria-label", "Guided tour"); document.body.appendChild(box);
    box.addEventListener("click", async (e) => {
      if (e.target.closest(".tour-x")) return close();
      const b = e.target.closest("[data-t]"); if (!b || busy) return;
      const t = b.dataset.t, s = STEPS[i];
      if (t === "back") return show(i - 1);
      if (t === "next") return i === STEPS.length - 1 ? close() : show(i + 1);
      if (t === "act" && s.run) {
        busy = true; b.disabled = true; b.innerHTML = `<span class="spin"></span>Working…`;
        try { await s.run(); render(s.after); setTimeout(() => focus(s.focus), 80); }
        catch { render("That took longer than usual. The result will appear in the highlighted panel; press Next when you're ready."); }
        finally { busy = false; }
      }
    });
    addEventListener("keydown", (e) => { if (e.key === "Escape" && box.classList.contains("open")) close(); });
  }
  const btn = $("#btn-tour"); btn?.addEventListener("click", open);
  let seen = false; try { seen = !!localStorage.getItem("st-tour-seen"); } catch {}
  if (/[?&]tour=1/.test(location.search) || !seen) setTimeout(open, 900);
})();
