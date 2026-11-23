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
  return { sc, update(p, t) {
    const e = smooth(p);
    camera.position.set(Math.sin(t * .05) * .3 + px * .4, 2.9 - e * .5 - py * .2, 9.5 - e * 3.5); look.set(0, 1.9, 0); camera.lookAt(look);
    items.forEach((o, i) => { const a = clamp((p - .1 - i * .1) / .35), q = smooth(a);
      o.position.set(from[i][0] + (to[i][0] - from[i][0]) * q + Math.sin(t * 1.3 + i) * .08, from[i][1] + (to[i][1] - from[i][1]) * q + Math.sin(t * 1.7 + i) * .06, from[i][2] + (to[i][2] - from[i][2]) * q);
      if (i === 2) { const out = clamp((p - .62) / .15); o.position.y -= out * 2.2; o.scale.setScalar(Math.max(.01, 1 - out)); } });
  } };
})();

// ------------------------------------------------------------------ chapter vi: a gothic corridor; the risky payment meets an iron portcullis
const corridor = (() => {
  const sc = baseScene(0x030907);
  sc.userData.sun.position.set(6, 12, 8); sc.userData.sun.intensity = .9;
  const CW = 2.3, CS = 3.3, N = 7, GAP = 5.2;
  const fl = new THREE.Mesh(new THREE.PlaneGeometry(9, 60), floor.material); fl.rotation.x = -Math.PI / 2; fl.position.z = -18; fl.receiveShadow = true; sc.add(fl);
  const ringS = new THREE.Shape(); archPath(CW + .5, CS, ringS); ringS.lineTo(-CW - .5, 0); ringS.holes.push(archPath(CW, CS, new THREE.Path()));
  const archGeo = new THREE.ExtrudeGeometry(ringS, { depth: .7, bevelEnabled: true, bevelThickness: .06, bevelSize: .06, bevelSegments: 2, curveSegments: 40 });
  const glowArches = [];
  for (let i = 0; i < N; i++) {
    const a = new THREE.Mesh(archGeo, traceryMat); a.position.z = -i * GAP; a.castShadow = a.receiveShadow = true; sc.add(a);
    const path = archPath(CW - .02, CS, new THREE.Path()).getSpacedPoints(60).map((v) => new THREE.Vector3(v.x, v.y, .72));
    const gm = new THREE.MeshBasicMaterial({ color: 0x7fd1b9, transparent: true, opacity: 0 });
    const g = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(path), 80, .025, 6, false), gm); g.position.z = -i * GAP; sc.add(g); glowArches.push(g);
    [-1, 1].forEach((s) => { const w = new THREE.Mesh(new THREE.BoxGeometry(.5, CS + 3.2, GAP - .7), rWallMat); w.position.set(s * (CW + 1.1), (CS + 3.2) / 2, -i * GAP - GAP / 2); w.receiveShadow = true; sc.add(w); });
  }
  [1, 3, 5].forEach((i) => { const l = new THREE.PointLight(0xd9a070, 1.6, 9, 2); l.position.set((i % 2 ? -1 : 1) * (CW - .3), 2.6, -i * GAP + .8); sc.add(l); });
  // the portcullis: an iron grid with spikes, at the pre-capture gate
  const port = new THREE.Group(), PG = 4;
  for (let i = -4; i <= 4; i++) { const bar = new THREE.Mesh(new THREE.CylinderGeometry(.05, .05, CS + 2.4, 10), iron); bar.position.set(i * .5, (CS + 2.4) / 2, 0); bar.castShadow = true; port.add(bar);
    const sp = new THREE.Mesh(new THREE.ConeGeometry(.08, .28, 10), iron); sp.position.set(i * .5, -.12, 0); sp.rotation.x = Math.PI; port.add(sp); }
  for (let j = 0; j < 5; j++) { const h = new THREE.Mesh(new THREE.BoxGeometry(4.4, .08, .09), iron); h.position.y = .45 + j * 1.05; h.castShadow = true; port.add(h); }
  port.position.z = -PG * GAP + .3; sc.add(port);
  const rimP = new THREE.PointLight(0x9fc8b8, 3, 6, 2); rimP.position.set(0, 3, -PG * GAP + 2); sc.add(rimP);
  const good = orb(0x9fe0c8, 2.2, .16, 2.6), bad = orb(0xe8766a, 2.0, .16, 2.4); sc.add(good, bad);
  return { sc, update(p, t) {
    const e = smooth(p);
    camera.position.set(px * .3, 1.9 - py * .2, 9 - e * 17); look.set(0, 1.7, camera.position.z - 10); camera.lookAt(look);
    const gz = 6 - clamp((p - .05) / .55) * 34;                       // the approved payment runs the whole corridor
    good.position.set(Math.sin(t * 2) * .05, 1.4 + Math.sin(t * 3) * .05, gz);
    const drop = clamp((p - .5) / .1); port.position.y = (1 - smooth(drop)) * (CS + 1.6);
    const bz = Math.max(-PG * GAP + 1.0, 6 - clamp((p - .2) / .45) * 30);  // the risky one stops at the portcullis
    const stopped = bz <= -PG * GAP + 1.0001 && drop >= 1;
    bad.position.set(Math.sin(t * 2 + 1) * .05, 1.2 + Math.sin(t * 3 + 1) * .05 + (stopped ? Math.sin(t * 9) * .03 : 0), bz);
    bad.children[0].scale.setScalar(stopped ? 1 + Math.sin(t * 6) * .12 : 1);
    glowArches.forEach((g, i) => { const z = -i * GAP, dz = Math.abs(gz - z); g.material.opacity = Math.max(0, .9 - dz * .45) + (i === PG && stopped ? .3 : 0);
      g.material.color.setHex(i === PG && stopped ? 0xe8766a : 0x7fd1b9); });
  } };
})();

// ------------------------------------------------------------------ chapter ix: a crisp moonlit lake; attack waves break on a shield of light
const lake = (() => {
  const sc = baseScene(0x050d0b);
  sc.userData.sun.intensity = 1.1;
  const sky = new THREE.Mesh(new THREE.SphereGeometry(120, 32, 16), new THREE.ShaderMaterial({ side: THREE.BackSide, depthWrite: false, fog: false,
    vertexShader: "varying vec3 vP; void main(){ vP=position; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }",
    fragmentShader: `varying vec3 vP; float h(vec3 p){return fract(sin(dot(p,vec3(12.9898,78.233,37.719)))*43758.5453);}
      void main(){ float y=normalize(vP).y; vec3 c=mix(vec3(.04,.09,.08),vec3(.006,.016,.014),smoothstep(-.05,.6,y));
        float s=step(.9985,h(floor(normalize(vP)*420.)))*smoothstep(.05,.4,y); c+=vec3(.7,.78,.74)*s*.8; gl_FragColor=vec4(c,1.);
        #include <colorspace_fragment>
      }` }));
  sc.add(sky);
  const moonL = new THREE.Mesh(new THREE.SphereGeometry(3.2, 64, 48), moonMat); moonL.position.set(14, 22, -70); sc.add(moonL);
  // the far shore: layers of dark pines
  const pine = new THREE.ConeGeometry(1, 4, 7); pine.translate(0, 2, 0);
  const PN = 260, pines = new THREE.InstancedMesh(pine, new THREE.MeshStandardMaterial({ color: 0x0d1c17, roughness: .95 }), PN), d = new THREE.Object3D();
  for (let i = 0; i < PN; i++) { const row = i % 3, x = (i / PN) * 140 - 70 + (rng() - .5) * 1.5;
    d.position.set(x, -.3, -34 - row * 5 - rng() * 2); d.scale.set(.8 + rng() * .8, 1.2 + rng() * 2.4 - row * .2, .8 + rng() * .8); d.updateMatrix(); pines.setMatrixAt(i, d.matrix); }
  sc.add(pines);
  // water: a dark, slightly glossy surface with slowly moving ripples (normal map scroll) and soft reflections of the moon
  const wn = (() => { const N = 256, n = fbm(N, 8, 4); return normalFrom(N, n, 3.2); })(); wn.repeat.set(10, 10);
  const water = new THREE.Mesh(new THREE.PlaneGeometry(160, 120), new THREE.MeshPhysicalMaterial({ color: 0x0a1814, roughness: .16, metalness: 0, normalMap: wn, normalScale: new THREE.Vector2(.35, .35), clearcoat: .6, clearcoatRoughness: .2, envMapIntensity: 1 }));
  water.rotation.x = -Math.PI / 2; water.position.z = -20; sc.add(water);
  const envS = new THREE.Scene(); envS.background = new THREE.Color(0x061210);
  const em = new THREE.Mesh(new THREE.SphereGeometry(4, 16, 16), new THREE.MeshBasicMaterial({ color: 0xbfd2c9 })); em.position.set(14, 22, -70); envS.add(em);
  sc.environment = pmrem.fromScene(envS, 0.02).texture; sc.environmentIntensity = .9;
  // the shield of light on the near shore
  const shield = new THREE.Group(); shield.position.set(4.6, 2.2, -4);
  const sh = new THREE.Shape(); sh.moveTo(0, 2.1); sh.bezierCurveTo(1.4, 1.8, 1.6, 1.2, 1.55, .3); sh.bezierCurveTo(1.45, -1, .7, -1.8, 0, -2.3); sh.bezierCurveTo(-.7, -1.8, -1.45, -1, -1.55, .3); sh.bezierCurveTo(-1.6, 1.2, -1.4, 1.8, 0, 2.1);
  const shieldMat = new THREE.MeshBasicMaterial({ color: new THREE.Color(0x7fd1b9).multiplyScalar(.5), transparent: true, opacity: .16, side: THREE.DoubleSide, depthWrite: false });
  shield.add(new THREE.Mesh(new THREE.ShapeGeometry(sh, 40), shieldMat));
  const edge = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(sh.getSpacedPoints(80).map((v) => new THREE.Vector3(v.x, v.y, 0)), true), 160, .035, 8, true), glowMat(0x7fd1b9, 1.6));
  shield.add(edge); shield.lookAt(-6, 2, 12); sc.add(shield);
  const shL = new THREE.PointLight(0x7fd1b9, 4, 10, 2); shL.position.set(4.6, 2.2, -3); sc.add(shL);
  // attack orbs skim the water; ripples spread where they touch
  const AN = 16, attacks = [], ripples = [];
  for (let i = 0; i < AN; i++) { const o = orb(0xe8766a, 1.7, .09, 0); o.userData = { x0: -16 - rng() * 6, z0: -10 + rng() * 9, lag: (i % 8) * .045 + (i >= 8 ? .32 : 0), h: .35 + rng() * .5 }; attacks.push(o); sc.add(o);
    const rg = new THREE.Mesh(new THREE.RingGeometry(.9, 1, 48), new THREE.MeshBasicMaterial({ color: 0x9fb8ac, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false }));
    rg.rotation.x = -Math.PI / 2; rg.position.y = .02; ripples.push(rg); sc.add(rg); }
  const wisps = [0, 1, 2, 3, 4].map((i) => { const w = orb(0xbfeedd, 1.1, .06, 0); w.position.set(-8 + i * 3.5, .6, 2 - i * 1.6); sc.add(w); return w; });
  return { sc, update(p, t) {
    const e = smooth(p);
    camera.position.set(-5 + e * 4 + px * .5, 1.7 - py * .2, 11 - e * 2.5); look.set(1.5, 1.4, -6); camera.lookAt(look);
    wn.offset.set(t * .006, t * .004);
    attacks.forEach((o, i) => { const u = o.userData, a = clamp((p - .08 - u.lag) / .28);
      const tx = 4.6 - 1.2, x = u.x0 + (tx - u.x0) * a, z = u.z0 + (-4 - u.z0) * a;
      o.position.set(x, u.h + Math.sin(a * 18 + i) * .08, z); o.visible = a > 0 && a < 1; o.scale.setScalar(a >= .98 ? 2 : 1);
      const r = ripples[i], ra = clamp((a - .2) / .8); r.position.set(x, .02, z); r.scale.setScalar(.2 + ra * 2.2); r.material.opacity = a > 0 && a < 1 ? .35 * (1 - ra) : 0; });
    const hit = attacks.some((o) => o.visible && o.position.x > 2.6);
    edge.material.color.copy(new THREE.Color(0x7fd1b9).multiplyScalar(hit ? 2.4 : 1.6)); shieldMat.opacity = hit ? .26 : .16 + Math.sin(t * 1.5) * .02;
    wisps.forEach((w, i) => { w.position.y = .6 + Math.sin(t * .8 + i) * .2; w.children[0].material.color.setScalar(.9 + Math.sin(t * 1.3 + i * 2) * .3); });
  } };
})();

// ------------------------------------------------------------------ chapter xi: across the same lake on stepping stones to a ruined arch
// (it shares the lake scene, so lake -> replay is one continuous camera move, not a cut)
const causeway = (() => {
  const sc = lake.sc;
  const EVT = [0, 0, 24, 61, 88, 120, 136, 172], stones = [], rims = [];
  const stoneGeo = new THREE.CylinderGeometry(1, 1.08, .22, 28, 1);
  { const pp = stoneGeo.attributes.position; for (let i = 0; i < pp.count; i++) { const x = pp.getX(i), z = pp.getZ(i), a = Math.atan2(z, x);
      const r = 1 + .08 * Math.sin(a * 3 + 1) + .05 * Math.sin(a * 7); pp.setX(i, x * r); pp.setZ(i, z * r); } stoneGeo.computeVertexNormals(); }
  const rimGeo = new THREE.TorusGeometry(1.02, .035, 8, 48);
  EVT.forEach((tv, i) => {
    const u = i / (EVT.length - 1), x = -1.2 + Math.sin(u * 2.4) * 1.6, z = 3 - u * 27;
    const st = new THREE.Mesh(stoneGeo, traceryMat); st.position.set(x, .06, z); st.scale.set(.85 + (i % 3) * .08, 1, .7 + (i % 2) * .1); st.rotation.y = i * .7;
    st.castShadow = st.receiveShadow = true; sc.add(st); stones.push(st);
    const m = new THREE.MeshBasicMaterial({ color: 0x24382f, transparent: true, opacity: .9 });
    const rim = new THREE.Mesh(rimGeo, m); rim.rotation.x = Math.PI / 2; rim.position.set(x, .18, z); rim.scale.copy(st.scale).setY(1); sc.add(rim);
    rims.push({ m, t: tv, dec: i === 5 });
  });
  // a small ruined gothic arch on the far shore, faintly lit inside
  const shrine = new THREE.Group(); shrine.position.set(.4, 0, -29);
  const ringS = new THREE.Shape(); archPath(1.9, 2.8, ringS); ringS.lineTo(-1.9, 0); ringS.holes.push(archPath(1.45, 2.8, new THREE.Path()));
  shrine.add(new THREE.Mesh(new THREE.ExtrudeGeometry(ringS, { depth: .55, bevelEnabled: true, bevelThickness: .05, bevelSize: .05, bevelSegments: 2, curveSegments: 36 }), traceryMat));
  [-1, 1].forEach((sd) => { const ruin = new THREE.Mesh(new THREE.BoxGeometry(1.2, 1.1 + (sd > 0 ? .6 : 0), .6), traceryMat); ruin.position.set(sd * 3, .55, .2); ruin.rotation.z = sd * .08; shrine.add(ruin); });
  const glow = new THREE.Mesh(new THREE.ShapeGeometry(archPath(1.45, 2.8, new THREE.Shape()), 24), new THREE.MeshBasicMaterial({ color: new THREE.Color(0x7fd1b9).multiplyScalar(.35), transparent: true, opacity: .25, depthWrite: false }));
  glow.position.z = .1; shrine.add(glow);
  const sl = new THREE.PointLight(0x9fd8c4, 2.5, 10, 2); sl.position.set(0, 2, 1.2); shrine.add(sl);
  shrine.traverse((o) => { if (o.isMesh) o.castShadow = o.receiveShadow = true; }); sc.add(shrine);
  const scrub = document.getElementById("scrub");
  return { sc, update(p, t) {
    const e = smooth(p), k = Math.min(stones.length - 1, e * (stones.length - 1.2));
    const i0 = Math.floor(k), f = k - i0, a = stones[i0].position, b2 = stones[Math.min(stones.length - 1, i0 + 1)].position;
    camera.position.set(a.x + (b2.x - a.x) * f + px * .4, 2.6 + Math.sin(t * .6) * .03 - py * .2, a.z + (b2.z - a.z) * f + 7.5);
    look.set(.2, 1.3 + e * .6, -29); camera.lookAt(look);
    const T = scrub ? +scrub.value : 200;
    rims.forEach((R) => { const known = R.t <= T;
      R.m.color.copy(new THREE.Color(!known ? 0x24382f : R.dec ? 0xf5b84b : R.t <= 120 ? 0x7fd1b9 : 0xe8766a).multiplyScalar(!known ? 1 : R.dec ? 2.2 : 1.6)); });
    glow.material.opacity = .2 + .1 * Math.sin(t * 1.1);
  } };
})();

// the fraud-ring graph (lanterns, vines, blooms) is gathered into one group so it can hang on the journey's ivy wall
const graph = new THREE.Group();
[...Object.values(lanterns), sigil, ...vines3.map((v) => v.mesh), rLeaves, petals, halo, sparks].forEach((o) => graph.add(o));

// ------------------------------------------------------------------ the journey (stage A): one connected world the camera walks through
// corridor -> a heavy door opens onto the moonlit lake -> a stone bridge assembles across the water -> the ivy wall
// where the fraud-ring graph lives. One scene, one camera path; nothing cuts.
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
  const skyM = new THREE.Mesh(new THREE.SphereGeometry(400, 32, 16), new THREE.ShaderMaterial({ side: THREE.BackSide, depthWrite: false, fog: false,
    vertexShader: "varying vec3 vP; void main(){ vP=position; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }",
    fragmentShader: `varying vec3 vP; float h(vec3 p){return fract(sin(dot(p,vec3(12.9898,78.233,37.719)))*43758.5453);}
      void main(){ float y=normalize(vP).y; vec3 c=mix(vec3(.045,.1,.085),vec3(.006,.016,.014),smoothstep(-.05,.6,y));
        float s=step(.9985,h(floor(normalize(vP)*420.)))*smoothstep(.05,.4,y); c+=vec3(.7,.78,.74)*s*.8; gl_FragColor=vec4(c,1.);
        #include <colorspace_fragment>
      }` }));
  J.add(skyM);
  const moonJ = new THREE.Mesh(new THREE.SphereGeometry(9, 64, 48), moonMat); moonJ.position.set(70, 95, -330); J.add(moonJ);
  const pineG = new THREE.ConeGeometry(1, 4, 7); pineG.translate(0, 2, 0);
  const PN = 520, pines = new THREE.InstancedMesh(pineG, new THREE.MeshStandardMaterial({ color: 0x0d1c17, roughness: .95 }), PN), d = new THREE.Object3D();
  for (let i = 0; i < PN; i++) { const side = i % 2 ? 1 : -1, row = (i >> 1) % 4;
    d.position.set(side * (22 + row * 5 + rng() * 6), -.3, -45 - ((i >> 3) / (PN / 8)) * 190 + rng() * 3);
    d.scale.set(1 + rng(), 1.6 + rng() * 2.6, 1 + rng()); d.updateMatrix(); pines.setMatrixAt(i, d.matrix); }
  J.add(pines);
  const wn = (() => { const N = 256, n = fbm(N, 8, 4); return normalFrom(N, n, 3.2); })(); wn.repeat.set(24, 30);
  const water = new THREE.Mesh(new THREE.PlaneGeometry(220, 300), new THREE.MeshPhysicalMaterial({ color: 0x0a1814, roughness: .16, normalMap: wn, normalScale: new THREE.Vector2(.35, .35), clearcoat: .6, clearcoatRoughness: .2 }));
  water.rotation.x = -Math.PI / 2; water.position.set(0, 0, -192); J.add(water);
  const envS = new THREE.Scene(); envS.background = new THREE.Color(0x061210);
  const em = new THREE.Mesh(new THREE.SphereGeometry(20, 16, 16), new THREE.MeshBasicMaterial({ color: 0xbfd2c9 })); em.position.set(70, 95, -330); envS.add(em);
  J.environment = pmrem.fromScene(envS, 0.02).texture; J.environmentIntensity = .7;

  const stoneAt = (rx, ry) => { const m = wallMat.clone(); ["map", "normalMap", "roughnessMap"].forEach((k) => { m[k] = wallMat[k].clone(); m[k].repeat.set(rx, ry); m[k].needsUpdate = true; }); return m; };
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
  return { sc: J, sun, update(p, t, dt) {
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
    const cp = camera.position;
    anchors.sort((a, b) => a.pos.distanceToSquared(cp) - b.pos.distanceToSquared(cp));
    pool.forEach((l, i) => { const A = anchors[i]; if (!A || (A.group && !A.group.visible)) { l.intensity = 0; return; }
      l.position.copy(A.pos); l.color.setHex(A.color); l.intensity = A.intensity * (A.flicker ? .85 + .15 * Math.sin(t * 13 + i * 3) * Math.sin(t * 7.3 + i) : 1); });
    anchors.forEach((A) => { if (A.flicker && A.mesh) A.mesh.scale.set(1, .9 + .2 * Math.abs(Math.sin(t * 11 + A.pos.z)), 1); });
    const out = smooth(clamp((p - .3) / .14)); sun.intensity = .9 + .5 * out; J.environmentIntensity = .32 + .38 * out;
    sun.position.set(cp.x - 8 + 16 * out, 14 + 18 * out, cp.z + 10); sun.target.position.set(cp.x, 0, cp.z - 22);   // moonlight from above and behind: it rakes the brick, never grazes the wall from behind
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
  { id: "cart", el: document.getElementById("mandate"), sc: cart.sc, update: cart.update },
  { id: "corridor", el: document.getElementById("pipeline"), sc: corridor.sc, update: corridor.update },
  { id: "lake", el: document.getElementById("redteam"), sc: lake.sc, update: lake.update },
  { id: "causeway", el: document.getElementById("replay"), sc: causeway.sc, update: causeway.update },
];
let progress = 0, ringP = 0, mode = "hero", chapOpacity = 0, active = null;
const onScroll = () => {
  const r = hero.getBoundingClientRect(); progress = clamp(-r.top / Math.max(1, r.height - innerHeight));
  if (r.bottom > innerHeight * .2 && progress < .62) { mode = "hero"; return; }
  let best = null, bestO = 0;
  chapters.forEach((c) => { if (!c.el) return; const s = c.el.getBoundingClientRect();
    const o = s.top < innerHeight * .85 && s.bottom > innerHeight * .15 ? Math.min(clamp((innerHeight * .85 - s.top) / (innerHeight * .6)), clamp((s.bottom - innerHeight * .15) / (innerHeight * .5))) : 0;
    c.p = clamp(-s.top / Math.max(1, s.height - innerHeight)); if (o > bestO) { bestO = o; best = c; } });
  if (!best) { best = chapters[0]; bestO = 1; }            // between the order card and the wall: stay with the lantern light
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
