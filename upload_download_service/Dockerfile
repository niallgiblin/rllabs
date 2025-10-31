# syntax=docker/dockerfile:1
FROM python:3.11-slim

# System deps (psycopg2, etc.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy service code
COPY app ./app

# Runtime envs (override in docker-compose/.env)
ENV PORT=8081
EXPOSE 8081

# Start server
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081"]