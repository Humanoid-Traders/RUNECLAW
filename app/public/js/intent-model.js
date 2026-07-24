/**
 * RUNECLAW — Intent Compiler model (Guardian).
 *
 * Turns a plain-language goal ("only majors, max 5% per trade, no shorts,
 * stop if down 8%") into a DETERMINISTIC, tighten-only Authority Envelope: a
 * set of typed rules, each tagged with WHO enforces it — the wallet, the risk
 * gate, or a human approval — so a natural-language wish becomes a machine-
 * checkable policy. This is the "Intent Compiler" leg of the Guardian principle:
 * the AI proposes, deterministic controls authorize, the wallet enforces.
 *
 * Compile only: it produces a policy PREVIEW. It binds nothing, signs nothing
 * and moves no funds (§4). It is also a public surface, so it never emits a
 * dollar figure — a dollar-denominated approval limit compiles to an approval
 * RULE whose exact figure is set privately in the app (§4: no $ on public
 * surfaces). Deterministic + pure so tests can assert the compiled rules.
 *
 * Dual export: browser (window.IntentModel) + Node (require) for unit tests.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.IntentModel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // Enforcement tiers — who actually stops a violating action.
  //   wallet   → a signable limit the wallet/session-key enforces before signing
  //   gate     → the risk gate checks it at decision time (pre-order)
  //   approval → a human must approve before it proceeds
  //   monitor  → a standing trigger the Sentinel/Escape agent watches
  const TIER = {
    wallet:   { label: 'wallet-enforced', rank: 1, note: 'the wallet blocks a signature that breaks this' },
    gate:     { label: 'gate-checked',    rank: 2, note: 'the risk gate rejects the order before it is placed' },
    approval: { label: 'needs approval',  rank: 3, note: 'a human must approve before it proceeds' },
    monitor:  { label: 'monitored',       rank: 4, note: 'a standing trigger watches for this and alerts / unwinds' },
  };

  // The protection axes we score coverage against — a good envelope caps size,
  // leverage, direction, loss and scope, and names an emergency trigger.
  const AXES = ['size', 'exposure', 'leverage', 'direction', 'loss', 'scope', 'approval', 'emergency'];

  function clampPct(n, lo, hi) {
    n = Math.round(Number(n));
    if (!isFinite(n)) return null;
    return Math.min(hi, Math.max(lo, n));
  }
  function firstPct(text, re) {
    const m = text.match(re);
    return m ? clampPct(m[1], 1, 100) : null;
  }

  const MAJORS = ['BTC', 'ETH', 'SOL', 'BNB', 'XRP'];

  // Each recognizer inspects the lowercased text and, on a hit, returns a rule.
  // Order matters only for display; compile() de-dupes by axis (first wins) so a
  // sentence that trips two size patterns yields one size rule.
  function recognize(text) {
    const t = ' ' + text.toLowerCase().replace(/\s+/g, ' ') + ' ';
    const rules = [];
    const add = (axis, tier, label, human, value) =>
      rules.push({ axis, tier, label, human, value: value == null ? null : value });

    // ---- Size: max per position ------------------------------------------
    var m = t.match(/(?:max|no more than|up to|cap(?:ped)?(?: at)?|limit(?:ed)? to)\s*\$?(\d{1,3}(?:\.\d+)?)\s*%\s*(?:per|a|each|in (?:one|a single|any))?\s*(?:trade|position|coin|token|asset|name|bet)/);
    if (!m) m = t.match(/(\d{1,3}(?:\.\d+)?)\s*%\s*(?:max )?(?:per|a|each)\s*(?:trade|position|coin|token|asset|name)/);
    if (m) add('size', 'wallet', 'Max size per position',
      'No single position may exceed ' + clampPct(m[1], 1, 100) + '% of the book.', clampPct(m[1], 1, 100));

    // ---- Exposure: total invested / keep cash ----------------------------
    m = t.match(/(?:total|overall|gross|net)?\s*exposure\s*(?:under|below|max|of|<=?)?\s*(\d{1,3})\s*%/)
      || t.match(/(?:keep|hold|stay)\s*(?:at least\s*)?(\d{1,3})\s*%\s*(?:in\s*)?(?:cash|stable|stables|dry)/)
      || t.match(/(?:max|no more than|up to)\s*(\d{1,3})\s*%\s*(?:invested|deployed|in the market|at risk)/);
    if (m) {
      var isCash = /cash|stable|dry/.test(m[0]);
      var pct = clampPct(m[1], 1, 100);
      var invested = isCash ? (100 - pct) : pct;
      add('exposure', 'wallet', 'Max total exposure',
        'Total deployed capital stays at or below ' + invested + '%' + (isCash ? ' (keep ' + pct + '% in reserve).' : '.'), invested);
    }

    // ---- Leverage --------------------------------------------------------
    if (/\bno leverage\b|\bunlevered\b|\bspot only\b|\bno margin\b/.test(t)) {
      add('leverage', 'wallet', 'No leverage', 'Leverage is disabled — spot / 1× only.', 1);
    } else {
      m = t.match(/(?:max|up to|no more than|cap(?:ped)?(?: at)?)\s*(\d{1,2}(?:\.\d)?)\s*x\b/) || t.match(/\b(\d{1,2})\s*x\s*(?:leverage|lev|max)/);
      if (m) {
        var lev = Math.min(100, Math.max(1, Number(m[1])));
        add('leverage', 'wallet', 'Max leverage', 'Leverage is capped at ' + lev + '×.', lev);
      }
    }

    // ---- Direction -------------------------------------------------------
    if (/\bno shorts?\b|\blong[ -]only\b|\bnever short\b|\bno shorting\b/.test(t)) {
      add('direction', 'gate', 'Long-only', 'Short positions are not allowed.', 'long_only');
    } else if (/\bno longs?\b|\bshort[ -]only\b/.test(t)) {
      add('direction', 'gate', 'Short-only', 'Long positions are not allowed.', 'short_only');
    }

    // ---- Loss controls: drawdown + daily loss ----------------------------
    m = t.match(/(?:stop|halt|flatten|exit|kill|pause)(?:\s+\w+){0,3}?\s*(?:if\s*)?(?:down|drawdown|dd|loss|below|at|-)?\s*(\d{1,2})\s*%/)
      || t.match(/(\d{1,2})\s*%\s*(?:draw ?down|dd|max loss|stop[- ]?loss)/);
    if (m && /(stop|halt|flatten|exit|kill|pause|draw ?down|dd|loss)/.test(m[0])) {
      var isDaily = /\b(day|daily|per day|a day|today)\b/.test(t) && /\b(day|daily|per day|a day|today)\b/.test(t.slice(Math.max(0, t.indexOf(m[0]) - 24), t.indexOf(m[0]) + m[0].length + 24));
      add(isDaily ? 'loss' : 'loss', 'gate', isDaily ? 'Daily loss stop' : 'Drawdown stop',
        (isDaily ? 'Trading halts for the day' : 'The book flattens / trading halts') + ' at ' + clampPct(m[1], 1, 100) + '% ' + (isDaily ? 'daily loss.' : 'drawdown.'),
        clampPct(m[1], 1, 100));
    }

    // ---- Min confidence --------------------------------------------------
    m = t.match(/(?:min(?:imum)?\s*)?confidence\s*(?:of|>=?|at least|above)?\s*(\d{1,3})\s*%/) || t.match(/(\d{1,3})\s*%\s*(?:min(?:imum)?\s*)?confidence/);
    if (m) add('gate_conf', 'gate', 'Minimum confidence',
      'Only act on signals at or above ' + clampPct(m[1], 1, 100) + '% confidence.', clampPct(m[1], 1, 100));
    else if (/\bhigh[- ]confidence\b|\bhigh conviction\b|\bstrong signals? only\b/.test(t))
      add('gate_conf', 'gate', 'High-confidence only', 'Only act on high-confidence signals.', 'high');

    // ---- Scope: asset universe -------------------------------------------
    if (/\bmajors? only\b|\bonly majors?\b|\bno alts?\b|\bno altcoins?\b|\bno memes?\b|\bblue[- ]?chips? only\b/.test(t)) {
      add('scope', 'gate', 'Majors only',
        'Trade only major assets (' + MAJORS.join(', ') + ') — no alts / memecoins.', MAJORS.slice());
    } else {
      // "only BTC and ETH", "trade only sol, avax"
      m = t.match(/\bonly\s+((?:[a-z]{2,6})(?:[ ,/&]+(?:and\s+)?[a-z]{2,6}){0,6})\b/);
      if (m && !/majors?|alts?|longs?|shorts?|high|the|cash|stable/.test(m[1])) {
        var syms = m[1].toUpperCase().split(/[ ,/&]+|and/i).map(function (s) { return s.trim(); }).filter(function (s) { return s.length >= 2 && s.length <= 6; });
        if (syms.length) add('scope', 'gate', 'Allow-list', 'Trade only: ' + syms.join(', ') + '.', syms);
      }
    }

    // ---- Protocol / venue restrictions -----------------------------------
    if (/\bno bridges?\b|\bnever bridge\b|\bno bridging\b|\bno cross[- ]?chain\b/.test(t))
      add('scope_venue', 'gate', 'No bridges', 'Bridging / cross-chain moves are not allowed.', 'no_bridge');

    // ---- Approval threshold (dollar → dollar-free rule, §4) --------------
    if (/\$\s?\d|\bover\b.*\b(usd|dollars?)\b|\babove\b.*\b(usd|dollars?)\b|\bapprov(?:e|al)\b|\bconfirm(?:ation)?\b.*\b(large|big)\b/.test(t)
        && /\bapprov|confirm|sign off|manual|over \$|above \$/.test(t)) {
      add('approval', 'approval', 'Approval over a limit',
        'Orders above your set size need manual approval before they proceed. Set the exact limit privately in the app.', 'dollar_limit_private');
    }

    // ---- Rate / cooldown -------------------------------------------------
    m = t.match(/(?:max|no more than|up to)\s*(\d{1,3})\s*(?:trades?|orders?)\s*(?:per|a)\s*(day|hour|week)/);
    if (m) add('rate', 'gate', 'Trade-rate cap',
      'At most ' + m[1] + ' trades per ' + m[2] + '.', { n: Number(m[1]), per: m[2] });

    // ---- Emergency / kill trigger ----------------------------------------
    if (/\b(unwind|exit everything|flatten everything|kill switch|emergency|panic|de-?risk everything)\b/.test(t))
      add('emergency', 'monitor', 'Emergency unwind trigger',
        'On the named trigger, hand off to the Escape Agent to unwind in dependency order.', 'escape');
    else if (/\bdepeg\b|\boracle fail|\bbridge halt|\bhack\b|\bexploit\b/.test(t))
      add('emergency', 'monitor', 'Systemic trigger',
        'Watch for the named systemic event and alert / begin an orderly unwind.', 'systemic');

    return rules;
  }

  /**
   * @param text a plain-language policy description
   * @returns { rules[], envelope{...}, coverage{...}, warnings[], summary, recognized }
   */
  function compile(input) {
    const text = String(input == null ? '' : input).slice(0, 2000).trim();
    if (!text) {
      return { rules: [], coverage: coverageOf([]), warnings: [], recognized: 0,
        summary: 'Describe your limits in plain words and the compiler turns them into a deterministic, revocable Authority Envelope.' };
    }

    // De-dupe by axis — first recognizer for an axis wins (deterministic).
    const seen = Object.create(null);
    const rules = [];
    for (const r of recognize(text)) {
      if (seen[r.axis]) continue;
      seen[r.axis] = 1;
      rules.push(Object.assign({}, r, { tier_label: TIER[r.tier].label, tier_note: TIER[r.tier].note }));
    }
    // Stable order: by enforcement rank, then discovery order.
    rules.forEach((r, i) => { r._i = i; });
    rules.sort((a, b) => (TIER[a.tier].rank - TIER[b.tier].rank) || (a._i - b._i));
    rules.forEach((r) => { delete r._i; });

    const coverage = coverageOf(rules);
    const warnings = [];
    if (rules.length && !coverage.axes.loss) warnings.push('No loss stop set — a drawdown or daily-loss limit is the single most important guard.');
    if (rules.length && !coverage.axes.size && !coverage.axes.exposure) warnings.push('No size cap — add a max per-position or total-exposure limit so one trade can’t dominate.');
    if (!rules.length) warnings.push('Nothing recognized yet — try phrasing like “max 5% per trade, no shorts, stop if down 8%”.');

    const strongest = rules[0];
    const summary = rules.length
      ? (rules.length + ' rule' + (rules.length === 1 ? '' : 's') + ' compiled — '
        + coverage.covered + '/' + AXES.length + ' protection axes covered. '
        + (strongest ? 'Tightest guard: ' + strongest.label.toLowerCase() + ' (' + strongest.tier_label + ').' : ''))
      : 'No rules recognized — rephrase your limits and re-compile.';

    return { rules, coverage, warnings, recognized: rules.length, summary, tiers: TIER };
  }

  function coverageOf(rules) {
    const axes = Object.create(null);
    for (const r of rules) {
      if (r.axis === 'gate_conf' || r.axis === 'rate') continue;      // extras, not core axes
      if (r.axis === 'scope_venue') { axes.scope = 1; continue; }
      axes[r.axis] = 1;
    }
    const covered = AXES.filter((a) => axes[a]).length;
    return { axes, covered, total: AXES.length,
      missing: AXES.filter((a) => !axes[a]) };
  }

  return { compile, TIER, AXES, MAJORS };
}));
