# Build context stays embedding-service/ to match scripts/swarm_image_builds.ps1.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV VIBEMIND_CONFIG_DIR=/config

WORKDIR /app

# vibemind-shared is the single, exact Git requirement below; install Git only
# to resolve that pinned source during image construction.
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8080

RUN useradd --create-home --shell /bin/bash appuser
USER appuser

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
