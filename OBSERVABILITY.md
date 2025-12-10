# Comprehensive Observability Guide

Complete guide for setting up the **Three Pillars of Observability** on the RLLabs platform: Metrics, Logs, and Traces.

---

## Quick Start: Setup and Access

### Prerequisites

**Required Dependencies:**

- **kubectl** (Kubernetes CLI)
- **Kind cluster** with ingress port mappings (ports 80/443 exposed)
- **Python 3.8+** (for load testing)

**Verify prerequisites:**

```bash
kubectl version --client
python3 --version
kubectl cluster-info
kubectl get pods -n ingress-nginx
```

### One-Command Setup (Recommended)

**Complete k8s/observability stack deployment:**

```bash
./scripts/start_everything.sh
```

This script:

- Deploys all infrastructure (PostgreSQL, Redis, RabbitMQ, MongoDB, MinIO)
- Deploys all application services
- Deploys full observability stack (Prometheus, Grafana, Jaeger, Loki, Alertmanager)
- Sets up HPA, Ingress, and metrics-server
- Starts port-forwards automatically
- Verifies everything is ready

### Access Observability Services

**Port-forwards are started automatically by the script. Access via:**

| Service                | URL                    | Credentials   |
| ---------------------- | ---------------------- | ------------- |
| **Grafana**      | http://localhost:3000  | admin / admin |
| **Prometheus**   | http://localhost:9090  | -             |
| **Jaeger**       | http://localhost:16686 | -             |
| **Alertmanager** | http://localhost:9093  | -             |
| **Loki**         | http://localhost:3100  | -             |

**Manual port-forward (if needed):**

```bash
kubectl port-forward svc/grafana 3000:3000
kubectl port-forward svc/prometheus 9090:9090
kubectl port-forward svc/jaeger-query 16686:16686
kubectl port-forward svc/alertmanager 9093:9093
kubectl port-forward svc/loki 3100:3100
```

### Verify Deployment

```bash
# Check all observability pods are running
kubectl get pods -l 'app in (prometheus,grafana,alertmanager,jaeger,loki,promtail)'

# Check services are accessible
curl http://localhost:9090/-/healthy  # Prometheus
curl http://localhost:16686/api/services  # Jaeger
```

**Expected result:** All pods should be in "Running" state and services should respond.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [The Three Pillars](#the-three-pillars)
3. [Prerequisites](#prerequisites)
4. [Deployment Options](#deployment-options)
5. [Deploying Observability Stack](#deploying-observability-stack)
6. [Accessing Services](#accessing-services)
7. [The Debugging Journey](#the-debugging-journey)
8. [Running Load Tests](#running-load-tests)
9. [Monitoring &amp; Metrics](#monitoring--metrics)
10. [Logging (Loki)](#logging-loki)
11. [Distributed Tracing (Jaeger)](#distributed-tracing-jaeger)
12. [Alerting (Alertmanager)](#alerting-alertmanager)
13. [Troubleshooting](#troubleshooting)

---

## Architecture Overview

### Observability Stack

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       Three Pillars of Observability                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                   │
│  │   METRICS    │    │    LOGS      │    │   TRACES     │                   │
│  │   (WHAT)     │    │    (WHY)     │    │   (WHERE)    │                   │
│  ├──────────────┤    ├──────────────┤    ├──────────────┤                   │
│  │ Prometheus   │    │ Loki         │    │ Jaeger       │                   │
│  │ Alertmanager │    │ Promtail     │    │ OpenTelemetry│                   │
│  └──────────────┘    └──────────────┘    └──────────────┘                   │
│         │                   │                   │                            │
│         └───────────────────┴───────────────────┘                            │
│                             │                                                │
│                      ┌──────▼──────┐                                         │
│                      │   GRAFANA   │ ◄─── Single pane of glass               │
│                      │  Dashboard  │                                         │
│                      └─────────────┘                                         │
└─────────────────────────────────────────────────────────────────────────────┘
```

### HA Architecture

| Component            | Configuration                     | Features                            |
| -------------------- | --------------------------------- | ----------------------------------- |
| **PostgreSQL** | Primary + 2 Replicas              | Streaming replication, read scaling |
| **Redis**      | Master + 2 Replicas + 3 Sentinels | Automatic failover, read scaling    |
| **MongoDB**    | 3-Node Replica Set                | Automatic failover, elections       |

## The Three Pillars

### Pillar 1: Metrics

**What they are:** Numerical, time-series values representing measurements.

**Characteristics:** Aggregatable, queryable, and efficient to store.

**Use Cases:**

- Dashboards (Grafana)
- Alerting ("Alert me if error rate > 5%")
- Spotting high-level trends

**The Four Golden Signals:**

| Signal               | Description              | Example Query                                         |
| -------------------- | ------------------------ | ----------------------------------------------------- |
| **Latency**    | How long requests take   | `histogram_quantile(0.95, ...)`                     |
| **Traffic**    | Request rate             | `sum(rate(http_requests_total[5m]))`                |
| **Errors**     | Request failure rate     | `sum(rate(http_requests_total{status=~"5.."}[5m]))` |
| **Saturation** | How "full" is the system | CPU, memory, queue depth                              |

**Implementation:** Prometheus + `prometheus-fastapi-instrumentator`

### Pillar 2: Logs

**What they are:** Immutable, timestamped records of discrete events.

**Use Case:** Debugging the root cause of an issue.

**Key Concept: Structured Logging**

**Bad Log (Unstructured):**

```
[INFO] User 123 logged in from 1.2.3.4 at 14:05:01
```

**Good Log (Structured JSON):**

```json
{
  "level": "INFO",
  "timestamp": "2024-01-15T14:05:01Z",
  "service": "api-gateway",
  "user_id": "123",
  "source_ip": "1.2.3.4",
  "message": "User login successful",
  "trace_id": "abc123def456"
}
```

**Why Structured?** It can be queried like a database: `{service="api-gateway"} | json | user_id="123"`

**Implementation:** `python-json-logger` → Promtail → Loki → Grafana

### Pillar 3: Distributed Traces

**What it is:** A way to follow a single request as it flows through all services.

**Use case:** Find out where an issue is arising.

**How it works:**

1. When a request enters the system, it gets a unique **Trace ID**
2. This Trace ID is passed via headers (`traceparent`) to every service
3. Each operation (API call, DB query) is a **Span**
4. All Spans with the same Trace ID are visualised as a waterfall diagram

**Example Trace:**

```
API Gateway (50ms total)
├── Authentication (5ms)
├── Model Catalog Service (40ms)
│   ├── Database Query (30ms)  ← Bottleneck!
│   └── Response Serialization (10ms)
└── Response (5ms)
```

**Implementation:** OpenTelemetry SDK → Jaeger

---

## Prerequisites

### Required Tools

- **kubectl** - Kubernetes CLI
- **Python 3.8+** - For load testing
- **Kind cluster** - With ingress port mappings (ports 80/443 exposed)

### Verify Prerequisites# Check kubectl

```bash
kubectl version --client

# Check Python
python3 --version

# Check cluster access
kubectl cluster-info

# Check ingress controller
kubectl get pods -n ingress-nginx

```

---

## Running Load Tests

### Prerequisites for Load Testing

```bash
# Install Python dependencies
pip install aiohttp asyncio

# Or use requirements file
pip install -r tests/requirements.txt
```

### Running the Load Test

```bash
# Basic test (10 users, 60 seconds)
python tests/comprehensive_load_test.py \
  --url http://localhost \
  --users 10 \
  --duration 60

# Medium load (50 users, 2 minutes)
python tests/comprehensive_load_test.py \
  --url http://localhost \
  --users 30 \
  --duration 120

# Stress test (100 users, 60 seconds, stress mode)
python tests/comprehensive_load_test.py \
  --url http://localhost \
  --users 100 \
  --duration 60 \
  --stress
```

### Monitoring During Load Test

**Watch pod scaling in real-time:**

```bash
# Terminal 1: Watch HPA
watch -n 2 'kubectl get hpa'

# Terminal 2: Watch pods
watch -n 2 'kubectl get pods'

# Terminal 3: Watch resource usage
watch -n 5 'kubectl top pods'
```

**Expected behavior during load:**

- HPA scales pods up (1 → 2 → 3 → 4 → 5)
- CPU/memory utilisation increases
- Request rate increases in Prometheus
- Traces appear in Jaeger
- Latency may increase slightly but should stabilise

---

## Monitoring & Metrics

### Key Prometheus Queries

Open Prometheus at http://localhost:9090 (via port-forward):

**Request Rate:**

```promql
sum(rate(http_requests_total[5m])) by (job)
```

**Request Latency (p95):**

```promql
histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (job, le))
```

**Request Latency (p99):**

```promql
histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket[5m])) by (job, le))
```

**Error Rate:**

```promql
sum(rate(http_requests_total{status=~"5.."}[5m])) by (job)
```

**Pod Count:**

```promql
count(count by (pod) (http_requests_total{job="api-gateway"}))
```

**PostgreSQL Connection Pool Usage:**

```promql
# Check connection pool metrics (if exposed)
pg_stat_database_numbackends{datname="model_catalog_db"}
```

**Redis Memory Usage:**

```promql
redis_memory_used_bytes / redis_memory_max_bytes * 100
```

### Grafana Dashboard Setup

1. Open Grafana: http://localhost:3000
2. Login with `admin` / `admin`
3. Data sources are pre-configured:
   - **Prometheus**: http://prometheus:9090
   - **Loki**: http://loki:3100
   - **Jaeger**: http://jaeger-query:16686
   - **Alertmanager**: http://alertmanager:9093
4. Import dashboard from `kubernetes/grafana-dashboard-rllabs.json`

---

## Logging (Loki)

### Viewing Logs in Grafana

1. Open Grafana → **Explore**
2. Select **Loki** as data source
3. Use LogQL queries:

**All logs from a service:**

```logql
{app="api-gateway"}
```

**Error logs only:**

```logql
{app="api-gateway"} |= "ERROR"
```

**Parse JSON and filter:**

```logql
{app="model-catalog-service"} | json | level="ERROR"
```

**Find logs for a specific trace:**

```logql
{app=~".+"} |= "abc123def456"
```

**Filter by user:**

```logql
{app="api-gateway"} | json | user_id="user-123"
```

### Log Format

All services output structured JSON logs:

```json
{
  "timestamp": "2024-01-15T10:30:00.123Z",
  "level": "INFO",
  "service": "api-gateway",
  "logger": "main",
  "message": "GET /api/models - 200 - 0.045s",
  "trace_id": "abc123def456789",
  "span_id": "def456789abc"
}
```

---

## Distributed Tracing (Jaeger)

### Viewing Traces

1. Open Jaeger: http://localhost:16686
2. Select **Service**: `api-gateway`
3. Click **Find Traces**
4. Click on a trace to see the waterfall diagram

### Understanding the Waterfall

```
api-gateway (total: 150ms)
├── HTTP GET /api/models (150ms)
│   ├── rate_limit_check (2ms)
│   ├── jwt_validation (5ms)
│   └── proxy_to_model_catalog (140ms)
│       └── model-catalog-service (135ms)
│           ├── HTTP GET /models (135ms)
│           │   ├── db_query (120ms)  ← Bottleneck
│           │   └── serialize_response (15ms)
```

### Trace Context Propagation

The trace ID flows automatically through all services:

```
User Request
    │
    ▼ (traceparent: 00-abc123-def456-01)
┌─────────────────┐
│   API Gateway   │ ◄─── Creates trace if none exists
└────────┬────────┘
         │ (traceparent header propagated)
         ▼
┌─────────────────┐
│ Model Catalog   │ ◄─── Continues same trace
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   PostgreSQL    │ ◄─── DB query as child span
└─────────────────┘
```

### Services Reporting Traces

All services automatically report traces to Jaeger:

- `api-gateway`
- `model-catalog-service`
- `upload-download-service`
- `collaboration-service`

---

## Alerting (Alertmanager)

### Viewing Alerts

1. Open Alertmanager: http://localhost:9093
2. View active alerts, silences, and status

### Alert Rules (Four Golden Signals)

The following alerts are pre-configured in `kubernetes/prometheus-alerts.yml`:

| Alert                  | Condition               | Severity |
| ---------------------- | ----------------------- | -------- |
| `HighLatencyP95`     | P95 latency > 1s for 5m | warning  |
| `CriticalLatencyP99` | P99 latency > 5s for 2m | critical |
| `HighErrorRate`      | Error rate > 5% for 5m  | warning  |
| `CriticalErrorRate`  | Error rate > 20% for 2m | critical |
| `ServiceDown`        | No scrapes for 1m       | critical |
| `TrafficDrop`        | 70% below yesterday     | warning  |

### Database Alerts

| Alert                           | Condition           | Severity |
| ------------------------------- | ------------------- | -------- |
| `PostgresReplicationLag`      | Lag > 30s for 5m    | warning  |
| `MongoDBReplicaSetMemberDown` | Member unhealthy    | critical |
| `RedisHighMemory`             | Memory > 85% for 5m | warning  |
| `RedisMasterFailover`         | Master changed      | warning  |

### Infrastructure Alerts

| Alert                   | Condition               | Severity |
| ----------------------- | ----------------------- | -------- |
| `PodCrashLooping`     | > 5 restarts/hour       | warning  |
| `HPAAtMaxReplicas`    | At max for 15m          | warning  |
| `RabbitMQNoConsumers` | Queue with no consumers | critical |

### Configuring Notifications

Edit `kubernetes/alertmanager.yml` to configure notification channels:

```yaml
receivers:
  - name: 'slack-notifications'
    slack_configs:
      - api_url: 'https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK'
        channel: '#alerts'
```
