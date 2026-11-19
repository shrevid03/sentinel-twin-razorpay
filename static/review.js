/* Review queue: a human approves or rejects investigator proposals and held agent checkouts. Loaded after app.js. */
const ACTION_LABEL = { freeze: "Merchant-side restriction (simulated)", refund: "Razorpay refund" };

async function loadReview() {
  const q = await api("/api/actions");
  const pendingA = q.actions.filter((a) => a.status === "pending");
  const pendingH = q.holds.filter((h) => h.status === "pending");
  const twinBadge = (t) => !t ? "" : `<div class="rt-badge ${t.decision}" title="Reviewer twin, ${esc(t.mode)} mode: a prediction, not a decision">
      Your twin would <b>${t.decision}</b> (${Math.round(t.confidence * 100)}%)${t.reasons.length ? " because " + esc(t.reasons.join("; ")) : ""}</div>`;
  const items = [
    ...pendingH.map((h) => ({ conf: h.twin ? h.twin.confidence : 1, html: `<div class="case">${twinBadge(h.twin)}<h4>${esc(h.id)} · Held agent checkout ${money(h.total)} <span class="tag">risk ${h.risk}</span></h4>
      <div class="muted">${esc(h.cart.map((c) => `${c.qty} × ${c.name}`).join(", "))}</div>
      <small class="muted">Why held: ${esc(h.factors.join("; ") || "risk above the review threshold")}</small>
      <div class="row" style="margin-top:6px"><button data-rv="hold" data-id="${esc(h.id)}" data-ok="1">Release (create Razorpay order)</button>
        <button class="ghost" data-rv="hold" data-id="${esc(h.id)}" data-ok="0">Reject</button>
        <a href="#" class="rcpt-link" data-rcpt="${esc(h.receipt_id)}">Receipt</a></div></div>` })),
    ...pendingA.map((a) => ({ conf: a.twin ? a.twin.confidence : 1, html: `<div class="case">${twinBadge(a.twin)}<h4>${esc(a.id)} · ${esc(ACTION_LABEL[a.type] || a.type)}: ${esc(a.target)}</h4>
      <div>${esc(a.reason)} <span class="muted">· proposed by ${esc(a.proposed_by)}${a.case_id ? " in " + esc(a.case_id) : ""}</span></div>
      <ul>${a.evidence.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>
      <div class="row"><button data-rv="action" data-id="${esc(a.id)}" data-ok="1">Approve</button>
        <button class="ghost" data-rv="action" data-id="${esc(a.id)}" data-ok="0">Reject</button></div></div>` })),
  ];
  items.sort((x, y) => x.conf - y.conf);  // the cases the twin is least sure about come first
  $("#review-queue").innerHTML = items.map((i) => i.html).join("") || `<p class="muted">Nothing waiting for review.</p>`;
  loadReviewerTwin();
  $("#review-audit").innerHTML = q.audit.length ? `<table class="dtable"><tr><th>When</th><th>Item</th><th>Decision</th><th>Reviewer</th><th>Note</th></tr>
    ${q.audit.map((e) => `<tr><td>${esc(e.at)}</td><td>${esc(e.id)}</td><td>${esc(e.decision)}</td><td>${esc(e.reviewer)}</td><td>${esc(e.note)}</td></tr>`).join("")}</table>`
    : `<p class="muted">No decisions yet.</p>`;
}

document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-rv]");
  if (!b) return;
  const ok = b.dataset.ok === "1", kind = b.dataset.rv, id = b.dataset.id;
  const note = prompt(`${ok ? "Approve" : "Reject"} ${id}. Reason (required, RBI-ACC-03; goes to the audit trail and ledger):`, "") ?? null;
  if (note === null) return;
  const verb = kind === "hold" ? (ok ? "release" : "reject") : (ok ? "approve" : "reject");
  const path = kind === "hold" ? `/api/holds/${id}/${verb}` : `/api/actions/${id}/${verb}`;
  try {
    await api(path, { reviewer: $("#reviewer").value || "merchant", note });
  } catch (err) { alert(err.message); }
  loadReview(); refresh();
  if (typeof renderCases === "function") renderCases();
});

async function loadReviewerTwin() {
  const el = $("#rt-card-body"); if (!el) return;
  const s = await api("/api/reviewer-twin");
  const ag = s.agreement, agr = s.agreement_real_only;
  el.innerHTML = `<div class="kpis">
      ${kpi("agreement with the reviewer", ag.rate == null ? "–" : Math.round(ag.rate * 100) + "%")}
      ${kpi("decisions it learned from", `${s.trained_on.real} real · ${s.trained_on.replay} replayed · ${s.trained_on.seeded} seeded`)}
      ${kpi("mode", s.autopilot.enabled ? "autopilot" : "shadow", s.autopilot.enabled ? "warn" : "ok")}</div>
    <p class="muted small">Agreement over the last ${ag.n} decisions${agr.n ? ` (${agr.n} real: ${Math.round(agr.rate * 100)}%)` : ""}, each predicted <i>before</i> the human decided. ${esc(s.label)}.
      Seeded decisions are synthetic, from a written policy: ${esc(s.policy)}</p>
    <div class="row"><button id="btn-rt-replay" class="ghost">Replay 30 reviewer decisions</button>
      <label class="toggle"><input type="checkbox" id="rt-auto" ${s.autopilot.enabled ? "checked" : ""}> Autopilot: release held checkouts up to ${money(s.autopilot.max_inr)} at ≥ ${Math.round(s.autopilot.min_confidence * 100)}% (never refunds, restrictions or captures)</label></div>
    <details><summary class="muted">What it weighs</summary><table class="dtable">${s.weights.slice(0, 8).map((w) => `<tr><td>${esc(w.feature)}</td><td class="n ${w.weight > 0 ? "ok" : "bad"}">${w.weight > 0 ? "+" : ""}${w.weight.toFixed(2)}</td></tr>`).join("")}</table></details>
    ${s.recent.length ? `<details><summary class="muted">Recent decisions: twin vs human</summary><table class="dtable"><tr><th>Item</th><th>Source</th><th>Twin said</th><th>Human</th></tr>
      ${s.recent.map((r) => `<tr><td>${esc(r.id || "")}</td><td>${esc(r.source)}</td><td>${r.prediction ? esc(r.prediction.decision) + " " + Math.round(r.prediction.confidence * 100) + "%" : "–"}</td>
        <td class="${(r.prediction && (r.prediction.decision === "approve") === r.human_approved) ? "ok" : "bad"}">${r.human_approved ? "approve" : "reject"}</td></tr>`).join("")}</table></details>` : ""}
    ${s.autopilot_log.length ? `<p class="small">Autopilot released: ${s.autopilot_log.map((e) => `${esc(e.hold_id)} ${money(e.total)}${e.undone ? " (undone)" : ` <a href="#" data-undo="${esc(e.hold_id)}">undo</a>`}`).join(", ")}</p>` : ""}`;
  $("#btn-rt-replay").onclick = async () => { const b = $("#btn-rt-replay"); b.disabled = true; b.textContent = "Replaying…";
    try { const r = await api("/api/reviewer-twin/replay", {}); b.textContent = `Agreement ${Math.round(r.agreement.rate * 100)}% on 30 replayed decisions`; }
    catch (e) { alert(e.message); } loadReviewerTwin(); };
  $("#rt-auto").onchange = async (e) => { try { await api("/api/reviewer-twin/autopilot", { enabled: e.target.checked }); } catch (err) { alert(err.message); } loadReviewerTwin(); };
}
document.addEventListener("click", async (e) => {
  const u = e.target.closest("[data-undo]"); if (!u) return;
  e.preventDefault();
  try { await api("/api/reviewer-twin/undo/" + encodeURIComponent(u.dataset.undo), {}); } catch (err) { alert(err.message); }
  loadReview(); refresh();
});

document.querySelectorAll('#tabs button[data-tab="investigator"]').forEach((b) => b.addEventListener("click", loadReview));
setInterval(() => document.querySelector("#tab-investigator.active") && loadReview(), 5000);

$("#btn-reset").onclick = async () => {
  if (!confirm("Reset the demo? This rebuilds the twin from its seed (about 10 seconds) and clears checkouts, holds, proposed actions, receipts and the policy.")) return;
  const b = $("#btn-reset");
  b.disabled = true; b.textContent = "Resetting…";
  try { await api("/api/reset", {}); location.reload(); }
  catch (e) { alert("Reset failed: " + e.message); b.disabled = false; b.textContent = "Reset demo"; }
};
