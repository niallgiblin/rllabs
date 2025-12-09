FROM python:3.11-slim
WORKDIR /app

COPY model_catalog_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/

COPY model_catalog_service/ /app/

ENV PYTHONPATH=/app:/app/shared

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
