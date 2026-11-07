"""Security and data-integrity regression tests for the handwriting portal."""

from __future__ import annotations

import io
import math
import os
import re
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from PIL import Image
from werkzeug.security import check_password_hash

TEST_ROOT = tempfile.mkdtemp(prefix="reco-tests-")
os.environ["ADMIN_INITIAL_PASSWORD"] = "test-admin-password"
os.environ["FLASK_SECRET_KEY"] = "test-secret"
os.environ["FIREBASE_DATA_SYNC_ENABLED"] = "false"
os.environ["OCR_DB_PATH"] = str(Path(TEST_ROOT) / "import.db")
os.environ["OCR_UPLOAD_DIR"] = str(Path(TEST_ROOT) / "uploads")

import app as app_module  # noqa: E402
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

    def png_file(self, width: int = 10, height: int = 10, name: str = "note.png"):
        payload = io.BytesIO()
        Image.new("RGB", (width, height), "white").save(payload, format="PNG")
        payload.seek(0)
        return payload, name

    def set_app_config(self, key: str, value: object) -> None:
        old_value = self.app.config.get(key)
        self.app.config[key] = value
        self.addCleanup(self.app.config.__setitem__, key, old_value)

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

    def test_logout_revokes_a_copied_session_cookie(self):
        user = self.create_user("session-replay-user")
        owner = self.client()
        self.login(owner, user["username"], user["password"])
        cookie = owner.get_cookie(self.app.config["SESSION_COOKIE_NAME"])
        self.assertIsNotNone(cookie)

        copied_cookie = cookie.value
        self.assertEqual(owner.post("/api/auth/logout").status_code, 200)

        replay = self.client()
        replay.set_cookie(self.app.config["SESSION_COOKIE_NAME"], copied_cookie)
        self.assertEqual(replay.get("/api/me").status_code, 401)

    def test_session_token_blocks_cookie_replay_after_id_reuse(self):
        root = Path(tempfile.mkdtemp(prefix="id-reuse-", dir=TEST_ROOT))
        db_path = root / "app.db"
        app = create_app(str(root / "missing.pt"), "cpu", str(db_path))
        app.config.update(TESTING=True)
        admin = app.test_client()
        self.assertEqual(
            admin.post(
                "/api/auth/login",
                json={"username": "admin", "password": "test-admin-password"},
            ).status_code,
            200,
        )
        self.assertEqual(
            admin.post(
                "/api/admin/users",
                json={"username": "id-reuse-old", "password": "user-password", "role": "user"},
            ).status_code,
            200,
        )
        conn = sqlite3.connect(db_path)
        try:
            old_user_id = conn.execute(
                "SELECT id FROM users WHERE username = 'id-reuse-old'"
            ).fetchone()[0]
        finally:
            conn.close()

        old_client = app.test_client()
        self.assertEqual(
            old_client.post(
                "/api/auth/login",
                json={"username": "id-reuse-old", "password": "user-password"},
            ).status_code,
            200,
        )
        old_cookie = old_client.get_cookie(app.config["SESSION_COOKIE_NAME"]).value
        self.assertEqual(admin.delete(f"/api/admin/users/{old_user_id}").status_code, 200)

        conn = sqlite3.connect(db_path)
        try:
            conn.execute("UPDATE sqlite_sequence SET seq = 1 WHERE name = 'users'")
            conn.execute("UPDATE sqlite_sequence SET seq = 1 WHERE name = 'user_sessions'")
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(
            admin.post(
                "/api/admin/users",
                json={"username": "id-reuse-new", "password": "user-password", "role": "user"},
            ).status_code,
            200,
        )
        replacement_client = app.test_client()
        self.assertEqual(
            replacement_client.post(
                "/api/auth/login",
                json={"username": "id-reuse-new", "password": "user-password"},
            ).status_code,
            200,
        )
        conn = sqlite3.connect(db_path)
        try:
            replacement_id = conn.execute(
                "SELECT id FROM users WHERE username = 'id-reuse-new'"
            ).fetchone()[0]
            replacement_session_id = conn.execute(
                "SELECT id FROM user_sessions WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (replacement_id,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(replacement_id, old_user_id)
        self.assertEqual(replacement_session_id, 2)

        replay = app.test_client()
        replay.set_cookie(app.config["SESSION_COOKIE_NAME"], old_cookie)
        self.assertEqual(replay.get("/api/me").status_code, 401)

    def test_password_change_revokes_every_user_session(self):
        user = self.create_user("password-revoke-user", "old-password")
        first = self.client()
        second = self.client()
        self.login(first, user["username"], user["password"])
        self.login(second, user["username"], user["password"])

        response = self.admin_client().put(
            f"/api/admin/users/{user['id']}",
            json={"password": "new-password"},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(first.get("/api/me").status_code, 401)
        self.assertEqual(second.get("/api/me").status_code, 401)
        self.assertEqual(
            self.client().post(
                "/api/auth/login",
                json={"username": user["username"], "password": "old-password"},
            ).status_code,
            401,
        )
        self.login(self.client(), user["username"], "new-password")

        conn = sqlite3.connect(self.db_path)
        try:
            active = conn.execute(
                "SELECT COUNT(*) FROM user_sessions WHERE user_id = ? AND is_active = 1",
                (user["id"],),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(active, 1, "Only the new-password login should be active")

    def test_deleted_admin_keeps_activity_history(self):
        primary = self.admin_client()
        response = primary.post(
            "/api/admin/users",
            json={"username": "audit-admin", "password": "audit-password", "role": "admin"},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

        conn = sqlite3.connect(self.db_path)
        try:
            admin_id = conn.execute(
                "SELECT id FROM users WHERE username = 'audit-admin'"
            ).fetchone()[0]
        finally:
            conn.close()

        audit_admin = self.client()
        self.login(audit_admin, "audit-admin", "audit-password")
        self.assertEqual(primary.put(f"/api/admin/users/{admin_id}", json={"role": "user"}).status_code, 200)
        self.assertEqual(primary.delete(f"/api/admin/users/{admin_id}").status_code, 200)

        conn = sqlite3.connect(self.db_path)
        try:
            remaining = conn.execute(
                "SELECT COUNT(*) FROM admin_activity_logs WHERE admin_user_id = ?",
                (admin_id,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertGreater(remaining, 0)

        report = primary.get("/api/admin/activity-report").get_json()["report"]
        deleted_actor = next(row for row in report if row["admin_user_id"] == admin_id)
        self.assertEqual(deleted_actor["admin_username"], "audit-admin")

    def test_history_confidence_rejects_nonfinite_and_out_of_range(self):
        user = self.create_user("confidence-user")
        record_id = self.add_history(user["id"])
        client = self.client()
        self.login(client, user["username"], user["password"])

        for value in ("NaN", "Infinity", "-Infinity", -0.01, 1.01):
            response = client.put(f"/api/history/{record_id}", json={"confidence": value})
            self.assertEqual(response.status_code, 400, value)
            self.assertIn("Invalid confidence", response.get_json()["error"])

        detail = client.get(f"/api/history/{record_id}").get_json()["record"]
        self.assertEqual(detail["confidence"], 0.9)
        pdf = client.get(f"/api/history/{record_id}/export/pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.data[:4], b"%PDF")
        pdf.close()

    def test_history_confidence_accepts_boundaries(self):
        user = self.create_user("confidence-boundary-user")
        record_id = self.add_history(user["id"])
        client = self.client()
        self.login(client, user["username"], user["password"])

        for value in (0, "0.5", 1):
            response = client.put(f"/api/history/{record_id}", json={"confidence": value})
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            self.assertEqual(response.get_json()["record"]["confidence"], float(value))

    def test_results_pdf_rejects_nonfinite_confidence(self):
        client = self.admin_client()
        response = client.post(
            "/api/export/results/pdf",
            json={"results": [{"file": "a.png", "prediction": "x", "confidence": "Infinity"}]},
        )
        self.assertEqual(response.status_code, 400)

        response = client.post(
            "/api/export/results/pdf",
            json={
                "results": [
                    {
                        "file": "a.png",
                        "prediction": "x",
                        "confidence": 0.9,
                        "alternatives": [{"text": "y", "confidence": "NaN"}],
                    }
                ]
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_predict_rejects_aggregate_pixels_before_decode(self):
        user = self.create_user("pixel-budget-user")
        client = self.client()
        self.login(client, user["username"], user["password"])
        self.set_app_config("OCR_MAX_TOTAL_PIXELS", 199)
        self.set_app_config("OCR_MAX_DECODED_BYTES", 10_000)

        with mock.patch("PIL.Image.Image.convert") as convert, mock.patch.object(
            app_module.InferenceService, "predict_images"
        ) as predict:
            response = client.post(
                "/api/predict",
                data={"images": [self.png_file(name="one.png"), self.png_file(name="two.png")]},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Combined images", response.get_json()["error"])
        convert.assert_not_called()
        predict.assert_not_called()

    def test_predict_busy_rejects_before_decode(self):
        user = self.create_user("busy-ocr-user")
        client = self.client()
        self.login(client, user["username"], user["password"])
        slots = self.app.extensions["ocr_slots"]
        acquired = 0
        while slots.acquire(blocking=False):
            acquired += 1
        self.addCleanup(lambda: [slots.release() for _ in range(acquired)])

        with mock.patch("PIL.Image.Image.convert") as convert, mock.patch.object(
            app_module.InferenceService, "predict_images"
        ) as predict:
            response = client.post(
                "/api/predict",
                data={"images": [self.png_file()]},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers.get("Retry-After"), "1")
        convert.assert_not_called()
        predict.assert_not_called()

    def test_predict_releases_slot_after_inference_failure(self):
        user = self.create_user("slot-release-user")
        client = self.client()
        self.login(client, user["username"], user["password"])
        slots = self.app.extensions["ocr_slots"]
        self.assertTrue(slots.acquire(blocking=False))
        self.addCleanup(slots.release)
        successful = (
            [{"file": "note.png", "prediction": "ok", "confidence": 0.9, "source": "test", "alternatives": []}],
            {"ocr_engine": "auto", "ocr_languages_used": [], "warnings": []},
        )
        with mock.patch.object(
            app_module.InferenceService,
            "predict_images",
            side_effect=[RuntimeError("test failure"), successful],
        ):
            first = client.post(
                "/api/predict",
                data={"images": [self.png_file(name="first.png")]},
                content_type="multipart/form-data",
            )
            second = client.post(
                "/api/predict",
                data={"images": [self.png_file(name="second.png")]},
                content_type="multipart/form-data",
            )
        self.assertEqual(first.status_code, 500)
        self.assertEqual(second.status_code, 200, second.get_data(as_text=True))

    def test_avatar_pixel_limit_precedes_decode(self):
        client = self.admin_client()
        self.set_app_config("OCR_MAX_AVATAR_PIXELS", 99)
        with mock.patch("PIL.Image.Image.convert") as convert:
            response = client.post(
                "/api/profile/avatar",
                data={"avatar": self.png_file(name="avatar.png")},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("dimensions", response.get_json()["error"])
        convert.assert_not_called()

    def test_large_batch_consumes_full_rate_budget(self):
        user = self.create_user("weighted-rate-user")
        client = self.client()
        self.login(client, user["username"], user["password"])
        files = [(io.BytesIO(b"bad"), f"bad-{index}.png") for index in range(20)]
        first = client.post(
            "/api/predict",
            data={"images": files},
            content_type="multipart/form-data",
        )
        self.assertEqual(first.status_code, 400)
        with mock.patch.object(app_module.Image, "open") as image_open:
            second = client.post(
                "/api/predict",
                data={"images": [(io.BytesIO(b"bad"), "again.png")]},
                content_type="multipart/form-data",
            )
        self.assertEqual(second.status_code, 429)
        image_open.assert_not_called()

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

    def test_legacy_audit_foreign_key_is_removed_without_losing_rows(self):
        path = Path(TEST_ROOT) / "legacy-audit.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL DEFAULT '',
                password_hash TEXT NOT NULL,
                firebase_uid TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL CHECK(role IN ('admin', 'user')) DEFAULT 'user',
                avatar_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_login TEXT
            );
            CREATE TABLE admin_activity_logs (
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
            INSERT INTO users (id, username, password_hash, role)
            VALUES (7, 'legacy-admin', 'hash', 'admin');
            INSERT INTO admin_activity_logs (id, admin_user_id, action, details)
            VALUES (11, 7, 'LOGIN', 'legacy row');
            """
        )
        conn.commit()
        conn.close()

        init_db(path)
        init_db(path)

        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            self.assertEqual(conn.execute("PRAGMA foreign_key_list(admin_activity_logs)").fetchall(), [])
            row = conn.execute(
                "SELECT id, admin_user_id, action, details FROM admin_activity_logs WHERE id = 11"
            ).fetchone()
            self.assertEqual(row, (11, 7, "LOGIN", "legacy row"))
            indexes = {row[1] for row in conn.execute("PRAGMA index_list(admin_activity_logs)")}
            self.assertIn("idx_admin_activity_created", indexes)
            self.assertIn("idx_admin_activity_admin_created", indexes)
            conn.execute("DELETE FROM users WHERE id = 7")
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM admin_activity_logs WHERE id = 11").fetchone()[0],
                1,
            )
            new_id = conn.execute(
                "INSERT INTO admin_activity_logs (admin_user_id, action) VALUES (7, 'AFTER_DELETE')"
            ).lastrowid
            self.assertGreater(new_id, 11)
        finally:
            conn.close()

    def test_init_db_repairs_legacy_confidence_values(self):
        path = Path(TEST_ROOT) / "legacy-confidence.db"
        init_db(path, initial_admin_password="review-password")
        conn = sqlite3.connect(path)
        try:
            user_id = conn.execute("SELECT id FROM users WHERE role = 'admin'").fetchone()[0]
            for value in (float("inf"), float("-inf"), 1.5, -0.5):
                conn.execute(
                    "INSERT INTO detection_history (user_id, file_name, prediction, confidence) VALUES (?, 'x', 'x', ?)",
                    (user_id, value),
                )
            conn.commit()
        finally:
            conn.close()

        init_db(path)
        conn = sqlite3.connect(path)
        try:
            values = [row[0] for row in conn.execute("SELECT confidence FROM detection_history")]
        finally:
            conn.close()
        self.assertTrue(all(math.isfinite(value) and 0 <= value <= 1 for value in values))


class FirebaseRecoveryTests(unittest.TestCase):
    def make_sync(self, *, configured: bool = True, enabled: bool = True):
        sync = mock.Mock()
        sync.configured = configured
        sync.enabled = enabled
        sync.last_error_reason = ""
        sync.last_restore_succeeded = True
        sync.restore_all_to_sqlite.return_value = {}
        sync.sync_all.return_value = []
        sync.sync_rows.return_value = True
        sync.sync_tables.return_value = []
        return sync

    def create_with_sync(self, path: Path, sync):
        env = {
            "FIREBASE_RESTORE_ON_STARTUP": "true",
            "FIREBASE_SYNC_ON_STARTUP": "true",
            "KEEP_ALIVE_ENABLED": "false",
            "ADMIN_INITIAL_PASSWORD": "firebase-test-password",
        }
        with mock.patch.object(app_module, "FirebaseDataSync", return_value=sync), mock.patch.dict(
            os.environ, env
        ):
            app = create_app(str(path.parent / "missing.pt"), "cpu", str(path))
        app.config.update(TESTING=True)
        return app

    def test_existing_sqlite_is_not_restored_over(self):
        root = Path(tempfile.mkdtemp(prefix="firebase-existing-", dir=TEST_ROOT))
        path = root / "app.db"
        init_db(path, initial_admin_password="firebase-test-password")
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO users (username, full_name, password_hash, role) VALUES ('local-newer', 'Local', 'hash', 'user')"
        )
        conn.commit()
        conn.close()

        sync = self.make_sync()
        self.create_with_sync(path, sync)

        sync.restore_all_to_sqlite.assert_not_called()
        sync.sync_all.assert_called_once_with(mock.ANY, force=True)
        conn = sqlite3.connect(path)
        try:
            self.assertIsNotNone(conn.execute("SELECT id FROM users WHERE username = 'local-newer'").fetchone())
        finally:
            conn.close()

    def test_missing_sqlite_is_seeded_from_firestore(self):
        root = Path(tempfile.mkdtemp(prefix="firebase-missing-", dir=TEST_ROOT))
        path = root / "app.db"
        sync = self.make_sync()
        self.create_with_sync(path, sync)
        sync.restore_all_to_sqlite.assert_called_once_with(mock.ANY)
        sync.sync_all.assert_called_once_with(mock.ANY, force=True)

    def test_schema_only_sqlite_is_still_recovery_state(self):
        root = Path(tempfile.mkdtemp(prefix="firebase-schema-only-", dir=TEST_ROOT))
        path = root / "app.db"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE placeholder (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        sync = self.make_sync()
        self.create_with_sync(path, sync)
        sync.restore_all_to_sqlite.assert_called_once_with(mock.ANY)

    def test_restore_failure_blocks_outbound_sync_and_keeps_marker(self):
        root = Path(tempfile.mkdtemp(prefix="firebase-restore-failure-", dir=TEST_ROOT))
        path = root / "app.db"
        sync = self.make_sync()
        sync.last_restore_succeeded = False
        sync.last_error_reason = "network unavailable"

        env = {
            "FIREBASE_RESTORE_ON_STARTUP": "true",
            "FIREBASE_SYNC_ON_STARTUP": "true",
            "KEEP_ALIVE_ENABLED": "false",
            "ADMIN_INITIAL_PASSWORD": "firebase-test-password",
        }
        with mock.patch.object(app_module, "FirebaseDataSync", return_value=sync), mock.patch.dict(
            os.environ, env
        ):
            with self.assertRaises(RuntimeError):
                create_app(str(root / "missing.pt"), "cpu", str(path))

        sync.sync_all.assert_not_called()
        self.assertTrue(Path(f"{path}.restore-pending").is_file())

    def test_disabled_runtime_sync_retries_full_reconciliation_on_next_write(self):
        root = Path(tempfile.mkdtemp(prefix="firebase-retry-", dir=TEST_ROOT))
        path = root / "app.db"
        sync = self.make_sync(enabled=False)
        app = self.create_with_sync(path, sync)
        sync.reset_mock()

        client = app.test_client()
        response = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "firebase-test-password"},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        sync.sync_all.assert_called_once_with(mock.ANY, force=False)
        sync.sync_rows.assert_not_called()

    def test_unconfigured_sync_stays_off(self):
        root = Path(tempfile.mkdtemp(prefix="firebase-off-", dir=TEST_ROOT))
        path = root / "app.db"
        sync = self.make_sync(configured=False, enabled=False)
        app = self.create_with_sync(path, sync)
        sync.reset_mock()
        client = app.test_client()
        response = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "firebase-test-password"},
        )
        self.assertEqual(response.status_code, 200)
        sync.sync_all.assert_not_called()
        sync.sync_rows.assert_not_called()


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
