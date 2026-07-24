'use strict';
/**
 * RCChartRead — the shared client chart-analytics library. The formulas
 * MIRROR the engine's own rules, so these tests pin the same invariants the
 * engine's tests pin: session-anchored VWAP = Σ(typical·vol)/Σvol with
 * ±k·RMS bands; 5-bar strict fractal swings; BOS = close beyond the last
 * swing ±0.1%; CHoCH = the HH/HL↔LH/LL sequence flipping. The module is a
 * browser IIFE with a module.exports branch, so the MATH is tested directly.
 */
const test = require('node:test');
const assert = require('node:assert');
const CR = require('../public/js/chartread');

const near = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) < eps, `${a} ≈ ${b}`);
const H = 3600000;
const DAY0 = 1784900000000 - (1784900000000 % 86400000);   // a UTC midnight

// Piecewise-linear price path through [barIndex, price] waypoints → candles
// with tiny wicks, so every waypoint is a strict fractal extreme.
function path(waypoints, t0) {
  const out = [];
  for (let w = 0; w < waypoints.length - 1; w++) {
    const [i0, p0] = waypoints[w], [i1, p1] = waypoints[w + 1];
    for (let i = i0; i < i1; i++) {
      const p = p0 + (p1 - p0) * ((i - i0) / (i1 - i0));
      out.push({ t: (t0 || DAY0) + i * H, o: p, h: p + 0.5, l: p - 0.5, c: p, v: 1 });
    }
  }
  const [iL, pL] = waypoints[waypoints.length - 1];
  out.push({ t: (t0 || DAY0) + iL * H, o: pL, h: pL + 0.5, l: pL - 0.5, c: pL, v: 1 });
  return out;
}

test('parseCandles: Bitget string rows → sorted numeric candles, junk dropped', () => {
  const rows = [
    ['1784910000000', '100', '101', '99', '100.5', '3'],
    ['1784906400000', '99', '100', '98', '99.5', '2'],
    ['bad'],
    ['1784913600000', 'x', '1', '1', '1', '1'],           // NaN open dropped
  ];
  const c = CR.parseCandles(rows);
  assert.equal(c.length, 2);
  assert.ok(c[0].t < c[1].t, 'sorted ascending');
  assert.equal(c[1].c, 100.5);
  assert.equal(c[1].v, 3);
});

test('vwap: exact Σ(typical·vol)/Σvol with symmetric RMS bands', () => {
  // Same UTC day, hand-computable: typicals 100, 110, 120 with vols 1, 2, 1.
  const candles = [
    { t: DAY0 + 1 * H, o: 100, h: 101, l: 99, c: 100, v: 1 },   // typical 100
    { t: DAY0 + 2 * H, o: 110, h: 111, l: 109, c: 110, v: 2 },  // typical 110
    { t: DAY0 + 3 * H, o: 120, h: 121, l: 119, c: 120, v: 1 },  // typical 120
    { t: DAY0 + 4 * H, o: 120, h: 121, l: 119, c: 120, v: 0 },  // vol 0 → weight 1
    { t: DAY0 + 5 * H, o: 120, h: 121, l: 119, c: 120, v: 1 },
  ];
  const vw = CR.vwap(candles);
  // Σ tp·v = 100 + 220 + 120 + 120 + 120 = 680 ; Σ v = 6
  near(vw.value, 680 / 6);
  near(vw.upper1 - vw.value, vw.value - vw.lower1);            // symmetric
  near(vw.upper2 - vw.value, 2 * (vw.upper1 - vw.value));      // 2σ = 2×1σ
  assert.ok(vw.dist_pct > 0, 'last close above vwap → positive distance');
});

test('vwap: anchors at the current UTC day, ignoring yesterday', () => {
  const yesterday = { t: DAY0 - 2 * H, o: 500, h: 501, l: 499, c: 500, v: 100 };
  const today = [];
  for (let i = 0; i < 6; i++) today.push({ t: DAY0 + i * H, o: 100, h: 101, l: 99, c: 100, v: 1 });
  const vw = CR.vwap([yesterday, ...today]);
  assert.equal(vw.anchor_index, 1, 'anchor at the first bar of the last UTC day');
  near(vw.value, 100, 1e-6);   // yesterday's 500-print never pollutes the session
});

test('findSwings: 5-bar strict fractal finds the pyramid peak and trough', () => {
  const candles = path([[0, 100], [7, 90], [14, 110], [21, 100]]);
  const sw = CR.findSwings(candles, 5);
  assert.equal(sw.lows.length, 1);
  assert.equal(sw.lows[0].i, 7);
  assert.equal(sw.highs.length, 1);
  assert.equal(sw.highs[0].i, 14);
});

test('structure: HH+HL classifies bullish; a break beyond +0.1% is a BOS↑', () => {
  // lows 90@5, 95@19 (HL); highs 110@12, 115@26 (HH); tail breaks above.
  const candles = path([[0, 100], [5, 90], [12, 110], [19, 95], [26, 115], [33, 108], [40, 117]]);
  const st = CR.structure(candles);
  assert.equal(st.structure, 'bullish');
  assert.equal(st.bos, true);
  assert.equal(st.bos_dir, 1);   // close 117 > 115 × 1.001
});

test('structure: a close INSIDE the 0.1% threshold is not a BOS', () => {
  // Pull back after the 115 swing (so it stays a fractal), then finish just
  // inside the threshold: 114.9 < 115 × 1.001 = 115.115.
  const candles = path([[0, 100], [5, 90], [12, 110], [19, 95], [26, 115], [32, 108], [38, 114.9]]);
  const st = CR.structure(candles);
  assert.equal(st.bos, false, '114.9 < 115×1.001 = 115.115 — no break');
});

test('structure: LH+LL classifies bearish', () => {
  const candles = path([[0, 100], [5, 110], [12, 90], [19, 105], [26, 85], [33, 95]]);
  const st = CR.structure(candles);
  assert.equal(st.structure, 'bearish');
});

test('structure: a bullish→bearish flip with 3 swings per side is a CHoCH↓', () => {
  // highs 110, 115(HH), 112(LH) · lows 90, 95(HL), 93(LL): prev bullish → now bearish.
  const candles = path([[0, 100], [5, 90], [12, 110], [19, 95], [26, 115], [33, 93], [40, 112], [47, 100]]);
  const st = CR.structure(candles);
  assert.equal(st.structure, 'bearish');
  assert.equal(st.choch, true);
  assert.equal(st.choch_dir, -1);
});

test('svgChart: draws candles, position levels in range, and the structure tag', () => {
  const candles = path([[0, 100], [5, 90], [12, 110], [19, 95], [26, 115], [33, 108], [40, 117]]);
  const svg = CR.svgChart(candles, { entry: 100, tp: 118, sl: 92, liq: 5 });
  assert.match(svg, /^<svg class="rc-chart"/);
  assert.match(svg, /<rect/);                       // candle bodies
  assert.match(svg, /entry 100/);
  assert.match(svg, /tp 118/);
  assert.match(svg, /sl 92\./);
  assert.ok(!/liq 5(?![0-9])/.test(svg), 'far-away liq price must not squash the chart');
  assert.match(svg, /BULLISH · BOS↑/);              // structure tag
  assert.match(svg, /polyline/);                    // vwap line
  assert.ok(svg.indexOf('aria-label') > 0, 'chart is labeled for screen readers');
});

test('elliottWavePoints: only engine-emitted wave prices, only Elliott patterns', () => {
  const pts = CR.elliottWavePoints({ name: 'Elliott 5-Wave Impulse',
    key_levels: { w1_start: 90, w1_top: 110, w2_low: 95, w3_top: 130, w4_low: 118, w5_top: 140, w3_fib: 1.62 } });
  const labels = pts.map(p => p.label).join('');
  assert.equal(labels, '12345');
  assert.equal(pts.find(p => p.label === '3').price, 130);
  // Non-Elliott patterns and missing key_levels yield nothing.
  assert.deepEqual(CR.elliottWavePoints({ name: 'Double Top', key_levels: { top1: 1 } }), []);
  assert.deepEqual(CR.elliottWavePoints(null), []);
});

test('matchWaveBars: labels land on the bar where the extreme printed — or not at all', () => {
  const candles = path([[0, 100], [7, 90], [14, 110], [21, 100]]);
  const hits = CR.matchWaveBars(candles, [
    { label: '2', price: 89.5 },     // == the low wick at bar 7 (90 - 0.5)
    { label: '3', price: 110.5 },    // == the high wick at bar 14
    { label: '5', price: 500 },      // nowhere in the window → skipped
  ]);
  assert.deepEqual(hits.map(h => [h.label, h.i]), [['2', 7], ['3', 14]]);
});

test('svgChart: engine levels, FVG zones, and wave labels render when supplied', () => {
  const candles = path([[0, 100], [7, 90], [14, 110], [21, 100]]);
  const svg = CR.svgChart(candles, {
    levels: [{ price: 105, kind: 'poc', score: 3 }, { price: 9999, kind: 'far', score: 9 }],
    fvgs: [{ kind: 'bullish', top: 103, bottom: 101, filled: false }],
    waves: [{ label: '3', price: 110.5 }],
  });
  assert.match(svg, />poc</);                        // in-range level labeled
  assert.ok(svg.indexOf('far') < 0, 'out-of-range level not drawn');
  assert.match(svg, /rgba\(47,191,113,\.10\)/);      // unfilled bull FVG tint
  assert.match(svg, /<circle[^>]+stroke="#e6b03c"/); // wave badge
  assert.match(svg, /text-anchor="middle">3</);
});

test('svgChart and analytics degrade to empty/null on thin data — never invent', () => {
  assert.equal(CR.svgChart([], {}), '');
  assert.equal(CR.vwap([]), null);
  assert.equal(CR.structure(path([[0, 100], [4, 101]])), null);
});
