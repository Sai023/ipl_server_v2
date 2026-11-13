// Generates every BCL Fantasy logo asset from ONE definition of the mark, so they can never drift apart.
// Output: Static/brand/*.svg (+ android/*.xml). PNGs are rasterised afterwards from these SVGs.
const fs = require('fs'), path = require('path');
const OUT = 'Static/brand'; fs.mkdirSync(OUT + '/android', { recursive: true });

// ---- palette (matches the app: navy / gold / teal, plus the cricket-ball red)
const C = { navy1: '#16345F', navy2: '#07111F', gold1: '#FFE88A', gold2: '#F2B705', red1: '#FF6B5F', red2: '#C41F2C', teal: '#00D4AA', white: '#FFFFFF' };

// ---- the mark, on a 108 x 108 grid (same grid as an Android adaptive icon). Centre of the artwork = (54, 55.5).
function mark(s = 1) {
  const g = (inner) => `<g transform="translate(54 55.5) scale(${s}) translate(-54 -55.5)">${inner}</g>`;
  const stump = (x) => `<path d="M${x},82 V45.5 a3.5,3.5 0 0 1 7,0 V82 Z" fill="url(#bcl-gold)"/>`;
  return g(
    // popping crease (the ground line)
    `<path d="M23,87.5 H85" stroke="${C.teal}" stroke-width="2.4" stroke-linecap="round" opacity=".9"/>` +
    // three stumps
    stump(28) + stump(40) + stump(52) +
    // a soft highlight down the left edge of each stump
    `<path d="M29.4,47 V80 M41.4,47 V80 M53.4,47 V80" stroke="#fff" stroke-opacity=".38" stroke-width="1.3" stroke-linecap="round"/>` +
    // bail still resting on stumps 1-2
    `<rect x="30" y="38.4" width="13" height="2.9" rx="1.45" fill="url(#bcl-gold)"/>` +
    // bail knocked flying up and to the right
    `<g transform="rotate(-30 64 32)"><rect x="57.5" y="30.5" width="13" height="2.9" rx="1.45" fill="url(#bcl-gold)"/></g>` +
    // the ball striking the stumps
    `<circle cx="70" cy="65" r="12.5" fill="url(#bcl-red)"/>` +
    `<clipPath id="bcl-ballclip"><circle cx="70" cy="65" r="12.5"/></clipPath>` +
    `<g clip-path="url(#bcl-ballclip)" fill="none" stroke="${C.white}" stroke-linecap="round">` +
      `<path d="M67,52 C60.5,59.5 60.5,70.5 67,78" stroke-width="1.6"/>` +
      `<path d="M70.4,51.5 C64,59.5 64,70.5 70.4,78.5" stroke-width="1.1" stroke-dasharray="1.5 2.3" opacity=".9"/>` +
    `</g>` +
    `<ellipse cx="64.8" cy="58.4" rx="4.3" ry="2.3" transform="rotate(-38 64.8 58.4)" fill="#fff" opacity=".24"/>`
  );
}

const defs = `<defs>
  <linearGradient id="bcl-bg" x1="8" y1="4" x2="100" y2="104" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="${C.navy1}"/><stop offset="1" stop-color="${C.navy2}"/></linearGradient>
  <radialGradient id="bcl-glow" cx="54" cy="58" r="50" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="${C.teal}" stop-opacity=".20"/><stop offset="1" stop-color="${C.teal}" stop-opacity="0"/></radialGradient>
  <linearGradient id="bcl-gold" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${C.gold1}"/><stop offset="1" stop-color="${C.gold2}"/></linearGradient>
  <linearGradient id="bcl-red" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${C.red1}"/><stop offset="1" stop-color="${C.red2}"/></linearGradient>
</defs>`;

const head = (w, h, vb) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${vb}" width="${w}" height="${h}" role="img" aria-label="BCL Fantasy">`;
const tile = (rx) => `<rect width="108" height="108" rx="${rx}" fill="url(#bcl-bg)"/><rect width="108" height="108" rx="${rx}" fill="url(#bcl-glow)"/>` +
  (rx ? `<rect x=".75" y=".75" width="106.5" height="106.5" rx="${rx - .75}" fill="none" stroke="#fff" stroke-opacity=".12" stroke-width="1.5"/>` : '');

const write = (name, body) => { fs.writeFileSync(path.join(OUT, name), body + '\n'); console.log('wrote', name, body.length, 'bytes'); };

// app / browser-tab / home-screen icon (rounded tile) and the full-bleed square (Apple touch icon, maskable)
write('icon.svg', head(512, 512, '0 0 108 108') + defs + tile(24) + mark(1.16) + '</svg>');
write('icon-square.svg', head(512, 512, '0 0 108 108') + defs + tile(0) + mark(1.0) + '</svg>');
// the mark alone on a transparent background (for use on any dark surface)
write('mark.svg', head(512, 512, '10 20 88 78') + defs + mark(1) + '</svg>');

// ---- horizontal lock-up for documents / sharing: tile + monoline "BCL" + FANTASY
const L = (t) => `<g transform="translate(${t.x} ${t.y})" fill="none" stroke="#F2F7FC" stroke-width="7.2" stroke-linecap="round" stroke-linejoin="round">`;
const B = L({ x: 134, y: 28 }) + `<path d="M3.6,3.6 V34.4 M3.6,3.6 H15 a8.4,8.4 0 0 1 0,16.8 H3.6 M3.6,20.4 H16.5 a7.2,7.2 0 0 1 0,14 H3.6"/></g>`;
const Cc = L({ x: 168, y: 28 }) + `<path d="M31,9 A17.2,17.2 0 1 0 31,29"/></g>`;
const Ll = L({ x: 210, y: 28 }) + `<path d="M3.6,3.6 V34.4 H23"/></g>`;
write('logo-lockup.svg',
  head(300, 108, '0 0 300 108') + defs + `<rect width="300" height="108" fill="none"/>` +
  `<g>${tile(24)}${mark(1.16)}</g>` + B + Cc + Ll +
  `<text x="136" y="88" fill="${C.gold2}" font-family="'Segoe UI', Inter, Roboto, Helvetica, Arial, sans-serif" font-weight="700" font-size="13" letter-spacing="6.2">FANTASY</text></svg>`);

// (the Android launcher drawables are generated by tools/gen_android_icon.js: they need their own composition for the adaptive-icon safe zone)
