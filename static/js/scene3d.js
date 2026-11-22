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
  ToneMappingEffect, ToneMappingMode, BlendFunction, HueSaturationEffect } from "postprocessing";

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
scene.fog = new THREE.FogExp2(0x0a1813, 0.028);
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
      vec3 col=mix(vec3(.012,.03,.026),vec3(.08,.22,.18),smoothstep(.35,.95,f)*.85);
      col+=vec3(.5,.62,.56)*smoothstep(.85,1.1,f*length(w))*.18;
      float md=length((vUv-vec2(.5,.62))*vec2(1.6,1.));
      col+=vec3(.85,.95,.9)*(exp(-md*9.)*.35+exp(-md*3.)*.05);
      gl_FragColor=vec4(col,1.); }`,
});
const sky = new THREE.Mesh(new THREE.PlaneGeometry(120, 70), skyMat); sky.position.set(0, 14, -45); scene.add(sky);
const moon = new THREE.Mesh(new THREE.SphereGeometry(1.6, 48, 32), new THREE.MeshBasicMaterial({ color: 0xd8ece2, fog: false }));
moon.position.set(.8, 12.2, -40); scene.add(moon);

// lights: cool moonlight from behind the window, faint warm fill, sage sky
scene.add(new THREE.HemisphereLight(0x7fa596, 0x0b1612, 0.55));
const front = new THREE.SpotLight(0x9fc8b8, 55, 40, .7, .9, 1.6); front.position.set(-7, 9, 14); front.target.position.set(0, 4, 0); scene.add(front, front.target);  // cool moonlit wash on the wall's face
const moonLight = new THREE.DirectionalLight(0xcfe6dc, 3.2);
moonLight.position.set(0.6, 14, -18); moonLight.target.position.set(0, 0, 6);
moonLight.castShadow = true; moonLight.shadow.mapSize.set(2048, 2048);
Object.assign(moonLight.shadow.camera, { left: -9, right: 9, top: 12, bottom: -4, near: 1, far: 50 });
moonLight.shadow.bias = -0.0004; moonLight.shadow.normalBias = 0.03; moonLight.shadow.radius = 5;
scene.add(moonLight, moonLight.target);
const fill = new THREE.PointLight(0xffb070, 2.5, 14, 2); fill.position.set(-7, 2, 8); scene.add(fill);
const rim = new THREE.PointLight(0x7fd1b9, 10, 14, 2); rim.position.set(0, 5, -2.5); scene.add(rim);

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
const stone = stoneSet(512, 128, 64, .65);
const wallMat = new THREE.MeshPhysicalMaterial({ map: stone.albedo, normalMap: stone.normal, roughnessMap: stone.rough, roughness: 1,
  normalScale: new THREE.Vector2(1.1, 1.1), clearcoat: .55, clearcoatRoughness: .22, color: 0xb9cfc4 });
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
const traceryMat = new THREE.MeshPhysicalMaterial({ map: stone.albedo, normalMap: stone.normal, roughness: .55, clearcoat: .7, clearcoatRoughness: .18, color: 0x7f978b });
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
    void main(){ vec2 uv=vP*.55; float d=drops(uv,4.)+drops(uv+7.3,7.)*.6;
      vec3 c=vec3(.75,.9,.85); gl_FragColor=vec4(c*d,.02+d*.45); }`,
});
const glass = new THREE.Mesh(new THREE.ShapeGeometry(glassShape, 48), glassMat); glass.position.z = -.6; scene.add(glass);

// wet stone floor: large flags, puddles (very low roughness), clearcoat; reflects the moon and lights
const floorSet = stoneSet(512, 170, 170, .9);
[floorSet.albedo, floorSet.normal, floorSet.rough].forEach((t) => t.repeat.set(5, 6));
const floor = new THREE.Mesh(new THREE.PlaneGeometry(60, 70), new THREE.MeshPhysicalMaterial({
  map: floorSet.albedo, normalMap: floorSet.normal, roughnessMap: floorSet.rough, roughness: .9, clearcoat: 1, clearcoatRoughness: .08, color: 0x8aa398 }));
floor.rotation.x = -Math.PI / 2; floor.position.z = -5; floor.receiveShadow = true; scene.add(floor);
// an environment for wet reflections: a tiny sky with a bright moon, prefiltered
const pmrem = new THREE.PMREMGenerator(renderer);
const envScene = new THREE.Scene(); envScene.background = new THREE.Color(0x07140f);
const envMoon = new THREE.Mesh(new THREE.SphereGeometry(3, 16, 16), new THREE.MeshBasicMaterial({ color: 0xdfeee7 })); envMoon.position.set(0, 12, -25); envScene.add(envMoon);
const envGlow = new THREE.Mesh(new THREE.SphereGeometry(30, 16, 16), new THREE.MeshBasicMaterial({ color: 0x163a30, side: THREE.BackSide })); envScene.add(envGlow);
scene.environment = pmrem.fromScene(envScene, 0.04).texture; scene.environmentIntensity = .55;

// ------------------------------------------------------------------ ivy: tube vines + instanced leaves (wind in the vertex shader)
const vineMat = new THREE.MeshStandardMaterial({ color: 0x2a3a2a, roughness: .7 });
const leafShape = new THREE.Shape();
leafShape.moveTo(0, 0); leafShape.bezierCurveTo(-.08, .02, -.2, .05, -.22, .17); leafShape.bezierCurveTo(-.14, .17, -.1, .22, -.08, .31);
leafShape.bezierCurveTo(-.03, .27, .03, .27, .08, .31); leafShape.bezierCurveTo(.1, .22, .14, .17, .22, .17); leafShape.bezierCurveTo(.2, .05, .08, .02, 0, 0);
const leafGeo = new THREE.ShapeGeometry(leafShape, 8);
{ const p = leafGeo.attributes.position; for (let i = 0; i < p.count; i++) { const x = p.getX(i), y = p.getY(i); p.setZ(i, -1.1 * x * x + .25 * y * y); } leafGeo.computeVertexNormals(); }
const windU = { value: 0 };
const leafMat = new THREE.MeshPhysicalMaterial({ side: THREE.DoubleSide, roughness: .38, clearcoat: .9, clearcoatRoughness: .12, sheen: .4, sheenColor: new THREE.Color(0x7fd1b9), color: 0xffffff });
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
const hues = [0x1e5a4c, 0x245f45, 0x2e7d6b, 0x3e6b4f, 0x4c7a55, 0x7a8a4a];
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
const dew = new THREE.InstancedMesh(new THREE.SphereGeometry(.018, 8, 6), new THREE.MeshPhysicalMaterial({ color: 0xffffff, roughness: 0, metalness: 0, clearcoat: 1, transmission: .0, emissive: 0x1e3a33, emissiveIntensity: .4 }), 420);
for (let i = 0; i < 420; i++) { const p = leafTips[i % leafTips.length]; dummy.position.set(p.x + (rng() - .5) * .08, p.y + (rng() - .5) * .08, p.z + .05); dummy.scale.setScalar(.6 + rng()); dummy.updateMatrix(); dew.setMatrixAt(i, dummy.matrix); }
scene.add(dew);
// drips falling from leaf tips
const drips = new THREE.InstancedMesh(new THREE.SphereGeometry(.02, 6, 6), new THREE.MeshBasicMaterial({ color: 0xcfe6dc }), 14);
const dripState = Array.from({ length: 14 }, (_, i) => ({ from: leafTips[(i * 13) % leafTips.length], y: 0, v: 0, wait: rng() * 4 }));
scene.add(drips);

// rain streaks around the camera, lit by the moon
const RAIN = 1800, rainGeo = new THREE.BufferGeometry(), rp = new Float32Array(RAIN * 6);
for (let i = 0; i < RAIN; i++) { const x = (rng() - .5) * 30, y = rng() * 20, z = -4 + rng() * 22; rp.set([x, y, z, x - .015, y - .28, z], i * 6); }
rainGeo.setAttribute("position", new THREE.BufferAttribute(rp, 3));
const rain = new THREE.LineSegments(rainGeo, new THREE.LineBasicMaterial({ color: 0x9fc4b6, transparent: true, opacity: .09, depthWrite: false })); scene.add(rain);
// fireflies (bloom makes them glow)
const FF = 70, ffGeo = new THREE.BufferGeometry(), ffp = new Float32Array(FF * 3), ffPh = new Float32Array(FF);
for (let i = 0; i < FF; i++) { ffp.set([(rng() - .5) * 14, .5 + rng() * 7, -2 + rng() * 12], i * 3); ffPh[i] = rng() * 6.28; }
ffGeo.setAttribute("position", new THREE.BufferAttribute(ffp, 3));
const ffSprite = (() => { const c = document.createElement("canvas"); c.width = c.height = 64; const x = c.getContext("2d"); const g = x.createRadialGradient(32, 32, 0, 32, 32, 32);
  g.addColorStop(0, "rgba(230,255,240,1)"); g.addColorStop(.25, "rgba(160,240,200,.8)"); g.addColorStop(1, "rgba(60,160,120,0)"); x.fillStyle = g; x.fillRect(0, 0, 64, 64); return new THREE.CanvasTexture(c); })();
const fireflies = new THREE.Points(ffGeo, new THREE.PointsMaterial({ size: .22, map: ffSprite, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, color: 0xd8ffe8 }));
scene.add(fireflies);
// ground mist: soft noisy sprites drifting low
const mistTex = (() => { const N = 128, n = fbm(N, 4, 4); return toTex(N, (i) => { const x = i % N - 64, y = ((i / N) | 0) - 64, f = Math.max(0, 1 - Math.hypot(x, y) / 64); const v = n[i] * f * 255; return [v, v, v]; }, false); })();
const mist = [];
for (let i = 0; i < 9; i++) { const m = new THREE.Mesh(new THREE.PlaneGeometry(14, 4), new THREE.MeshBasicMaterial({ color: 0x8fb3a6, alphaMap: mistTex, transparent: true, opacity: .16, depthWrite: false }));
  m.position.set((rng() - .5) * 18, .6 + rng() * .8, -3 + rng() * 14); mist.push(m); scene.add(m); }

// ------------------------------------------------------------------ post-processing
const composer = new EffectComposer(renderer, { frameBufferType: THREE.HalfFloatType });
composer.addPass(new RenderPass(scene, camera));
const godRays = new GodRaysEffect(camera, moon, { density: .9, decay: .92, weight: .4, exposure: .26, samples: 60, clampMax: 1, resolutionScale: .5 });
const bloom = new BloomEffect({ intensity: .9, luminanceThreshold: .68, luminanceSmoothing: .25, mipmapBlur: true, radius: .72 });
const grade = new HueSaturationEffect({ hue: .02, saturation: -.12 });
const vignette = new VignetteEffect({ offset: .28, darkness: .72 });
const grain = new NoiseEffect({ blendFunction: BlendFunction.OVERLAY }); grain.blendMode.opacity.value = .14;
const tone = new ToneMappingEffect({ mode: ToneMappingMode.ACES_FILMIC });
const raysPass = new EffectPass(camera, godRays);
composer.addPass(raysPass);
composer.addPass(new EffectPass(camera, bloom, grade, vignette, grain, tone));

// ------------------------------------------------------------------ sizing, scroll camera, adaptive quality, loop
function resize() {
  const w = innerWidth, h = innerHeight;
  renderer.setPixelRatio(dpr); composer.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix();
}
addEventListener("resize", resize); resize();
let progress = 0;
const onScroll = () => { const r = hero.getBoundingClientRect(); progress = Math.min(1, Math.max(0, -r.top / Math.max(1, r.height - innerHeight))); };
addEventListener("scroll", onScroll, { passive: true }); onScroll();
let px = 0, py = 0; addEventListener("pointermove", (e) => { px = e.clientX / innerWidth - .5; py = e.clientY / innerHeight - .5; }, { passive: true });
const look = new THREE.Vector3();
let level = 2, frames = 0, acc = 0, settledAt = 0;  // 2 = full, 1 = reduced, 0 = minimal
function setLevel(l) {
  level = l;
  dpr = l === 2 ? Math.min(devicePixelRatio || 1, 1.5) : l === 1 ? 1 : .75;
  moonLight.shadow.mapSize.set(l === 2 ? 2048 : 1024, l === 2 ? 2048 : 1024); moonLight.shadow.map?.dispose(); moonLight.shadow.map = null;
  raysPass.enabled = l > 0; godRays.blurPass.enabled = l > 0; rain.visible = l > 0; dew.visible = l > 0;
  resize();
}
const clock = new THREE.Clock();
let running = true, visible = true;
new IntersectionObserver((e) => { visible = e[0].isIntersecting; }, { rootMargin: "200px" }).observe(hero);
document.addEventListener("visibilitychange", () => { running = !document.hidden; if (running) { clock.getDelta(); requestAnimationFrame(loop); } });
document.documentElement.classList.add("has-3d");

function loop() {
  if (!running) return;
  requestAnimationFrame(loop);
  if (!visible) return;
  const dt = Math.min(.05, clock.getDelta()), t = clock.elapsedTime;
  // adaptive quality: average the frame time for ~1.5 s at a time, step down if it is slow
  acc += dt; frames++;
  if (acc > 1.5) { const ft = acc / frames * 1000; acc = 0; frames = 0;
    if (ft > 24 && level > 0 && t - settledAt > 2) { setLevel(level - 1); settledAt = t; }
    window.__st3d = { level, frameMs: +ft.toFixed(1), dpr }; }
  // scroll: dolly toward and through the arch
  const p = progress, ease = p * p * (3 - 2 * p);
  camera.position.set(px * .6 * (1 - ease) - ease * W * .5, 4.3 + (-py) * .3 + ease * .4, 15.5 - ease * 21);  // through the left lancet
  look.set(px * .3 - ease * W * .5, 5.2 + ease * .4, -6 - ease * 10); camera.lookAt(look);
  glassMat.opacity = 1; glass.visible = camera.position.z > 1.6;
  canvas.style.opacity = String(p > .5 ? Math.max(0, 1 - (p - .5) / .25) : 1);
  windU.value = t; skyMat.uniforms.t.value = t; glassMat.uniforms.t.value = t;
  // rain falls and wraps
  const a = rainGeo.attributes.position.array;
  for (let i = 0; i < a.length; i += 6) { a[i + 1] -= dt * 16; a[i + 4] -= dt * 16; if (a[i + 4] < 0) { a[i + 1] += 20; a[i + 4] += 20; } }
  rainGeo.attributes.position.needsUpdate = true;
  // fireflies wander, drawn gently toward the pointer
  const f = ffGeo.attributes.position.array;
  for (let i = 0; i < FF; i++) { const ph = ffPh[i] += dt * .7; f[i * 3] += Math.cos(ph * 1.3) * dt * .25 + px * dt * .3; f[i * 3 + 1] += Math.sin(ph) * dt * .2; f[i * 3 + 2] += Math.sin(ph * .7) * dt * .2; }
  ffGeo.attributes.position.needsUpdate = true;
  fireflies.material.opacity = .65 + .35 * Math.sin(t * 2.1);
  // drips
  dripState.forEach((d, i) => { d.wait -= dt; if (d.wait > 0) { dummy.scale.setScalar(0); } else { d.v += 9.8 * dt; d.y -= d.v * dt; dummy.scale.setScalar(1);
      if (d.from.y + d.y < 0) { d.y = 0; d.v = 0; d.wait = 1.5 + rng() * 4; } }
    dummy.position.set(d.from.x, d.from.y + d.y, d.from.z + .05); dummy.updateMatrix(); drips.setMatrixAt(i, dummy.matrix); });
  drips.instanceMatrix.needsUpdate = true;
  mist.forEach((m, i) => { m.position.x += Math.sin(t * .05 + i) * dt * .3; m.lookAt(camera.position.x, m.position.y, camera.position.z); });
  composer.render(dt);
}
requestAnimationFrame(loop);
window.__st3dScene = { scene, camera, renderer, setLevel };
