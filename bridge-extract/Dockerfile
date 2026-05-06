FROM python:3.12-slim

WORKDIR /app

# OS deps for cryptography wheels (Fly's builder usually pulls prebuilt
# wheels for slim, but keep build-essential as a fallback).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py accounts.py llm_providers.py extract_limits.py config.py ./

# Persistent state lives on the volume mounted at /data.
ENV ACCOUNTS_PATH=/data/accounts.json
ENV EXTRACT_LIMITS_PATH=/data/extract_limits.json
ENV PORT=8100

EXPOSE 8100

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]
