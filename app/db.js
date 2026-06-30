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

    // -- USERS --
    if (cmd.includes('INSERT INTO USERS')) {
      const exists = this.users.find(u => u.email === params[0]);
      if (exists) {
        const err = new Error('Duplicate entry'); err.code = 'ER_DUP_ENTRY'; throw err;
      }
      const user = { id: this._nextUserId++, email: params[0], password_hash: params[1], plan: 'free', telegram_linked: false, link_token: null, link_token_expires: null, created_at: new Date() };
      this.users.push(user);
      return [{ insertId: user.id }, []];
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
        // Consume token: UPDATE users SET link_token=NULL, ..., telegram_linked=TRUE WHERE id=?
        const user = this.users.find(u => u.id === params[0]);
        if (user) { user.link_token = null; user.link_token_expires = null; user.telegram_linked = true; }
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

    // -- TRADES --
    if (cmd.includes('DELETE FROM TRADES') && cmd.includes('USER_ID')) {
      if (cmd.includes('LIMIT 1')) {
        // Delete one open trade by symbol
        const idx = this.trades.findIndex(t => t.user_id === params[0] && t.symbol === params[1] && t.status === 'OPEN');
        if (idx >= 0) this.trades.splice(idx, 1);
      } else {
        this.trades = this.trades.filter(t => t.user_id !== params[0]);
      }
      return [{ affectedRows: 0 }, []];
    }

    if (cmd.includes('INSERT INTO TRADES')) {
      const trade = { id: this._nextTradeId++ };
      // Parse based on param count
      if (params.length === 12) {
        // Closed trade insert
        Object.assign(trade, { user_id: params[0], symbol: params[1], direction: params[2], entry_price: params[3], exit_price: params[4], size_usd: params[5], pnl: params[6], fees: params[7], status: 'CLOSED', pattern: params[8], opened_at: params[9], closed_at: params[10] });
      } else if (params.length === 10) {
        // Open trade insert
        Object.assign(trade, { user_id: params[0], symbol: params[1], direction: params[2], entry_price: params[3], size_usd: params[4], fees: params[5], status: 'OPEN', pattern: params[6], stop_loss: params[7], take_profit: params[8], opened_at: new Date() });
      }
      this.trades.push(trade);
      return [{ insertId: trade.id }, []];
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
      const count = this.trades.filter(t => t.user_id === params[0] && t.status === params[1]).length;
      return [[{ total: count }], []];
    }

    if (cmd.includes('SELECT PNL, SIZE_USD')) {
      const rows = this.trades.filter(t => t.user_id === params[0] && t.status === params[1]).sort((a, b) => new Date(a.closed_at) - new Date(b.closed_at));
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
        link_token VARCHAR(100),
        link_token_expires TIMESTAMP NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);
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
        INDEX idx_user_status (user_id, status),
        INDEX idx_user_opened (user_id, opened_at)
      )
    `);
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
  }
  // In-memory DB needs no migration
}

module.exports = { pool, migrate };
