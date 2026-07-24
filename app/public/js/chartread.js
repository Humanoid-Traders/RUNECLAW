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

  // Session VWAP + bands — the ENGINE's exact semantics (analyzer
  // _session_anchor_index/_session_vwap): anchor at the first bar of the
  // LAST bar's UTC day, however few bars that is; zero-volume bars weigh
  // NOTHING; a zero-volume session falls back to the full-window VWAP
  // (what the engine's caller keeps when _session_vwap returns None).
  function vwap(candles) {
    if (!candles || candles.length < 5) return null;
    var last = candles[candles.length - 1];
    var day = Math.floor(last.t / 86400000);
    var start = candles.length - 1;
    while (start > 0 && Math.floor(candles[start - 1].t / 86400000) === day) start--;
    function accum(from) {
      var pv = 0, vol = 0, series = [];
      for (var j = from; j < candles.length; j++) {
        var b = candles[j];
        var typical = (b.h + b.l + b.c) / 3;
        var w = b.v > 0 ? b.v : 0;
        pv += typical * w; vol += w;
        series.push({ t: b.t, v: vol > 0 ? pv / vol : null });
      }
      return { pv: pv, vol: vol, series: series };
    }
    var seg = accum(start), anchor = start;
    if (!(seg.vol > 0)) { seg = accum(0); anchor = 0; }
    if (!(seg.vol > 0)) return null;
    // Leading null points (bars before the first traded volume) carry no
    // average yet — drop them and shift the plot anchor accordingly.
    var series = [];
    for (var si = 0; si < seg.series.length; si++) {
      if (seg.series[si].v != null) series.push(seg.series[si]);
    }
    var center = seg.pv / seg.vol;
    // RMS dispersion of the last-20 typical prices about the center.
    var tail = candles.slice(-20);
    var ss = 0;
    for (var k = 0; k < tail.length; k++) {
      var tp = (tail[k].h + tail[k].l + tail[k].c) / 3;
      ss += (tp - center) * (tp - center);
    }
    var dev = Math.sqrt(ss / tail.length);
    return {
      value: center, series: series,
      anchor_index: anchor + (seg.series.length - series.length),
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

  // Wilder-style ATR (engine elliott._atr): simple mean of the true range
  // over the last `period` bars.
  function atrOf(candles, period) {
    var n = candles.length;
    if (n < 2) return 0;
    var p = Math.max(1, Math.min(period || 14, n - 1));
    var sum = 0;
    for (var k = 0; k < p; k++) {
      var i = n - p + k;
      var prevC = candles[i - 1].c;
      sum += Math.max(candles[i].h - candles[i].l,
        Math.abs(candles[i].h - prevC), Math.abs(candles[i].l - prevC));
    }
    return sum / p;
  }

  // ATR-ZigZag pivots (engine elliott.atr_zigzag_pivots, atr_mult 1.5): a
  // pivot registers only once price reverses by >= 1.5×ATR14 from the
  // running extreme. This is the swing source the engine's structure read
  // uses BY DEFAULT (structure_zigzag_enabled ships ON); the 5-bar fractal
  // is only its starved-window fallback — mirrored below the same way.
  function zigzagSwings(candles, atrMult) {
    var n = candles.length;
    if (n < 3) return { highs: [], lows: [] };
    var threshold = (atrMult || 1.5) * atrOf(candles, 14);
    if (!(threshold > 0)) return { highs: [], lows: [] };
    var pivots = [];
    var lastIdx = 0, lastHigh = candles[0].h, lastLow = candles[0].l, dir = 0;
    for (var i = 1; i < n; i++) {
      var hi = candles[i].h, lo = candles[i].l;
      if (dir >= 0) {
        if (hi > lastHigh) { lastHigh = hi; lastIdx = i; }
        if (lastHigh - lo >= threshold) {
          pivots.push({ i: lastIdx, p: lastHigh, k: 'H' });
          dir = -1; lastLow = lo; lastIdx = i;
        }
      }
      if (dir <= 0) {
        if (lo < lastLow) { lastLow = lo; lastIdx = i; }
        if (hi - lastLow >= threshold) {
          pivots.push({ i: lastIdx, p: lastLow, k: 'L' });
          dir = 1; lastHigh = hi; lastIdx = i;
        }
      }
    }
    var highs = [], lows = [];
    for (var pi = 0; pi < pivots.length; pi++) {
      (pivots[pi].k === 'H' ? highs : lows).push({ i: pivots[pi].i, p: pivots[pi].p });
    }
    return { highs: highs.slice(-8), lows: lows.slice(-8) };
  }

  // Market structure — the ENGINE's _analyze_structure, rule for rule:
  // ZigZag swings first (fractal fallback when <2 per side); <2 per side →
  // ranging with NO BOS/CHoCH; STRICT HH+HL / LH+LL (equal swings rank as
  // ranging, never bearish); BOS beyond the last swing ±0.1%; CHoCH via the
  // 3-swing flip, or the 2-swing branch (current swings opposing bos_dir).
  function structure(candles) {
    if (!candles || candles.length < 15) return null;
    var sw = zigzagSwings(candles, 1.5);
    if (sw.highs.length < 2 || sw.lows.length < 2) sw = findSwings(candles, 5);
    var hs = sw.highs, ls = sw.lows;
    var out = { structure: 'ranging', bos: false, bos_dir: 0, choch: false, choch_dir: 0, swings: { highs: hs, lows: ls } };
    if (hs.length < 2 || ls.length < 2) return out;
    var hh = hs[hs.length - 1].p > hs[hs.length - 2].p;
    var hl = ls[ls.length - 1].p > ls[ls.length - 2].p;
    var lh = hs[hs.length - 1].p < hs[hs.length - 2].p;
    var ll = ls[ls.length - 1].p < ls[ls.length - 2].p;
    if (hh && hl) out.structure = 'bullish';
    else if (lh && ll) out.structure = 'bearish';
    var close = candles[candles.length - 1].c;
    if (close > hs[hs.length - 1].p * 1.001) { out.bos = true; out.bos_dir = 1; }
    else if (close < ls[ls.length - 1].p * 0.999) { out.bos = true; out.bos_dir = -1; }
    if (hs.length >= 3 && ls.length >= 3) {
      var prevBull = hs[hs.length - 3].p < hs[hs.length - 2].p && ls[ls.length - 3].p < ls[ls.length - 2].p;
      var prevBear = hs[hs.length - 3].p > hs[hs.length - 2].p && ls[ls.length - 3].p > ls[ls.length - 2].p;
      if (prevBull && lh && ll) { out.choch = true; out.choch_dir = -1; }
      else if (prevBear && hh && hl) { out.choch = true; out.choch_dir = 1; }
    } else if (out.bos && out.bos_dir > 0 && lh && ll) {
      out.choch = true; out.choch_dir = -1;
    } else if (out.bos && out.bos_dir < 0 && hh && hl) {
      out.choch = true; out.choch_dir = 1;
    }
    return out;
  }

  function fmt(n) {
    if (!isFinite(n)) return '';
    return n >= 1000 ? n.toFixed(1) : n >= 1 ? n.toFixed(3) : n.toPrecision(4);
  }

  // Elliott wave points from the ENGINE'S pattern key_levels (chart_patterns
  // entries whose name starts with "Elliott"). Only price points the engine
  // itself emitted — nothing is re-derived here.
  var WAVE_KEYS = [
    ['w1_top', '1'], ['w1_low', '1'], ['w2_low', '2'], ['w2_high', '2'],
    ['w3_top', '3'], ['w3_low', '3'], ['w4_low', '4'], ['w4_high', '4'],
    ['w5_top', '5'], ['w5_low', '5'],
    ['a_end', 'A'], ['b_end', 'B'], ['c_end', 'C'],
    ['w_end', 'W'], ['x_end', 'X'], ['y_end', 'Y'],
  ];
  function elliottWavePoints(pattern) {
    var kl = pattern && pattern.key_levels;
    if (!kl || !/^Elliott/i.test(String(pattern.name || ''))) return [];
    var out = [];
    for (var i = 0; i < WAVE_KEYS.length; i++) {
      var p = Number(kl[WAVE_KEYS[i][0]]);
      if (p > 0) out.push({ label: WAVE_KEYS[i][1], price: p });
    }
    return out;
  }

  // Match wave prices to the bars where those extremes actually printed
  // (within 0.15% of a high/low). A wave outside the visible window gets NO
  // label — placement is honest or absent, never guessed.
  function matchWaveBars(candles, points) {
    var out = [];
    for (var i = 0; i < (points || []).length; i++) {
      var pt = points[i], best = -1;
      for (var j = candles.length - 1; j >= 0; j--) {
        var b = candles[j];
        if (Math.abs(b.h - pt.price) / pt.price < 0.0015
          || Math.abs(b.l - pt.price) / pt.price < 0.0015) { best = j; break; }
      }
      if (best >= 0) out.push({ label: pt.label, price: pt.price, i: best });
    }
    return out;
  }

  /**
   * SVG candle chart with the position's own geometry drawn on it.
   * opts: { width, height, entry, sl, tp, liq, direction, vwap: true,
   *   structure: true,
   *   levels: [{price, kind, score}]     — engine S/R (top 5 by score drawn),
   *   fvgs: [{kind, top, bottom, filled}]— engine fair-value gaps (zones),
   *   waves: [{label, price}]            — engine Elliott points (labeled at
   *                                        the bar where the extreme printed) }
   * Static drawing — no animation, so prefers-reduced-motion needs no branch.
   */
  function svgChart(candles, opts) {
    opts = opts || {};
    if (!candles || candles.length < 5) return '';
    // Callers pass the mount's real clientWidth so 1 SVG unit ≈ 1 CSS px and
    // the label text renders at true, legible size on every screen.
    var W = Math.max(300, opts.width || 640), H = opts.height || 220, PAD = 6;
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
    ['entry', 'exit', 'sl', 'tp', 'liq'].forEach(function (k) {
      var p = Number(opts[k]);
      if (p > 0 && p > lo - span0 * 0.6 && p < hi + span0 * 0.6) {
        if (p < lo) lo = p;
        if (p > hi) hi = p;
      }
    });
    var span = (hi - lo) || 1;
    lo -= span * 0.04; hi += span * 0.04; span = hi - lo;
    // Right axis sized to the longest label ("entry 63939.7") so 4+ digit
    // prices are never clipped: ~5.8px/char at font 9.5 monospace + margin.
    var AXIS = Math.max(58, (6 + fmt(hi).length) * 5.8 + 8);
    var iw = W - AXIS - PAD;
    var step = iw / candles.length;
    var cw = Math.max(1.5, step * 0.62);
    function X(i) { return PAD + i * step + step / 2; }
    function Y(p) { return PAD + (1 - (p - lo) / span) * (H - PAD * 2); }
    var s = '<svg class="rc-chart" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Price chart with position levels">';
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
    // Engine FVG zones — unfilled gaps as tinted bands (filled drawn fainter).
    (opts.fvgs || []).slice(0, 4).forEach(function (g) {
      var top = Number(g.top), bot = Number(g.bottom);
      if (!(top > 0) || !(bot > 0) || top < lo || bot > hi) return;
      var bull = String(g.kind || '').indexOf('bear') < 0;
      s += '<rect x="' + PAD + '" y="' + Y(top).toFixed(1) + '" width="' + iw + '" height="'
        + Math.max(1, Y(bot) - Y(top)).toFixed(1) + '" fill="' + (bull ? 'rgba(47,191,113,' : 'rgba(224,82,82,')
        + (g.filled ? '.05' : '.10') + ')"/>';
    });
    // Engine S/R levels — top 5 by score inside the window, labeled by kind.
    var lv = (opts.levels || []).filter(function (l) {
      var p = Number(l.price); return p > lo && p < hi;
    }).sort(function (a, b) { return (Number(b.score) || 0) - (Number(a.score) || 0); }).slice(0, 5);
    lv.forEach(function (l) {
      var y = Y(Number(l.price)).toFixed(1);
      s += '<line x1="' + PAD + '" y1="' + y + '" x2="' + (PAD + iw) + '" y2="' + y
        + '" stroke="rgba(120,150,220,.5)" stroke-width="1" stroke-dasharray="8 5"/>';
      s += '<text x="' + (PAD + 2) + '" y="' + (Number(y) - 2) + '" font-size="8.5" font-family="monospace" fill="rgba(120,150,220,.8)">'
        + String(l.kind || '').replace(/[^a-z0-9_]/gi, '') + '</text>';
    });
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
    level(opts.exit, '#3fb6ff', '', 'exit');
    level(opts.tp, '#2fbf71', '6 4', 'tp');
    level(opts.sl, '#e05252', '6 4', 'sl');
    level(opts.liq, '#a33', '2 3', 'liq');
    // Elliott wave labels — circled numbers at the bars where the engine's
    // wave extremes printed (matchWaveBars: honest placement or none).
    matchWaveBars(candles, opts.waves || []).forEach(function (w) {
      var wx = X(w.i).toFixed(1), wy = Y(w.price);
      var above = wy > (H / 2);
      var ly = above ? wy - 11 : wy + 11;
      s += '<circle cx="' + wx + '" cy="' + ly.toFixed(1) + '" r="7" fill="rgba(230,176,60,.14)" stroke="#e6b03c" stroke-width="1"/>';
      s += '<text x="' + wx + '" y="' + (ly + 3.2).toFixed(1) + '" font-size="9" font-family="monospace" fill="#e6b03c" text-anchor="middle">' + w.label + '</text>';
    });
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

  var api = { parseCandles: parseCandles, vwap: vwap, structure: structure, findSwings: findSwings, zigzagSwings: zigzagSwings, atrOf: atrOf, svgChart: svgChart, elliottWavePoints: elliottWavePoints, matchWaveBars: matchWaveBars };
  if (typeof window !== 'undefined') window.RCChartRead = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})();
