"""
RUNECLAW -- Multi-user database layer.
File: bot/db/models.py

Single SQLite file at data/runeclaw.db (same volume mounted by bot + api_bridge).
No extra dependencies -- uses Python stdlib sqlite3.

Tables:
  users          -- registered accounts (email + hashed password)
  user_telegram  -- links a user to their Telegram chat_id after /link
  user_settings  -- per-user risk params, LLM key, paper balance
  user_portfolio -- per-user paper trading state (JSON blob)
  link_tokens    -- short-lived tokens for the website -> bot link flow
"""

from __future__ import annotations
import hashlib, hmac, os, secrets, sqlite3, time, json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DB_PATH = Path(os.getenv("DB_PATH", "data/runeclaw.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# -- Schema ----------------------------------------------------------------

SCHEMA = """\
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT    UNIQUE NOT NULL,
    password_hash TEXT    NOT NULL,
    created_at    INTEGER NOT NULL DEFAULT (unixepoch()),
    plan          TEXT    NOT NULL DEFAULT 'free',
    is_active     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS user_telegram (
    user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    chat_id    TEXT    UNIQUE NOT NULL,
    username   TEXT,
    linked_at  INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id              INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    paper_balance        REAL    NOT NULL DEFAULT 10000,
    max_daily_loss_pct   REAL    NOT NULL DEFAULT 5.0,
    max_position_pct     REAL    NOT NULL DEFAULT 2.0,
    max_open_positions   INTEGER NOT NULL DEFAULT 5,
    llm_provider         TEXT    NOT NULL DEFAULT 'gemini',
    llm_api_key          TEXT    NOT NULL DEFAULT '',
    notifications_on     INTEGER NOT NULL DEFAULT 1,
    scan_interval_sec    INTEGER NOT NULL DEFAULT 60
);

CREATE TABLE IF NOT EXISTS user_portfolio (
    user_id      INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    equity       REAL    NOT NULL DEFAULT 10000,
    daily_pnl    REAL    NOT NULL DEFAULT 0,
    positions    TEXT    NOT NULL DEFAULT '[]',
    trade_history TEXT   NOT NULL DEFAULT '[]',
    updated_at   INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS link_tokens (
    token      TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL,
    used       INTEGER NOT NULL DEFAULT 0
);

-- NEWS-2 (BYON): a user's own paid news-provider key, ENCRYPTED at rest.
-- Additive + idempotent (IF NOT EXISTS) so existing DBs pick it up on init with
-- no migration. Isolated from user_settings so the LLM-key row is untouched.
CREATE TABLE IF NOT EXISTS user_news_keys (
    user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    provider   TEXT    NOT NULL DEFAULT '',
    api_key    TEXT    NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL DEFAULT (unixepoch())
);

-- NEWS-3 (personal ingest): text a user CHOSE to share with their own agent
-- (a newsletter they received, an article excerpt they pasted). PRIVATE per
-- user, ENCRYPTED at rest, and NEVER redistributed or shown on any public /
-- community surface (§4). The platform never fetches this — the user supplies
-- it, so there is no paywalled-scraping path here. Additive + idempotent.
CREATE TABLE IF NOT EXISTS user_ingest_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT    NOT NULL DEFAULT '',
    body       TEXT    NOT NULL DEFAULT '',
    source     TEXT    NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_ingest_user ON user_ingest_notes(user_id, created_at);

CREATE INDEX IF NOT EXISTS idx_telegram_chat ON user_telegram(chat_id);
CREATE INDEX IF NOT EXISTS idx_tokens_user   ON link_tokens(user_id);
"""


@contextmanager
def get_db():
    """Yield a sqlite3 connection with WAL mode and foreign keys enabled."""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create tables if they don't exist."""
    with get_db() as db:
        db.executescript(SCHEMA)


# -- Password hashing (stdlib only) ----------------------------------------

def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return f"{salt}${h.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt, h = stored.split("$", 1)
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
        return hmac.compare_digest(check.hex(), h)
    except Exception:
        return False


# -- User CRUD --------------------------------------------------------------

@dataclass
class User:
    id: int
    email: str
    plan: str
    is_active: bool
    telegram_chat_id: Optional[str] = None


def create_user(email: str, password: str) -> int:
    """Create a new user. Returns user_id. Raises ValueError on duplicate or short password."""
    if len(password) < 10:
        raise ValueError("Password must be at least 10 characters")
    with get_db() as db:
        try:
            cur = db.execute(
                "INSERT INTO users (email, password_hash) VALUES (?, ?)",
                (email.lower().strip(), _hash_password(password)),
            )
            uid = cur.lastrowid
            db.execute("INSERT INTO user_settings (user_id) VALUES (?)", (uid,))
            db.execute("INSERT INTO user_portfolio (user_id) VALUES (?)", (uid,))
            return uid
        except sqlite3.IntegrityError:
            raise ValueError("Email already registered")


def authenticate_user(email: str, password: str) -> Optional[User]:
    """Verify credentials, return User or None."""
    with get_db() as db:
        row = db.execute(
            "SELECT id, email, password_hash, plan, is_active "
            "FROM users WHERE email = ?",
            (email.lower().strip(),),
        ).fetchone()
    if not row or not row["is_active"]:
        return None
    if not _verify_password(password, row["password_hash"]):
        return None
    return _load_user(row["id"])


def _load_user(user_id: int) -> Optional[User]:
    with get_db() as db:
        row = db.execute(
            "SELECT u.id, u.email, u.plan, u.is_active, t.chat_id "
            "FROM users u LEFT JOIN user_telegram t ON t.user_id = u.id "
            "WHERE u.id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    return User(
        id=row["id"],
        email=row["email"],
        plan=row["plan"],
        is_active=bool(row["is_active"]),
        telegram_chat_id=row["chat_id"],
    )


def get_user_by_chat_id(chat_id: str) -> Optional[User]:
    """Look up a user by their linked Telegram chat_id."""
    with get_db() as db:
        row = db.execute(
            "SELECT user_id FROM user_telegram WHERE chat_id = ?", (str(chat_id),)
        ).fetchone()
    if not row:
        return None
    return _load_user(row["user_id"])


def get_user_by_id(user_id: int) -> Optional[User]:
    return _load_user(user_id)


# -- Link token flow -------------------------------------------------------

LINK_TOKEN_TTL = 600  # 10 minutes


def create_link_token(user_id: int) -> str:
    """Generate a short-lived token the user pastes into the Telegram bot."""
    token = secrets.token_urlsafe(20)
    expires_at = int(time.time()) + LINK_TOKEN_TTL
    with get_db() as db:
        db.execute(
            "UPDATE link_tokens SET used=1 WHERE user_id=? AND used=0", (user_id,)
        )
        db.execute(
            "INSERT INTO link_tokens (token, user_id, expires_at) VALUES (?,?,?)",
            (token, user_id, expires_at),
        )
    return token


def consume_link_token(token: str) -> Optional[int]:
    """Validate + consume a link token atomically. Returns user_id if valid, None otherwise."""
    with get_db() as db:
        # Atomic: UPDATE ... WHERE used=0 AND not expired, then check rowcount
        db.execute(
            "UPDATE link_tokens SET used=1 "
            "WHERE token=? AND used=0 AND expires_at >= ?",
            (token, int(time.time())),
        )
        if db.execute("SELECT changes()").fetchone()[0] == 0:
            return None
        row = db.execute(
            "SELECT user_id FROM link_tokens WHERE token=?", (token,)
        ).fetchone()
        return row["user_id"] if row else None


def link_telegram(user_id: int, chat_id: str, username: str = "") -> bool:
    """Link a Telegram chat_id to a registered user."""
    with get_db() as db:
        try:
            db.execute(
                "INSERT INTO user_telegram (user_id, chat_id, username) VALUES (?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET chat_id=excluded.chat_id, "
                "username=excluded.username, linked_at=unixepoch()",
                (user_id, str(chat_id), username),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def unlink_telegram(user_id: int) -> None:
    with get_db() as db:
        db.execute("DELETE FROM user_telegram WHERE user_id=?", (user_id,))


# -- Per-user settings ------------------------------------------------------

@dataclass
class UserSettings:
    user_id: int
    paper_balance: float = 10_000
    max_daily_loss_pct: float = 5.0
    max_position_pct: float = 2.0
    max_open_positions: int = 5
    llm_provider: str = "gemini"
    llm_api_key: str = ""
    notifications_on: bool = True
    scan_interval_sec: int = 60


import logging as _logging
_log = _logging.getLogger(__name__)

# --- Per-user LLM API key encryption at rest ---------------------------------
# The llm_api_key column held the user's LLM provider key in PLAINTEXT. These
# are real secrets (provider quota / billing), so they are now Fernet-encrypted
# at rest using the SAME master key as the exchange-credential store
# (RUNECLAW_SECRETS_KEY > data/.exchange_secret.key). Encryption happens at the
# save/get boundary so the in-memory UserSettings.llm_api_key stays plaintext for
# callers. Legacy plaintext rows are read transparently and re-encrypted on the
# next save (no migration step needed).
_LLM_CIPHER = None


def _llm_cipher():
    global _LLM_CIPHER
    if _LLM_CIPHER is None:
        from cryptography.fernet import Fernet
        from bot.core.exchange_credentials import _load_or_create_master_key
        _LLM_CIPHER = Fernet(_load_or_create_master_key())
    return _LLM_CIPHER


def _encrypt_llm_key(plaintext: str) -> str:
    """Fernet-encrypt an LLM key for storage. Empty stays empty. Fail-CLOSED: if
    encryption is unavailable, store nothing rather than leak plaintext."""
    if not plaintext:
        return ""
    try:
        return _llm_cipher().encrypt(plaintext.encode()).decode()
    except Exception as exc:
        _log.error("LLM key encryption failed (%s) — not storing the key", exc)
        return ""


def _decrypt_llm_key(stored: str) -> str:
    """Decrypt a stored LLM key. A value that isn't valid Fernet ciphertext is
    assumed to be a legacy plaintext key and returned as-is (it will be
    re-encrypted on the next save)."""
    if not stored:
        return ""
    try:
        return _llm_cipher().decrypt(stored.encode()).decode()
    except Exception:
        # Legacy plaintext (pre-encryption) or unreadable — pass through.
        return stored


def settings_user_id(identity) -> Optional[int]:
    """Map a gateway/chat identity to the user_settings INTEGER key.

    Telegram ids are positive numbers ("12345" -> 12345). Web-only identities
    ("web:5") map to the NEGATIVE of the website user id (-5) — the two id
    spaces can never collide because Telegram ids are always positive.
    Returns None for anything else (no settings row is reachable)."""
    s = str(identity or "").strip()
    if s.isdigit():
        return int(s)
    if s.startswith("web:") and s[4:].isdigit():
        return -int(s[4:])
    return None


def ensure_settings_parent(uid: int) -> None:
    """Guarantee the users parent row exists for a settings write keyed by a
    MAPPED identity (settings_user_id: telegram id, or negative web id) —
    user_settings has a FK to users(id). The stub row carries a synthetic
    unique email and an EMPTY password hash, so nobody can ever authenticate
    against it; it exists only to satisfy the FK. (Rows created by real
    website signups are untouched — INSERT OR IGNORE.)"""
    with get_db() as db:
        db.execute(
            "INSERT OR IGNORE INTO users (id, email, password_hash) "
            "VALUES (?, ?, '')",
            (uid, f"identity:{uid}@bot.local"),
        )


def get_user_settings(user_id: int) -> UserSettings:
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM user_settings WHERE user_id=?", (user_id,)
        ).fetchone()
    if not row:
        return UserSettings(user_id=user_id)
    return UserSettings(
        user_id=row["user_id"],
        paper_balance=row["paper_balance"],
        max_daily_loss_pct=row["max_daily_loss_pct"],
        max_position_pct=row["max_position_pct"],
        max_open_positions=row["max_open_positions"],
        llm_provider=row["llm_provider"],
        llm_api_key=_decrypt_llm_key(row["llm_api_key"]),
        notifications_on=bool(row["notifications_on"]),
        scan_interval_sec=row["scan_interval_sec"],
    )


def save_user_settings(s: UserSettings) -> None:
    with get_db() as db:
        db.execute(
            "INSERT INTO user_settings VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "paper_balance=excluded.paper_balance, "
            "max_daily_loss_pct=excluded.max_daily_loss_pct, "
            "max_position_pct=excluded.max_position_pct, "
            "max_open_positions=excluded.max_open_positions, "
            "llm_provider=excluded.llm_provider, "
            "llm_api_key=excluded.llm_api_key, "
            "notifications_on=excluded.notifications_on, "
            "scan_interval_sec=excluded.scan_interval_sec",
            (
                s.user_id, s.paper_balance, s.max_daily_loss_pct,
                s.max_position_pct, s.max_open_positions,
                s.llm_provider, _encrypt_llm_key(s.llm_api_key),
                int(s.notifications_on), s.scan_interval_sec,
            ),
        )


# -- Per-user BYON news key (NEWS-2) -----------------------------------------

def get_user_news_key(user_id: int) -> tuple[str, str]:
    """Return ``(provider, api_key)`` for a user, or ``("", "")`` if none. The
    key is decrypted on the way out (reusing the LLM-key cipher)."""
    with get_db() as db:
        row = db.execute(
            "SELECT provider, api_key FROM user_news_keys WHERE user_id=?",
            (user_id,)).fetchone()
    if not row:
        return "", ""
    return (row["provider"] or ""), _decrypt_llm_key(row["api_key"] or "")


def save_user_news_key(user_id: int, provider: str, api_key: str) -> None:
    """Upsert a user's BYON provider + key. The key is ENCRYPTED at rest (fail-
    closed: a crypto failure stores nothing rather than leak plaintext)."""
    with get_db() as db:
        db.execute(
            "INSERT INTO user_news_keys (user_id, provider, api_key, updated_at) "
            "VALUES (?,?,?,unixepoch()) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "provider=excluded.provider, api_key=excluded.api_key, "
            "updated_at=excluded.updated_at",
            (user_id, str(provider or ""), _encrypt_llm_key(str(api_key or ""))))


def clear_user_news_key(user_id: int) -> None:
    """Forget a user's BYON key — they fall back to the public RSS radar."""
    with get_db() as db:
        db.execute("DELETE FROM user_news_keys WHERE user_id=?", (user_id,))


# -- Per-user personal ingest notes (NEWS-3) --------------------------------
# Text the user CHOSE to share with their own agent. PRIVATE per user, encrypted
# at rest, never redistributed. A per-user cap bounds storage.

INGEST_MAX_NOTES = 50
INGEST_MAX_BODY = 20000
INGEST_MAX_TITLE = 200


def add_user_ingest_note(user_id: int, title: str, body: str,
                         source: str = "") -> Optional[int]:
    """Store one shared note for a user (body ENCRYPTED at rest). Returns the
    new note id, or None when the body is empty. Oldest notes are pruned past
    INGEST_MAX_NOTES so a single user can't grow the table unbounded."""
    body = str(body or "").strip()[:INGEST_MAX_BODY]
    if not body:
        return None
    title = str(title or "").strip()[:INGEST_MAX_TITLE]
    source = str(source or "").strip()[:INGEST_MAX_TITLE]
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO user_ingest_notes (user_id, title, body, source, created_at) "
            "VALUES (?,?,?,?,unixepoch())",
            (user_id, title, _encrypt_llm_key(body), source))
        note_id = cur.lastrowid
        # Prune the oldest beyond the cap (per user).
        db.execute(
            "DELETE FROM user_ingest_notes WHERE user_id=? AND id NOT IN "
            "(SELECT id FROM user_ingest_notes WHERE user_id=? "
            " ORDER BY created_at DESC, id DESC LIMIT ?)",
            (user_id, user_id, INGEST_MAX_NOTES))
    return note_id


def list_user_ingest_notes(user_id: int, limit: int = 20) -> list[dict]:
    """A user's own shared notes, newest first, body DECRYPTED. Returns only
    THIS user's rows — never anyone else's."""
    limit = max(1, min(int(limit or 20), INGEST_MAX_NOTES))
    with get_db() as db:
        rows = db.execute(
            "SELECT id, title, body, source, created_at FROM user_ingest_notes "
            "WHERE user_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
            (user_id, limit)).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "title": r["title"] or "",
            "body": _decrypt_llm_key(r["body"] or ""),
            "source": r["source"] or "",
            "created_at": r["created_at"],
        })
    return out


def delete_user_ingest_note(user_id: int, note_id: int) -> bool:
    """Delete ONE of the user's own notes (scoped by user_id so a caller can
    never delete another user's row). True if a row was removed."""
    with get_db() as db:
        cur = db.execute(
            "DELETE FROM user_ingest_notes WHERE user_id=? AND id=?",
            (user_id, int(note_id)))
        return cur.rowcount > 0


def clear_user_ingest_notes(user_id: int) -> int:
    """Forget ALL of a user's shared notes. Returns the count removed."""
    with get_db() as db:
        cur = db.execute(
            "DELETE FROM user_ingest_notes WHERE user_id=?", (user_id,))
        return cur.rowcount


# -- Per-user portfolio -----------------------------------------------------

def get_user_portfolio(user_id: int) -> dict:
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM user_portfolio WHERE user_id=?", (user_id,)
        ).fetchone()
    if not row:
        return {"equity": 10000, "daily_pnl": 0, "positions": [], "trade_history": []}
    return {
        "equity":        row["equity"],
        "daily_pnl":     row["daily_pnl"],
        "positions":     json.loads(row["positions"]),
        "trade_history": json.loads(row["trade_history"]),
    }


def save_user_portfolio(user_id: int, equity: float, daily_pnl: float,
                        positions: list, trade_history: list) -> None:
    th = trade_history[-200:]
    with get_db() as db:
        db.execute(
            "INSERT INTO user_portfolio VALUES (?,?,?,?,?,unixepoch()) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "equity=excluded.equity, daily_pnl=excluded.daily_pnl, "
            "positions=excluded.positions, trade_history=excluded.trade_history, "
            "updated_at=unixepoch()",
            (user_id, equity, daily_pnl, json.dumps(positions), json.dumps(th)),
        )


# -- Admin helpers -----------------------------------------------------------

def list_users(limit: int = 100) -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            "SELECT u.id, u.email, u.plan, u.created_at, t.chat_id, t.username "
            "FROM users u LEFT JOIN user_telegram t ON t.user_id=u.id "
            "ORDER BY u.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def user_count() -> dict:
    with get_db() as db:
        total = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        linked = db.execute("SELECT COUNT(*) FROM user_telegram").fetchone()[0]
    return {"total": total, "linked": linked, "unlinked": total - linked}


# Initialise schema on import
init_db()
