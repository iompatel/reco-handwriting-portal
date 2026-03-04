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
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render provides PORT automatically.
CMD sh -c "python app.py --host 0.0.0.0 --port ${PORT:-5000} --db-path ${OCR_DB_PATH:-/var/data/app.db} --checkpoint ${OCR_CHECKPOINT:-checkpoints/fix2/best.pt}"

