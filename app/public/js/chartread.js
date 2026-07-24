/**
 * RCChartRead — self-contained chart analytics + SVG candle chart.
 *
 * Pure OHLCV math, zero dependencies, shared by the Arena position expander
 * and the dashboard symbol modal. The formulas MIRROR the engine's own rules
 * so the website never invents a different read:
 *  - session VWAP: anchored at the current UTC day's first bar,
 *    VWAP = Σ(typical·vol)/Σvol, typical = (H+L+C)/3; bands = center ± k·RMS
 *    of the last-20 typical prices about the center (engine analyzer rules).
 *  - market structure: 5-bar strict fractal swings; BOS = last close beyond
 *    the last swing high ×1.001 / low ×0.999; CHoCH = the swing sequence
 *    (HH/HL vs LH/LL) flipping against the previous structure (engine
 *    multi_timeframe rules).
 * Chart/candle patterns (Elliott, Wyckoff, doji, …) are NOT recomputed here —
 * they come from the engine's own /api/patterns read, one source of truth.
 *
 * Candle input: Bitget v2 rows [ts_ms, open, high, low, close, baseVol, ...]
 * (strings), oldest → newest — exactly what /api/market/candles returns.
 */
(function () {
  'use strict';

  function parseCandles(rows) {
    var out = [];
    for (var i = 0; i < (rows || []).length; i++) {
      var r = rows[i];
      if (!r || r.length < 5) continue;
      var c = { t: Number(r[0]), o: parseFloat(r[1]), h: parseFloat(r[2]), l: parseFloat(r[3]), c: parseFloat(r[4]), v: parseFloat(r[5]) || 0 };
      if (isFinite(c.o) && isFinite(c.h) && isFinite(c.l) && isFinite(c.c)) out.push(c);
    }
    out.sort(function (a, b) { return a.t - b.t; });
    return out;
  }

  // Session VWAP + bands. Anchor: first bar of the LAST bar's UTC day (falls
  // back to the whole window when the day has under 3 bars — a fresh UTC day
  // gives the average nothing to say yet).
  function vwap(candles) {
    if (!candles || candles.length < 5) return null;
    var last = candles[candles.length - 1];
    var day = Math.floor(last.t / 86400000);
    var start = 0;
    for (var i = candles.length - 1; i >= 0; i--) {
      if (Math.floor(candles[i].t / 86400000) !== day) { start = i + 1; break; }
    }
    if (candles.length - start < 3) start = 0;
    var pv = 0, vol = 0, series = [];
    for (var j = start; j < candles.length; j++) {
      var b = candles[j];
      var typical = (b.h + b.l + b.c) / 3;
      pv += typical * (b.v || 1);
      vol += (b.v || 1);
      series.push({ t: b.t, v: pv / vol });
    }
    var center = series[series.length - 1].v;
    // RMS dispersion of the last-20 typical prices about the center.
    var tail = candles.slice(-20);
    var ss = 0;
    for (var k = 0; k < tail.length; k++) {
      var tp = (tail[k].h + tail[k].l + tail[k].c) / 3;
      ss += (tp - center) * (tp - center);
    }
    var dev = Math.sqrt(ss / tail.length);
    return {
      value: center, series: series, anchor_index: start,
      upper1: center + dev, lower1: center - dev,
      upper2: center + 2 * dev, lower2: center - 2 * dev,
      dist_pct: center > 0 ? ((last.c - center) / center) * 100 : 0,
    };
  }

  // 5-bar strict fractal swings (engine _find_swings): a swing high must top
  // every high within `lookback` bars on both sides. Returns the last 4 each.
  function findSwings(candles, lookback) {
    var lb = lookback || 5;
    var highs = [], lows = [];
    for (var i = lb; i < candles.length - lb; i++) {
      var isH = true, isL = true;
      for (var d = 1; d <= lb; d++) {
        if (candles[i].h <= candles[i - d].h || candles[i].h <= candles[i + d].h) isH = false;
        if (candles[i].l >= candles[i - d].l || candles[i].l >= candles[i + d].l) isL = false;
        if (!isH && !isL) break;
      }
      if (isH) highs.push({ i: i, p: candles[i].h });
      if (isL) lows.push({ i: i, p: candles[i].l });
    }
    return { highs: highs.slice(-4), lows: lows.slice(-4) };
  }

  function classify(hs, ls) {
    if (hs.length < 2 || ls.length < 2) return 'ranging';
    var hh = hs[hs.length - 1].p > hs[hs.length - 2].p;
    var hl = ls[ls.length - 1].p > ls[ls.length - 2].p;
    if (hh && hl) return 'bullish';
    if (!hh && !hl) return 'bearish';
    return 'ranging';
  }

  // Engine multi_timeframe rules: BOS on a close beyond the last swing ±0.1%;
  // CHoCH when the structure classification flips vs the previous swings.
  function structure(candles) {
    if (!candles || candles.length < 15) return null;
    var sw = findSwings(candles, 5);
    var hs = sw.highs, ls = sw.lows;
    var out = { structure: classify(hs, ls), bos: false, bos_dir: 0, choch: false, choch_dir: 0, swings: sw };
    var close = candles[candles.length - 1].c;
    if (hs.length && close > hs[hs.length - 1].p * 1.001) { out.bos = true; out.bos_dir = 1; }
    else if (ls.length && close < ls[ls.length - 1].p * 0.999) { out.bos = true; out.bos_dir = -1; }
    if (hs.length >= 3 && ls.length >= 3) {
      var prev = classify(hs.slice(0, -1), ls.slice(0, -1));
      if (prev === 'bullish' && out.structure === 'bearish') { out.choch = true; out.choch_dir = -1; }
      if (prev === 'bearish' && out.structure === 'bullish') { out.choch = true; out.choch_dir = 1; }
    }
    return out;
  }

  function fmt(n) {
    if (!isFinite(n)) return '';
    return n >= 1000 ? n.toFixed(1) : n >= 1 ? n.toFixed(3) : n.toPrecision(4);
  }

  /**
   * SVG candle chart with the position's own geometry drawn on it.
   * opts: { width, height, entry, sl, tp, liq, direction, vwap: true, structure: true }
   * Static drawing — no animation, so prefers-reduced-motion needs no branch.
   */
  function svgChart(candles, opts) {
    opts = opts || {};
    if (!candles || candles.length < 5) return '';
    var W = opts.width || 640, H = opts.height || 220, PAD = 6, AXIS = 54;
    var vw = opts.vwap === false ? null : vwap(candles);
    var st = opts.structure === false ? null : structure(candles);
    var lo = Infinity, hi = -Infinity;
    for (var i = 0; i < candles.length; i++) {
      if (candles[i].l < lo) lo = candles[i].l;
      if (candles[i].h > hi) hi = candles[i].h;
    }
    // Include the position's own levels when they sit near the price action
    // (a far-away liq price must not squash the candles flat).
    var span0 = hi - lo || 1;
    ['entry', 'sl', 'tp', 'liq'].forEach(function (k) {
      var p = Number(opts[k]);
      if (p > 0 && p > lo - span0 * 0.6 && p < hi + span0 * 0.6) {
        if (p < lo) lo = p;
        if (p > hi) hi = p;
      }
    });
    var span = (hi - lo) || 1;
    lo -= span * 0.04; hi += span * 0.04; span = hi - lo;
    var iw = W - AXIS - PAD;
    var step = iw / candles.length;
    var cw = Math.max(1.5, step * 0.62);
    function X(i) { return PAD + i * step + step / 2; }
    function Y(p) { return PAD + (1 - (p - lo) / span) * (H - PAD * 2); }
    var s = '<svg class="rc-chart" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Price chart with position levels" preserveAspectRatio="none">';
    // VWAP ±1σ band first (underneath everything)
    if (vw) {
      s += '<rect x="' + PAD + '" y="' + Y(vw.upper1).toFixed(1) + '" width="' + iw + '" height="'
        + Math.max(0, Y(vw.lower1) - Y(vw.upper1)).toFixed(1) + '" fill="rgba(230,176,60,.07)"/>';
      var pts = [];
      for (var vi = 0; vi < vw.series.length; vi++) {
        pts.push(X(vw.anchor_index + vi).toFixed(1) + ',' + Y(vw.series[vi].v).toFixed(1));
      }
      s += '<polyline points="' + pts.join(' ') + '" fill="none" stroke="#e6b03c" stroke-width="1.2" stroke-dasharray="5 3" opacity=".85"/>';
    }
    // Swing levels (structure context)
    if (st && st.swings) {
      st.swings.highs.slice(-2).forEach(function (sw2) {
        s += '<line x1="' + X(sw2.i).toFixed(1) + '" y1="' + Y(sw2.p).toFixed(1) + '" x2="' + (PAD + iw) + '" y2="' + Y(sw2.p).toFixed(1) + '" stroke="rgba(224,82,82,.45)" stroke-width="1" stroke-dasharray="2 4"/>';
      });
      st.swings.lows.slice(-2).forEach(function (sw3) {
        s += '<line x1="' + X(sw3.i).toFixed(1) + '" y1="' + Y(sw3.p).toFixed(1) + '" x2="' + (PAD + iw) + '" y2="' + Y(sw3.p).toFixed(1) + '" stroke="rgba(47,191,113,.45)" stroke-width="1" stroke-dasharray="2 4"/>';
      });
    }
    // Candles
    for (var ci = 0; ci < candles.length; ci++) {
      var b = candles[ci];
      var up = b.c >= b.o;
      var col = up ? '#2fbf71' : '#e05252';
      var x = X(ci);
      s += '<line x1="' + x.toFixed(1) + '" y1="' + Y(b.h).toFixed(1) + '" x2="' + x.toFixed(1) + '" y2="' + Y(b.l).toFixed(1) + '" stroke="' + col + '" stroke-width="1"/>';
      var yTop = Y(Math.max(b.o, b.c)), yBot = Y(Math.min(b.o, b.c));
      s += '<rect x="' + (x - cw / 2).toFixed(1) + '" y="' + yTop.toFixed(1) + '" width="' + cw.toFixed(1) + '" height="' + Math.max(0.8, yBot - yTop).toFixed(1) + '" fill="' + col + '"/>';
    }
    // Position geometry: entry solid, TP/SL dashed, liq dotted — each labeled.
    function level(price, color, dash, label) {
      var p = Number(price);
      if (!(p > 0) || p < lo || p > hi) return;
      var y = Y(p).toFixed(1);
      s += '<line x1="' + PAD + '" y1="' + y + '" x2="' + (PAD + iw) + '" y2="' + y + '" stroke="' + color + '" stroke-width="1.2"' + (dash ? ' stroke-dasharray="' + dash + '"' : '') + '/>';
      s += '<text x="' + (PAD + iw + 3) + '" y="' + (Number(y) + 3.5) + '" font-size="9.5" font-family="monospace" fill="' + color + '">' + label + ' ' + fmt(p) + '</text>';
    }
    level(opts.entry, '#e6b03c', '', 'entry');
    level(opts.tp, '#2fbf71', '6 4', 'tp');
    level(opts.sl, '#e05252', '6 4', 'sl');
    level(opts.liq, '#a33', '2 3', 'liq');
    // Structure tag (top-left) — text only when there is something to say.
    if (st) {
      var tag = st.structure.toUpperCase();
      if (st.bos) tag += ' · BOS' + (st.bos_dir > 0 ? '↑' : '↓');
      if (st.choch) tag += ' · CHoCH' + (st.choch_dir > 0 ? '↑' : '↓');
      s += '<text x="' + (PAD + 2) + '" y="' + (PAD + 10) + '" font-size="10" font-family="monospace" fill="rgba(200,205,215,.85)">' + tag + '</text>';
    }
    s += '</svg>';
    return s;
  }

  var api = { parseCandles: parseCandles, vwap: vwap, structure: structure, findSwings: findSwings, svgChart: svgChart };
  if (typeof window !== 'undefined') window.RCChartRead = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})();
