/* Viridian smoke behind the landing hero (WebGL fragment shader: domain-warped fbm), fireflies drawn toward the pointer,
   and a soft cursor glow. Half-resolution, DPR capped, paused when the tab is hidden or the hero has scrolled away.
   No WebGL or reduced motion: the CSS glow blobs stay as the fallback and none of this runs. */
(() => {
  "use strict";
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce) return;
  const hero = document.querySelector(".hero");
  const state = { mx: .5, my: .5, tx: .5, ty: .5, scroll: 0, heroVisible: true, glint: 0 };
  addEventListener("pointermove", (e) => { state.tx = e.clientX / innerWidth; state.ty = 1 - e.clientY / innerHeight; }, { passive: true });
  addEventListener("touchmove", (e) => { const t = e.touches[0]; if (t) { state.tx = t.clientX / innerWidth; state.ty = 1 - t.clientY / innerHeight; } }, { passive: true });
  const onScroll = () => {
    const h = hero ? hero.offsetHeight : innerHeight;
    state.scroll = Math.min(1, scrollY / Math.max(1, h - innerHeight * .5));
    state.heroVisible = scrollY < h + innerHeight * .2;
  };
  addEventListener("scroll", onScroll, { passive: true }); onScroll();
  window.__smoke = state;  // landing.js nudges state.glint when the fraud ring is caught

  // ------------------------------------------------------------------ smoke shader
  const canvas = document.createElement("canvas");
  canvas.className = "smoke-gl"; canvas.setAttribute("aria-hidden", "true");
  const gl = canvas.getContext("webgl", { antialias: false, alpha: false, powerPreference: "low-power", premultipliedAlpha: false });
  let draw = null;
  if (gl) {
    const vs = "attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}";
    const fs = `precision mediump float;
uniform vec2 res; uniform float t; uniform vec2 m; uniform float sc; uniform float gl0;
float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
float n(vec2 p){vec2 i=floor(p),f=fract(p);vec2 u=f*f*(3.-2.*f);
  return mix(mix(h(i),h(i+vec2(1,0)),u.x),mix(h(i+vec2(0,1)),h(i+vec2(1,1)),u.x),u.y);}
float fbm(vec2 p){float v=0.,a=.5;mat2 r=mat2(.8,-.6,.6,.8);for(int i=0;i<5;i++){v+=a*n(p);p=r*p*2.02;a*=.5;}return v;}
void main(){
  vec2 uv=gl_FragCoord.xy/res; vec2 q=uv; q.x*=res.x/res.y;
  vec2 mm=m; mm.x*=res.x/res.y;
  vec2 d=q-mm; float fall=exp(-dot(d,d)*7.);              // the smoke swirls away from the pointer
  q+=normalize(d+1e-4)*fall*.10 + vec2(-d.y,d.x)*fall*.18;
  float tt=t*.045;
  vec2 w1=vec2(fbm(q*1.6+vec2(0.,tt)),fbm(q*1.6+vec2(5.2,1.3)-tt));
  vec2 w2=vec2(fbm(q*1.7+3.*w1+vec2(1.7,9.2)+tt*.6),fbm(q*1.7+3.*w1+vec2(8.3,2.8)-tt*.4));
  float f=fbm(q*1.4+3.4*w2);
  float dens=smoothstep(.25,.95,f)*(.85-sc*.35)+sc*.12;     // thins as the camera pushes through the window
  vec3 base=vec3(.024,.071,.059);                           // #06120F
  vec3 body=vec3(.180,.490,.420);                           // #2E7D6B
  vec3 wisp=vec3(.498,.820,.725);                           // #7FD1B9
  vec3 moonc=vec3(.91,.96,.93);                             // moonlight
  vec3 gold=vec3(.788,.643,.361);                           // #C9A45C
  vec3 coral=vec3(1.,.36,.36);                              // #FF5C5C
  vec3 col=mix(base,body,dens*.5);
  col=mix(col,wisp,smoothstep(.62,1.,f*length(w2))*.38);
  // moon, its halo and soft rays through the mist
  vec2 mp=vec2(.5,.79); vec2 mq=uv-mp; mq.x*=res.x/res.y; float md=length(mq);
  col+=moonc*(exp(-md*9.)*.35+exp(-md*3.2)*.12)*(1.-sc*.6);
  float ang=atan(mq.y,mq.x);
  float rays=pow(max(0.,sin(ang*9.+t*.05)*.5+.5),6.)*exp(-md*2.4)*smoothstep(.02,.2,md);
  col+=moonc*rays*(.10+.12*f)*(1.-sc);
  // a slow diagonal beam of light
  float bm=smoothstep(.10,0.,abs((uv.x*.8-uv.y)+.15-.35*sin(t*.03)));
  col+=moonc*bm*.05*(.5+f);
  // drifting fog banks at two depths, thicker near the floor
  float fog1=fbm(vec2(q.x*1.2+t*.02,q.y*3.))*smoothstep(.55,0.,uv.y);
  float fog2=fbm(vec2(q.x*2.1-t*.035,q.y*5.+3.))*smoothstep(.35,0.,uv.y);
  col=mix(col,vec3(.36,.46,.41),fog1*.22+fog2*.14);
  // ink-in-water gold flecks, carried by the marbled flow
  vec2 gq=(q+w2*.25)*70.; vec2 gc=floor(gq); float gh=h(gc);
  float fl=step(.985,gh)*smoothstep(.45,0.,length(fract(gq)-.5))*(.55+.45*sin(t*1.7+gh*40.));
  col+=gold*fl*.9*smoothstep(.35,.75,f);
  float vein=smoothstep(.015,0.,abs(f-.62))*smoothstep(.5,.9,length(w2));
  col+=gold*vein*.10;
  float g=smoothstep(.55,1.,fbm(q*2.3-tt*1.3))*smoothstep(.45,.9,uv.x)*smoothstep(.1,.55,uv.y);
  col+=coral*g*(.04+gl0*.35);                                // faint coral glint when the ring is caught
  col*=1.-.6*length(uv-vec2(.5,.55));                       // vignette
  gl_FragColor=vec4(col,1.);
}`;
    const sh = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); return gl.getShaderParameter(s, gl.COMPILE_STATUS) ? s : null; };
    const v = sh(gl.VERTEX_SHADER, vs), f = sh(gl.FRAGMENT_SHADER, fs);
    const prog = v && f && gl.createProgram();
    if (prog) {
      gl.attachShader(prog, v); gl.attachShader(prog, f); gl.linkProgram(prog);
      if (gl.getProgramParameter(prog, gl.LINK_STATUS)) {
        gl.useProgram(prog);
        const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
        const loc = gl.getAttribLocation(prog, "p"); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
        const U = (k) => gl.getUniformLocation(prog, k);
        const u = { res: U("res"), t: U("t"), m: U("m"), sc: U("sc"), gl0: U("gl0") };
        const size = () => {
          const k = .5 * Math.min(devicePixelRatio || 1, 1.5);  // half resolution, DPR capped
          canvas.width = Math.max(2, Math.round(innerWidth * k)); canvas.height = Math.max(2, Math.round(innerHeight * k));
          gl.viewport(0, 0, canvas.width, canvas.height);
        };
        size(); addEventListener("resize", size);
        document.body.prepend(canvas);
        document.documentElement.classList.add("has-smoke");
        const t0 = performance.now();
        draw = (now) => {
          gl.uniform2f(u.res, canvas.width, canvas.height); gl.uniform1f(u.t, (now - t0) / 1000);
          gl.uniform2f(u.m, state.mx, state.my); gl.uniform1f(u.sc, state.scroll); gl.uniform1f(u.gl0, state.glint);
          gl.drawArrays(gl.TRIANGLES, 0, 3);
        };
      }
    }
  }

  // ------------------------------------------------------------------ fireflies + cursor glow (2D canvas)
  const fly = document.createElement("canvas"); fly.className = "fireflies"; fly.setAttribute("aria-hidden", "true");
  const ctx = fly.getContext("2d");
  const fine = matchMedia("(hover: hover) and (pointer: fine)").matches;
  const N = innerWidth < 700 ? 18 : 38;
  const flies = [];
  let s = 11; const rnd = () => ((s = (s * 16807) % 2147483647) - 1) / 2147483646;
  const fsize = () => { const k = Math.min(devicePixelRatio || 1, 1.5); fly.width = innerWidth * k; fly.height = innerHeight * k; ctx.setTransform(k, 0, 0, k, 0, 0); };
  fsize(); addEventListener("resize", fsize);
  for (let i = 0; i < N; i++) flies.push({ x: rnd() * innerWidth, y: rnd() * innerHeight, vx: 0, vy: 0, ph: rnd() * 6.28, r: 1 + rnd() * 1.8 });
  const dust = [];  // gold dust: tiny, slow, drifting down and sideways like motes in a moonbeam
  for (let i = 0; i < (innerWidth < 700 ? 30 : 70); i++) dust.push({ x: rnd() * innerWidth, y: rnd() * innerHeight, s: .3 + rnd() * .9, ph: rnd() * 6.28, r: .5 + rnd() * 1.1 });
  document.body.appendChild(fly);
  let glow = null;
  if (fine) { glow = document.createElement("div"); glow.className = "cursor-glow"; glow.setAttribute("aria-hidden", "true"); document.body.appendChild(glow); }
  let px = -999, py = -999;
  addEventListener("pointermove", (e) => { px = e.clientX; py = e.clientY; }, { passive: true });
  const stepFlies = (dt, now) => {
    ctx.clearRect(0, 0, innerWidth, innerHeight);
    if (state.scatter) {  // launch: every firefly bursts outward from the window
      for (const f of flies) { const dx = f.x - innerWidth / 2, dy = f.y - innerHeight * .42, d = Math.hypot(dx, dy) || 1; f.vx += dx / d * 9; f.vy += dy / d * 9; }
      state.scatter = 0;
    }
    for (const f of flies) {
      f.ph += dt * .8;
      f.vx += Math.cos(f.ph * 1.3) * .012; f.vy += Math.sin(f.ph) * .012;
      const dx = px - f.x, dy = py - f.y, d2 = dx * dx + dy * dy;
      if (d2 < 260 * 260 && d2 > 400) { const k = .018 / Math.sqrt(d2) * 9; f.vx += dx * k * .05; f.vy += dy * k * .05; }  // drawn to the pointer
      f.vx *= .96; f.vy *= .96; f.x += f.vx * dt * 60; f.y += f.vy * dt * 60;
      if (f.x < -10) f.x = innerWidth + 10; if (f.x > innerWidth + 10) f.x = -10;
      if (f.y < -10) f.y = innerHeight + 10; if (f.y > innerHeight + 10) f.y = -10;
      const a = .35 + .35 * Math.sin(now / 700 + f.ph * 3);
      ctx.beginPath(); ctx.fillStyle = `rgba(185,240,210,${a.toFixed(3)})`; ctx.shadowColor = "rgba(95,191,159,.9)"; ctx.shadowBlur = 10;
      ctx.arc(f.x, f.y, f.r, 0, 6.283); ctx.fill();
    }
    ctx.shadowBlur = 0;
    for (const g of dust) {
      g.ph += dt * .6; g.y += g.s * dt * 9; g.x += Math.sin(g.ph) * g.s * dt * 7;
      if (g.y > innerHeight + 5) { g.y = -5; g.x = Math.random() * innerWidth; }
      const a = .25 + .35 * Math.max(0, Math.sin(now / 900 + g.ph * 4));
      ctx.fillStyle = `rgba(217,186,121,${a.toFixed(3)})`;
      ctx.fillRect(g.x, g.y, g.r, g.r);
    }
  };

  // ------------------------------------------------------------------ one loop, paused when not needed
  let last = performance.now(), running = true, frame = 0;
  const loop = (now) => {
    if (!running) return;
    const dt = Math.min(.05, (now - last) / 1000); last = now;
    state.mx += (state.tx - state.mx) * .05; state.my += (state.ty - state.my) * .05;
    state.glint *= .985;
    const in3d = document.documentElement.classList.contains("has-3d");  // the 3D hero draws its own sky
    if (draw && state.heroVisible && !in3d) draw(now);
    if (canvas) canvas.style.opacity = state.heroVisible ? (1 - state.scroll * .45).toFixed(3) : "0";
    if (frame++ % 1 === 0) stepFlies(dt, now);
    if (glow) glow.style.transform = `translate(${px - 160}px, ${py - 160}px)`;
    requestAnimationFrame(loop);
  };
  document.addEventListener("visibilitychange", () => {
    running = !document.hidden;
    if (running) { last = performance.now(); requestAnimationFrame(loop); }
  });
  requestAnimationFrame(loop);
})();
