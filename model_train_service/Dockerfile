FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The training service is a RabbitMQ consumer, not a web server
# It runs model_trainer.py which starts the consumer
CMD ["python", "main.py"]

