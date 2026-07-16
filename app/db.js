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
    this.pendingCreds = [];   // pending_credentials (UPSERT by user_id)
    this.exchangeStatus = {}; // user_id -> { connected }
    this.pendingControls = []; // pending_controls (UPSERT by user_id)
    this.userControls = {};   // user_id -> { live_enabled, max_margin, paused, allowlisted }
    this.pendingFlatten = []; // pending_flatten (UPSERT by user_id)
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
      // params: user_id, exchange(venue), connected
      this.exchangeStatus[params[0]] = { exchange: params[1] || 'bitget', connected: !!params[2] };
      return [{ affectedRows: 1 }, []];
    }
    if (cmd.includes('FROM EXCHANGE_STATUS')) {
      const s = this.exchangeStatus[params[0]];
      return [s ? [{ connected: s.connected, exchange: s.exchange || 'bitget' }] : [], []];
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
    // Self-custody sign-in: the user's EVM wallet address (lowercased, unique).
    try {
      await pool.execute('ALTER TABLE users ADD COLUMN wallet_address VARCHAR(42) DEFAULT NULL');
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
        user_id INT PRIMARY KEY,
        exchange VARCHAR(16) DEFAULT 'bitget',
        connected BOOLEAN DEFAULT FALSE,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
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
  }
  // In-memory DB needs no migration
}

module.exports = { pool, migrate };
