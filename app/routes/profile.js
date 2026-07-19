/**
 * Per-user agent profile (JWT-authed).
 *
 * The user's OWN agent preferences, stored server-side so they follow the
 * account across devices (previously nothing persisted at all):
 *   - risk_pref   : conservative | balanced | aggressive (personal display +
 *                   chat context only — NEVER touches the operator bot's
 *                   global stance; that is the admin-gated /api/controls/stance)
 *   - watchlist   : pinned symbols (float to the top of the Markets universe)
 *   - prefs       : whitelisted UI defaults (trade ticket margin/leverage,
 *                   chart symbol/timeframe)
 *
 * Everything is validated/whitelisted here before write; the chat proxy
 * (routes/chat.js) reads the row and passes a compact profile to the bot
 * gateway so the agent knows who it's talking to.
 */

const express = require('express');
const { pool } = require('../db');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');

const router = express.Router();
router.use(authMiddleware);

const putLimit = rateLimit({ windowMs: 60000, max: 30, key: userKey });

const RISK_PREFS = new Set(['conservative', 'balanced', 'aggressive']);
const CHART_TFS = new Set(['15min', '1h', '4h', '1d']);
// AI-chat languages the bot can localize replies into (base codes; kept in
// sync with bot/utils/i18n.py _CHAT_LANG_NAMES).
const LANGS = new Set(['en', 'zh', 'es', 'fr', 'de', 'pt', 'ru', 'ja', 'ko',
  'ar', 'hi', 'tr', 'it', 'id', 'vi', 'th', 'nl', 'pl', 'uk', 'fa', 'ms',
  'fil', 'tl', 'bn', 'ur', 'sv', 'no', 'da', 'fi', 'cs', 'el', 'he', 'ro',
  'hu']);
const WATCHLIST_MAX = 20;
const SYMBOL_RE = /^[A-Z0-9]{2,20}$/;

function safeParse(s, fallback) {
  if (!s) return fallback;
  try { const v = JSON.parse(s); return v ?? fallback; } catch (e) { return fallback; }
}

function shapeRow(row) {
  return {
    risk_pref: row?.risk_pref || null,
    watchlist: safeParse(row?.watchlist, []),
    prefs: safeParse(row?.prefs, {}),
  };
}

async function loadProfile(userId) {
  const [rows] = await pool.execute(
    'SELECT user_id, risk_pref, watchlist, prefs FROM user_profiles WHERE user_id = ?',
    [userId]);
  return shapeRow(rows[0]);
}

// Whitelist + clamp the prefs object. Unknown keys are dropped silently.
function sanitizePrefs(input) {
  const out = {};
  if (!input || typeof input !== 'object') return out;
  if (input.default_margin_usd !== undefined && input.default_margin_usd !== null) {
    const m = Number(input.default_margin_usd);
    if (Number.isFinite(m) && m > 0) out.default_margin_usd = Math.min(m, 1e6);
  }
  if (input.default_leverage !== undefined && input.default_leverage !== null) {
    const l = Math.round(Number(input.default_leverage));
    if (Number.isFinite(l) && l >= 1) out.default_leverage = Math.min(l, 125);
  }
  if (typeof input.chart_symbol === 'string') {
    const s = input.chart_symbol.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 20);
    if (s.length >= 2) out.chart_symbol = s;
  }
  if (typeof input.chart_tf === 'string' && CHART_TFS.has(input.chart_tf)) {
    out.chart_tf = input.chart_tf;
  }
  // Preferred language for the AI chat (the bot LLM localizes freeform replies).
  // Store the normalized base code; the bot maps it to a language name.
  if (typeof input.lang === 'string') {
    const base = input.lang.trim().toLowerCase().replace(/_/g, '-').split('-')[0];
    if (LANGS.has(base)) out.lang = base;
  }
  return out;
}

// GET /api/profile -> { risk_pref, watchlist, prefs }
router.get('/', async (req, res) => {
  try {
    res.json(await loadProfile(req.user.user_id));
  } catch (err) {
    console.error('Profile read error:', err.message);
    res.status(500).json({ error: 'Failed to read profile' });
  }
});

// PUT /api/profile  body: { risk_pref?, watchlist?, prefs? } — omitted fields
// keep their stored value (merge semantics, so callers can PATCH one thing).
router.put('/', putLimit, async (req, res) => {
  try {
    const uid = req.user.user_id;
    const b = req.body || {};
    const current = await loadProfile(uid);

    let riskPref = current.risk_pref;
    if (b.risk_pref !== undefined) {
      if (b.risk_pref === null || b.risk_pref === '') riskPref = null;
      else if (RISK_PREFS.has(String(b.risk_pref).toLowerCase())) {
        riskPref = String(b.risk_pref).toLowerCase();
      } else {
        return res.status(400).json({ error: 'risk_pref must be conservative|balanced|aggressive or null' });
      }
    }

    let watchlist = current.watchlist;
    if (b.watchlist !== undefined) {
      if (!Array.isArray(b.watchlist)) {
        return res.status(400).json({ error: 'watchlist must be an array of symbols' });
      }
      const cleaned = [...new Set(b.watchlist
        .map(s => String(s || '').toUpperCase().replace(/[^A-Z0-9]/g, ''))
        .filter(s => SYMBOL_RE.test(s)))];
      if (cleaned.length > WATCHLIST_MAX) {
        return res.status(400).json({ error: `watchlist is capped at ${WATCHLIST_MAX} symbols` });
      }
      watchlist = cleaned;
    }

    const prefs = b.prefs !== undefined
      ? { ...current.prefs, ...sanitizePrefs(b.prefs) }
      : current.prefs;

    await pool.execute(
      `INSERT INTO user_profiles (user_id, risk_pref, watchlist, prefs)
       VALUES (?, ?, ?, ?)
       ON DUPLICATE KEY UPDATE risk_pref = VALUES(risk_pref),
         watchlist = VALUES(watchlist), prefs = VALUES(prefs)`,
      [uid, riskPref, JSON.stringify(watchlist), JSON.stringify(prefs)]);
    res.json({ ok: true, risk_pref: riskPref, watchlist, prefs });
  } catch (err) {
    console.error('Profile write error:', err.message);
    res.status(500).json({ error: 'Failed to save profile' });
  }
});

module.exports = router;
module.exports.loadProfile = loadProfile;
