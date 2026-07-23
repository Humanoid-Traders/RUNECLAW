/**
 * Follow-an-agent: new-pick push watch (Marketplace Phase 3b).
 *
 * Periodically re-derives each FOLLOWED agent's live "would-take" picks (the
 * same gate-match the /api/copy/picks surface uses) and pushes a notification
 * to that agent's followers when a NEW pick appears — so "your agent just found
 * a setup" reaches you even when the tab is closed.
 *
 * Safety / §4:
 *   - Opt-in per user via profile prefs (push_copy); reaches nobody who didn't
 *     ask, and only the followers of the agent that fired.
 *   - The payload carries agent name + symbol + direction only — never a dollar
 *     figure, never account data. It links to the app; it places no trade.
 *   - Baseline on first sweep: a restart records the current picks silently and
 *     never replays the existing backlog as fresh news.
 *   - Everything is best-effort — a gateway/push/DB hiccup skips the sweep,
 *     never throws.
 */

const { isConfigured: gatewayConfigured, getGateway } = require('./gateway');
const { picksForAgent } = require('./agent_match');

let seen = null;                 // Set<"agentId|signal_key">; null until baseline
const MAX_SEEN = 5000;

/** Reset the baseline (tests + restarts). */
function resetCopyWatch() { seen = null; }

function baseSym(sym) { return String(sym || '').toUpperCase().replace(/[:/].*$/, ''); }

/**
 * Pure: the picks (across all followed agents) not already in `seenSet`.
 * Returns [{ agentId, name, icon, signal, key }]. `catalogueById` is a Map of
 * agent_id -> catalogue card; `followedAgentIds` the distinct followed slugs.
 */
function newPicks(catalogueById, signals, followedAgentIds, seenSet) {
  const out = [];
  for (const agentId of followedAgentIds) {
    const a = catalogueById.get(agentId);
    if (!a) continue;
    const { picks, name, icon } = picksForAgent(a, signals, 20);
    for (const s of picks) {
      const key = `${agentId}|${s.signal_key}`;
      if (!seenSet.has(key)) out.push({ agentId, name, icon, signal: s, key });
    }
  }
  return out;
}

/**
 * One sweep. `deps` supplies the data (all injectable for tests):
 *   loadFollowedAgentIds() -> string[]   (distinct followed slugs)
 *   loadSignals()          -> signal[]   (live OPEN signals)
 *   loadCatalogue()        -> agent[]    (marketplace cards w/ scorecard.gates)
 *   loadFollowers(agentId) -> number[]   (user_ids following that agent)
 *   loadOptIns()           -> Set<number>(user_ids with push_copy === true)
 * `notify(payload, userIds)` defaults to push.notifySubscribers. Returns the
 * number of push sends attempted.
 */
async function sweepCopy(deps = {}, notify) {
  try {
    const followed = await (deps.loadFollowedAgentIds || dbFollowedAgentIds)();
    if (!followed || !followed.length) return 0;
    const signals = await (deps.loadSignals || dbSignals)();
    const catalogue = await (deps.loadCatalogue || dbCatalogue)();
    if (!catalogue || !catalogue.length) return 0;
    const byId = new Map(catalogue.map(a => [a.id, a]));

    if (seen === null) {                       // baseline: record, never notify
      seen = new Set(newPicks(byId, signals, followed, new Set()).map(p => p.key));
      return 0;
    }

    const fresh = newPicks(byId, signals, followed, seen);
    if (!fresh.length) return 0;
    fresh.forEach(p => seen.add(p.key));
    if (seen.size > MAX_SEEN) seen = new Set(Array.from(seen).slice(-Math.floor(MAX_SEEN / 2)));

    const optIns = await (deps.loadOptIns || dbOptIns)();     // Set<user_id>
    const send = notify || defaultNotify;

    // Group fresh picks by agent; notify each agent's opted-in followers once.
    const byAgent = new Map();
    for (const p of fresh) {
      if (!byAgent.has(p.agentId)) byAgent.set(p.agentId, []);
      byAgent.get(p.agentId).push(p);
    }
    let sent = 0;
    for (const [agentId, picks] of byAgent) {
      const followers = await (deps.loadFollowers || dbFollowers)(agentId);
      const targets = (followers || []).filter(uid => optIns.has(uid));
      if (!targets.length) continue;
      const p0 = picks[0];
      const more = picks.length > 1 ? ` +${picks.length - 1} more` : '';
      const dir = String(p0.signal.direction || '').toUpperCase();
      sent += await send({
        title: `📡 ${p0.name} — new pick`,
        body: `${baseSym(p0.signal.symbol)} ${dir}${more} · paper-copy it in the app`,
        url: '/dashboard#agents',
      }, targets);
    }
    return sent;
  } catch (e) {
    return 0;
  }
}

// ── Real-DB data sources (only run in production; tests inject deps) ──────────
async function dbFollowedAgentIds() {
  const { pool } = require('../db');
  const [rows] = await pool.execute('SELECT DISTINCT agent_id FROM copy_subscriptions');
  return rows.map(r => r.agent_id);
}
async function dbFollowers(agentId) {
  const { pool } = require('../db');
  const [rows] = await pool.execute(
    'SELECT user_id FROM copy_subscriptions WHERE agent_id = ?', [agentId]);
  return rows.map(r => r.user_id);
}
async function dbSignals() {
  const { pool } = require('../db');
  const [rows] = await pool.execute(
    `SELECT signal_key, symbol, direction, confidence, regime, status
     FROM signals WHERE status = ? ORDER BY created_at DESC LIMIT 100`, ['OPEN']);
  return rows;
}
async function dbCatalogue() {
  if (!gatewayConfigured()) return [];
  const r = await getGateway('/public/strategies', 15000);
  return (r && r.status >= 200 && r.status < 300 && r.data && Array.isArray(r.data.agents))
    ? r.data.agents : [];
}
async function dbOptIns() {
  const { pool } = require('../db');
  const [rows] = await pool.execute('SELECT user_id, prefs FROM user_profiles LIMIT 2000');
  const set = new Set();
  for (const r of rows) {
    let prefs = {};
    try { prefs = JSON.parse(r.prefs || '{}'); } catch (e) { continue; }
    if (prefs && prefs.push_copy === true) set.add(r.user_id);
  }
  return set;
}
function defaultNotify(payload, userIds) {
  return require('./push').notifySubscribers(payload, userIds);
}

/** Start the periodic sweep (best-effort; needs push + gateway configured). */
function startCopyWatch(intervalMs = 5 * 60 * 1000) {
  const push = require('./push');
  const tick = () => { if (push.isConfigured() && gatewayConfigured()) sweepCopy().catch(() => {}); };
  setTimeout(tick, 30 * 1000);            // first real sweep after boot settles
  return setInterval(tick, intervalMs);
}

module.exports = { sweepCopy, newPicks, resetCopyWatch, startCopyWatch, baseSym };
