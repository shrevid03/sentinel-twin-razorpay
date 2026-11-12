# Razorpay AI Builders application

The [AI Builders](https://razorpay.com/ai-builders/) page asks for a form, plus a project or GitHub link. Paste from here.

## Project link
https://github.com/shrevid03/sentinel-twin-razorpay

## One line
An AI shopping agent that pays through Razorpay, guarded by a digital twin, a graph attention network and an AI red team, so unsafe agent payments are stopped before money moves.

## What I built (short)
Sentinel Twin is a safety layer for agentic payments. A shopping agent (Gemini tool use, chat and voice in 10 Indian languages) proposes a cart. Before any Razorpay order exists, the buyer's spending mandate is checked in code, not in the prompt, and a digital twin of the merchant's business scores the order with a graph attention network I wrote from scratch in NumPy, catching fraud rings that share devices and IPs. Only an approved order becomes a Razorpay order with manual capture. The buyer pays in Razorpay Checkout; the server verifies the payment signature, checks the payment belongs to that order, re-checks the mandate and captures exactly once. Refunds proposed by an investigator agent run only after a human approves them, and signed Razorpay webhooks (disputes, refunds, failures) feed back into the twin. An adaptive red team attacks a copy of the twin, and a one-click defense drill finds a safer policy, pricing false positives, and proves it on unseen attacks.

## Why it fits Razorpay
- Agentic payments need guardrails that live outside the model: mandate checks, risk gates, manual capture and human approval, all enforced in code and covered by 30 tests.
- It uses the Razorpay primitives the way a merchant would: Orders, Standard Checkout, signature verification, payment capture, refunds and webhook HMAC verification.
- It is built for Indian merchants: UPI-first checkout, INR charges, and voice in Hindi, Tamil, Telugu, Bengali, Marathi, Kannada, Gujarati, Malayalam and Punjabi.

## How I used AI to build it
I built it with AI coding agents as pair programmers: architecture, the NumPy GAT with gradient checks, the agent tool loops, and the test suite, then ported the payment layer from PayPal to Razorpay in a day. Results I report are the ones I measured, including where the graph does not beat simple rules (see the README's "Rules vs graph").

## Numbers
- Unseen seeded attacks ($9,409): rules only intercept 42.5%, GAT + rules 72-81%, GAT + hardened policy 95.3%.
- Defense drill: unseen attack money that got through fell from $2,211 to $434, for one good customer sent to review.
