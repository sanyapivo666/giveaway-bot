import sqlite3
from typing import Optional

from config import ADMIN_IDS


class Database:
    def __init__(self, db_path: str = "giveaway.db"):
        self.db_path = db_path
        self._init_db()

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id   INTEGER PRIMARY KEY,
                    username  TEXT,
                    joined_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS giveaways (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    title       TEXT NOT NULL,
                    description TEXT NOT NULL,
                    prize       TEXT NOT NULL,
                    end_date    TEXT NOT NULL,
                    is_active   INTEGER DEFAULT 1,
                    winner_id   INTEGER,
                    created_by  INTEGER NOT NULL,
                    created_at  TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS participants (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    giveaway_id  INTEGER NOT NULL REFERENCES giveaways(id),
                    user_id      INTEGER NOT NULL REFERENCES users(user_id),
                    base_tickets INTEGER DEFAULT 1,
                    joined_at    TEXT DEFAULT (datetime('now')),
                    UNIQUE(giveaway_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS shares (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    giveaway_id INTEGER NOT NULL REFERENCES giveaways(id),
                    sharer_id   INTEGER NOT NULL REFERENCES users(user_id),
                    referred_id INTEGER NOT NULL REFERENCES users(user_id),
                    shared_at   TEXT DEFAULT (datetime('now')),
                    UNIQUE(giveaway_id, referred_id)
                );

                CREATE TABLE IF NOT EXISTS invites (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    inviter_id  INTEGER NOT NULL REFERENCES users(user_id),
                    invited_id  INTEGER NOT NULL REFERENCES users(user_id),
                    invited_at  TEXT DEFAULT (datetime('now')),
                    UNIQUE(invited_id)
                );
            """)

    # ── Users ────────────────────────────────────────────────────────────────

    def add_user(self, user_id: int, username: Optional[str]):
        with self._conn() as conn:
            conn.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?,?)", (user_id, username))
            conn.execute("UPDATE users SET username=? WHERE user_id=?", (username, user_id))

    def get_user(self, user_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            return dict(row) if row else None

    def get_total_users(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    # ── Invites ───────────────────────────────────────────────────────────────

    def track_invite(self, inviter_id: int, invited_id: int):
        """Record that inviter_id brought invited_id to the bot."""
        with self._conn() as conn:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO invites (inviter_id, invited_id) VALUES (?,?)",
                    (inviter_id, invited_id)
                )
            except Exception:
                pass

    def get_total_invites(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM invites").fetchone()[0]

    def get_top_inviters(self, limit: int = 10) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT u.user_id, u.username, COUNT(i.id) as invite_count
                   FROM invites i JOIN users u ON u.user_id = i.inviter_id
                   GROUP BY i.inviter_id ORDER BY invite_count DESC LIMIT ?""",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_top_sharers_global(self, limit: int = 10) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT u.user_id, u.username, COUNT(s.id) as total_shares
                   FROM shares s JOIN users u ON u.user_id = s.sharer_id
                   GROUP BY s.sharer_id ORDER BY total_shares DESC LIMIT ?""",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Giveaways ─────────────────────────────────────────────────────────────

    def create_giveaway(self, title, description, prize, end_date, created_by) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO giveaways (title, description, prize, end_date, created_by) VALUES (?,?,?,?,?)",
                (title, description, prize, end_date, created_by)
            )
            return cur.lastrowid

    def _enrich(self, conn, row) -> dict:
        g   = dict(row)
        gid = g["id"]
        placeholders = ",".join("?" for _ in ADMIN_IDS)
        g["participants_count"] = conn.execute(
            f"SELECT COUNT(*) FROM participants WHERE giveaway_id=? AND user_id NOT IN ({placeholders})",
            (gid, *ADMIN_IDS)
        ).fetchone()[0]
        g["total_tickets"] = self._calc_total_tickets(conn, gid)
        return g

    def _calc_total_tickets(self, conn, giveaway_id: int) -> int:
        placeholders = ",".join("?" for _ in ADMIN_IDS)
        base   = conn.execute(
            f"SELECT COALESCE(SUM(base_tickets),0) FROM participants WHERE giveaway_id=? AND user_id NOT IN ({placeholders})",
            (giveaway_id, *ADMIN_IDS)
        ).fetchone()[0]
        shares = conn.execute(
            f"SELECT COUNT(*) FROM shares WHERE giveaway_id=? AND sharer_id NOT IN ({placeholders})",
            (giveaway_id, *ADMIN_IDS)
        ).fetchone()[0]
        return base + shares

    def get_active_giveaways(self) -> list:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM giveaways WHERE is_active=1 ORDER BY id DESC").fetchall()
            return [self._enrich(conn, r) for r in rows]

    def get_all_giveaways(self) -> list:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM giveaways ORDER BY id DESC").fetchall()
            return [self._enrich(conn, r) for r in rows]

    def get_giveaway(self, giveaway_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM giveaways WHERE id=?", (giveaway_id,)).fetchone()
            return self._enrich(conn, row) if row else None

    def toggle_giveaway(self, giveaway_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute("SELECT is_active FROM giveaways WHERE id=?", (giveaway_id,)).fetchone()
            if not cur:
                return False
            new = 0 if cur["is_active"] else 1
            conn.execute("UPDATE giveaways SET is_active=? WHERE id=?", (new, giveaway_id))
            return bool(new)

    def set_winner(self, giveaway_id: int, winner_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE giveaways SET winner_id=?, is_active=0 WHERE id=?",
                (winner_id, giveaway_id)
            )

    # ── Participants ──────────────────────────────────────────────────────────

    def join_giveaway(self, giveaway_id: int, user_id: int) -> str:
        with self._conn() as conn:
            g = conn.execute("SELECT is_active FROM giveaways WHERE id=?", (giveaway_id,)).fetchone()
            if not g or not g["is_active"]:
                return "inactive"
            if conn.execute(
                "SELECT id FROM participants WHERE giveaway_id=? AND user_id=?", (giveaway_id, user_id)
            ).fetchone():
                return "already"
            conn.execute("INSERT INTO participants (giveaway_id, user_id) VALUES (?,?)", (giveaway_id, user_id))
            return "joined"

    def get_user_tickets(self, giveaway_id: int, user_id: int) -> int:
        with self._conn() as conn:
            base = conn.execute(
                "SELECT base_tickets FROM participants WHERE giveaway_id=? AND user_id=?",
                (giveaway_id, user_id)
            ).fetchone()
            if not base:
                return 0
            shares = conn.execute(
                "SELECT COUNT(*) FROM shares WHERE giveaway_id=? AND sharer_id=?",
                (giveaway_id, user_id)
            ).fetchone()[0]
            return base["base_tickets"] + shares

    def get_total_tickets(self, giveaway_id: int) -> int:
        with self._conn() as conn:
            return self._calc_total_tickets(conn, giveaway_id)

    def get_user_shares(self, giveaway_id: int, user_id: int) -> int:
        with self._conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM shares WHERE giveaway_id=? AND sharer_id=?",
                (giveaway_id, user_id)
            ).fetchone()[0]

    def get_user_entries(self, user_id: int) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT p.*, g.title, g.is_active,
                   (SELECT COUNT(*) FROM shares s WHERE s.giveaway_id=p.giveaway_id AND s.sharer_id=p.user_id) as shares
                   FROM participants p JOIN giveaways g ON g.id=p.giveaway_id
                   WHERE p.user_id=? ORDER BY p.id DESC""",
                (user_id,)
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["total_tickets"] = self._calc_total_tickets(conn, d["giveaway_id"])
                d["tickets"]       = d["base_tickets"] + d["shares"]
                result.append(d)
            return result

    def get_participants_with_tickets(self, giveaway_id: int) -> list:
        with self._conn() as conn:
            placeholders = ",".join("?" for _ in ADMIN_IDS)
            participants = conn.execute(
                f"SELECT user_id, base_tickets FROM participants WHERE giveaway_id=? AND user_id NOT IN ({placeholders})",
                (giveaway_id, *ADMIN_IDS)
            ).fetchall()
            result = []
            for p in participants:
                shares = conn.execute(
                    "SELECT COUNT(*) FROM shares WHERE giveaway_id=? AND sharer_id=?",
                    (giveaway_id, p["user_id"])
                ).fetchone()[0]
                result.append({"user_id": p["user_id"], "tickets": p["base_tickets"] + shares})
            return result

    # ── Shares ────────────────────────────────────────────────────────────────

    def credit_share(self, giveaway_id: int, sharer_id: int, referred_id: int) -> bool:
        with self._conn() as conn:
            g = conn.execute("SELECT is_active FROM giveaways WHERE id=?", (giveaway_id,)).fetchone()
            if not g or not g["is_active"]:
                return False
            try:
                conn.execute(
                    "INSERT INTO shares (giveaway_id, sharer_id, referred_id) VALUES (?,?,?)",
                    (giveaway_id, sharer_id, referred_id)
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def get_shares_leaderboard(self, giveaway_id: int) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT u.user_id, u.username,
                   COUNT(s.id) as shares,
                   COALESCE(p.base_tickets, 0) as base_tickets,
                   COUNT(s.id) as share_tickets
                   FROM shares s
                   JOIN users u ON u.user_id = s.sharer_id
                   LEFT JOIN participants p ON p.giveaway_id=s.giveaway_id AND p.user_id=s.sharer_id
                   WHERE s.giveaway_id=?
                   GROUP BY s.sharer_id ORDER BY shares DESC LIMIT 50""",
                (giveaway_id,)
            ).fetchall()
            return [dict(r) for r in rows]


db = Database()
