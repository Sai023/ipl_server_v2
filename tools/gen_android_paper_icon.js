// Generates the Android drawables for the BCL Fantasy WebView app in the "scorebook" style, from the same drawing as the web mark.
//   node tools/gen_android_paper_icon.js        (any folder)  ->  Static/brand/android/*.xml
//
//   ic_launcher_foreground.xml   the pencil mark (three butter stumps, flying bail, red ball) with its double pencil outlines and hatching
//   ic_launcher_background.xml   cream paper: soft tone, ruled scorebook lines, a red margin, a pencil crease line
//   ic_launcher_monochrome.xml   Android 13 "themed icon" layer: one colour, the ball cut out of the stump behind it
//   pencil_line.xml              the pencil line that draws itself under the native loading splash (same strokes as .spinner in paper.css)
//
// How it works: tools/gen_paper_mark.js draws the mark as SVG (plain paths, clip-paths and one radial gradient). This script reads that SVG,
// bakes ONE uniform scale + shift into every coordinate (so no <group> transforms are needed) and writes it as VectorDrawable XML.
// An adaptive icon is a 108dp canvas that the launcher crops to a circle / squircle / rounded square; only the central 66dp circle (radius 33)
// is guaranteed to survive every mask, so the artwork is scaled until its smallest enclosing circle fits inside it (checked below).
// Everything is deterministic: re-running gives identical files. Preview them with:  node tools/preview_android_icon.js
const fs = require('fs'), path = require('path');
const { drawing, smooth, rng, wobble, PAL } = require('./gen_paper_mark.js');
const OUT = path.join(__dirname, '..', 'Static', 'brand', 'android');
fs.mkdirSync(OUT, { recursive: true });

const FIT_RADIUS = 32.0;     // the drawn shapes' enclosing circle, in dp (safe circle = 33, launcher mask = 36); pencil wobble + stroke add about 1
const LINE = (w) => (w >= 2 ? 1.3 : 1.1);   // pencil strokes are boosted a little so they still read at launcher size

const r2 = (n) => String(Math.round(n * 100) / 100);
const hex = (c) => (/^#[0-9a-f]{3}$/i.test(c) ? '#' + c.slice(1).split('').map((x) => x + x).join('') : c).toUpperCase();
const circ = (c, r, n = 40) => Array.from({ length: n }, (_, i) => [c[0] + Math.cos(i / n * 2 * Math.PI) * r, c[1] + Math.sin(i / n * 2 * Math.PI) * r]);

// ---- path data: bake p' = s*p + t into M / L / C / l / Z --------------------------------------------------------------------------------
function bakePath(d, T) {
  const toks = d.match(/[A-Za-z]|-?\d*\.?\d+/g) || []; let out = '', cmd = '', i = 0;
  const nums = (n) => { const a = []; for (let k = 0; k < n; k++) a.push(parseFloat(toks[i++])); return a; };
  const P = (x, y) => r2(T.s * x + T.tx) + ' ' + r2(T.s * y + T.ty), D = (x, y) => r2(T.s * x) + ' ' + r2(T.s * y);
  while (i < toks.length) {
    if (/[A-Za-z]/.test(toks[i])) cmd = toks[i++];
    if (cmd === 'M') { const [x, y] = nums(2); out += 'M' + P(x, y); cmd = 'L'; }
    else if (cmd === 'L') { const [x, y] = nums(2); out += 'L' + P(x, y); }
    else if (cmd === 'C') { const a = nums(6); out += 'C' + P(a[0], a[1]) + ' ' + P(a[2], a[3]) + ' ' + P(a[4], a[5]); }
    else if (cmd === 'l') { const [x, y] = nums(2); out += 'l' + D(x, y); }
    else if (cmd === 'Z' || cmd === 'z') { out += 'Z'; if (i < toks.length && !/[A-Za-z]/.test(toks[i])) throw new Error('numbers after Z'); }
    else throw new Error('path command not handled: ' + cmd);
  }
  return out;
}
const absPoints = (d) => { const pts = [], t = d.match(/[A-Za-z]|-?\d*\.?\d+/g) || []; let cmd = '', i = 0;
  while (i < t.length) { if (/[A-Za-z]/.test(t[i])) { cmd = t[i++]; continue; }
    const n = cmd === 'C' ? 6 : (cmd === 'M' || cmd === 'L' || cmd === 'l') ? 2 : 0; if (!n) { i++; continue; }
    const a = t.slice(i, i + n).map(parseFloat); i += n; if (cmd !== 'l') for (let k = 0; k < n; k += 2) pts.push([a[k], a[k + 1]]); if (cmd === 'M') cmd = 'L'; }
  return pts; };

// points ON the curve (cubics sampled): control points of the smoothed outlines overshoot the corners, the curve itself does not
function flatPoints(d) {
  const pts = [], t = d.match(/[A-Za-z]|-?\d*\.?\d+/g) || []; let cmd = '', i = 0, cur = [0, 0];
  while (i < t.length) {
    if (/[A-Za-z]/.test(t[i])) { cmd = t[i++]; continue; }
    if (cmd === 'C') { const a = t.slice(i, i + 6).map(parseFloat); i += 6;
      for (let k = 1; k <= 8; k++) { const u = k / 8, v = 1 - u; pts.push([0, 1].map((j) => v * v * v * cur[j] + 3 * v * v * u * a[j] + 3 * v * u * u * a[2 + j] + u * u * u * a[4 + j])); } cur = [a[4], a[5]]; }
    else if (cmd === 'M' || cmd === 'L') { cur = [parseFloat(t[i]), parseFloat(t[i + 1])]; i += 2; pts.push(cur); if (cmd === 'M') cmd = 'L'; }
    else if (cmd === 'l') { cur = [cur[0] + parseFloat(t[i]), cur[1] + parseFloat(t[i + 1])]; i += 2; pts.push(cur); }
    else i++;
  }
  return pts;
}

// ---- SVG (from gen_paper_mark.js) -> a flat list of nodes ----------------------------------------------------------------------------------
function parseNodes(markup) {
  const nodes = [], re = /<(\/?)(g|path|ellipse)\b([^>]*?)(\/?)>/g; let m;
  while ((m = re.exec(markup))) {
    const [, close, tag, rest] = m;
    if (tag === 'g') { nodes.push(close ? { t: 'end' } : { t: 'group', clip: /clip-path="url\(#([^)]+)\)"/.exec(rest)[1] }); continue; }
    const a = {}; rest.replace(/([\w-]+)="([^"]*)"/g, (_, k, v) => (a[k] = v)); nodes.push({ t: tag, a });
  }
  return nodes;
}
function parseDefs(defs) {
  const clips = {}, grads = {};
  defs.replace(/<clipPath id="([^"]+)"><path d="([^"]+)"\/><\/clipPath>/g, (_, id, d) => (clips[id] = d));
  defs.replace(/<radialGradient id="([^"]+)" cx="([^"]+)" cy="([^"]+)" r="([^"]+)">(.*?)<\/radialGradient>/g, (_, id, cx, cy, r, st) => {
    const stops = []; st.replace(/<stop offset="([^"]+)" stop-color="([^"]+)"\/>/g, (__, o, c) => stops.push([+o, hex(c)])); grads[id] = { cx: +cx, cy: +cy, r: +r, stops };
  });
  return { clips, grads };
}

// ---- nodes -> VectorDrawable XML -----------------------------------------------------------------------------------------------------------
function ellipsePath(a, T) {
  const cx = +a.cx, cy = +a.cy, rx = +a.rx, ry = +a.ry, deg = +((/rotate\(\s*([-\d.]+)/.exec(a.transform || '') || [0, 0])[1]), th = deg * Math.PI / 180;
  const dx = rx * Math.cos(th), dy = rx * Math.sin(th), P = (x, y) => r2(T.s * x + T.tx) + ' ' + r2(T.s * y + T.ty), arc = `A${r2(rx * T.s)} ${r2(ry * T.s)} ${deg} 1 0 `;
  return `M${P(cx - dx, cy - dy)}${arc}${P(cx + dx, cy + dy)}${arc}${P(cx - dx, cy - dy)}Z`;
}
function pathXml(a, T, defs) {
  const at = [`android:pathData="${a.t === 'ellipse' ? ellipsePath(a.a, T) : bakePath(a.a.d, T)}"`], p = a.a; let inner = '';
  if (p.fill && p.fill !== 'none') {
    const g = /^url\(#([^)]+)\)$/.exec(p.fill);
    if (g) {                                                              // SVG objectBoundingBox radial gradient -> absolute coordinates
      const gr = defs.grads[g[1]], pts = absPoints(p.d), xs = pts.map((q) => q[0]), ys = pts.map((q) => q[1]);
      const x0 = Math.min(...xs), y0 = Math.min(...ys), w = Math.max(...xs) - x0, h = Math.max(...ys) - y0;
      inner = `<aapt:attr name="android:fillColor"><gradient android:type="radial" android:centerX="${r2(T.s * (x0 + gr.cx * w) + T.tx)}" android:centerY="${r2(T.s * (y0 + gr.cy * h) + T.ty)}" android:gradientRadius="${r2(T.s * gr.r * w)}">` +
        gr.stops.map(([o, c]) => `<item android:offset="${o}" android:color="${c}"/>`).join('') + `</gradient></aapt:attr>`;
    } else { at.push(`android:fillColor="${hex(p.fill)}"`); if (p['fill-opacity'] != null) at.push(`android:fillAlpha="${p['fill-opacity']}"`); }
  }
  if (p.stroke && p.stroke !== 'none') {
    const w = +p['stroke-width']; at.push(`android:strokeColor="${hex(p.stroke)}"`, `android:strokeWidth="${r2(w * T.s * LINE(w))}"`);
    if (p['stroke-opacity'] != null) at.push(`android:strokeAlpha="${p['stroke-opacity']}"`);
    if (p['stroke-linecap']) at.push(`android:strokeLineCap="${p['stroke-linecap']}"`);
    if (p['stroke-linejoin']) at.push(`android:strokeLineJoin="${p['stroke-linejoin']}"`);
  }
  return inner ? `<path ${at.join(' ')}>${inner}</path>` : `<path ${at.join(' ')}/>`;
}
function nodesXml(nodes, T, defs) {
  const out = [];
  for (const n of nodes) {
    if (n.t === 'group') out.push(`<group><clip-path android:pathData="${bakePath(defs.clips[n.clip], T)}"/>`);
    else if (n.t === 'end') out.push('</group>');
    else out.push(pathXml(n, T, defs));
  }
  return out.join('\n    ');
}
const ROOT = (label, inner) => `<?xml version="1.0" encoding="utf-8"?>
<!-- BCL Fantasy ${label}. GENERATED by tools/gen_android_paper_icon.js - edit the script, not this file. -->
<vector xmlns:android="http://schemas.android.com/apk/res/android" xmlns:aapt="http://schemas.android.com/aapt"
    android:width="108dp" android:height="108dp" android:viewportWidth="108" android:viewportHeight="108">
    ${inner}
</vector>
`;
const pathLine = (d, attrs) => `<path android:pathData="${d}" ${attrs}/>`;

// ---- the drawing, fitted to the adaptive-icon safe circle -----------------------------------------------------------------------------------
const D = drawing(), nodes = parseNodes(D.body), defs = parseDefs(D.defs), S = D.shapes;
const shapePts = [].concat(...S.planks, S.bail, S.ball.poly);
function enclosingCircle(pts) {                                            // Badoiu-Clarkson: converges on the smallest enclosing circle
  let c = pts[0].slice();
  for (let i = 1; i <= 3000; i++) { let far = pts[0], fd = -1; for (const p of pts) { const d = Math.hypot(p[0] - c[0], p[1] - c[1]); if (d > fd) { fd = d; far = p; } } c = [c[0] + (far[0] - c[0]) / (i + 1), c[1] + (far[1] - c[1]) / (i + 1)]; }
  return { c, r: Math.max(...pts.map((p) => Math.hypot(p[0] - c[0], p[1] - c[1]))) };
}
const enc = enclosingCircle(shapePts), s = FIT_RADIUS / enc.r;
const T = { s, tx: 54 - s * enc.c[0], ty: 54 - s * enc.c[1] };
// how far does everything we draw (control points of every path, shapes and pencil wobble included) reach from the icon centre?
// (paths inside a clip group are cut to their clip, e.g. hatching that runs past a stump's end, so the clip outline counts instead)
const visible = []; { let depth = 0; for (const n of nodes) { if (n.t === 'group') { depth++; visible.push(...flatPoints(defs.clips[n.clip])); } else if (n.t === 'end') depth--; else if (!depth) visible.push(...(n.a.d ? flatPoints(n.a.d) : [[+n.a.cx, +n.a.cy]])); } }
const reach = Math.max(...visible.map(([x, y]) => Math.hypot(s * x + T.tx - 54, s * y + T.ty - 54)));
console.log(`scale ${r2(s)}  enclosing circle of the shapes r=${r2(enc.r * s)}dp  farthest drawn point ${r2(reach)}dp (safe circle 33, launcher mask 36)`);
if (reach > 35) throw new Error('artwork leaves the launcher mask - lower FIT_RADIUS');

// 1) foreground: the coloured pencil drawing
fs.writeFileSync(path.join(OUT, 'ic_launcher_foreground.xml'), ROOT('launcher foreground (pencil mark)', nodesXml(nodes, T, defs)));

// 2) background: cream paper, ruled lines, a red margin and a pencil crease (any cropping of the edges is harmless)
const rules = []; for (let y = 11; y < 108; y += 12) rules.push(pathLine(`M-2,${y}H110`, `android:strokeColor="#628CB8" android:strokeAlpha="0.22" android:strokeWidth="0.5"`));
const crease = smooth(wobble([[14, 94], [94, 94]], false, 0.5, 4, rng(5)), false).replace(/(-?\d+\.\d+)/g, (m) => r2(+m));
fs.writeFileSync(path.join(OUT, 'ic_launcher_background.xml'), ROOT('launcher background (paper)', [
  `<path android:pathData="M0,0h108v108h-108z"><aapt:attr name="android:fillColor"><gradient android:type="radial" android:centerX="54" android:centerY="48" android:gradientRadius="80"><item android:offset="0" android:color="#FBF6E8"/><item android:offset="1" android:color="${PAL.paper}"/></gradient></aapt:attr></path>`,
  ...rules,
  pathLine('M24,-2V110', `android:strokeColor="${PAL.ball}" android:strokeAlpha="0.3" android:strokeWidth="0.6"`),
  pathLine(crease, `android:strokeColor="${PAL.pencil}" android:strokeAlpha="0.45" android:strokeWidth="1" android:strokeLineCap="round"`),
].join('\n    ')));

// 3) themed icon: one colour. Stumps and bail solid; the ball is a ring with seams, and the stump behind it is cut away (a clip made of the
//    whole canvas plus the ball drawn the opposite way round, so the ball's area has winding 0 and is left out)
const bc = S.ball.c, br = S.ball.r, plank = S.planks.map((p) => smooth(p, true)), ballPath = smooth(circ(bc, br, 40), true);
const cutOut = 'M-60 -60L400 -60L400 400L-60 400Z' + smooth(circ(bc, br + 7, 40).reverse(), true);
const seamPts = (off) => { const a = []; for (let t = -1; t <= 1.001; t += .125) a.push([bc[0] + off + 12 * (1 - t * t) + t * 4, bc[1] + t * 40]); return a; };
// the two seams are thin closed bands drawn the opposite way round inside the (solid) ball, so they stay empty: a bold ball with seam lines
const area = (p) => p.reduce((a, q, i) => { const n = p[(i + 1) % p.length]; return a + (q[0] * n[1] - n[0] * q[1]) / 2; }, 0);
const band = (off, half = 2.2) => {
  const c = seamPts(off), side = (sign) => c.map((p, i) => { const a = c[Math.max(0, i - 1)], b = c[Math.min(c.length - 1, i + 1)], l = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1; return [p[0] - sign * (b[1] - a[1]) / l * half, p[1] + sign * (b[0] - a[0]) / l * half]; });
  let poly = side(1).concat(side(-1).reverse()); if (Math.sign(area(poly)) === Math.sign(area(circ(bc, br)))) poly = poly.reverse(); return smooth(poly, true);
};
const ballSolid = ballPath + band(-10) + band(10);
fs.writeFileSync(path.join(OUT, 'ic_launcher_monochrome.xml'), ROOT('themed icon (monochrome)', [
  `<group><clip-path android:pathData="${bakePath(cutOut, T)}"/>`,
  ...plank.map((d) => pathLine(bakePath(d, T), 'android:fillColor="#000000"')),
  pathLine(bakePath(smooth(S.bail, true), T), 'android:fillColor="#000000"'),
  '</group>',
  pathLine(bakePath(ballSolid, T), 'android:fillColor="#000000"'),
].join('\n    ')));

// 4) the pencil line under the native loading splash (the strokes of .spinner in Static/paper.css, in a 190 x 14 box)
fs.writeFileSync(path.join(OUT, 'pencil_line.xml'), `<?xml version="1.0" encoding="utf-8"?>
<!-- BCL Fantasy pencil line for the native loading splash. GENERATED by tools/gen_android_paper_icon.js - edit the script, not this file. -->
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="190dp" android:height="14dp" android:viewportWidth="190" android:viewportHeight="14">
    <path android:pathData="M3,8 C30,4 52,11 80,7 S135,5 187,8" android:strokeColor="${PAL.pencil}" android:strokeWidth="2.4" android:strokeLineCap="round"/>
    <path android:pathData="M6,10.5 C40,7.5 70,12.5 110,9.5 S160,8.5 184,10" android:strokeColor="${PAL.pencil}" android:strokeAlpha="0.5" android:strokeWidth="1.2" android:strokeLineCap="round"/>
</vector>
`);
console.log('wrote ic_launcher_foreground / _background / _monochrome and pencil_line into', OUT);
