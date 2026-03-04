#!/usr/bin/env python3
"""
Generate project documentation PDF for the handwriting recognition portal.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PDF = ROOT / "output" / "pdf" / "project_documentation.pdf"
DB_PATH = ROOT / "data" / "app.db"

SCREENSHOTS = [
    {
        "title": "Screen 1: Login Page",
        "file": ROOT / "output" / "playwright" / "doc_01_login.png",
        "navigation": "Open /login -> fill username and password -> click Login.",
        "widgets": [
            ("Username input", "login-username"),
            ("Password input", "login-password"),
            ("Show/Hide password toggle", "data-toggle-password"),
            ("Login button", "login-form submit"),
            ("Create Account link", "show-register"),
            ("Forgot Password link", "show-reset"),
        ],
        "data_note": "Sample input filled with existing user credentials.",
    },
    {
        "title": "Screen 2: Create Account Page",
        "file": ROOT / "output" / "playwright" / "doc_02_create_account.png",
        "navigation": "Login page -> click Create Account -> fill form.",
        "widgets": [
            ("Full name input", "reg-full-name"),
            ("Username input", "reg-username"),
            ("Password input", "reg-password"),
            ("Confirm password input", "reg-confirm"),
            ("Create Account button", "register-form submit"),
            ("Back to Login link", "show-login-from-register"),
        ],
        "data_note": "Demo registration data shown for form validation and flow explanation.",
    },
    {
        "title": "Screen 3: Reset Password Page",
        "file": ROOT / "output" / "playwright" / "doc_03_reset_password.png",
        "navigation": "Login page -> click Forgot Password? -> fill reset details.",
        "widgets": [
            ("Username input", "reset-username"),
            ("New password input", "reset-new-password"),
            ("Confirm password input", "reset-confirm"),
            ("Reset Password button", "reset-form submit"),
            ("Back to Login link", "show-login-from-reset"),
            ("Create Account link", "show-register-from-reset"),
        ],
        "data_note": "Existing user ID and new password fields are filled for practical demo.",
    },
    {
        "title": "Screen 4: User Dashboard",
        "file": ROOT / "output" / "playwright" / "doc_04_user_dashboard.png",
        "navigation": "Login as user -> upload image -> keep filters set -> click Recognize Text.",
        "widgets": [
            ("Image input", "image-input"),
            ("Browse Files button", "browse-btn"),
            ("Recognize Text button", "run-btn"),
            ("Filter controls", "grayscale / denoise / adaptive-threshold"),
            ("Crop/Rotate controls", "preview crop/rotate buttons"),
            ("Profile menu trigger", "profile-toggle"),
        ],
        "data_note": "Notebook image is uploaded with active preprocessing filters.",
    },
    {
        "title": "Screen 5: User Recognition Results",
        "file": ROOT / "output" / "playwright" / "doc_05_user_results.png",
        "navigation": "After recognition, portal redirects to /results automatically.",
        "widgets": [
            ("Output Window textarea", "output-text"),
            ("Copy Text button", "copy-output"),
            ("Recognition Results list", "results-list"),
            ("Speak All button", "speak-all"),
            ("Stop Speech button", "stop-speech"),
            ("Detection History link", "top navigation link"),
        ],
        "data_note": "Sample OCR output and confidence are shown from uploaded handwritten image.",
    },
    {
        "title": "Screen 6: User Detection History",
        "file": ROOT / "output" / "playwright" / "doc_06_user_history.png",
        "navigation": "User clicks Detection History from top navigation.",
        "widgets": [
            ("History table body", "history-body"),
            ("View button", "row action"),
            ("Download PDF button", "row action"),
            ("Delete button", "row action"),
            ("Refresh button", "refresh-history"),
            ("Recognize Text link", "top navigation link"),
        ],
        "data_note": "User records with practical timestamps and action controls are displayed.",
    },
    {
        "title": "Screen 7: Admin Panel",
        "file": ROOT / "output" / "playwright" / "doc_07_admin_panel.png",
        "navigation": "Login as admin -> home page shows Admin Panel controls.",
        "widgets": [
            ("Detection History link", "admin action nav"),
            ("Admin Activity link", "admin action nav"),
            ("Download All User Data button", "download-admin-export"),
            ("Date & Time widget", "admin-live-datetime"),
            ("Current Location widget", "admin-live-location"),
            ("Add User form", "admin-full-name / admin-username / admin-password / admin-role"),
        ],
        "data_note": "Admin stats and user management controls shown with live database values.",
    },
    {
        "title": "Screen 8: Admin Detection History",
        "file": ROOT / "output" / "playwright" / "doc_08_admin_history.png",
        "navigation": "Admin panel -> Detection History -> apply search filter for user details.",
        "widgets": [
            ("Search input", "history-search-input"),
            ("Source filter dropdown", "history-source-filter"),
            ("Filtered record count", "history-filter-count"),
            ("Highlighted search matches", "history-search-highlight"),
            ("View button", "row action"),
            ("Download PDF button", "row action"),
        ],
        "data_note": "Search keyword filtering and highlighted results are visible for admin lookup.",
    },
    {
        "title": "Screen 9: Admin Activity Report",
        "file": ROOT / "output" / "playwright" / "doc_09_admin_activity.png",
        "navigation": "Admin panel -> Admin Activity -> view operation logs.",
        "widgets": [
            ("Search input", "activity-search-input"),
            ("Operation filter dropdown", "activity-action-filter"),
            ("Record count", "activity-count"),
            ("Activity table body", "activity-body"),
            ("Refresh button", "refresh-activity"),
            ("Navigation links", "Admin Panel / Detection History"),
        ],
        "data_note": "View-only report includes admin logins, delete actions, and timestamps.",
    },
    {
        "title": "Screen 10: Admin Uploads Page",
        "file": ROOT / "output" / "playwright" / "doc_10_admin_uploads.png",
        "navigation": "Admin opens /admin/uploads to inspect all user uploads.",
        "widgets": [
            ("Uploads status", "uploads-status"),
            ("Refresh button", "refresh-uploads"),
            ("Uploads table body", "uploads-body"),
            ("Admin Panel link", "top navigation link"),
            ("Detection History link", "top navigation link"),
            ("Profile menu button", "profile-toggle"),
        ],
        "data_note": "All uploads list includes user identity, file, confidence, source, and time.",
    },
]

DIAGRAMS = [
    (
        "4.1 Flowchart",
        """
[Start]
   |
   v
[Open /login]
   |
   v
[Authenticate User]
   |
   v
{Role Check}
 /         \\
v           v
[User]     [Admin]
  |          |
  v          v
[Upload]   [Manage Users]
  |          |
  v          v
[Preprocess + OCR/RNN]
  |
  v
[Store in SQLite]
  |
  v
[Show Results + History]
  |
  v
[End]
""".strip(
            "\n"
        ),
    ),
    (
        "4.2 Data Flow Diagram - Level 1",
        """
[User] ---- upload request ----> (0.0 Handwriting Portal) ---- save/read ----> [D1 SQLite]
[Admin] --- admin actions -----> (0.0 Handwriting Portal) ---- save/read ----> [D1 SQLite]
(0.0 Handwriting Portal) ---- image text request ----> [E1 OCR/RNN Engine]
[E1 OCR/RNN Engine] ---- prediction + confidence ----> (0.0 Handwriting Portal)
(0.0 Handwriting Portal) ---- results/history ----> [User]
(0.0 Handwriting Portal) ---- reports/logs ----> [Admin]
""".strip(
            "\n"
        ),
    ),
    (
        "4.3 Data Flow Diagram - Level 2",
        """
[User] -> (1.0 Auth) -> [D1 users]
[User] -> (2.0 Recognition UI) -> (3.0 OCR Pipeline) -> [E1 OCR/RNN]
(3.0 OCR Pipeline) -> [D2 detection_history]
(2.0 Recognition UI) <- [D2 detection_history]

[Admin] -> (4.0 Admin Panel) -> [D1 users]
[Admin] -> (5.0 Activity Report) -> [D3 admin_activity_logs]
[Admin] -> (6.0 Upload Monitor) -> [D2 detection_history]
""".strip(
            "\n"
        ),
    ),
    (
        "4.4 Data Flow Diagram - Level 3 (Recognition Pipeline)",
        """
[Image File]
   |
   v
(3.1 Validate + Save Upload) --> [D2 detection_history.image_path]
   |
   v
(3.2 Preprocess: grayscale/denoise/threshold/notebook mode)
   |
   v
(3.3 OCR Engine Select: auto/hybrid/rnn/tesseract)
   |
   v
(3.4 Decode + Cleanup + Confidence score)
   |
   v
(3.5 Persist prediction) --> [D2 detection_history.prediction]
   |
   v
(3.6 Return JSON + Results UI + Speech)
""".strip(
            "\n"
        ),
    ),
    (
        "4.5 Sequence Diagram",
        """
User Browser -> Flask App: POST /api/auth/login
Flask App -> SQLite: verify user + create session
SQLite -> Flask App: user role
Flask App -> User Browser: login success

User Browser -> Flask App: POST /api/predict (image + filters)
Flask App -> OCR/RNN Service: run preprocessing + inference
OCR/RNN Service -> Flask App: prediction, confidence, source
Flask App -> SQLite: insert detection_history row
Flask App -> User Browser: JSON result
User Browser -> Flask App: GET /results and GET /api/history
""".strip(
            "\n"
        ),
    ),
    (
        "4.6 Class Diagram (Conceptual)",
        """
+----------------+        +----------------+
| User           |1      *| UserSession    |
+----------------+--------+----------------+
| id             |        | id             |
| username       |        | user_id (FK)   |
| full_name      |        | is_active      |
| role           |        | login_at       |
| last_login     |        | logout_at      |
+----------------+        +----------------+
        |
        |1
        |      *
        v
+----------------------+
| DetectionRecord      |
+----------------------+
| id                   |
| user_id (FK)         |
| file_name            |
| prediction           |
| confidence           |
| source               |
| image_path           |
+----------------------+

+-----------------------+    uses    +-------------------+
| AppController (Flask) |----------->| OCRService        |
+-----------------------+            +-------------------+
                                     | run_predict()     |
                                     | resolve_engine()  |
                                     +-------------------+
""".strip(
            "\n"
        ),
    ),
    (
        "4.7 Activity Diagram",
        """
(Start)
   |
   v
[Login]
   |
   v
{Valid credentials?}
   |Yes                  |No
   v                     v
[Open Portal]        [Show error]
   |
   v
[Upload Image]
   |
   v
[Apply Filters]
   |
   v
[Recognize Text]
   |
   v
{Prediction available?}
   |Yes                  |No
   v                     v
[Show Output + Save] [Show fallback / retry]
   |
   v
[View Detection History]
   |
   v
(End)
""".strip(
            "\n"
        ),
    ),
    (
        "4.8 E-R Diagram",
        """
[users] 1 -------- * [detection_history]
  PK id                PK id
  username             FK user_id -> users.id
  full_name            file_name
  password_hash        prediction
  role                 confidence
  last_login           source
                       image_path
                       created_at

[users] 1 -------- * [user_sessions]
  PK id                PK id
                       FK user_id -> users.id
                       is_active
                       login_at
                       logout_at

[users] 1 -------- * [admin_activity_logs]
  PK id                PK id
                       FK admin_user_id -> users.id
                       action
                       target_type
                       target_id
                       details
                       ip_address
                       created_at
""".strip(
            "\n"
        ),
    ),
]


def page_number(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 9)
    canvas.setFillColor(colors.HexColor("#666666"))
    canvas.drawRightString(A4[0] - 1.8 * cm, 1.2 * cm, f"Page {doc.page}")
    canvas.restoreState()


def style_table(table: Table, header_color: str = "#0F4C81") -> None:
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_color)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#C8D2DC")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FBFF")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )


def scaled_image(path: Path, max_w: float, max_h: float) -> Image:
    img = Image(str(path))
    width = float(img.imageWidth)
    height = float(img.imageHeight)
    ratio = min(max_w / width, max_h / height)
    img.drawWidth = width * ratio
    img.drawHeight = height * ratio
    return img


def safe_count(conn: sqlite3.Connection, query: str) -> int:
    try:
        row = conn.execute(query).fetchone()
        if row is None:
            return 0
        return int(row[0] or 0)
    except sqlite3.Error:
        return 0


def get_db_counts(db_path: Path) -> dict[str, int]:
    if not db_path.exists():
        return {
            "users": 0,
            "admins": 0,
            "history": 0,
            "active_sessions": 0,
            "activity_logs": 0,
        }

    with sqlite3.connect(db_path) as conn:
        users = safe_count(conn, "SELECT COUNT(*) FROM users")
        admins = safe_count(conn, "SELECT COUNT(*) FROM users WHERE role = 'admin'")
        history = safe_count(conn, "SELECT COUNT(*) FROM detection_history")
        active_sessions = safe_count(conn, "SELECT COUNT(*) FROM user_sessions WHERE is_active = 1")
        activity_logs = safe_count(conn, "SELECT COUNT(*) FROM admin_activity_logs")
    return {
        "users": users,
        "admins": admins,
        "history": history,
        "active_sessions": active_sessions,
        "activity_logs": activity_logs,
    }


def add_data_dictionary_table(
    story: list,
    title: str,
    rows: list[list[str]],
    normal_style: ParagraphStyle,
) -> None:
    story.append(Paragraph(f"Table: {title}", normal_style))
    table = Table(rows, colWidths=[3.3 * cm, 3.2 * cm, 10.2 * cm])
    style_table(table)
    story.append(table)
    story.append(Spacer(1, 0.2 * cm))


def build_pdf() -> Path:
    OUTPUT_PDF.parent.mkdir(parents=True, exist_ok=True)
    counts = get_db_counts(DB_PATH)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleMain",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=24,
        leading=30,
        textColor=colors.HexColor("#0F4C81"),
        alignment=1,
    )
    subtitle_style = ParagraphStyle(
        "Subtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#333333"),
        alignment=1,
    )
    h1 = ParagraphStyle(
        "H1",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=16,
        leading=21,
        textColor=colors.HexColor("#0F4C81"),
        spaceAfter=8,
    )
    h2 = ParagraphStyle(
        "H2",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#0F4C81"),
        spaceBefore=8,
        spaceAfter=6,
    )
    normal = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#222222"),
    )
    small = ParagraphStyle(
        "Small",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#333333"),
    )
    code_style = ParagraphStyle(
        "Code",
        parent=styles["Code"],
        fontName="Courier",
        fontSize=8.4,
        leading=10.6,
        backColor=colors.HexColor("#F3F6FA"),
        borderPadding=6,
    )

    doc = SimpleDocTemplate(
        str(OUTPUT_PDF),
        pagesize=A4,
        leftMargin=1.8 * cm,
        rightMargin=1.8 * cm,
        topMargin=1.8 * cm,
        bottomMargin=1.7 * cm,
    )

    story: list = []

    # Cover page
    story.append(Spacer(1, 2.8 * cm))
    story.append(Paragraph("PROJECT DOCUMENTATION", title_style))
    story.append(Spacer(1, 0.45 * cm))
    story.append(
        Paragraph(
            "Handwriting Recognition Using RNN (CRNN + OCR Hybrid Pipeline)",
            subtitle_style,
        )
    )
    story.append(Spacer(1, 0.5 * cm))
    story.append(
        Paragraph(
            "Prepared for viva / practical exam with full UI navigation, diagrams, and implementation details.",
            subtitle_style,
        )
    )
    story.append(Spacer(1, 0.55 * cm))
    story.append(Paragraph(f"Generated on: {datetime.now().strftime('%d %B %Y, %I:%M %p')}", subtitle_style))
    story.append(Spacer(1, 0.15 * cm))
    story.append(
        Paragraph(
            "Screenshot source: Chrome-compatible browser workflow (Playwright Chromium) on localhost portal.",
            subtitle_style,
        )
    )
    story.append(Spacer(1, 0.45 * cm))
    story.append(
        Paragraph(
            "Run command: <b>./run_portal.sh</b>  (custom port: <b>PORT=5050 ./run_portal.sh</b>)",
            subtitle_style,
        )
    )
    story.append(PageBreak())

    # 1. Table of contents
    story.append(Paragraph("1. Table of Contents", h1))
    toc_rows = [
        ["Section", "Coverage"],
        ["1. Table of Contents", "Document section index"],
        ["2. Introduction", "Objective, purpose, technologies, backend/frontend, and database"],
        ["3. Time Line Chart", "Phase-wise project timeline from planning to viva readiness"],
        ["4. System Diagrams", "Flowchart, DFD Level 1/2/3, Sequence, Class, Activity, and E-R diagram"],
        ["5. Data Dictionary", "Database tables, fields, types, and relationships"],
        ["6. Project Modules", "Functional module breakdown"],
        ["7. Screenshots of App", "All UI screens with widget labels and navigation explanation"],
        ["8. Conclusion", "Summary of learnings and challenges"],
        ["9. References", "API references, documentation, and tutorials"],
        ["Appendix A", "Viva/practical exam navigation script and run commands"],
    ]
    toc_table = Table(toc_rows, colWidths=[5.3 * cm, 11.4 * cm])
    style_table(toc_table)
    story.append(toc_table)
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph("Primary Run Command: <b>./run_portal.sh</b>", normal))
    story.append(PageBreak())

    # 2. Introduction
    story.append(Paragraph("2. Introduction", h1))
    story.append(Paragraph("Project Objective and Purpose", h2))
    story.append(
        Paragraph(
            "The objective of this project is to detect handwritten notebook text accurately from uploaded images "
            "using an RNN-based model with OCR fallback, and provide a complete web portal for user and admin roles. "
            "The portal supports authentication, image upload, preprocessing, recognition, output view, speech, "
            "history management, and admin activity monitoring.",
            normal,
        )
    )
    story.append(Spacer(1, 0.2 * cm))

    story.append(Paragraph("Technologies Used in Frontend and Backend", h2))
    tech_rows = [
        ["Layer", "Technologies Used"],
        ["Frontend", "HTML5, CSS3, Vanilla JavaScript"],
        ["Backend", "Python 3.13, Flask (page routes + REST APIs)"],
        ["OCR / ML", "PyTorch CRNN checkpoint + pytesseract fallback"],
        ["Database", "SQLite (data/app.db)"],
        ["PDF/Reporting", "ReportLab"],
        ["Browser Automation", "Playwright (Chromium) for UI screenshot capture"],
    ]
    tech_table = Table(tech_rows, colWidths=[4.5 * cm, 12.2 * cm])
    style_table(tech_table)
    story.append(tech_table)
    story.append(Spacer(1, 0.2 * cm))

    story.append(Paragraph("Database Used", h2))
    story.append(
        Paragraph(
            "The project uses <b>SQLite</b> database stored at <b>data/app.db</b>. "
            f"Current snapshot: users={counts['users']}, admins={counts['admins']}, "
            f"detection_history={counts['history']}, active_sessions={counts['active_sessions']}, "
            f"admin_activity_logs={counts['activity_logs']}.",
            normal,
        )
    )
    story.append(Spacer(1, 0.2 * cm))

    story.append(Paragraph("Run Project Command", h2))
    run_cmd = (
        "$ ./run_portal.sh\n"
        "# custom host/port\n"
        "$ HOST=127.0.0.1 PORT=5050 ./run_portal.sh\n\n"
        "# equivalent direct command\n"
        "$ .venv313/bin/python app.py --host 127.0.0.1 --port 5050"
    )
    story.append(Preformatted(run_cmd, code_style))
    story.append(PageBreak())

    # 3. Timeline chart
    story.append(Paragraph("3. Time Line Chart", h1))
    story.append(
        Paragraph(
            "The following timeline chart summarizes implementation flow for this college project.",
            normal,
        )
    )
    story.append(Spacer(1, 0.2 * cm))
    timeline_chart = """
Weeks:   W1     W2     W3     W4     W5     W6
--------------------------------------------------------
Planning + Requirement    [#####]
Auth + Role UI            [##########]
OCR/RNN Integration              [###############]
History + Admin Modules                 [###########]
Testing + Bug Fixing                          [########]
Documentation + Viva Prep                           [########]
""".strip(
        "\n"
    )
    story.append(Preformatted(timeline_chart, code_style))
    story.append(Spacer(1, 0.2 * cm))

    timeline_rows = [
        ["Phase", "Duration", "Outputs"],
        ["Planning and requirement finalization", "Week 1", "Feature list, role definitions, DB schema draft"],
        ["Frontend auth and navigation", "Week 1-2", "Login/create/reset pages with validation and role redirects"],
        ["Recognition pipeline integration", "Week 2-4", "Upload -> preprocess -> OCR/RNN -> output flow"],
        ["History + admin operations", "Week 4-5", "Detection history, admin controls, activity logs"],
        ["Testing and bug fixing", "Week 5-6", "Flow stabilization and UX fixes"],
        ["Documentation + viva preparation", "Week 6", "PDF report, screenshots, diagrams, demo script"],
    ]
    timeline_table = Table(timeline_rows, colWidths=[5.6 * cm, 2.6 * cm, 8.5 * cm])
    style_table(timeline_table, header_color="#155A8A")
    story.append(timeline_table)
    story.append(PageBreak())

    # 4. Diagrams
    story.append(Paragraph("4. System Diagrams", h1))
    story.append(
        Paragraph(
            "This section contains the required flow and architecture diagrams for viva/practical explanation. "
            "DFD Level 1/2/3 are included as requested.",
            normal,
        )
    )
    story.append(Spacer(1, 0.2 * cm))

    for idx, (title, diagram_text) in enumerate(DIAGRAMS):
        story.append(Paragraph(title, h2))
        story.append(Preformatted(diagram_text, code_style))
        if idx != len(DIAGRAMS) - 1:
            story.append(Spacer(1, 0.15 * cm))
        if idx in {2, 5}:
            story.append(PageBreak())
    story.append(PageBreak())

    # 5. Data dictionary
    story.append(Paragraph("5. Data Dictionary", h1))
    story.append(
        Paragraph(
            "The following dictionary explains core SQLite tables used in this web portal.",
            normal,
        )
    )
    story.append(Spacer(1, 0.15 * cm))

    add_data_dictionary_table(
        story,
        "users",
        [
            ["Field", "Type", "Description"],
            ["id", "INTEGER PK", "Primary key for user record"],
            ["username", "TEXT UNIQUE", "Login ID (email/username)"],
            ["full_name", "TEXT", "Display name in profile/admin"],
            ["password_hash", "TEXT", "Hashed password"],
            ["role", "TEXT", "Role: admin or user"],
            ["avatar_path", "TEXT", "Profile image path"],
            ["created_at", "TEXT", "Account creation timestamp"],
            ["last_login", "TEXT", "Last login timestamp"],
        ],
        normal,
    )
    add_data_dictionary_table(
        story,
        "detection_history",
        [
            ["Field", "Type", "Description"],
            ["id", "INTEGER PK", "Detection record ID"],
            ["user_id", "INTEGER FK", "Reference to users.id"],
            ["file_name", "TEXT", "Uploaded file name"],
            ["prediction", "TEXT", "Recognized text result"],
            ["confidence", "REAL", "Prediction confidence score"],
            ["source", "TEXT", "Recognition source engine"],
            ["filters_json", "TEXT", "Applied filter configuration"],
            ["image_path", "TEXT", "Stored file path"],
            ["created_at", "TEXT", "Record creation time"],
            ["updated_at", "TEXT", "Record update time"],
        ],
        normal,
    )
    add_data_dictionary_table(
        story,
        "user_sessions",
        [
            ["Field", "Type", "Description"],
            ["id", "INTEGER PK", "Session record ID"],
            ["user_id", "INTEGER FK", "Reference to users.id"],
            ["is_active", "INTEGER", "1 if active, else 0"],
            ["login_at", "TEXT", "Login timestamp"],
            ["last_seen", "TEXT", "Last active timestamp"],
            ["logout_at", "TEXT", "Logout timestamp"],
        ],
        normal,
    )
    add_data_dictionary_table(
        story,
        "admin_activity_logs",
        [
            ["Field", "Type", "Description"],
            ["id", "INTEGER PK", "Activity row ID"],
            ["admin_user_id", "INTEGER FK", "Reference to users.id (admin)"],
            ["action", "TEXT", "Operation type (login, delete history, etc.)"],
            ["target_type", "TEXT", "Target entity type"],
            ["target_id", "TEXT", "Target record identifier"],
            ["details", "TEXT", "Action detail message"],
            ["ip_address", "TEXT", "Source IP address"],
            ["created_at", "TEXT", "Activity timestamp"],
        ],
        normal,
    )
    story.append(PageBreak())

    # 6. Modules
    story.append(Paragraph("6. Project Modules", h1))
    module_rows = [
        ["Module", "Purpose"],
        ["Authentication Module", "Login, Create Account, Reset Password, role-aware session management"],
        ["Recognition Module", "Upload image, preprocess, run OCR/RNN, return predictions and confidence"],
        ["Output Module", "Show recognized text in Output Window and per-file result cards"],
        ["Speech Module", "Speak output text and stop audio controls in browser"],
        ["Detection History Module", "Persist predictions and support view, delete, and PDF export"],
        ["Profile Module", "User/admin profile display, avatar upload, and logout"],
        ["Admin Panel Module", "User management, active user stats, location/time widgets, exports"],
        ["Admin Activity Module", "Read-only log of admin login/actions with search and filtering"],
    ]
    module_table = Table(module_rows, colWidths=[4.8 * cm, 11.9 * cm])
    style_table(module_table)
    story.append(module_table)
    story.append(PageBreak())

    # 7. Screenshots
    story.append(Paragraph("7. Screenshots of App", h1))
    story.append(
        Paragraph(
            "All screenshots were captured from live running portal in Chrome-compatible browser flow "
            "with sample practical data inputs.",
            normal,
        )
    )
    story.append(Spacer(1, 0.2 * cm))
    index_rows = [["Screen", "Image File", "Navigation Summary"]]
    for item in SCREENSHOTS:
        index_rows.append([item["title"], item["file"].name, item["navigation"]])
    index_table = Table(index_rows, colWidths=[4.9 * cm, 4.7 * cm, 7.1 * cm])
    style_table(index_table)
    story.append(index_table)
    story.append(PageBreak())

    for item in SCREENSHOTS:
        story.append(Paragraph(item["title"], h1))
        story.append(Paragraph(f"<b>Navigation:</b> {item['navigation']}", normal))
        story.append(Spacer(1, 0.12 * cm))
        if item["file"].exists():
            story.append(scaled_image(item["file"], max_w=16.8 * cm, max_h=8.25 * cm))
        else:
            story.append(Paragraph(f"Screenshot file missing: {item['file']}", normal))
        story.append(Spacer(1, 0.12 * cm))
        story.append(Paragraph(f"<b>Sample Data Note:</b> {item['data_note']}", small))
        story.append(Spacer(1, 0.12 * cm))

        widget_rows = [["Widget Label", "Widget ID / Reference"]]
        for label, widget in item["widgets"]:
            widget_rows.append([label, widget])
        widget_table = Table(widget_rows, colWidths=[7.2 * cm, 9.4 * cm])
        style_table(widget_table, header_color="#136F63")
        story.append(widget_table)
        story.append(PageBreak())

    # 8. Conclusion
    story.append(Paragraph("8. Conclusion", h1))
    story.append(Paragraph("Summary of Learnings and Challenges", h2))
    story.append(
        Paragraph(
            "This project demonstrates end-to-end implementation of handwriting recognition with a practical "
            "role-based web portal. Core learnings include image preprocessing impacts on OCR, combining deep "
            "learning and OCR for robust output, session-aware role access, and stable data persistence.",
            normal,
        )
    )
    story.append(Spacer(1, 0.15 * cm))
    story.append(
        Paragraph(
            "Key challenges were variable handwriting quality, notebook-line interference, low-light images, "
            "and maintaining smooth user/admin navigation. These were addressed with filter pipelines, OCR engine "
            "fallback strategy, SQLite-backed history, and iterative UI-flow fixes.",
            normal,
        )
    )
    story.append(PageBreak())

    # 9. References
    story.append(Paragraph("9. References", h1))
    references = [
        "Flask Documentation: https://flask.palletsprojects.com/",
        "PyTorch Documentation: https://pytorch.org/docs/stable/index.html",
        "Tesseract OCR Documentation: https://tesseract-ocr.github.io/",
        "pytesseract Package: https://pypi.org/project/pytesseract/",
        "ReportLab Documentation: https://www.reportlab.com/documentation/",
        "SQLite Documentation: https://www.sqlite.org/docs.html",
        "Playwright Documentation: https://playwright.dev/docs/intro",
    ]
    for ref in references:
        story.append(Paragraph(f"- {ref}", normal))
        story.append(Spacer(1, 0.08 * cm))
    story.append(Spacer(1, 0.2 * cm))

    story.append(Paragraph("Appendix A: Viva / Practical Exam Notes", h2))
    viva_steps = [
        "1. Run portal: ./run_portal.sh",
        "2. Open browser: http://127.0.0.1:5000/login (or custom port used at runtime).",
        "3. Explain auth pages: Login, Create Account, Reset Password.",
        "4. Login as user and show upload, filters, recognize, output window, and speech controls.",
        "5. Show user Detection History with View/Delete/PDF actions.",
        "6. Login as admin and explain Admin Panel cards, Add User, and user list controls.",
        "7. Open Admin Detection History and use search/filter to fetch a user quickly.",
        "8. Open Admin Activity page and explain view-only operation logs.",
        "9. Explain DFD levels (1/2/3) and ER links among users, history, sessions, and admin logs.",
        "10. End with pipeline summary: upload -> preprocess -> OCR/RNN -> post-process -> store -> view/export.",
    ]
    for line in viva_steps:
        story.append(Paragraph(line, normal))
        story.append(Spacer(1, 0.08 * cm))

    doc.build(story, onFirstPage=page_number, onLaterPages=page_number)
    return OUTPUT_PDF


if __name__ == "__main__":
    out = build_pdf()
    print(f"Documentation PDF generated: {out}")
