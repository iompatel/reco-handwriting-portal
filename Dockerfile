FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-hin \
    tesseract-ocr-spa \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN grep -vE '^(torch|torchvision)' requirements.txt > /tmp/requirements.deploy.txt \
    && pip install --no-cache-dir -r /tmp/requirements.deploy.txt

COPY . .

# Render provides PORT automatically. Gunicorn keeps the dev server out of production.
CMD sh -c "OCR_CHECKPOINT=${OCR_CHECKPOINT:-checkpoints/fix2/best.pt} OCR_DB_PATH=${OCR_DB_PATH:-/var/data/app.db} OCR_UPLOAD_DIR=${OCR_UPLOAD_DIR:-/var/data/uploads} gunicorn --bind 0.0.0.0:${PORT:-5000} --worker-class gthread --workers 1 --threads 4 --timeout 300 app:app"
