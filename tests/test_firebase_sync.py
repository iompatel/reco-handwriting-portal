"""Firebase replication tests using an in-memory Firestore double."""

from __future__ import annotations

import logging
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import src.firebase_sync as firebase_sync_module
from src.database import connect_db, init_db
from src.firebase_sync import FirebaseDataSync


class FakeSnapshot:
    def __init__(self, doc_id: str, payload: dict[str, object]) -> None:
        self.id = doc_id
        self._payload = payload

    def to_dict(self):
        return dict(self._payload)


class FakeDocument:
    def __init__(self, collection, doc_id: str) -> None:
        self.collection = collection
        self.id = doc_id


class FakeCollection:
    def __init__(self, client, name: str) -> None:
        self.client = client
        self.name = name

    @property
    def rows(self):
        return self.client.rows.setdefault(self.name, {})

    def stream(self):
        return [FakeSnapshot(doc_id, payload) for doc_id, payload in self.rows.items()]

    def document(self, doc_id: str):
        return FakeDocument(self, doc_id)

    def list_documents(self):
        if self.client.block_list_documents:
            self.client.full_sync_inside.set()
            self.client.release_full_sync.wait(timeout=2)
        return [FakeDocument(self, doc_id) for doc_id in self.rows]


class FakeBatch:
    def __init__(self, client) -> None:
        self.client = client
        self.operations = []

    def set(self, document, payload):
        self.operations.append(("set", document, dict(payload)))

    def delete(self, document):
        self.operations.append(("delete", document, None))

    def commit(self):
        for operation, document, payload in self.operations:
            rows = document.collection.rows
            if operation == "set":
                rows[document.id] = payload
            else:
                rows.pop(document.id, None)
        self.operations.clear()


class FakeFirestoreClient:
    def __init__(self, rows=None) -> None:
        self.rows = rows or {}
        self.batch_count = 0
        self.block_list_documents = False
        self.full_sync_inside = threading.Event()
        self.release_full_sync = threading.Event()
        self.second_batch_created = threading.Event()

    def collection(self, name: str):
        return FakeCollection(self, name)

    def batch(self):
        self.batch_count += 1
        if self.batch_count >= 2:
            self.second_batch_created.set()
        return FakeBatch(self)


class FirebaseDataSyncTests(unittest.TestCase):
    def make_sync(self, client: FakeFirestoreClient) -> FirebaseDataSync:
        firestore_api = mock.Mock()
        firestore_api.client.return_value = client
        with mock.patch.object(firebase_sync_module, "FIRESTORE_AVAILABLE", True), mock.patch.object(
            firebase_sync_module, "firestore", firestore_api
        ):
            return FirebaseDataSync(enabled=True, logger=logging.getLogger("firebase-test"), collection_prefix="test")

    def test_restore_invalidates_sessions_and_backfills_audit_actor(self):
        root = Path(tempfile.mkdtemp(prefix="firebase-restore-test-"))
        path = root / "app.db"
        init_db(path, initial_admin_password="restore-password")
        client = FakeFirestoreClient(
            {
                "test_users": {
                    "1": {
                        "id": 1,
                        "username": "restored-admin",
                        "full_name": "Restored Admin",
                        "password_hash": "hash",
                        "role": "admin",
                    }
                },
                "test_user_sessions": {
                    "1": {"id": 1, "user_id": 1, "session_token": "old-token", "is_active": 1}
                },
                "test_detection_history": {},
                "test_admin_activity_logs": {
                    "1": {"id": 1, "admin_user_id": 1, "action": "LOGIN"}
                },
            }
        )
        sync = self.make_sync(client)
        db = connect_db(path)
        try:
            counts = sync.restore_all_to_sqlite(db)
            session = db.execute(
                "SELECT session_token, is_active, logout_at FROM user_sessions WHERE id = 1"
            ).fetchone()
            activity = db.execute(
                "SELECT admin_username, admin_full_name FROM admin_activity_logs WHERE id = 1"
            ).fetchone()
        finally:
            db.close()

        self.assertTrue(sync.last_restore_succeeded)
        self.assertEqual(counts["users"], 1)
        self.assertEqual(tuple(session)[:2], ("old-token", 0))
        self.assertIsNotNone(session["logout_at"])
        self.assertEqual(tuple(activity), ("restored-admin", "Restored Admin"))

    def test_invalid_restore_rolls_back_without_touching_local_data(self):
        root = Path(tempfile.mkdtemp(prefix="firebase-rollback-test-"))
        path = root / "app.db"
        init_db(path, initial_admin_password="restore-password")
        db = connect_db(path)
        db.execute(
            "INSERT INTO users (username, full_name, password_hash, role) VALUES ('local-safe', 'Local', 'hash', 'user')"
        )
        db.commit()

        client = FakeFirestoreClient(
            {
                "test_users": {
                    "1": {"id": 1, "username": "remote", "password_hash": "hash", "role": "admin"}
                },
                "test_user_sessions": {},
                "test_detection_history": {
                    "1": {
                        "id": 1,
                        "user_id": 1,
                        "file_name": "bad.png",
                        "prediction": "bad",
                        "confidence": "Infinity",
                    }
                },
                "test_admin_activity_logs": {},
            }
        )
        sync = self.make_sync(client)
        try:
            self.assertEqual(sync.restore_all_to_sqlite(db), {})
            self.assertIsNotNone(db.execute("SELECT id FROM users WHERE username = 'local-safe'").fetchone())
        finally:
            db.close()
        self.assertFalse(sync.last_restore_succeeded)

    def test_full_and_row_sync_are_serialized(self):
        root = Path(tempfile.mkdtemp(prefix="firebase-lock-test-"))
        path = root / "app.db"
        init_db(path, initial_admin_password="restore-password")
        client = FakeFirestoreClient()
        client.block_list_documents = True
        sync = self.make_sync(client)
        errors = []

        def full_sync():
            try:
                db = connect_db(path)
                try:
                    sync.sync_table(db, "users", force=True)
                finally:
                    db.close()
            except Exception as exc:  # pragma: no cover - assertion reports thread failures
                errors.append(exc)

        def row_sync():
            try:
                db = connect_db(path)
                try:
                    sync.sync_rows(db, [("users", 1)], force=True)
                finally:
                    db.close()
            except Exception as exc:  # pragma: no cover - assertion reports thread failures
                errors.append(exc)

        first = threading.Thread(target=full_sync)
        second = threading.Thread(target=row_sync)
        first.start()
        self.assertTrue(client.full_sync_inside.wait(timeout=1))
        second.start()
        time.sleep(0.05)
        self.assertFalse(client.second_batch_created.is_set())
        client.release_full_sync.set()
        first.join(timeout=2)
        second.join(timeout=2)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(client.second_batch_created.is_set())


if __name__ == "__main__":
    unittest.main()
