"""Security and data-integrity regression tests for the handwriting portal."""

from __future__ import annotations

import io
import os
import re
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from werkzeug.security import check_password_hash

TEST_ROOT = tempfile.mkdtemp(prefix="reco-tests-")
os.environ["ADMIN_INITIAL_PASSWORD"] = "test-admin-password"
os.environ["FLASK_SECRET_KEY"] = "test-secret"
os.environ["FIREBASE_DATA_SYNC_ENABLED"] = "false"
os.environ["OCR_DB_PATH"] = str(Path(TEST_ROOT) / "import.db")
os.environ["OCR_UPLOAD_DIR"] = str(Path(TEST_ROOT) / "uploads")

from app import create_app, csv_safe, keep_alive_target  # noqa: E402
from src.database import init_db  # noqa: E402


class PortalTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="reco-app-", dir=TEST_ROOT))
        cls.db_path = cls.root / "app.db"
        cls.app = create_app(str(cls.root / "missing.pt"), "cpu", str(cls.db_path))
        cls.app.config.update(TESTING=True)

    def client(self):
        return self.app.test_client()

    def login(self, client, username: str, password: str):
        response = client.post("/api/auth/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()["user"]

    def admin_client(self):
        client = self.client()
        self.login(client, "admin", "test-admin-password")
        return client

    def create_user(self, username: str, password: str = "user-password") -> dict:
        response = self.admin_client().post(
            "/api/admin/users",
            json={"username": username, "password": password, "role": "user"},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        finally:
            conn.close()
        return {"id": int(row[0]), "username": username, "password": password}

    def add_history(self, user_id: int, prediction: str = "sample text") -> int:
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                """
                INSERT INTO detection_history
                    (user_id, file_name, prediction, confidence, source, filters_json, image_path)
                VALUES (?, 'note.png', ?, 0.9, 'tesseract', '{}', '')
                """,
                (user_id, prediction),
            )
            conn.commit()
            return int(cursor.lastrowid)
        finally:
            conn.close()

    def test_password_reset_route_is_gone(self):
        response = self.client().post(
            "/api/auth/reset-password",
            json={"username": "admin", "new_password": "attacker-password"},
        )
        self.assertEqual(response.status_code, 404)

    def test_anonymous_requests_are_rejected(self):
        self.assertEqual(self.client().get("/api/history").status_code, 401)
        self.assertEqual(self.client().get("/api/me").status_code, 401)

    def test_users_cannot_read_each_others_history(self):
        owner = self.create_user("owner-user")
        other = self.create_user("other-user")
        record_id = self.add_history(owner["id"])

        owner_client = self.client()
        self.login(owner_client, owner["username"], owner["password"])
        self.assertEqual(owner_client.get(f"/api/history/{record_id}").status_code, 200)

        other_client = self.client()
        self.login(other_client, other["username"], other["password"])
        self.assertEqual(other_client.get(f"/api/history/{record_id}").status_code, 403)
        self.assertEqual(other_client.delete(f"/api/history/{record_id}").status_code, 403)

    def test_last_admin_cannot_be_demoted(self):
        client = self.admin_client()
        admin_id = client.get("/api/me").get_json()["user"]["id"]
        response = client.put(f"/api/admin/users/{admin_id}", json={"role": "user"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("last admin", response.get_json()["error"])

    def test_csv_safe_blocks_formulas(self):
        for value in ("=1+1", "+1+1", "-1+1", "@SUM(A1:A2)"):
            self.assertEqual(csv_safe(value), "'" + value)
        self.assertEqual(csv_safe("plain text"), "plain text")
        self.assertEqual(csv_safe(42), 42)

    def test_history_is_paginated(self):
        user = self.create_user("page-user")
        for _ in range(5):
            self.add_history(user["id"], prediction="page test")

        client = self.client()
        self.login(client, user["username"], user["password"])
        payload = client.get("/api/history?limit=2&offset=1").get_json()
        self.assertEqual(len(payload["history"]), 2)
        self.assertEqual(payload["total"], 5)
        self.assertEqual(payload["limit"], 2)
        self.assertEqual(payload["offset"], 1)

    def test_prediction_file_count_is_capped(self):
        user = self.create_user("upload-user")
        client = self.client()
        self.login(client, user["username"], user["password"])

        files = [(io.BytesIO(b"not-an-image"), f"note_{index}.png") for index in range(21)]
        response = client.post(
            "/api/predict",
            data={"images": files},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("at most 20", response.get_json()["error"])

    def test_history_pdf_export_uses_file_backed_buffer(self):
        user = self.create_user("pdf-user")
        record_id = self.add_history(user["id"], prediction="pdf export")
        client = self.client()
        self.login(client, user["username"], user["password"])

        response = client.get(f"/api/history/{record_id}/export/pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[:4], b"%PDF")
        response.close()

    def test_prediction_rate_limit(self):
        user = self.create_user("rate-user")
        client = self.client()
        self.login(client, user["username"], user["password"])

        for _ in range(10):
            response = client.post("/api/predict", data={})
            self.assertEqual(response.status_code, 400)
        response = client.post("/api/predict", data={})
        self.assertEqual(response.status_code, 429)

    def test_admin_export_escapes_formulas(self):
        user = self.create_user("export-user")
        self.add_history(user["id"], prediction="=HYPERLINK(\"http://example.test\")")

        response = self.admin_client().get("/api/admin/export")
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.data)) as bundle:
            history_csv = bundle.read("detection_history.csv").decode("utf-8")
        self.assertIn("'=HYPERLINK", history_csv)
        response.close()

    def test_first_start_generates_a_random_admin_password(self):
        path = Path(TEST_ROOT) / "no-admin.db"
        with mock.patch.dict(os.environ, {"ADMIN_INITIAL_PASSWORD": ""}):
            with self.assertLogs("src.database", level="WARNING") as captured:
                init_db(path)

        match = re.search(r"one-time password: (\S+)", "\n".join(captured.output))
        self.assertIsNotNone(match)
        password = match.group(1)
        self.assertGreaterEqual(len(password), 12)
        self.assertNotEqual(password, "admin123")

        conn = sqlite3.connect(path)
        try:
            row = conn.execute("SELECT password_hash FROM users WHERE username = 'admin'").fetchone()
        finally:
            conn.close()
        self.assertTrue(check_password_hash(row[0], password))

    def test_legacy_database_migrates_firebase_uid(self):
        path = Path(TEST_ROOT) / "legacy.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL DEFAULT '',
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin', 'user')) DEFAULT 'user',
                avatar_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_login TEXT
            );
            INSERT INTO users (username, password_hash, role) VALUES ('admin', 'not-a-real-hash', 'admin');
            """
        )
        conn.commit()
        conn.close()

        init_db(path)

        conn = sqlite3.connect(path)
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        finally:
            conn.close()
        self.assertIn("firebase_uid", columns)


class KeepAliveTests(unittest.TestCase):
    def test_uses_render_external_url_by_default(self):
        with mock.patch.dict(
            os.environ,
            {
                "KEEP_ALIVE_ENABLED": "true",
                "KEEP_ALIVE_URL": "",
                "RENDER_EXTERNAL_URL": "https://portal.onrender.com/",
            },
        ):
            self.assertEqual(
                keep_alive_target(),
                ("https://portal.onrender.com/api/health", 240),
            )

    def test_explicit_url_wins_and_interval_clamps(self):
        with mock.patch.dict(
            os.environ,
            {
                "KEEP_ALIVE_URL": "https://example.test/api/health",
                "KEEP_ALIVE_INTERVAL_SECONDS": "1",
                "RENDER_EXTERNAL_URL": "https://ignored.test",
            },
        ):
            self.assertEqual(
                keep_alive_target(),
                ("https://example.test/api/health", 60),
            )

    def test_disabled_or_unconfigured(self):
        with mock.patch.dict(os.environ, {"KEEP_ALIVE_ENABLED": "false"}):
            self.assertIsNone(keep_alive_target())
        with mock.patch.dict(
            os.environ,
            {"KEEP_ALIVE_ENABLED": "true", "KEEP_ALIVE_URL": "", "RENDER_EXTERNAL_URL": ""},
        ):
            self.assertIsNone(keep_alive_target())


if __name__ == "__main__":
    unittest.main()
