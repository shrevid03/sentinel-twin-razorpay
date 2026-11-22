/* Decision receipts: why an agent checkout was created, held, blocked or rejected. Loaded after app.js. */
const OUTCOME = {
  razorpay_order_created: ["Razorpay order created", "pass"], held_for_review: ["Held for human review, no Razorpay order", "fail"],
  blocked_by_risk_engine: ["Blocked by the risk engine, no Razorpay order", "fail"],
  rejected_by_mandate_guard: ["Rejected by the mandate guard, no Razorpay order", "fail"],
  released_by_reviewer: ["Released by a reviewer, Razorpay order created", "pass"], rejected_by_reviewer: ["Rejected by a reviewer", "fail"],
  release_undone: ["Release undone, checkout cancelled", "fail"],
};
const yes = (b) => (b ? '<span class="pass">pass</span>' : '<span class="fail">fail</span>');

async function openReceipt(id) {
  const r = await api("/api/receipts/" + encodeURIComponent(id));
  const [label, cls] = OUTCOME[r.outcome] || [r.outcome, ""];
  const m = r.mandate, k = r.risk, pp = r.payment, rv = r.review, ps = r.payment_status;
  const moneyOk = ps && ["captured", "no_payment", "created", "cancelled"].includes(ps.state);
  $("#modal-title").textContent = `Decision receipt ${r.id}`;
  $("#modal-body").innerHTML = `
    <div class="rc-head"><span class="big">${money(r.total)}</span><b class="${cls}">${esc(label)}</b></div>
    <p class="muted small">created ${esc(r.created_at)} · buyer ${esc(r.buyer)}</p>
    ${ps ? `<div class="rc-sec money ${moneyOk ? "ok" : ""}"><h4>Money · what the customer is told</h4>
      <b>${esc(ps.state.replace(/_/g, " "))}</b> · ${esc(ps.customer_message)}<div class="muted small" style="margin-top:6px">Gateway: ${esc(ps.gateway_note)}</div></div>` : ""}
    <div class="rc-grid">
      <div class="rc-sec"><h4>Cart</h4>${r.cart.map((c) => esc(`${c.qty} × ${c.name} (${money(c.price)}, ${c.category})`)).join("<br>")}</div>
      <div class="rc-sec"><h4>Spending mandate at decision time</h4>Per order ${money(m.per_order_limit)} · month ${money(m.monthly_budget)}<br>
        spent ${money(m.spent)} · remaining ${money(m.remaining)}<br><span class="muted small">blocked: ${esc(m.blocked_categories.join(", ") || "none")}</span></div>
    </div>
    <div class="rc-sec"><h4>Mandate checks (in code)</h4><table class="dtable"><thead><tr><th>Check</th><th>Result</th><th>Detail</th></tr></thead><tbody>
      ${r.mandate_checks.map((c) => `<tr><td>${esc(c.check)}</td><td>${yes(c.pass)}</td><td class="muted">${esc(c.detail)}</td></tr>`).join("")}</tbody></table></div>
    ${k ? `<div class="rc-sec"><h4>Risk decision · pre-checkout</h4>
      Risk <b>${k.score}</b> = GAT part ${k.gat_part} combined with rules part ${k.rules_part} · decision <span class="decision ${esc(k.decision)}">${esc(k.decision)}</span>
      <div class="muted small" style="margin-top:4px">Policy <code>${esc(k.policy_version.hash)}</code> applied ${esc(k.policy_version.applied_at)} · reasons: ${esc(k.factors.join("; ") || "none, normal for this buyer")}</div>
      ${k.decision_id ? `<div class="row" style="margin-top:10px"><code>${esc(k.decision_id)}</code><span class="muted small">t=${esc(String(k.decided_at))} · model <code>${esc(k.model_version || "")}</code></span>
        <button class="ghost" data-replay="${esc(k.decision_id)}">Replay decision</button></div><div class="replay-out" data-for="${esc(k.decision_id)}"></div>` : ""}</div>` : ""}
    ${r.pre_capture ? `<div class="rc-sec"><h4>Pre-capture check · after authorization, before capture</h4>
      <code>${esc(r.pre_capture.decision_id)}</code> <span class="decision ${esc(r.pre_capture.outcome)}">${esc(r.pre_capture.outcome)}</span> risk ${r.pre_capture.score}
      <div class="muted small" style="margin-top:4px">${esc(r.pre_capture.factors.join("; ") || "no new signals")}</div>
      <div class="row" style="margin-top:10px"><button class="ghost" data-replay="${esc(r.pre_capture.decision_id)}">Replay decision</button></div>
      <div class="replay-out" data-for="${esc(r.pre_capture.decision_id)}"></div></div>` : ""}
    ${pp ? `<div class="rc-sec"><h4>Razorpay · ${esc(pp.mode !== "demo" ? "test mode" : "demo mode")}</h4>
      Order <code>${esc(pp.order_id)}</code> · ${esc(pp.status)} · ${esc(pp.approval)} · payment ${esc(pp.capture_id || "not captured")}</div>` : ""}
    ${rv ? `<div class="rc-sec"><h4>Human review</h4>${esc(rv.hold_id)} · ${esc(rv.status)}${rv.reviewer ? ` by ${esc(rv.reviewer)}` : ""}${rv.note ? ` · "${esc(rv.note)}"` : ""}</div>` : ""}
    <div class="rc-sec"><h4>Timeline</h4><ol class="rc-tl">${r.timeline.map((t) => `<li><span>${esc(t.at)}</span>${esc(t.event)}</li>`).join("")}</ol></div>`;
  $("#modal").hidden = false;
}
document.addEventListener("click", (e) => {
  const a = e.target.closest(".rcpt-link");
  if (a) { e.preventDefault(); openReceipt(a.dataset.rcpt).catch((err) => addMsg("sys", "Receipt error: " + esc(err.message))); }
});
$("#modal-close").onclick = () => { $("#modal").hidden = true; };
$("#modal").onclick = (e) => { if (e.target.id === "modal") $("#modal").hidden = true; };
addEventListener("keydown", (e) => { if (e.key === "Escape") $("#modal").hidden = true; });

/* Replay a stored risk decision on the graph as it was at decision time, drawn as a timeline */
async function replayDecision(id) {
  const el = document.querySelector(`.replay-out[data-for="${CSS.escape(id)}"]`) || $("#scn-out");
  if (el) el.innerHTML = `<div class="rp"><div class="skel line"></div><div class="skel line short"></div></div>`;
  const [r, dec] = await Promise.all([api(`/api/decisions/${encodeURIComponent(id)}/replay`, {}), api(`/api/decisions/${encodeURIComponent(id)}`).catch(() => null)]);
  if (!el) return;
  if (!r.replayable) { el.innerHTML = `<div class="rp"><span class="rp-badge diff">Not replayable</span> <span class="muted small">${esc(r.reason)}</span></div>`; return; }
  const used = (dec?.evidence || []).filter((e) => e.kind !== "account"), later = r.evidence_added_since || [];
  const tEnd = Math.max(r.decided_at, ...later.map((e) => +e.available_at || 0), ...used.map((e) => +e.available_at || 0)) * 1.08 + 1;
  const tMin = Math.min(0, ...used.map((e) => +e.available_at || 0));
  const pos = (t) => (((+t || 0) - tMin) / (tEnd - tMin) * 100).toFixed(2) + "%";
  el.innerHTML = `<div class="rp">
    <div class="rp-top"><span class="rp-badge ${r.identical ? "same" : "diff"}">${r.identical ? "Identical on replay" : "Different on replay"}</span>
      <span class="muted small">${esc(r.source || "in-memory replay")}${r.events_replayed ? ` · ${r.events_replayed} events` : ""}</span></div>
    <div class="rp-axis"><div class="known" style="width:${pos(r.decided_at)}"></div>
      ${used.map((e) => `<i class="rp-dot used" style="left:${pos(e.available_at)}" data-tip="${esc(e.item)} · ${esc(e.kind)} · ${esc(e.provenance)}"></i>`).join("")}
      ${later.map((e) => `<i class="rp-dot later" style="left:${pos(e.available_at)}" data-tip="later: ${esc(e.item)} · ${esc(e.provenance)}"></i>`).join("")}
      <div class="rp-dec" style="left:${pos(r.decided_at)}"><span>decision t=${esc(String(r.decided_at))}</span></div></div>
    <div class="rp-cmp"><div><span class="muted small">Stored</span><b>${esc(r.stored.outcome)} · ${r.stored.score}</b></div>
      <div><span class="muted small">Replayed with model ${esc(r.model_version)}</span><b>${esc(r.replayed.outcome)} · ${r.replayed.score}</b></div></div>
    ${later.length ? `<p class="muted small" style="margin:10px 0 0">Arrived after the decision, correctly ignored:</p><ul class="rp-later">${later.slice(0, 6).map((e) => `<li>${esc(e.item)} · ${esc(e.kind)} · ${esc(e.provenance)}</li>`).join("")}</ul>`
      : `<p class="muted small" style="margin:10px 0 0">No evidence has arrived since the decision.</p>`}
  </div>`;
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-replay]"); if (!b) return;
  replayDecision(b.dataset.replay).catch((err) => (window.toast ? toast(err.message, "bad") : alert(err.message)));
});
