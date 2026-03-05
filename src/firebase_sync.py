from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from logging import Logger

try:
    from firebase_admin import firestore

    FIRESTORE_AVAILABLE = True
except ImportError:
    firestore = None
    FIRESTORE_AVAILABLE = False

FIREBASE_SYNC_TABLES: tuple[str, ...] = (
    "users",
    "user_sessions",
    "detection_history",
    "admin_activity_logs",
)

FIREBASE_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "users": (
        "id",
        "username",
        "full_name",
        "password_hash",
        "role",
        "avatar_path",
        "created_at",
        "last_login",
    ),
    "user_sessions": (
        "id",
        "user_id",
        "is_active",
        "login_at",
        "last_seen",
        "logout_at",
    ),
    "detection_history": (
        "id",
        "user_id",
        "file_name",
        "prediction",
        "confidence",
        "source",
        "filters_json",
        "image_path",
        "created_at",
        "updated_at",
    ),
    "admin_activity_logs": (
        "id",
        "admin_user_id",
        "action",
        "target_type",
        "target_id",
        "details",
        "ip_address",
        "created_at",
    ),
}


class FirebaseDataSync:
    def __init__(
        self,
        *,
        enabled: bool,
        logger: Logger,
        collection_prefix: str = "reco",
        retry_interval_seconds: int = 300,
    ) -> None:
        self.logger = logger
        self.collection_prefix = (collection_prefix or "reco").strip() or "reco"
        self.configured = bool(enabled and FIRESTORE_AVAILABLE)
        self._last_table_sync_at: dict[str, float] = {}
        self._client = firestore.client() if self.configured and firestore is not None else None
        self.configured = bool(self.configured and self._client is not None)
        self.enabled = self.configured
        self.retry_interval_seconds = max(30, int(retry_interval_seconds))
        self._runtime_disabled_until = 0.0
        self.last_error_reason = ""

        if enabled and not FIRESTORE_AVAILABLE:
            self.logger.warning("firebase-admin firestore module not available; Firebase data sync is disabled.")
        if enabled and not self.configured:
            self.logger.warning("Firestore client unavailable; Firebase data sync is disabled.")

    def _disable_runtime_sync(self, reason: str) -> None:
        self.last_error_reason = reason
        self._runtime_disabled_until = time.time() + float(self.retry_interval_seconds)
        self.enabled = False
        self.logger.warning(
            "Firebase data sync has been disabled at runtime: %s. Will retry in %ss.",
            reason,
            self.retry_interval_seconds,
        )

    def _enable_runtime_sync_if_due(self, *, force: bool = False) -> bool:
        if not self.configured:
            return False
        if self.enabled:
            return True

        now = time.time()
        if not force and now < self._runtime_disabled_until:
            return False

        self.enabled = True
        self.last_error_reason = ""
        self.logger.info("Retrying Firebase data sync.")
        return True

    def _collection_name(self, table_name: str) -> str:
        return f"{self.collection_prefix}_{table_name}"

    def _validate_table_name(self, table_name: str) -> None:
        if table_name not in FIREBASE_SYNC_TABLES:
            raise ValueError(f"Unsupported table for Firebase sync: {table_name}")

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, object]:
        return {key: row[key] for key in row.keys()}

    def _default_column_value(self, table_name: str, column_name: str) -> object:
        timestamp_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        defaults: dict[tuple[str, str], object] = {
            ("users", "full_name"): "",
            ("users", "password_hash"): "",
            ("users", "role"): "user",
            ("users", "avatar_path"): "",
            ("users", "created_at"): timestamp_now,
            ("users", "last_login"): None,
            ("user_sessions", "is_active"): 1,
            ("user_sessions", "login_at"): timestamp_now,
            ("user_sessions", "last_seen"): timestamp_now,
            ("user_sessions", "logout_at"): None,
            ("detection_history", "file_name"): "",
            ("detection_history", "prediction"): "",
            ("detection_history", "confidence"): 0.0,
            ("detection_history", "source"): "rnn",
            ("detection_history", "filters_json"): "{}",
            ("detection_history", "image_path"): "",
            ("detection_history", "created_at"): timestamp_now,
            ("detection_history", "updated_at"): timestamp_now,
            ("admin_activity_logs", "action"): "UNKNOWN",
            ("admin_activity_logs", "target_type"): "",
            ("admin_activity_logs", "target_id"): "",
            ("admin_activity_logs", "details"): "",
            ("admin_activity_logs", "ip_address"): "",
            ("admin_activity_logs", "created_at"): timestamp_now,
        }
        return defaults.get((table_name, column_name))

    def _normalize_firestore_row(self, table_name: str, payload: dict[str, object]) -> dict[str, object]:
        columns = FIREBASE_TABLE_COLUMNS[table_name]
        normalized: dict[str, object] = {}
        for column_name in columns:
            value = payload.get(column_name)
            if value is None and column_name != "id":
                value = self._default_column_value(table_name, column_name)

            if column_name in {"id", "user_id", "admin_user_id", "is_active"} and value is not None:
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    value = 0
            if column_name == "confidence" and value is not None:
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    value = 0.0

            normalized[column_name] = value
        return normalized

    def sync_table(
        self,
        db: sqlite3.Connection,
        table_name: str,
        *,
        min_interval_seconds: int = 0,
        force: bool = False,
    ) -> bool:
        if self._client is None:
            return False
        if not self._enable_runtime_sync_if_due(force=force):
            return False

        self._validate_table_name(table_name)
        now = time.time()
        if not force and min_interval_seconds > 0:
            last_sync = self._last_table_sync_at.get(table_name, 0.0)
            if (now - last_sync) < float(min_interval_seconds):
                return False

        try:
            rows = db.execute(f"SELECT * FROM {table_name}").fetchall()
            collection = self._client.collection(self._collection_name(table_name))
            local_ids: set[str] = set()

            batch = self._client.batch()
            operations = 0
            flush_every = 400

            for row in rows:
                payload = self._row_to_dict(row)
                doc_id = str(payload.get("id") or "")
                if not doc_id:
                    continue
                local_ids.add(doc_id)
                batch.set(collection.document(doc_id), payload)
                operations += 1
                if operations >= flush_every:
                    batch.commit()
                    batch = self._client.batch()
                    operations = 0

            for doc_ref in collection.list_documents():
                if doc_ref.id in local_ids:
                    continue
                batch.delete(doc_ref)
                operations += 1
                if operations >= flush_every:
                    batch.commit()
                    batch = self._client.batch()
                    operations = 0

            if operations > 0:
                batch.commit()

            self._last_table_sync_at[table_name] = now
            return True
        except Exception as exc:
            self._disable_runtime_sync(f"{type(exc).__name__}: {exc}")
            return False

    def sync_tables(
        self,
        db: sqlite3.Connection,
        table_names: Sequence[str] | Iterable[str],
        *,
        min_interval_seconds: int = 0,
        force: bool = False,
    ) -> list[str]:
        synced: list[str] = []
        for table_name in dict.fromkeys(table_names):
            if self.sync_table(
                db,
                table_name,
                min_interval_seconds=min_interval_seconds,
                force=force,
            ):
                synced.append(table_name)
        return synced

    def sync_all(
        self,
        db: sqlite3.Connection,
        *,
        min_interval_seconds: int = 0,
        force: bool = False,
    ) -> list[str]:
        return self.sync_tables(
            db,
            FIREBASE_SYNC_TABLES,
            min_interval_seconds=min_interval_seconds,
            force=force,
        )

    def restore_all_to_sqlite(self, db: sqlite3.Connection, *, force: bool = False) -> dict[str, int]:
        if not self.enabled or self._client is None:
            return {}

        try:
            firebase_rows: dict[str, list[dict[str, object]]] = {}
            total_records = 0

            for table_name in FIREBASE_SYNC_TABLES:
                self._validate_table_name(table_name)
                collection = self._client.collection(self._collection_name(table_name))
                rows: list[dict[str, object]] = []
                for doc in collection.stream():
                    payload = doc.to_dict() or {}
                    if "id" not in payload:
                        payload["id"] = doc.id
                    normalized = self._normalize_firestore_row(table_name, payload)
                    if int(normalized.get("id") or 0) <= 0:
                        continue
                    rows.append(normalized)
                firebase_rows[table_name] = rows
                total_records += len(rows)

            if total_records == 0 and not force:
                return {}

            db.execute("PRAGMA foreign_keys = OFF")
            for table_name in FIREBASE_SYNC_TABLES:
                db.execute(f"DELETE FROM {table_name}")
                db.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table_name,))

            for table_name in FIREBASE_SYNC_TABLES:
                rows = firebase_rows.get(table_name) or []
                if not rows:
                    continue

                columns = FIREBASE_TABLE_COLUMNS[table_name]
                column_sql = ", ".join(columns)
                placeholders = ", ".join("?" for _ in columns)
                values = [tuple(row.get(column_name) for column_name in columns) for row in rows]

                db.executemany(
                    f"INSERT INTO {table_name} ({column_sql}) VALUES ({placeholders})",
                    values,
                )
                max_id = max(int(row["id"]) for row in rows)
                db.execute(
                    "INSERT INTO sqlite_sequence(name, seq) VALUES(?, ?)",
                    (table_name, max_id),
                )

            db.commit()
            db.execute("PRAGMA foreign_keys = ON")
            return {table_name: len(rows) for table_name, rows in firebase_rows.items() if rows}
        except Exception as exc:
            try:
                db.rollback()
                db.execute("PRAGMA foreign_keys = ON")
            except Exception:
                pass
            self._disable_runtime_sync(f"{type(exc).__name__}: {exc}")
            return {}
