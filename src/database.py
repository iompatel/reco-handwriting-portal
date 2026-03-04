from __future__ import annotations

import sqlite3
from pathlib import Path

from werkzeug.security import generate_password_hash


def connect_db(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | Path) -> None:
    conn = connect_db(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            full_name TEXT NOT NULL DEFAULT '',
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin', 'user')) DEFAULT 'user',
            avatar_path TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_login TEXT
        );

        CREATE TABLE IF NOT EXISTS user_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            login_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            logout_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS detection_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            file_name TEXT NOT NULL,
            prediction TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.0,
            source TEXT NOT NULL DEFAULT 'rnn',
            filters_json TEXT NOT NULL DEFAULT '{}',
            image_path TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS admin_activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            target_type TEXT NOT NULL DEFAULT '',
            target_id TEXT NOT NULL DEFAULT '',
            details TEXT NOT NULL DEFAULT '',
            ip_address TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(admin_user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_history_user_created
          ON detection_history(user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_sessions_active_seen
          ON user_sessions(is_active, last_seen DESC);
        CREATE INDEX IF NOT EXISTS idx_admin_activity_created
          ON admin_activity_logs(created_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_admin_activity_admin_created
          ON admin_activity_logs(admin_user_id, created_at DESC);
        """
    )

    history_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(detection_history)").fetchall()
    }
    if "image_path" not in history_columns:
        conn.execute(
            "ALTER TABLE detection_history ADD COLUMN image_path TEXT NOT NULL DEFAULT ''"
        )

    user_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(users)").fetchall()
    }
    if "avatar_path" not in user_columns:
        conn.execute(
            "ALTER TABLE users ADD COLUMN avatar_path TEXT NOT NULL DEFAULT ''"
        )

    admin_exists = conn.execute(
        "SELECT id FROM users WHERE role='admin' LIMIT 1"
    ).fetchone()
    if not admin_exists:
        conn.execute(
            """
            INSERT INTO users (username, full_name, password_hash, role)
            VALUES (?, ?, ?, 'admin')
            """,
            ("admin", "System Admin", generate_password_hash("admin123")),
        )

    conn.commit()
    conn.close()
