// Contrast guard for the "scorebook" theme. Reads the colour tokens straight from Static/theme.css and checks that every text colour
// is readable (WCAG AA, 4.5:1) on the surfaces and tinted backgrounds it is used on. Exits 1 on any failure.
//   node tools/theme_contrast.js           (add --verbose for the full table)
const fs = require('fs'), path = require('path');
const css = fs.readFileSync(path.join(__dirname, '..', 'Static', 'theme.css'), 'utf8');
const T = {}; for (const m of css.matchAll(/--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\b/g)) T[m[1]] = m[2];
const need = (k) => { if (!T[k]) throw new Error('token --' + k + ' not found in theme.css'); return T[k]; };
const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const lin = (v) => { v /= 255; return v <= .03928 ? v / 12.92 : Math.pow((v + .055) / 1.055, 2.4); };
const lum = (c) => .2126 * lin(c[0]) + .7152 * lin(c[1]) + .0722 * lin(c[2]);
const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05); };
const over = (a, b, t) => a.map((v, i) => v * t + b[i] * (1 - t));          // colour a at opacity t over colour b
const verbose = process.argv.includes('--verbose');
let fails = 0, checks = 0;
function check(label, fg, bg, min = 4.5) { const r = ratio(fg, bg); checks++; const ok = r >= min; if (!ok) fails++; if (!ok || verbose) console.log(`${ok ? 'ok  ' : 'FAIL'} ${r.toFixed(2).padStart(5)}  (min ${min})  ${label}`); }

const surfaces = { paper: hex(need('paper')), sheet: hex(need('sheet')), 'sheet-2': hex(need('sheet-2')), 'sheet-3': hex(need('sheet-3')) };
for (const t of ['text', 'text-strong', 'muted', 'dim']) for (const [s, bg] of Object.entries(surfaces)) check(`--${t} on --${s}`, hex(need(t)), bg);
for (const fam of ['gold', 'teal', 'danger', 'success', 'info', 'violet', 'orange']) {
  const ink = hex(need(fam + '-ink')), wash = hex(need(fam + '-wash')), fill = hex(need(fam + '-fill'));
  for (const [s, bg] of Object.entries(surfaces)) check(`--${fam}-ink on --${s}`, ink, bg);
  for (const t of [0.3, 0.46]) check(`--${fam}-ink on ${fam} tint ${t * 100}% over --sheet`, ink, over(wash, surfaces.sheet, t));
  check(`--on-fill on --${fam}-fill`, hex(need('on-fill')), fill, 7);
}
// the Match Centre pre-season note: text on the butter tint it sits on
for (const t of ['text', 'text-strong']) check(`--${t} on gold tint 30% over --sheet-2 (pre-season note)`, hex(need(t)), over(hex(need('gold-wash')), surfaces['sheet-2'], 0.3));
// the picker's team chips (Ui.chips) and "Your XI" cells: dark text on the butter tints they sit on
const butter = (t) => over(hex(need('gold-fill')), surfaces['sheet-2'], t);
check('--on-fill on butter 70% over --sheet-2 (team chip, on)', hex(need('on-fill')), butter(0.70), 7);
check('--on-fill on butter 38% over --sheet-2 (Your XI cell: name, price)', hex(need('on-fill')), butter(0.38), 7);
check('--muted on butter 38% over --sheet-2 (Your XI cell: role line)', hex(need('muted')), butter(0.38));
console.log(`${checks} checks, ${fails} failing`);
process.exit(fails ? 1 : 0);
