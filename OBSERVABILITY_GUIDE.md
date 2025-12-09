# Comprehensive Observability Guide

Complete guide for setting up the **Three Pillars of Observability** on the RLLabs platform: Metrics, Logs, and Traces.

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

### Deployment Options

| Mode                      | Description                          | Use Case                     |
| ------------------------- | ------------------------------------ | ---------------------------- |
| **Simple**          | Single-instance deployments          | Quick testing, low resources |
| **HA (Full)**       | Full high-availability with replicas | Production-like, demos       |
| **HA (Simplified)** | Minimal HA with fewer replicas       | Resource-constrained HA demo |

### Full HA Architecture

| Component            | Configuration                     | Features                            |
| -------------------- | --------------------------------- | ----------------------------------- |
| **PostgreSQL** | Primary + 2 Replicas              | Streaming replication, read scaling |
| **Redis**      | Master + 2 Replicas + 3 Sentinels | Automatic failover, read scaling    |
| **RabbitMQ**   | 3-Node Cluster                    | Queue mirroring, autoheal           |
| **MongoDB**    | 3-Node Replica Set                | Automatic failover, elections       |
| **MinIO**      | 4-Node Distributed                | Erasure coding, data redundancy     |

### Application Services (Always HA via HPA)

| Service                           | Scaling          | Range    |
| --------------------------------- | ---------------- | -------- |
| **API Gateway**             | HPA (CPU/Memory) | 1-5 pods |
| **Model Catalog Service**   | HPA (CPU/Memory) | 1-5 pods |
| **Upload-Download Service** | HPA (CPU/Memory) | 1-5 pods |
| **Collaboration Service**   | HPA (CPU/Memory) | 1-5 pods |

---

## The Three Pillars

### Pillar 1: Metrics (WHAT is happening?)

**What they are:** Numerical, time-series values representing measurements.

**Characteristics:** Aggregatable, queryable, and efficient to store.

**They answer:** "What is the system's health?" (The 10,000-foot view)

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

### Pillar 2: Logs (WHY did this happen?)

**What they are:** Immutable, timestamped records of discrete events.

**They answer:** "Why did this specific event happen?" (The ground-level detail)

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

**Why Structured?** You can query it like a database: `{service="api-gateway"} | json | user_id="123"`

**Implementation:** `python-json-logger` → Promtail → Loki → Grafana

### Pillar 3: Distributed Traces (WHERE is the problem?)

**What it is:** A way to follow a single request as it flows through all services.

**They answer:** "WHERE is the bottleneck in the chain?"

**How it works:**

1. When a request enters the system, it gets a unique **Trace ID**
2. This Trace ID is passed via headers (`traceparent`) to every service
3. Each operation (API call, DB query) is a **Span**
4. All Spans with the same Trace ID are visualized as a waterfall diagram

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
- **krew** - kubectl plugin manager (for MinIO Operator, optional)

### Verify Prerequisites

```bash
# Check kubectl
kubectl version --client

# Check Python
python3 --version

# Check cluster access
kubectl cluster-info

# Check ingress controller
kubectl get pods -n ingress-nginx

# Optional: Check krew for MinIO Operator
export PATH="${KREW_ROOT:-$HOME/.krew}/bin:$PATH"
kubectl krew version
```

---

## Deploying Observability Stack

### Quick Start: Complete Setup (Recommended)

**For a complete setup from scratch (infrastructure + services + observability):**

```bash
# One command: Zero to Load Testing Ready
./scripts/start_everything.sh
```

This script:

- Deploys all infrastructure (PostgreSQL, Redis, RabbitMQ, MongoDB, MinIO)
- Deploys all application services
- Deploys full observability stack (Prometheus, Grafana, Jaeger, Loki, Alertmanager)
- Sets up HPA, Ingress, and metrics-server
- Starts port-forwards automatically
- Verifies everything is ready

**See [Quick Start section](#quick-start-one-command-setup---recommended) at the bottom for more options.**

### Manual Deployment (For Custom Setups)

If you prefer to deploy step-by-step:

```bash
# Deploy full observability stack (Alertmanager, Jaeger, Loki)
./scripts/deploy_observability.sh

# Or manually:
kubectl apply -f kubernetes/prometheus-alerts.yml
kubectl apply -f kubernetes/alertmanager.yml
kubectl apply -f kubernetes/jaeger.yml
kubectl apply -f kubernetes/loki.yml
kubectl apply -f kubernetes/prometheus.yml
kubectl apply -f kubernetes/grafana.yml

# Wait for pods
kubectl wait --for=condition=ready pod -l app=alertmanager --timeout=120s
kubectl wait --for=condition=ready pod -l app=jaeger --timeout=120s
kubectl wait --for=condition=ready pod -l app=loki --timeout=120s
kubectl wait --for=condition=ready pod -l app=promtail --timeout=120s
```

**Note:** Services already include observability code in their Docker images, so no rebuild is needed. Just restart pods after deploying the observability stack.

---

## Deployment Options

### Option A: Simple Deployment (Quick Start)

Single-instance backing services for quick testing:

```bash
# Deploy all simple configs
kubectl apply -k kubernetes/

# Wait for pods
kubectl wait --for=condition=ready pod --all --timeout=300s
```

### Option B: Full HA Deployment (Recommended for Demo)

Complete high-availability with full replication:

```bash
# Deploy HA infrastructure
kubectl apply -f kubernetes/postgres-ha.yml
kubectl apply -f kubernetes/redis-ha.yml
kubectl apply -f kubernetes/rabbitmq-ha.yml
kubectl apply -f kubernetes/mongodb.yml  # Already HA

# Deploy application services
kubectl apply -f kubernetes/model-catalog-service.yml
kubectl apply -f kubernetes/upload-download-service.yml
kubectl apply -f kubernetes/collaboration-service.yml
kubectl apply -f kubernetes/api-gateway.yml

# Deploy observability stack
kubectl apply -f kubernetes/prometheus-alerts.yml
kubectl apply -f kubernetes/alertmanager.yml
kubectl apply -f kubernetes/jaeger.yml
kubectl apply -f kubernetes/loki.yml
kubectl apply -f kubernetes/prometheus.yml
kubectl apply -f kubernetes/grafana.yml

# Deploy scaling and ingress
kubectl apply -f kubernetes/hpa.yml
kubectl apply -f kubernetes/ingress.yml
```

### Install Metrics Server (Required for HPA)

```bash
# Install metrics-server
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml

# Patch for Kind (insecure kubelet)
kubectl patch deployment metrics-server -n kube-system --type='json' \
  -p='[{"op": "add", "path": "/spec/template/spec/containers/0/args/-", "value": "--kubelet-insecure-tls"}]'

# Wait and verify
sleep 30
kubectl top nodes
kubectl get hpa
```

---

## Accessing Services

### Via Port-Forward

```bash
# Grafana (all-in-one dashboard)
kubectl port-forward svc/grafana 3000:3000

# Prometheus (metrics & alerting rules)
kubectl port-forward svc/prometheus 9090:9090

# Jaeger (distributed traces)
kubectl port-forward svc/jaeger-query 16686:16686

# Alertmanager (alert routing)
kubectl port-forward svc/alertmanager 9093:9093
```

### Access URLs

| Service                 | URL                            | Credentials                      |
| ----------------------- | ------------------------------ | -------------------------------- |
| **Grafana**       | http://localhost:3000          | admin / admin                    |
| **Prometheus**    | http://localhost:9090          | -                                |
| **Jaeger**        | http://localhost:16686         | -                                |
| **Alertmanager**  | http://localhost:9093          | -                                |
| **API Gateway**   | http://localhost (via Ingress) | -                                |
| **MinIO Console** | http://localhost:9001          | minioadmin / minioadmin_password |
| **RabbitMQ**      | http://localhost:15672         | admin / admin_password           |

---

## The Debugging Journey

**This is how the three pillars work together:**

### 1. ALERT (Metric) → You know WHAT is wrong

```
Alert fires: "P95 latency for api-gateway > 2 seconds!"
```

**Where to look:** Alertmanager (http://localhost:9093) or Grafana Alerts

### 2. TRACE (Trace) → You know WHERE the problem is

```
Open Jaeger → Find slow trace → See waterfall diagram
   → "model-catalog-service span takes 1.8s of 2s total"
```

**Where to look:** Jaeger (http://localhost:16686)

- Select Service: `api-gateway`
- Click "Find Traces"
- Click on a slow trace to see the waterfall

### 3. LOG (Log) → You know WHY it failed

```
Query Loki: {service="model-catalog-service"} |= "trace_id"
   → {"level":"ERROR","message":"Database query timed out"}
```

**Where to look:** Grafana → Explore → Loki

```logql
{app="model-catalog-service"} |= "trace_id_from_jaeger" | json
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

# Medium load (30 users, 2 minutes)
python tests/comprehensive_load_test.py \
  --url http://localhost \
  --users 30 \
  --duration 120

# Stress test (50 users, 60 seconds, stress mode)
python tests/comprehensive_load_test.py \
  --url http://localhost \
  --users 50 \
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
- CPU/memory utilization increases
- Request rate increases in Prometheus
- Traces appear in Jaeger
- Latency may increase slightly but should stabilize

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
│           │   ├── db_query (120ms)  ← Bottleneck!
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

---

## Troubleshooting

### HPA Shows `<unknown>` for Metrics

**Solution:** Install metrics-server (see [Install Metrics Server](#install-metrics-server-required-for-hpa))

### No Traces in Jaeger

1. Check services have OTel env vars:

```bash
kubectl get deployment api-gateway -o yaml | grep OTEL
```

2. Verify Jaeger is receiving data:

```bash
curl http://localhost:16686/api/services
```

3. Check service logs for tracing errors:

```bash
kubectl logs -l app=api-gateway | grep -i "tracing\|otel"
```

### No Logs in Loki

1. Check Promtail is running:

```bash
kubectl get pods -l app=promtail
```

2. Check Promtail logs:

```bash
kubectl logs -l app=promtail
```

3. Verify services output JSON logs:

```bash
kubectl logs -l app=api-gateway --tail=5
```

### Ingress Returns 502/503 Errors

```bash
# Check ingress controller is on control-plane node
kubectl get pods -n ingress-nginx -o wide

# If on wrong node, patch it:
kubectl patch deployment ingress-nginx-controller -n ingress-nginx --type='json' -p='[
  {"op": "add", "path": "/spec/template/spec/nodeSelector", "value": {"ingress-ready": "true"}},
  {"op": "add", "path": "/spec/template/spec/tolerations", "value": [{"key": "node-role.kubernetes.io/control-plane", "operator": "Equal", "effect": "NoSchedule"}]}
]'
```

### PostgreSQL Replication Issues (HA Mode)

```bash
# Check replication status
kubectl exec postgres-primary-0 -- psql -U rllabs -d postgres -c "SELECT * FROM pg_stat_replication;"

# Check databases exist
kubectl exec postgres-primary-0 -- psql -U rllabs -d postgres -c "SELECT datname FROM pg_database;"
```

### MongoDB Replica Set Issues

```bash
# Check replica set status
kubectl exec mongodb-0 -c mongodb -- mongosh --eval "rs.status()"

# If not initialized, run fix script:
chmod +x kubernetes/fix-mongodb-replica-set.sh
./kubernetes/fix-mongodb-replica-set.sh
```

### Redis Sentinel Issues (HA Mode)

```bash
# Check Sentinel masters
kubectl exec redis-sentinel-0 -- redis-cli -p 26379 SENTINEL masters

# Check master info
kubectl exec redis-master-0 -- redis-cli -a redis_password INFO replication
```

---

## Quick Reference Commands

```bash
# === Observability Stack ===
./scripts/deploy_observability.sh                    # Deploy full stack
./scripts/rebuild_services_with_otel.sh              # Rebuild with tracing

# === Port Forwards ===
kubectl port-forward svc/grafana 3000:3000           # Grafana
kubectl port-forward svc/prometheus 9090:9090        # Prometheus
kubectl port-forward svc/jaeger-query 16686:16686    # Jaeger
kubectl port-forward svc/alertmanager 9093:9093      # Alertmanager

# === Status ===
kubectl get pods                                      # All pods
kubectl get hpa                                       # Autoscaling
kubectl get pods -l 'app in (prometheus,grafana,alertmanager,jaeger,loki,promtail)'

# === Testing ===
curl http://localhost/health                          # Health check
curl http://localhost/api/models                      # Test API
python tests/comprehensive_load_test.py --url http://localhost --users 30

# === Logs ===
kubectl logs -l app=api-gateway --tail=20            # View JSON logs

# === Traces ===
curl http://localhost:16686/api/services             # List traced services

# === HA Verification ===
kubectl exec postgres-primary-0 -- psql -U rllabs -c "SELECT * FROM pg_stat_replication;"
kubectl exec redis-sentinel-0 -- redis-cli -p 26379 SENTINEL masters
kubectl exec rabbitmq-0 -- rabbitmqctl cluster_status
kubectl exec mongodb-0 -c mongodb -- mongosh --eval "rs.status()"
```

---

## Performance Optimization Summary

### Phase 5 Infrastructure Optimizations (Applied)

The system has been optimized through 5 phases of performance improvements:

**PostgreSQL Configuration:**

- `work_mem`: 16MB (prevents disk spills for complex queries)
- `maintenance_work_mem`: 128MB (faster VACUUM/INDEX operations)
- `shared_buffers`: 256MB (25% of memory, improved caching)
- `effective_cache_size`: 768MB (75% of memory, better query planning)
- Read replicas configured with same performance parameters

**Redis Configuration:**

- `maxmemory`: 100mb (master), 50mb (replicas) - prevents OOM kills
- `maxmemory-policy`: allkeys-lru - evicts least recently used keys
- `maxclients`: 10000 - explicit connection limit

**MinIO Configuration:**

- CPU limits: 1000m (increased from 500m for erasure coding)
- CPU requests: 200m (increased from 100m)

**Expected Performance (30 Users):**

- P95 latency: 150-180ms (20-30% improvement)
- P99 latency: 350-450ms (20-40% improvement)
- Success rate: 98-99% (improved from 96.38%)
- Cache hit rate: 90%+ (application-level tracking)

### Monitoring Endpoints

**Model Catalog Service:**

- `GET /cache/stats` - Cache hit rate statistics per endpoint
- `GET /database/pool-stats` - Connection pool usage (primary + replicas)
- `GET /models/{model_id}/diagnostics` - Model-specific diagnostics

**Usage:**

```bash
# Check cache performance
curl http://localhost/api/cache/stats

# Check connection pools
curl http://localhost/api/database/pool-stats

# Diagnose specific model
curl http://localhost/api/models/7154/diagnostics
```

## Summary

This guide provides a complete observability solution:

- **Metrics** (Prometheus) - What is happening?
- **Logs** (Loki) - Why did it happen?
- **Traces** (Jaeger) - Where is the problem?
- **Alerts** (Alertmanager) - When should we be notified?
- **Dashboards** (Grafana) - Single pane of glass
- **Performance Monitoring** - Cache stats, connection pools, diagnostics

**The Debugging Journey:**

```
1. ALERT fires → You know WHAT is wrong (Metrics)
2. Find TRACE → You know WHERE the problem is (Jaeger)
3. Query LOGS → You know WHY it failed (Loki)
4. Check DIAGNOSTICS → You know HOW to fix it (Monitoring endpoints)
```

**Quick Start (One-Command Setup - Recommended):**

```bash
# Complete setup: Zero to Load Testing Ready
# Deploys infrastructure, services, observability, and starts port-forwards
./scripts/start_everything.sh
```

This single script does **everything**:

1. Checks prerequisites (kubectl, cluster, ingress)
2. Deploys infrastructure (PostgreSQL, Redis, RabbitMQ, MongoDB, MinIO)
3. Initializes databases and storage
4. Deploys application services
5. Deploys full observability stack (Prometheus, Grafana, Jaeger, Loki, Alertmanager)
6. Deploys HPA, Ingress, and metrics-server
7. Waits for everything to be ready
8. Starts port-forwards automatically
9. Verifies setup and provides access instructions
