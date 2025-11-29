"""
Training Service Entry Point

Starts the RabbitMQ consumer to process training jobs.
"""
from model_trainer import start_consumer

if __name__ == "__main__":
    start_consumer()

