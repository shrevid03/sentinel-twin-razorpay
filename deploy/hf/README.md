---
title: Sentinel Twin
emoji: 🛡️
colorFrom: green
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
short_description: A risk layer for agent-led payments on Razorpay (demo)
---

# Sentinel Twin

An AI agent shops and pays on Razorpay. A digital twin of the business checks every order with a graph
model and stops the unsafe ones before money moves.

- `/` is the scroll story (3D; a **Lite mode** switch in the header turns the scenery off on slower laptops).
- `/app` is the live twin. A **60-second guided tour** opens on the first visit (or press *Guided tour*).

This Space runs in **demo mode**: no API keys, synthetic data, a labelled mock payment gateway, and a database
that resets when the Space restarts. Source: https://github.com/shrevid03/sentinel-twin-razorpay
