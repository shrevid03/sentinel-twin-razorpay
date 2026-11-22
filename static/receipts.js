/* Decision receipts: why an agent checkout was created, held, blocked or rejected. Loaded after app.js. */
const OUTCOME = {
  razorpay_order_created: ["Razorpay order created", "pass"], held_for_review: ["Held for human review, no Razorpay order", "fail"],
  blocked_by_risk_engine: ["Blocked by the risk engine, no Razorpay order", "fail"],
  rejected_by_mandate_guard: ["Rejected by the mandate guard, no Razorpay order", "fail"],
  released_by_reviewer: ["Released by a reviewer, Razorpay order created", "pass"], rejected_by_reviewer: ["Rejected by a reviewer", "fail"],
};
const yes = (b) => (b ? '<span class="pass">pass</span>' : '<span class="fail">fail</span>');

async function openReceipt(id) {
  const r = await api("/api/receipts/" + encodeURIComponent(id));
  const [label, cls] = OUTCOME[r.outcome] || [r.outcome, ""];
  const m = r.mandate, k = r.risk, pp = r.payment, rv = r.review;
  $("#modal-title").textContent = `Decision receipt ${r.id}`;
  $("#modal-body").innerHTML = `
    <p><b class="${cls}">${esc(label)}</b> · ${money(r.total)} · created ${esc(r.created_at)} · buyer ${esc(r.buyer)}</p>
    <div class="rc-sec"><h4>Cart</h4>${r.cart.map((c) => esc(`${c.qty} × ${c.name} (${money(c.price)}, ${c.category})`)).join("<br>")}</div>
    <div class="rc-sec"><h4>Spending mandate at decision time</h4>
      Per-order limit ${money(m.per_order_limit)} · monthly budget ${money(m.monthly_budget)} · spent ${money(m.spent)} · remaining ${money(m.remaining)} · blocked: ${esc(m.blocked_categories.join(", ") || "none")}
      <table class="dtable">${r.mandate_checks.map((c) => `<tr><td>${esc(c.check)}</td><td>${yes(c.pass)}</td><td class="muted">${esc(c.detail)}</td></tr>`).join("")}</table></div>
    ${k ? `<div class="rc-sec"><h4>Risk decision</h4>
      Risk <b>${k.score}</b> = GAT part ${k.gat_part} combined with rules part ${k.rules_part} · decision <b>${esc(k.decision)}</b>
      · policy version <code>${esc(k.policy_version.hash)}</code> (applied ${esc(k.policy_version.applied_at)})
      <div class="muted">Reason codes: ${esc(k.factors.join("; ") || "none, normal for this buyer")}</div>
      ${k.decision_id ? `<div>Pre-checkout decision <code>${esc(k.decision_id)}</code> at t=${esc(String(k.decided_at))} · model <code>${esc(k.model_version || "")}</code>
        <button class="ghost" data-replay="${esc(k.decision_id)}">Replay decision</button><div class="replay-out" data-for="${esc(k.decision_id)}"></div></div>` : ""}</div>` : ""}
    ${r.pre_capture ? `<div class="rc-sec"><h4>Pre-capture risk check (after authorization, before capture)</h4>
      <code>${esc(r.pre_capture.decision_id)}</code> · <b>${esc(r.pre_capture.outcome)}</b> (risk ${r.pre_capture.score}) · ${esc(r.pre_capture.factors.join("; ") || "no new signals")}
      <button class="ghost" data-replay="${esc(r.pre_capture.decision_id)}">Replay decision</button><div class="replay-out" data-for="${esc(r.pre_capture.decision_id)}"></div></div>` : ""}
    ${pp ? `<div class="rc-sec"><h4>Razorpay (${esc(pp.mode !== "demo" ? "Real Razorpay test mode" : "Demo mode")})</h4>
      Order <code>${esc(pp.order_id)}</code> · status ${esc(pp.status)} · ${esc(pp.approval)} · payment ${esc(pp.capture_id || "not captured")}</div>` : ""}
    ${r.payment_status ? `<div class="rc-sec"><h4>Money: what the customer is told</h4><b>${esc(r.payment_status.state.replace(/_/g, " "))}</b> · ${esc(r.payment_status.customer_message)}
      <div class="muted">Gateway: ${esc(r.payment_status.gateway_note)}</div></div>` : ""}
    ${rv ? `<div class="rc-sec"><h4>Human review</h4>${esc(rv.hold_id)} · ${esc(rv.status)}${rv.reviewer ? ` by ${esc(rv.reviewer)}` : ""}${rv.note ? ` · "${esc(rv.note)}"` : ""}</div>` : ""}
    <div class="rc-sec"><h4>Timeline</h4>${r.timeline.map((t) => `<div><span class="muted">${esc(t.at)}</span> ${esc(t.event)}</div>`).join("")}</div>`;
  $("#modal").hidden = false;
}
document.addEventListener("click", (e) => {
  const a = e.target.closest(".rcpt-link");
  if (a) { e.preventDefault(); openReceipt(a.dataset.rcpt).catch((err) => addMsg("sys", "Receipt error: " + esc(err.message))); }
});
$("#modal-close").onclick = () => { $("#modal").hidden = true; };
$("#modal").onclick = (e) => { if (e.target.id === "modal") $("#modal").hidden = true; };

/* Replay a stored risk decision on the graph as it was at decision time */
async function replayDecision(id) {
  const r = await api(`/api/decisions/${encodeURIComponent(id)}/replay`, {});
  const el = document.querySelector(`.replay-out[data-for="${CSS.escape(id)}"]`) || $("#scn-out");
  const html = !r.replayable ? `<p class="bad small">Not replayable: ${esc(r.reason)}</p>` :
    `<p class="small ${r.identical ? "ok" : "bad"}"><b>${r.identical ? "Identical on replay" : "DIFFERENT on replay"}</b>: stored ${esc(r.stored.outcome)} (risk ${r.stored.score}),
     replayed ${esc(r.replayed.outcome)} (risk ${r.replayed.score}) on the graph as of t=${esc(String(r.decided_at))} with model <code>${esc(r.model_version)}</code>${r.model_version !== r.current_model_version ? ` (current model is <code>${esc(r.current_model_version)}</code>)` : ""}.
     ${r.evidence_added_since.length ? `Evidence that arrived later and was correctly ignored: ${r.evidence_added_since.slice(0, 6).map((e) => `${esc(e.item)} (${esc(e.kind)}, ${esc(e.provenance)})`).join(", ")}.` : "No evidence has arrived since."}</p>`;
  if (el) el.innerHTML = html;
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-replay]"); if (!b) return;
  replayDecision(b.dataset.replay).catch((err) => alert(err.message));
});
