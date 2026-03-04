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

- `http://127.0.0.1:5000/login`

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

- If site does not open, confirm server log shows `Running on http://127.0.0.1:5000`.
- If prediction fails with `No module named 'torch'`, run using `.venv313` and reinstall requirements.
- If checkpoint is missing, train first or use existing `checkpoints/fix2/best.pt`.
