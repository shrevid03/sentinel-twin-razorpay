/* "Try it in 60 seconds": a calm guided path for reviewers. Each step does one real thing by pressing the app's own
   buttons (the same requests a person would make), highlights where the answer appears, and explains it in a line.
   Shown once on the first visit (or with ?tour=1); the "Guided tour" button reopens it. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const wait = (test, ms = 45000) => new Promise((ok, no) => { const t0 = Date.now(); const tick = () => { const v = test(); if (v) return ok(v); if (Date.now() - t0 > ms) return no(new Error("timeout")); setTimeout(tick, 250); }; tick(); });
  const go = (tab) => (window.showTab || (() => {}))(tab);
  const STEPS = [
    { title: "One suspicious order, start to finish", tab: "drill", focus: "#scn-card", act: "Run it",
      text: "A new, KYC-verified account checks out a ₹9,999 keyboard. Everything about it looks normal. Press Run it: the twin scores it three ways.",
      run: async () => { $("#btn-scn").click(); await wait(() => $("#scn-out .scn-grid")); },
      after: "Rules alone approve it. Once the graph sees the phone number it shares with a known fraud ring, it goes to review instead." },
    { title: "Stopped before any money is captured", tab: "drill", focus: () => document.querySelectorAll("#scn-out .scn-grid")[1]?.children[1], act: null,
      text: "A second ring account pays from a UPI ID the ring used before. Razorpay authorizes the payment, but the twin checks again before capture and holds it: not captured, and any bank hold is reversed by Razorpay." },
    { title: "Replay the decision", tab: "drill", focus: () => $("#scn-out [data-replay]")?.closest(".scn-col"), act: "Replay",
      text: "Every decision is stored with the evidence it was allowed to see. Press Replay: the twin rebuilds the graph as it was at decision time and re-runs the stored model.",
      run: async () => { const b = $("#scn-out [data-replay]"); b?.click(); await wait(() => { const o = $("#scn-out .replay-out"); return o && o.textContent.trim().length > 10; }, 20000); },
      after: "Same score, same decision. Anything that arrived later is listed and ignored." },
    { title: "Let a synthetic attacker try", tab: "redteam", focus: "#tab-redteam .card", act: "Launch it (about 20 s)", optional: true,
      text: "An attacker agent probes the twin, learns what got caught and changes tactics. All traffic is synthetic. This step is optional.",
      run: async () => { const b = $("#btn-campaign"); b?.click(); await wait(() => b?.disabled, 3000).catch(() => {}); await wait(() => !b?.disabled, 150000); },
      after: "That's the tour. The sidebar has the rest: the concierge you can chat with, the investigator, the defense drill, the what-if simulator and the audit ledger." },
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
