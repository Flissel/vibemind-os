FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.legit.txt .
RUN pip install --no-cache-dir -r requirements.legit.txt

ENV VIBEMIND_CONFIG_DIR=/config

# Copy scripts
COPY setup_db.py .
COPY attack_demo.py .
COPY silent_attack_demo.py .
COPY real_llm_attack.py .

# Default: run the real LLM attack demo
CMD ["python", "-u", "real_llm_attack.py"]
