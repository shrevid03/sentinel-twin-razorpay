/* Sentinel Twin: the landing hero in real-time 3D (three.js + postprocessing, vendored and pinned).
   A wet gothic window at night: ashlar stone after rain (procedural albedo, normal, roughness and puddle masks;
   clearcoat for wetness), an extruded pointed arch with tracery, moonlight streaming through it and casting the
   tracery's shadow on a wet stone floor, 3D ivy (tube vines + instanced curved leaves with wind sway, dew drops
   and the odd drip), rain streaks, raindrops running down the glass, fireflies and mist. Post: god rays from the
   moon, bloom, ACES filmic tone mapping, vignette and film grain. Adaptive quality scales the pixel ratio,
   shadows and post effects to hold the frame rate. Every texture is generated here; no image assets.
   Loaded only on wide screens that allow motion and have WebGL 2; everything else keeps the 2D artwork. */
import * as THREE from "three";
import { EffectComposer, RenderPass, EffectPass, BloomEffect, GodRaysEffect, VignetteEffect, NoiseEffect,
  ToneMappingEffect, ToneMappingMode, BlendFunction, HueSaturationEffect, BrightnessContrastEffect } from "postprocessing";

const canvas = document.getElementById("gl3d");
const hero = document.querySelector(".hero");
const gl2 = canvas && canvas.getContext("webgl2", { antialias: false, powerPreference: "high-performance", stencil: false });
if (!canvas || !gl2) throw new Error("no WebGL2: keeping the 2D hero");

// ------------------------------------------------------------------ procedural textures (value-noise fbm)
const rng = (() => { let s = 1234567; return () => ((s = (s * 16807) % 2147483647) - 1) / 2147483646; })();
function noise2(N, freq, seedOff = 0) {  // tileable value noise, N x N, returns Float32Array 0..1
  const G = freq, g = new Float32Array((G + 1) * (G + 1)).map(() => rng());
  const out = new Float32Array(N * N);
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
    const X = (x / N) * G, Y = (y / N) * G, xi = X | 0, yi = Y | 0, xf = X - xi, yf = Y - yi;
    const at = (i, j) => g[(j % G) * (G + 1) + (i % G)];
    const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf);
    out[y * N + x] = (at(xi, yi) * (1 - u) + at(xi + 1, yi) * u) * (1 - v) + (at(xi, yi + 1) * (1 - u) + at(xi + 1, yi + 1) * u) * v;
  }
  return out;
}
function fbm(N, base, oct) { const r = new Float32Array(N * N); let a = .5, t = 0; for (let o = 0; o < oct; o++) { const n = noise2(N, base << o); for (let i = 0; i < r.length; i++) r[i] += n[i] * a; t += a; a *= .5; } for (let i = 0; i < r.length; i++) r[i] /= t; return r; }
function toTex(N, fill, srgb) {
  const c = document.createElement("canvas"); c.width = c.height = N; const x = c.getContext("2d"), img = x.createImageData(N, N);
  for (let i = 0; i < N * N; i++) { const [r, g, b] = fill(i); img.data[i * 4] = r; img.data[i * 4 + 1] = g; img.data[i * 4 + 2] = b; img.data[i * 4 + 3] = 255; }
  x.putImageData(img, 0, 0);
  const t = new THREE.CanvasTexture(c); t.wrapS = t.wrapT = THREE.RepeatWrapping; t.anisotropy = 8;
  t.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace; return t;
}
function normalFrom(N, h, k) {  // Sobel height -> tangent-space normal map
  const at = (x, y) => h[((y + N) % N) * N + ((x + N) % N)];
  return toTex(N, (i) => {
    const x = i % N, y = (i / N) | 0;
    const dx = (at(x + 1, y - 1) + 2 * at(x + 1, y) + at(x + 1, y + 1)) - (at(x - 1, y - 1) + 2 * at(x - 1, y) + at(x - 1, y + 1));
    const dy = (at(x - 1, y + 1) + 2 * at(x, y + 1) + at(x + 1, y + 1)) - (at(x - 1, y - 1) + 2 * at(x, y - 1) + at(x + 1, y - 1));
    let nx = -dx * k, ny = -dy * k, nz = 1; const l = Math.hypot(nx, ny, nz); nx /= l; ny /= l; nz /= l;
    return [(nx * .5 + .5) * 255, (ny * .5 + .5) * 255, (nz * .5 + .5) * 255];
  }, false);
}
// ashlar stone: blocks with bevelled mortar, per-block tone, pitting; wet: darker albedo and low roughness in streaks/puddles
function stoneSet(N, blockW, blockH, wetness) {
  const n1 = fbm(N, 4, 5), n2 = fbm(N, 16, 3), streak = noise2(N, 8), h = new Float32Array(N * N), tone = new Float32Array(64).map(() => rng());
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
    const row = Math.floor(y / blockH), off = (row % 2) * blockW / 2, bx = Math.floor((x + off) / blockW);
    const mx = Math.min((x + off) % blockW, blockW - (x + off) % blockW), my = Math.min(y % blockH, blockH - y % blockH);
    const edge = Math.min(1, Math.min(mx, my) / 5);  // bevel toward the mortar joint
    const i = y * N + x; h[i] = edge * (.75 + .25 * n1[i]) - (n2[i] > .72 ? .15 : 0) + tone[(row * 7 + bx * 13) % 64] * .05;
  }
  const albedo = toTex(N, (i) => {
    const x = i % N, y = (i / N) | 0, row = Math.floor(y / blockH), bx = Math.floor((x + (row % 2) * blockW / 2) / blockW);
    const t = tone[(row * 7 + bx * 13) % 64], wet = wetness * (.55 + .45 * streak[(x * 7 + ((y / 6) | 0) * N) % (N * N)]);
    let l = 52 + t * 28 + n1[i] * 30 - (h[i] < .25 ? 22 : 0); l *= 1 - .35 * wet;   // wet stone is darker
    const moss = Math.max(0, n2[i] - .58) * 2.2;
    return [l * (.82 - moss * .2), l * (.95 + moss * .25), l * (.86 - moss * .05)];
  }, true);
  const rough = toTex(N, (i) => {
    const x = i % N, y = (i / N) | 0, s = streak[(x * 7 + ((y / 6) | 0) * N) % (N * N)];
    let r = .78 - wetness * (.35 + .4 * s) + (h[i] < .25 ? .1 : 0); r = Math.max(.08, Math.min(1, r)) * 255; return [r, r, r];
  }, false);
  return { albedo, normal: normalFrom(N, h, 2.4), rough };
}

// ------------------------------------------------------------------ renderer, scene, camera
const renderer = new THREE.WebGLRenderer({ canvas, context: gl2, antialias: false, powerPreference: "high-performance" });
let dpr = Math.min(devicePixelRatio || 1, 1.5);
renderer.setPixelRatio(dpr);
renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.NoToneMapping;  // tone mapping happens in the post chain
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x05100d);
scene.fog = new THREE.FogExp2(0x0c1a16, 0.006);  // only a faint distance falloff for depth
const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 200);
camera.position.set(0, 4.3, 15.5);

// sky behind the window: smoky, moonlit (a shader on a far plane)
const skyMat = new THREE.ShaderMaterial({
  uniforms: { t: { value: 0 } }, depthWrite: false, fog: false,
  vertexShader: "varying vec2 vUv; void main(){ vUv=uv; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }",
  fragmentShader: `varying vec2 vUv; uniform float t;
    float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
    float n(vec2 p){vec2 i=floor(p),f=fract(p);vec2 u=f*f*(3.-2.*f);return mix(mix(h(i),h(i+vec2(1,0)),u.x),mix(h(i+vec2(0,1)),h(i+vec2(1,1)),u.x),u.y);}
    float fbm(vec2 p){float v=0.,a=.5;for(int i=0;i<5;i++){v+=a*n(p);p*=2.03;a*=.5;}return v;}
    void main(){ vec2 q=vUv*vec2(3.,2.); float tt=t*.03;
      vec2 w=vec2(fbm(q+tt),fbm(q+vec2(5.2,1.3)-tt)); float f=fbm(q+2.6*w);
      vec3 col=mix(vec3(.008,.02,.018),vec3(.05,.13,.11),smoothstep(.35,.95,f)*.8);
      col+=vec3(.5,.62,.56)*smoothstep(.85,1.1,f*length(w))*.18;
      float md=length((vUv-vec2(.5,.62))*vec2(1.6,1.));
      col+=vec3(.6,.7,.66)*exp(-md*3.)*.025;  // the moon itself carries the light now
      gl_FragColor=vec4(col,1.); }`,
});
const sky = new THREE.Mesh(new THREE.PlaneGeometry(120, 70), skyMat); sky.position.set(0, 14, -45); scene.add(sky);
const moonTex = (() => {
  const Wd = 1024, Ht = 512, h = new Float32Array(Wd * Ht), alb = new Float32Array(Wd * Ht);
  const low = fbm(512, 3, 4), mid = fbm(512, 12, 4);
  const at = (a, x, y) => a[((y % 512) * 512) + (x % 512)];
  for (let y = 0; y < Ht; y++) for (let x = 0; x < Wd; x++) {
    const i = y * Wd + x, m = at(low, x >> 1, y), d = at(mid, x >> 1, y);
    const mare = Math.min(1, Math.max(0, (m - .47 + (d - .5) * .12) * 4));  // dark basaltic seas, with soft irregular edges
    alb[i] = (.80 - mare * .36) * (.9 + d * .2); h[i] = -mare * .2 + d * .06;
  }
  const craters = [];
  for (let k = 0; k < 900; k++) { const r = 1 + Math.pow(rng(), 4) * 34; craters.push([rng() * Wd, (.06 + rng() * .88) * Ht, r, .25 + rng() * .75, rng() * 6.283, .75 + rng() * .5]); }
  const wob = fbm(256, 8, 3);
  craters.forEach(([cx, cy, r, age, rot, ell]) => {  // soft bowls and rims; old craters are shallow and faint, outlines wobble
    const x0 = Math.max(0, cx - r * 1.8 | 0), x1 = Math.min(Wd - 1, cx + r * 1.8 | 0), y0 = Math.max(0, cy - r * 1.8 | 0), y1 = Math.min(Ht - 1, cy + r * 1.8 | 0);
    const depth = .3 * age * Math.min(1, r / 10), cs = Math.cos(rot), sn = Math.sin(rot);
    for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {
      const dx = x - cx, dy = y - cy, ux = (dx * cs + dy * sn), uy = (-dx * sn + dy * cs) * ell;
      const w = 1 + (wob[((y & 255) << 8) + (x & 255)] - .5) * .35;
      const d = Math.hypot(ux, uy) / (r * w), i = y * Wd + x;
      const bowl = d < 1 ? (1 - d * d) : 0, rim = Math.exp(-Math.pow((d - 1.05) / .18, 2));
      h[i] += -depth * bowl * bowl + depth * .45 * rim;
      alb[i] *= 1 - .05 * bowl * age + .05 * rim * age;
    }
  });
  const fine = fbm(512, 32, 3);
  for (let y = 0; y < Ht; y++) for (let x = 0; x < Wd; x++) { const i = y * Wd + x; alb[i] *= .93 + .14 * fine[((y % 512) * 512) + (x % 512)]; }
  for (let k = 0; k < 6; k++) {  // a few young craters with bright ray systems
    const cx = rng() * Wd, cy = (.2 + rng() * .6) * Ht, n = 14 + (rng() * 10 | 0);
    for (let j = 0; j < n; j++) { const a = rng() * 6.283, L = 30 + rng() * 120;
      for (let t = 6; t < L; t++) { const x = (cx + Math.cos(a) * t) | 0, y = (cy + Math.sin(a) * t * .5) | 0; if (x < 0 || y < 0 || x >= Wd || y >= Ht) break; alb[y * Wd + x] = Math.min(1, alb[y * Wd + x] + .12 * (1 - t / L)); } }
  }
  const c = document.createElement("canvas"); c.width = Wd; c.height = Ht; const x = c.getContext("2d"), img = x.createImageData(Wd, Ht);
  for (let i = 0; i < Wd * Ht; i++) { const v = Math.min(1, alb[i]); img.data[i * 4] = v * 226; img.data[i * 4 + 1] = v * 221; img.data[i * 4 + 2] = v * 204; img.data[i * 4 + 3] = 255; }
  x.putImageData(img, 0, 0);
  const map = new THREE.CanvasTexture(c); map.colorSpace = THREE.SRGBColorSpace; map.anisotropy = 8;
  const nc = document.createElement("canvas"); nc.width = Wd; nc.height = Ht; const nx = nc.getContext("2d"), ni = nx.createImageData(Wd, Ht);
  const H = (xx, yy) => h[Math.min(Ht - 1, Math.max(0, yy)) * Wd + ((xx + Wd) % Wd)];
  for (let y = 0; y < Ht; y++) for (let xx = 0; xx < Wd; xx++) {
    const dx = (H(xx + 1, y) - H(xx - 1, y)) * 2.2, dy = (H(xx, y + 1) - H(xx, y - 1)) * 2.2, l = Math.hypot(dx, dy, 1), i = (y * Wd + xx) * 4;
    ni.data[i] = (-dx / l * .5 + .5) * 255; ni.data[i + 1] = (-dy / l * .5 + .5) * 255; ni.data[i + 2] = (1 / l * .5 + .5) * 255; ni.data[i + 3] = 255;
  }
  nx.putImageData(ni, 0, 0);
  return { map, normal: new THREE.CanvasTexture(nc) };
})();
const moonMat = new THREE.ShaderMaterial({
  uniforms: { map: { value: moonTex.map }, nmap: { value: moonTex.normal }, sun: { value: new THREE.Vector3(-.35, .15, 1).normalize() } }, fog: false,
  vertexShader: `varying vec2 vUv; varying vec3 vN; varying vec3 vV; varying vec3 vT;
    void main(){ vUv=uv; vN=normalize(normalMatrix*normal); vec4 mv=modelViewMatrix*vec4(position,1.); vV=normalize(-mv.xyz);
      vT=normalize(normalMatrix*vec3(-normal.z,0.,normal.x)); gl_Position=projectionMatrix*mv; }`,
  fragmentShader: `uniform sampler2D map, nmap; uniform vec3 sun; varying vec2 vUv; varying vec3 vN; varying vec3 vV; varying vec3 vT;
    void main(){ vec3 n=normalize(vN), t=normalize(vT), b=cross(n,t); vec3 tn=texture2D(nmap,vUv).xyz*2.-1.;
      vec3 N=normalize(tn.x*t+tn.y*b+tn.z*n); vec3 L=normalize((viewMatrix*vec4(sun,0.)).xyz);
      float lam=max(dot(N,L),0.), mu0=max(dot(n,L),0.), mu=max(dot(n,vV),.001);
      float ls=mu0/(mu0+mu);                                  // Lommel-Seeliger: the moon's gentle, realistic limb
      vec3 c=texture2D(map,vUv).rgb*(.6*ls*2.+.4*lam)*.86;
      gl_FragColor=vec4(c,1.);
      #include <colorspace_fragment>
    }`,
});
const moon = new THREE.Mesh(new THREE.SphereGeometry(2.5, 128, 96), moonMat);
moon.position.set(4.8, 8.7, -40); moon.rotation.y = -.6; scene.add(moon);  // seen through the right lancet, clear of the tracery
// a gentle, natural halo and thin cloud drifting across the moon's face
const haloTex = (() => { const c = document.createElement("canvas"); c.width = c.height = 256; const x = c.getContext("2d"); const g = x.createRadialGradient(128, 128, 40, 128, 128, 128);
  g.addColorStop(0, "rgba(220,232,226,.10)"); g.addColorStop(.35, "rgba(170,195,185,.035)"); g.addColorStop(1, "rgba(120,150,140,0)"); x.fillStyle = g; x.fillRect(0, 0, 256, 256); return new THREE.CanvasTexture(c); })();
const moonHalo = new THREE.Sprite(new THREE.SpriteMaterial({ map: haloTex, transparent: true, depthWrite: false, fog: false })); moonHalo.scale.set(13, 13, 1); moonHalo.position.set(4.8, 8.7, -41); scene.add(moonHalo);

// lights: cool moonlight from behind the window, faint warm fill, sage sky
scene.add(new THREE.HemisphereLight(0x7f9f92, 0x101c17, 0.56));  // a very faint cool fill: brick, mortar and ivy read in the mid-tones
const rimL = new THREE.PointLight(0x8fc9b5, 4, 14, 2); rimL.position.set(-5.5, 7, 3); scene.add(rimL);   // faint rim on the arch and ivy
const rimR = new THREE.PointLight(0x8fc9b5, 3.5, 14, 2); rimR.position.set(5.5, 6, 3); scene.add(rimR);
const front = new THREE.SpotLight(0xa2c2b5, 48, 42, .85, 1, 1.55); front.position.set(-7, 9, 14); front.target.position.set(0, 4, 0); scene.add(front, front.target);  // cool moonlit wash on the wall's face
const moonLight = new THREE.DirectionalLight(0xc4d8d0, 1.25);
moonLight.position.set(0.6, 14, -18); moonLight.target.position.set(0, 0, 6);
moonLight.castShadow = true; moonLight.shadow.mapSize.set(2048, 2048);
Object.assign(moonLight.shadow.camera, { left: -9, right: 9, top: 12, bottom: -4, near: 1, far: 50 });
moonLight.shadow.bias = -0.0004; moonLight.shadow.normalBias = 0.03; moonLight.shadow.radius = 5;
scene.add(moonLight, moonLight.target);
const fill = new THREE.PointLight(0xd9a070, 1.1, 14, 2); fill.position.set(-7, 2, 8); scene.add(fill);
const rim = new THREE.PointLight(0x6fa898, 7, 14, 2); rim.position.set(0, 5, -2.5); scene.add(rim);

// ------------------------------------------------------------------ the wall with its pointed-arch opening
const W = 2.3, SPRING = 4.8, R = 2 * W, APEX = SPRING + Math.sqrt(R * R - W * W);
const archPath = (w, spring, path) => {  // equilateral pointed arch, left jamb up and over to the right jamb
  const r = 2 * w, a0 = Math.atan2(Math.sqrt(r * r - w * w), -w);
  path.moveTo(-w, 0); path.lineTo(-w, spring);
  path.absarc(w, spring, r, Math.PI, a0, true);
  path.absarc(-w, spring, r, Math.PI - a0, 0, true);
  path.lineTo(w, 0);
  return path;
};
const stone = stoneSet(512, 128, 64, .32);
const wallMat = new THREE.MeshPhysicalMaterial({ map: stone.albedo, normalMap: stone.normal, roughnessMap: stone.rough, roughness: .82, clearcoat: .2, clearcoatRoughness: .45,
  normalScale: new THREE.Vector2(1.1, 1.1), color: 0xb2c5bc });
[stone.albedo, stone.normal, stone.rough].forEach((t) => t.repeat.set(1 / 4, 1 / 4));
const wallShape = new THREE.Shape(); wallShape.moveTo(-26, 0); wallShape.lineTo(26, 0); wallShape.lineTo(26, 22); wallShape.lineTo(-26, 22); wallShape.lineTo(-26, 0);
wallShape.holes.push(archPath(W, SPRING, new THREE.Path()));
const wall = new THREE.Mesh(new THREE.ExtrudeGeometry(wallShape, { depth: 1.1, bevelEnabled: false, curveSegments: 48 }), wallMat);
wall.position.z = -1.1; wall.castShadow = wall.receiveShadow = true; scene.add(wall);
// moulded surround: an arch ring, bevelled
const ring = new THREE.Shape(); archPath(W + .55, SPRING, ring); ring.lineTo(-W - .55, 0);
ring.holes.push(archPath(W, SPRING, new THREE.Path()));
const mould = new THREE.Mesh(new THREE.ExtrudeGeometry(ring, { depth: .35, bevelEnabled: true, bevelThickness: .12, bevelSize: .1, bevelSegments: 4, curveSegments: 64 }), wallMat);
mould.position.z = 0; mould.castShadow = mould.receiveShadow = true; scene.add(mould);
// tracery: a mullion, two lancets, a roundel with a quatrefoil
const traceryMat = new THREE.MeshPhysicalMaterial({ map: stone.albedo, normalMap: stone.normal, roughness: .66, clearcoat: .2, clearcoatRoughness: .45, color: 0x7f978b });
const trac = new THREE.Group(); trac.position.z = -.55; scene.add(trac);
const tube = (pts, r) => new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 64, r, 10, false), traceryMat);
trac.add(new THREE.Mesh(new THREE.BoxGeometry(.24, APEX - 2.6, .3), traceryMat).translateY((APEX - 2.6) / 2));
const lancet = (cx) => { const p = new THREE.Path(); archPath(W / 2, SPRING + .6, p); return tube(p.getPoints(40).map((v) => new THREE.Vector3(v.x + cx, v.y, 0)), .085); };
trac.add(lancet(-W / 2), lancet(W / 2));
const rc = new THREE.Vector3(0, APEX - 1.75, 0);
const torus = (r, x, y, t = .08) => { const m = new THREE.Mesh(new THREE.TorusGeometry(r, t, 12, 64), traceryMat); m.position.set(x, y, 0); return m; };
trac.add(torus(.85, rc.x, rc.y, .1));
[[0, .36], [0, -.36], [.36, 0], [-.36, 0]].forEach(([dx, dy]) => trac.add(torus(.33, rc.x + dx, rc.y + dy, .05)));
trac.add(new THREE.Mesh(new THREE.BoxGeometry(2 * W, .16, .26), traceryMat).translateY(2.2));
trac.traverse((o) => { if (o.isMesh) o.castShadow = o.receiveShadow = true; });

// glass with raindrops running down it (procedural drops and trails, lit by the moon)
const glassShape = archPath(W, SPRING, new THREE.Shape());
const glassMat = new THREE.ShaderMaterial({
  transparent: true, depthWrite: false, uniforms: { t: { value: 0 } },
  vertexShader: "varying vec2 vP; void main(){ vP=position.xy; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }",
  fragmentShader: `varying vec2 vP; uniform float t;
    float h(vec2 p){return fract(sin(dot(p,vec2(12.9898,78.233)))*43758.5453);}
    float drops(vec2 uv, float s){ uv*=s; vec2 id=floor(uv); float r=h(id); uv.y+=t*(.25+r*.5)+r*10.; id=floor(uv);
      vec2 f=fract(uv)-.5; float k=h(id+3.1); f.x+=(k-.5)*.6; float d=length(f*vec2(1.,1.6));
      float drop=smoothstep(.16,.05,d)*step(.55,k); float trail=smoothstep(.06,.0,abs(f.x))*smoothstep(.5,-.1,f.y)*step(.55,k)*.35; return drop+trail; }
    void main(){ vec2 uv=vP*.55; float d=drops(uv,3.)*.8;
      vec3 c=vec3(.72,.86,.8); gl_FragColor=vec4(c*d,d*.2); }`,
});
const glass = new THREE.Mesh(new THREE.ShapeGeometry(glassShape, 48), glassMat); glass.position.z = -.6; scene.add(glass);  // a few drops on the panes

// wet stone floor: large flags, puddles (very low roughness), clearcoat; reflects the moon and lights
const floorSet = stoneSet(512, 170, 170, .38);
[floorSet.albedo, floorSet.normal, floorSet.rough].forEach((t) => t.repeat.set(5, 6));
const floor = new THREE.Mesh(new THREE.PlaneGeometry(60, 70), new THREE.MeshPhysicalMaterial({
  map: floorSet.albedo, normalMap: floorSet.normal, normalScale: new THREE.Vector2(.45, .45), roughnessMap: floorSet.rough, roughness: .86, clearcoat: .1, clearcoatRoughness: .55, color: 0x56665e }));
floor.rotation.x = -Math.PI / 2; floor.position.z = -5; floor.receiveShadow = true; scene.add(floor);
// an environment for wet reflections: a tiny sky with a bright moon, prefiltered
const pmrem = new THREE.PMREMGenerator(renderer);
const envScene = new THREE.Scene(); envScene.background = new THREE.Color(0x07140f);
const envMoon = new THREE.Mesh(new THREE.SphereGeometry(3, 16, 16), new THREE.MeshBasicMaterial({ color: 0xdfeee7 })); envMoon.position.set(0, 12, -25); envScene.add(envMoon);
const envGlow = new THREE.Mesh(new THREE.SphereGeometry(30, 16, 16), new THREE.MeshBasicMaterial({ color: 0x163a30, side: THREE.BackSide })); envScene.add(envGlow);
scene.environment = pmrem.fromScene(envScene, 0.04).texture; scene.environmentIntensity = .38;  // a soft, blurred hint of reflection

// ------------------------------------------------------------------ ivy: tube vines + instanced leaves (wind in the vertex shader)
const vineMat = new THREE.MeshStandardMaterial({ color: 0x2a3a2a, roughness: .7 });
const leafShape = new THREE.Shape();
leafShape.moveTo(0, 0); leafShape.bezierCurveTo(-.08, .02, -.2, .05, -.22, .17); leafShape.bezierCurveTo(-.14, .17, -.1, .22, -.08, .31);
leafShape.bezierCurveTo(-.03, .27, .03, .27, .08, .31); leafShape.bezierCurveTo(.1, .22, .14, .17, .22, .17); leafShape.bezierCurveTo(.2, .05, .08, .02, 0, 0);
const leafGeo = new THREE.ShapeGeometry(leafShape, 8);
{ const p = leafGeo.attributes.position; for (let i = 0; i < p.count; i++) { const x = p.getX(i), y = p.getY(i); p.setZ(i, -1.1 * x * x + .25 * y * y); } leafGeo.computeVertexNormals(); }
const windU = { value: 0 };
const leafMat = new THREE.MeshPhysicalMaterial({ side: THREE.DoubleSide, roughness: .58, clearcoat: .25, clearcoatRoughness: .4, sheen: .5, sheenRoughness: .7, sheenColor: new THREE.Color(0x5f8f7f), color: 0xffffff });
leafMat.onBeforeCompile = (sh) => {
  sh.uniforms.uTime = windU;
  sh.vertexShader = "uniform float uTime;\n" + sh.vertexShader.replace("#include <begin_vertex>", `#include <begin_vertex>
    float ph = instanceMatrix[3].x * 1.7 + instanceMatrix[3].y * 2.3;
    transformed.z += sin(uTime * 1.6 + ph) * .035 * position.y * 3.;
    transformed.x += sin(uTime * 1.1 + ph * 1.3) * .02 * position.y * 3.;`);
};
const vineCurves = [];
const archCurve = (side, inset, share) => {  // up one jamb of the moulding and part-way over the arch
  let pts = archPath(W + .3 + inset, SPRING, new THREE.Path()).getSpacedPoints(80);
  if (side > 0) pts = pts.reverse();
  pts = pts.slice(0, Math.floor(pts.length * share)).filter((_, i) => i % 3 === 0);
  return new THREE.CatmullRomCurve3(pts.map((v) => new THREE.Vector3(v.x + (rng() - .5) * .12, v.y, .42 + rng() * .12)));
};
vineCurves.push(archCurve(-1, .05, .62), archCurve(1, .12, .45));
for (let i = 0; i < 5; i++) {  // vines spreading over the wall beside the window
  const side = i % 2 ? 1 : -1, x0 = side * (W + 1.2 + rng() * 4), pts = [];
  for (let y = 0; y < 9 + rng() * 5; y += .8) pts.push(new THREE.Vector3(x0 + Math.sin(y * .7 + i) * .8 + side * y * .15, y, .02 + rng() * .05));
  vineCurves.push(new THREE.CatmullRomCurve3(pts));
}
const vineGroup = new THREE.Group(); scene.add(vineGroup);
vineCurves.forEach((c) => { const m = new THREE.Mesh(new THREE.TubeGeometry(c, 120, .028, 6, false), vineMat); m.castShadow = true; vineGroup.add(m); });
const LEAVES = 2200;
const leaves = new THREE.InstancedMesh(leafGeo, leafMat, LEAVES); leaves.castShadow = true; leaves.receiveShadow = true;
const dummy = new THREE.Object3D(), colr = new THREE.Color(), leafTips = [];
const hues = [0x1c4a3f, 0x22503f, 0x2b6253, 0x395c48, 0x44684d, 0x6a7448];
for (let i = 0; i < LEAVES; i++) {
  const c = vineCurves[i % vineCurves.length], u = rng(), p = c.getPointAt(u), tng = c.getTangentAt(u);
  const off = new THREE.Vector3((rng() - .5) * .45, (rng() - .5) * .3, .04 + rng() * .14);
  dummy.position.copy(p).add(off);
  dummy.lookAt(dummy.position.clone().add(new THREE.Vector3(off.x * .4, .3 + tng.y * .2, 1)));
  dummy.rotateZ((rng() - .5) * 1.6); dummy.rotateX(-.4 + rng() * .5);
  const s = .55 + rng() * .75; dummy.scale.set(s, s, s); dummy.updateMatrix(); leaves.setMatrixAt(i, dummy.matrix);
  const hcol = hues[rng() < .06 ? 5 : (rng() * 5) | 0]; colr.setHex(hcol).offsetHSL((rng() - .5) * .03, 0, (rng() - .5) * .06); leaves.setColorAt(i, colr);
  if (i % 9 === 0) leafTips.push(dummy.position.clone());
}
scene.add(leaves);
// dew: tiny glossy droplets resting on leaves
const dew = new THREE.InstancedMesh(new THREE.SphereGeometry(.012, 8, 6), new THREE.MeshPhysicalMaterial({ color: 0xdfeee7, roughness: .1, metalness: 0, clearcoat: .6, emissive: 0x16302a, emissiveIntensity: .25 }), 120);
for (let i = 0; i < 120; i++) { const p = leafTips[i % leafTips.length]; dummy.position.set(p.x + (rng() - .5) * .08, p.y + (rng() - .5) * .08, p.z + .05); dummy.scale.setScalar(.6 + rng()); dummy.updateMatrix(); dew.setMatrixAt(i, dummy.matrix); }
scene.add(dew);  // a few tiny glints
// drips falling from leaf tips
const drips = new THREE.InstancedMesh(new THREE.SphereGeometry(.02, 6, 6), new THREE.MeshBasicMaterial({ color: 0xcfe6dc }), 14);
const dripState = Array.from({ length: 14 }, (_, i) => ({ from: leafTips[(i * 13) % leafTips.length], y: 0, v: 0, wait: rng() * 4 }));


// rain streaks around the camera, lit by the moon
const RAIN = 1400, rainGeo = new THREE.BufferGeometry(), rp = new Float32Array(RAIN * 6);
for (let i = 0; i < RAIN; i++) { const x = (rng() - .5) * 16, y = rng() * 18, z = -2.6 - rng() * 30; rp.set([x, y, z, x - .01, y - .32, z], i * 6); }  // outdoors only, behind the wall
rainGeo.setAttribute("position", new THREE.BufferAttribute(rp, 3));
const rain = new THREE.LineSegments(rainGeo, new THREE.LineBasicMaterial({ color: 0xa9c8bc, transparent: true, opacity: .13, depthWrite: false })); scene.add(rain);  // falls outside the window only 
// fireflies (bloom makes them glow)
const FF = 28, ffGeo = new THREE.BufferGeometry(), ffp = new Float32Array(FF * 3), ffPh = new Float32Array(FF);
for (let i = 0; i < FF; i++) { ffp.set([(rng() - .5) * 14, .5 + rng() * 7, -2 + rng() * 12], i * 3); ffPh[i] = rng() * 6.28; }
ffGeo.setAttribute("position", new THREE.BufferAttribute(ffp, 3));
const ffSprite = (() => { const c = document.createElement("canvas"); c.width = c.height = 64; const x = c.getContext("2d"); const g = x.createRadialGradient(32, 32, 0, 32, 32, 32);
  g.addColorStop(0, "rgba(230,255,240,1)"); g.addColorStop(.25, "rgba(160,240,200,.8)"); g.addColorStop(1, "rgba(60,160,120,0)"); x.fillStyle = g; x.fillRect(0, 0, 64, 64); return new THREE.CanvasTexture(c); })();
const fireflies = new THREE.Points(ffGeo, new THREE.PointsMaterial({ size: .15, map: ffSprite, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, color: 0xb9d8c6 }));
scene.add(fireflies);
// ground mist: soft noisy sprites drifting low
const mistTex = (() => { const N = 128, n = fbm(N, 4, 4); return toTex(N, (i) => { const x = i % N - 64, y = ((i / N) | 0) - 64, f = Math.max(0, 1 - Math.hypot(x, y) / 64); const v = n[i] * f * 255; return [v, v, v]; }, false); })();
const mist = [];
const fogNoise = (() => { const N = 256, n = fbm(N, 4, 5); const t = toTex(N, (i) => { const v = n[i] * 255; return [v, v, v]; }, false); t.wrapS = t.wrapT = THREE.RepeatWrapping; return t; })();
const fogMat = (tint, alpha) => new THREE.ShaderMaterial({  // two lookups into a tileable noise texture: cheap even when layered
  transparent: true, depthWrite: false, fog: false, uniforms: { t: { value: 0 }, tint: { value: new THREE.Color(tint) }, a: { value: alpha }, seed: { value: rng() * 10 }, nz: { value: fogNoise } },
  vertexShader: "varying vec2 vUv; void main(){ vUv=uv; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }",
  fragmentShader: `varying vec2 vUv; uniform float t, a, seed; uniform vec3 tint; uniform sampler2D nz;
    void main(){ vec2 q=vUv*vec2(1.6,.55)+vec2(t*.006+seed,seed*.3);
      float f=texture2D(nz,q).r*.65+texture2D(nz,q*2.3+vec2(-t*.009,.4)).r*.35;
      float edge=smoothstep(0.,.3,vUv.y)*smoothstep(1.,.5,vUv.y)*smoothstep(0.,.15,vUv.x)*smoothstep(1.,.85,vUv.x);
      gl_FragColor=vec4(tint, smoothstep(.38,.8,f)*edge*a); }`,
});
const fogBanks = [];
for (let i = 0; i < 0; i++) {  // fog removed (kept as a no-op so it can return later); low banks hugging the ground, and higher drifting layers between the depth planes
  const low = i < 5, m = new THREE.Mesh(new THREE.PlaneGeometry(low ? 30 : 40, low ? 3.2 : 6), fogMat(low ? 0x8fa79c : 0x6f8a80, low ? .38 : .18));
  m.position.set((rng() - .5) * 10, low ? .4 + rng() * 1.2 : 3 + rng() * 6, -8 + i * 2.9); m.userData.v = (rng() - .5) * .25; fogBanks.push(m); scene.add(m);
}
const moonClouds = [].map((k) => {  // no cloud veil over the moon  // thin cloud veiling the moon, drifting slowly across it
  const m = new THREE.Mesh(new THREE.PlaneGeometry(14, 2.6), fogMat(0x7d958b, .2)); m.position.set(1 + k * 6, 9.0 + k * 1.6, -36 - k);
  m.userData.v = .12 + k * .05; scene.add(m); return m; });
for (let i = 0; i < 9; i++) { const m = new THREE.Mesh(new THREE.PlaneGeometry(14, 4), new THREE.MeshBasicMaterial({ color: 0x8fb3a6, alphaMap: mistTex, transparent: true, opacity: .16, depthWrite: false }));
  m.position.set((rng() - .5) * 18, .6 + rng() * .8, -3 + rng() * 14); mist.push(m); }

// ------------------------------------------------------------------ post-processing
const composer = new EffectComposer(renderer, { frameBufferType: THREE.HalfFloatType });
composer.addPass(new RenderPass(scene, camera));
// GodRaysEffect moves its light-source mesh into its own internal scene, so it gets a stand-in sphere and the
// textured moon stays in the main scene.
const raySource = new THREE.Mesh(new THREE.SphereGeometry(2.3, 32, 24), new THREE.MeshBasicMaterial({ color: 0x9aaaa2, fog: false }));
raySource.position.copy(moon.position);
const godRays = new GodRaysEffect(camera, raySource, { density: .82, decay: .88, weight: .16, exposure: .06, samples: 60, clampMax: 1, resolutionScale: .5 });
const bloom = new BloomEffect({ intensity: .7, luminanceThreshold: .74, luminanceSmoothing: .25, mipmapBlur: true, radius: .72 });
const grade = new HueSaturationEffect({ hue: .02, saturation: -.15 });
const lift = new BrightnessContrastEffect({ brightness: .012, contrast: -.02 });  // lift the black point a touch
const vignette = new VignetteEffect({ offset: .29, darkness: .66 });
const grain = new NoiseEffect({ blendFunction: BlendFunction.OVERLAY }); grain.blendMode.opacity.value = .07;
const tone = new ToneMappingEffect({ mode: ToneMappingMode.ACES_FILMIC });
const raysPass = new EffectPass(camera, godRays);
composer.addPass(raysPass);
composer.addPass(new EffectPass(camera, bloom, grade, vignette, grain, tone, lift));

// ------------------------------------------------------------------ chapter i: the ivy graph on a stone wall (same renderer, same camera)
const ringScene = new THREE.Scene();
ringScene.background = new THREE.Color(0x040c09); ringScene.fog = new THREE.FogExp2(0x0c1a16, .006);
ringScene.environment = scene.environment; ringScene.environmentIntensity = .32;
ringScene.add(new THREE.HemisphereLight(0x6a8f83, 0x081210, .42));
const rMoon = new THREE.DirectionalLight(0xc4d8d0, 1.9); rMoon.position.set(-10, 16, 14); rMoon.target.position.set(0, 5, 0);
rMoon.castShadow = true; rMoon.shadow.mapSize.set(2048, 2048); Object.assign(rMoon.shadow.camera, { left: -16, right: 16, top: 14, bottom: -4, near: 1, far: 60 });
rMoon.shadow.bias = -.0004; rMoon.shadow.normalBias = .03; ringScene.add(rMoon, rMoon.target);
const rWallMat = wallMat.clone();
["map", "normalMap", "roughnessMap"].forEach((k) => { rWallMat[k] = wallMat[k].clone(); rWallMat[k].repeat.set(15, 7.5); rWallMat[k].needsUpdate = true; });
const rWall = new THREE.Mesh(new THREE.PlaneGeometry(60, 30, 1, 1), rWallMat); rWall.position.set(0, 8, 0); rWall.receiveShadow = true; ringScene.add(rWall);
const rFloor = new THREE.Mesh(new THREE.PlaneGeometry(60, 30), floor.material); rFloor.rotation.x = -Math.PI / 2; rFloor.position.set(0, 0, 10); rFloor.receiveShadow = true; ringScene.add(rFloor);
// nodes: glass lanterns with glowing cores; the shared phone is an amber sigil; the ring is known fraud
const N3 = { me: [-6.5, 5.6], phone: [-1.6, 6.2], f1: [3.2, 9.2], f2: [5.4, 6.0], f3: [3.0, 3.0], a1: [-10, 8.6], a2: [-10.4, 3.4], a3: [-6.8, 10.2], a4: [8.4, 10.4], a5: [9.0, 3.6] };
const v3 = (k, z = .75) => new THREE.Vector3(N3[k][0], N3[k][1], z);
const iron = new THREE.MeshPhysicalMaterial({ color: 0x1a201d, metalness: .6, roughness: .6, clearcoat: .2, clearcoatRoughness: .45 });
const glassL = new THREE.MeshPhysicalMaterial({ color: 0xc9dcd3, metalness: 0, roughness: .45, transmission: .85, thickness: .5, ior: 1.45, transparent: true });  // frosted, matte glass
const lanterns = {};
function lantern(k, color, intensity, light) {
  const g = new THREE.Group(); g.position.copy(v3(k));
  const shell = new THREE.Mesh(new THREE.SphereGeometry(.5, 32, 24), glassL);
  const core = new THREE.Mesh(new THREE.SphereGeometry(.2, 20, 16), new THREE.MeshBasicMaterial({ color: new THREE.Color(color).multiplyScalar(intensity) }));
  const cap = new THREE.Mesh(new THREE.CylinderGeometry(.18, .3, .16, 16), iron); cap.position.y = .52;
  const hook = new THREE.Mesh(new THREE.TorusGeometry(.1, .025, 8, 20), iron); hook.position.y = .68;
  const base = new THREE.Mesh(new THREE.CylinderGeometry(.28, .16, .12, 16), iron); base.position.y = -.5;
  [shell, cap, hook, base].forEach((m) => { m.castShadow = true; }); g.add(shell, core, cap, hook, base);
  if (light) { const pl = new THREE.PointLight(color, 2.6, 7, 2); g.add(pl); g.userData.light = pl; }
  g.userData.core = core; ringScene.add(g); lanterns[k] = g; return g;
}
lantern("me", 0x7fd1b9, 2.0, true);
["a1", "a2", "a3", "a4", "a5"].forEach((k) => lantern(k, 0x9fc4b6, 1.0, false));
["f1", "f2", "f3"].forEach((k, i) => lantern(k, 0xe8665a, 1.5, i === 1));
const sigil = new THREE.Group(); sigil.position.copy(v3("phone", .8)); ringScene.add(sigil);
const amber = new THREE.MeshStandardMaterial({ color: 0xc99a4a, emissive: 0xd98f30, emissiveIntensity: 1.1, metalness: .5, roughness: .6 });
sigil.add(new THREE.Mesh(new THREE.OctahedronGeometry(.42, 0), amber));
const sring = new THREE.Mesh(new THREE.TorusGeometry(.7, .035, 10, 64), amber); sigil.add(sring);
const sring2 = new THREE.Mesh(new THREE.TorusGeometry(.9, .02, 8, 64), amber); sring2.rotation.x = Math.PI / 2.4; sigil.add(sring2);
const sigLight = new THREE.PointLight(0xe0a050, 3, 8, 2); sigil.add(sigLight);
// vines between nodes: tube geometry that grows (drawRange), with instanced leaves revealed as it grows
const vineDefs = [["a1", "me", 0], ["a2", "me", 0], ["a3", "a1", 0], ["me", "phone", 0], ["phone", "f1", 1], ["phone", "f2", 1], ["phone", "f3", 1],
  ["f1", "f2", 0], ["f2", "f3", 0], ["f1", "a4", 0], ["f2", "a5", 0]];
const vines3 = vineDefs.map(([a, b, lit]) => {
  const A = v3(a, .35), B = v3(b, .35), mid = A.clone().lerp(B, .5); mid.y -= .7 + rng() * .5; mid.z = .5;
  const curve = new THREE.CatmullRomCurve3([A, A.clone().lerp(mid, .5).add(new THREE.Vector3(0, .2, .1)), mid, B.clone().lerp(mid, .5).add(new THREE.Vector3(0, .25, .05)), B]);
  const geo = new THREE.TubeGeometry(curve, 80, lit ? .045 : .035, 6, false);
  const m = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: lit ? 0x3a4a2e : 0x2a3a2a, roughness: .65 })); m.castShadow = true; ringScene.add(m);
  return { curve, mesh: m, lit, count: geo.index.count, grow: lit ? 0 : 1 };
});
const RL = 950, rLeaves = new THREE.InstancedMesh(leafGeo, leafMat, RL); rLeaves.castShadow = true; rLeaves.receiveShadow = true;
const rLeafInfo = [];
for (let i = 0; i < RL; i++) {
  const vi = i % vines3.length, v = vines3[vi], u = rng(), p = v.curve.getPointAt(u);
  dummy.position.copy(p).add(new THREE.Vector3((rng() - .5) * .25, (rng() - .5) * .22, .05 + rng() * .14));
  dummy.lookAt(dummy.position.clone().add(new THREE.Vector3((rng() - .5) * .6, .4, 1))); dummy.rotateZ((rng() - .5) * 1.8);
  const s = .38 + rng() * .45; dummy.scale.setScalar(s); dummy.updateMatrix();
  rLeafInfo.push({ vi, u, m: dummy.matrix.clone(), s });
  rLeaves.setMatrixAt(i, dummy.matrix); colr.setHex(hues[rng() < .06 ? 5 : (rng() * 5) | 0]).offsetHSL(0, 0, (rng() - .5) * .06); rLeaves.setColorAt(i, colr);
}
ringScene.add(rLeaves);
// coral blooms around the fraud lanterns: instanced curved petals that open
const petalShape = new THREE.Shape(); petalShape.moveTo(0, 0); petalShape.bezierCurveTo(-.22, .2, -.2, .62, 0, .8); petalShape.bezierCurveTo(.2, .62, .22, .2, 0, 0);
const petalGeo = new THREE.ShapeGeometry(petalShape, 10); { const pp = petalGeo.attributes.position; for (let i = 0; i < pp.count; i++) { const y = pp.getY(i), x = pp.getX(i); pp.setZ(i, .35 * y * y - .6 * x * x); } petalGeo.computeVertexNormals(); }
const petalMat = new THREE.MeshPhysicalMaterial({ color: 0xd9655a, emissive: 0xb0352a, emissiveIntensity: .25, roughness: .8, sheen: .6, sheenRoughness: .7, sheenColor: new THREE.Color(0xe0a090), side: THREE.DoubleSide });
const PET = 3 * 14, petals = new THREE.InstancedMesh(petalGeo, petalMat, PET); petals.castShadow = true; ringScene.add(petals);
const petalInfo = [];
["f1", "f2", "f3"].forEach((k, bi) => { for (let j = 0; j < 14; j++) petalInfo.push({ c: v3(k, .6), ang: (j / 14) * Math.PI * 2 + (j % 2) * .2, layer: j % 2, bi }); });
const halo = new THREE.Mesh(new THREE.TorusGeometry(4.2, .025, 8, 120), new THREE.MeshBasicMaterial({ color: new THREE.Color(0xd9655a).multiplyScalar(1.1), transparent: true, opacity: 0 }));
halo.position.set(3.9, 6.1, .4); ringScene.add(halo);
// sparks when the ring is caught
const SP = 160, spGeo = new THREE.BufferGeometry(), spPos = new Float32Array(SP * 3), spVel = new Float32Array(SP * 3);
spGeo.setAttribute("position", new THREE.BufferAttribute(spPos, 3));
const sparks = new THREE.Points(spGeo, new THREE.PointsMaterial({ size: .12, map: ffSprite, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, color: 0xffd6a0 }));
sparks.visible = false; ringScene.add(sparks);
let sparkT = -1;
function burst() { for (let i = 0; i < SP; i++) { spPos.set([3.9, 6.1, .8], i * 3); const a = rng() * 6.283, e = (rng() - .5) * 2, sp = 2 + rng() * 4; spVel.set([Math.cos(a) * sp, Math.sin(a) * sp, e * 2], i * 3); } sparkT = 0; sparks.visible = true; }

// ------------------------------------------------------------------ shared helpers for the chapter scenes
const clamp = (x) => Math.min(1, Math.max(0, x));
const smooth = (p) => p * p * (3 - 2 * p);
function baseScene(bg = 0x050d0a, fogD = .006) {
  const sc = new THREE.Scene(); sc.background = new THREE.Color(bg); sc.fog = new THREE.FogExp2(0x0c1a16, fogD);
  sc.environment = scene.environment; sc.environmentIntensity = .32;
  sc.add(new THREE.HemisphereLight(0x7f9f92, 0x0c1612, .52));
  const L = new THREE.DirectionalLight(0xc4d8d0, 1.5); L.position.set(-8, 14, 10); L.target.position.set(0, 0, 0);
  L.castShadow = true; L.shadow.mapSize.set(1024, 1024); Object.assign(L.shadow.camera, { left: -14, right: 14, top: 14, bottom: -6, near: 1, far: 60 });
  L.shadow.bias = -.0005; L.shadow.normalBias = .03; sc.add(L, L.target); sc.userData.sun = L;
  return sc;
}
const glowMat = (c, k = 2) => new THREE.MeshBasicMaterial({ color: new THREE.Color(c).multiplyScalar(k) });
function orb(c, k, r = .14, light = 2.2) {
  const g = new THREE.Group(); g.add(new THREE.Mesh(new THREE.SphereGeometry(r, 20, 16), glowMat(c, k)));
  if (light) { const pl = new THREE.PointLight(c, light, 6, 2); g.add(pl); } return g;
}
const soil = new THREE.MeshPhysicalMaterial({ map: floorSet.albedo, normalMap: floorSet.normal, normalScale: new THREE.Vector2(.5, .5), roughness: .9, color: 0x2e3a33 });

// ------------------------------------------------------------------ chapter iv: the cart among giant hosta and succulent leaves
const cart = (() => {
  const sc = baseScene(0x040b08);
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(60, 60), soil); ground.rotation.x = -Math.PI / 2; ground.receiveShadow = true; sc.add(ground);
  // hosta: a broad leaf with parallel curving ribs and a gentle cup
  const hs = new THREE.Shape(); hs.moveTo(0, 0); hs.bezierCurveTo(-.62, .5, -.55, 1.35, 0, 1.8); hs.bezierCurveTo(.55, 1.35, .62, .5, 0, 0);
  const hg = new THREE.ShapeGeometry(hs, 24); { const p = hg.attributes.position;
    for (let i = 0; i < p.count; i++) { const x = p.getX(i), y = p.getY(i); p.setZ(i, .28 * Math.sin(Math.PI * y / 1.8) - .45 * x * x + .018 * Math.sin((Math.abs(x) / (.08 + y * .18)) * 9)); } hg.computeVertexNormals(); }
  const hm = new THREE.MeshPhysicalMaterial({ side: THREE.DoubleSide, roughness: .6, clearcoat: .22, clearcoatRoughness: .45, sheen: .45, sheenRoughness: .7, sheenColor: new THREE.Color(0x5f8f7f) });
  const HN = 90, hosta = new THREE.InstancedMesh(hg, hm, HN); hosta.castShadow = hosta.receiveShadow = true;
  const d = new THREE.Object3D(), c = new THREE.Color();
  for (let i = 0; i < HN; i++) {
    const a = (i / HN) * Math.PI * 2 + rng() * .4, r = 3.2 + rng() * 6.5;
    d.position.set(Math.cos(a) * r, rng() * .6, Math.sin(a) * r - 1.5);
    d.lookAt(0, 2.4 + rng() * 2, 0); d.rotateX(-.9 - rng() * .5); d.rotateZ((rng() - .5) * .8);
    d.scale.setScalar(1.4 + rng() * 2.1); d.updateMatrix(); hosta.setMatrixAt(i, d.matrix);
    c.setHex([0x1c4a3f, 0x22503f, 0x2b6253, 0x395c48, 0x1f4a42][(rng() * 5) | 0]).offsetHSL(0, -.05, (rng() - .5) * .05); hosta.setColorAt(i, c);
  }
  sc.add(hosta);
  // succulent rosettes: plump instanced petals in a spiral
  const ps = new THREE.Shape(); ps.moveTo(0, 0); ps.bezierCurveTo(-.16, .14, -.12, .4, 0, .5); ps.bezierCurveTo(.12, .4, .16, .14, 0, 0);
  const pg = new THREE.ExtrudeGeometry(ps, { depth: .03, bevelEnabled: true, bevelThickness: .03, bevelSize: .03, bevelSegments: 3, curveSegments: 10 });
  const PN = 8 * 34, suc = new THREE.InstancedMesh(pg, new THREE.MeshPhysicalMaterial({ roughness: .55, clearcoat: .3, clearcoatRoughness: .4, color: 0xffffff }), PN); suc.castShadow = true;
  const ros = [[-2.6, 3.2], [2.8, 2.6], [-4.4, -1], [4.2, -.5], [-1.4, 4.6], [1.6, 4.4], [-3.2, -3.6], [3.4, -3.8]];
  let k = 0;
  ros.forEach(([rx, rz]) => { const sc0 = .7 + rng() * .5;
    for (let j = 0; j < 34; j++) { const ang = j * 2.39996, ring = Math.sqrt(j / 34);
      d.position.set(rx, .05, rz); d.rotation.set(0, ang, 0); d.rotateX(-(.15 + ring * 1.15)); d.scale.setScalar(sc0 * (.45 + ring * .7)); d.updateMatrix(); suc.setMatrixAt(k, d.matrix);
      c.setHex(0x3f6b5a).offsetHSL((rng() - .5) * .02, -.1, ring * .08 - .04); suc.setColorAt(k++, c); } });
  sc.add(suc);
  // the cart's items arrive like fireflies: two sage lights settle; the gift card's coral light is turned away
  const items = [orb(0x9fe0c8, 1.8), orb(0x9fe0c8, 1.8), orb(0xe8766a, 1.6)];
  const from = [[-6, 4.5, -3], [6.5, 5, -2], [0, 6, -6]], to = [[-.7, 1.9, 0], [.7, 2.1, .2], [0, 2.6, -.2]];
  items.forEach((o) => sc.add(o));
  const moonC = new THREE.Mesh(new THREE.SphereGeometry(2.2, 64, 48), moonMat); moonC.position.set(-9, 17, -40); sc.add(moonC);
  return { sc, hg, hm, pg, sucMat: suc.material, update(p, t) {
    const e = smooth(p);
    camera.position.set(Math.sin(t * .05) * .3 + px * .4, 2.9 - e * .5 - py * .2, 9.5 - e * 3.5); look.set(0, 1.9, 0); camera.lookAt(look);
    items.forEach((o, i) => { const a = clamp((p - .1 - i * .1) / .35), q = smooth(a);
      o.position.set(from[i][0] + (to[i][0] - from[i][0]) * q + Math.sin(t * 1.3 + i) * .08, from[i][1] + (to[i][1] - from[i][1]) * q + Math.sin(t * 1.7 + i) * .06, from[i][2] + (to[i][2] - from[i][2]) * q);
      if (i === 2) { const out = clamp((p - .62) / .15); o.position.y -= out * 2.2; o.scale.setScalar(Math.max(.01, 1 - out)); } });
  } };
})();

// the fraud-ring graph (lanterns, vines, blooms) is gathered into one group so it can hang on the journey's ivy wall
const graph = new THREE.Group();
[...Object.values(lanterns), sigil, ...vines3.map((v) => v.mesh), rLeaves, petals, halo, sparks].forEach((o) => graph.add(o));

// ------------------------------------------------------------------ the journey (stage A): one connected world the camera walks through
// corridor -> a heavy door opens onto the moonlit lake -> a stone bridge assembles across the water -> the ivy wall
// where the fraud-ring graph lives. One scene, one camera path; nothing cuts.
const stoneAt = (rx, ry) => { const m = wallMat.clone(); ["map", "normalMap", "roughnessMap"].forEach((k) => { m[k] = wallMat[k].clone(); m[k].repeat.set(rx, ry); m[k].needsUpdate = true; }); return m; };
const journey = (() => {
  const J = new THREE.Scene(); J.background = new THREE.Color(0x040a08); J.fog = new THREE.FogExp2(0x0c1a16, .0022);
  J.add(new THREE.HemisphereLight(0x7f9f92, 0x0c1612, .5));
  const sun = new THREE.DirectionalLight(0xc4d8d0, 1.5); sun.position.set(20, 40, -150); sun.target.position.set(0, 0, -80);
  sun.castShadow = true; sun.shadow.mapSize.set(1024, 1024); Object.assign(sun.shadow.camera, { left: -30, right: 30, top: 30, bottom: -10, near: 1, far: 160 });
  sun.shadow.bias = -.0005; sun.shadow.normalBias = .04; J.add(sun, sun.target); J.userData.sun = sun;
  // a pool of four warm/cool point lights, moved to the nearest light sources every frame (constant light count: no shader recompiles)
  const pool = [0, 1, 2, 3].map(() => { const l = new THREE.PointLight(0xffb070, 0, 9, 2); J.add(l); return l; });
  const anchors = [];   // { pos, color, intensity, flicker }
  // sky, moon, far pines, water (the lake look she likes)
  const skyM = new THREE.Mesh(new THREE.SphereGeometry(660, 32, 16), new THREE.ShaderMaterial({ side: THREE.BackSide, depthWrite: false, fog: false,
    vertexShader: "varying vec3 vP; void main(){ vP=position; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }",
    fragmentShader: `varying vec3 vP; float h(vec3 p){return fract(sin(dot(p,vec3(12.9898,78.233,37.719)))*43758.5453);}
      void main(){ float y=normalize(vP).y; vec3 c=mix(vec3(.045,.1,.085),vec3(.006,.016,.014),smoothstep(-.05,.6,y));
        float s=step(.9985,h(floor(normalize(vP)*420.)))*smoothstep(.05,.4,y); c+=vec3(.7,.78,.74)*s*.8; gl_FragColor=vec4(c,1.);
        #include <colorspace_fragment>
      }` }));
  J.add(skyM);
  const moonJ = new THREE.Mesh(new THREE.SphereGeometry(17, 64, 48), moonMat); moonJ.position.set(130, 112, -600); J.add(moonJ);
  const pineG = new THREE.ConeGeometry(1, 4, 7); pineG.translate(0, 2, 0);
  const PN = 520, pines = new THREE.InstancedMesh(pineG, new THREE.MeshStandardMaterial({ color: 0x0d1c17, roughness: .95 }), PN), d = new THREE.Object3D();
  for (let i = 0; i < PN; i++) { const side = i % 2 ? 1 : -1, row = (i >> 1) % 4;
    d.position.set(side * (22 + row * 5 + rng() * 6), -.3, -45 - ((i >> 3) / (PN / 8)) * 190 + rng() * 3);
    d.scale.set(1 + rng(), 1.6 + rng() * 2.6, 1 + rng()); if ((d.position.x > 20 && d.position.x < 58 && d.position.z < -80 && d.position.z > -165) || d.position.z < -182) d.scale.setScalar(0);
    d.updateMatrix(); pines.setMatrixAt(i, d.matrix); }
  J.add(pines);
  const wn = (() => { const N = 256, n = fbm(N, 8, 4); return normalFrom(N, n, 3.2); })(); wn.repeat.set(46, 36);
  const water = new THREE.Mesh(new THREE.PlaneGeometry(420, 360), new THREE.MeshPhysicalMaterial({ color: 0x0a1814, roughness: .16, normalMap: wn, normalScale: new THREE.Vector2(.35, .35), clearcoat: .6, clearcoatRoughness: .2, transparent: true, opacity: .74 }));
  water.rotation.x = -Math.PI / 2; water.position.set(20, -.03, -222); J.add(water);
  const envS = new THREE.Scene(); envS.background = new THREE.Color(0x061210);
  const em = new THREE.Mesh(new THREE.SphereGeometry(20, 16, 16), new THREE.MeshBasicMaterial({ color: 0xbfd2c9 })); em.position.set(70, 95, -330); envS.add(em);
  J.environment = pmrem.fromScene(envS, 0.02).texture; J.environmentIntensity = .7;

  const cWallMat = stoneAt(13.5, 1.2), vaultMat = stoneAt(13.5, 2), ivyWallMat = stoneAt(15, 7.5); vaultMat.side = THREE.BackSide;
  // ---- room 1: the corridor (vaulted, torch-lit, ivy creeping), z 12 .. -40
  const CW = 2.4, CS = 3.4, GAP = 5.2, N = 10;
  const cFloor = new THREE.Mesh(new THREE.PlaneGeometry(CW * 2 + 1, 58), floor.material); cFloor.rotation.x = -Math.PI / 2; cFloor.position.z = -14; cFloor.receiveShadow = true; J.add(cFloor);
  const vault = new THREE.Mesh(new THREE.CylinderGeometry(CW + .6, CW + .6, 54, 32, 1, true, 0, Math.PI), vaultMat);
  vault.rotation.set(Math.PI / 2, 0, Math.PI / 2); vault.position.set(0, CS + 1.2, -14); vault.castShadow = true; vault.visible = false; J.add(vault);
  const ringS = new THREE.Shape(); archPath(CW + .5, CS, ringS); ringS.lineTo(-CW - .5, 0); ringS.holes.push(archPath(CW, CS, new THREE.Path()));
  const archGeo = new THREE.ExtrudeGeometry(ringS, { depth: .7, bevelEnabled: true, bevelThickness: .06, bevelSize: .06, bevelSegments: 2, curveSegments: 36 });
  const glowArches = [], gArchGeo = new THREE.TubeGeometry(new THREE.CatmullRomCurve3(archPath(CW - .02, CS, new THREE.Path()).getSpacedPoints(60).map((v) => new THREE.Vector3(v.x, v.y, .72))), 80, .025, 6, false);
  for (let i = 0; i < N; i++) { const a = new THREE.Mesh(archGeo, traceryMat); a.position.z = 10 - i * GAP; a.castShadow = a.receiveShadow = true; J.add(a);
    const g = new THREE.Mesh(gArchGeo, new THREE.MeshBasicMaterial({ color: 0x7fd1b9, transparent: true, opacity: 0 })); g.position.z = 10 - i * GAP; J.add(g); glowArches.push(g);
    [-1, 1].forEach((sd) => { const w = new THREE.Mesh(new THREE.BoxGeometry(.5, CS + 3.2, GAP - .7), rWallMat); w.position.set(sd * (CW + 1.1), (CS + 3.2) / 2, 10 - i * GAP - GAP / 2); w.receiveShadow = w.castShadow = true; J.add(w); }); }
  // whimsy lanterns: small glass lanterns that float ahead of you, bob and sway, mostly up and to the right; two lead the way to the door
  const floaters = [];
  for (let i = 0; i < 9; i++) { const warm = i % 3 !== 2, col = warm ? 0xffc37a : 0x8fe0c4, g = new THREE.Group();
    const sh = new THREE.Mesh(new THREE.SphereGeometry(.17, 20, 14), glassL), co = new THREE.Mesh(new THREE.SphereGeometry(.075, 12, 10), glowMat(col, warm ? 2.4 : 2));
    const cap = new THREE.Mesh(new THREE.CylinderGeometry(.06, .1, .06, 12), iron); cap.position.y = .18; g.add(sh, co, cap);
    const home = new THREE.Vector3(.3 + rng() * 1.7, 2.6 + rng() * 1.6, 8 - i * 5.2 - rng() * 2); g.position.copy(home); J.add(g);
    const F = { g, home, ph: rng() * 6.28, lead: i >= 7, col, core: co }; floaters.push(F);
    anchors.push({ pos: g.position, color: col, intensity: warm ? 1.6 : 1.2, flicker: true, floater: F }); }
  const flameMat = glowMat(0xffa860, 2.6);
  for (let i = 0; i < 5; i++) { const sd = i % 2 ? 1 : -1, z = 6 - i * 10;
    const br = new THREE.Mesh(new THREE.CylinderGeometry(.06, .04, .5, 8), iron); br.position.set(sd * (CW + .4), 2.3, z); br.rotation.z = sd * .5; J.add(br);
    const fl = new THREE.Mesh(new THREE.ConeGeometry(.1, .32, 10), flameMat); fl.position.set(sd * (CW + .25), 2.65, z); J.add(fl);
    anchors.push({ pos: new THREE.Vector3(sd * (CW + .1), 2.7, z), color: 0xffa860, intensity: 3.2, flicker: true, mesh: fl }); }
  [-1, 1].forEach((sd) => { const fl = new THREE.Mesh(new THREE.ConeGeometry(.12, .36, 10), flameMat); fl.position.set(sd * (CW + .2), 2.9, -38.6); J.add(fl);
    const br = new THREE.Mesh(new THREE.CylinderGeometry(.06, .04, .5, 8), iron); br.position.set(sd * (CW + .35), 2.55, -38.6); br.rotation.z = sd * .5; J.add(br);
    anchors.push({ pos: new THREE.Vector3(sd * (CW - .1), 3, -38.2), color: 0xffa860, intensity: 4, flicker: true, mesh: fl }); });
  const ivyC = new THREE.InstancedMesh(leafGeo, leafMat, 900); ivyC.castShadow = true;
  for (let i = 0; i < 900; i++) { const sd = i % 2 ? 1 : -1, z = 10 - rng() * 50, y = Math.pow(rng(), 1.6) * (CS + .8);
    d.position.set(sd * (CW + .48), y, z); d.lookAt(d.position.x - sd, y + .3, z); d.rotateZ((rng() - .5) * 2); d.scale.setScalar(.6 + rng() * .7); d.updateMatrix(); ivyC.setMatrixAt(i, d.matrix);
    colr.setHex(hues[(rng() * 5) | 0]); ivyC.setColorAt(i, colr); }
  J.add(ivyC);

  // ---- the heavy door at the corridor's end, z -40
  const DZ = -40.5, doorFrame = new THREE.Shape(); archPath(CW + .9, CS + .2, doorFrame); doorFrame.lineTo(-CW - .9, 0); doorFrame.holes.push(archPath(CW, CS + .2, new THREE.Path()));
  const df = new THREE.Mesh(new THREE.ExtrudeGeometry(doorFrame, { depth: 1, bevelEnabled: false, curveSegments: 40 }), traceryMat); df.position.z = DZ - .5; df.castShadow = true; J.add(df);
  const wood = new THREE.MeshPhysicalMaterial({ color: 0x2a2219, roughness: .72, clearcoat: .15, clearcoatRoughness: .5 });
  const leafShape = (sd) => { const sh = new THREE.Shape(); const pth = archPath(CW, CS + .2, new THREE.Path()).getPoints(60).filter((v) => sd < 0 ? v.x <= .001 : v.x >= -.001);
    sh.moveTo(0, 0); pth.forEach((v) => sh.lineTo(v.x, v.y)); sh.lineTo(0, 0); return sh; };
  const doors = [-1, 1].map((sd) => { const pivot = new THREE.Group(); pivot.position.set(sd * CW, 0, DZ);
    const g = new THREE.ExtrudeGeometry(leafShape(sd), { depth: .16, bevelEnabled: false, curveSegments: 30 }); g.translate(-sd * CW, 0, 0);
    const m = new THREE.Mesh(g, wood); m.castShadow = true; pivot.add(m);
    for (let k = 0; k < 4; k++) { const strap = new THREE.Mesh(new THREE.BoxGeometry(CW - .2, .1, .04), iron); strap.position.set(-sd * (CW / 2), .7 + k * .9, .18); pivot.add(strap); }
    const ring = new THREE.Mesh(new THREE.TorusGeometry(.14, .025, 8, 24), iron); ring.position.set(-sd * (CW - .35), 1.6, .22); pivot.add(ring);
    J.add(pivot); return { pivot, sd }; });

  // ---- the bridge: segments rise out of the water as you walk; lanterns on the parapets
  const BZ0 = -42, SEG = 12, SL = 5.6, segs = [];
  const deckG = new THREE.BoxGeometry(3.8, .5, SL - .08), parG = new THREE.BoxGeometry(.3, .55, SL - .1);
  for (let i = 0; i < SEG; i++) {
    const g = new THREE.Group(); g.position.set(0, .2, BZ0 - SL / 2 - i * SL);
    const deck = new THREE.Mesh(deckG, traceryMat); deck.castShadow = deck.receiveShadow = true; g.add(deck);
    [-1, 1].forEach((sd) => { const p = new THREE.Mesh(parG, traceryMat); p.position.set(sd * 1.75, .5, 0); p.castShadow = true; g.add(p); });
    if (i % 2 === 0) { const post = new THREE.Mesh(new THREE.CylinderGeometry(.06, .06, .8, 8), iron); post.position.set(1.75, 1.15, -SL / 2 + .3); g.add(post);
      const lamp = new THREE.Mesh(new THREE.SphereGeometry(.13, 14, 10), glowMat(0x9fe0c8, 2)); lamp.position.set(1.75, 1.6, -SL / 2 + .3); g.add(lamp);
      anchors.push({ pos: new THREE.Vector3(1.75, 2.5, BZ0 - SL / 2 - i * SL - SL / 2 + .3), color: 0x9fe0c8, intensity: 2.2, group: g }); }
    const pierS = new THREE.Shape(); archPath(1.6, .2, pierS); pierS.lineTo(-1.6, 0); pierS.holes.push(archPath(1.2, .2, new THREE.Path()));
    if (i % 2 === 1) { const pier = new THREE.Mesh(new THREE.ExtrudeGeometry(pierS, { depth: .5, bevelEnabled: false, curveSegments: 20 }), traceryMat); pier.rotation.y = Math.PI / 2; pier.position.set(-.25, -2.2, 0); pier.scale.set(1, 1.1, 1); g.add(pier); }
    g.userData.i = i; J.add(g); segs.push(g);
  }
  const BEND = BZ0 - SEG * SL;   // where the bridge meets the far shore

  // ---- the ivy wall on the far shore, with the fraud-ring graph growing on it
  const WZ = BEND - 4;
  const wall = new THREE.Mesh(new THREE.PlaneGeometry(60, 30), ivyWallMat); wall.position.set(0, 13, WZ); wall.receiveShadow = true; J.add(wall);
  const shore = new THREE.Mesh(new THREE.PlaneGeometry(60, 10), floor.material); shore.rotation.x = -Math.PI / 2; shore.position.set(0, .02, WZ + 4.5); shore.receiveShadow = true; J.add(shore);
  const ivyW = new THREE.InstancedMesh(leafGeo, leafMat, 1100); ivyW.castShadow = true;
  for (let i = 0; i < 1100; i++) { const edgeX = (rng() < .5 ? -1 : 1) * (7 + rng() * 20), y = rng() * 22;
    d.position.set(i % 4 === 0 ? (rng() - .5) * 50 : edgeX, i % 4 === 0 ? 10 + rng() * 14 : y, WZ + .08); d.lookAt(d.position.x, d.position.y + .3, WZ + 2); d.rotateZ((rng() - .5) * 2.4);
    d.scale.setScalar(.7 + rng() * .9); d.updateMatrix(); ivyW.setMatrixAt(i, d.matrix); colr.setHex(hues[(rng() * 5) | 0]); ivyW.setColorAt(i, colr); }
  J.add(ivyW);
  graph.position.set(0, 1.2, WZ + .05); graph.scale.setScalar(.55); J.add(graph);   // the graph pieces move from the old ring scene to the wall

  // ---- the camera path: keyframes along the walk (p = progress through the story section)
  const K = [
    [0.00, [0, 1.8, 13], [0, 1.8, -10]],
    [0.26, [0, 1.8, -24], [0, 1.9, -48]],
    [0.40, [0, 1.9, -35], [0, 2.1, -62]],
    [0.58, [0, 2.6, -58], [0, 2.4, -100]],
    [0.76, [0, 2.7, -88], [0, 3.6, WZ]],
    [0.92, [.6, 4.4, WZ + 13], [.6, 4.6, WZ]],
    [1.00, [.6, 4.4, WZ + 13], [.6, 4.6, WZ]],
  ].map(([u, a, b]) => ({ u, a: new THREE.Vector3(...a), b: new THREE.Vector3(...b) }));
  const camAt = (p, outPos, outLook) => {
    let i = 0; while (i < K.length - 2 && p > K[i + 1].u) i++;
    const A = K[i], B = K[i + 1], f = smooth(clamp((p - A.u) / (B.u - A.u)));
    outPos.lerpVectors(A.a, B.a, f); outLook.lerpVectors(A.b, B.b, f);
  };
  const tmpP = new THREE.Vector3(), tmpL = new THREE.Vector3();
  let caught = false;
  const api = { sc: J, sun, anchors, light, update(p, t, dt) {
    camAt(p, tmpP, tmpL);
    camera.position.set(tmpP.x + px * .35, tmpP.y - py * .2 + Math.sin(t * 1.7) * .015, tmpP.z); look.copy(tmpL); camera.lookAt(look);
    // the door swings open as you reach it
    const open = smooth(clamp((p - .3) / .1)); doors.forEach(({ pivot, sd }) => { pivot.rotation.y = -sd * open * 1.65; });
    // bridge segments rise out of the water just ahead of you
    segs.forEach((g) => { const need = .38 + g.userData.i * .016, r = smooth(clamp((p - need) / .08)); g.position.y = .2 - (1 - r) * 4; g.rotation.x = (1 - r) * .25; g.visible = r > .01; });
    // the fraud-ring graph on the wall: vines grow, the ring blooms (as before)
    const grow = clamp((p - .66) / .14), bloomP = clamp((p - .8) / .15);
    vines3.forEach((v) => { const g = v.lit ? grow : 1; v.mesh.geometry.setDrawRange(0, Math.floor(v.count * g / 6) * 6); v.grow = g; });
    rLeafInfo.forEach((L, i) => { if (L.u <= vines3[L.vi].grow) rLeaves.setMatrixAt(i, L.m); else { dummy.scale.setScalar(0); dummy.position.set(0, -50, 0); dummy.updateMatrix(); rLeaves.setMatrixAt(i, dummy.matrix); } });
    rLeaves.instanceMatrix.needsUpdate = true;
    petalInfo.forEach((P, i) => { const o = clamp(bloomP * 1.3 - P.bi * .15), op = .25 + o * (P.layer ? .95 : 1.25);
      dummy.position.copy(P.c).add(new THREE.Vector3(Math.cos(P.ang + Math.PI / 2) * .42, Math.sin(P.ang + Math.PI / 2) * .42, .15)); dummy.rotation.set(0, 0, P.ang); dummy.rotateX(-op); dummy.scale.setScalar(o * (P.layer ? .8 : 1.05)); dummy.updateMatrix(); petals.setMatrixAt(i, dummy.matrix); });
    petals.instanceMatrix.needsUpdate = true;
    ["f1", "f2", "f3"].forEach((k) => { lanterns[k].userData.core.scale.setScalar(.6 + bloomP * .8 + Math.sin(t * 3) * .05 * bloomP); });
    lanterns.me.userData.core.scale.setScalar(1 + Math.sin(t * 2.2) * .06);
    halo.material.opacity = bloomP * (.55 + .15 * Math.sin(t * 2)); halo.rotation.z = t * .05; sigil.rotation.y = t * .6; sring2.rotation.z = t * .9;
    if (bloomP > .6 && !caught) { caught = true; burst(); } else if (bloomP < .3) caught = false;
    if (sparkT >= 0) { sparkT += dt; for (let i = 0; i < SP; i++) { spVel[i * 3 + 1] -= 3 * dt; for (let k = 0; k < 3; k++) spPos[i * 3 + k] += spVel[i * 3 + k] * dt; }
      spGeo.attributes.position.needsUpdate = true; sparks.material.opacity = Math.max(0, 1 - sparkT / 1.6); if (sparkT > 1.6) { sparks.visible = false; sparkT = -1; } }
    wn.offset.set(t * .006, t * .004);
    floaters.forEach((F, i) => { const lp = F.lead ? smooth(clamp((p - .22) / .2)) : 0;
      F.g.position.set(F.home.x + Math.sin(t * .5 + F.ph) * .25, F.home.y + Math.sin(t * .9 + F.ph) * .14 + lp * .6, F.home.z - lp * (F.home.z + 46 + i * 1.5));
      F.g.rotation.z = Math.sin(t * .8 + F.ph) * .12; F.core.scale.setScalar(.9 + .15 * Math.sin(t * 9 + F.ph) * Math.sin(t * 5.3 + i)); });
    glowArches.forEach((g, i) => { const dz = (10 - i * GAP) - camera.position.z; g.material.opacity = dz < 0 ? clamp(.85 - Math.abs(dz + 9) * .07) * (.85 + .15 * Math.sin(t * 1.3 + i)) : 0; });
    // the light pool follows the camera: the four nearest light sources get the four lights
    light(t, smooth(clamp((p - .3) / .14)));
  }, light };
  function light(t, out) {
    if (camera.far !== 700) { camera.far = 700; camera.updateProjectionMatrix(); }
    const cp = camera.position;
    anchors.sort((a, b) => a.pos.distanceToSquared(cp) - b.pos.distanceToSquared(cp));
    pool.forEach((l, i) => { const A = anchors[i]; if (!A || ((A.group && !A.group.visible) || (A.item && !A.item.visible))) { l.intensity = 0; return; }
      l.position.copy(A.pos); l.color.setHex(A.color); l.distance = A.dist || 9; l.intensity = A.intensity * (A.flicker ? .85 + .15 * Math.sin(t * 13 + i * 3) * Math.sin(t * 7.3 + i) : 1); });
    anchors.forEach((A) => { if (A.flicker && A.mesh) A.mesh.scale.set(1, .9 + .2 * Math.abs(Math.sin(t * 11 + A.pos.z)), 1); });
    sun.intensity = .9 + .5 * out; J.environmentIntensity = .32 + .38 * out;
    sun.position.set(cp.x - 8 + 16 * out, Math.max(0, cp.y - 3) + 14 + 18 * out, cp.z + 10); sun.target.position.set(cp.x, Math.max(0, cp.y - 3), cp.z - 22);   // moonlight from above and behind: it rakes the brick, never grazes the wall from behind
  }
  return api;
})();

// ------------------------------------------------------------------ the journey (stage B): a hot-air balloon in a small garden beside the ivy wall,
// loaded with the cart; then it drifts to a gothic city gate on a cliff where the payment is checked before capture
const stoneRock = new THREE.MeshPhysicalMaterial({ map: stone.albedo, normalMap: stone.normal, roughnessMap: stone.rough, roughness: .9, color: 0x6f7a74 });
["map", "normalMap", "roughnessMap"].forEach((k) => { stoneRock[k] = stone[k === "map" ? "albedo" : k === "normalMap" ? "normal" : "rough"].clone(); stoneRock[k].repeat.set(6, 4); stoneRock[k].needsUpdate = true; });
const balloon = (() => {
  const J = journey.sc, A = journey.anchors, G = new THREE.Vector3(36, 0, -104);
  // the garden: a mossy island of soil, giant hosta and succulent rosettes
  const isl = new THREE.Mesh(new THREE.CylinderGeometry(10, 11, 1, 40), soil); isl.position.set(G.x, -.42, G.z); isl.receiveShadow = true; J.add(isl);
  const d = new THREE.Object3D(), c = new THREE.Color();
  const HN = 46, hosta = new THREE.InstancedMesh(cart.hg, cart.hm, HN); hosta.castShadow = hosta.receiveShadow = true;
  for (let i = 0; i < HN; i++) { const a = (i / HN) * Math.PI * 2 + rng() * .4, r = 3.4 + rng() * 5.4;
    d.position.set(G.x + Math.cos(a) * r, .05 + rng() * .3, G.z + Math.sin(a) * r); d.lookAt(G.x, 2.4 + rng() * 2, G.z); d.rotateX(-.9 - rng() * .5); d.rotateZ((rng() - .5) * .8);
    d.scale.setScalar(1.2 + rng() * 1.8); d.updateMatrix(); hosta.setMatrixAt(i, d.matrix); c.setHex(hues[(rng() * 5) | 0]).offsetHSL(0, -.05, (rng() - .5) * .05); hosta.setColorAt(i, c); }
  J.add(hosta);
  const ros = [[-3, 2.6], [2.6, 3], [-3.8, -1.4], [3.6, -1.2], [0, 4.2], [-1.8, -3.4]], suc = new THREE.InstancedMesh(cart.pg, cart.sucMat, ros.length * 34); suc.castShadow = true; let k = 0;
  ros.forEach(([rx, rz]) => { const s0 = .8 + rng() * .5; for (let j = 0; j < 34; j++) { const ring = Math.sqrt(j / 34);
    d.position.set(G.x + rx, .1, G.z + rz); d.rotation.set(0, j * 2.39996, 0); d.rotateX(-(.15 + ring * 1.15)); d.scale.setScalar(s0 * (.45 + ring * .7)); d.updateMatrix(); suc.setMatrixAt(k, d.matrix);
    c.setHex(0x3f6b5a).offsetHSL((rng() - .5) * .02, -.1, ring * .08 - .04); suc.setColorAt(k++, c); } });
  J.add(suc);
  // the balloon: a striped silk envelope (lathe), ropes, a wicker basket, a burner whose flame is the budget meter
  const B = new THREE.Group(); B.position.copy(G); J.add(B);
  const gores = (() => { const cv = document.createElement("canvas"); cv.width = 1024; cv.height = 512; const x = cv.getContext("2d");
    for (let i = 0; i < 16; i++) { x.fillStyle = i % 2 ? "#1d5547" : "#2c7865"; x.fillRect(i * 64, 0, 64, 512);
      const g = x.createLinearGradient(i * 64, 0, i * 64 + 64, 0); g.addColorStop(0, "rgba(0,0,0,.35)"); g.addColorStop(.5, "rgba(255,255,255,.06)"); g.addColorStop(1, "rgba(0,0,0,.35)"); x.fillStyle = g; x.fillRect(i * 64, 0, 64, 512); }
    x.fillStyle = "#c9a24e"; x.fillRect(0, 300, 1024, 10); x.fillRect(0, 330, 1024, 4); x.fillStyle = "#123a31"; x.fillRect(0, 440, 1024, 72);
    const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4; return t; })();
  const prof = []; for (let i = 0; i <= 40; i++) { const u = i / 40, y = u * 7.6; const r = u < .2 ? .9 + u * 6 : 3.3 * Math.sin(Math.min(1, (u - .02) / .98) * Math.PI * .92) * (u > .8 ? 1 - (u - .8) * .9 : 1) + .05; prof.push(new THREE.Vector2(Math.max(.05, r), y)); }
  prof[prof.length - 1].x = .05;
  const envMat = new THREE.MeshPhysicalMaterial({ map: gores, roughness: .55, sheen: .7, sheenRoughness: .5, sheenColor: new THREE.Color(0x8fd8c0), emissive: 0xff9a50, emissiveMap: gores, emissiveIntensity: 0, side: THREE.DoubleSide });
  const env = new THREE.Mesh(new THREE.LatheGeometry(prof, 48), envMat); env.position.y = 4.6; env.castShadow = true; B.add(env);
  const wick = new THREE.MeshStandardMaterial({ map: floorSet.albedo, color: 0x8a6440, roughness: .9 });
  const basket = new THREE.Mesh(new THREE.CylinderGeometry(.95, .8, 1, 20, 1, true), wick); basket.position.y = .9; basket.material.side = THREE.DoubleSide; basket.castShadow = true; B.add(basket);
  const rim = new THREE.Mesh(new THREE.TorusGeometry(.95, .06, 8, 32), wick); rim.rotation.x = Math.PI / 2; rim.position.y = 1.4; B.add(rim);
  const bottom = new THREE.Mesh(new THREE.CircleGeometry(.8, 20), wick); bottom.rotation.x = -Math.PI / 2; bottom.position.y = .41; B.add(bottom);
  const ropePts = []; for (let i = 0; i < 8; i++) { const a = i / 8 * Math.PI * 2; ropePts.push(Math.cos(a) * .9, 1.4, Math.sin(a) * .9, Math.cos(a) * 1.15, 5.2, Math.sin(a) * 1.15); }
  const ropes = new THREE.LineSegments(new THREE.BufferGeometry().setAttribute("position", new THREE.Float32BufferAttribute(ropePts, 3)), new THREE.LineBasicMaterial({ color: 0x3a3226 })); B.add(ropes);
  const flame = new THREE.Mesh(new THREE.ConeGeometry(.22, .9, 14), glowMat(0xffa050, 2.8)); flame.position.y = 2.6; B.add(flame);
  const flameCore = new THREE.Mesh(new THREE.ConeGeometry(.1, .5, 10), glowMat(0xbfe8ff, 2.2)); flameCore.position.y = 2.4; B.add(flameCore);
  const burner = new THREE.Mesh(new THREE.CylinderGeometry(.22, .26, .2, 14), iron); burner.position.y = 2.05; B.add(burner);
  const flamePos = new THREE.Vector3(); A.push({ pos: flamePos, color: 0xffa050, intensity: 3.5, flicker: true });
  // the cart: three parcels float up out of the garden into the basket; the gift card is turned away and falls back
  const box = (w, h, dd, col, ribbon) => { const g = new THREE.Group(); const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, dd), new THREE.MeshPhysicalMaterial({ color: col, roughness: .7, clearcoat: .1 })); m.castShadow = true; g.add(m);
    if (ribbon) { [[w + .02, .05, dd + .02], [.05, h + .02, dd + .02]].forEach((s) => g.add(new THREE.Mesh(new THREE.BoxGeometry(...s), new THREE.MeshStandardMaterial({ color: 0xd9b45a, metalness: .6, roughness: .4, emissive: 0x6a4a10 })))); } return g; };
  const items = [box(.42, .36, .36, 0x9a7b55, false), box(.8, .14, .32, 0x1e2422, false), box(.38, .38, .38, 0xd0675c, true)];
  const halos = [0x9fe0c8, 0x9fe0c8, 0xe8766a].map((col) => { const h = new THREE.Mesh(new THREE.SphereGeometry(.5, 16, 12), new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: .0, depthWrite: false, blending: THREE.AdditiveBlending })); return h; });
  const from = [[-4, .4, 3], [4.2, .5, 2.4], [.5, .4, 4.6]], into = [[-.3, 1.25, .1], [.25, 1.2, -.2], [0, 1.5, .25]];
  items.forEach((o, i) => { o.add(halos[i]); J.add(o); });
  const giftPos = new THREE.Vector3(); A.push({ pos: giftPos, color: 0xe8766a, intensity: 1.6, item: items[2] });
  const tmpP = new THREE.Vector3(), tmpL = new THREE.Vector3(), endP = new THREE.Vector3(.6, 4.4, -100.2), endL = new THREE.Vector3(.6, 4.6, -113.2);
  const state = { rise: 0 };
  return { sc: J, B, flame, envMat, state, update(p, t, dt) {
    // camera: from the ivy wall, a slow pan right to the garden; then it rises with the balloon
    const pan = smooth(clamp(p / .2)), rise = smooth(clamp((p - .76) / .24)); state.rise = rise;
    B.position.set(G.x, rise * 14, G.z - rise * 6); B.rotation.y = Math.sin(t * .2) * .06;
    tmpP.set(G.x - 9, 3.4 + rise * 13, G.z + 13 - rise * 4); tmpL.set(G.x - 1.5, 4.6 + rise * 13.5, G.z - rise * 6);
    camera.position.lerpVectors(endP, tmpP, pan); camera.position.x += px * .4; camera.position.y += -py * .2 + Math.sin(t * .6) * .03;
    look.lerpVectors(endL, tmpL, pan); camera.lookAt(look);
    // parcels: float up into the basket one by one; at .62 the gift card is turned away and falls into the hosta
    items.forEach((o, i) => { const q = smooth(clamp((p - .15 - i * .1) / .25));
      o.position.set(G.x + from[i][0] + (into[i][0] - from[i][0]) * q, from[i][1] + (into[i][1] - from[i][1]) * q + Math.sin(q * Math.PI) * 2.2 + B.position.y * q, G.z + from[i][2] + (into[i][2] - from[i][2]) * q - rise * 6 * q);
      o.rotation.y = t * .4 * (1 - q) + i; halos[i].material.opacity = (q > 0 && q < 1 ? .18 : .05) + (i === 2 ? .1 : 0);
      if (i === 2) { const out = smooth(clamp((p - .62) / .12)); o.position.x += out * 2.6; o.position.y = o.position.y * (1 - out) + (.4 + Math.sin(out * Math.PI) * 1.5) * out; o.position.z += out * 1.5;
        o.rotation.z = out * 2.2; giftPos.copy(o.position).y += .6; } });
    // the budget meter is the burner: the flame grows as spend + reserved climbs to ₹28,000 of ₹35,000
    const meter = .44 + .56 * smooth(clamp((p - .5) / .35)), fl = meter * (1 + .12 * Math.sin(t * 17) * Math.sin(t * 7.1));
    flame.scale.set(.7 + fl * .5, fl * 1.5, .7 + fl * .5); flameCore.scale.setScalar(.6 + fl * .6); envMat.emissiveIntensity = .05 + meter * .35 + rise * .15;
    flame.getWorldPosition(flamePos); flamePos.y += .6;
    journey.light(t, 1);
  } };
})();

const gate = (() => {
  const J = journey.sc, A = journey.anchors, GX = 36, GY = 20, GZ = -170;
  // the cliff: a heap of deformed rock rising out of the lake, the gatehouse on its lip
  const rockG = new THREE.IcosahedronGeometry(1, 4); { const pp = rockG.attributes.position; for (let i = 0; i < pp.count; i++) { const v = new THREE.Vector3().fromBufferAttribute(pp, i);
    const n = 1 + .18 * Math.sin(v.x * 5.1 + v.y * 2.3) * Math.sin(v.z * 4.3 + 1) + .08 * Math.sin(v.y * 13 + v.x * 7); v.multiplyScalar(n); pp.setXYZ(i, v.x, v.y, v.z); } rockG.computeVertexNormals(); }
  [[GX, -5, GZ - 4, 24, 21, 13], [GX - 19, -6, GZ - 4, 15, 20, 11], [GX + 20, -6, GZ - 5, 16, 21, 11], [GX - 3, -2, GZ + 7, 12, 8, 7]].forEach(([x, y, z, sx, sy, sz]) => {
    const r = new THREE.Mesh(rockG, stoneRock); r.position.set(x, y - 2, z); r.scale.set(sx, sy, sz); r.castShadow = r.receiveShadow = true; J.add(r); });
  const ledge = new THREE.Mesh(new THREE.BoxGeometry(18, 1.2, 10), traceryMat); ledge.position.set(GX, GY - .6, GZ + 1); ledge.receiveShadow = true; J.add(ledge);
  // the gatehouse: a wall with a tall pointed arch, two towers with steep roofs, a viridian glow on the arch, an iron portcullis
  const GW = 2.4, GS = 2.2, gh = new THREE.Group(); gh.position.set(GX, GY, GZ); J.add(gh);
  const wS = new THREE.Shape(); wS.moveTo(-8, 0); wS.lineTo(8, 0); wS.lineTo(8, 14); wS.lineTo(-8, 14); wS.lineTo(-8, 0); wS.holes.push(archPath(GW, GS, new THREE.Path()));
  const gw = new THREE.Mesh(new THREE.ExtrudeGeometry(wS, { depth: 1.6, bevelEnabled: false, curveSegments: 40 }), traceryMat); gw.position.z = -1.6; gw.castShadow = gw.receiveShadow = true; gh.add(gw);
  for (let i = -7; i <= 7; i += 2) { const m = new THREE.Mesh(new THREE.BoxGeometry(1, .9, 1.7), traceryMat); m.position.set(i, 14.45, -.8); gh.add(m); }
  [-1, 1].forEach((sd) => { const tw = new THREE.Mesh(new THREE.CylinderGeometry(1.9, 2.1, 18, 20), traceryMat); tw.position.set(sd * 8.6, 9, -.8); tw.castShadow = true; gh.add(tw);
    const roof = new THREE.Mesh(new THREE.ConeGeometry(2.4, 6, 20), new THREE.MeshPhysicalMaterial({ color: 0x1b2723, roughness: .6, clearcoat: .3 })); roof.position.set(sd * 8.6, 21, -.8); roof.castShadow = true; gh.add(roof);
    const win = new THREE.Mesh(new THREE.PlaneGeometry(.5, 1.1), glowMat(0xffc37a, 1.4)); win.position.set(sd * 8.6, 12, 1.15); gh.add(win);
    const fl = new THREE.Mesh(new THREE.ConeGeometry(.13, .38, 10), glowMat(0xffa860, 2.6)); fl.position.set(sd * (GW + 1.1), 3.4, .25); gh.add(fl);
    A.push({ pos: new THREE.Vector3(GX + sd * (GW + 1), GY + 3.6, GZ + .8), color: 0xffa860, intensity: 4, flicker: true, mesh: fl }); });
  const glowArch = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(archPath(GW - .02, GS, new THREE.Path()).getSpacedPoints(60).map((v) => new THREE.Vector3(v.x, v.y, .05))), 80, .04, 6, false),
    new THREE.MeshBasicMaterial({ color: 0x7fd1b9, transparent: true, opacity: .8 })); gh.add(glowArch);
  const port = new THREE.Group();
  for (let i = -4; i <= 4; i++) { const bar = new THREE.Mesh(new THREE.CylinderGeometry(.055, .055, 6.6, 10), iron); bar.position.set(i * .52, 3.3, 0); bar.castShadow = true; port.add(bar);
    const sp = new THREE.Mesh(new THREE.ConeGeometry(.09, .3, 10), iron); sp.position.set(i * .52, -.13, 0); sp.rotation.x = Math.PI; port.add(sp); }
  for (let j = 0; j < 6; j++) { const h = new THREE.Mesh(new THREE.BoxGeometry(4.6, .09, .1), iron); h.position.y = .45 + j * 1.15; port.add(h); }
  port.position.z = -.6; gh.add(port);
  // a lit path of stone steps from the ledge's lip into the arch; the two payments are lights that leave the basket
  const good = orb(0x9fe0c8, 2.4, .2, 0), bad = orb(0xe8766a, 2.2, .2, 0); J.add(good, bad);
  A.push({ pos: good.position, color: 0x9fe0c8, intensity: 2.6 }, { pos: bad.position, color: 0xe8766a, intensity: 2.6 });
  const tmp = new THREE.Vector3(), bStart = new THREE.Vector3(36, 14, -110), bEnd = new THREE.Vector3(GX + 7.5, GY + 1.5, GZ + 6.5);
  const c0 = new THREE.Vector3(27, 16.4, -95), l0 = new THREE.Vector3(34.5, 18.1, -110), c1 = new THREE.Vector3(GX - 3, GY + 3.2, GZ + 19), l1 = new THREE.Vector3(GX + 1.5, GY + 5.6, GZ);
  return { sc: J, update(p, t, dt) {
    const drift = smooth(clamp(p / .3)), B = balloon.B;
    B.position.lerpVectors(bStart, bEnd, drift); B.position.y += Math.sin(drift * Math.PI) * 4 + Math.sin(t * .7) * .12; B.rotation.y = Math.sin(t * .2) * .06;
    balloon.envMat.emissiveIntensity = .4 + .1 * Math.sin(t * 3); balloon.flame.scale.set(1.15, 1.6 * (1 + .1 * Math.sin(t * 17)), 1.15);
    // camera: behind and beside the balloon on the way over, then settles facing the gate
    const settle = smooth(clamp(p / .34));
    camera.position.lerpVectors(c0, c1, settle); camera.position.y += Math.sin(settle * Math.PI) * 3; camera.position.x += px * .4; camera.position.y -= py * .2;
    look.lerpVectors(l0, l1, settle); camera.lookAt(look);
    // the approved payment rides a light through the arch; the risky one stops at the portcullis, which slams at .72
    const basket = tmp.copy(B.position).setY(B.position.y + 1.6);
    const gq = smooth(clamp((p - .1) / .7)), bq = clamp((p - .3) / .42);
    const path = (q, out) => { if (q < .5) out.lerpVectors(basket, new THREE.Vector3(GX, GY + 1.3, GZ + 1.5), smooth(q * 2)); else out.set(GX, GY + 1.3, GZ + 1.5 - (q - .5) * 2 * 16); out.y += Math.sin(t * 3) * .05; };
    path(gq, good.position); path(Math.min(.5, bq * .5 + .001), bad.position);
    good.visible = p > .1; bad.visible = p > .3;
    const drop = smooth(clamp((p - .72) / .05)); port.position.y = (1 - drop) * 7.1;
    const stopped = bq >= 1 && drop >= 1;
    bad.children[0].scale.setScalar(stopped ? 1 + Math.sin(t * 6) * .14 : 1); if (stopped) bad.position.z += Math.sin(t * 9) * .02;
    glowArch.material.color.setHex(stopped ? 0xe8766a : 0x7fd1b9); glowArch.material.opacity = .6 + .25 * Math.sin(t * 1.4) + (stopped ? .2 : 0);
    journey.light(t, 1);
  } };
})();

// ------------------------------------------------------------------ the journey (stage C): over the gate, a gothic city across a second lake.
// The red team's attacks break on a shield of light over it; then the camera rises to a panorama where each piece of evidence is a tower.
const city = (() => {
  const J = journey.sc, C = new THREE.Vector3(36, 0, -335), d = new THREE.Object3D(), c = new THREE.Color();
  // blocks of houses and halls, taller toward the centre; spires on the tall ones; thousands of lit windows facing the lake
  const stoneDark = new THREE.MeshStandardMaterial({ color: 0x223029, roughness: .82, metalness: .05 });
  const roofMat = new THREE.MeshStandardMaterial({ color: 0x18231f, roughness: .6, metalness: .2 });
  const BN = 620, blocks = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1).translate(0, .5, 0), stoneDark, BN);
  const SN = 260, spires = new THREE.InstancedMesh(new THREE.ConeGeometry(1, 1, 6).translate(0, .5, 0), roofMat, SN);
  const WN = 7000, winGeo = new THREE.PlaneGeometry(.42, .7), winMat = new THREE.MeshBasicMaterial({ color: 0xffffff, toneMapped: false });
  const wins = new THREE.InstancedMesh(winGeo, winMat, WN), mirror = new THREE.InstancedMesh(winGeo, new THREE.MeshBasicMaterial({ color: 0xc8c8c8, toneMapped: false, side: THREE.DoubleSide }), WN);
  const flip = new THREE.Matrix4().makeScale(1, -1, 1), stretch = new THREE.Matrix4().makeScale(.8, 2.2, 1);  // reflections smear a little on the water
  let bi = 0, si = 0, wi = 0;
  const winCols = [0xffc37a, 0xffb060, 0xffd9a0, 0x8fe0c4, 0xe8a060];
  const addWindows = (x, z, w, h) => { const cols = Math.max(1, Math.floor(w / 1.15)), rows = Math.max(1, Math.floor((h - 1) / 1.7));
    for (let r = 0; r < rows; r++) for (let k = 0; k < cols; k++) { if (wi >= WN || rng() > .5) continue;
      d.position.set(x - w / 2 + (k + .5) * (w / cols), 1 + r * 1.7, z + .02); d.rotation.set(0, 0, 0); d.scale.setScalar(1); d.updateMatrix();
      wins.setMatrixAt(wi, d.matrix); mirror.setMatrixAt(wi, new THREE.Matrix4().multiplyMatrices(flip, d.matrix).multiply(stretch));
      const k2 = rng(); c.setHex(winCols[k2 < .08 ? 3 : (rng() * 5) | 0]).multiplyScalar(.7 + rng() * .9); if (k2 < .08) c.setHex(winCols[3]);
      wins.setColorAt(wi, c); mirror.setColorAt(wi, c); wi++; } };
  for (let row = 0; row < 9 && bi < BN; row++) {
    let x = -78 + rng() * 4; const z0 = -290 - row * 11;
    while (x < 150 && bi < BN) {
      const w = 3 + rng() * 5, dz = 4 + rng() * 5, cx = Math.abs(x + w / 2 - C.x) / 110, h = (5 + rng() * 9) * (1.6 - cx) + row * 1.4 + (rng() < .08 ? 14 : 0);
      const z = z0 - rng() * 3;
      if (Math.abs(x + w / 2 - 95) < 13 && z > -306) { x += w + .4; continue; }
      d.position.set(x + w / 2, 0, z); d.rotation.set(0, 0, 0); d.scale.set(w, h, dz); d.updateMatrix(); blocks.setMatrixAt(bi, d.matrix);
      c.setHex(0x223029).offsetHSL(0, 0, (rng() - .5) * .05); blocks.setColorAt(bi++, c);
      if (si < SN && (h > 13 || rng() < .25)) { d.position.set(x + w / 2, h, z); d.scale.set(w * .5, 3 + rng() * (h > 16 ? 10 : 4), dz * .5); d.updateMatrix(); spires.setMatrixAt(si++, d.matrix); }
      addWindows(x + w / 2, z + dz / 2, w, h);
      x += w + .4 + rng() * 1.6;
    }
  }
  blocks.count = bi; spires.count = si; wins.count = wi; mirror.count = wi;
  blocks.receiveShadow = true; J.add(blocks, spires, wins, mirror);
  // eight landmark towers, one per piece of evidence (left to right in time); ivy climbs them; a lantern crown shows what the replay may use
  const EVT = [0, 0, 24, 61, 88, 120, 136, 172], DEC = 120, marks = [];
  const crownGeo = new THREE.SphereGeometry(1.1, 20, 14), beamGeo = new THREE.CylinderGeometry(.25, .9, 46, 16, 1, true).translate(0, 30, 0);
  const ivyT = new THREE.InstancedMesh(leafGeo, leafMat, 1600); let li = 0;
  const streetN = 8 * 14, street = new THREE.InstancedMesh(new THREE.SphereGeometry(.32, 10, 8), new THREE.MeshBasicMaterial({ color: 0xffffff, toneMapped: false }), streetN);
  EVT.forEach((tv, i) => {
    const x = -46 + i * 23 + (rng() - .5) * 4, z = -318 - (i % 3) * 14, h = 30 + (i === 5 ? 14 : 0) + rng() * 8, g = new THREE.Group(); g.position.set(x, 0, z); J.add(g);
    const body = new THREE.Mesh(new THREE.BoxGeometry(5, h, 5).translate(0, h / 2, 0), stoneDark); g.add(body);
    [-1, 1].forEach((sd) => { const b = new THREE.Mesh(new THREE.BoxGeometry(1.4, h * .8, 1.4).translate(0, h * .4, 0), stoneDark); b.position.set(sd * 3, 0, 2.2); g.add(b); });
    const sp = new THREE.Mesh(new THREE.ConeGeometry(3.4, 12, 8).translate(0, 6, 0), roofMat); sp.position.y = h; g.add(sp);
    const crown = new THREE.Mesh(crownGeo, new THREE.MeshBasicMaterial({ color: 0x24382f, toneMapped: false })); crown.position.y = h + 1.4; g.add(crown);
    const beam = new THREE.Mesh(beamGeo, new THREE.MeshBasicMaterial({ color: 0x7fd1b9, transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide })); beam.position.y = h + 1.4; g.add(beam);
    for (let k = 0; k < 190 && li < 1600; k++) { const yy = Math.pow(rng(), 1.4) * h * .85, xx = (rng() - .5) * 5;
      d.position.set(x + xx, yy, z + 2.56); d.lookAt(x + xx, yy + .3, z + 4); d.rotateZ((rng() - .5) * 2.4); d.scale.setScalar(1.4 + rng() * 1.4); d.updateMatrix(); ivyT.setMatrixAt(li, d.matrix);
      c.setHex(hues[(rng() * 5) | 0]); ivyT.setColorAt(li++, c); }
    for (let k = 0; k < 14; k++) { const u = k / 13; d.position.set(x + (C.x - x) * .15 * (1 - u) + Math.sin(u * 5 + i) * 1.5, .9, -283 + (z + 4 + 283) * u); d.rotation.set(0, 0, 0); d.scale.setScalar(1); d.updateMatrix(); street.setMatrixAt(i * 14 + k, d.matrix); }
    // the windows of every tower face the lake
    const cols = 3, rows = Math.floor(h / 2.4); for (let r = 0; r < rows; r++) for (let k = 0; k < cols; k++) { if (wi >= WN || rng() > .55) continue;
      d.position.set(x - 1.6 + k * 1.6, 2 + r * 2.4, z + 2.52); d.rotation.set(0, 0, 0); d.scale.set(1.3, 1.3, 1); d.updateMatrix(); wins.setMatrixAt(wi, d.matrix); mirror.setMatrixAt(wi, new THREE.Matrix4().multiplyMatrices(flip, d.matrix).multiply(stretch));
      c.setHex(winCols[(rng() * 3) | 0]); wins.setColorAt(wi, c); mirror.setColorAt(wi, c); wi++; }
    marks.push({ t: tv, dec: i === 5, crown, beam, i });
  });
  wins.count = wi; mirror.count = wi; ivyT.count = li; J.add(ivyT, street);
  // gold dust drifting over the roofs
  const GD = 1400, gdGeo = new THREE.BufferGeometry(), gdPos = new Float32Array(GD * 3), gdPh = new Float32Array(GD);
  for (let i = 0; i < GD; i++) { gdPos.set([-80 + rng() * 240, 2 + rng() * 50, -280 - rng() * 110], i * 3); gdPh[i] = rng() * 6.28; }
  gdGeo.setAttribute("position", new THREE.BufferAttribute(gdPos, 3));
  const dust = new THREE.Points(gdGeo, new THREE.PointsMaterial({ size: .5, map: ffSprite, color: 0xe8c27a, transparent: true, opacity: .7, depthWrite: false, blending: THREE.AdditiveBlending })); J.add(dust);
  // the shield: a flattened dome of light over the city; attacks that hit it ripple outward
  const HITS = 6, shieldU = { strength: { value: 0 }, t: { value: 0 }, hits: { value: Array.from({ length: HITS }, () => new THREE.Vector4(0, -999, 0, 9)) } };
  const shield = new THREE.Mesh(new THREE.SphereGeometry(1, 72, 36, 0, Math.PI * 2, 0, Math.PI / 2), new THREE.ShaderMaterial({
    uniforms: shieldU, transparent: true, depthWrite: false, side: THREE.FrontSide, blending: THREE.AdditiveBlending,
    vertexShader: "varying vec3 vW; varying vec3 vN; varying vec3 vV; void main(){ vec4 w = modelMatrix * vec4(position,1.); vW = w.xyz; vN = normalize(mat3(modelMatrix) * normal); vV = normalize(cameraPosition - w.xyz); gl_Position = projectionMatrix * viewMatrix * w; }",
    fragmentShader: `uniform float strength; uniform float t; uniform vec4 hits[${HITS}]; varying vec3 vW; varying vec3 vN; varying vec3 vV;
      void main(){ float f = pow(1. - abs(dot(normalize(vN), vV)), 2.2); float hex = .5 + .5 * sin(vW.x * .9 + t * .6) * sin(vW.y * .9) * sin(vW.z * .9 - t * .4);
        float a = (pow(f, 2.6) * .34 + hex * .012) * strength; vec3 col = vec3(.5, .86, .74) * a;
        for (int i = 0; i < ${HITS}; i++) { float age = hits[i].w; if (age > 1.5) continue; float dd = distance(vW, hits[i].xyz); float fade = 1. - age / 1.5, ring = exp(-pow((dd - age * 18.) * .9, 2.)) * fade * fade;
          col += vec3(.95, .55, .45) * ring * .35 + vec3(.6, .95, .8) * exp(-dd * .3) * fade * .35; }
        gl_FragColor = vec4(col, 1.);
      }` }));
  shield.scale.set(118, 62, 70); shield.position.copy(C).setZ(C.z + 5); J.add(shield);
  // three waves of attacks, sixteen each; each wave the shield is stronger (rules 22.4%, graph 86.7%, hardened 93.9% caught)
  const AN = 48, caughtPer = [4, 14, 15], atk = new THREE.InstancedMesh(new THREE.SphereGeometry(1.3, 12, 10), new THREE.MeshBasicMaterial({ color: new THREE.Color(0xe8766a).multiplyScalar(2.2), toneMapped: false }), AN);
  const attacks = []; for (let w = 0; w < 3; w++) { const caught = new Set(); while (caught.size < caughtPer[w]) caught.add((rng() * 16) | 0);
    for (let k = 0; k < 16; k++) { const from = new THREE.Vector3(-60 + rng() * 190, 4 + rng() * 10, -215 - rng() * 15), ang = rng() * Math.PI - Math.PI / 2;
      const hit = new THREE.Vector3(C.x + Math.sin(ang) * 118 * .62, 8 + rng() * 26, C.z + 5 + 70 * .82); hit.x = Math.max(-60, Math.min(130, from.x * .5 + hit.x * .5));
      attacks.push({ w, k, from, hit, caught: caught.has(k), start: .06 + w * .24 + k * .009, hitSlot: -1 }); } }
  d.position.set(0, -500, 0); d.scale.setScalar(0); d.updateMatrix(); for (let i = 0; i < AN; i++) atk.setMatrixAt(i, d.matrix);
  J.add(atk);
  const hitsAge = new Float32Array(HITS).fill(9); let hitNext = 0;
  const scrub = document.getElementById("scrub");
  const tmp = new THREE.Vector3(), P = new THREE.Vector3(), L = new THREE.Vector3();
  const cam = {   // keyframes: over the gate wall, down to the far lip of the cliff facing the city, then up to the panorama
    rt: [[0, [33, 23.2, -151], [37.5, 25.6, -170]], [.22, [36, 38, -168], [36, 20, -260]], [.45, [36, 27, -184], [36, 14, -330]], [1, [30, 26, -190], [38, 13, -335]]],
    rp: [[0, [30, 26, -190], [38, 13, -335]], [.14, [95, 56.9, -257], [95, 56.5, -276]], [.24, [95, 56.4, -276.2], [95, 59.4, -290]], [.44, [92.6, 56.4, -276.3], [97.2, 60.4, -289]],
         [.62, [97.4, 57, -276.6], [93.4, 60.2, -289.5]], [.75, [95, 55.8, -287.6], [95, 55.5, -300]], [.88, [95, 56, -292.6], [78, 44, -360]], [1, [94, 57, -294.2], [58, 34, -392]]] };
  const along = (K, p) => { let i = 0; while (i < K.length - 2 && p > K[i + 1][0]) i++; const a = K[i], b = K[i + 1], f = smooth(clamp((p - a[0]) / (b[0] - a[0])));
    P.set(...a[1]).lerp(tmp.set(...b[1]), f); L.set(...a[2]).lerp(tmp.set(...b[2]), f); };
  let lastT = -1;
  const common = (t, dt) => {
    for (let i = 0; i < GD; i++) { gdPh[i] += dt * .3; gdPos[i * 3] += Math.cos(gdPh[i]) * dt * .25; gdPos[i * 3 + 1] += Math.sin(gdPh[i] * 1.3) * dt * .18; }
    for (let i = 0; i < HITS; i++) { hitsAge[i] += dt; shieldU.hits.value[i].w = hitsAge[i]; }
    gdGeo.attributes.position.needsUpdate = true; dust.material.opacity = .5 + .2 * Math.sin(t * .8); shieldU.t.value = t;
    camera.position.copy(P); camera.position.x += px * .8; camera.position.y -= py * .4; look.copy(L); camera.lookAt(look);
    journey.light(t, 1);
  };
  const setTowers = (T) => { if (T === lastT) return; lastT = T;
    marks.forEach((M) => { const known = M.t <= T, col = !known ? 0x24382f : M.dec ? 0xf5b84b : M.t <= DEC ? 0x7fd1b9 : 0xe8766a;
      M.crown.material.color.setHex(col).multiplyScalar(known ? 2.2 : 1); M.beam.material.color.setHex(col); M.beam.material.opacity = known ? (M.t > DEC ? .02 : M.dec ? .05 : .03) : 0;
      for (let k = 0; k < 14; k++) { c.setHex(known ? col : 0x1c2a24).multiplyScalar(known ? 1.8 : 1); street.setColorAt(M.i * 14 + k, c); } });
    street.instanceColor.needsUpdate = true; };
  setTowers(200);
  return {
    sc: J,
    redteam: { sc: J, update(p, t, dt) {
      along(cam.rt, p); common(t, dt); setTowers(200);
      shieldU.strength.value = .25 + smooth(clamp((p - .3) / .2)) * .45 + smooth(clamp((p - .55) / .2)) * .35;
      attacks.forEach((A, i) => { const q = (p - A.start) / .1;
        if (q <= 0) { d.scale.setScalar(0); if (A.hitSlot >= 0) A.hitSlot = -1; }
        else if (q < 1 || !A.caught) { const qq = Math.min(q, A.caught ? 1 : 1.6); tmp.lerpVectors(A.from, A.hit, qq); tmp.y += Math.sin(Math.min(1, qq) * Math.PI) * 6;
          if (!A.caught && qq > 1) tmp.y -= (qq - 1) * 14;            // the ones that slip through sink into the city, dimming
          d.position.copy(tmp); d.scale.setScalar(!A.caught && qq > 1 ? Math.max(0, 1 - (qq - 1) * 1.6) : 1); }
        else { d.scale.setScalar(0); if (A.hitSlot < 0) { A.hitSlot = hitNext; shieldU.hits.value[hitNext].set(A.hit.x, A.hit.y, A.hit.z, 0); hitsAge[hitNext] = 0; hitNext = (hitNext + 1) % HITS; } }
        d.rotation.set(0, 0, 0); d.updateMatrix(); atk.setMatrixAt(i, d.matrix); });
      atk.instanceMatrix.needsUpdate = true;
    } },
    replay: { sc: J, update(p, t, dt) {
      along(cam.rp, p); common(t, dt); shieldU.strength.value = Math.max(.12, .9 - p * 1.6);
      atk.count = 0; const T = scrub ? +scrub.value : 200; setTowers(T); clockTower.update(p, t, dt, T);
      const bq = smooth(clamp((p - .3) / .55)), B = balloon.B; B.position.set(43.5 + (79 - 43.5) * bq, 21.5 + (61 - 21.5) * bq + Math.sin(bq * Math.PI) * 18 + Math.sin(t * .7) * .15, -163.5 + (-314 + 163.5) * bq);   // the balloon follows you to the tower
      marks.forEach((M) => { M.beam.rotation.y = t * .1; });
    } } };
})();

// ------------------------------------------------------------------ the clockwork tower: the replay happens inside a clock. You glide in through a high arch;
// brass and verdigris gears turn as the slider drags time; a spiral of hanging lanterns are the evidence (mint used, amber the decision, coral
// arrived later). At the end the great clock face swings open like a window and you step out onto a balcony over the city.
const clockTower = (() => {
  const J = journey.sc, A = journey.anchors, X = 95, Z = -282, FL = 52, CEIL = 72, d = new THREE.Object3D(), c = new THREE.Color();
  const CX = X, CY = FL + 6.4, CZ = -290, CR = 4.2;        // the clock face, in the north wall
  const T0 = new THREE.Group(); J.add(T0);
  const base = new THREE.Mesh(new THREE.BoxGeometry(16, FL, 16).translate(0, FL / 2, 0), traceryMat); base.position.set(X, 0, Z); base.castShadow = base.receiveShadow = true; T0.add(base);
  const inner = stoneAt(4, 3), H = CEIL - FL + 2;
  // south wall with the high arch you enter by
  const sS = new THREE.Shape(); sS.moveTo(-8, 0); sS.lineTo(8, 0); sS.lineTo(8, H); sS.lineTo(-8, H); sS.lineTo(-8, 0);
  const ah = archPath(2.3, 3.2, new THREE.Path()); sS.holes.push(new THREE.Path(ah.getPoints(48).map((v) => new THREE.Vector2(v.x, v.y + .6))));
  const south = new THREE.Mesh(new THREE.ExtrudeGeometry(sS, { depth: 1, bevelEnabled: false, curveSegments: 40 }), inner); south.position.set(X, FL, Z + 7); south.castShadow = south.receiveShadow = true; T0.add(south);
  const archRim = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(archPath(2.3, 3.2, new THREE.Path()).getSpacedPoints(60).map((v) => new THREE.Vector3(v.x, v.y + .6, 1.05))), 80, .05, 6, false), new THREE.MeshBasicMaterial({ color: 0x7fd1b9, transparent: true, opacity: .7 }));
  archRim.position.set(X, FL, Z + 7); T0.add(archRim);
  // north wall with the round clock window
  const nS = new THREE.Shape(); nS.moveTo(-8, 0); nS.lineTo(8, 0); nS.lineTo(8, H); nS.lineTo(-8, H); nS.lineTo(-8, 0);
  const hole = new THREE.Path(); hole.absarc(0, CY - FL, CR, 0, Math.PI * 2, true); nS.holes.push(hole);
  const north = new THREE.Mesh(new THREE.ExtrudeGeometry(nS, { depth: 1, bevelEnabled: false, curveSegments: 64 }), inner); north.position.set(X, FL, Z - 9); north.castShadow = north.receiveShadow = true; T0.add(north);
  [-1, 1].forEach((sd) => { const w = new THREE.Mesh(new THREE.BoxGeometry(1, H, 16), inner); w.position.set(X + sd * 7.5, FL + H / 2, Z); w.castShadow = w.receiveShadow = true; T0.add(w); });
  const ceil = new THREE.Mesh(new THREE.BoxGeometry(16, 1.2, 18), traceryMat); ceil.position.set(X, CEIL + .6, Z - 1); ceil.castShadow = true; T0.add(ceil);
  const roof = new THREE.Mesh(new THREE.ConeGeometry(12, 20, 4), new THREE.MeshPhysicalMaterial({ color: 0x1b2723, roughness: .6, clearcoat: .3 })); roof.rotation.y = Math.PI / 4; roof.position.set(X, CEIL + 11.2, Z - 1); roof.castShadow = true; T0.add(roof);
  [-1, 1].forEach((sx) => [-1, 1].forEach((sz) => { const pin = new THREE.Mesh(new THREE.ConeGeometry(.9, 9, 6), roof.material); pin.position.set(X + sx * 8, CEIL + 5.5, Z + sz * 8); T0.add(pin); }));
  const floorC = new THREE.Mesh(new THREE.PlaneGeometry(14, 16), floor.material); floorC.rotation.x = -Math.PI / 2; floorC.position.set(X, FL + .02, Z - 1); floorC.receiveShadow = true; T0.add(floorC);
  // the balcony outside the clock window
  const bal = new THREE.Mesh(new THREE.BoxGeometry(12, .6, 5), traceryMat); bal.position.set(X, FL - .3, CZ - 3.4); bal.castShadow = bal.receiveShadow = true; T0.add(bal);
  for (let i = -5; i <= 5; i++) { const b = new THREE.Mesh(new THREE.CylinderGeometry(.13, .17, 1, 8), traceryMat); b.position.set(X + i * 1.1, FL + .5, CZ - 5.6); T0.add(b); }
  const rail = new THREE.Mesh(new THREE.BoxGeometry(12, .22, .4), traceryMat); rail.position.set(X, FL + 1.1, CZ - 5.6); T0.add(rail);
  // ivy creeping through the cracks inside and up the outside
  const IV = 520, ivy = new THREE.InstancedMesh(leafGeo, leafMat, IV);
  for (let i = 0; i < IV; i++) { const side = i % 3, y = FL + Math.pow(rng(), 1.5) * (H - 2);
    if (side === 0) { const sx = rng() < .5 ? -1 : 1; d.position.set(X + sx * 6.95, y, Z - 7 + rng() * 14); d.lookAt(X, y + .3, d.position.z); }
    else if (side === 1) { d.position.set(X - 7 + rng() * 14, y * .5 + FL * .5 + rng() * 6, Z + 8.08); d.lookAt(d.position.x, d.position.y + .3, Z + 10); }
    else { const a = rng() * Math.PI * 2, r = CR + .4 + rng() * 1.8; d.position.set(CX + Math.cos(a) * r, CY + Math.sin(a) * r, CZ - 1.05); d.lookAt(d.position.x, d.position.y + .3, CZ - 3); }
    d.rotateZ((rng() - .5) * 2.2); d.scale.setScalar(.45 + rng() * .5); d.updateMatrix(); ivy.setMatrixAt(i, d.matrix); c.setHex(hues[(rng() * 5) | 0]); ivy.setColorAt(i, c); }
  T0.add(ivy);
  // gears: brass and verdigris, on both side walls and above the clock
  const gearGeo = (r, n) => { const s = new THREE.Shape(); for (let i = 0; i < n; i++) { const a0 = (i / n) * Math.PI * 2, da = Math.PI * 2 / n;
      [[a0, r * .84], [a0 + da * .12, r], [a0 + da * .45, r], [a0 + da * .57, r * .84]].forEach(([a, rr], k) => (i === 0 && k === 0 ? s.moveTo : s.lineTo).call(s, Math.cos(a) * rr, Math.sin(a) * rr)); }
    const hc = new THREE.Path(); hc.absarc(0, 0, r * .16, 0, Math.PI * 2, true); s.holes.push(hc);
    for (let k = 0; k < 5; k++) { const a = k / 5 * Math.PI * 2, h2 = new THREE.Path(); h2.absarc(Math.cos(a) * r * .52, Math.sin(a) * r * .52, r * .19, 0, Math.PI * 2, true); s.holes.push(h2); }
    return new THREE.ExtrudeGeometry(s, { depth: .32, bevelEnabled: true, bevelThickness: .04, bevelSize: .04, bevelSegments: 1, curveSegments: 6 }); };
  const brass = new THREE.MeshPhysicalMaterial({ color: 0xb08a4a, metalness: .85, roughness: .36, clearcoat: .2 });
  const verd = new THREE.MeshPhysicalMaterial({ color: 0x4f8a76, metalness: .45, roughness: .55 });
  const gears = [];
  [[-1, FL + 12.5, -285, 3.4, 28, .8], [-1, FL + 7, -279.5, 2.2, 18, -1.25], [-1, FL + 17, -278, 1.6, 14, 1.7], [1, FL + 10.5, -284, 3, 24, -.9], [1, FL + 16, -279, 2.1, 16, 1.3], [1, FL + 5, -279, 1.5, 12, -1.8]].forEach(([sd, y, z, r, n, k], i) => {
    const g = new THREE.Mesh(gearGeo(r, n), i % 2 ? verd : brass); g.position.set(X + sd * 6.75, y, z); g.rotation.y = sd * Math.PI / 2; g.castShadow = true; T0.add(g);
    const axle = new THREE.Mesh(new THREE.CylinderGeometry(r * .15, r * .15, .8, 12), iron); axle.rotation.z = Math.PI / 2; axle.position.copy(g.position); T0.add(axle);
    gears.push({ g, k }); });
  [[-4.4, FL + 14.5, 2.4, 18, 1.1], [4.2, FL + 15, 1.9, 14, -1.4], [0, FL + 16.6, 1.3, 10, 2]].forEach(([dx, y, r, n, k], i) => { const g = new THREE.Mesh(gearGeo(r, n), i % 2 ? brass : verd); g.position.set(X + dx, y, CZ + 1.3); g.castShadow = true; T0.add(g); gears.push({ g, k, face: true }); });
  // the pendulum
  const pend = new THREE.Group(); pend.position.set(X + 5.6, CEIL - .2, CZ + 1.4); T0.add(pend);
  const rod = new THREE.Mesh(new THREE.CylinderGeometry(.05, .05, 12, 8), brass); rod.position.y = -6; pend.add(rod);
  const bob = new THREE.Mesh(new THREE.CylinderGeometry(1, 1, .22, 40), brass); bob.rotation.x = Math.PI / 2; bob.position.y = -12; bob.scale.setScalar(.8); bob.castShadow = true; pend.add(bob);
  // a spiral of hanging lanterns; eight of them are the evidence
  const EV = [0, 0, 24, 61, 88, 120, 136, 172], DEC = 120, lan = [], chainPts = [];
  for (let k = 0; k < 12; k++) { const a = k * .95 + .4, r = 3.6 + (k % 2) * .5, y = CEIL - 2.2 - k * 1.25, g = new THREE.Group(); g.position.set(X + Math.cos(a) * r, y, Z - 1 + Math.sin(a) * r * .8);
    const sh = new THREE.Mesh(new THREE.SphereGeometry(.26, 18, 12), k >= 2 && k < 10 ? glassL.clone() : glassL), co = new THREE.Mesh(new THREE.SphereGeometry(.13, 12, 10), new THREE.MeshBasicMaterial({ color: 0xffc37a, toneMapped: false }));
    const cap = new THREE.Mesh(new THREE.CylinderGeometry(.09, .16, .1, 12), iron); cap.position.y = .28; g.add(sh, co, cap); T0.add(g);
    chainPts.push(g.position.x, y + .32, g.position.z, g.position.x, CEIL, g.position.z);
    const ev = k >= 2 && k < 10 ? k - 2 : -1, anc = { pos: g.position, color: 0xffc37a, intensity: 30, dist: 14, flicker: true }; A.push(anc);
    lan.push({ g, co, sh, ev, anc, ph: rng() * 6.28, y0: y }); }
  T0.add(new THREE.LineSegments(new THREE.BufferGeometry().setAttribute("position", new THREE.Float32BufferAttribute(chainPts, 3)), new THREE.LineBasicMaterial({ color: 0x2c2a24 })));
  // the great clock face: translucent glass with tracery and numerals; two leaves that swing open; hands and evidence marks ride on them
  const faceTex = (() => { const N = 1024, cv = document.createElement("canvas"); cv.width = cv.height = N; const x = cv.getContext("2d"), m = N / 2;
    const g = x.createRadialGradient(m, m, 0, m, m, m); g.addColorStop(0, "rgba(232,245,238,.13)"); g.addColorStop(.7, "rgba(190,220,205,.07)"); g.addColorStop(1, "rgba(160,200,185,.14)"); x.fillStyle = g; x.beginPath(); x.arc(m, m, m - 4, 0, 7); x.fill();
    x.strokeStyle = "rgba(201,162,78,.95)"; x.lineWidth = 14; x.beginPath(); x.arc(m, m, m - 12, 0, 7); x.stroke(); x.lineWidth = 6; x.beginPath(); x.arc(m, m, m * .72, 0, 7); x.stroke(); x.beginPath(); x.arc(m, m, m * .2, 0, 7); x.stroke();
    for (let i = 0; i < 12; i++) { const a = i / 12 * Math.PI * 2; x.lineWidth = 5; x.beginPath(); x.moveTo(m + Math.cos(a) * m * .2, m + Math.sin(a) * m * .2); x.lineTo(m + Math.cos(a) * m * .72, m + Math.sin(a) * m * .72); x.stroke();
      x.beginPath(); x.arc(m + Math.cos(a + .26) * m * .46, m + Math.sin(a + .26) * m * .46, m * .1, 0, 7); x.stroke(); }
    x.fillStyle = "rgba(225,200,140,.95)"; x.font = "600 64px Georgia, serif"; x.textAlign = "center"; x.textBaseline = "middle";
    ["XII", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI"].forEach((s, i) => { const a = i / 12 * Math.PI * 2 - Math.PI / 2; x.fillText(s, m + Math.cos(a) * m * .85, m + Math.sin(a) * m * .85); });
    const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4; return t; })();
  const faceMat = new THREE.MeshBasicMaterial({ map: faceTex, transparent: true, depthWrite: false, side: THREE.DoubleSide, toneMapped: false, color: new THREE.Color(.95, .95, .95) });
  const leaves2 = [-1, 1].map((sd) => { const pv = new THREE.Group(); pv.position.set(CX + sd * CR, CY, CZ - .5); T0.add(pv);
    const m = new THREE.Mesh(new THREE.CircleGeometry(CR, 64, sd < 0 ? -Math.PI / 2 : Math.PI / 2, Math.PI), faceMat); m.position.x = -sd * CR; pv.add(m);
    const frame = new THREE.Mesh(new THREE.TorusGeometry(CR, .12, 8, 64, Math.PI), brass); frame.rotation.z = sd < 0 ? Math.PI / 2 : -Math.PI / 2; frame.position.x = -sd * CR; pv.add(frame);
    const bar = new THREE.Mesh(new THREE.BoxGeometry(.18, CR * 2, .18), brass); bar.position.x = -sd * CR - sd * .02; pv.add(bar);
    return { pv, sd }; });
  const hands = new THREE.Group(); hands.position.set(-(-1) * CR, 0, .25); leaves2[0].pv.add(hands);
  const hourH = new THREE.Mesh(new THREE.BoxGeometry(.18, 1.9, .1).translate(0, .95, 0), brass), minH = new THREE.Mesh(new THREE.BoxGeometry(.11, 3.1, .08).translate(0, 1.55, 0), brass);
  hourH.position.z = .05; minH.position.z = .12; hands.add(hourH, minH, new THREE.Mesh(new THREE.CylinderGeometry(.3, .3, .3, 16).rotateX(Math.PI / 2), brass));
  const marks = EV.map((tv, i) => { const a = tv / 200 * Math.PI * 2 + (i === 1 ? .08 : 0), px2 = Math.sin(a) * (CR - .9), py2 = Math.cos(a) * (CR - .9), L = px2 < 0 || (px2 === 0 && i === 0) ? leaves2[0] : leaves2[1];
    const m = new THREE.Mesh(new THREE.SphereGeometry(.3, 14, 10), new THREE.MeshBasicMaterial({ color: 0x24382f, toneMapped: false })); m.position.set(px2 + L.sd * CR, py2, .22); L.pv.add(m); return { m, t: tv, dec: i === 5 }; });
  // moonbeams through the clock window, dust in them
  const DN = 600, dGeo = new THREE.BufferGeometry(), dPos = new Float32Array(DN * 3), dPh = new Float32Array(DN);
  for (let i = 0; i < DN; i++) { dPos.set([X - 6.5 + rng() * 13, FL + rng() * (H - 2), Z - 8 + rng() * 15], i * 3); dPh[i] = rng() * 6.28; }
  dGeo.setAttribute("position", new THREE.BufferAttribute(dPos, 3));
  const motes = new THREE.Points(dGeo, new THREE.PointsMaterial({ size: .09, map: ffSprite, color: 0xf0d79a, transparent: true, opacity: .8, depthWrite: false, blending: THREE.AdditiveBlending })); T0.add(motes);
  A.push({ pos: new THREE.Vector3(X, FL + 5, Z + 5.5), color: 0xffb070, intensity: 30, dist: 14, flicker: true });
  const fill = new THREE.PointLight(0xffb27a, 170, 24, 2); fill.position.set(X, FL + 8, Z - 2); T0.add(fill);   // the room's warm glow (a constant light: no recompiles)
  let lastT = -1;
  const paint = (T) => { if (T === lastT) return; lastT = T;
    lan.forEach((Ln) => { if (Ln.ev < 0) return; const tv = EV[Ln.ev], known = tv <= T, col = !known ? 0x2a3a32 : Ln.ev === 5 ? 0xf5b84b : tv <= DEC ? 0x7fd1b9 : 0xe8766a;
      Ln.co.material.color.setHex(col).multiplyScalar(known ? (tv > DEC ? 1.4 : 2.4) : 1); Ln.sh.material.emissive.setHex(col); Ln.sh.material.emissiveIntensity = known ? (tv > DEC ? .45 : .8) : .1; Ln.anc.color = known ? col : 0x2a3a32; Ln.anc.intensity = known ? (tv > DEC ? 16 : 30) : 6; });
    marks.forEach((M) => { const known = M.t <= T; M.m.material.color.setHex(!known ? 0x24382f : M.dec ? 0xf5b84b : M.t <= DEC ? 0x7fd1b9 : 0xe8766a).multiplyScalar(known ? 2.4 : 1); }); };
  paint(200);
  return { update(p, t, dt, T) {
    paint(T);
    const turn = T / 200;                                    // dragging time turns the hands and every gear, forward or back
    minH.rotation.z = -turn * Math.PI * 2 * 4; hourH.rotation.z = -turn * Math.PI * 2;
    gears.forEach((G) => { if (G.face) G.g.rotation.z = turn * G.k * 6 + t * .02 * G.k; else G.g.rotation.z = turn * G.k * 6 + t * .02 * G.k; });
    pend.rotation.z = .2 * Math.sin(t * 1.25);
    lan.forEach((Ln, k) => { Ln.g.position.y = Ln.y0 + Math.sin(t * .9 + Ln.ph) * .08; Ln.g.rotation.z = Math.sin(t * .7 + Ln.ph) * .06; });
    for (let i = 0; i < DN; i++) { dPh[i] += dt * .25; dPos[i * 3] += Math.cos(dPh[i]) * dt * .05; dPos[i * 3 + 1] += Math.sin(dPh[i] * 1.3) * dt * .04; } dGeo.attributes.position.needsUpdate = true;
    const open = smooth(clamp((p - .64) / .12)); leaves2.forEach(({ pv, sd }) => { pv.rotation.y = -sd * open * 2.95; });
  } };
})();

// ------------------------------------------------------------------ sizing, scroll, adaptive quality, loop
const renderPass = composer.passes[0];
function resize() {
  const w = innerWidth, h = innerHeight;
  renderer.setPixelRatio(dpr); composer.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix();
}
addEventListener("resize", resize); resize();
const storyEl = document.getElementById("story");
const chapters = [
  { id: "journey", el: storyEl, sc: journey.sc, update: journey.update },
  { id: "balloon", el: document.getElementById("mandate"), sc: balloon.sc, update: balloon.update },
  { id: "gate", el: document.getElementById("pipeline"), sc: gate.sc, update: gate.update },
  { id: "city", el: document.getElementById("redteam"), sc: city.sc, update: city.redteam.update },
  { id: "panorama", el: document.getElementById("replay"), sc: city.sc, update: city.replay.update },
];
let progress = 0, ringP = 0, mode = "hero", chapOpacity = 0, active = null;
const onScroll = () => {
  const r = hero.getBoundingClientRect(); progress = clamp(-r.top / Math.max(1, r.height - innerHeight));
  if (r.bottom > innerHeight * .2 && progress < .62) { mode = "hero"; return; }
  let best = null, bestO = 0;
  chapters.forEach((c) => { if (!c.el) return; const s = c.el.getBoundingClientRect();
    const o = s.top < innerHeight * .85 && s.bottom > innerHeight * .15 ? Math.min(clamp((innerHeight * .85 - s.top) / (innerHeight * .6)), clamp((s.bottom - innerHeight * .15) / (innerHeight * .5))) : 0;
    c.p = clamp(-s.top / Math.max(1, s.height - innerHeight)); if (o > bestO) { bestO = o; best = c; } });
  if (!best) { const rr = document.getElementById("replay").getBoundingClientRect(); best = rr.bottom < innerHeight * .5 ? chapters[chapters.length - 1] : chapters[0]; bestO = 1; }            // between the order card and the wall: stay with the lantern light
  chapters.forEach((c) => { if (c !== best && c.sc === best.sc && c.el) { const s2 = c.el.getBoundingClientRect();
    const o2 = s2.top < innerHeight * .85 && s2.bottom > innerHeight * .15 ? 1 : 0; bestO = Math.max(bestO, o2); } });
  if (best.id === "journey" && r.bottom > 0) bestO = Math.max(bestO, clamp((progress - .62) / .15));  // the window's glow dissolves into the lantern's
  mode = best.id; active = best; chapOpacity = bestO; ringP = chapters[0].p || 0;
};
addEventListener("scroll", onScroll, { passive: true }); onScroll();
// compile every chapter's shaders up front (idle time), so no backdrop hitches the first time it appears
const precompile = () => chapters.forEach((c) => { try { renderer.compile(c.sc, camera); } catch (e) { /* compile lazily instead */ } });
(window.requestIdleCallback || ((f) => setTimeout(f, 600)))(precompile);
let px = 0, py = 0; addEventListener("pointermove", (e) => { px = e.clientX / innerWidth - .5; py = e.clientY / innerHeight - .5; }, { passive: true });
const look = new THREE.Vector3();
let level = 2, frames = 0, acc = 0, settledAt = 0, fast = 0;  // 2 = full, 1 = reduced, 0 = minimal
const shadowLights = () => [moonLight, rMoon, ...chapters.slice(1).map((c) => c.sc.userData.sun)];
function setLevel(l) {
  level = l;
  dpr = l === 2 ? Math.min(devicePixelRatio || 1, 1.5) : l === 1 ? 1 : .75;
  shadowLights().forEach((L) => { const s = l === 2 ? (L === moonLight || L === rMoon ? 2048 : 1024) : 1024; L.shadow.mapSize.set(s, s); L.shadow.map?.dispose(); L.shadow.map = null; });
  godRays.blurPass.enabled = l > 0; dew.visible = l > 0; rain.visible = l > 0;
  resize();
}
const clock = new THREE.Clock();
let running = true;
document.addEventListener("visibilitychange", () => { running = !document.hidden; if (running) { clock.getDelta(); requestAnimationFrame(loop); } });
document.documentElement.classList.add("has-3d");
requestAnimationFrame(() => window.ScrollTrigger && window.ScrollTrigger.refresh());
const atmos = [fireflies, ...fogBanks];
let atmosIn = scene, caughtFired = false;
let lastMode = "", blendT = 1; const prevPos = new THREE.Vector3(), prevQuat = new THREE.Quaternion(), lastPos = new THREE.Vector3(), lastQuat = new THREE.Quaternion();

function loop() {
  if (!running) return;
  requestAnimationFrame(loop);
  if (mode === "off") { canvas.style.opacity = "0"; return; }  // nothing 3D on screen: draw nothing
  const dt = Math.min(.05, clock.getDelta()), t = clock.elapsedTime;
  acc += dt; frames++;
  if (acc > 1.5) { const ft = acc / frames * 1000; acc = 0; frames = 0;
    if (ft > 24 && level > 0 && t - settledAt > 2) { setLevel(level - 1); settledAt = t; fast = 0; }
    else if (ft < 17.5 && level < 2 && t - settledAt > 4 && ++fast >= 2) { setLevel(level + 1); settledAt = t; fast = 0; }  // recover when the machine is quiet again
    else if (ft >= 17.5) fast = 0;
    window.__st3d = { level, frameMs: +ft.toFixed(1), dpr, mode }; }
  const activeScene = mode === "hero" ? scene : active.sc;
  if (atmosIn !== activeScene) { atmos.forEach((o) => activeScene.add(o)); atmosIn = activeScene; }
  renderPass.mainScene = activeScene; raysPass.enabled = mode === "hero" && level > 0;
  if (mode === "hero") {
    if (camera.far !== 200) { camera.far = 200; camera.updateProjectionMatrix(); }
    const p = progress, ease = p * p * (3 - 2 * p);
    camera.position.set(px * .6 * (1 - ease) - ease * W * .5, 4.9 + (-py) * .3 + ease * .2, 15.5 - ease * 21);  // through the left lancet
    look.set(px * .3 - ease * W * .5, 6.4 - ease * .6, -6 - ease * 10); camera.lookAt(look);
    glass.visible = camera.position.z > 1.6;
    canvas.style.opacity = String(p > .5 ? Math.max(0, 1 - (p - .5) / .25) : 1);
  } else if (mode === "ring") {
    // the camera dollies along the vines toward the ring; vines grow, the ring blooms
    const p = ringP, e = p * p * (3 - 2 * p);
    camera.position.set(-8 + e * 11.5 + px * .5, 6.3 + Math.sin(t * .3) * .08 - py * .3, 14 - e * 1.2);
    look.set(-5 + e * 8.8, 6.2, 0);
    const intro = smooth(clamp(1 - p / .14));                 // close on the lantern's glow, then pull back to reveal the wall
    if (intro > 0) { camera.position.lerp(new THREE.Vector3(-6.4, 5.75, 3.2), intro); look.lerp(new THREE.Vector3(-6.5, 5.6, 0), intro); }
    camera.lookAt(look);
    canvas.style.opacity = String(chapOpacity);
    const grow = clamp((p - .18) / .35), bloomP = clamp((p - .5) / .25);
    vines3.forEach((v) => { const g = v.lit ? grow : 1; v.mesh.geometry.setDrawRange(0, Math.floor(v.count * g / 6) * 6); v.grow = g; });
    rLeafInfo.forEach((L, i) => { const g = vines3[L.vi].grow; if (L.u <= g) rLeaves.setMatrixAt(i, L.m); else { dummy.scale.setScalar(0); dummy.position.set(0, -50, 0); dummy.updateMatrix(); rLeaves.setMatrixAt(i, dummy.matrix); } });
    rLeaves.instanceMatrix.needsUpdate = true;
    petalInfo.forEach((P, i) => { const o = clamp(bloomP * 1.3 - P.bi * .15); const open = .25 + o * (P.layer ? .95 : 1.25);
      dummy.position.copy(P.c).add(new THREE.Vector3(Math.cos(P.ang + Math.PI / 2) * .42, Math.sin(P.ang + Math.PI / 2) * .42, .15)); dummy.rotation.set(0, 0, P.ang); dummy.rotateX(-open); dummy.scale.setScalar(o * (P.layer ? .8 : 1.05)); dummy.updateMatrix(); petals.setMatrixAt(i, dummy.matrix); });
    petals.instanceMatrix.needsUpdate = true;
    ["f1", "f2", "f3"].forEach((k) => { lanterns[k].userData.core.scale.setScalar(.6 + bloomP * .8 + Math.sin(t * 3) * .05 * bloomP); });
    halo.material.opacity = bloomP * (.55 + .15 * Math.sin(t * 2)); halo.rotation.z = t * .05;
    sigil.rotation.y = t * .6; sring2.rotation.z = t * .9;
    if (bloomP > .6 && !caughtFired) { caughtFired = true; burst(); } else if (bloomP < .3) caughtFired = false;
    if (sparkT >= 0) { sparkT += dt; for (let i = 0; i < SP; i++) { spVel[i * 3 + 1] -= 3 * dt; for (let k = 0; k < 3; k++) spPos[i * 3 + k] += spVel[i * 3 + k] * dt; }
      spGeo.attributes.position.needsUpdate = true; sparks.material.opacity = Math.max(0, 1 - sparkT / 1.6); if (sparkT > 1.6) { sparks.visible = false; sparkT = -1; } }
    lanterns.me.userData.core.scale.setScalar(1 + Math.sin(t * 2.2) * .06);
  } else {
    canvas.style.opacity = String(chapOpacity);
    active.update(active.p || 0, t, dt);
  }
  windU.value = t; skyMat.uniforms.t.value = t; glassMat.uniforms.t.value = t;
  const ox = camera.position.x;
  if (mode === "hero") { const a = rainGeo.attributes.position.array;
    for (let i = 0; i < a.length; i += 6) { a[i + 1] -= dt * 13; a[i + 4] -= dt * 13; if (a[i + 4] < 0) { a[i + 1] += 18; a[i + 4] += 18; } }
    rainGeo.attributes.position.needsUpdate = true; }
  const f = ffGeo.attributes.position.array;
  for (let i = 0; i < FF; i++) { const ph = ffPh[i] += dt * .35; f[i * 3] += Math.cos(ph * 1.3) * dt * .12 + px * dt * .12; f[i * 3 + 1] += Math.sin(ph) * dt * .1; f[i * 3 + 2] += Math.sin(ph * .7) * dt * .1; }
  ffGeo.attributes.position.needsUpdate = true; fireflies.position.copy(mode === "hero" ? new THREE.Vector3() : new THREE.Vector3(ox, camera.position.y - 4, camera.position.z - 8));
  fireflies.material.opacity = .4 + .15 * Math.sin(t * 1.2);
  if (mode !== lastMode) { prevPos.copy(lastPos); prevQuat.copy(lastQuat); blendT = mode === "hero" || lastMode === "hero" || lastMode === "" ? 1 : 0; lastMode = mode; }
  if (blendT < 1) { blendT = Math.min(1, blendT + dt / 1.1); const k = smooth(blendT);
    camera.position.lerpVectors(prevPos, camera.position.clone(), k); camera.quaternion.slerpQuaternions(prevQuat, camera.quaternion.clone(), k); }
  lastPos.copy(camera.position); lastQuat.copy(camera.quaternion);
  composer.render(dt);
}
requestAnimationFrame(loop);
window.__st3dScene = { scene, ringScene, camera, renderer, setLevel, fogBanks, composer, bloom, moon, moonClouds, godRays, raysPass, moonHalo, sky, rain, chapters };
