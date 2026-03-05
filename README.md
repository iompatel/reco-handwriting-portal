# Handwriting Recognition Using RNN (CRNN + CTC)

College project with a working web portal, role-based login, SQLite storage, handwriting filters, and speech output.

## Main Features

- CRNN model (CNN + BiLSTM + CTC) for handwriting recognition.
- Login/Create Account page.
- Two roles:
  - `Admin`: login, view all uploads/users, add/update/delete users, see active users.
  - `User`: login, run detection, view/edit/delete own detection history.
- SQLite database (`data/app.db`) for users, sessions, and detection history.
- Working browse/upload flow (drag-drop + browse files).
- Output window showing recognized text.
- Working speech controls (speak per result, speak all, stop, speech-rate).
- Hybrid OCR pipeline: `auto`, `hybrid`, `rnn`, `tesseract`.
- Multilingual OCR support via Tesseract language packs.
- Filters/options:
  - Grayscale
  - Denoise
  - Adaptive Threshold
  - Invert Colors
  - Contrast Boost
  - Handwriting Boost (multi-pass)
  - Student Notebook Mode
  - Remove Notebook Lines
  - Smart Text Cleanup

## Project Structure

- `app.py` - Flask app and APIs
- `src/database.py` - SQLite schema and initialization
- `src/inference_service.py` - recognition + preprocessing pipeline
- `templates/login.html` - login/create-account page
- `templates/index.html` - main portal (user/admin)
- `static/app.js` - portal behavior
- `static/login.js` - auth page behavior
- `static/styles.css` - main portal styling
- `run_portal.sh` - safe launcher

## Quick Start (Run Project)

1. Install dependencies in a virtual environment:

```bash
python3.13 -m venv .venv313
source .venv313/bin/activate
pip install -r requirements.txt
```

2. Start portal:

```bash
./run_portal.sh
```

3. Open:

- `http://localhost:5000/login`

Note for Google/Firebase login:
- Prefer `localhost` URL.
- If you use `127.0.0.1`, add it in Firebase `Authentication -> Settings -> Authorized domains`.

## Multilingual Setup (Important)

By default, systems often have only English OCR packs.  
Install extra language packs locally in this project:

```bash
./scripts/install_tessdata_languages.sh --force eng osd hin spa
```

Or install a wider set (script default set):

```bash
./scripts/install_tessdata_languages.sh
```

Then run:

```bash
OCR_TESSDATA_DIR=/Users/ompatel/Desktop/Reco/data/tessdata ./run_portal.sh
```

In the portal:
- choose `OCR Engine` = `Auto` (recommended) or `Hybrid`
- set `OCR Language Codes` like `eng+hin+spa`

## Default Admin Account

On first run, database auto-creates:

- Username: `admin`
- Password: `admin123`

## Google Login (Firebase Auth)

This project now supports **Continue with Google** on the login page.

1. In Firebase Console:
- Enable **Authentication -> Sign-in method -> Google**.
- Add your local/prod domain in **Authentication -> Settings -> Authorized domains**.

2. Export Firebase web SDK keys (for frontend):

```bash
export FIREBASE_WEB_API_KEY="..."
export FIREBASE_WEB_AUTH_DOMAIN="your-project.firebaseapp.com"
export FIREBASE_WEB_PROJECT_ID="your-project-id"
export FIREBASE_WEB_APP_ID="..."
```

Optional keys:
- `FIREBASE_WEB_STORAGE_BUCKET`
- `FIREBASE_WEB_MESSAGING_SENDER_ID`

3. Export Firebase Admin credentials (for backend ID token verify):

Use either JSON string:

```bash
export FIREBASE_SERVICE_ACCOUNT_JSON='{"type":"service_account","project_id":"...","private_key_id":"...","private_key":"-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n","client_email":"...","client_id":"...","auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token"}'
```

or JSON file path:

```bash
export FIREBASE_SERVICE_ACCOUNT_PATH="/absolute/path/to/service-account.json"
```

If any required Firebase setting is missing, normal username/password login still works and Google button stays disabled.

## Firebase Data Sync (SQLite -> Firestore)

All app tables can be mirrored to Firebase Firestore:
- `users`
- `user_sessions`
- `detection_history`
- `admin_activity_logs`

Enable with environment variables:

```bash
export FIREBASE_DATA_SYNC_ENABLED="true"
export FIREBASE_SYNC_ON_STARTUP="true"
export FIREBASE_RESTORE_ON_STARTUP="true"
export FIREBASE_SYNC_RETRY_SECONDS="300"
export FIREBASE_DATA_COLLECTION_PREFIX="reco_prod"
```

Before enabling sync in production, Firebase Console me ye ensure karo:
- `Build -> Firestore Database` created (Native mode)
- Google Cloud me `Cloud Firestore API` enabled for your project

How it works:
- Every write/commit in app syncs updated tables to Firestore.
- On server startup, a full sync runs once (when `FIREBASE_SYNC_ON_STARTUP=true`).
- On server startup, optional restore from Firestore to local SQLite runs when `FIREBASE_RESTORE_ON_STARTUP=true`.
- If Firebase temporarily fails, sync auto-retries (interval from `FIREBASE_SYNC_RETRY_SECONDS`).
- Admin can trigger manual full sync:
  - `POST /api/admin/firebase-sync`

Firestore collection naming:
- `<prefix>_users`
- `<prefix>_user_sessions`
- `<prefix>_detection_history`
- `<prefix>_admin_activity_logs`

## Deploy Live (Render + Firebase)

This repo already contains `render.yaml` with disk + Firebase env placeholders.

1. Push this project to GitHub.
2. In Render Dashboard, choose **New -> Blueprint** and connect the repo.
3. While creating service, set these required env vars:
   - `FIREBASE_SERVICE_ACCOUNT_JSON` (full service-account JSON string)
   - `FIREBASE_WEB_API_KEY`
   - `FIREBASE_WEB_AUTH_DOMAIN`
   - `FIREBASE_WEB_PROJECT_ID`
   - `FIREBASE_WEB_APP_ID`
4. Firebase side checks:
   - Firestore Database created (Native mode)
   - Cloud Firestore API enabled
5. Keep these enabled:
   - `FIREBASE_DATA_SYNC_ENABLED=true`
   - `FIREBASE_SYNC_ON_STARTUP=true`
   - `FIREBASE_RESTORE_ON_STARTUP=true`
   - `FIREBASE_SYNC_RETRY_SECONDS=300`
6. Deploy.
7. After deploy, run one manual full sync once:
   - Login as admin
   - `POST /api/admin/firebase-sync`
8. Verify:
   - `GET /api/health` should show:
     - `"firebase_google_enabled": true`
     - `"firebase_data_sync_configured": true`
     - `"firebase_data_sync_enabled": true`

## High-Accuracy Training (Public + Local + History)

Recognition can be improved a lot with larger, diverse data, but **100% error-free OCR for every handwriting/language is not realistic**.
Use this pipeline to significantly reduce errors in real notebook images.

1. Install extra training dependency:

```bash
pip install datasets
```

2. Generate stronger synthetic line-level data:

```bash
python3 scripts/generate_synthetic_dataset.py --out-dir data/synthetic_plus
```

3. Build merged dataset using:
- public internet dataset(s) from HuggingFace (`--hf-source`)
- local synthetic labels (`--local-metadata`)
- optional high-confidence portal history (`--include-history`)

```bash
python3 scripts/prepare_training_dataset.py \
  --out-dir data/training_mix \
  --hf-source "Teklia/IAM-line||train" \
  --local-metadata "data/synthetic_plus/labels.csv|data/synthetic_plus" \
  --include-history \
  --db-path data/app.db \
  --upload-root data/uploads
```

4. Train CRNN with scheduler + augmentations:

```bash
python3 train.py \
  --metadata data/training_mix/labels.csv \
  --data-root data/training_mix \
  --img-width 256 \
  --img-height 64 \
  --epochs 35 \
  --batch-size 48 \
  --checkpoint-dir checkpoints/robust
```

5. Run portal with new checkpoint:

```bash
OCR_CHECKPOINT=checkpoints/robust/best.pt ./run_portal.sh
```

## Troubleshooting

- If site does not open, confirm server log shows `Running on http://localhost:5000`.
- For local run, open `http://localhost:5000/login` first when testing Firebase Google login.
- If prediction fails with `No module named 'torch'`, run using `.venv313` and reinstall requirements.
- If checkpoint is missing, train first or use existing `checkpoints/fix2/best.pt`.
