from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sqlite3
import zipfile
from dataclasses import asdict
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from flask import Flask, g, jsonify, redirect, render_template, request, send_file, session, url_for
from PIL import Image
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from src.database import connect_db, init_db
from src.inference_service import InferenceService, PreprocessConfig

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Image as PDFImage
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
ALLOWED_AVATAR_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def clamp_int(value: str | None, default: int, min_value: int, max_value: int) -> int:
    try:
        number = int(value) if value is not None else default
    except (TypeError, ValueError):
        number = default
    return max(min_value, min(max_value, number))


def clamp_float(value: str | None, default: float, min_value: float, max_value: float) -> float:
    try:
        number = float(value) if value is not None else default
    except (TypeError, ValueError):
        number = default
    return max(min_value, min(max_value, number))


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def safe_filename_part(value: str | None, fallback: str = "user") -> str:
    text = (value or "").strip().lower()
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text)
    cleaned = cleaned.strip("_")
    return cleaned or fallback


def create_app(checkpoint: str, device: str, db_path: str) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
    app.config["DB_PATH"] = db_path
    app.config["UPLOAD_ROOT"] = os.getenv("OCR_UPLOAD_DIR", "data/uploads")
    app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me-in-production")

    service = InferenceService(checkpoint_path=Path(checkpoint), device_name=device)
    init_db(db_path)

    def get_db() -> sqlite3.Connection:
        if "db" not in g:
            g.db = connect_db(app.config["DB_PATH"])
        return g.db

    def build_avatar_url(avatar_path: str | None) -> str:
        avatar = (avatar_path or "").strip()
        if not avatar:
            return ""
        version = Path(avatar).name
        return f"{url_for('profile_avatar')}?v={version}"

    def save_uploaded_image(user_id: int, filename: str, raw_bytes: bytes, batch_id: str, index: int) -> str:
        upload_root = Path(app.config["UPLOAD_ROOT"])
        user_dir = upload_root / f"user_{user_id}"
        user_dir.mkdir(parents=True, exist_ok=True)

        safe_name = secure_filename(filename) or f"upload_{index}.png"
        stored_name = f"{batch_id}_{index:03d}_{safe_name}"
        target = user_dir / stored_name

        target.write_bytes(raw_bytes)
        relative = target.relative_to(upload_root).as_posix()
        return relative

    @app.teardown_appcontext
    def close_db(_: object | None = None) -> None:
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def session_user() -> sqlite3.Row | None:
        if "user_id" not in session:
            return None
        if "session_user" in g:
            return g.session_user

        user = get_db().execute(
            "SELECT id, username, full_name, role, avatar_path, created_at, last_login FROM users WHERE id = ?",
            (session["user_id"],),
        ).fetchone()

        if user is None:
            session.clear()
            return None

        g.session_user = user
        return user

    def login_required_api(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if session_user() is None:
                return jsonify({"ok": False, "error": "Authentication required"}), 401
            return fn(*args, **kwargs)

        return wrapper

    def login_required_page(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if session_user() is None:
                return redirect(url_for("login_page"))
            return fn(*args, **kwargs)

        return wrapper

    def admin_required_api(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = session_user()
            if user is None:
                return jsonify({"ok": False, "error": "Authentication required"}), 401
            if user["role"] != "admin":
                return jsonify({"ok": False, "error": "Admin access required"}), 403
            return fn(*args, **kwargs)

        return wrapper

    def client_ip_address() -> str:
        forwarded = (request.headers.get("X-Forwarded-For") or "").strip()
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
        return (request.remote_addr or "").strip()[:64]

    def insert_admin_activity(
        *,
        admin_user_id: int,
        action: str,
        target_type: str = "",
        target_id: str = "",
        details: str = "",
    ) -> None:
        db = get_db()
        db.execute(
            """
            INSERT INTO admin_activity_logs
                (admin_user_id, action, target_type, target_id, details, ip_address, created_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                int(admin_user_id),
                (action or "UNKNOWN")[:64],
                (target_type or "")[:64],
                (target_id or "")[:128],
                (details or "")[:500],
                client_ip_address(),
            ),
        )

    def log_admin_activity(
        action: str,
        *,
        target_type: str = "",
        target_id: str = "",
        details: str = "",
        admin_user_id: int | None = None,
        commit: bool = False,
    ) -> None:
        try:
            resolved_admin_id = admin_user_id
            if resolved_admin_id is None:
                current = session_user()
                if current is None or current["role"] != "admin":
                    return
                resolved_admin_id = int(current["id"])

            insert_admin_activity(
                admin_user_id=int(resolved_admin_id),
                action=action,
                target_type=target_type,
                target_id=target_id,
                details=details,
            )
            if commit:
                get_db().commit()
        except Exception:
            app.logger.exception("Failed to write admin activity log")

    @app.before_request
    def touch_active_session() -> None:
        if request.endpoint and request.endpoint.startswith("static"):
            return

        session_record_id = session.get("session_record_id")
        if session_record_id:
            db = get_db()
            db.execute(
                """
                UPDATE user_sessions
                SET last_seen = CURRENT_TIMESTAMP
                WHERE id = ? AND is_active = 1
                """,
                (session_record_id,),
            )
            db.commit()

    @app.get("/login")
    def login_page():
        if session_user() is not None:
            return redirect(url_for("index"))
        return render_template("login.html")

    @app.get("/favicon.ico")
    def favicon():
        return ("", 204)

    @app.get("/")
    @login_required_page
    def index():
        return render_template("index.html", checkpoint=str(service.checkpoint_path))

    @app.get("/history")
    @login_required_page
    def history_page():
        return render_template("history.html")

    @app.get("/results")
    @login_required_page
    def results_page():
        return render_template("results.html")

    @app.get("/admin/uploads")
    @login_required_page
    def admin_uploads_page():
        user = session_user()
        if user is None:
            return redirect(url_for("login_page"))
        if user["role"] != "admin":
            return redirect(url_for("index"))
        return render_template("admin_uploads.html")

    @app.get("/admin/activity-report")
    @login_required_page
    def admin_activity_page():
        user = session_user()
        if user is None:
            return redirect(url_for("login_page"))
        if user["role"] != "admin":
            return redirect(url_for("index"))
        return render_template("admin_activity.html")

    @app.post("/api/auth/register")
    def register():
        payload = request.get_json(silent=True) or {}
        username = normalize_username(payload.get("username", ""))
        full_name = (payload.get("full_name") or "").strip() or username
        password = payload.get("password") or ""

        if len(username) < 3:
            return jsonify({"ok": False, "error": "Username must be at least 3 characters"}), 400
        if len(password) < 6:
            return jsonify({"ok": False, "error": "Password must be at least 6 characters"}), 400

        db = get_db()
        try:
            db.execute(
                """
                INSERT INTO users (username, full_name, password_hash, role)
                VALUES (?, ?, ?, 'user')
                """,
                (username, full_name, generate_password_hash(password)),
            )
            db.commit()
        except sqlite3.IntegrityError:
            return jsonify({"ok": False, "error": "Username already exists"}), 400

        return jsonify({"ok": True, "message": "Account created. Please login."})

    @app.post("/api/auth/reset-password")
    def reset_password():
        payload = request.get_json(silent=True) or {}
        username = normalize_username(payload.get("username", ""))
        new_password = payload.get("new_password") or ""

        if len(username) < 3:
            return jsonify({"ok": False, "error": "Username must be at least 3 characters"}), 400
        if len(new_password) < 6:
            return jsonify({"ok": False, "error": "New password must be at least 6 characters"}), 400

        db = get_db()
        user = db.execute(
            "SELECT id FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if user is None:
            return jsonify({"ok": False, "error": "Invalid username"}), 404

        db.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(new_password), int(user["id"])),
        )
        db.commit()
        return jsonify({"ok": True, "message": "Password updated successfully"})

    @app.post("/api/auth/login")
    def login():
        payload = request.get_json(silent=True) or {}
        username = normalize_username(payload.get("username", ""))
        password = payload.get("password") or ""

        db = get_db()
        user = db.execute(
            "SELECT id, username, full_name, role, avatar_path, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()

        if user is None or not check_password_hash(user["password_hash"], password):
            return jsonify({"ok": False, "error": "Invalid username or password"}), 401

        session.clear()

        db.execute(
            "UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?",
            (user["id"],),
        )
        cur = db.execute(
            "INSERT INTO user_sessions (user_id, is_active) VALUES (?, 1)",
            (user["id"],),
        )
        if user["role"] == "admin":
            insert_admin_activity(
                admin_user_id=int(user["id"]),
                action="LOGIN",
                target_type="auth",
                target_id=str(int(user["id"])),
                details="Admin logged in successfully",
            )
        db.commit()

        session["user_id"] = int(user["id"])
        session["role"] = user["role"]
        session["session_record_id"] = int(cur.lastrowid)

        return jsonify(
            {
                "ok": True,
                "user": {
                    "id": int(user["id"]),
                    "username": user["username"],
                    "full_name": user["full_name"],
                    "role": user["role"],
                    "avatar_url": build_avatar_url(user["avatar_path"]),
                },
            }
        )

    @app.post("/api/auth/logout")
    def logout():
        user = session_user()
        is_admin = user is not None and user["role"] == "admin"
        session_record_id = session.get("session_record_id")
        db = get_db() if session_record_id or is_admin else None
        if session_record_id:
            assert db is not None
            db.execute(
                """
                UPDATE user_sessions
                SET is_active = 0,
                    logout_at = CURRENT_TIMESTAMP,
                    last_seen = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (session_record_id,),
            )

        if is_admin and user is not None:
            insert_admin_activity(
                admin_user_id=int(user["id"]),
                action="LOGOUT",
                target_type="auth",
                target_id=str(int(user["id"])),
                details="Admin logged out",
            )

        if db is not None:
            db.commit()

        session.clear()
        return jsonify({"ok": True})

    @app.get("/api/me")
    def me():
        user = session_user()
        if user is None:
            return jsonify({"ok": False, "error": "Not logged in"}), 401

        return jsonify(
            {
                "ok": True,
                "user": {
                    "id": int(user["id"]),
                    "username": user["username"],
                    "full_name": user["full_name"],
                    "role": user["role"],
                    "avatar_url": build_avatar_url(user["avatar_path"]),
                    "created_at": user["created_at"],
                    "last_login": user["last_login"],
                },
            }
        )

    @app.get("/api/profile/avatar")
    @login_required_api
    def profile_avatar():
        user = session_user()
        if user is None:
            return jsonify({"ok": False, "error": "Authentication required"}), 401

        avatar_path = (user["avatar_path"] or "").strip()
        if not avatar_path:
            return ("", 204)

        upload_root = Path(app.config["UPLOAD_ROOT"]).resolve()
        source_image = (upload_root / avatar_path).resolve()
        try:
            source_image.relative_to(upload_root)
        except ValueError:
            return ("", 204)

        if not source_image.exists() or not source_image.is_file():
            return ("", 204)

        return send_file(source_image)

    @app.post("/api/profile/avatar")
    @login_required_api
    def upload_profile_avatar():
        user = session_user()
        if user is None:
            return jsonify({"ok": False, "error": "Authentication required"}), 401

        file = request.files.get("avatar")
        if file is None:
            return jsonify({"ok": False, "error": "Please choose an image file."}), 400

        filename = secure_filename(file.filename or "")
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_AVATAR_EXTENSIONS:
            return jsonify({"ok": False, "error": "Use PNG, JPG, JPEG or WEBP image."}), 400

        raw_bytes = file.read()
        if not raw_bytes:
            return jsonify({"ok": False, "error": "Empty image file."}), 400
        if len(raw_bytes) > 5 * 1024 * 1024:
            return jsonify({"ok": False, "error": "Image is too large. Max 5MB allowed."}), 400

        try:
            image = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
        except Exception:
            return jsonify({"ok": False, "error": "Invalid image file."}), 400

        resampling = getattr(Image, "Resampling", Image)
        image.thumbnail((512, 512), resampling.LANCZOS)

        upload_root = Path(app.config["UPLOAD_ROOT"]).resolve()
        user_dir = upload_root / "avatars" / f"user_{int(user['id'])}"
        user_dir.mkdir(parents=True, exist_ok=True)

        avatar_name = f"avatar_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}.jpg"
        avatar_file = user_dir / avatar_name
        image.save(avatar_file, format="JPEG", quality=90, optimize=True)

        old_avatar = (user["avatar_path"] or "").strip()
        relative_path = avatar_file.relative_to(upload_root).as_posix()

        db = get_db()
        db.execute(
            "UPDATE users SET avatar_path = ? WHERE id = ?",
            (relative_path, int(user["id"])),
        )
        db.commit()
        g.pop("session_user", None)

        if old_avatar:
            old_file = (upload_root / old_avatar).resolve()
            try:
                old_file.relative_to(upload_root)
                if old_file.exists() and old_file.is_file():
                    old_file.unlink()
            except Exception:
                pass

        return jsonify(
            {
                "ok": True,
                "avatar_url": build_avatar_url(relative_path),
            }
        )

    @app.get("/api/health")
    @login_required_api
    def health():
        status = service.get_status()
        status["ok"] = True
        return jsonify(status)

    @app.get("/api/ocr/capabilities")
    @login_required_api
    def ocr_capabilities():
        capabilities = service.get_ocr_capabilities()
        capabilities["ok"] = True
        return jsonify(capabilities)

    @app.post("/api/predict")
    @login_required_api
    def predict():
        user = session_user()
        files = request.files.getlist("images")
        if not files:
            return jsonify({"ok": False, "error": "Upload at least one image."}), 400

        decode_mode = (request.form.get("decode_mode") or "beam").strip().lower()
        if decode_mode not in {"beam", "greedy"}:
            decode_mode = "beam"

        beam_width = clamp_int(request.form.get("beam_width"), default=8, min_value=1, max_value=32)
        top_k = clamp_int(request.form.get("top_k"), default=3, min_value=1, max_value=10)
        ocr_engine = (request.form.get("ocr_engine") or "auto").strip().lower()
        ocr_languages = (request.form.get("ocr_languages") or "auto").strip()

        preprocess = PreprocessConfig(
            autocontrast=parse_bool(request.form.get("autocontrast"), default=True),
            threshold=clamp_int(request.form.get("threshold"), default=0, min_value=0, max_value=255),
            sharpen=clamp_float(request.form.get("sharpen"), default=0.0, min_value=0.0, max_value=2.0),
            grayscale=parse_bool(request.form.get("grayscale"), default=True),
            denoise=parse_bool(request.form.get("denoise"), default=False),
            adaptive_threshold=parse_bool(request.form.get("adaptive_threshold"), default=False),
            invert_colors=parse_bool(request.form.get("invert_colors"), default=False),
            contrast_boost=clamp_float(request.form.get("contrast_boost"), default=1.0, min_value=1.0, max_value=3.0),
            handwriting_boost=parse_bool(request.form.get("handwriting_boost"), default=False),
            student_notebook_mode=parse_bool(request.form.get("student_notebook_mode"), default=False),
            remove_notebook_lines=parse_bool(request.form.get("remove_notebook_lines"), default=False),
            smart_text_cleanup=parse_bool(request.form.get("smart_text_cleanup"), default=True),
        )

        named_images: list[tuple[str, Image.Image]] = []
        stored_image_paths: list[str] = []
        rejected: list[str] = []
        upload_batch_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")

        for index, file in enumerate(files, start=1):
            filename = secure_filename(file.filename) or f"image_{index}.png"
            suffix = Path(filename).suffix.lower()
            if suffix and suffix not in ALLOWED_EXTENSIONS:
                rejected.append(filename)
                continue

            try:
                raw_bytes = file.read()
                if not raw_bytes:
                    raise ValueError("Empty file")

                image = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
                named_images.append((filename, image))
                try:
                    stored_path = save_uploaded_image(
                        user_id=int(user["id"]),
                        filename=filename,
                        raw_bytes=raw_bytes,
                        batch_id=upload_batch_id,
                        index=index,
                    )
                except Exception:
                    stored_path = ""
                stored_image_paths.append(stored_path)
            except Exception:
                rejected.append(filename)

        if not named_images:
            message = "No valid image files found. Use PNG/JPG/JPEG/BMP/TIF/TIFF."
            return jsonify({"ok": False, "error": message, "rejected": rejected}), 400

        try:
            predictions, ocr_meta = service.predict_images(
                named_images=named_images,
                decode_mode=decode_mode,
                beam_width=beam_width,
                top_k=top_k,
                preprocess=preprocess,
                ocr_engine=ocr_engine,
                ocr_languages=ocr_languages,
            )
        except FileNotFoundError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Prediction failed: {exc}"}), 500

        db = get_db()
        filters_json = json.dumps(
            {
                "preprocess": asdict(preprocess),
                "ocr_engine": ocr_engine,
                "ocr_languages_requested": ocr_languages,
                "ocr_languages_used": ocr_meta.get("ocr_languages_used", []),
            }
        )
        for pred_index, item in enumerate(predictions):
            db.execute(
                """
                INSERT INTO detection_history
                    (user_id, file_name, prediction, confidence, source, filters_json, image_path, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    int(user["id"]),
                    item.get("file") or "unknown",
                    item.get("prediction") or "",
                    float(item.get("confidence") or 0.0),
                    item.get("source") or "rnn",
                    filters_json,
                    stored_image_paths[pred_index] if pred_index < len(stored_image_paths) else "",
                ),
            )
        db.commit()

        avg_conf = sum(item["confidence"] for item in predictions) / max(1, len(predictions))
        warnings = sorted(
            {
                warning
                for item in predictions
                for warning in (item.get("warnings") or [])
                if isinstance(warning, str) and warning.strip()
            }
        )
        if not warnings:
            warnings = ocr_meta.get("warnings", [])

        return jsonify(
            {
                "ok": True,
                "results": predictions,
                "rejected": rejected,
                "meta": {
                    "count": len(predictions),
                    "decode_mode": decode_mode,
                    "beam_width": beam_width,
                    "top_k": top_k,
                    "avg_confidence": round(float(avg_conf), 4),
                    "checkpoint": str(service.checkpoint_path),
                    "device": service.get_status()["device"],
                    "ocr_engine": ocr_meta.get("ocr_engine", ocr_engine),
                    "ocr_languages_requested": ocr_meta.get("ocr_languages_requested", ocr_languages or "auto"),
                    "ocr_languages_used": ocr_meta.get("ocr_languages_used", []),
                    "unsupported_ocr_languages": ocr_meta.get("unsupported_ocr_languages", []),
                    "warnings": warnings,
                    "filters": {
                        "grayscale": preprocess.grayscale,
                        "denoise": preprocess.denoise,
                        "adaptive_threshold": preprocess.adaptive_threshold,
                        "invert_colors": preprocess.invert_colors,
                        "contrast_boost": preprocess.contrast_boost,
                        "handwriting_boost": preprocess.handwriting_boost,
                        "student_notebook_mode": preprocess.student_notebook_mode,
                        "remove_notebook_lines": preprocess.remove_notebook_lines,
                        "smart_text_cleanup": preprocess.smart_text_cleanup,
                    },
                },
            }
        )

    @app.post("/api/export/results/pdf")
    @login_required_api
    def export_results_pdf():
        if not REPORTLAB_AVAILABLE:
            return jsonify({"ok": False, "error": "PDF export is unavailable. Install reportlab first."}), 500

        payload = request.get_json(silent=True) or {}
        raw_results = payload.get("results")
        summary = (payload.get("summary") or "").strip()

        if not isinstance(raw_results, list) or len(raw_results) == 0:
            return jsonify({"ok": False, "error": "No results provided for PDF export."}), 400

        user = session_user()
        if user is None:
            return jsonify({"ok": False, "error": "Authentication required"}), 401

        def clean_text(value: object, fallback: str = "-", max_len: int | None = None) -> str:
            text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
            if not text:
                text = fallback
            if max_len is not None and len(text) > max_len:
                text = text[: max_len - 3] + "..."
            return text

        prepared_rows: list[dict[str, object]] = []
        for item in raw_results[:300]:
            if not isinstance(item, dict):
                continue
            try:
                confidence = float(item.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0

            alternatives = item.get("alternatives") or []
            if not isinstance(alternatives, list):
                alternatives = []

            alt_texts: list[str] = []
            for alt in alternatives[:5]:
                if not isinstance(alt, dict):
                    continue
                try:
                    alt_conf = float(alt.get("confidence") or 0.0)
                except (TypeError, ValueError):
                    alt_conf = 0.0
                alt_texts.append(f"{clean_text(alt.get('text'), fallback='(blank)', max_len=40)} ({round(alt_conf * 100)}%)")

            prepared_rows.append(
                {
                    "file": clean_text(item.get("file"), fallback="unknown", max_len=60),
                    "prediction": clean_text(item.get("prediction"), fallback="(blank)", max_len=180),
                    "confidence": f"{round(confidence * 100)}%",
                    "alternatives": " | ".join(alt_texts) if alt_texts else "none",
                }
            )

        if not prepared_rows:
            return jsonify({"ok": False, "error": "No valid results found for PDF export."}), 400

        pdf_buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            pdf_buffer,
            pagesize=A4,
            leftMargin=36,
            rightMargin=36,
            topMargin=36,
            bottomMargin=36,
            title="Recognition Results Export",
            author="Handwriting Recognition Portal",
        )
        styles = getSampleStyleSheet()
        meta_style = ParagraphStyle(
            "ResultMetaStyle",
            parent=styles["BodyText"],
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#4e5d71"),
        )

        story: list[object] = []
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        story.append(Paragraph("Recognition Results Export", styles["Title"]))
        story.append(Paragraph(f"Generated at (UTC): {xml_escape(generated_at)}", meta_style))
        story.append(
            Paragraph(
                f"User: {xml_escape(clean_text(user['full_name'] or user['username'], fallback='User'))} "
                f"({xml_escape(clean_text(user['username'], fallback='user'))})",
                meta_style,
            )
        )
        if summary:
            story.append(Paragraph(f"Summary: {xml_escape(clean_text(summary, max_len=320))}", meta_style))
        story.append(Spacer(1, 10))

        chunk_size = 28
        for chunk_start in range(0, len(prepared_rows), chunk_size):
            chunk = prepared_rows[chunk_start : chunk_start + chunk_size]
            table_data = [["File", "Prediction", "Confidence", "Alternatives"]]
            for row in chunk:
                table_data.append(
                    [
                        row["file"],
                        row["prediction"],
                        row["confidence"],
                        row["alternatives"],
                    ]
                )

            result_table = Table(
                table_data,
                repeatRows=1,
                colWidths=[120, 185, 65, 135],
                hAlign="LEFT",
            )
            result_table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9f2fd")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1f4364")),
                        ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#d5deea")),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 5),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            story.append(result_table)

            if chunk_start + chunk_size < len(prepared_rows):
                story.append(PageBreak())

        doc.build(story)
        pdf_buffer.seek(0)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        username_slug = safe_filename_part(str(user["username"]), fallback="user")
        filename = f"recognition_results_{username_slug}_{timestamp}.pdf"
        return send_file(
            pdf_buffer,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
        )

    @app.get("/api/history")
    @login_required_api
    def history_list():
        user = session_user()
        db = get_db()
        search_query = (request.args.get("search") or "").strip().lower()
        source_filter = (request.args.get("source") or "").strip().lower()
        is_admin_scope = user["role"] == "admin" and request.args.get("scope") == "all"

        where_clauses: list[str] = []
        params: list[object] = []
        if not is_admin_scope:
            where_clauses.append("h.user_id = ?")
            params.append(int(user["id"]))

        if source_filter and source_filter != "all":
            where_clauses.append("LOWER(h.source) = ?")
            params.append(source_filter)

        if search_query:
            like_query = f"%{search_query}%"
            where_clauses.append(
                """
                (
                    CAST(h.user_id AS TEXT) LIKE ?
                    OR LOWER(u.username) LIKE ?
                    OR LOWER(u.full_name) LIKE ?
                    OR LOWER(h.file_name) LIKE ?
                    OR LOWER(h.prediction) LIKE ?
                    OR LOWER(h.source) LIKE ?
                )
                """
            )
            params.extend([like_query] * 6)

        where_sql = ""
        if where_clauses:
            where_sql = "WHERE " + " AND ".join(where_clauses)

        base_from_sql = """
            FROM detection_history h
            JOIN users u ON u.id = h.user_id
        """
        rows = db.execute(
            f"""
            SELECT h.id, h.user_id, u.username, u.full_name, h.file_name, h.prediction,
                   h.confidence, h.source, h.image_path, h.created_at, h.updated_at
            {base_from_sql}
            {where_sql}
            ORDER BY h.created_at DESC
            """,
            params,
        ).fetchall()

        total_row = db.execute(
            f"""
            SELECT COUNT(*) AS total
            {base_from_sql}
            {where_sql}
            """,
            params,
        ).fetchone()
        total = int(total_row["total"] or 0) if total_row is not None else len(rows)

        return jsonify({"ok": True, "history": [dict(row) for row in rows], "total": total})

    @app.get("/api/history/<int:record_id>")
    @login_required_api
    def history_detail(record_id: int):
        user = session_user()
        db = get_db()
        row = db.execute(
            """
            SELECT h.id, h.user_id, u.username, u.full_name, h.file_name, h.prediction,
                   h.confidence, h.source, h.image_path, h.created_at, h.updated_at
            FROM detection_history h
            JOIN users u ON u.id = h.user_id
            WHERE h.id = ?
            """,
            (record_id,),
        ).fetchone()
        if row is None:
            return jsonify({"ok": False, "error": "Record not found"}), 404

        if user["role"] != "admin" and int(row["user_id"]) != int(user["id"]):
            return jsonify({"ok": False, "error": "Not allowed"}), 403

        upload_root = Path(app.config["UPLOAD_ROOT"]).resolve()
        image_path = (row["image_path"] or "").strip()
        image_exists = False
        if image_path:
            candidate = (upload_root / image_path).resolve()
            try:
                candidate.relative_to(upload_root)
                image_exists = candidate.exists() and candidate.is_file()
            except ValueError:
                image_exists = False

        record = dict(row)
        record["image_available"] = image_exists
        record["image_url"] = f"/api/history/{record_id}/image" if image_exists else ""
        return jsonify({"ok": True, "record": record})

    @app.get("/api/history/<int:record_id>/image")
    @login_required_api
    def history_image(record_id: int):
        user = session_user()
        db = get_db()
        row = db.execute(
            """
            SELECT user_id, image_path
            FROM detection_history
            WHERE id = ?
            """,
            (record_id,),
        ).fetchone()
        if row is None:
            return jsonify({"ok": False, "error": "Record not found"}), 404

        if user["role"] != "admin" and int(row["user_id"]) != int(user["id"]):
            return jsonify({"ok": False, "error": "Not allowed"}), 403

        image_path = (row["image_path"] or "").strip()
        if not image_path:
            return jsonify({"ok": False, "error": "No image for this record"}), 404

        upload_root = Path(app.config["UPLOAD_ROOT"]).resolve()
        source_image = (upload_root / image_path).resolve()
        try:
            source_image.relative_to(upload_root)
        except ValueError:
            return jsonify({"ok": False, "error": "Invalid image path"}), 400

        if not source_image.exists() or not source_image.is_file():
            return jsonify({"ok": False, "error": "Image file not found"}), 404

        return send_file(source_image)

    @app.get("/api/history/<int:record_id>/export/pdf")
    @login_required_api
    def history_export_pdf(record_id: int):
        if not REPORTLAB_AVAILABLE:
            return jsonify({"ok": False, "error": "PDF export is unavailable. Install reportlab first."}), 500

        user = session_user()
        db = get_db()
        row = db.execute(
            """
            SELECT h.id, h.user_id, h.file_name, h.prediction, h.confidence, h.source,
                   h.image_path, h.created_at, h.updated_at, u.username, u.full_name
            FROM detection_history h
            JOIN users u ON u.id = h.user_id
            WHERE h.id = ?
            """,
            (record_id,),
        ).fetchone()
        if row is None:
            return jsonify({"ok": False, "error": "Record not found"}), 404

        if user["role"] != "admin" and int(row["user_id"]) != int(user["id"]):
            return jsonify({"ok": False, "error": "Not allowed"}), 403

        def clean_text(value: object, fallback: str = "-", max_len: int | None = None) -> str:
            text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
            if not text:
                text = fallback
            if max_len is not None and len(text) > max_len:
                text = text[: max_len - 3] + "..."
            return text

        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        confidence_pct = f"{round(float(row['confidence'] or 0.0) * 100)}%"
        prediction_text = clean_text(row["prediction"], fallback="(blank)", max_len=1200)
        pdf_buffer = io.BytesIO()

        doc = SimpleDocTemplate(
            pdf_buffer,
            pagesize=A4,
            leftMargin=36,
            rightMargin=36,
            topMargin=36,
            bottomMargin=36,
            title=f"Detection Record - {record_id}",
            author="Handwriting Recognition Portal",
        )
        styles = getSampleStyleSheet()
        meta_style = ParagraphStyle(
            "HistoryMetaStyle",
            parent=styles["BodyText"],
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#4e5d71"),
        )
        story: list[object] = []

        story.append(Paragraph("Detection History Record", styles["Title"]))
        story.append(Paragraph(f"Generated at (UTC): {xml_escape(generated_at)}", meta_style))
        story.append(Spacer(1, 10))

        details_table = Table(
            [
                ["Record ID", str(int(row["id"]))],
                ["Name", clean_text(row["full_name"], fallback=clean_text(row["username"]))],
                ["E-mail (username)", clean_text(row["username"])],
                ["File", clean_text(row["file_name"], max_len=120)],
                ["Confidence", confidence_pct],
                ["Source", clean_text(row["source"])],
                ["Detected At", clean_text(row["created_at"])],
                ["Updated At", clean_text(row["updated_at"])],
            ],
            colWidths=[150, 355],
            hAlign="LEFT",
        )
        details_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f5fc")),
                    ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#1f3b57")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d5deea")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(details_table)
        story.append(Spacer(1, 12))

        story.append(Paragraph("Prediction Text", styles["Heading2"]))
        story.append(Spacer(1, 4))
        story.append(Paragraph(xml_escape(prediction_text).replace("\n", "<br/>"), styles["BodyText"]))

        image_path = clean_text(row["image_path"], fallback="").strip()
        if image_path:
            upload_root = Path(app.config["UPLOAD_ROOT"]).resolve()
            source_image = (upload_root / image_path).resolve()
            try:
                source_image.relative_to(upload_root)
                if source_image.exists() and source_image.is_file():
                    story.append(PageBreak())
                    story.append(Paragraph("Uploaded Image", styles["Heading2"]))
                    story.append(Spacer(1, 6))
                    try:
                        preview = PDFImage(str(source_image))
                        preview._restrictSize(6.0 * inch, 4.0 * inch)
                        story.append(preview)
                    except Exception:
                        story.append(Paragraph("Image preview unavailable for this record.", meta_style))
            except ValueError:
                pass

        doc.build(story)
        pdf_buffer.seek(0)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        file_slug = safe_filename_part(row["file_name"], fallback=f"record_{record_id}")
        filename = f"history_{record_id}_{file_slug}_{timestamp}.pdf"
        if user["role"] == "admin":
            log_admin_activity(
                "EXPORT_HISTORY_PDF",
                target_type="history_record",
                target_id=str(record_id),
                details=f"Exported history PDF for record {record_id}",
                commit=True,
            )
        return send_file(
            pdf_buffer,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
        )

    @app.put("/api/history/<int:record_id>")
    @login_required_api
    def history_update(record_id: int):
        user = session_user()
        payload = request.get_json(silent=True) or {}

        db = get_db()
        row = db.execute(
            "SELECT id, user_id FROM detection_history WHERE id = ?",
            (record_id,),
        ).fetchone()
        if row is None:
            return jsonify({"ok": False, "error": "Record not found"}), 404

        if user["role"] != "admin" and int(row["user_id"]) != int(user["id"]):
            return jsonify({"ok": False, "error": "Not allowed"}), 403

        updates = []
        params: list[object] = []

        if "file_name" in payload:
            updates.append("file_name = ?")
            params.append((payload.get("file_name") or "").strip() or "untitled")
        if "prediction" in payload:
            updates.append("prediction = ?")
            params.append((payload.get("prediction") or "").strip())
        if "confidence" in payload:
            try:
                confidence = float(payload.get("confidence"))
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "Invalid confidence value"}), 400
            updates.append("confidence = ?")
            params.append(confidence)

        if not updates:
            return jsonify({"ok": False, "error": "No updatable fields provided"}), 400

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(record_id)

        db.execute(
            f"UPDATE detection_history SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        if user["role"] == "admin":
            changed_fields = [field.split(" = ")[0] for field in updates if field != "updated_at = CURRENT_TIMESTAMP"]
            insert_admin_activity(
                admin_user_id=int(user["id"]),
                action="UPDATE_HISTORY",
                target_type="history_record",
                target_id=str(record_id),
                details=f"Updated history record {record_id}; fields: {', '.join(changed_fields)}",
            )
        db.commit()

        updated = db.execute(
            """
            SELECT h.id, h.user_id, u.username, u.full_name, h.file_name, h.prediction,
                   h.confidence, h.source, h.created_at, h.updated_at
            FROM detection_history h
            JOIN users u ON u.id = h.user_id
            WHERE h.id = ?
            """,
            (record_id,),
        ).fetchone()

        return jsonify({"ok": True, "record": dict(updated)})

    @app.delete("/api/history/<int:record_id>")
    @login_required_api
    def history_delete(record_id: int):
        user = session_user()
        db = get_db()

        row = db.execute(
            "SELECT id, user_id FROM detection_history WHERE id = ?",
            (record_id,),
        ).fetchone()
        if row is None:
            return jsonify({"ok": False, "error": "Record not found"}), 404

        if user["role"] != "admin" and int(row["user_id"]) != int(user["id"]):
            return jsonify({"ok": False, "error": "Not allowed"}), 403

        db.execute("DELETE FROM detection_history WHERE id = ?", (record_id,))
        if user["role"] == "admin":
            insert_admin_activity(
                admin_user_id=int(user["id"]),
                action="DELETE_HISTORY",
                target_type="history_record",
                target_id=str(record_id),
                details=f"Deleted history record {record_id}",
            )
        db.commit()
        return jsonify({"ok": True})

    @app.get("/api/admin/users")
    @admin_required_api
    def admin_users_list():
        db = get_db()
        rows = db.execute(
            """
            SELECT id, username, full_name, role, created_at, last_login
            FROM users
            ORDER BY created_at DESC
            """
        ).fetchall()
        return jsonify({"ok": True, "users": [dict(row) for row in rows]})

    @app.get("/api/admin/users/<int:user_id>/export/pdf")
    @admin_required_api
    def admin_user_pdf_export(user_id: int):
        if not REPORTLAB_AVAILABLE:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "PDF export is unavailable. Install reportlab first.",
                    }
                ),
                500,
            )

        db = get_db()
        user_row = db.execute(
            """
            SELECT id, username, full_name, role, created_at, last_login
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()
        if user_row is None:
            return jsonify({"ok": False, "error": "User not found"}), 404
        if user_row["role"] != "user":
            return jsonify({"ok": False, "error": "PDF export is allowed only for user accounts"}), 400

        history_rows = db.execute(
            """
            SELECT id, file_name, prediction, confidence, source, image_path, created_at, updated_at
            FROM detection_history
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        ).fetchall()

        def clean_text(value: object, fallback: str = "-", max_len: int | None = None) -> str:
            text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
            if not text:
                text = fallback
            if max_len is not None and len(text) > max_len:
                text = text[: max_len - 3] + "..."
            return text

        def chunk_rows(rows: list[sqlite3.Row], size: int) -> list[list[sqlite3.Row]]:
            return [rows[idx : idx + size] for idx in range(0, len(rows), size)]

        username = clean_text(user_row["username"], fallback=f"user_{user_id}")
        full_name = clean_text(user_row["full_name"], fallback=username)
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        pdf_buffer = io.BytesIO()

        doc = SimpleDocTemplate(
            pdf_buffer,
            pagesize=A4,
            leftMargin=36,
            rightMargin=36,
            topMargin=36,
            bottomMargin=36,
            title=f"User Report - {username}",
            author="Handwriting Recognition Portal",
        )
        styles = getSampleStyleSheet()
        meta_style = ParagraphStyle(
            "MetaStyle",
            parent=styles["BodyText"],
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#4e5d71"),
        )
        story: list[object] = []

        story.append(Paragraph("User Information Export", styles["Title"]))
        story.append(Paragraph(f"Generated at (UTC): {xml_escape(generated_at)}", meta_style))
        story.append(Spacer(1, 10))

        details_table = Table(
            [
                ["Name", full_name],
                ["E-mail (username)", username],
                ["Role", clean_text(user_row["role"])],
                ["Account Created", clean_text(user_row["created_at"])],
                ["Last Login", clean_text(user_row["last_login"])],
                ["Total Detections", str(len(history_rows))],
            ],
            colWidths=[140, 365],
            hAlign="LEFT",
        )
        details_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f5fc")),
                    ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#1f3b57")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d5deea")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(details_table)
        story.append(Spacer(1, 12))
        story.append(Paragraph("Detection History", styles["Heading2"]))
        story.append(Spacer(1, 4))

        if history_rows:
            history_chunks = chunk_rows(history_rows, 24)
            for chunk_index, chunk in enumerate(history_chunks):
                table_data = [["Time (UTC)", "File", "Prediction", "Conf", "Source"]]
                for row in chunk:
                    confidence_pct = f"{round(float(row['confidence'] or 0.0) * 100)}%"
                    table_data.append(
                        [
                            clean_text(row["created_at"], max_len=19),
                            clean_text(row["file_name"], max_len=28),
                            clean_text(row["prediction"], fallback="(blank)", max_len=62),
                            confidence_pct,
                            clean_text(row["source"], max_len=12),
                        ]
                    )

                history_table = Table(
                    table_data,
                    repeatRows=1,
                    colWidths=[92, 100, 206, 50, 57],
                    hAlign="LEFT",
                )
                history_table.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9f2fd")),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1f4364")),
                            ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#d5deea")),
                            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                            ("FONTSIZE", (0, 0), (-1, -1), 9),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 5),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                            ("TOPPADDING", (0, 0), (-1, -1), 4),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                        ]
                    )
                )
                story.append(history_table)
                if chunk_index < len(history_chunks) - 1:
                    story.append(PageBreak())
                    story.append(Paragraph("Detection History (continued)", styles["Heading2"]))
                    story.append(Spacer(1, 4))
        else:
            story.append(Paragraph("No detection history found for this user.", meta_style))

        upload_root = Path(app.config["UPLOAD_ROOT"]).resolve()
        image_entries: list[tuple[Path, sqlite3.Row]] = []
        for row in history_rows:
            image_path = (row["image_path"] or "").strip()
            if not image_path:
                continue

            source_image = (upload_root / image_path).resolve()
            try:
                source_image.relative_to(upload_root)
            except ValueError:
                continue
            if source_image.exists() and source_image.is_file():
                image_entries.append((source_image, row))

        story.append(PageBreak())
        story.append(Paragraph("Uploaded Images", styles["Heading2"]))
        story.append(Spacer(1, 4))
        if image_entries:
            for idx, (image_file, row) in enumerate(image_entries, start=1):
                story.append(
                    Paragraph(
                        f"{idx}. {xml_escape(clean_text(row['file_name'], max_len=80))} ({xml_escape(clean_text(row['created_at']))})",
                        styles["BodyText"],
                    )
                )
                try:
                    preview = PDFImage(str(image_file))
                    preview._restrictSize(5.8 * inch, 3.4 * inch)
                    story.append(preview)
                except Exception:
                    story.append(Paragraph("Image preview unavailable for this record.", meta_style))

                prediction_text = xml_escape(clean_text(row["prediction"], fallback="(blank)", max_len=300))
                confidence_pct = f"{round(float(row['confidence'] or 0.0) * 100)}%"
                story.append(Paragraph(f"Prediction: {prediction_text}", meta_style))
                story.append(Paragraph(f"Confidence: {confidence_pct}", meta_style))
                story.append(Spacer(1, 10))
        else:
            story.append(Paragraph("No stored images available for this user.", meta_style))

        doc.build(story)
        pdf_buffer.seek(0)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"user_{safe_filename_part(username)}_report_{timestamp}.pdf"
        current_admin = session_user()
        if current_admin is not None and current_admin["role"] == "admin":
            log_admin_activity(
                "EXPORT_USER_PDF",
                target_type="user",
                target_id=str(int(user_row["id"])),
                details=f"Exported user PDF report for {username}",
                commit=True,
            )
        return send_file(
            pdf_buffer,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
        )

    @app.post("/api/admin/users")
    @admin_required_api
    def admin_users_create():
        payload = request.get_json(silent=True) or {}
        username = normalize_username(payload.get("username", ""))
        full_name = (payload.get("full_name") or "").strip() or username
        password = payload.get("password") or ""
        role = (payload.get("role") or "user").strip().lower()

        if role not in {"admin", "user"}:
            return jsonify({"ok": False, "error": "Role must be admin or user"}), 400
        if len(username) < 3:
            return jsonify({"ok": False, "error": "Username must be at least 3 characters"}), 400
        if len(password) < 6:
            return jsonify({"ok": False, "error": "Password must be at least 6 characters"}), 400

        db = get_db()
        current_admin = session_user()
        try:
            cur = db.execute(
                """
                INSERT INTO users (username, full_name, password_hash, role)
                VALUES (?, ?, ?, ?)
                """,
                (username, full_name, generate_password_hash(password), role),
            )
            if current_admin is not None and current_admin["role"] == "admin":
                insert_admin_activity(
                    admin_user_id=int(current_admin["id"]),
                    action="CREATE_USER",
                    target_type="user",
                    target_id=str(int(cur.lastrowid)),
                    details=f"Created {role} account: {username}",
                )
            db.commit()
        except sqlite3.IntegrityError:
            return jsonify({"ok": False, "error": "Username already exists"}), 400

        return jsonify({"ok": True})

    @app.put("/api/admin/users/<int:user_id>")
    @admin_required_api
    def admin_users_update(user_id: int):
        payload = request.get_json(silent=True) or {}
        db = get_db()
        user_row = db.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
        if user_row is None:
            return jsonify({"ok": False, "error": "User not found"}), 404

        updates = []
        params: list[object] = []

        if "username" in payload:
            username = normalize_username(payload.get("username") or "")
            if len(username) < 3:
                return jsonify({"ok": False, "error": "Username must be at least 3 characters"}), 400
            updates.append("username = ?")
            params.append(username)

        if "full_name" in payload:
            updates.append("full_name = ?")
            params.append((payload.get("full_name") or "").strip())

        if "role" in payload:
            role = (payload.get("role") or "").strip().lower()
            if role not in {"admin", "user"}:
                return jsonify({"ok": False, "error": "Role must be admin or user"}), 400
            updates.append("role = ?")
            params.append(role)

        if "password" in payload and payload.get("password"):
            password = payload.get("password")
            if len(password) < 6:
                return jsonify({"ok": False, "error": "Password must be at least 6 characters"}), 400
            updates.append("password_hash = ?")
            params.append(generate_password_hash(password))

        if not updates:
            return jsonify({"ok": False, "error": "No updatable fields provided"}), 400

        params.append(user_id)

        try:
            db.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
            current_admin = session_user()
            if current_admin is not None and current_admin["role"] == "admin":
                changed_fields = [field.split(" = ")[0] for field in updates]
                insert_admin_activity(
                    admin_user_id=int(current_admin["id"]),
                    action="UPDATE_USER",
                    target_type="user",
                    target_id=str(user_id),
                    details=(
                        f"Updated user {user_row['username']} (id={user_id}); "
                        f"fields: {', '.join(changed_fields)}"
                    ),
                )
            db.commit()
        except sqlite3.IntegrityError:
            return jsonify({"ok": False, "error": "Username already exists"}), 400

        return jsonify({"ok": True})

    @app.delete("/api/admin/users/<int:user_id>")
    @admin_required_api
    def admin_users_delete(user_id: int):
        user = session_user()
        if int(user["id"]) == user_id:
            return jsonify({"ok": False, "error": "You cannot delete your own account"}), 400

        db = get_db()
        row = db.execute("SELECT id, role, username FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            return jsonify({"ok": False, "error": "User not found"}), 404
        if row["role"] == "admin":
            return jsonify({"ok": False, "error": "Admin accounts cannot be deleted"}), 400

        db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        insert_admin_activity(
            admin_user_id=int(user["id"]),
            action="DELETE_USER",
            target_type="user",
            target_id=str(user_id),
            details=f"Deleted user account: {row['username']}",
        )
        db.commit()
        return jsonify({"ok": True})

    @app.get("/api/admin/overview")
    @admin_required_api
    def admin_overview():
        db = get_db()

        stats = db.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM users) AS total_users,
              (SELECT COUNT(*) FROM detection_history) AS total_records,
              (
                SELECT COUNT(DISTINCT user_id)
                FROM user_sessions
                WHERE is_active = 1
                  AND datetime(last_seen) >= datetime('now', '-30 minutes')
              ) AS active_users
            """
        ).fetchone()

        uploads = db.execute(
            """
            SELECT h.id, h.file_name, h.prediction, h.confidence, h.source,
                   h.created_at, u.username, u.full_name
            FROM detection_history h
            JOIN users u ON u.id = h.user_id
            ORDER BY h.created_at DESC
            LIMIT 100
            """
        ).fetchall()

        return jsonify(
            {
                "ok": True,
                "stats": {
                    "active_users": int(stats["active_users"] or 0),
                    "total_users": int(stats["total_users"] or 0),
                    "total_records": int(stats["total_records"] or 0),
                },
                "uploads": [dict(row) for row in uploads],
            }
        )

    @app.get("/api/admin/activity-report")
    @admin_required_api
    def admin_activity_report():
        limit = clamp_int(request.args.get("limit"), default=200, min_value=20, max_value=1000)
        db = get_db()
        rows = db.execute(
            """
            SELECT l.id, l.action, l.target_type, l.target_id, l.details, l.ip_address, l.created_at,
                   u.username AS admin_username, u.full_name AS admin_full_name
            FROM admin_activity_logs l
            JOIN users u ON u.id = l.admin_user_id
            ORDER BY l.created_at DESC, l.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return jsonify({"ok": True, "report": [dict(row) for row in rows]})

    @app.get("/api/admin/uploads")
    @admin_required_api
    def admin_uploads_list():
        limit = clamp_int(request.args.get("limit"), default=200, min_value=10, max_value=1000)
        db = get_db()
        rows = db.execute(
            """
            SELECT h.id, h.file_name, h.confidence, h.source, h.created_at,
                   u.username, u.full_name
            FROM detection_history h
            JOIN users u ON u.id = h.user_id
            ORDER BY h.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return jsonify({"ok": True, "uploads": [dict(row) for row in rows]})

    @app.get("/api/admin/export")
    @admin_required_api
    def admin_export():
        current_admin = session_user()
        db = get_db()
        upload_root = Path(app.config["UPLOAD_ROOT"])

        users = db.execute(
            """
            SELECT id, username, full_name, role, created_at, last_login
            FROM users
            ORDER BY id ASC
            """
        ).fetchall()

        history = db.execute(
            """
            SELECT h.id, h.user_id, u.username, u.full_name, h.file_name, h.prediction,
                   h.confidence, h.source, h.filters_json, h.image_path, h.created_at, h.updated_at
            FROM detection_history h
            JOIN users u ON u.id = h.user_id
            ORDER BY h.created_at DESC
            """
        ).fetchall()

        buffer = io.BytesIO()
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as bundle:
            users_csv = io.StringIO()
            users_writer = csv.DictWriter(
                users_csv,
                fieldnames=["id", "name", "email", "role", "created_at", "last_login"],
            )
            users_writer.writeheader()
            for row in users:
                users_writer.writerow(
                    {
                        "id": int(row["id"]),
                        "name": row["full_name"] or "",
                        "email": row["username"] or "",
                        "role": row["role"] or "",
                        "created_at": row["created_at"] or "",
                        "last_login": row["last_login"] or "",
                    }
                )
            bundle.writestr("users.csv", users_csv.getvalue())

            history_csv = io.StringIO()
            history_writer = csv.DictWriter(
                history_csv,
                fieldnames=[
                    "record_id",
                    "user_id",
                    "name",
                    "email",
                    "file_name",
                    "prediction",
                    "confidence",
                    "source",
                    "created_at",
                    "updated_at",
                    "image_file",
                ],
            )
            history_writer.writeheader()

            copied_images = 0
            for row in history:
                archive_image_path = ""
                image_path = (row["image_path"] or "").strip()
                if image_path:
                    source_image = upload_root / image_path
                    if source_image.exists() and source_image.is_file():
                        archive_image_path = f"images/{image_path}"
                        bundle.write(source_image, arcname=archive_image_path)
                        copied_images += 1

                history_writer.writerow(
                    {
                        "record_id": int(row["id"]),
                        "user_id": int(row["user_id"]),
                        "name": row["full_name"] or "",
                        "email": row["username"] or "",
                        "file_name": row["file_name"] or "",
                        "prediction": row["prediction"] or "",
                        "confidence": row["confidence"] or 0.0,
                        "source": row["source"] or "",
                        "created_at": row["created_at"] or "",
                        "updated_at": row["updated_at"] or "",
                        "image_file": archive_image_path,
                    }
                )

            bundle.writestr("detection_history.csv", history_csv.getvalue())
            bundle.writestr(
                "export_info.json",
                json.dumps(
                    {
                        "generated_at_utc": datetime.now(timezone.utc)
                        .isoformat(timespec="seconds")
                        .replace("+00:00", "Z"),
                        "users_count": len(users),
                        "history_count": len(history),
                        "images_included": copied_images,
                        "note": "email column maps to username in this project schema.",
                    },
                    indent=2,
                ),
            )

        buffer.seek(0)
        filename = f"admin_export_{timestamp}.zip"
        if current_admin is not None and current_admin["role"] == "admin":
            log_admin_activity(
                "EXPORT_ALL_DATA",
                target_type="system_export",
                target_id=timestamp,
                details=f"Exported all data bundle (users={len(users)}, history={len(history)})",
                commit=True,
            )
        return send_file(
            buffer,
            mimetype="application/zip",
            as_attachment=True,
            download_name=filename,
        )

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run handwriting recognition web portal")
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--checkpoint",
        type=str,
        default=os.getenv("OCR_CHECKPOINT", "checkpoints/fix2/best.pt"),
        help="Path to trained checkpoint (best.pt)",
    )
    parser.add_argument(
        "--device",
        type=str,
        default=os.getenv("OCR_DEVICE", "auto"),
        help="auto|cpu|cuda|mps",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=os.getenv("OCR_DB_PATH", "data/app.db"),
        help="SQLite database file path",
    )
    return parser.parse_args()


_default_checkpoint = os.getenv("OCR_CHECKPOINT", "checkpoints/fix2/best.pt")
_default_device = os.getenv("OCR_DEVICE", "auto")
_default_db_path = os.getenv("OCR_DB_PATH", "data/app.db")
app = create_app(_default_checkpoint, _default_device, _default_db_path)


if __name__ == "__main__":
    args = parse_args()
    runtime_app = create_app(args.checkpoint, args.device, args.db_path)
    runtime_app.run(host=args.host, port=args.port, debug=args.debug)
