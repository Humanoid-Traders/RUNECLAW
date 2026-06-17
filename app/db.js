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
  }

  // Minimal query interface matching mysql2 pool.execute() return format
  async execute(sql, params = []) {
    const cmd = sql.trim().toUpperCase();

    if (cmd.startsWith('CREATE TABLE')) return [[], []];

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
    if (cmd.includes('INSERT INTO EQUITY_SNAPSHOTS')) {
      this.snapshots.push({ id: this._nextSnapId++, user_id: params[0], equity: params[1], snapshot_at: params[2] });
      return [{ insertId: this._nextSnapId - 1 }, []];
    }

    if (cmd.includes('FROM EQUITY_SNAPSHOTS')) {
      const rows = this.snapshots.filter(s => s.user_id === params[0]).sort((a, b) => new Date(a.snapshot_at) - new Date(b.snapshot_at)).slice(0, 365);
      return [rows, []];
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
  }
  // In-memory DB needs no migration
}

module.exports = { pool, migrate };
