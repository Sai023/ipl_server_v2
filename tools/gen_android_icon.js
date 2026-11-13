// Generates the Android launcher drawables for the BCL Fantasy WebView app from the same mark as the web logo.
//   node tools/gen_android_icon.js          (run from the repo root)  ->  Static/brand/android/*.xml
//
// Why a separate composition: an adaptive icon is a 108dp canvas that the launcher crops to a circle / squircle / teardrop.
// Only the central 66dp circle (radius 33) is guaranteed to survive every mask, so the artwork is scaled until its
// farthest corner is inside it (checked below), and the crease line moves into the BACKGROUND layer, where being cropped
// at the edges is harmless. Android vectors have no dashes, so the ball's stitches are drawn as small separate strokes.
const fs = require('fs'), path = require('path');
const OUT = 'Static/brand/android'; fs.mkdirSync(OUT, { recursive: true });
const f = (n) => String(Math.round(n * 100) / 100);

const COL = { navy1: '#16345F', navy2: '#07111F', goldTop: '#FFE88A', goldBot: '#F2B705', red1: '#FF6B5F', red2: '#C41F2C', teal: '#00D4AA' };
// ---- source geometry: identical numbers to mark() in tools/gen_brand.js (108 grid)
const G = {
  stumps: [28, 40, 52], sw: 7, top: 45.5, bot: 82, sr: 3.5,
  bailRest: { x: 30, y: 38.4, w: 13, h: 2.9 }, bailFly: { x: 57.5, y: 30.5, w: 13, h: 2.9, rot: -30, cx: 64, cy: 32 },
  ball: { cx: 70, cy: 65, r: 12.5 }, crease: { x1: 23, x2: 85, y: 87.5 },
  seam: [[67, 52], [60.5, 59.5], [60.5, 70.5], [67, 78]], stitch: [[70.4, 51.5], [64, 59.5], [64, 70.5], [70.4, 78.5]],
  gloss: { cx: 64.8, cy: 58.4, rx: 4.3, ry: 2.3, rot: -38 }
};
// the artwork (without the crease) spans x 28..82.5, y 28..82 -> its own centre:
const ART_CENTRE = [55.25, 55.0];

const aapt = (attr, grad) => `<aapt:attr name="android:${attr}">${grad}</aapt:attr>`;
const lin = (x1, y1, x2, y2, c1, c2) => `<gradient android:type="linear" android:startX="${f(x1)}" android:startY="${f(y1)}" android:endX="${f(x2)}" android:endY="${f(y2)}"><item android:offset="0" android:color="${c1}"/><item android:offset="1" android:color="${c2}"/></gradient>`;
const pathEl = (d, attrs, inner = '') => `<path android:pathData="${d}"${attrs ? ' ' + attrs : ''}${inner ? `>${inner}</path>` : '/>'}`;

// mapping factory: p' = dst + (p - src) * s
const mapper = (s, src, dst) => (x, y) => [dst[0] + (x - src[0]) * s, dst[1] + (y - src[1]) * s];
const rotPt = (x, y, cx, cy, deg) => { const a = deg * Math.PI / 180, dx = x - cx, dy = y - cy; return [cx + dx * Math.cos(a) - dy * Math.sin(a), cy + dx * Math.sin(a) + dy * Math.cos(a)]; };
const bez = (p, t) => { const u = 1 - t; return [0, 1].map((k) => u * u * u * p[0][k] + 3 * u * u * t * p[1][k] + 3 * u * t * t * p[2][k] + t * t * t * p[3][k]); };

// Returns { pts: every vertex that must stay inside the safe circle, parts: {...path strings} } for a mapping T and scale s
function build(T, s, o = {}) {
  const P = (x, y) => T(x, y), pts = [], ps = (x, y) => { const q = P(x, y); pts.push(q); return `${f(q[0])},${f(q[1])}`; };
  const parts = { stumps: [], bails: [], ball: '', clipD: '', seam: '', stitch: '', gloss: '', crease: '' };
  // stumps
  G.stumps.forEach((x) => {
    const [bx, by] = P(x, G.bot), [, ty] = P(x, G.top), r = f(G.sr * s), w = f(G.sw * s);
    pts.push([bx, by], P(x + G.sw, G.bot), [bx, ty - G.sr * s]);
    parts.stumps.push({ d: `M${f(bx)},${f(by)} V${f(ty)} a${r},${r} 0 0 1 ${w},0 V${f(by)} Z`, y1: ty - G.sr * s, y2: by });
  });
  // bails
  const rect = (x, y, w, h, rot, cx, cy) => [[x, y], [x + w, y], [x + w, y + h], [x, y + h]].map(([a, b]) => (rot ? rotPt(a, b, cx, cy, rot) : [a, b])).map(([a, b]) => P(a, b));
  const poly = (c) => { c.forEach((q) => pts.push(q)); return 'M' + c.map((q) => `${f(q[0])},${f(q[1])}`).join(' L') + ' Z'; };
  const br = G.bailRest, bf = G.bailFly;
  parts.bails.push({ d: poly(rect(br.x, br.y, br.w, br.h)), y1: P(0, br.y)[1], y2: P(0, br.y + br.h)[1] });
  parts.bails.push({ d: poly(rect(bf.x, bf.y, bf.w, bf.h, bf.rot, bf.cx, bf.cy)), y1: P(0, bf.y - 8)[1], y2: P(0, bf.y + 6)[1] });
  // ball
  const [cx, cy] = P(G.ball.cx, G.ball.cy), r = G.ball.r * s; pts.push([cx + r * 0.0, cy], [cx, cy]); pts.push([cx + r, cy], [cx - r, cy], [cx, cy + r], [cx, cy - r], [cx + r * 0.7071, cy + r * 0.7071]);
  const circle = (x, y, rad) => `M${f(x - rad)},${f(y)} a${f(rad)},${f(rad)} 0 1 0 ${f(rad * 2)},0 a${f(rad)},${f(rad)} 0 1 0 -${f(rad * 2)},0 Z`;
  parts.ball = { d: circle(cx, cy, r), x1: cx - r, y1: cy - r, x2: cx + r, y2: cy + r }; parts.clipD = parts.ball.d;
  const curve = (c) => { const q = c.map(([a, b]) => P(a, b)); return `M${f(q[0][0])},${f(q[0][1])} C${f(q[1][0])},${f(q[1][1])} ${f(q[2][0])},${f(q[2][1])} ${f(q[3][0])},${f(q[3][1])}`; };
  parts.seam = curve(G.seam);
  // stitches: sample the offset seam and draw short separate strokes
  const N = 9; let st = '';
  for (let i = 0; i < N; i++) { const t0 = (i + 0.12) / N, t1 = (i + 0.58) / N; const a = bez(G.stitch, t0), b = bez(G.stitch, t1), A = P(a[0], a[1]), B = P(b[0], b[1]); st += `M${f(A[0])},${f(A[1])} L${f(B[0])},${f(B[1])} `; }
  parts.stitch = st.trim();
  // gloss (rotated ellipse)
  const gl = G.gloss, [gx, gy] = P(gl.cx, gl.cy), rx = gl.rx * s, ry = gl.ry * s, th = gl.rot * Math.PI / 180, dx = rx * Math.cos(th), dy = rx * Math.sin(th);
  parts.gloss = `M${f(gx - dx)},${f(gy - dy)} a${f(rx)},${f(ry)} ${gl.rot} 1 0 ${f(2 * dx)},${f(2 * dy)} a${f(rx)},${f(ry)} ${gl.rot} 1 0 ${f(-2 * dx)},${f(-2 * dy)} Z`;
  const [c1x, c1y] = P(G.crease.x1, G.crease.y), [c2x] = P(G.crease.x2, G.crease.y);
  parts.crease = { y: c1y, x1: c1x, x2: c2x };
  return { pts, parts };
}

const goldPath = (it) => pathEl(it.d, '', aapt('fillColor', lin(0, it.y1, 0, it.y2, COL.goldTop, COL.goldBot)));
function markXml(b, s, mono) {
  const p = b.parts, out = [];
  if (mono) {
    p.stumps.forEach((it) => out.push(pathEl(it.d, 'android:fillColor="#000000"')));
    p.bails.forEach((it) => out.push(pathEl(it.d, 'android:fillColor="#000000"')));
    out.push(pathEl(p.ball.d, `android:strokeColor="#000000" android:strokeWidth="${f(2.4 * s)}" android:fillColor="#00000000"`));
    out.push(`<group><clip-path android:pathData="${p.clipD}"/>${pathEl(p.seam, `android:strokeColor="#000000" android:strokeWidth="${f(2 * s)}" android:strokeLineCap="round" android:fillColor="#00000000"`)}</group>`);
    return out.join('\n    ');
  }
  p.stumps.forEach((it) => out.push(goldPath(it)));
  // soft highlight along the left edge of each stump
  p.stumps.forEach((it) => { const m = /^M([\d.\-]+),([\d.\-]+) V([\d.\-]+)/.exec(it.d); const x = +m[1] + 1.4 * s; out.push(pathEl(`M${f(x)},${f(+m[3] + 1.5 * s)} V${f(+m[2] - 2 * s)}`, `android:strokeColor="#FFFFFF" android:strokeAlpha="0.38" android:strokeWidth="${f(1.3 * s)}" android:strokeLineCap="round" android:fillColor="#00000000"`)); });
  p.bails.forEach((it) => out.push(goldPath(it)));
  out.push(pathEl(p.ball.d, '', aapt('fillColor', lin(p.ball.x1, p.ball.y1, p.ball.x2, p.ball.y2, COL.red1, COL.red2))));
  out.push(`<group><clip-path android:pathData="${p.clipD}"/>` +
    pathEl(p.seam, `android:strokeColor="#FFFFFF" android:strokeWidth="${f(1.6 * s)}" android:strokeLineCap="round" android:fillColor="#00000000"`) +
    pathEl(p.stitch, `android:strokeColor="#FFFFFF" android:strokeAlpha="0.9" android:strokeWidth="${f(1.1 * s)}" android:strokeLineCap="round" android:fillColor="#00000000"`) + '</group>');
  out.push(pathEl(p.gloss, 'android:fillColor="#FFFFFF" android:fillAlpha="0.24"'));
  return out.join('\n    ');
}
const ROOT = (inner, mono) => `<?xml version="1.0" encoding="utf-8"?>
<!-- BCL Fantasy ${mono || 'launcher'} layer. GENERATED by tools/gen_android_icon.js - edit the script, not this file. -->
<vector xmlns:android="http://schemas.android.com/apk/res/android" xmlns:aapt="http://schemas.android.com/aapt"
    android:width="108dp" android:height="108dp" android:viewportWidth="108" android:viewportHeight="108">
    ${inner}
</vector>
`;
const creaseXml = (c, s, alpha = 0.9) => pathEl(`M${f(c.x1)},${f(c.y)} L${f(c.x2)},${f(c.y)}`, `android:strokeColor="${COL.teal}" android:strokeAlpha="${alpha}" android:strokeWidth="${f(2.4 * s)}" android:strokeLineCap="round" android:fillColor="#00000000"`);
const rrect = (x, y, w, h, rad) => `M${f(x + rad)},${f(y)} H${f(x + w - rad)} A${rad},${rad} 0 0 1 ${f(x + w)},${f(y + rad)} V${f(y + h - rad)} A${rad},${rad} 0 0 1 ${f(x + w - rad)},${f(y + h)} H${f(x + rad)} A${rad},${rad} 0 0 1 ${f(x)},${f(y + h - rad)} V${f(y + rad)} A${rad},${rad} 0 0 1 ${f(x + rad)},${f(y)} Z`;

// ============ 1) adaptive icon: foreground / monochrome (artwork inside the safe circle) ============
const SA = 0.87;                                        // chosen so the farthest corner is inside the safe circle (verified below)
const TA = mapper(SA, ART_CENTRE, [54, 54]);
const fg = build(TA, SA);
const maxR = Math.max(...fg.pts.map(([x, y]) => Math.hypot(x - 54, y - 54)));
console.log(`artwork farthest point from the icon centre: ${maxR.toFixed(2)} (safe circle radius = 33, launcher mask radius = 36)`);
if (maxR > 34.2) throw new Error('artwork leaves the adaptive-icon safe zone - lower SA');
fs.writeFileSync(path.join(OUT, 'ic_launcher_foreground.xml'), ROOT(markXml(fg, SA, false), 'foreground'));
fs.writeFileSync(path.join(OUT, 'ic_launcher_monochrome.xml'), ROOT(markXml(fg, SA, true), 'monochrome (themed icon)'));

// ============ 2) adaptive icon background: navy gradient + teal glow + the crease line (cropping it is harmless) ============
const full = 'M0,0h108v108h-108z';
fs.writeFileSync(path.join(OUT, 'ic_launcher_background.xml'), ROOT([
  pathEl(full, '', aapt('fillColor', lin(8, 4, 100, 104, COL.navy1, COL.navy2))),
  pathEl(full, '', aapt('fillColor', `<gradient android:type="radial" android:centerX="54" android:centerY="58" android:gradientRadius="52"><item android:offset="0" android:color="#3300D4AA"/><item android:offset="1" android:color="#0000D4AA"/></gradient>`)),
  creaseXml({ x1: -4, x2: 112, y: fg.parts.crease.y + 1.2 * SA }, SA, 0.9)
].join('\n    '), 'background'));

// ============ 3) splash / error-screen logo: the full tile exactly like Static/brand/icon.svg ============
const SS = 1.16, TS = mapper(SS, [54, 55.5], [54, 55.5]);
const sp = build(TS, SS);
fs.writeFileSync(path.join(OUT, 'ic_splash_logo.xml'), ROOT([
  pathEl(rrect(0, 0, 108, 108, 24), '', aapt('fillColor', lin(8, 4, 100, 104, COL.navy1, COL.navy2))),
  pathEl(rrect(0, 0, 108, 108, 24), '', aapt('fillColor', `<gradient android:type="radial" android:centerX="54" android:centerY="58" android:gradientRadius="50"><item android:offset="0" android:color="#3300D4AA"/><item android:offset="1" android:color="#0000D4AA"/></gradient>`)),
  pathEl(rrect(0.75, 0.75, 106.5, 106.5, 23.25), 'android:strokeColor="#FFFFFF" android:strokeAlpha="0.12" android:strokeWidth="1.5" android:fillColor="#00000000"'),
  creaseXml(sp.parts.crease, SS),
  markXml(sp, SS, false)
].join('\n    '), 'splash logo'));
console.log('wrote ic_launcher_foreground / _background / _monochrome / ic_splash_logo into', OUT);
