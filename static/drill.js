/* Defense drill tab: attack, search, replay, scorecard. Loaded after app.js (uses $, api, esc, money, kpi, refresh). */
let drillRes = null, drillChart = null;
const pct = (x) => (x == null ? "–" : (x * 100).toFixed(1) + "%");
const KNOB = { review_threshold: "review ≥ ", block_threshold: "block ≥ ", max_auto_amount: "auto-approve ≤ ",
  review_new_device_over: "new-device review > ", review_spend_multiple: "review at spend × " };
const MONEY_KNOBS = new Set(["max_auto_amount", "review_new_device_over"]);
const changed = (p, base) => Object.keys(KNOB).filter((k) => p[k] !== base[k]).map((k) => KNOB[k] + (MONEY_KNOBS.has(k) ? money(p[k]) : p[k])).join(", ") || "no change";
const fmtParams = (p) => Object.entries(p || {}).filter(([k]) => k !== "victim").map(([k, v]) => `${k}=${v}`).join(", ");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function attackRows(rows, round) {
  return rows.map((r) => `<tr class="${r.evaded_inr > 0 ? "breach" : ""}"><td>${esc(r.attack)}</td><td class="muted">${round}</td>
    <td>${esc(fmtParams(r.params))}${r.why_adapted ? `<div class="muted small">Why: ${esc(r.why_adapted)} · decided by ${esc(r.decided_by)}</div>` : ""}</td><td class="n">${money(r.fraud_inr)}</td><td class="n">${money(r.blocked_inr)} / ${money(r.reviewed_inr)}</td><td class="n ev">${money(r.evaded_inr)}</td>
    <td class="n">${Math.round((r.detection_rate || 0) * 100)}%</td></tr>`).join("");
}

async function runDrill() {
  const btn = $("#btn-drill");
  btn.disabled = true;
  btn.innerHTML = `<span class="spin"></span>Running drill…`;
  document.querySelectorAll(".drill-step").forEach((x) => x.classList.remove("show"));
  $("#drill-status").textContent = "";
  try {
    const d = drillRes = await api("/api/drill", { seed: 7 });
    const s = d.summary;

    $("#d-attacks").innerHTML = `<table class="dtable"><tr><th>Attack</th><th>Round</th><th>Params</th><th class="n">Attempted</th><th class="n">Blocked / reviewed</th><th class="n">Got through</th><th class="n">Intercepted</th></tr>
      ${attackRows(d.probes, "probe")}${attackRows(d.adapted, "adapted")}</table>`;
    $("#ds-1").classList.add("show");
    await sleep(600);

    const searchLine = (d.log.find((l) => l.step === "search") || {}).text || "";
    $("#d-search").textContent = searchLine.charAt(0).toUpperCase() + searchLine.slice(1) +
      `. Score = fraud ₹ intercepted (blocked or sent to review) minus false-positive cost (${money(d.review_cost_inr)} per good order reviewed, plus the full value of good orders blocked).`;
    const base = d.before_policy;
    const rows = [{ policy: base, ...d.baseline, today: true }, ...d.top_candidates];
    $("#d-cands").innerHTML = `<table class="dtable"><tr><th>Policy (changed knobs)</th><th class="n">Intercept rate</th><th class="n">Good orders reviewed</th><th class="n">FP cost</th><th class="n">₹ stopped per 100 flagged</th><th class="n">Net benefit</th></tr>
      ${rows.map((c, i) => {
        const chosen = !c.today && i === 1 && d.chosen;
        return `<tr class="${chosen ? "chosen" : ""}"><td>${c.today ? "<b>Today's policy</b>" : esc(changed(c.policy, base))}${chosen ? ' <span class="tag">chosen</span>' : ""}</td>
        <td class="n">${pct(c.catch_rate)}</td><td class="n">${c.good_reviewed}</td><td class="n">${money(c.false_positive_cost_inr)}</td><td class="n">${money(c.fraud_stopped_per_100_flagged_inr)}</td><td class="n">${money(c.net_benefit_inr)}</td></tr>`;
      }).join("")}</table>`;
    $("#ds-2").classList.add("show");
    await sleep(600);

    if (d.chosen) {
      $("#d-verify").innerHTML = `<table class="dtable"><tr><th>Attack (same seed)</th><th>Params</th><th class="n">Got through before</th><th class="n">After</th></tr>
        ${d.breaches.map((b, i) => {
          const v = d.verify[i] || {};
          return `<tr><td>${esc(b.attack)}</td><td>${esc(fmtParams(b.params))}</td><td class="n bad">${money(b.evaded_inr)}</td>
            <td class="n ${v.evaded_inr > 0 ? "bad" : "ok"}">${money(v.evaded_inr)}</td></tr>`;
        }).join("")}</table>`;
      $("#d-fresh").textContent = `After the fix, intercepted fraud splits into ${money(s.breach_blocked_after_inr)} blocked and ${money(s.breach_reviewed_after_inr)} sent to review. Unseen attacks (fresh seeds, never tuned on, same frozen twin): ${money(s.fresh_evaded_before_inr)} got through under today's policy, ${money(s.fresh_evaded_after_inr)} under the hardened one, of ${money(s.fresh_attempted_inr)} attempted.`;
    } else {
      $("#d-verify").innerHTML = `<p class="muted">No policy beat today's once false positives were priced in, so there is nothing to replay.</p>`;
      $("#d-fresh").textContent = "";
    }
    $("#ds-3").classList.add("show");
    await sleep(600);

    const arrow = (a, b) => `${a} → ${b ?? "–"}`;
    const after = (v, f) => (d.chosen ? f(v) : null);
    $("#d-kpis").innerHTML = [
      kpi(`Attack ₹ that got through<small>same seeded attacks</small>`, arrow(money(s.breach_evaded_before_inr), after(s.breach_evaded_after_inr, money)), "ok"),
      kpi(`Unseen attacks that got through<small>fresh seeds, never tuned on</small>`, arrow(money(s.fresh_evaded_before_inr), after(s.fresh_evaded_after_inr, money)), "ok"),
      kpi(`Attack ₹ intercepted<small>the seeded attacks only · blocked or sent to review</small>`, arrow(pct(1 - s.breach_evaded_before_inr / d.attempted_inr), after(1 - s.breach_evaded_after_inr / d.attempted_inr, pct)), "ok"),
      kpi(`All fraud ₹ intercepted<small>replayed everyday traffic plus the attacks</small>`, arrow(pct(s.catch_rate_before), after(s.catch_rate_after, pct)), "ok"),
      kpi(`Good customers sent to review<small>of ${s.legit_orders} good orders · blocked ${money(s.legit_blocked_before_inr)} → ${money(s.legit_blocked_after_inr)}</small>`,
        arrow(s.legit_reviewed_before, s.legit_reviewed_after), "warn"),
      kpi(`False-positive cost<small>${money(d.review_cost_inr)} per review plus blocked value</small>`, arrow(money(s.fp_cost_before_inr), after(s.fp_cost_after_inr, money)), "warn"),
      kpi(`Fraud ₹ stopped per 100 orders flagged<small>blocked or sent to review · ${s.orders_flagged_before} → ${s.orders_flagged_after ?? "–"} orders flagged</small>`,
        arrow(money(s.per_100_flagged_before_inr), after(s.per_100_flagged_after_inr, money)), "ok"),
    ].join("");
    $("#ds-4").classList.add("show");  // show before drawing so Chart.js can measure the canvas
    const data = { labels: ["Attack ₹ that got through", "False-positive cost ₹"], datasets: [
      { label: "Before", data: [s.breach_evaded_before_inr, s.fp_cost_before_inr], backgroundColor: "#40826D" },
      { label: "After", data: [s.breach_evaded_after_inr ?? 0, s.fp_cost_after_inr ?? 0], backgroundColor: "#8FD9BF" }] };
    if (drillChart) { drillChart.data = data; drillChart.update(); } else {
      drillChart = new Chart($("#d-chart"), { type: "bar", data, options: {
        plugins: { legend: { labels: { color: "#E6F2EC" } } },
        scales: { x: { ticks: { color: "#8FB3A6" } }, y: { ticks: { color: "#8FB3A6" }, grid: { color: "#1B3B33" } } } } });
    }
    drillChart.resize();
    await sleep(600);
    renderCompare(d.compare);
    $("#drill-status").textContent = `Drill finished in ${d.seconds}s on a frozen copy of the twin (seed ${d.seed}). Nothing live has changed yet.`;
    $("#btn-drill-apply").disabled = !d.chosen;
  } catch (e) {
    $("#drill-status").textContent = "Drill failed: " + e.message;
  }
  btn.disabled = false;
  btn.textContent = "Run defense drill";
}

/* step 5: rules only vs GAT + rules vs GAT + rules hardened, on identical orders */
let cmpChart = null;
const ENGINE = { rules_only: "Rules only (no graph)", gat_only: "GAT only (no rules)", gat_rules: "GAT + rules (shipped)", gat_rules_hardened: "GAT + rules, hardened policy" };
const ATTACK_LABEL = { "live fraud traffic": "Live fraud traffic (accounts the GAT trained on)", fraud_ring: "Fraud rings",
  device_farm_evasion: "Device farms", card_testing: "Card testing", account_takeover: "Account takeover", synthetic_identity: "Synthetic identity",
  layering_cycle: "Layering cycles", mule_fanout: "Mule fan-out", collusion_ring: "Buyer–seller collusion" };
function renderCompare(c) {
  if (!c) return;
  const names = Object.keys(c.engines);
  $("#d-engines").innerHTML = `<table class="dtable"><tr><th>Engine</th><th class="n">Unseen attacks intercepted</th><th class="n">All fraud intercepted</th>
    <th class="n">Fraud blocked / reviewed</th><th class="n">Good orders reviewed</th><th class="n">Good ₹ blocked</th><th class="n">FP cost</th><th class="n" title="fraud ₹ intercepted per 100 orders blocked or sent to review">₹ stopped per 100 flagged</th></tr>
    ${names.map((n) => { const e = c.engines[n], u = c.unseen_attacks[n]; return `<tr class="${n === "gat_rules_hardened" ? "chosen" : ""}"><td>${ENGINE[n]}</td>
      <td class="n"><b>${pct(u.intercept_rate)}</b> <small class="muted">of ${money(u.total_inr)}</small></td><td class="n">${pct(e.intercept_rate)}</td>
      <td class="n">${money(e.fraud_blocked_inr)} / ${money(e.fraud_reviewed_inr)}</td><td class="n">${e.good_reviewed} of ${e.good_total}</td>
      <td class="n">${money(e.good_blocked_inr)}</td><td class="n">${money(e.false_positive_cost_inr)}</td><td class="n">${money(e.fraud_stopped_per_100_flagged_inr)}</td></tr>`; }).join("")}</table>`;
  const kinds = Object.keys(c.by_attack).filter((k) => k !== "live fraud traffic");
  const colors = { rules_only: "#8FB3A6", gat_only: "#2F6B58", gat_rules: "#5FBF9F", gat_rules_hardened: "#40826D" };
  const data = { labels: kinds.map((k) => ATTACK_LABEL[k] || k), datasets: names.map((n) => ({ label: ENGINE[n],
    data: kinds.map((k) => Math.round((c.by_attack[k][n] || 0) * 100)), backgroundColor: colors[n] })) };
  const opts = { plugins: { legend: { labels: { color: "#E6F2EC" } } }, scales: { x: { ticks: { color: "#8FB3A6" } },
    y: { max: 100, ticks: { color: "#8FB3A6", callback: (v) => v + "%" }, grid: { color: "#1B3B33" } } } };
  $("#ds-5").classList.add("show");
  if (cmpChart) { cmpChart.data = data; cmpChart.update(); } else cmpChart = new Chart($("#d-cmp-chart"), { type: "bar", data, options: opts });
  cmpChart.resize();
  const winsFor = (a, b) => kinds.filter((k) => (c.by_attack[k][a] || 0) > (c.by_attack[k][b] || 0)).map((k) => ATTACK_LABEL[k] || k);
  const g = winsFor("gat_rules", "rules_only"), r = winsFor("rules_only", "gat_rules");
  $("#d-byattack").innerHTML = `<p>The graph wins on <b>${esc(g.join(", ") || "none")}</b>: fresh accounts tied together by shared devices and IPs.
    ${r.length ? `Rules alone do better on <b>${esc(r.join(", "))}</b>.` : ""} Where both are equal, the attack looks normal on the graph too, and only the policy layer helps.</p>
    ${c.by_attack["live fraud traffic"] ? `<p class="muted">"Live fraud traffic" (${money(c.by_attack["live fraud traffic"].total_inr)}) comes from accounts the GAT was trained on, so it is left out of the unseen-attack figure and the chart.</p>` : ""}`;
  const a = c.account_auc;
  $("#d-auc").textContent = a ? `Account-level held-out AUC on ${a.test_accounts} test accounts: same features with no graph ${a.features_only_no_graph}, GAT ${a.gat}. On this synthetic data, the graph's value shows up per transaction (new accounts joining a known ring), not in ranking already-known accounts.` : "";
}

$("#btn-drill").onclick = runDrill;
$("#btn-drill-apply").onclick = async () => {
  if (!drillRes?.after_policy) return;
  await api("/api/policy", drillRes.after_policy);
  $("#drill-status").textContent = "Hardened policy applied to the live twin: " + changed(drillRes.after_policy, drillRes.before_policy) + ".";
  $("#btn-drill-rollback").disabled = false;
  refresh();
};
$("#btn-drill-rollback").onclick = async () => {
  if (!drillRes) return;
  await api("/api/policy", drillRes.before_policy);
  $("#drill-status").textContent = "Rolled back to the previous live policy.";
  $("#btn-drill-rollback").disabled = true;
  refresh();
};

/* Command-center hero: CTA buttons, Attack -> Analyze -> Harden -> Verify strip, real sandbox capture badge. */
$("#hero-drill").onclick = () => showTab("drill");
$("#hero-shop").onclick = () => { showTab("concierge"); $("#msg").focus(); };
async function loadHero() {
  const [d, cos] = await Promise.all([api("/api/drill/latest").catch(() => null), api("/api/checkouts").catch(() => [])]);
  const real = cos.find((c) => c.status === "COMPLETED" && c.pay_order_id && !String(c.pay_order_id).startsWith("order_DEMO"));
  $("#hero-proof").innerHTML = real
    ? `<span class="proof">✓ Real Razorpay test-mode payment captured · order ${esc(real.pay_order_id)} · ${money(real.total)}</span>` : "";
  if (!d) {
    window.renderGauge?.(null); window.renderFlow?.(null);
    $("#loop-card").innerHTML = `<div class="loop-cta"><div><h3>Attack → Analyze → Harden → Verify</h3>
      <p class="muted" style="margin:0">No drill yet. One click runs the red team against a frozen copy of the twin, searches for a safer policy and replays the same attacks.</p></div>
      <button id="loop-run">Run defense drill</button></div>`;
    $("#loop-run").onclick = () => { showTab("drill"); runDrill(); };
    return;
  }
  const s = d.summary || {};
  window.renderGauge?.(d); window.renderFlow?.(d);
  const step = (n, title, v) => `<div class="step"><b><span class="n">${n}</span>${title}</b><span class="v">${v}</span></div>`;
  $("#loop-card").innerHTML = `<div class="cardhead"><h3>Latest defense drill${d.source ? ` <small class="muted">run when the server was built, on this seeded twin</small>` : ""}</h3><button class="ghost" id="loop-open">Open drill</button></div>
    <div class="loop">
      ${step(1, "Attack", `${d.attacks} synthetic attacks, ${money(d.attempted_inr)} attempted; ${money(s.breach_evaded_before_inr)} got through`)}
      ${step(2, "Analyze", esc(d.search ? d.search.charAt(0).toUpperCase() + d.search.slice(1) : "Policies scored on fraud stopped minus false-positive cost"))}
      ${step(3, "Harden", d.chosen ? "New policy: " + esc(changed(d.after_policy, d.before_policy)) : "Kept today's policy: nothing beat it once false positives were priced in")}
      ${step(4, "Verify", d.chosen ? `Same attacks replayed: ${money(s.breach_evaded_before_inr)} → ${money(s.breach_evaded_after_inr)} got through; attack ₹ intercepted ${pct(1 - s.breach_evaded_before_inr / d.attempted_inr)} → ${pct(1 - s.breach_evaded_after_inr / d.attempted_inr)}` : "Nothing to replay")}
    </div>`;
  $("#loop-card").querySelector(".loop").style.marginBottom = "0";
  $("#loop-open").onclick = () => showTab("drill");
}
window.loadHero = loadHero;
loadHero();
const _runDrill = runDrill;
runDrill = async (...a) => { try { return await _runDrill(...a); } finally { loadHero(); } };
$("#btn-drill").onclick = () => runDrill();

/* Drill tab: attack arrows hitting the shield, before vs after hardening (counts from the latest drill) */
window.renderFlow = (d) => {
  const el = document.getElementById("drill-flow"); if (!el) return;
  const N = 10, s = d?.summary || {}, att = d?.attempted_inr || 0;
  const share = (v) => (!att || !v ? 0 : Math.max(1, Math.round((N * v) / att)));
  const passB = d ? share(s.breach_evaded_before_inr) : 3, passA = d ? (d.chosen ? share(s.breach_evaded_after_inr) : passB) : 0;
  const panel = (ox, pass, title, sub, shieldCol) => {
    const lanes = Array.from({ length: N }, (_, i) => i), passSet = new Set(lanes.filter((i) => i % Math.ceil(N / Math.max(pass, 1)) === 1).slice(0, pass));
    while (passSet.size < pass) passSet.add(lanes.find((i) => !passSet.has(i)));
    return `<g transform="translate(${ox} 0)">
      <text x="10" y="22" class="fl-title">${title}</text><text x="10" y="40" class="fl-sub">${sub}</text>
      <g transform="translate(250 56)"><path d="M30 0 0 11v22c0 20 13 35 30 41 17-6 30-21 30-41V11Z" fill="${shieldCol}"/><path d="M18 36l9 9 16-18" stroke="#0F2620" stroke-width="5" fill="none" stroke-linecap="round" stroke-linejoin="round"/></g>
      ${lanes.map((i) => { const y = 58 + i * 7.2, ok = !passSet.has(i), dir = i < N / 2 ? -1 : 1;
        return `<g class="arrow ${ok ? "defl" : "pass"}" style="--dy:${dir * (40 + (i % 3) * 14)}px;animation-delay:${(i * 0.37).toFixed(2)}s"><path d="M10 ${y}h26" stroke="${ok ? "#5FBF9F" : "#FF5C5C"}" stroke-width="2.4" stroke-linecap="round"/><path d="M33 ${y - 4}l6 4-6 4" fill="${ok ? "#5FBF9F" : "#FF5C5C"}"/></g>`; }).join("")}
    </g>`;
  };
  const sub = (p) => `${p} of ${N} arrows get through`;
  el.innerHTML = `<div class="cardhead"><h3>Attack flow <span class="tag synth">Synthetic attack traffic</span></h3>
      <small class="muted">${d ? `Each arrow is a tenth of the ${money(att)} attempted. Red got through; green was intercepted.` : "Illustration until you run a drill: then the arrows follow the real result."}</small></div>
    <svg class="flowsvg" viewBox="0 0 900 140" role="img" aria-label="Before hardening ${passB} of ${N} attack arrows pass the shield; after hardening ${passA} of ${N}">
      ${panel(0, passB, "Before hardening", d ? `${money(s.breach_evaded_before_inr)} got through · ${sub(passB)}` : "Today's policy", "#8FB3A6")}
      <line x1="450" y1="20" x2="450" y2="130" stroke="#1B3B33" stroke-width="2"/>
      ${panel(460, passA, "After hardening", d ? (d.chosen ? `${money(s.breach_evaded_after_inr)} got through · ${sub(passA)}` : "No better policy found") : "Run a drill to see it", "#40826D")}
    </svg>`;
};

/* Scenario: a clean new account joins a known ring through a shared UPI ID; rules vs GAT vs GAT + neighbours */
async function runScenario(pre) {
  const btn = $("#btn-scn"), out = $("#scn-out");
  btn.disabled = true; btn.innerHTML = `<span class="spin"></span>Running…`;
  try {
    const r = pre || await api("/api/scenario/new-account", {});
    if (r.error) { out.innerHTML = `<p class="muted">${esc(r.error)}</p>`; return; }
    const dec = (d) => `<span class="scn-dec ${esc(d)}">${esc(d)}</span>`;
    const col = (title, risk, d, reasons, note) => `<div class="scn-col"><h4>${title}</h4>${dec(d)} <span class="muted">risk ${Number(risk).toFixed(2)}</span>
      <ul>${reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>${note ? `<p class="muted small">${note}</p>` : ""}</div>`;
    const ns = r.graph.neighbour_signal;
    out.innerHTML = (r.source ? `<p class="small muted">Shown from the ${esc(r.source)}. <b>Run scenario</b> repeats it live (about a minute on the free server).</p>` : "") + `<p><b>${esc(r.account.name)}</b> (${esc(r.account.id)}) · ${r.account.age_days} days old · KYC verified · ${esc(r.account.email_domain)} email · own phone and home IP · 2 normal past orders.
        At signup it verified a phone number (<b>${esc(r.ring.shared_contact)}</b>) that ${r.ring.accounts_on_contact - 1} accounts in ${esc(r.ring.segment)} also use, ${r.ring.known_fraud_on_contact} of them already labelled fraud.
        Order: ${money(r.order.total)} at ${esc(r.order.merchant)}.</p>
      <div class="scn-grid">
        ${col("Rules only", r.rules_only.score, r.rules_only.decision, r.rules_only.reasons, "New device, foreign IP, spend jump, velocity, account age.")}
        ${col("GAT + rules", r.gat_rules.risk, r.gat_rules.decision, [`GAT risk ${Number(r.gat_rules.gat_risk).toFixed(2)}: the account's own features look clean`], "How the engine scored before the neighbour signal was added.")}
        ${col("GAT + rules + known-fraud neighbours", r.graph.risk, r.graph.decision, r.graph.reasons,
              `${ns.known_fraud} of ${ns.labelled} labelled linked accounts are known fraud (training labels and frozen accounts only). The shipped engine.`)}
      </div>
      <div class="scn-grid">
        <div class="scn-col"><h4>1 · Pre-checkout <code>${esc(r.pre_checkout.decision_id)}</code></h4><span class="scn-dec ${esc(r.pre_checkout.outcome)}">${esc(r.pre_checkout.outcome)}</span>
          <ul>${r.pre_checkout.evidence.map((e) => `<li>${esc(e.item)} (${esc(e.kind)}): ${esc(e.provenance)}</li>`).join("")}</ul>
          <button class="ghost" data-replay="${esc(r.pre_checkout.decision_id)}">Replay decision</button><div class="replay-out" data-for="${esc(r.pre_checkout.decision_id)}"></div></div>
        <div class="scn-col"><h4>2 · Pre-capture <code>${esc(r.pre_capture.decision_id || "")}</code></h4>
          <p class="small">${esc(r.pre_capture.name)} passed pre-checkout (${esc(r.pre_capture.pre_checkout_outcome)}), then paid from a UPI ID three ring members used before. Checked after authorization, before capture:</p>
          <span class="scn-dec ${esc(r.pre_capture.outcome || "")}">${esc(r.pre_capture.outcome || "")}</span> <span class="muted">risk ${r.pre_capture.score} · ${esc(r.pre_capture.capture_status || "")}</span>
          <ul>${r.pre_capture.evidence.map((e) => `<li>${esc(e.item)} (${esc(e.kind)}): ${esc(e.provenance)}</li>`).join("")}</ul>
          <button class="ghost" data-replay="${esc(r.pre_capture.pre_checkout_decision_id)}">Replay its pre-checkout decision</button><div class="replay-out" data-for="${esc(r.pre_capture.pre_checkout_decision_id)}"></div></div>
      </div>
      <p class="muted small" style="margin-top:10px">Agent checkout receipt ${esc(r.receipt.id || "")}: ${r.receipt.timeline.map(esc).join(" → ")}.
        GAT attention: ${r.graph.top_neighbours.slice(0, 3).map((n) => `${esc(n.node)} (${esc(n.type)}, ${n.attention.toFixed(2)})`).join(", ")}.</p>`;
  } catch (e) {
    out.innerHTML = `<p class="muted">Scenario failed: ${esc(String(e.message || e))}</p>`;
  } finally { btn.disabled = false; btn.textContent = "Run scenario"; }
}
$("#btn-scn").onclick = () => runScenario();
window.runScenario = runScenario;

/* Model red team: clean -> attacked -> hardened recall, plus bounded RED/BLUE rounds */
$("#btn-mrt").onclick = async () => {
  const b = $("#btn-mrt"), out = $("#mrt-out");
  b.disabled = true; b.innerHTML = `<span class="spin"></span>Attacking…`;
  try {
    const r = await api("/api/model-redteam", {});
    const f = (v) => (v == null || Number.isNaN(v[0]) ? "–" : `${(v[0] * 100).toFixed(0)}%`);
    const a = r.robustness.mean_std, m = r.robustness_mixed.mean_std;
    out.innerHTML = `<table class="dtable"><tr><th>Ring recall on held-out rings</th><th class="n">Clean</th><th class="n">Under attack</th><th class="n">Hardened, fresh attack</th><th class="n">Hardened, clean rings</th><th class="n">Good accounts flagged</th></tr>
      <tr><td>Hardened on attacked rings only</td><td class="n">${f(a.clean)}</td><td class="n bad">${f(a.attacked)}</td><td class="n">${f(a.hardened)}</td><td class="n">${f(a.hardened_clean)}</td><td class="n">${f(a.hardened_legit_flag_rate)}</td></tr>
      <tr class="chosen"><td><b>Mixed hardening</b> (half the rings attacked, half clean)</td><td class="n">${f(m.clean)}</td><td class="n bad">${f(m.attacked)}</td><td class="n">${f(m.hardened)}</td><td class="n">${f(m.hardened_clean)}</td><td class="n">${f(m.hardened_legit_flag_rate)}</td></tr></table>
      <p class="muted small">RED/BLUE rounds (RED picks the perturbation that hurts the current model most; BLUE retrains on a fresh draw):
      ${r.coevolution.slice(1).map((c) => `round ${c.round}: RED drop ${c.red_drop}, cover ${c.red_cover} → recall ${(c.recall_before_blue * 100).toFixed(0)}%; after BLUE ${(c.fresh_attack_recall_after_blue * 100).toFixed(0)}% on a fresh attack, ${(c.clean_recall_after_blue * 100).toFixed(0)}% on clean rings`).join(" · ")}.
      ${esc(r.note)} ${r.accounts} accounts, ${r.edges} links, ${r.fraud} fraud.</p>`;
  } catch (e) { out.innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
  finally { b.disabled = false; b.textContent = "Attack and harden the model"; }
};
