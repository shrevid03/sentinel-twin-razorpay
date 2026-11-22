/* Sentinel Twin sound: an original, fully synthesized sound set (Web Audio API: no audio files, no samples).
   - launch(): the signature sting (~2.2 s): a soft low gong, a warm minor chord that blooms open, a rising airy
     shimmer, and a bright chime that resolves the chord to major. Played on "Open the live twin".
   - chime(), hold(), tick(), whoosh(), sparkle(): quiet UI sounds.
   - ambient(on): optional night bed (wind, a faint low drone, a distant chime now and then). Off by default.
   Rules: nothing plays before a user gesture; a speaker toggle (remembered) mutes everything; the master level is
   modest; automated browsers (navigator.webdriver) and missing Web Audio get silence. */
(() => {
  "use strict";
  const KEY = "st-sound", AKEY = "st-ambient";
  const store = { get: (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch {} } };
  const AC = window.AudioContext || window.webkitAudioContext;
  const silent = !AC || navigator.webdriver;
  let ctx = null, master = null, wet = null, dry = null, gestured = false, ambientNodes = null;
  const enabled = () => !silent && store.get(KEY, "on") === "on";

  function boot() {
    if (ctx || silent) return ctx;
    ctx = new AC();
    master = ctx.createGain(); master.gain.value = 0.22;
    const comp = ctx.createDynamicsCompressor(); comp.threshold.value = -18; comp.ratio.value = 3;
    master.connect(comp).connect(ctx.destination);
    // generated reverb: two seconds of decaying stereo noise, a little darker at the end
    const len = Math.floor(ctx.sampleRate * 2.8), ir = ctx.createBuffer(2, len, ctx.sampleRate);
    for (let c = 0; c < 2; c++) { const d = ir.getChannelData(c); let lp = 0; for (let i = 0; i < len; i++) { const t = i / len; lp += ((Math.random() * 2 - 1) - lp) * (0.6 - t * 0.45); d[i] = lp * Math.pow(1 - t, 2.6); } }
    const conv = ctx.createConvolver(); conv.buffer = ir;
    wet = ctx.createGain(); wet.gain.value = 0.55; dry = ctx.createGain(); dry.gain.value = 0.8;
    wet.connect(conv).connect(master); dry.connect(master);
    return ctx;
  }
  const ready = () => enabled() && gestured && boot() && (ctx.state === "running" || (ctx.resume(), true));
  const now = () => ctx.currentTime;
  const out = (node, w = 0.5) => { const a = ctx.createGain(), b = ctx.createGain(); a.gain.value = 1 - w * 0.4; b.gain.value = w; node.connect(a).connect(dry); node.connect(b).connect(wet); };
  function tone(freq, t0, dur, { type = "sine", gain = 0.2, attack = 0.01, w = 0.5, detune = 0, glideTo = null } = {}) {
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.type = type; o.frequency.setValueAtTime(freq, t0); o.detune.value = detune;
    if (glideTo) o.frequency.exponentialRampToValueAtTime(glideTo, t0 + dur * 0.6);
    g.gain.setValueAtTime(0.0001, t0); g.gain.exponentialRampToValueAtTime(gain, t0 + attack); g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    o.connect(g); out(g, w); o.start(t0); o.stop(t0 + dur + 0.05); return o;
  }
  function noise(t0, dur, { gain = 0.1, type = "bandpass", f0 = 800, f1 = 4000, q = 0.8, w = 0.6, attack = 0.3 } = {}) {
    const len = Math.floor(ctx.sampleRate * dur), buf = ctx.createBuffer(1, len, ctx.sampleRate), d = buf.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
    const s = ctx.createBufferSource(), f = ctx.createBiquadFilter(), g = ctx.createGain();
    s.buffer = buf; f.type = type; f.Q.value = q; f.frequency.setValueAtTime(f0, t0); f.frequency.exponentialRampToValueAtTime(f1, t0 + dur * 0.8);
    g.gain.setValueAtTime(0.0001, t0); g.gain.exponentialRampToValueAtTime(gain, t0 + attack); g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    s.connect(f).connect(g); out(g, w); s.start(t0); s.stop(t0 + dur + 0.05);
  }

  const S = {
    // the signature: gong + minor bloom + airy rise + a chime that turns the chord major
    launch() {
      if (!ready()) return false;
      const t = now() + 0.02;
      tone(55, t, 2.6, { gain: 0.32, attack: 0.02, w: 0.35 });                       // low gong body
      tone(55 * 2.76, t, 1.6, { gain: 0.07, attack: 0.01, w: 0.6 });                // inharmonic gong partials
      tone(55 * 5.4, t, 0.9, { gain: 0.035, attack: 0.005, w: 0.7 });
      [110, 130.81, 164.81, 246.94].forEach((f, i) =>                              // A minor add9 bloom (A C E B)
        tone(f, t + 0.12 + i * 0.07, 2.1, { type: "triangle", gain: 0.06, attack: 0.45, w: 0.6, detune: (i % 2 ? 6 : -6) }));
      noise(t + 0.3, 1.5, { gain: 0.05, f0: 900, f1: 7500, q: 1.2, w: 0.85, attack: 1.0 });  // the airy rise
      [1760, 2637].forEach((f, i) => tone(f, t + 0.6 + i * 0.12, 1.1, { gain: 0.012, attack: 0.6, w: 0.9 }));
      const r = t + 1.45;                                                           // resolve: C# makes it A major
      [138.59, 277.18, 659.25].forEach((f) => tone(f, r, 1.8, { type: "triangle", gain: 0.05, attack: 0.08, w: 0.7 }));
      [1318.5, 1975.5, 2637].forEach((f, i) => tone(f, r + i * 0.04, 1.6, { gain: 0.05 - i * 0.012, attack: 0.004, w: 0.8 }));
      return true;
    },
    chime() { if (!ready()) return; const t = now(); [1568, 2349, 3136].forEach((f, i) => tone(f, t + i * 0.05, 1.2, { gain: 0.045 - i * 0.01, attack: 0.003, w: 0.8 })); },
    hold() {
      if (!ready()) return; const t = now();
      tone(120, t, 0.45, { gain: 0.22, attack: 0.005, w: 0.25, glideTo: 48 });
      noise(t, 0.12, { gain: 0.06, type: "lowpass", f0: 600, f1: 200, w: 0.3, attack: 0.004 });
      [987.8, 1480].forEach((f, i) => tone(f, t + 0.12 + i * 0.06, 0.8, { gain: 0.018, attack: 0.01, w: 0.85 }));
    },
    tick() { if (!ready()) return; const t = now(); tone(2400, t, 0.05, { type: "triangle", gain: 0.02, attack: 0.001, w: 0.15 }); tone(1200, t + 0.012, 0.04, { gain: 0.012, attack: 0.001, w: 0.1 }); },
    whoosh() { if (!ready()) return; noise(now(), 1.3, { gain: 0.07, f0: 260, f1: 2600, q: 0.9, w: 0.7, attack: 0.5 }); },
    sparkle() { if (!ready()) return; const t = now(); [1318.5, 1661.2, 1975.5, 2637].forEach((f, i) => tone(f, t + i * 0.07, 0.7, { gain: 0.03, attack: 0.003, w: 0.85 })); },
    ambient(on) {
      store.set(AKEY, on ? "on" : "off");
      if (!on) { if (ambientNodes) { ambientNodes.stop(); ambientNodes = null; } return; }
      if (!ready() || ambientNodes) return;
      const t = now(), len = ctx.sampleRate * 4, buf = ctx.createBuffer(1, len, ctx.sampleRate), d = buf.getChannelData(0);
      let b = 0; for (let i = 0; i < len; i++) { b = (b + 0.02 * (Math.random() * 2 - 1)) / 1.02; d[i] = b * 3.5; }   // brown noise
      const wind = ctx.createBufferSource(); wind.buffer = buf; wind.loop = true;
      const wf = ctx.createBiquadFilter(); wf.type = "lowpass"; wf.frequency.value = 500;
      const lfo = ctx.createOscillator(), lg = ctx.createGain(); lfo.frequency.value = 0.07; lg.gain.value = 260; lfo.connect(lg).connect(wf.frequency);
      const wg = ctx.createGain(); wg.gain.setValueAtTime(0.0001, t); wg.gain.exponentialRampToValueAtTime(0.05, t + 3);
      wind.connect(wf).connect(wg); out(wg, 0.3);
      const drone = [55, 82.4].map((f) => { const o = ctx.createOscillator(), g = ctx.createGain(); o.frequency.value = f; g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.025, t + 4); o.connect(g); out(g, 0.6); o.start(); return o; });
      wind.start(); lfo.start();
      let timer = 0; const far = () => { timer = setTimeout(() => { if (enabled()) { const tt = now(); tone(1046.5 * (Math.random() < 0.5 ? 1 : 1.5), tt, 2.5, { gain: 0.012, attack: 0.02, w: 1 }); } far(); }, 12000 + Math.random() * 14000); }; far();
      ambientNodes = { stop() { const e = now(); wg.gain.exponentialRampToValueAtTime(0.0001, e + 1); setTimeout(() => { wind.stop(); lfo.stop(); drone.forEach((o) => o.stop()); }, 1200); clearTimeout(timer); } };
    },
    enabled, silent,
    set(on) {
      store.set(KEY, on ? "on" : "off");
      if (!on && ambientNodes) { ambientNodes.stop(); ambientNodes = null; }
      if (ctx) master.gain.setTargetAtTime(on ? 0.22 : 0, ctx.currentTime, 0.05);
      document.querySelectorAll("[data-sound-toggle]").forEach(paint);
    },
  };

  // nothing plays until the visitor has interacted with the page (browser autoplay rules)
  const unlock = () => { gestured = true; if (enabled()) { boot(); ctx.resume(); if (store.get(AKEY, "off") === "on") S.ambient(true); } };
  ["pointerdown", "keydown", "touchstart"].forEach((ev) => addEventListener(ev, unlock, { once: true, passive: true, capture: true }));

  // speaker toggles: any element with data-sound-toggle; data-sound-toggle="ambient" toggles the night bed
  const ICON_ON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9h4l5-4v14l-5-4H4z"/><path d="M16 8.5a5 5 0 0 1 0 7M18.5 6a8.5 8.5 0 0 1 0 12"/></svg>';
  const ICON_OFF = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9h4l5-4v14l-5-4H4z"/><path d="m16 9 5 6m0-6-5 6"/></svg>';
  const ICON_NIGHT = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 14.5A7.5 7.5 0 0 1 9.5 5a7.5 7.5 0 1 0 9.5 9.5z"/></svg>';
  function paint(b) {
    if (b.dataset.soundToggle === "ambient") {
      const on = store.get(AKEY, "off") === "on" && enabled();
      b.innerHTML = ICON_NIGHT; b.setAttribute("aria-pressed", String(on)); b.classList.toggle("on", on);
      b.title = on ? "Night ambience on" : "Night ambience off"; b.setAttribute("aria-label", b.title); b.hidden = silent;
      return;
    }
    const on = enabled();
    b.innerHTML = on ? ICON_ON : ICON_OFF; b.setAttribute("aria-pressed", String(on)); b.classList.toggle("on", on);
    b.title = silent ? "Sound is not available in this browser" : on ? "Sound on (click to mute)" : "Sound muted (click to turn on)";
    b.setAttribute("aria-label", b.title);
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-sound-toggle]"); if (!b) return;
    e.preventDefault(); gestured = true;
    if (b.dataset.soundToggle === "ambient") { if (!enabled()) S.set(true); S.ambient(store.get(AKEY, "off") !== "on"); paint(b); }
    else { S.set(!enabled()); if (enabled()) { boot(); ctx.resume(); S.tick(); } }
    document.querySelectorAll("[data-sound-toggle]").forEach(paint);
  });
  document.querySelectorAll("[data-sound-toggle]").forEach(paint);
  window.STSound = S;
})();
