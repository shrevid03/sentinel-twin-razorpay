/* Evidence cases: model triage, deterministic graph evidence, precedent, grounded copilot, human decision. */
let evCase = null;
const TRIAGE = { auto_clear: "auto-clear", review: "human review", flag: "flag" };

function renderEvidence(c) {
  evCase = c;
  const steps = c.reasoning_steps.map((s) => `<li><b>${esc(s.claim)}</b><div class="small muted">evidence: ${s.evidence.slice(0, 8).map((e) => `<code>${esc(String(e))}</code>`).join(" ")} · source: ${esc(s.source)}</div></li>`).join("");
  const prec = c.precedent.length ? `<details open><summary class="muted">Precedent: similar resolved cases</summary><ul>${c.precedent.map((p) =>
    `<li>${esc(p.case_id)} (${esc(p.account)}, ${esc(p.pattern)}, similarity ${p.similarity}): <b>${esc(p.human_decision)}</b> by ${esc(p.reviewer)}: “${esc(p.reason)}”</li>`).join("")}</ul></details>` : `<p class="muted small">No similar resolved case yet.</p>`;
  const decided = c.decision ? `<p><b>Decided:</b> ${esc(c.decision.decision)} by ${esc(c.decision.reviewer)} (“${esc(c.decision.reason)}”)${c.decision.reportable ? ' <span class="tag">reportable (RBI-RPT-04)</span>' : ""}</p>`
    : `<div class="row"><button data-evd="confirm_fraud">Confirm fraud</button><button class="ghost" data-evd="monitor">Monitor</button><button class="ghost" data-evd="clear">Clear</button></div>`;
  $("#ev-out").innerHTML = `<h4>${esc(c.id)} · ${esc(c.account)} · model recommends <span class="scn-dec ${c.recommendation === "auto_clear" ? "approve" : "review"}">${esc(TRIAGE[c.recommendation] || c.recommendation)}</span> · pattern ${esc(c.pattern)} · risk ${c.risk_score.toFixed(2)}</h4>
    <ol class="ev-steps">${steps}</ol>${prec}
    <div class="row"><input id="ev-q" placeholder="Ask the copilot about this case" style="flex:1"><button class="ghost" id="btn-ev-ask">Ask</button></div>
    <div id="ev-chat">${c.chat.map((m) => `<p class="small"><b>Q:</b> ${esc(m.q)}<br><b>Copilot (${esc(m.brain)}):</b> ${esc(m.a)}</p>`).join("")}</div>${decided}`;
  $("#btn-ev-ask").onclick = async () => {
    const q = $("#ev-q").value.trim(); if (!q) return;
    const r = await api(`/api/evidence/${encodeURIComponent(c.id)}/ask`, { question: q });
    c.chat.push({ q, a: r.answer, brain: r.brain }); renderEvidence(c);
  };
  loadComplianceLedger();
  loadTimeline(c.account);
}

async function loadTimeline(acc) {
  try {
    const t = await api(`/api/timeline/${encodeURIComponent(acc)}`);
    if (!t.events.length) return;
    const head = t.sequence_model ? `Event timeline · sequence transformer risk ${t.risk} (bars = attention)` :
      "Event timeline (sequence transformer is off; start with TWIN_SEQ=1 to see its attention)";
    $("#ev-out").insertAdjacentHTML("beforeend", `<details><summary class="muted">${esc(head)}</summary><ol class="ev-steps small">${t.events.map((e) =>
      `<li>${esc(e.event)} · ${money(e.inr)}${e.new_device ? " · new device" : ""}${e.new_place ? " · new place/counterparty" : ""}${
        e.attention != null ? ` <span style="display:inline-block;height:6px;width:${Math.round(e.attention * 300)}px;background:#5FBF9F;border-radius:3px"></span> ${e.attention}` : ""}</li>`).join("")}</ol></details>`);
  } catch (e) { /* the timeline is extra context */ }
}

async function openEvidence(acc) {
  if (!acc) return;
  try { renderEvidence(await api(`/api/evidence/${encodeURIComponent(acc)}`, {})); } catch (e) { $("#ev-out").innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
}

async function loadComplianceLedger() {
  try {
    const l = await api("/api/ledger?limit=5");
    $("#ev-ledger").textContent = `Ledger: ${l.verify.records} records, hash chain ${l.verify.ok ? "verified" : "BROKEN at #" + l.verify.broken_at}` +
      (l.metrics.resolved ? ` · ${l.metrics.resolved} decided, human agreed with the model on ${Math.round(l.metrics.agreement_rate * 100)}%, ${l.metrics.reportable} reportable` : "");
  } catch (e) { /* ledger panel is informational */ }
}

document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-evd]"); if (!b || !evCase) return;
  const reason = prompt(`${b.textContent} for ${evCase.account}. Reason (required, RBI-ACC-03):`, "");
  if (reason === null) return;
  try {
    renderEvidence(await api(`/api/evidence/${encodeURIComponent(evCase.id)}/decide`, { decision: b.dataset.evd, reason, reviewer: $("#reviewer").value || "" }));
    refresh();
  } catch (err) { alert(err.message); }
});
$("#btn-ev-open").onclick = () => openEvidence($("#ev-account").value.trim());
$("#btn-ev-top").onclick = async () => {
  const f = (await api("/api/accounts")).filter((a) => a.status === "active").sort((a, b) => b.risk - a.risk);
  if (f.length) { $("#ev-account").value = f[0].id; openEvidence(f[0].id); }
};
document.querySelectorAll('#tabs button[data-tab="investigator"]').forEach((b) => b.addEventListener("click", loadComplianceLedger));
