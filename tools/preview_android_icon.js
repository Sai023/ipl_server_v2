// Renders the generated Android drawables (Static/brand/android/*.xml, made by tools/gen_android_paper_icon.js) as an HTML preview: the
// launcher masks a phone applies (circle / squircle / rounded square), the safe-zone ring, the themed (monochrome) icon and the pencil line.
//   node tools/preview_android_icon.js [out.html]     (default: a file in your temp folder; open it in a browser)
// It parses the real XML files, so what you see is what the APK contains.
const fs = require('fs'), path = require('path');
const DIR = 'Static/brand/android', OUT = process.argv[2] || path.join(require('os').tmpdir(), 'bcl_android_icon_preview.html');
let gid = 0;

function colour(c) {                                   // #RRGGBB or #AARRGGBB -> {rgb, a}
  c = (c || '').replace('#', '');
  if (c.length === 8) return { rgb: '#' + c.slice(2), a: parseInt(c.slice(0, 2), 16) / 255 };
  return { rgb: '#' + c, a: 1 };
}
const attr = (s, n) => { const m = new RegExp(n.replace(':', '\\:') + '="([^"]*)"').exec(s); return m ? m[1] : null; };

function toSvg(xml) {
  const defs = [], body = [], stack = []; let pending = null, gradient = null, cur = null;
  const re = /<(\/?)([\w:-]+)([^>]*?)(\/?)>/g; let m;
  while ((m = re.exec(xml))) {
    const [, close, tag, a, selfClose] = m;
    if (tag === 'aapt:attr' && !close) { pending = attr(a, 'name').replace('android:', ''); continue; }
    if (tag === 'aapt:attr' && close) { pending = null; continue; }
    if (tag === 'gradient' && !close) {
      const id = 'g' + (++gid), type = attr(a, 'android:type');
      gradient = { id, type, stops: [], a };
      continue;
    }
    if (tag === 'item' && gradient) { const c = colour(attr(a, 'android:color')); gradient.stops.push(`<stop offset="${attr(a, 'android:offset')}" stop-color="${c.rgb}" stop-opacity="${c.a}"/>`); continue; }
    if (tag === 'gradient' && close) {
      const g = gradient, A = (n) => attr(g.a, 'android:' + n);
      defs.push(g.type === 'radial'
        ? `<radialGradient id="${g.id}" gradientUnits="userSpaceOnUse" cx="${A('centerX')}" cy="${A('centerY')}" r="${A('gradientRadius')}">${g.stops.join('')}</radialGradient>`
        : `<linearGradient id="${g.id}" gradientUnits="userSpaceOnUse" x1="${A('startX')}" y1="${A('startY')}" x2="${A('endX')}" y2="${A('endY')}">${g.stops.join('')}</linearGradient>`);
      if (cur && pending) cur[pending] = `url(#${g.id})`;
      gradient = null; continue;
    }
    if (tag === 'clip-path') { const id = 'c' + (++gid); defs.push(`<clipPath id="${id}"><path d="${attr(a, 'android:pathData')}"/></clipPath>`); body.push(`<g clip-path="url(#${id})">`); stack.push('g'); continue; }
    if (tag === 'group' && close) { if (stack.pop() === 'g') body.push('</g>'); continue; }
    if (tag === 'path' && !close) {
      cur = { d: attr(a, 'android:pathData'), fillColor: attr(a, 'android:fillColor'), strokeColor: attr(a, 'android:strokeColor'), strokeWidth: attr(a, 'android:strokeWidth'), cap: attr(a, 'android:strokeLineCap'), fa: attr(a, 'android:fillAlpha'), sa: attr(a, 'android:strokeAlpha') };
      if (selfClose) { body.push(pathSvg(cur)); cur = null; }
      continue;
    }
    if (tag === 'path' && close) { if (cur) body.push(pathSvg(cur)); cur = null; }
  }
  return { defs: defs.join(''), body: body.join('') };
}
function pathSvg(p) {
  const fill = p.fillColor ? (p.fillColor.startsWith('url') ? p.fillColor : colour(p.fillColor).rgb) : 'none';
  const fo = p.fillColor && !p.fillColor.startsWith('url') ? colour(p.fillColor).a * (p.fa ? +p.fa : 1) : (p.fa ? +p.fa : 1);
  const stroke = p.strokeColor ? colour(p.strokeColor).rgb : 'none';
  return `<path d="${p.d}" fill="${fill}" fill-opacity="${fill === 'none' ? 0 : fo}" stroke="${stroke}" stroke-opacity="${p.sa || 1}" stroke-width="${p.strokeWidth || 0}" stroke-linecap="${p.cap || 'butt'}"/>`;
}
const load = (n) => toSvg(fs.readFileSync(path.join(DIR, n), 'utf8'));
const bg = load('ic_launcher_background.xml'), fg = load('ic_launcher_foreground.xml'), mono = load('ic_launcher_monochrome.xml'), pen = load('pencil_line.xml');

const layers = (extra = '') => `<defs>${bg.defs}${fg.defs}${extra}</defs>`;
const shapes = {
  'Circle': (c) => `<circle cx="54" cy="54" r="36"/>`.replace('<circle', '<circle'),
  'Squircle': () => `<path d="M54,18 C84,18 90,24 90,54 C90,84 84,90 54,90 C24,90 18,84 18,54 C18,24 24,18 54,18 Z"/>`,
  'Rounded square': () => `<rect x="18" y="18" width="72" height="72" rx="16"/>`
};
let n = 0;
const tile = (label, shapeFn, size = 132) => { const id = 'm' + (++n); return `<figure><svg width="${size}" height="${size}" viewBox="18 18 72 72">${layers(`<clipPath id="${id}">${shapeFn()}</clipPath>`)}<g clip-path="url(#${id})">${bg.body}${fg.body}</g></svg><figcaption>${label}</figcaption></figure>`; };
const html = `<!doctype html><meta charset="utf-8"><title>Android icon preview</title>
<style>body{margin:0;padding:14px;background:#1b2330;color:#cbd5e1;font:12px system-ui;display:flex;flex-wrap:wrap;gap:14px;align-items:flex-start}
figure{margin:0;text-align:center}figcaption{margin-top:6px}.light{background:#e8edf3;padding:10px;border-radius:12px}h4{width:100%;margin:6px 0 -4px;font-weight:600;color:#94a3b8}</style>
<h4>How phones will crop it</h4>
${Object.entries(shapes).map(([k, fn]) => tile(k, fn)).join('')}
<h4>Full 108dp canvas: safe-zone ring (radius 33) and mask edge (radius 36)</h4>
<figure><svg width="260" height="260" viewBox="0 0 108 108">${layers()}${bg.body}${fg.body}<circle cx="54" cy="54" r="33" fill="none" stroke="#fff" stroke-opacity=".7" stroke-dasharray="2 2" stroke-width=".6"/><circle cx="54" cy="54" r="36" fill="none" stroke="#ff5a5a" stroke-opacity=".8" stroke-width=".6"/></svg><figcaption>foreground + background</figcaption></figure>
<h4>Android 13 themed icon (monochrome layer, tinted by the wallpaper)</h4>
<figure class="light"><svg width="132" height="132" viewBox="18 18 72 72"><defs><clipPath id="mc"><circle cx="54" cy="54" r="36"/></clipPath></defs><g clip-path="url(#mc)"><rect width="108" height="108" fill="#cfe0ff"/><g style="filter:none">${mono.body.replace(/#000000/g, '#1f3a6d')}</g></g></svg><figcaption>themed</figcaption></figure>
<h4>Pencil line under the native loading splash (190 x 14dp, on the paper colour)</h4>
<figure style="background:#F3EBD8;padding:18px;border-radius:12px"><svg width="190" height="14" viewBox="0 0 190 14"><defs>${pen.defs}</defs>${pen.body}</svg><figcaption style="color:#4A4337">pencil line</figcaption></figure>`;
fs.writeFileSync(OUT, html);
console.log('preview written:', OUT);
