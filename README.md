# RLLabs DS Project

This project is a distributed system for a reinforcement learning platform. It is designed to be run with Docker Compose for local development and Kubernetes for production-grade scaling observation.

## Running the Project

You can run the project using either Docker Compose for a simple local setup, or Kind for a more advanced multi-node Kubernetes environment.

### Local Development (Docker Compose)

For simple local development, you can use Docker Compose to quickly spin up the entire application stack.

**Prerequisites:**
*   Docker
*   Docker Compose

**To start the application:**

```bash
docker-compose up --build
```

**To stop the application and remove the containers:**

```bash
docker-compose down -v
```

### Advanced Local Development (Kubernetes with Kind)

For local development that simulates a multi-node cluster (useful for testing scaling), you can use Kind.

**Prerequisites:**
*   Docker
*   `kubectl`
*   [Kind](https://kind.sigs.k8s.io/docs/user/quick-start/)

**To create the local cluster:**

```bash
kind create cluster --config kind-cluster-config.yml
```

**To load your Docker images into the cluster:**

After you build your Docker images, you need to load them into the Kind cluster.

```bash
kind load docker-image api-gateway
kind load docker-image model-catalog-service
```

**To deploy the application:**

```bash
kubectl apply -k kubernetes
```

**To delete the cluster:**

```bash
kind delete cluster
```

### Production Environment (Kubernetes)

For a production-like environment, you can use Kubernetes to deploy and manage the application.

**Prerequisites:**
*   A Kubernetes cluster (e.g., Docker Desktop, Minikube, or a cloud provider)
*   `kubectl` configured to connect to your cluster

**To deploy the application:**

```bash
kubectl apply -k kubernetes
```

**To remove all resources:**

```bash
kubectl delete -k kubernetes
```

## Testing

Tests so far: `tests/test_integration.py`

To run the tests, first start the application using either Docker Compose or Kubernetes. Then, from the project root in a different terminal, run:

```bash
pytest -v tests/test_integration.py
```

## Services

*   **`api-gateway`**: The entry point for all incoming traffic. It routes requests to the appropriate service.
*   **`model_catalog_service`**: RESTful API for managing model metadata. It communicates with the `postgres` database to provide endpoints for creating models, registering versions, and querying for the latest version.
*   **`postgres`**: Serves as the metadata store for the `model_catalog_service`.
*   **`redis`**: Caching frequent database queries to improve performance during traffic spikes.
*   **`minio`**: An S3-compatible object storage service for the large model artifacts.
*   **`create-buckets`**: One-off utility job that runs on startup. Its only job is to automate the creation of the first bucket.

## Adding a New Service

To add your own service to the project, you will need to:

1.  Add your service's Dockerfile to its directory.
2.  Add a new entry to the `services` section of `docker-compose.yml`.
3.  Create a new Kubernetes manifest for your service in the `kubernetes` directory and add it to `kubernetes/kustomization.yml`.