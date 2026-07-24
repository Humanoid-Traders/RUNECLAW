/**
 * Database layer - uses MySQL (TiDB) when DATABASE_URL is available,
 * falls back to in-memory storage for demo/development.
 */

let pool = null;
let memDb = null;
let USE_MYSQL = !!process.env.DATABASE_URL;

if (USE_MYSQL) {
  try {
    const mysql = require('mysql2/promise');
    pool = mysql.createPool(process.env.DATABASE_URL);
    console.log('Using MySQL database');
  } catch (err) {
    console.error('mysql2 not available, falling back to in-memory:', err.message);
    USE_MYSQL = false;
  }
}

// ── In-memory database ──────────────────────────────────────────

class MemoryDB {
  constructor() {
    this.users = [];
    this.trades = [];
    this.snapshots = [];
    this._nextUserId = 1;
    this._nextTradeId = 1;
    this._nextSnapId = 1;
    this.scanCache = null; // { scan_json, updated_at }
    this.signals = [];     // global signal stream (UPSERT by signal_key)
    this._nextSignalId = 1;
    this.agentEvents = []; // public agent mind-stream feed (bounded ring)
    this._nextAgentEventId = 1;
    this.reportsCache = null;  // { reports_json, updated_at } (single row)
    this.walletLinkCodes = {};   // code -> { user_id, expires_at }
    this.walletLinkNonces = {};  // address -> { message, expires_at }
    this.userProfiles = {};    // user_id -> { risk_pref, watchlist, prefs }
    this.pushSubs = [];        // web-push subscriptions (UPSERT by endpoint)
    this._nextPushSubId = 1;
    this.pendingStance = null; // { mode, requested_by, telegram_id, created_at } (single row)
    this.pendingCreds = [];   // pending_credentials (UPSERT by user_id)
    this.exchangeStatus = {}; // user_id -> { connected }
    this.pendingControls = []; // pending_controls (UPSERT by user_id)
    this.userControls = {};   // user_id -> { live_enabled, max_margin, paused, allowlisted }
    this.pendingFlatten = []; // pending_flatten (UPSERT by user_id)
    this.userAlerts = [];     // custom "tell me when…" tripwires
    this._nextAlertId = 1;
    this.userStrategies = []; // user-authored marketplace strategies (config only)
    this._nextStrategyId = 1;
    this.agentLetters = [];   // weekly agent letters (UPSERT-free; one per week_key)
    this.copySubs = [];       // strategy-agent follows (UNIQUE user_id+agent_id)
    this._nextCopySubId = 1;
    this.arenaAccounts = {};  // user_id -> { balance, created_at } (paper arena)
    this.arenaPositions = []; // open paper positions
    this._nextArenaPosId = 1;
    this.arenaTrades = [];    // closed paper trades (history)
    this._nextArenaTradeId = 1;
    this.arenaSeasons = [];   // named competition windows (no resets)
    this._nextArenaSeasonId = 1;
  }

  // Minimal query interface matching mysql2 pool.execute() return format
  async execute(sql, params = []) {
    const cmd = sql.trim().toUpperCase();

    if (cmd.startsWith('CREATE TABLE')) return [[], []];

    // -- SIGNALS -- (checked before TRADES: the stats query shares COUNT(*)/wins
    // aliases with trade handlers, so it must match here first.)
    if (cmd.includes('INSERT INTO SIGNALS')) {
      // params: signal_key, symbol, direction, confidence, score, pattern,
      // regime, entry_price, stop_loss, take_profit, rr, thesis, status, pnl,
      // created_at, resolved_at. ON DUPLICATE KEY updates status/pnl/resolved_at.
      const cols = ['signal_key','symbol','direction','confidence','score','pattern',
        'regime','entry_price','stop_loss','take_profit','rr','thesis','status','pnl',
        'created_at','resolved_at'];
      const row = {}; cols.forEach((k, i) => { row[k] = params[i]; });
      const existing = this.signals.find(s => s.signal_key === row.signal_key);
      if (existing) {
        existing.status = row.status; existing.pnl = row.pnl; existing.resolved_at = row.resolved_at;
      } else {
        row.id = this._nextSignalId++;
        this.signals.push(row);
      }
      return [{ affectedRows: 1 }, []];
    }

    if (cmd.includes('FROM SIGNALS') && cmd.includes('COUNT(*)')) {
      const resolved = this.signals.filter(s => s.pnl !== null && s.pnl !== undefined);
      const wins = resolved.filter(s => Number(s.pnl) > 0).length;
      const net_pnl = resolved.reduce((a, s) => a + (Number(s.pnl) || 0), 0);
      return [[{ resolved: resolved.length, wins, net_pnl }], []];
    }

    if (cmd.includes('FROM SIGNALS')) {
      // Filters are ignored in the mock; newest-first up to the LIMIT (last param).
      const limit = parseInt(params[params.length - 1]) || 50;
      const rows = [...this.signals]
        .sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
        .slice(0, limit);
      return [rows, []];
    }

    // -- AGENT LETTERS (weekly fund-style letter; one row per ISO week) --
    if (cmd.includes('INSERT INTO AGENT_LETTERS')) {
      // params: week_key, generated_at, letter_json
      if (!this.agentLetters.some(l => l.week_key === params[0])) {
        this.agentLetters.push({ week_key: params[0], generated_at: params[1],
          letter_json: params[2] });
      }
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM AGENT_LETTERS')) {
      if (cmd.includes('WHERE WEEK_KEY')) {
        return [this.agentLetters.filter(l => l.week_key === params[0]).map(r => ({ ...r })), []];
      }
      const rows = [...this.agentLetters]
        .sort((a, b) => String(b.week_key).localeCompare(String(a.week_key)))
        .slice(0, 52)
        .map(({ week_key, generated_at }) => ({ week_key, generated_at }));
      return [rows, []];
    }

    // -- USER ALERTS (custom "tell me when…" tripwires; one-shot) --
    // Checked before USERS handlers: 'USER_ALERTS' must never fall through
    // to a substring match on 'USERS'.
    if (cmd.includes('INSERT INTO USER_ALERTS')) {
      // params: user_id, symbol, metric, op, threshold, mode, cooldown_min, created_at
      this.userAlerts.push({
        id: this._nextAlertId++, user_id: params[0], symbol: params[1],
        metric: params[2], op: params[3], threshold: params[4],
        mode: params[5], cooldown_min: params[6],
        active: 1, trigger_price: null, created_at: params[7], triggered_at: null,
      });
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM USER_ALERTS') && cmd.includes('COUNT(*)')) {
      const n = this.userAlerts.filter(
        a => a.user_id === params[0] && a.active === 1).length;
      return [[{ n }], []];
    }
    if (cmd.includes('UPDATE USER_ALERTS')) {
      // Two shapes share the params (triggered_at, trigger_price, id [, cutoff]):
      //  one-shot disarm  … SET ACTIVE = 0, …          WHERE id AND active = 1
      //  recurring restamp … SET triggered_at, … WHERE id AND active = 1
      //                      AND (triggered_at IS NULL OR triggered_at <= ?)
      const a = this.userAlerts.find(x => x.id === params[2] && x.active === 1);
      if (!a) return [{ affectedRows: 0 }, []];
      if (cmd.includes('ACTIVE = 0')) {
        a.active = 0;
      } else if (params.length > 3) {
        // cooldown guard: only restamp if the last fire is old enough
        const cutoff = new Date(params[3]).getTime();
        const last = a.triggered_at ? new Date(a.triggered_at).getTime() : null;
        if (last !== null && last > cutoff) return [{ affectedRows: 0 }, []];
      }
      a.triggered_at = params[0]; a.trigger_price = params[1];
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('DELETE FROM USER_ALERTS')) {
      // params: id, user_id (own rows only)
      const before = this.userAlerts.length;
      this.userAlerts = this.userAlerts.filter(
        a => !(a.id === params[0] && a.user_id === params[1]));
      return [{ affectedRows: before - this.userAlerts.length }, []];
    }
    if (cmd.includes('FROM USER_ALERTS')) {
      if (cmd.includes('WHERE USER_ID')) {
        const rows = this.userAlerts.filter(a => a.user_id === params[0])
          .sort((a, b) => b.id - a.id).slice(0, 50);
        return [rows.map(r => ({ ...r })), []];
      }
      // engine sweep: WHERE active = 1
      return [this.userAlerts.filter(a => a.active === 1).map(r => ({ ...r })), []];
    }

    // -- USER STRATEGIES (user-authored marketplace strategies; config only) --
    // Placed before USERS handlers so a substring match on 'USERS' can't swallow
    // these ('USER_STRATEGIES' shares the 'USER' prefix). No dollar fields (§4).
    if (cmd.includes('INSERT INTO USER_STRATEGIES')) {
      // params: user_id, slug, name, tagline, how, icon, rules, risk_label, regime, horizon, created_at, updated_at
      this.userStrategies.push({
        id: this._nextStrategyId++, user_id: params[0], slug: params[1], name: params[2],
        tagline: params[3], how: params[4], icon: params[5], rules: params[6],
        risk_label: params[7], regime: params[8], horizon: params[9],
        visibility: 'draft', created_at: params[10], updated_at: params[11],
      });
      return [{ affectedRows: 1, insertId: this._nextStrategyId - 1 }, []];
    }
    if (cmd.includes('FROM USER_STRATEGIES') && cmd.includes('COUNT(*)')) {
      const pub = cmd.includes("VISIBILITY = 'PUBLIC'");
      const n = this.userStrategies.filter(
        s => s.user_id === params[0] && (!pub || s.visibility === 'public')).length;
      return [[{ n }], []];
    }
    if (cmd.includes('UPDATE USER_STRATEGIES')) {
      if (cmd.includes('SET VISIBILITY')) {
        // params: visibility, updated_at, id, user_id
        const s = this.userStrategies.find(x => x.id === params[2] && x.user_id === params[3]);
        if (!s) return [{ affectedRows: 0 }, []];
        s.visibility = params[0]; s.updated_at = params[1];
        return [{ affectedRows: 1 }, []];
      }
      // full-row edit — params: name, tagline, how, icon, rules, risk_label, regime, horizon, updated_at, id, user_id
      const s = this.userStrategies.find(x => x.id === params[9] && x.user_id === params[10]);
      if (!s) return [{ affectedRows: 0 }, []];
      s.name = params[0]; s.tagline = params[1]; s.how = params[2]; s.icon = params[3];
      s.rules = params[4]; s.risk_label = params[5]; s.regime = params[6]; s.horizon = params[7];
      s.updated_at = params[8];
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('DELETE FROM USER_STRATEGIES')) {
      // params: id, user_id (own rows only)
      const before = this.userStrategies.length;
      this.userStrategies = this.userStrategies.filter(
        s => !(s.id === params[0] && s.user_id === params[1]));
      return [{ affectedRows: before - this.userStrategies.length }, []];
    }
    if (cmd.includes('FROM USER_STRATEGIES')) {
      if (cmd.includes('AND USER_ID')) {
        // getById: WHERE id = ? AND user_id = ?
        const rows = this.userStrategies.filter(s => s.id === params[0] && s.user_id === params[1]);
        return [rows.map(r => ({ ...r })), []];
      }
      if (cmd.includes('WHERE USER_ID')) {
        const rows = this.userStrategies.filter(s => s.user_id === params[0])
          .sort((a, b) => b.id - a.id).slice(0, 50);
        return [rows.map(r => ({ ...r })), []];
      }
      if (cmd.includes('WHERE SLUG')) {
        const rows = this.userStrategies.filter(
          s => s.slug === params[0] && s.visibility === 'public');
        return [rows.map(r => ({ ...r })), []];
      }
      // public list: WHERE visibility = 'public'
      const rows = this.userStrategies.filter(s => s.visibility === 'public')
        .sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at))
        .slice(0, Number(params[0]) || 120);
      return [rows.map(r => ({ ...r })), []];
    }

    // -- PAPER TRADING ARENA (virtual accounts; §4: no real funds ever) --
    if (cmd.includes('INSERT INTO ARENA_ACCOUNTS')) {
      // params: user_id, balance, created_at
      if (!this.arenaAccounts[params[0]]) {
        this.arenaAccounts[params[0]] = { user_id: params[0], balance: params[1], created_at: params[2] };
      }
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('UPDATE ARENA_ACCOUNTS')) {
      // params: balance, user_id
      const a = this.arenaAccounts[params[1]];
      if (!a) return [{ affectedRows: 0 }, []];
      a.balance = params[0];
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM ARENA_ACCOUNTS')) {
      if (cmd.includes('WHERE USER_ID')) {
        const a = this.arenaAccounts[params[0]];
        return [a ? [{ ...a }] : [], []];
      }
      // leaderboard: all accounts
      return [Object.values(this.arenaAccounts).map(a => ({ ...a })), []];
    }
    if (cmd.includes('INSERT INTO ARENA_POSITIONS')) {
      // params: user_id, symbol, direction, entry, margin, leverage, opened_at
      this.arenaPositions.push({
        id: this._nextArenaPosId++, user_id: params[0], symbol: params[1],
        direction: params[2], entry: params[3], margin: params[4],
        leverage: params[5], opened_at: params[6],
      });
      return [{ affectedRows: 1, insertId: this._nextArenaPosId - 1 }, []];
    }
    if (cmd.includes('DELETE FROM ARENA_POSITIONS')) {
      // params: id, user_id (own rows only)
      const before = this.arenaPositions.length;
      this.arenaPositions = this.arenaPositions.filter(
        p => !(p.id === params[0] && p.user_id === params[1]));
      return [{ affectedRows: before - this.arenaPositions.length }, []];
    }
    if (cmd.includes('FROM ARENA_POSITIONS')) {
      if (cmd.includes('WHERE ID')) {
        // params: id, user_id
        const rows = this.arenaPositions.filter(p => p.id === params[0] && p.user_id === params[1]);
        return [rows.map(r => ({ ...r })), []];
      }
      if (cmd.includes('WHERE USER_ID')) {
        const rows = this.arenaPositions.filter(p => p.user_id === params[0])
          .sort((a, b) => b.id - a.id);
        return [rows.map(r => ({ ...r })), []];
      }
      // leaderboard: all open positions
      return [this.arenaPositions.map(r => ({ ...r })), []];
    }
    if (cmd.includes('INSERT INTO ARENA_TRADES')) {
      // params: user_id, symbol, direction, entry, exit_price, margin, leverage, pnl, reason, opened_at, closed_at
      this.arenaTrades.push({
        id: this._nextArenaTradeId++, user_id: params[0], symbol: params[1],
        direction: params[2], entry: params[3], exit_price: params[4],
        margin: params[5], leverage: params[6], pnl: params[7],
        reason: params[8], opened_at: params[9], closed_at: params[10],
      });
      return [{ affectedRows: 1, insertId: this._nextArenaTradeId - 1 }, []];
    }
    if (cmd.includes('INSERT INTO ARENA_SEASONS')) {
      // params: name, starts_at, ends_at, created_at
      this.arenaSeasons.push({ id: this._nextArenaSeasonId++, name: params[0],
        starts_at: params[1], ends_at: params[2], created_at: params[3] });
      return [{ affectedRows: 1, insertId: this._nextArenaSeasonId - 1 }, []];
    }
    if (cmd.includes('FROM ARENA_SEASONS')) {
      // newest first — the route picks the relevant one
      const rows = this.arenaSeasons.slice().sort((a, b) => b.id - a.id);
      return [rows.map(r => ({ ...r })), []];
    }
    if (cmd.includes('FROM ARENA_TRADES')) {
      if (cmd.includes('CLOSED_AT >=')) {
        // season window: WHERE closed_at >= ? AND closed_at <= ?
        const lo = new Date(params[0]).getTime(), hi = new Date(params[1]).getTime();
        const rows = this.arenaTrades.filter(t => {
          const c = new Date(t.closed_at).getTime();
          return c >= lo && c <= hi;
        });
        return [rows.map(r => ({ ...r })), []];
      }
      if (cmd.includes('COUNT(*)') && cmd.includes('GROUP BY USER_ID')) {
        const counts = {};
        for (const t of this.arenaTrades) counts[t.user_id] = (counts[t.user_id] || 0) + 1;
        return [Object.entries(counts).map(([user_id, n]) => ({ user_id: Number(user_id), n })), []];
      }
      // history: WHERE user_id = ? ORDER BY id DESC LIMIT 30
      const rows = this.arenaTrades.filter(t => t.user_id === params[0])
        .sort((a, b) => b.id - a.id).slice(0, 30);
      return [rows.map(r => ({ ...r })), []];
    }

    // -- PUSH SUBSCRIPTIONS (web push; UPSERT by endpoint) --
    if (cmd.includes('INSERT INTO PUSH_SUBSCRIPTIONS')) {
      // params: user_id, endpoint, keys_json
      const i = this.pushSubs.findIndex(s => s.endpoint === params[1]);
      if (i >= 0) {
        this.pushSubs[i].user_id = params[0];
        this.pushSubs[i].keys_json = params[2];
      } else {
        this.pushSubs.push({ id: this._nextPushSubId++, user_id: params[0],
          endpoint: params[1], keys_json: params[2], created_at: new Date() });
      }
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('DELETE FROM PUSH_SUBSCRIPTIONS')) {
      if (cmd.includes('ORDER BY ID ASC')) {          // drop oldest for user
        const mine = this.pushSubs.filter(s => s.user_id === params[0])
          .sort((a, b) => a.id - b.id);
        if (mine.length) this.pushSubs = this.pushSubs.filter(s => s.id !== mine[0].id);
      } else if (cmd.includes('AND ENDPOINT')) {      // user-scoped unsubscribe
        this.pushSubs = this.pushSubs.filter(
          s => !(s.user_id === params[0] && s.endpoint === params[1]));
      } else {                                        // prune by endpoint (410)
        this.pushSubs = this.pushSubs.filter(s => s.endpoint !== params[0]);
      }
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM PUSH_SUBSCRIPTIONS') && cmd.includes('COUNT(*)')) {
      return [[{ n: this.pushSubs.filter(s => s.user_id === params[0]).length }], []];
    }
    if (cmd.includes('FROM PUSH_SUBSCRIPTIONS')) {
      const rows = cmd.includes('WHERE USER_ID')
        ? this.pushSubs.filter(s => s.user_id === params[0])
        : [...this.pushSubs].sort((a, b) => b.id - a.id);
      return [rows.map(s => ({ ...s })), []];
    }

    // -- COPY SUBSCRIPTIONS (strategy-agent follows; UNIQUE user_id+agent_id) --
    if (cmd.includes('INSERT INTO COPY_SUBSCRIPTIONS')) {
      // params: user_id, agent_id. Idempotent on (user_id, agent_id).
      if (!this.copySubs.some(s => s.user_id === params[0] && s.agent_id === params[1])) {
        this.copySubs.push({ id: this._nextCopySubId++, user_id: params[0],
          agent_id: params[1], created_at: new Date() });
      }
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('DELETE FROM COPY_SUBSCRIPTIONS')) {
      // params: user_id, agent_id (user-scoped unfollow).
      this.copySubs = this.copySubs.filter(
        s => !(s.user_id === params[0] && s.agent_id === params[1]));
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM COPY_SUBSCRIPTIONS') && cmd.includes('COUNT(*)')) {
      return [[{ n: this.copySubs.filter(s => s.user_id === params[0]).length }], []];
    }
    if (cmd.includes('FROM COPY_SUBSCRIPTIONS')) {
      const rows = this.copySubs.filter(s => s.user_id === params[0])
        .sort((a, b) => a.id - b.id);
      return [rows.map(s => ({ ...s })), []];
    }

    // -- USER PROFILES (per-user agent profile: risk pref, watchlist, prefs) --
    if (cmd.includes('INTO USER_PROFILES')) {
      // params: user_id, risk_pref, watchlist, prefs (UPSERT by user_id)
      this.userProfiles[params[0]] = {
        user_id: params[0], risk_pref: params[1],
        watchlist: params[2], prefs: params[3], updated_at: new Date(),
      };
      return [{ affectedRows: 1 }, []];
    }
    // Topic-push fan-out: ALL profiles (no user_id param). Matched before the
    // single-profile lookup, which requires params[0].
    if (cmd.includes('FROM USER_PROFILES') && cmd.includes('LIMIT 2000')) {
      return [Object.values(this.userProfiles).slice(0, 2000)
        .map(p => ({ user_id: p.user_id, prefs: p.prefs })), []];
    }
    if (cmd.includes('FROM USER_PROFILES')) {
      const p = this.userProfiles[params[0]];
      return [p ? [{ ...p }] : [], []];
    }

    // -- REPORTS CACHE (single-row, like scan_cache) --
    if (cmd.includes('REPLACE INTO REPORTS_CACHE')) {
      this.reportsCache = { reports_json: params[0], updated_at: new Date() };
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM REPORTS_CACHE')) {
      return [this.reportsCache ? [{ ...this.reportsCache }] : [], []];
    }

    // -- PENDING STANCE (single-row admin request queue) --
    if (cmd.includes('REPLACE INTO PENDING_STANCE')) {
      // params: mode, requested_by, telegram_id
      this.pendingStance = {
        id: 1, mode: params[0], requested_by: params[1],
        telegram_id: params[2], created_at: new Date(),
      };
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('DELETE FROM PENDING_STANCE')) {
      this.pendingStance = null;
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM PENDING_STANCE')) {
      return [this.pendingStance ? [{ ...this.pendingStance }] : [], []];
    }

    // -- AGENT EVENTS (public mind-stream feed; bounded ring) --
    if (cmd.includes('INSERT INTO AGENT_EVENTS')) {
      // params: event_type, severity, symbol, title, body, data_json, created_at
      const row = {
        id: this._nextAgentEventId++,
        event_type: params[0], severity: params[1], symbol: params[2],
        title: params[3], body: params[4], data_json: params[5],
        created_at: params[6] || new Date(),
      };
      this.agentEvents.push(row);
      return [{ insertId: row.id }, []];
    }
    if (cmd.includes('FROM AGENT_EVENTS') && cmd.includes('OFFSET')) {
      // Prune probe: SELECT id ... ORDER BY id DESC LIMIT 1 OFFSET <keep>
      const m = cmd.match(/OFFSET\s+(\d+)/);
      const off = m ? parseInt(m[1]) : 0;
      const sorted = [...this.agentEvents].sort((a, b) => b.id - a.id);
      return [sorted.slice(off, off + 1).map(r => ({ id: r.id })), []];
    }
    if (cmd.includes('DELETE FROM AGENT_EVENTS')) {
      // DELETE ... WHERE id <= ? (ring-buffer prune)
      const cutoff = Number(params[0]);
      this.agentEvents = this.agentEvents.filter(e => e.id > cutoff);
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM AGENT_EVENTS')) {
      const m = cmd.match(/LIMIT\s+(\d+)/);
      const limit = m ? parseInt(m[1]) : 50;
      const rows = [...this.agentEvents].sort((a, b) => b.id - a.id).slice(0, limit);
      return [rows, []];
    }

    // -- PENDING CREDENTIALS / EXCHANGE STATUS --
    if (cmd.includes('INSERT INTO PENDING_CREDENTIALS')) {
      // params: user_id, telegram_id, exchange(venue), [encrypted_payload] —
      // action is a literal in the SQL ('connect'/'disconnect'). UPSERT by user_id.
      const action = cmd.includes("'DISCONNECT'") ? 'disconnect' : 'connect';
      const row = {
        user_id: params[0], telegram_id: params[1],
        exchange: params[2] || 'bitget', action,
        encrypted_payload: action === 'disconnect' ? null : params[3],
        created_at: new Date(),
      };
      const i = this.pendingCreds.findIndex(p => p.user_id === row.user_id);
      if (i >= 0) this.pendingCreds[i] = { id: this.pendingCreds[i].id, ...row };
      else this.pendingCreds.push({ id: this.pendingCreds.length + 1, ...row });
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('DELETE FROM PENDING_CREDENTIALS')) {
      this.pendingCreds = this.pendingCreds.filter(p => String(p.user_id) !== String(params[0]));
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM PENDING_CREDENTIALS')) {  // SELECT (DELETE handled above)
      if (cmd.includes('WHERE USER_ID')) {
        return [this.pendingCreds.filter(p => String(p.user_id) === String(params[0])), []];
      }
      return [[...this.pendingCreds].sort((a, b) => a.created_at - b.created_at), []];
    }
    if (cmd.includes('INSERT INTO EXCHANGE_STATUS')) {
      // params: user_id, exchange(venue), connected — upsert per (user, venue)
      // so multiple connected exchanges coexist.
      const key = String(params[0]);
      if (!this.exchangeStatus[key] || !Array.isArray(this.exchangeStatus[key])) {
        this.exchangeStatus[key] = [];
      }
      const venue = params[1] || 'bitget';
      const row = this.exchangeStatus[key].find(r => r.exchange === venue);
      if (row) row.connected = !!params[2];
      else this.exchangeStatus[key].push({ exchange: venue, connected: !!params[2] });
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM EXCHANGE_STATUS')) {
      const rows = this.exchangeStatus[String(params[0])];
      return [Array.isArray(rows)
        ? rows.map(r => ({ connected: r.connected, exchange: r.exchange || 'bitget' }))
        : [], []];
    }

    // -- PENDING CONTROLS / USER CONTROLS --
    if (cmd.includes('DELETE FROM PENDING_CONTROLS')) {
      this.pendingControls = this.pendingControls.filter(p => String(p.user_id) !== String(params[0]));
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('INSERT INTO PENDING_CONTROLS')) {
      // params: user_id, telegram_id, live_enabled, max_margin, paused
      const row = { user_id: params[0], telegram_id: params[1],
        live_enabled: params[2], max_margin: params[3], paused: params[4],
        created_at: new Date() };
      const i = this.pendingControls.findIndex(p => String(p.user_id) === String(row.user_id));
      if (i >= 0) this.pendingControls[i] = { id: this.pendingControls[i].id, ...row };
      else this.pendingControls.push({ id: this.pendingControls.length + 1, ...row });
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM PENDING_CONTROLS')) {
      if (cmd.includes('WHERE USER_ID')) {
        return [this.pendingControls.filter(p => String(p.user_id) === String(params[0])), []];
      }
      return [[...this.pendingControls].sort((a, b) => a.created_at - b.created_at), []];
    }
    if (cmd.includes('INSERT INTO USER_CONTROLS')) {
      // params: user_id, live_enabled, max_margin, paused, allowlisted
      this.userControls[params[0]] = {
        live_enabled: !!params[1], max_margin: params[2],
        paused: !!params[3], allowlisted: !!params[4],
      };
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM USER_CONTROLS')) {
      const c = this.userControls[params[0]];
      return [c ? [c] : [], []];
    }
    if (cmd.includes('DELETE FROM PENDING_FLATTEN')) {
      this.pendingFlatten = this.pendingFlatten.filter(p => String(p.user_id) !== String(params[0]));
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('INTO PENDING_FLATTEN')) {
      const row = { user_id: params[0], telegram_id: params[1], created_at: new Date() };
      const i = this.pendingFlatten.findIndex(p => String(p.user_id) === String(row.user_id));
      if (i >= 0) this.pendingFlatten[i] = row; else this.pendingFlatten.push(row);
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM PENDING_FLATTEN')) {
      if (cmd.includes('WHERE USER_ID')) {
        return [this.pendingFlatten.filter(p => String(p.user_id) === String(params[0])), []];
      }
      return [[...this.pendingFlatten].sort((a, b) => a.created_at - b.created_at), []];
    }

    // -- USERS --
    if (cmd.includes('INSERT INTO USERS')) {
      const exists = this.users.find(u => u.email === params[0]);
      if (exists) {
        const err = new Error('Duplicate entry'); err.code = 'ER_DUP_ENTRY'; throw err;
      }
      const user = { id: this._nextUserId++, email: params[0], password_hash: null,
        google_id: null, telegram_id: null, discord_id: null, x_id: null,
        wallet_address: null, avatar_url: null, plan: 'free',
        telegram_linked: false, link_token: null, link_token_expires: null,
        email_verified: false, verify_token: null, verify_token_expires: null,
        reset_token: null, reset_token_expires: null,
        referral_code: null, referred_by: null,
        leaderboard_handle: null, created_at: new Date() };
      // Column order varies: email/password vs the OAuth passwordless inserts.
      if (cmd.includes('PASSWORD_HASH')) {
        user.password_hash = params[1];
      } else if (cmd.includes('GOOGLE_ID')) {
        user.google_id = params[1]; user.avatar_url = params[2]; user.telegram_linked = !!params[3];
      } else if (cmd.includes('TELEGRAM_ID')) {
        user.telegram_id = params[1]; user.avatar_url = params[2]; user.telegram_linked = !!params[3];
      } else if (cmd.includes('DISCORD_ID')) {
        user.discord_id = params[1]; user.avatar_url = params[2]; user.telegram_linked = !!params[3];
      } else if (cmd.includes('X_ID')) {
        user.x_id = params[1]; user.avatar_url = params[2]; user.telegram_linked = !!params[3];
      } else if (cmd.includes('WALLET_ADDRESS')) {
        user.wallet_address = params[1]; user.avatar_url = params[2]; user.telegram_linked = !!params[3];
      }
      this.users.push(user);
      return [{ insertId: user.id }, []];
    }

    if (cmd.includes('FROM USERS WHERE GOOGLE_ID')) {
      return [this.users.filter(u => u.google_id === params[0]), []];
    }

    if (cmd.includes('FROM USERS WHERE TELEGRAM_ID')) {
      return [this.users.filter(u => String(u.telegram_id) === String(params[0])), []];
    }

    if (cmd.includes('FROM USERS WHERE DISCORD_ID')) {
      return [this.users.filter(u => String(u.discord_id) === String(params[0])), []];
    }

    if (cmd.includes('FROM USERS WHERE X_ID')) {
      return [this.users.filter(u => String(u.x_id) === String(params[0])), []];
    }

    if (cmd.includes('FROM USERS WHERE WALLET_ADDRESS')) {
      return [this.users.filter(u => u.wallet_address != null
        && String(u.wallet_address).toLowerCase() === String(params[0]).toLowerCase()), []];
    }

    if (cmd.startsWith('UPDATE USERS SET GOOGLE_ID')) {
      const user = this.users.find(u => u.id === params[1]);
      if (user) user.google_id = params[0];
      return [{ affectedRows: user ? 1 : 0 }, []];
    }

    if (cmd.startsWith('UPDATE USERS SET TELEGRAM_ID')) {
      const user = this.users.find(u => u.id === params[1]);
      if (user) user.telegram_id = params[0];
      return [{ affectedRows: user ? 1 : 0 }, []];
    }

    if (cmd.startsWith('UPDATE USERS SET DISCORD_ID')) {
      const user = this.users.find(u => u.id === params[1]);
      if (user) user.discord_id = params[0];
      return [{ affectedRows: user ? 1 : 0 }, []];
    }

    if (cmd.startsWith('UPDATE USERS SET X_ID')) {
      const user = this.users.find(u => u.id === params[1]);
      if (user) user.x_id = params[0];
      return [{ affectedRows: user ? 1 : 0 }, []];
    }

    if (cmd.startsWith('UPDATE USERS SET WALLET_ADDRESS')) {
      const user = this.users.find(u => u.id === params[1]);
      if (user) user.wallet_address = params[0];
      return [{ affectedRows: user ? 1 : 0 }, []];
    }

    if (cmd.startsWith('UPDATE USERS SET SOL_ADDRESS')) {
      // params: [sol_address|null, id] — Solana watch address (read-only mirror)
      const user = this.users.find(u => u.id === params[1]);
      if (user) user.sol_address = params[0];
      return [{ affectedRows: user ? 1 : 0 }, []];
    }

    // -- Referral / invite --
    if (cmd.startsWith('UPDATE USERS SET REFERRAL_CODE')) {
      const user = this.users.find(u => u.id === params[1]);
      if (user) user.referral_code = params[0];
      return [{ affectedRows: user ? 1 : 0 }, []];
    }
    if (cmd.startsWith('UPDATE USERS SET REFERRED_BY')) {
      const user = this.users.find(u => u.id === params[1]);
      if (user) user.referred_by = params[0];
      return [{ affectedRows: user ? 1 : 0 }, []];
    }
    if (cmd.includes('FROM USERS WHERE REFERRAL_CODE')) {
      return [this.users.filter(u => u.referral_code != null && u.referral_code === params[0]), []];
    }
    if (cmd.includes('FROM USERS WHERE REFERRED_BY')) {
      return [this.users.filter(u => u.referred_by === params[0]), []];
    }

    // -- Leaderboard opt-in (anonymous handle) --
    if (cmd.startsWith('UPDATE USERS SET LEADERBOARD_HANDLE')) {
      const user = this.users.find(u => u.id === params[1]);
      if (user) user.leaderboard_handle = params[0];  // params[0] may be null (opt-out)
      return [{ affectedRows: user ? 1 : 0 }, []];
    }
    // Bot sync desired-state pull: opted-in AND bot-linked, aliased columns.
    // Matched on both predicates (the route's SQL is multi-line, so exact
    // 'FROM USERS WHERE …' adjacency can't be relied on). Must be matched
    // BEFORE the generic handle handler below.
    if (cmd.includes('LEADERBOARD_HANDLE IS NOT NULL')
        && cmd.includes('TELEGRAM_ID IS NOT NULL')) {
      return [this.users
        .filter(u => u.leaderboard_handle != null && u.telegram_id != null)
        .slice(0, 500)
        .map(u => ({ user_id: u.id, telegram_id: u.telegram_id,
                     handle: u.leaderboard_handle })), []];
    }
    if (cmd.includes('FROM USERS WHERE LEADERBOARD_HANDLE IS NOT NULL')) {
      return [this.users.filter(u => u.leaderboard_handle != null), []];
    }
    if (cmd.includes('FROM USERS WHERE LEADERBOARD_HANDLE')) {  // = ?  (uniqueness check)
      return [this.users.filter(u => u.leaderboard_handle != null
        && String(u.leaderboard_handle).toLowerCase() === String(params[0]).toLowerCase()), []];
    }

    if (cmd.includes('FROM USERS WHERE EMAIL')) {
      const rows = this.users.filter(u => u.email === params[0]);
      return [rows, []];
    }

    if (cmd.includes('FROM USERS WHERE ID')) {
      const rows = this.users.filter(u => u.id === params[0]);
      return [rows, []];
    }

    if (cmd.startsWith('UPDATE USERS SET TELEGRAM_LINKED')) {
      // params: [linked, id] — plain linked-flag set (test fixtures / admin
      // tooling; the real link flow goes through the LINK_TOKEN branch below).
      const user = this.users.find(u => u.id === params[1]);
      if (user) user.telegram_linked = !!params[0];
      return [{ affectedRows: user ? 1 : 0 }, []];
    }

    if (cmd.includes('UPDATE USERS SET LINK_TOKEN')) {
      // Could be the link-token generation (3 params) or token consumption (1 param)
      if (cmd.includes('TELEGRAM_LINKED')) {
        // Consume token: ...telegram_linked=TRUE[, telegram_id=?] WHERE id=?
        // params end with the user id; telegram_id (if present) is just before it.
        const userId = params[params.length - 1];
        const tgId = cmd.includes('TELEGRAM_ID') ? params[params.length - 2] : null;
        const user = this.users.find(u => u.id === userId);
        if (user) {
          user.link_token = null; user.link_token_expires = null; user.telegram_linked = true;
          if (tgId != null) user.telegram_id = tgId;
        }
        return [{ affectedRows: user ? 1 : 0 }, []];
      }
      const user = this.users.find(u => u.id === params[2]);
      if (user) { user.link_token = params[0]; user.link_token_expires = params[1]; }
      return [{ affectedRows: user ? 1 : 0 }, []];
    }

    if (cmd.includes('FROM USERS WHERE LINK_TOKEN')) {
      const now = new Date();
      const rows = this.users.filter(u => u.link_token === params[0] && u.link_token_expires > now);
      return [rows, []];
    }

    // -- Account management: verify + reset tokens, password changes --
    if (cmd.startsWith('UPDATE USERS SET VERIFY_TOKEN')) {
      // params: [verify_token, verify_token_expires, id]
      const user = this.users.find(u => u.id === params[params.length - 1]);
      if (user) { user.verify_token = params[0]; user.verify_token_expires = params[1]; }
      return [{ affectedRows: user ? 1 : 0 }, []];
    }
    if (cmd.includes('FROM USERS WHERE VERIFY_TOKEN')) {
      const now = new Date();
      const rows = this.users.filter(u => u.verify_token === params[0] && u.verify_token_expires > now);
      return [rows, []];
    }
    if (cmd.startsWith('UPDATE USERS SET EMAIL_VERIFIED')) {
      // params: [id]
      const user = this.users.find(u => u.id === params[params.length - 1]);
      if (user) { user.email_verified = true; user.verify_token = null; user.verify_token_expires = null; }
      return [{ affectedRows: user ? 1 : 0 }, []];
    }
    if (cmd.startsWith('UPDATE USERS SET RESET_TOKEN')) {
      // params: [reset_token, reset_token_expires, id]
      const user = this.users.find(u => u.id === params[params.length - 1]);
      if (user) { user.reset_token = params[0]; user.reset_token_expires = params[1]; }
      return [{ affectedRows: user ? 1 : 0 }, []];
    }
    if (cmd.includes('FROM USERS WHERE RESET_TOKEN')) {
      const now = new Date();
      const rows = this.users.filter(u => u.reset_token === params[0] && u.reset_token_expires > now);
      return [rows, []];
    }
    if (cmd.startsWith('UPDATE USERS SET PASSWORD_HASH')) {
      // Reset flow clears reset_token too: [hash, id] or [hash, id] with reset clear.
      const user = this.users.find(u => u.id === params[params.length - 1]);
      if (user) {
        user.password_hash = params[0];
        if (cmd.includes('RESET_TOKEN')) { user.reset_token = null; user.reset_token_expires = null; }
      }
      return [{ affectedRows: user ? 1 : 0 }, []];
    }

    // -- TRADES --
    if (cmd.includes('DELETE FROM TRADES') && cmd.includes('USER_ID')) {
      if (cmd.includes('LIMIT 1')) {
        // Delete one open trade by symbol
        const idx = this.trades.findIndex(t => t.user_id === params[0] && t.symbol === params[1] && t.status === 'OPEN');
        if (idx >= 0) this.trades.splice(idx, 1);
      } else if (cmd.includes("STATUS = 'OPEN'")) {
        // Delete only the user's OPEN rows (portfolio write-through refresh)
        this.trades = this.trades.filter(t => !(t.user_id === params[0] && t.status === 'OPEN'));
      } else {
        this.trades = this.trades.filter(t => t.user_id !== params[0]);
      }
      return [{ affectedRows: 0 }, []];
    }

    if (cmd.includes('INSERT INTO TRADES')) {
      const trade = { id: this._nextTradeId++ };
      // Parse based on param count. Both real call sites (sync.js's full
      // POST / and its /trade-event close branch) bind exactly 11 params
      // for a closed-trade insert -- 'CLOSED' is a literal in the SQL, not
      // a placeholder -- so this must be 11, not 12 (the previous ===12
      // check never matched either real call site, silently dropping every
      // closed trade's user_id/symbol/etc in dev/demo mode without MySQL).
      if (params.length === 11) {
        // Closed trade insert
        Object.assign(trade, { user_id: params[0], symbol: params[1], direction: params[2], entry_price: params[3], exit_price: params[4], size_usd: params[5], pnl: params[6], fees: params[7], status: 'CLOSED', pattern: params[8], opened_at: params[9], closed_at: params[10] });
      } else if (params.length === 10) {
        // Open trade insert (positions array from the full sync -- includes opened_at)
        Object.assign(trade, { user_id: params[0], symbol: params[1], direction: params[2], entry_price: params[3], size_usd: params[4], fees: params[5], status: 'OPEN', pattern: params[6], stop_loss: params[7], take_profit: params[8], opened_at: params[9] });
      } else if (params.length === 9) {
        // Open trade insert (trade-event open branch -- no explicit opened_at)
        Object.assign(trade, { user_id: params[0], symbol: params[1], direction: params[2], entry_price: params[3], size_usd: params[4], fees: params[5], status: 'OPEN', pattern: params[6], stop_loss: params[7], take_profit: params[8], opened_at: new Date() });
      }
      this.trades.push(trade);
      return [{ insertId: trade.id }, []];
    }

    if (cmd.includes('UPDATE USERS SET TOTP_SECRET')) {
      // params: [secret|null, enabled, backup_codes_json|null, id]
      const u = this.users.find(x => x.id === params[params.length - 1]);
      if (u) {
        u.totp_secret = params[0];
        u.totp_enabled = params[1];
        u.totp_backup_codes = params[2];
      }
      return [{ affectedRows: u ? 1 : 0 }, []];
    }

    if (cmd.includes('UPDATE USERS SET TOTP_BACKUP_CODES')) {
      // params: [backup_codes_json, id]
      const u = this.users.find(x => x.id === params[1]);
      if (u) u.totp_backup_codes = params[0];
      return [{ affectedRows: u ? 1 : 0 }, []];
    }

    if (cmd.includes('UPDATE USERS SET PLAN')) {
      // Tier sync: params [plan, telegram_id]
      const u = this.users.find(x => String(x.telegram_id) === String(params[1]));
      if (u) u.plan = params[0];
      return [{ affectedRows: u ? 1 : 0 }, []];
    }

    if (cmd.includes('UPDATE TRADES SET NOTES')) {
      // params: notes, id, user_id
      const t = this.trades.find(t => t.id === params[1] && t.user_id === params[2]);
      if (t) t.notes = params[0];
      return [{ affectedRows: t ? 1 : 0 }, []];
    }

    if (cmd.includes('SUM(PNL)') && cmd.includes('SUM(FEES)') && cmd.includes('COUNT(*)')) {
      const closed = this.trades.filter(t => t.user_id === params[0] && t.status === 'CLOSED');
      const net_pnl = closed.reduce((a, t) => a + (parseFloat(t.pnl) || 0), 0);
      const total_fees = closed.reduce((a, t) => a + (parseFloat(t.fees) || 0), 0);
      return [[{ net_pnl, total_fees, total_trades: closed.length }], []];
    }

    if (cmd.includes('COALESCE(SUM(PNL)') && !cmd.includes('SUM(FEES)')) {
      const closed = this.trades.filter(t => t.user_id === params[0] && t.status === params[1]);
      const total_pnl = closed.reduce((a, t) => a + (parseFloat(t.pnl) || 0), 0);
      return [[{ total_pnl }], []];
    }

    if (cmd.includes('COUNT(*)') && cmd.includes('PNL > 0')) {
      const wins = this.trades.filter(t => t.user_id === params[0] && t.status === params[1] && parseFloat(t.pnl) > 0);
      return [[{ wins: wins.length }], []];
    }

    if (cmd.includes('COUNT(*)') && cmd.includes('FROM TRADES') && cmd.includes('STATUS') && !cmd.includes('PNL')) {
      if (cmd.includes('OPEN')) {
        const count = this.trades.filter(t => t.user_id === params[0] && t.status === 'OPEN').length;
        return [[{ open_count: count }], []];
      }
      // /api/trades/history's count query has status = 'CLOSED' as a literal,
      // not a bound param -- there is no params[1] to compare against there.
      const status = cmd.includes("STATUS = 'CLOSED'") ? 'CLOSED' : params[1];
      const count = this.trades.filter(t => t.user_id === params[0] && t.status === status).length;
      return [[{ total: count }], []];
    }

    if (cmd.includes('SELECT PNL, SIZE_USD')) {
      const rows = this.trades.filter(t => t.user_id === params[0] && t.status === params[1]).sort((a, b) => new Date(a.closed_at) - new Date(b.closed_at));
      return [rows, []];
    }

    if (cmd.includes('COALESCE(CLOSED_AT')) {
      // GET /api/trades/activity -- both OPEN and CLOSED trades, newest first
      const limit = params[1] || 60;
      const rows = this.trades.filter(t => t.user_id === params[0])
        .sort((a, b) => new Date(b.closed_at || b.opened_at) - new Date(a.closed_at || a.opened_at))
        .slice(0, limit);
      return [rows, []];
    }

    if (cmd.includes("STATUS = 'CLOSED'") && cmd.includes('ORDER BY CLOSED_AT ASC')) {
      // Track-record aggregation: full closed history, oldest first.
      const rows = this.trades
        .filter(t => t.user_id === params[0] && t.status === 'CLOSED' && t.closed_at)
        .sort((a, b) => new Date(a.closed_at) - new Date(b.closed_at));
      return [rows, []];
    }

    if (cmd.includes("STATUS = 'CLOSED'") && cmd.includes('ORDER BY CLOSED_AT DESC')) {
      let rows = this.trades.filter(t => t.user_id === params[0] && t.status === 'CLOSED').sort((a, b) => new Date(b.closed_at) - new Date(a.closed_at));
      const limit = params[1] || 50;
      const offset = params[2] || 0;
      rows = rows.slice(offset, offset + limit);
      return [rows, []];
    }

    if (cmd.includes("STATUS = 'OPEN'") && cmd.includes('ORDER BY OPENED_AT')) {
      const rows = this.trades.filter(t => t.user_id === params[0] && t.status === 'OPEN').sort((a, b) => new Date(b.opened_at) - new Date(a.opened_at));
      return [rows, []];
    }

    // -- EQUITY SNAPSHOTS --
    if (cmd.includes('DELETE FROM EQUITY_SNAPSHOTS')) {
      this.snapshots = this.snapshots.filter(s => s.user_id !== params[0]);
      return [{ affectedRows: 0 }, []];
    }

    if (cmd.includes('INSERT INTO EQUITY_SNAPSHOTS')) {
      this.snapshots.push({ id: this._nextSnapId++, user_id: params[0], equity: params[1], snapshot_at: params[2] });
      return [{ insertId: this._nextSnapId - 1 }, []];
    }

    // Global equity snapshot query (no user_id filter) - for public portfolio summary
    if (cmd.includes('FROM EQUITY_SNAPSHOTS') && cmd.includes('ORDER BY SNAPSHOT_AT DESC') && params.length === 0) {
      const rows = [...this.snapshots].sort((a, b) => new Date(b.snapshot_at) - new Date(a.snapshot_at)).slice(0, 1);
      return [rows, []];
    }

    if (cmd.includes('FROM EQUITY_SNAPSHOTS') && cmd.includes('ORDER BY SNAPSHOT_AT DESC')) {
      const rows = this.snapshots.filter(s => s.user_id === params[0]).sort((a, b) => new Date(b.snapshot_at) - new Date(a.snapshot_at)).slice(0, 1);
      return [rows, []];
    }

    if (cmd.includes('FROM EQUITY_SNAPSHOTS')) {
      const rows = this.snapshots.filter(s => s.user_id === params[0]).sort((a, b) => new Date(a.snapshot_at) - new Date(b.snapshot_at)).slice(0, 365);
      return [rows, []];
    }

    // Global trade stats queries (no user_id filter) - for public portfolio summary
    if (cmd.includes('COUNT(*)') && cmd.includes('SUM(PNL)') && cmd.includes("STATUS = 'CLOSED'") && params.length === 0) {
      const closed = this.trades.filter(t => t.status === 'CLOSED');
      const net_pnl = closed.reduce((a, t) => a + (parseFloat(t.pnl) || 0), 0);
      return [[{ total: closed.length, net_pnl }], []];
    }

    if (cmd.includes('COUNT(*)') && cmd.includes("STATUS = 'OPEN'") && params.length === 0) {
      const count = this.trades.filter(t => t.status === 'OPEN').length;
      return [[{ open_count: count }], []];
    }

    if (cmd.includes('COUNT(*)') && cmd.includes('PNL > 0') && params.length === 0) {
      const wins = this.trades.filter(t => t.status === 'CLOSED' && parseFloat(t.pnl) > 0).length;
      return [[{ wins }], []];
    }

    // -- SCAN CACHE --
    if (cmd.includes('REPLACE INTO SCAN_CACHE') || (cmd.includes('INSERT') && cmd.includes('SCAN_CACHE'))) {
      this.scanCache = { id: 1, scan_json: params[0], updated_at: new Date() };
      return [{ affectedRows: 1 }, []];
    }

    if (cmd.includes('FROM SCAN_CACHE')) {
      return [this.scanCache ? [this.scanCache] : [], []];
    }

    // -- WALLET LINK CODES (phone/QR flow) --
    if (cmd.includes('INTO WALLET_LINK_CODES')) {          // REPLACE INTO ... (code, user_id, expires_at)
      this.walletLinkCodes[params[0]] = { code: params[0], user_id: params[1], expires_at: params[2] };
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('DELETE FROM WALLET_LINK_CODES')) {
      if (cmd.includes('EXPIRES_AT <')) {                  // prune expired
        const cutoff = params[0];
        for (const k of Object.keys(this.walletLinkCodes)) {
          if (this.walletLinkCodes[k].expires_at < cutoff) delete this.walletLinkCodes[k];
        }
      } else {
        delete this.walletLinkCodes[params[0]];
      }
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM WALLET_LINK_CODES')) {
      const rec = this.walletLinkCodes[params[0]];
      return [rec ? [{ user_id: rec.user_id, expires_at: rec.expires_at }] : [], []];
    }

    // -- WALLET LINK NONCES (phone/QR flow) --
    if (cmd.includes('INTO WALLET_LINK_NONCES')) {         // REPLACE INTO ... (address, message, expires_at)
      this.walletLinkNonces[params[0]] = { address: params[0], message: params[1], expires_at: params[2] };
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('DELETE FROM WALLET_LINK_NONCES')) {
      if (cmd.includes('EXPIRES_AT <')) {
        const cutoff = params[0];
        for (const k of Object.keys(this.walletLinkNonces)) {
          if (this.walletLinkNonces[k].expires_at < cutoff) delete this.walletLinkNonces[k];
        }
      } else {
        delete this.walletLinkNonces[params[0]];
      }
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM WALLET_LINK_NONCES')) {
      const rec = this.walletLinkNonces[params[0]];
      return [rec ? [{ message: rec.message, expires_at: rec.expires_at }] : [], []];
    }

    return [[], []];
  }
}

if (!USE_MYSQL) {
  memDb = new MemoryDB();
  pool = memDb;
  console.log('Using in-memory database (no DATABASE_URL found)');
}

async function migrate() {
  if (USE_MYSQL) {
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        email VARCHAR(255) UNIQUE NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        plan VARCHAR(50) DEFAULT 'free',
        telegram_linked BOOLEAN DEFAULT FALSE,
        telegram_id VARCHAR(32) DEFAULT NULL,
        link_token VARCHAR(100),
        link_token_expires TIMESTAMP NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);
    // Back-fill telegram_id on pre-existing deployments (CREATE TABLE IF NOT
    // EXISTS won't add it). Ignore the duplicate-column error if already present.
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN telegram_id VARCHAR(32) DEFAULT NULL');
    } catch (e) { /* column already exists — fine */ }
    // 2FA (MH1): TOTP secret, enabled flag, hashed one-time backup codes.
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN totp_secret VARCHAR(64) DEFAULT NULL');
    } catch (e) { /* exists */ }
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN totp_enabled TINYINT NOT NULL DEFAULT 0');
    } catch (e) { /* exists */ }
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN totp_backup_codes TEXT DEFAULT NULL');
    } catch (e) { /* exists */ }
    // OAuth: google_id + avatar_url, and password_hash must be nullable
    // (OAuth accounts have no password). Each guarded — ignore if present.
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN google_id VARCHAR(64) DEFAULT NULL');
    } catch (e) { /* exists */ }
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN avatar_url VARCHAR(512) DEFAULT NULL');
    } catch (e) { /* exists */ }
    // Social OAuth expansion: Discord + X (Twitter) provider identities.
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN discord_id VARCHAR(64) DEFAULT NULL');
    } catch (e) { /* exists */ }
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN x_id VARCHAR(64) DEFAULT NULL');
    } catch (e) { /* exists */ }
    // Multi-venue exchange keys: exchange_status becomes one row per
    // (user, venue) so several connected exchanges coexist. DROP+ADD in one
    // statement is idempotent — re-running recreates the same composite key.
    try {
      await pool.execute('ALTER TABLE exchange_status DROP PRIMARY KEY, ADD PRIMARY KEY (user_id, exchange)');
    } catch (e) { /* already composite / column constraints — fine */ }
    // Alerts 2.0: recurring mode + cooldown on pre-existing deployments.
    try {
      await pool.execute("ALTER TABLE user_alerts ADD COLUMN mode VARCHAR(12) NOT NULL DEFAULT 'once'");
    } catch (e) { /* exists */ }
    try {
      await pool.execute('ALTER TABLE user_alerts ADD COLUMN cooldown_min INT NOT NULL DEFAULT 60');
    } catch (e) { /* exists */ }
    // Self-custody sign-in: the user's EVM wallet address (lowercased, unique).
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN wallet_address VARCHAR(42) DEFAULT NULL');
    } catch (e) { /* exists */ }
    // Solana WATCH address (base58, read-only mirror — no signing surface).
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN sol_address VARCHAR(48) DEFAULT NULL');
    } catch (e) { /* exists */ }
    try {
      await pool.execute('CREATE UNIQUE INDEX idx_users_wallet_address ON users (wallet_address)');
    } catch (e) { /* index exists */ }
    try {
      await pool.execute('ALTER TABLE users MODIFY COLUMN password_hash VARCHAR(255) NULL');
    } catch (e) { /* already nullable */ }
    // Account management: email verification + password reset. Tokens are
    // stored HASHED (sha256 hex) — a DB leak can't be replayed to reset/verify.
    // Each ALTER is guarded so re-running migrate() on a live DB is a no-op.
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN email_verified BOOLEAN DEFAULT FALSE');
    } catch (e) { /* exists */ }
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN verify_token VARCHAR(100) DEFAULT NULL');
    } catch (e) { /* exists */ }
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN verify_token_expires TIMESTAMP NULL');
    } catch (e) { /* exists */ }
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN reset_token VARCHAR(100) DEFAULT NULL');
    } catch (e) { /* exists */ }
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN reset_token_expires TIMESTAMP NULL');
    } catch (e) { /* exists */ }
    // Invite / referral: each user's own share code + who referred them.
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN referral_code VARCHAR(16) DEFAULT NULL');
    } catch (e) { /* exists */ }
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN referred_by INT DEFAULT NULL');
    } catch (e) { /* exists */ }
    try {
      await pool.execute('CREATE UNIQUE INDEX idx_users_referral_code ON users (referral_code)');
    } catch (e) { /* index exists */ }
    // Leaderboard opt-in: an anonymous display handle (NULL = not on the board).
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN leaderboard_handle VARCHAR(24) DEFAULT NULL');
    } catch (e) { /* exists */ }
    try {
      await pool.execute('CREATE UNIQUE INDEX idx_users_leaderboard_handle ON users (leaderboard_handle)');
    } catch (e) { /* index exists */ }
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS trades (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        symbol VARCHAR(30) NOT NULL,
        direction VARCHAR(10) NOT NULL,
        entry_price DECIMAL(18,8) NOT NULL,
        exit_price DECIMAL(18,8),
        size_usd DECIMAL(14,2) NOT NULL,
        pnl DECIMAL(14,2),
        fees DECIMAL(14,2) DEFAULT 0,
        status VARCHAR(10) DEFAULT 'OPEN',
        pattern VARCHAR(100),
        stop_loss DECIMAL(18,8),
        take_profit DECIMAL(18,8),
        opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        closed_at TIMESTAMP NULL,
        notes TEXT DEFAULT NULL,
        INDEX idx_user_status (user_id, status),
        INDEX idx_user_opened (user_id, opened_at)
      )
    `);
    // Back-fill notes on pre-existing deployments (CREATE TABLE IF NOT EXISTS
    // won't add it). Ignore the duplicate-column error if already present.
    try {
      await pool.execute('ALTER TABLE trades ADD COLUMN notes TEXT DEFAULT NULL');
    } catch (e) { /* column already exists — fine */ }
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS equity_snapshots (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        equity DECIMAL(14,2) NOT NULL,
        snapshot_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_user_snap (user_id, snapshot_at)
      )
    `);
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS scan_cache (
        id INT PRIMARY KEY DEFAULT 1,
        scan_json LONGTEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
      )
    `);
    // Phone/QR wallet-link: short-lived single-use codes + sign nonces persisted
    // so the flow survives a web restart or a second instance between "show QR"
    // and "phone signs" (see lib/wallet_link_store). expires_at is epoch ms.
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS wallet_link_codes (
        code VARCHAR(32) PRIMARY KEY,
        user_id VARCHAR(64) NOT NULL,
        expires_at BIGINT NOT NULL
      )
    `);
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS wallet_link_nonces (
        address VARCHAR(64) PRIMARY KEY,
        message TEXT NOT NULL,
        expires_at BIGINT NOT NULL
      )
    `);
    // Global signal stream (every generated signal, taken or not). signal_key is
    // a stable per-signal id from the bot so re-syncs UPSERT (update outcome)
    // instead of duplicating. pnl/status are filled when the signal resolves.
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS signals (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        signal_key VARCHAR(128) NOT NULL UNIQUE,
        symbol VARCHAR(32) NOT NULL,
        direction VARCHAR(8) NOT NULL,
        confidence DECIMAL(6,4) DEFAULT 0,
        score DECIMAL(10,4) DEFAULT 0,
        pattern VARCHAR(64) DEFAULT NULL,
        regime VARCHAR(32) DEFAULT NULL,
        entry_price DECIMAL(20,8) DEFAULT 0,
        stop_loss DECIMAL(20,8) DEFAULT 0,
        take_profit DECIMAL(20,8) DEFAULT 0,
        rr DECIMAL(10,4) DEFAULT 0,
        thesis TEXT DEFAULT NULL,
        status VARCHAR(16) DEFAULT 'NEW',
        pnl DECIMAL(20,8) DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        resolved_at TIMESTAMP NULL DEFAULT NULL,
        INDEX idx_created (created_at),
        INDEX idx_symbol (symbol)
      )
    `);
    // Web-push subscriptions (opt-in, per browser). endpoint is the unique
    // key so re-subscribing the same browser updates instead of duplicating.
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS push_subscriptions (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        endpoint VARCHAR(500) NOT NULL UNIQUE,
        keys_json TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_push_user (user_id)
      )
    `);
    // Strategy-Agent follows (Marketplace Phase 3). A user "follows" a listed
    // agent to surface its live would-take picks and (opt-in) milestone alerts.
    // Follow-only — nothing here moves real funds; copying is user-initiated and
    // paper-only via the normal trade ticket. agent_id is a catalogue slug.
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS copy_subscriptions (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        agent_id VARCHAR(64) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uniq_copy_user_agent (user_id, agent_id),
        INDEX idx_copy_user (user_id)
      )
    `);
    // Per-user agent profile: the user's OWN risk preference (display + chat
    // context only — never touches the operator bot's global stance), pinned
    // watchlist, and UI prefs. JSON columns are validated/whitelisted by
    // routes/profile.js before write.
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS user_profiles (
        user_id INT PRIMARY KEY,
        risk_pref VARCHAR(16) DEFAULT NULL,
        watchlist TEXT DEFAULT NULL,
        prefs TEXT DEFAULT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
      )
    `);
    // Weekly agent letters — one per completed ISO week, composed entirely
    // from recorded data (lib/letter.js). week_key is the natural key.
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS agent_letters (
        id INT AUTO_INCREMENT PRIMARY KEY,
        week_key VARCHAR(10) NOT NULL UNIQUE,
        generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        letter_json LONGTEXT NOT NULL
      )
    `);
    // Custom user alerts ("tell me when BTC drops below 100k"). One-shot
    // tripwires: the alert engine (lib/alerts.js) evaluates active rows
    // against public tickers and deactivates a row as it trips. Notification
    // only — an alert can never place or touch a trade.
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS user_alerts (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        symbol VARCHAR(24) NOT NULL,
        metric VARCHAR(20) NOT NULL DEFAULT 'price',
        op VARCHAR(2) NOT NULL,
        threshold DOUBLE NOT NULL,
        mode VARCHAR(12) NOT NULL DEFAULT 'once',
        cooldown_min INT NOT NULL DEFAULT 60,
        active TINYINT NOT NULL DEFAULT 1,
        trigger_price DOUBLE DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        triggered_at TIMESTAMP NULL DEFAULT NULL,
        INDEX idx_alerts_user (user_id),
        INDEX idx_alerts_active (active)
      )
    `);
    // User-authored marketplace strategies. A strategy is a CONFIG (intent-rule
    // chips + prose), never a performance claim — no dollar/stat columns (§4).
    // `rules` is a JSON array of {type,value}; `visibility` gates the public
    // marketplace. Saving/publishing never touches a trade.
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS user_strategies (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        slug VARCHAR(64) NOT NULL,
        name VARCHAR(80) NOT NULL,
        tagline VARCHAR(160),
        how VARCHAR(600),
        icon VARCHAR(8),
        rules JSON NOT NULL,
        risk_label VARCHAR(24),
        regime VARCHAR(24),
        horizon VARCHAR(24),
        visibility VARCHAR(12) NOT NULL DEFAULT 'draft',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_strat_user (user_id),
        INDEX idx_strat_pub (visibility, slug)
      )
    `);
    // Bot-pushed intelligence reports (funding scan / arb tracker / parity /
    // yield radar) — single-row cache like scan_cache. The yield section is
    // operator-sensitive and only served to admin-plan users (routes/reports.js).
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS reports_cache (
        id INT PRIMARY KEY DEFAULT 1,
        reports_json LONGTEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
      )
    `);
    // Guardian Flight Recorder cache (single row): the bot pushes recent joined
    // decision records + the engine-verified hash-chain status. Read-only
    // provenance surface for the website — the authoritative ledger lives
    // bot-side in logs/audit_chain.jsonl.
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS flight_cache (
        id INT PRIMARY KEY DEFAULT 1,
        flight_json LONGTEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
      )
    `);
    // Admin-queued strategy-stance change (global, single in-flight row).
    // The bot pulls it, re-verifies the requester's tier is 'admin' against
    // its OWN UserStore, applies RUNTIME.strategy_mode, then acks (deletes).
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS pending_stance (
        id INT PRIMARY KEY DEFAULT 1,
        mode VARCHAR(16) NOT NULL,
        requested_by INT NOT NULL,
        telegram_id VARCHAR(32) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);
    // Public agent mind-stream feed (bot-pushed, SSE-rebroadcast). Bounded
    // ring: the sync route prunes to the newest ~500 rows. No user data —
    // operator-agent activity only, pre-sanitized bot-side (agent_feed.py).
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS agent_events (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        event_type VARCHAR(24) NOT NULL,
        severity VARCHAR(16) DEFAULT 'info',
        symbol VARCHAR(32) DEFAULT NULL,
        title VARCHAR(300) NOT NULL,
        body TEXT DEFAULT NULL,
        data_json TEXT DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_agent_events_created (created_at)
      )
    `);
    // Pending exchange-credential submissions. The website encrypts the keys
    // (AES-256-GCM, WEB_CREDS_KEY) into encrypted_payload; the bot PULLS pending
    // rows over the shared-secret channel, imports them into its own Fernet store
    // keyed by telegram_id, then the row is deleted. One in-flight request per
    // user (UPSERT). Raw keys are NEVER stored in plaintext.
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS pending_credentials (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL UNIQUE,
        telegram_id VARCHAR(32) NOT NULL,
        exchange VARCHAR(16) DEFAULT 'bitget',
        action VARCHAR(16) DEFAULT 'connect',
        encrypted_payload TEXT DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);
    // Per-user exchange connection status, set by the bot's ack after it imports
    // (connect) or removes (disconnect) the credentials. Drives the web UI badge.
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS exchange_status (
        user_id INT NOT NULL,
        exchange VARCHAR(16) NOT NULL DEFAULT 'bitget',
        connected BOOLEAN DEFAULT FALSE,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, exchange)
      )
    `);
    // Pending per-user live-control changes (flags/numbers, not secrets — no
    // encryption). The web queues a change; the bot pulls + applies it via the
    // UserStore (live on/off, per-trade margin cap, pause-to-paper), then acks.
    // NULL columns mean "leave unchanged". One in-flight request per user.
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS pending_controls (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL UNIQUE,
        telegram_id VARCHAR(32) NOT NULL,
        live_enabled TINYINT DEFAULT NULL,
        max_margin DECIMAL(20,2) DEFAULT NULL,
        paused TINYINT DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);
    // Current applied control state, written back by the bot's ack (the bot's
    // UserStore is the source of truth; this mirrors it for the web UI).
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS user_controls (
        user_id INT PRIMARY KEY,
        live_enabled BOOLEAN DEFAULT FALSE,
        max_margin DECIMAL(20,2) DEFAULT NULL,
        paused BOOLEAN DEFAULT FALSE,
        allowlisted BOOLEAN DEFAULT FALSE,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
      )
    `);
    // Emergency-stop flatten requests. Separate from pending_controls because the
    // bot processes it asynchronously (closes the user's live positions via THEIR
    // own executor) and must not clear the request until the close completes.
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS pending_flatten (
        user_id INT PRIMARY KEY,
        telegram_id VARCHAR(32) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);
    // Paper Trading Arena — virtual accounts for every registered user, no
    // exchange keys or bot gateway required. §4: virtual funds only; the public
    // leaderboard built on these shows percent return + opt-in handles only.
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS arena_accounts (
        user_id INT PRIMARY KEY,
        balance DOUBLE NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS arena_positions (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        symbol VARCHAR(20) NOT NULL,
        direction VARCHAR(5) NOT NULL,
        entry DOUBLE NOT NULL,
        margin DOUBLE NOT NULL,
        leverage INT NOT NULL,
        opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_arena_pos_user (user_id)
      )
    `);
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS arena_trades (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        symbol VARCHAR(20) NOT NULL,
        direction VARCHAR(5) NOT NULL,
        entry DOUBLE NOT NULL,
        exit_price DOUBLE NOT NULL,
        margin DOUBLE NOT NULL,
        leverage INT NOT NULL,
        pnl DOUBLE NOT NULL,
        reason VARCHAR(12) NOT NULL,
        opened_at TIMESTAMP NULL,
        closed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_arena_tr_user (user_id)
      )
    `);
    await pool.execute(`
      CREATE TABLE IF NOT EXISTS arena_seasons (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(60) NOT NULL,
        starts_at TIMESTAMP NOT NULL,
        ends_at TIMESTAMP NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);
  }
  // In-memory DB needs no migration
}

module.exports = { pool, migrate };
