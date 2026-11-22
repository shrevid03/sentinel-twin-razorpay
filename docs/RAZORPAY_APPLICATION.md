# Razorpay AI Builders application

The [AI Builders](https://razorpay.com/ai-builders/) page asks for a form, plus a project or GitHub link. Paste from here.

## Project link
https://github.com/shrevid03/sentinel-twin-razorpay

## One line
An AI shopping agent that pays through Razorpay, guarded by a digital twin, a graph attention network and an AI red team, so unsafe agent payments are stopped before money moves.

## What I built (short)
Sentinel Twin is a safety layer for agentic payments. A shopping agent (Gemini tool use, chat and voice in 10 Indian languages) proposes a cart. Before any Razorpay order exists, the buyer's spending mandate is checked in code, not in the prompt, and a digital twin of the merchant's business scores the order with a graph attention network I wrote from scratch in NumPy, catching fraud rings that share devices and IPs. Only an approved order becomes a Razorpay order with manual capture. The buyer pays in Razorpay Checkout; the server verifies the payment signature, checks the payment belongs to that order, re-checks the mandate and captures exactly once. Refunds proposed by an investigator agent run only after a human approves them, and signed Razorpay webhooks (disputes, refunds, failures) feed back into the twin. An adaptive red team attacks a copy of the twin, and a one-click defense drill finds a safer policy, pricing false positives, and proves it on unseen attacks.

## Why it fits Razorpay
- **Risk for AI-led transactions.** Razorpay's Agentic Payments page lists "Advanced Risk & Compliance: real-time fraud detection, authentication, and compliance built for AI-led transactions". Sentinel Twin is a working version of that layer: a graph attention network scores every agent-proposed order on the would-be graph before a Razorpay order exists, and a red team plus defense drill keep the policy honest.
- **A Reserve Pay-style mandate.** The buyer approves a limit once (per order and per month) and the agent spends inside it, like UPI Reserve Pay. It is enforced in code and re-checked at capture. Honest caveat: the demo simulates this consent on standard Razorpay test checkout; it does not call UPI Reserve Pay.
- **A dispute agent.** A signed `payment.dispute.created` webhook opens an investigation automatically. The investigator agent gathers graph evidence, files a case and proposes refunds or restrictions that a human approves: the kind of dispute-management agent Razorpay's Agent Studio lists. Refunds use the real Razorpay Refunds API in test mode; account restrictions are simulated inside the twin.
- Agentic payments need guardrails that live outside the model: mandate checks, risk gates, manual capture and human approval, all enforced in code and covered by 130 tests.
- It uses the Razorpay primitives the way a merchant would: Orders, Standard Checkout, signature verification, payment capture, refunds and webhook HMAC verification.
- It is built for Indian merchants: UPI-first checkout, INR charges, and voice in Hindi, Tamil, Telugu, Bengali, Marathi, Kannada, Gujarati, Malayalam and Punjabi.

## How I used AI to build it
I built it with AI coding agents as pair programmers: architecture, the NumPy GAT with gradient checks, the agent tool loops, and the test suite, then ported the payment layer from PayPal to Razorpay in a day. Results I report are the ones I measured, including where the graph does not beat simple rules (see the README's "Rules vs graph").

## Numbers
- Unseen seeded attacks (₹14,46,140 across eight playbooks, including layering cycles, mule fan-out and collusion): rules only intercept 22.4%, GAT + rules 86.7%, GAT + hardened policy 93.9%.
- Defense drill: unseen attack money that got through fell from ₹1,44,435 to ₹85,851 (of ₹5,92,817 attempted), for 5 more good customers sent to review (8 of 324) and no good orders blocked.
- A new account that verified a phone number at signup that three known fraudsters use: rules approve (0.09), the GAT alone approves, the shipped engine holds it for review (0.63) before checkout. A second account paying from a ring's UPI ID passes pre-checkout and is stopped after authorization, before capture (0.61): not captured, and the customer is told their bank may show a temporary hold that Razorpay reverses automatically.
- Every risk decision is replayable: each link and label carries when it became known and where it came from, and "Replay decision" rebuilds the graph as it was and re-runs the stored model and policy versions. A fraud link added after checkout leaves the replayed decision identical (regression-tested).
- Payments are crash-safe: payment and mandate state in Postgres (integer paise, forward-only states), budget reserved atomically before checkout (two ₹1,500 checkouts against ₹2,000 left: exactly one wins), "capture requested" written before calling Razorpay and a reconciler that records a capture once after a crash. Duplicate and out-of-order webhooks converge to one capture.
- The model choice is measured: on a chronological holdout at the same review volume, a random forest on explicit graph features matched the forest + GAT (86.6% vs 84.0% of fraud rupees at 5% review), but on fresh red-team attacks the GAT engine caught 86.7% vs 77.4% and blocked no good orders (vs ₹29,701), so the GAT stays the default and the forest is one switch away.
- Investigation is evidence-first: the model triages, deterministic graph checks gather evidence with record ids, an LLM copilot only explains that evidence, and a person decides with a reason. RBI-grounded rules are enforced in code and every decision lands in a hash-chained ledger.
- Prompt injection: 20 attempts (multilingual, encoded, indirect through listings and account names) on a throwaway copy, 0 got through; no model can reach a capture, refund, restriction or approval.
