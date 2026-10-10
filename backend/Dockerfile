# Build from the project root:  docker build -f backend/Dockerfile .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 FRONTEND_DIR=/srv/frontend
WORKDIR /app
COPY backend/requirements.txt backend/requirements-ai.txt ./
# INSTALL_AI=true (default) adds Whisper, PaddleOCR, Tesseract (with Hindi) and Coqui TTS.
# Paddle needs libgomp1; Coqui uses CPU-only PyTorch here (smaller image; see docker-compose.gpu.yml for GPUs).
# Separate steps so an interrupted build resumes from the last finished step instead of starting over.
ARG INSTALL_AI=true
RUN pip install -r requirements.txt
RUN if [ "$INSTALL_AI" = "true" ]; then \
      apt-get update && apt-get install -y --no-install-recommends libgomp1 espeak-ng tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin \
      && rm -rf /var/lib/apt/lists/*; \
    fi
RUN if [ "$INSTALL_AI" = "true" ]; then pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu; fi
RUN if [ "$INSTALL_AI" = "true" ]; then \
      pip install -r requirements-ai.txt \
      && pip uninstall -y opencv-python opencv-contrib-python \
      && pip install opencv-contrib-python-headless; \
    fi
COPY backend/ /app/
COPY frontend/ /srv/frontend/
RUN useradd -m -u 1000 app && mkdir -p /app/media /app/models /home/app/.cache /home/app/.paddleocr /home/app/.local/share/tts \
    && chown -R app:app /app /home/app
USER app
# Pre-train the gradient-boosting risk model so the first request is fast.
RUN python -m scripts.train_risk_model
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=4).status == 200 else 1)"
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-2} --proxy-headers"]
