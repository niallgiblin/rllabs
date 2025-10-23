# RLLabs DS Project

## Run using docker compose

From project root:
```bash
docker compose up --build
```

To remove containers and data:
```bash
docker compose down -v
```

## Testing

Tests so far: tests/test_integration.py

Run docker compose, then from project root in a different terminal:
```bash
pytest -v tests/test_integration.py
```

## Docker compose

Services so far:

-   `postgres`: Serves as the metadata store for the `model_catalog_service`.

-   `redis`: Caching frequent database queries to improve performance during traffic spikes.

-   `minio`: An S3-compatible object storage service for the large model artifacts.

-   `create-buckets`: One-off utility service that runs on startup. Its only job is to automate the creation of the first bucket.

-   `model_catalog_service`: RESTful API for managing model metadata. It communicates with the `postgres` database to provide endpoints for creating models, registering versions, and querying for the latest version.

Adding a new service:

To add your own service to the project, add your service Dockerfile to its directory and add a new entry to the `services` section of `docker-compose.yml`. Example template:

```yaml
  # A description of your new service
  your-new-service:
    build:
      context: ./your-service-directory # The directory containing your Dockerfile
    container_name: your-new-service-name
    ports:
      - "8002:8000" # Map a host port to your service's container port
    environment:
      # Environment variables your service needs to connect to others
      - MODEL_CATALOG_URL=http://model_catalog_service:8000
    depends_on:
      # Add services that must be healthy before your service starts
      model_catalog_service:
        condition: service_healthy
    networks:
      - rllabs_net # Ensures your service can communicate with others
    volumes:
      # Mount your code for live-reloading during development
      - ./your-service-directory:/app
```