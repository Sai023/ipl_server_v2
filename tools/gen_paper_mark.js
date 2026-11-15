// Generates the compact BCL mark in the "scorebook" style: three butter-yellow stumps, a flying bail and a red ball, all
// drawn with a wobbly double pencil stroke and a little hatching. Used for the app bar, favicon, PWA / iPhone icons and
// (later) the Android launcher icon.
//   node tools/gen_paper_mark.js            -> writes Static/brand/mark-paper.svg (transparent) and icon-paper.svg (paper tile)
// Rasterise to PNG with tools/make_brand_pngs.ps1. Everything is deterministic (seeded), so re-runs give identical files.
const fs = require('fs'), path = require('path');
const OUT = path.join(__dirname, '..', 'Static', 'brand');
const PAL = { pencil: '#4A4337', paper: '#F3EBD8', butter: '#F6D77E', butterDeep: '#E9B93C', butterLine: '#C99B26', ball: '#D9442F', ballDark: '#A52A22', ballLight: '#F4907F', seam: '#7C1F1A', mint: '#B4E2D2' };

// ---- tiny helpers ---------------------------------------------------------------------------------------------------
const rng = (seed) => { let a = seed >>> 0; return () => { a = (a + 0x6D2B79F5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; };
const f = (n) => +n.toFixed(1);
const rot = (p, a, c) => { const s = Math.sin(a), co = Math.cos(a), x = p[0] - c[0], y = p[1] - c[1]; return [c[0] + x * co - y * s, c[1] + x * s + y * co]; };

function roundedRect(x, y, w, h, rt, rb, seg = 7) {                     // polygon of a rect with separate top / bottom corner radii
  const pts = []; const arc = (cx, cy, r, a0, a1) => { for (let i = 0; i <= seg; i++) { const a = a0 + (a1 - a0) * i / seg; pts.push([cx + Math.cos(a) * r, cy + Math.sin(a) * r]); } };
  arc(x + rt, y + rt, rt, Math.PI, 1.5 * Math.PI); arc(x + w - rt, y + rt, rt, 1.5 * Math.PI, 2 * Math.PI);
  arc(x + w - rb, y + h - rb, rb, 0, 0.5 * Math.PI); arc(x + rb, y + h - rb, rb, 0.5 * Math.PI, Math.PI);
  return pts;
}
const circle = (cx, cy, r, n = 40) => Array.from({ length: n }, (_, i) => [cx + Math.cos(i / n * 2 * Math.PI) * r, cy + Math.sin(i / n * 2 * Math.PI) * r]);

// resample a closed/open polyline at ~step spacing and push every point sideways by noise: a hand-drawn line
function wobble(poly, closed, amp, step, rand) {
  const P = closed ? poly.concat([poly[0]]) : poly, out = [];
  for (let i = 0; i < P.length - 1; i++) {
    const a = P[i], b = P[i + 1], len = Math.hypot(b[0] - a[0], b[1] - a[1]), n = Math.max(1, Math.round(len / step));
    for (let k = 0; k < n; k++) out.push([a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n]);
  }
  if (!closed) out.push(P[P.length - 1]);
  // low-frequency noise along the line (a slow drift) + a little jitter, perpendicular to the direction of travel
  const ph = rand() * 10, res = out.map((p, i) => {
    const q = out[(i + 1) % out.length], o = out[(i + out.length - 1) % out.length]; let tx = q[0] - o[0], ty = q[1] - o[1]; const l = Math.hypot(tx, ty) || 1; tx /= l; ty /= l;
    const d = amp * (Math.sin(i * 0.55 + ph) * 0.65 + (rand() - 0.5) * 0.9);
    return [p[0] - ty * d, p[1] + tx * d];
  });
  return res;
}
function smooth(pts, closed) {                                           // Catmull-Rom -> cubic Bezier path
  const n = pts.length, g = (i) => closed ? pts[(i + n) % n] : pts[Math.min(n - 1, Math.max(0, i))];
  let d = `M${f(pts[0][0])} ${f(pts[0][1])}`; const last = closed ? n : n - 1;
  for (let i = 0; i < last; i++) { const p0 = g(i - 1), p1 = g(i), p2 = g(i + 1), p3 = g(i + 2);
    d += `C${f(p1[0] + (p2[0] - p0[0]) / 6)} ${f(p1[1] + (p2[1] - p0[1]) / 6)} ${f(p2[0] - (p3[0] - p1[0]) / 6)} ${f(p2[1] - (p3[1] - p1[1]) / 6)} ${f(p2[0])} ${f(p2[1])}`; }
  return d + (closed ? 'Z' : '');
}
const stroke = (d, w, op, col = PAL.pencil) => `<path d="${d}" fill="none" stroke="${col}" stroke-width="${w}" stroke-opacity="${op}" stroke-linecap="round" stroke-linejoin="round"/>`;
const sketch = (poly, closed, rand, w = 3.1) => stroke(smooth(wobble(poly, closed, 1.5, 9, rand), closed), w, .92) + stroke(smooth(wobble(poly, closed, 2.1, 11, rand), closed), w * .62, .5);

// ---- the drawing ---------------------------------------------------------------------------------------------------
function drawing(seed = 11) {
  const R = rng(seed), C = [128, 130], A = 38 * Math.PI / 180, defs = [], body = [];
  const R2 = (p) => rot(p, A, C);
  const planks = [-62, 0, 62].map((dx, i) => {
    const local = roundedRect(C[0] + dx - 19, 36, 38, 176, 15, 3), poly = local.map(R2);
    return { poly, dx, local, i };
  });
  // stumps (back to front: right, middle, left so the ball overlaps the left one)
  [planks[2], planks[1], planks[0]].forEach((pl, k) => {
    const id = 'sp' + pl.i;
    defs.push(`<clipPath id="${id}"><path d="${smooth(pl.poly, true)}"/></clipPath>`);
    body.push(`<path d="${smooth(pl.poly, true)}" fill="${PAL.butter}"/>`);
    // soft shading on the right half of each stump + hatch strokes
    const sh = roundedRect(C[0] + pl.dx + 3, 36, 16, 176, 8, 3).map(R2);
    body.push(`<g clip-path="url(#${id})"><path d="${smooth(sh, true)}" fill="${PAL.butterDeep}" fill-opacity=".55"/>`);
    let hat = ''; for (let y = 50; y < 220; y += 7.5) { const a = R2([C[0] + pl.dx - 18, y + R() * 2]), b = R2([C[0] + pl.dx - 18 + 13 + R() * 14, y + 5 + R() * 3]); hat += `M${f(a[0])} ${f(a[1])}L${f(b[0])} ${f(b[1])}`; }
    body.push(stroke(hat, 1.15, .34) + `</g>`);
    body.push(sketch(pl.poly, true, R));
    // the heavier shadow edge down the right-hand side, as in the sketch
    const edge = [[C[0] + pl.dx + 19, 54], [C[0] + pl.dx + 19, 205]].map(R2);
    body.push(stroke(smooth(wobble(edge, false, 1.1, 8, R), false), 3.4, .95));
  });
  // the flying bail
  const bail = roundedRect(-26, -7, 52, 14, 7, 7).map((p) => rot([p[0] + 218, p[1] + 36], -24 * Math.PI / 180, [218, 36]));
  body.push(`<path d="${smooth(bail, true)}" fill="${PAL.butter}"/>` + sketch(bail, true, R, 2.6) + stroke(smooth(wobble([rot([201, 36], -24 * Math.PI / 180, [218, 36]), rot([235, 36], -24 * Math.PI / 180, [218, 36])], false, .6, 8, R), false), 1.2, .45));
  // the ball
  const bc = [58, 78], br = 42, bp = circle(bc[0], bc[1], br, 36);
  defs.push(`<radialGradient id="bg" cx=".36" cy=".3" r=".85"><stop offset="0" stop-color="${PAL.ballLight}"/><stop offset=".42" stop-color="${PAL.ball}"/><stop offset="1" stop-color="${PAL.ballDark}"/></radialGradient>`);
  defs.push(`<clipPath id="bclip"><path d="${smooth(bp, true)}"/></clipPath>`);
  body.push(`<path d="${smooth(bp, true)}" fill="url(#bg)"/>`);
  // seam: two parallel arcs with stitch ticks, as drawn on the sketch ball
  const seam = (off) => { const pts = []; for (let t = -1; t <= 1.001; t += .125) pts.push([bc[0] + off + 12 * (1 - t * t) * 1.0 + t * 4, bc[1] + t * 40]); return pts; };
  let seamSvg = ''; for (const off of [-10, 10]) { const pts = seam(off); seamSvg += stroke(smooth(wobble(pts, false, .8, 6, R), false), 2.1, .85, PAL.seam); pts.forEach((p, i) => { if (i % 1 === 0 && i > 0 && i < pts.length - 1) seamSvg += stroke(`M${f(p[0] + (off < 0 ? -6 : 3))} ${f(p[1])}l${off < 0 ? 4 : 4} 1.5`, 1.1, .6, PAL.seam); }); }
  let hatB = ''; for (let k = 0; k < 7; k++) { const x = bc[0] + 8 + k * 6, y0 = bc[1] + 20 + k * 2.3; hatB += `M${f(x)} ${f(y0)}l-9 12`; }
  body.push(`<g clip-path="url(#bclip)">${seamSvg}${stroke(hatB, 1.2, .38, PAL.seam)}<ellipse cx="${bc[0] - 15}" cy="${bc[1] - 17}" rx="8.5" ry="5.5" transform="rotate(-35 ${bc[0] - 15} ${bc[1] - 17})" fill="#fff" fill-opacity=".55"/></g>`);
  body.push(sketch(bp, true, R, 3.2));
  // `shapes` is the bare geometry (no pencil wobble) for tools that redraw the mark another way, e.g. tools/gen_android_paper_icon.js
  return { defs: defs.join(''), body: body.join(''), shapes: { planks: planks.map((p) => p.poly), bail, ball: { c: bc, r: br, poly: bp } } };
}

// fit the drawing to a 256 viewBox (the bounding box is measured from the drawing's own control points)
function svgMark({ tile = false, size = 256 } = {}) {
  const d = drawing();
  const fit = tile ? 'translate(38 40) scale(.70)' : 'translate(-4 2) scale(.98)';
  const grain = tile ? `<filter id="gr" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency=".9" numOctaves="3" seed="4" stitchTiles="stitch"/><feColorMatrix values="0 0 0 0 .36  0 0 0 0 .29  0 0 0 0 .19  0 0 0 .5 -.14"/></filter>` : '';
  const bgTile = tile ? `<rect width="256" height="256" fill="${PAL.paper}"/><rect width="256" height="256" filter="url(#gr)" opacity=".8"/>${stroke(smooth(wobble([[30, 226], [226, 226]], false, 1.6, 9, rng(5)), false), 3, .55)}` : '';
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="${size}" height="${size}"><defs>${d.defs}${grain}</defs>${bgTile}<g transform="${fit}">${d.body}</g></svg>\n`;
}

if (require.main === module) {
  fs.mkdirSync(OUT, { recursive: true });
  fs.writeFileSync(path.join(OUT, 'mark-paper.svg'), svgMark({ tile: false }));
  fs.writeFileSync(path.join(OUT, 'icon-paper.svg'), svgMark({ tile: true }));
  console.log('wrote Static/brand/mark-paper.svg and icon-paper.svg');
}
module.exports = { svgMark, drawing, smooth, rng, wobble, PAL };
