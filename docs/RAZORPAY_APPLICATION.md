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
- Agentic payments need guardrails that live outside the model: mandate checks, risk gates, manual capture and human approval, all enforced in code and covered by 30 tests.
- It uses the Razorpay primitives the way a merchant would: Orders, Standard Checkout, signature verification, payment capture, refunds and webhook HMAC verification.
- It is built for Indian merchants: UPI-first checkout, INR charges, and voice in Hindi, Tamil, Telugu, Bengali, Marathi, Kannada, Gujarati, Malayalam and Punjabi.

## How I used AI to build it
I built it with AI coding agents as pair programmers: architecture, the NumPy GAT with gradient checks, the agent tool loops, and the test suite, then ported the payment layer from PayPal to Razorpay in a day. Results I report are the ones I measured, including where the graph does not beat simple rules (see the README's "Rules vs graph").

## Numbers
- Unseen seeded attacks (₹8,04,989): rules only intercept 41.8%, GAT + rules 84.3%, GAT + hardened policy 94.8%. Linking accounts through UPI IDs and cards lifted fraud-ring interception from about 51% to 80.5%.
- Defense drill: unseen attack money that got through fell from ₹1,85,742 to ₹36,452 (of ₹4,87,889 attempted), for 5 more good customers sent to review (7 of 344) and no good orders blocked.
