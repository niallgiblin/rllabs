FROM python:3.11-slim

WORKDIR /app

COPY upload_download_service/requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/

COPY upload_download_service/ /app/

ENV PYTHONPATH=/app:/app/shared

EXPOSE 8002

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002", "--workers", "1", "--loop", "uvloop", "--log-level", "warning"]