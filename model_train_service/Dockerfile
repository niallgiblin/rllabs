FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY model_train_service/requirements.txt .
COPY shared/requirements.txt shared_requirements.txt

RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir -r shared_requirements.txt || true

COPY model_train_service/ .

COPY shared/ /app/shared/

CMD ["python", "main.py"]
