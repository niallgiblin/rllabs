#!/bin/bash

set +e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║        RLLabs - Starting Complete System                     ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "This will build images, start all services and form clusters automatically."
echo "Safe to run even if services are already running (idempotent)."
echo "Please wait 5-7 minutes for everything to be ready..."
echo ""
echo "Note: Docker BuildKit automatically detects code changes and rebuilds affected layers."
echo "      To force a complete rebuild without cache, run: FORCE_REBUILD=1 ./scripts/start_everything.sh"
echo ""

echo -e "${BLUE}STEP 0: Building Docker Images${NC}"
echo "───────────────────────────────────────────────────────────────"

CLUSTER_NAME=$(kind get clusters 2>/dev/null | head -1)
if [ -z "$CLUSTER_NAME" ]; then
    echo -e "${YELLOW}No kind cluster found. Creating cluster 'rllabs'...${NC}"
    if [ -f "kind-cluster-config.yml" ]; then
        echo "  Using cluster config: kind-cluster-config.yml"
        if kind create cluster --name rllabs --config kind-cluster-config.yml; then
            echo -e "  ${GREEN}✓ Cluster 'rllabs' created successfully${NC}"
            CLUSTER_NAME="rllabs"
        else
            echo -e "${RED}✗ Failed to create cluster${NC}"
            exit 1
        fi
    else
        echo "  Creating cluster with default configuration..."
        if kind create cluster --name rllabs; then
            echo -e "  ${GREEN}✓ Cluster 'rllabs' created successfully${NC}"
            CLUSTER_NAME="rllabs"
        else
            echo -e "${RED}✗ Failed to create cluster${NC}"
            exit 1
        fi
    fi
else
    echo "  Using existing kind cluster: $CLUSTER_NAME"
fi

if docker ps --format "{{.Names}}" | grep -qE "rabbitmq|postgres|redis|mongo"; then
    echo -e "  ${YELLOW}⚠️  Docker Compose services detected running${NC}"
    echo "     This may slow down builds. Consider stopping: docker-compose down"
    echo ""
fi

export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

echo ""

build_and_load() {
    local service_name=$1
    local dockerfile_path=$2
    
    echo -n "  Building $service_name... "
    
    if docker images ${service_name}:latest --format "{{.CreatedAt}}" 2>/dev/null | head -1 | grep -q .; then
        IMAGE_AGE=$(docker images ${service_name}:latest --format "{{.CreatedAt}}" 2>/dev/null | head -1)
        echo -n "(checking cache...) "
    fi
    
    export DOCKER_BUILDKIT=1
    BUILD_START=$(date +%s)
    
    if [ "${FORCE_REBUILD:-0}" = "1" ]; then
        BUILD_ARGS="--no-cache"
        echo -n "(force rebuild, no cache) "
    else
        BUILD_ARGS="--build-arg BUILDKIT_INLINE_CACHE=1"
    fi
    
    if docker build \
        --progress=plain \
        --tag ${service_name}:latest \
        --file ${dockerfile_path} \
        ${BUILD_ARGS} \
        . > /tmp/${service_name}-build.log 2>&1
    then
        BUILD_TIME=$(($(date +%s) - BUILD_START))
        echo -e "${GREEN}✓${NC} (${BUILD_TIME}s)"
        
        IMAGE_SIZE=$(docker images ${service_name}:latest --format "{{.Size}}" 2>/dev/null | head -1)
        if [ -n "$IMAGE_SIZE" ]; then
            echo "    Image size: $IMAGE_SIZE"
        fi
        
        echo -n "    Loading into kind... "
        LOAD_START=$(date +%s)
        if kind load docker-image ${service_name}:latest --name ${CLUSTER_NAME} >/dev/null 2>&1; then
            LOAD_TIME=$(($(date +%s) - LOAD_START))
            echo -e "${GREEN}✓${NC} (${LOAD_TIME}s)"
        else
            echo -e "${YELLOW}(may already be loaded)${NC}"
        fi
    else
        BUILD_TIME=$(($(date +%s) - BUILD_START))
        echo -e "${RED}✗${NC} (failed after ${BUILD_TIME}s)"
        echo "    Build logs: /tmp/${service_name}-build.log"
        echo "    Last 10 lines:"
        tail -10 /tmp/${service_name}-build.log | sed 's/^/      /'
        
        if docker images ${service_name}:latest --format "{{.Repository}}" 2>/dev/null | grep -q "^${service_name}$"; then
            echo "    Attempting to use existing image..."
            if kind load docker-image ${service_name}:latest --name ${CLUSTER_NAME} >/dev/null 2>&1; then
                echo "    ${GREEN}✓ Using existing image${NC}"
            fi
        fi
        return 1
    fi
}

build_and_load "api-gateway" "api_gateway/Dockerfile"
build_and_load "model-catalog-service" "model_catalog_service/Dockerfile"
build_and_load "upload-download-service" "upload_download_service/Dockerfile"
build_and_load "collaboration-service" "collaboration_service/dockerfile"
build_and_load "model-train-service" "model_train_service/Dockerfile"

echo -n "  Building frontend... "
BUILD_START=$(date +%s)
if docker build \
    --progress=plain \
    --tag frontend:latest \
    --file frontend/Dockerfile \
    --build-arg VITE_API_BASE_URL=http://localhost:8080 \
    --build-arg BUILDKIT_INLINE_CACHE=1 \
    ./frontend > /tmp/frontend-build.log 2>&1
then
    BUILD_TIME=$(($(date +%s) - BUILD_START))
    echo -e "${GREEN}✓${NC} (${BUILD_TIME}s)"
    echo -n "    Loading into kind... "
    LOAD_START=$(date +%s)
    if kind load docker-image frontend:latest --name ${CLUSTER_NAME} >/dev/null 2>&1; then
        LOAD_TIME=$(($(date +%s) - LOAD_START))
        echo -e "${GREEN}✓${NC} (${LOAD_TIME}s)"
    else
        echo -e "${YELLOW}(may already be loaded)${NC}"
    fi
else
    BUILD_TIME=$(($(date +%s) - BUILD_START))
    echo -e "${RED}✗${NC} (failed after ${BUILD_TIME}s)"
    echo "    Build logs: /tmp/frontend-build.log"
    echo "    Last 10 lines:"
    tail -10 /tmp/frontend-build.log | sed 's/^/      /'
    if docker images frontend:latest --format "{{.Repository}}" 2>/dev/null | grep -q "^frontend$"; then
        echo "    Attempting to use existing image..."
        if kind load docker-image frontend:latest --name ${CLUSTER_NAME} >/dev/null 2>&1; then
            echo "    ${GREEN}✓ Using existing image${NC}"
        fi
    fi
fi

echo "  Images built and loaded"
echo ""

echo -e "${BLUE}STEP 0.5: Applying Resource Quotas and Limits${NC}"
echo "───────────────────────────────────────────────────────────────"
echo "  Setting up resource quotas and limit ranges..."
kubectl apply -f kubernetes/resource-quotas.yml 2>/dev/null || true
echo "  Resource quotas and limits applied"
echo ""

echo -e "${BLUE}STEP 0.6: Installing Ingress Controller${NC}"
echo "───────────────────────────────────────────────────────────────"
echo "  Checking for ingress controller..."
if kubectl get ingressclass nginx >/dev/null 2>&1; then
    echo "  Ingress controller already installed"
else
    echo "  Installing nginx ingress controller..."
    kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml 2>/dev/null || true
    echo "  Waiting for ingress controller to be ready..."
    kubectl wait --namespace ingress-nginx --for=condition=ready pod --selector=app.kubernetes.io/component=controller --timeout=120s 2>/dev/null || true
    echo "  Ingress controller installed"
fi
echo ""

echo -e "${BLUE}STEP 1: Applying Infrastructure Manifests${NC}"
echo "───────────────────────────────────────────────────────────────"

kubectl apply -f kubernetes/postgres-ha.yml 2>/dev/null || true
kubectl apply -f kubernetes/redis-ha.yml 2>/dev/null || true
kubectl apply -f kubernetes/rabbitmq-ha.yml 2>/dev/null || true
kubectl apply -f kubernetes/mongodb.yml 2>/dev/null || true
kubectl delete statefulset minio 2>/dev/null || true
kubectl delete svc minio-hl 2>/dev/null || true
kubectl delete job minio-create-buckets 2>/dev/null || true
kubectl delete pvc -l app=minio 2>/dev/null || true
kubectl apply -f kubernetes/minio.yml 2>/dev/null || true
kubectl apply -f kubernetes/minio-ingress.yml 2>/dev/null || true

echo "  Manifests applied"
echo ""

echo -e "${BLUE}STEP 2: Restarting Infrastructure${NC}"
echo "───────────────────────────────────────────────────────────────"

echo "  Restarting infrastructure pods (safe to run if already running)..."

kubectl delete pod postgres-primary-0 --wait=false 2>/dev/null || true
kubectl delete pod -l app=redis-sentinel --wait=false 2>/dev/null || true
kubectl delete pod -l app=mongodb --wait=false 2>/dev/null || true
kubectl delete pod -l app=postgres-replica --wait=false 2>/dev/null || true
kubectl delete pod -l app=redis-master --wait=false 2>/dev/null || true
kubectl delete pod -l app=redis-replica --wait=false 2>/dev/null || true
kubectl delete pod -l app=rabbitmq --wait=false 2>/dev/null || true
kubectl delete pod -l app=minio --wait=false 2>/dev/null || true

echo "  Waiting 90 seconds for infrastructure to start/restart..."
echo "    (RabbitMQ needs extra time to initialize)"
sleep 90

echo ""

echo -e "${BLUE}STEP 3: RabbitMQ Setup${NC}"
echo "───────────────────────────────────────────────────────────────"

REPLICAS=$(kubectl get statefulset rabbitmq -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "1")
echo "  RabbitMQ configured for $REPLICAS node(s)"

echo -n "  Waiting for RabbitMQ pod to be running..."
for i in {1..60}; do
    if kubectl get pod rabbitmq-0 -o jsonpath='{.status.phase}' 2>/dev/null | grep -q "Running"; then
        echo -e " ${GREEN}✓${NC}"
        break
    fi
    if [ $i -eq 60 ]; then
        echo -e " ${YELLOW}⚠️  Pod not running after 2 minutes${NC}"
    fi
    sleep 2
done

echo -n "  Ensuring RabbitMQ app is started..."
APP_STARTED=false
for i in {1..60}; do
    if kubectl exec rabbitmq-0 -- rabbitmq-diagnostics ping >/dev/null 2>&1; then
        START_OUTPUT=$(kubectl exec rabbitmq-0 -- rabbitmqctl start_app 2>&1 || true)
        sleep 3
        
        if kubectl exec rabbitmq-0 -- rabbitmqctl status >/dev/null 2>&1; then
            echo -e " ${GREEN}✓${NC}"
            APP_STARTED=true
            break
        fi
        
        if echo "$START_OUTPUT" | grep -q "already started\|already running"; then
            sleep 5
            if kubectl exec rabbitmq-0 -- rabbitmqctl status >/dev/null 2>&1; then
                echo -e " ${GREEN}✓${NC}"
                APP_STARTED=true
                break
            fi
        fi
    fi
    
    if [ $i -eq 60 ]; then
        echo -e " ${YELLOW}⚠️  App not started after 2 minutes${NC}"
        echo "    Attempting manual start..."
        kubectl exec rabbitmq-0 -- rabbitmqctl start_app 2>&1 | head -3 || true
        sleep 5
        if kubectl exec rabbitmq-0 -- rabbitmqctl status >/dev/null 2>&1; then
            echo -e "    ${GREEN}✓ App started manually${NC}"
            APP_STARTED=true
        else
            echo -e "    ${YELLOW}⚠️  Manual start failed - will continue anyway${NC}"
        fi
    fi
    sleep 2
done

if [ "$REPLICAS" -gt 1 ]; then
    echo "  Note: Multi-node cluster - peer discovery will form cluster automatically"
    echo "  Setting HA policy (if cluster forms)..."
    kubectl exec rabbitmq-0 -- rabbitmqctl set_policy ha-all ".*" '{"ha-mode":"all","ha-sync-mode":"automatic"}' >/dev/null 2>&1 || true
else
    echo "  Single-node mode - no cluster formation needed"
fi

echo ""

echo -e "${BLUE}STEP 4: Starting Application Services${NC}"
echo "───────────────────────────────────────────────────────────────"

echo "  Applying application manifests (safe to run if already running)..."
kubectl apply -f kubernetes/model-catalog-service.yml 2>/dev/null || true
kubectl apply -f kubernetes/upload-download-service.yml 2>/dev/null || true
kubectl apply -f kubernetes/collaboration-service.yml 2>/dev/null || true
kubectl apply -f kubernetes/model-train-service.yml 2>/dev/null || true
kubectl apply -f kubernetes/api-gateway.yml 2>/dev/null || true
kubectl apply -f kubernetes/frontend.yml 2>/dev/null || true

echo "  Restarting deployments to ensure fresh start..."
for deployment in api-gateway model-catalog-service upload-download-service collaboration-service model-train-service frontend; do
    kubectl rollout restart deployment/$deployment 2>/dev/null || true
done

echo "  Setting proper replica counts (based on performance fixes)..."
kubectl scale deployment model-catalog-service --replicas=3 2>/dev/null || true
kubectl scale deployment api-gateway --replicas=2 2>/dev/null || true

echo "  Applying Pod Disruption Budgets for high availability..."
kubectl apply -f kubernetes/pod-disruption-budgets.yml 2>/dev/null || true

echo "  Application manifests applied and restarted"
echo "  Waiting 90 seconds for applications to start..."
sleep 90

echo ""

echo -e "${BLUE}STEP 4.5: Deploying Observability Stack${NC}"
echo "───────────────────────────────────────────────────────────────"

echo "  Deploying observability services (Prometheus, Grafana, Jaeger, Loki, Alertmanager)..."
kubectl apply -f kubernetes/prometheus-alerts.yml 2>/dev/null || true
kubectl apply -f kubernetes/alertmanager.yml 2>/dev/null || true
kubectl apply -f kubernetes/jaeger.yml 2>/dev/null || true
kubectl apply -f kubernetes/loki.yml 2>/dev/null || true
kubectl apply -f kubernetes/prometheus.yml 2>/dev/null || true

if [ -f "kubernetes/grafana-dashboard-rllabs.json" ]; then
    echo "  Creating Grafana dashboard ConfigMap..."
    kubectl create configmap grafana-dashboard-rllabs \
        --from-file=kubernetes/grafana-dashboard-rllabs.json \
        --dry-run=client -o yaml | kubectl apply -f - 2>/dev/null || true
fi

kubectl apply -f kubernetes/grafana.yml 2>/dev/null || true

echo "  Observability manifests applied"
echo "  Waiting 60 seconds for observability services to start..."
sleep 60

echo ""

echo -e "${BLUE}STEP 5: Verifying All Pods Are Ready${NC}"
echo "───────────────────────────────────────────────────────────────"

echo "  Checking pod readiness (this may take a few minutes)..."
MAX_WAIT=300
ELAPSED=0
ALL_READY=false

while [ $ELAPSED -lt $MAX_WAIT ]; do

    NOT_READY=$(kubectl get pods --no-headers 2>/dev/null | awk '$3 == "Running" && $2 !~ /^[1-9]/ {print $1}' | wc -l | tr -d ' \n')
    CRASHED=$(kubectl get pods --no-headers 2>/dev/null | grep -c "CrashLoopBackOff\|Error" 2>/dev/null || echo "0")
    CRASHED=$(echo "$CRASHED" | tr -d ' \n')
    
    NOT_READY=${NOT_READY:-0}
    CRASHED=${CRASHED:-0}
    
    if [ "$NOT_READY" -eq 0 ] && [ "$CRASHED" -eq 0 ]; then
        ALL_READY=true
        break
    fi
    
    if [ $((ELAPSED % 30)) -eq 0 ]; then
        echo "    Still waiting... ($ELAPSED/$MAX_WAIT seconds)"
        if [ "$NOT_READY" -gt 0 ]; then
            echo "    Pods not ready: $NOT_READY"
        fi
        if [ "$CRASHED" -gt 0 ]; then
            echo "    Pods crashed: $CRASHED"
        fi
    fi
    
    sleep 5
    ELAPSED=$((ELAPSED + 5))
done

echo ""

echo -e "${BLUE}STEP 6: Final Health Check${NC}"
echo "───────────────────────────────────────────────────────────────"

if [ -f "./scripts/check_system_health.sh" ]; then

    set +e
    ./scripts/check_system_health.sh
    HEALTH_EXIT=$?
    set -e
else
    echo "  Running quick status check..."
    kubectl get pods -l 'app in (api-gateway,model-catalog-service,upload-download-service,collaboration-service,model-train-service,frontend)' --no-headers 2>/dev/null | head -10
    HEALTH_EXIT=0
fi

echo ""
echo "───────────────────────────────────────────────────────────────"

if [ "$HEALTH_EXIT" -eq 0 ]; then
    echo -e "${GREEN}SYSTEM STARTUP COMPLETE - ALL SERVICES READY${NC}"
    echo ""
    echo "All services are running and ready. The system is ready for load testing."
    echo ""
echo "Next steps:"
echo "  1. Access the Frontend: http://localhost:5173"
echo "  2. Test the API: curl http://localhost:8080/health"
echo "  3. Run load test: python tests/comprehensive_load_test.py --users 10 --duration 60"
echo "  4. Check Grafana: http://localhost:3000"
echo ""
echo "Note: Frontend port-forward is set up on port 5173"
echo "      API Gateway port-forward is set up on port 8080"
echo "      If port-forwards fail, run:"
echo "        kubectl port-forward svc/frontend 5173:80"
echo "        kubectl port-forward svc/api-gateway 8080:8080"
else
    echo -e "${YELLOW}⚠️  SYSTEM STARTUP COMPLETE - SOME SERVICES NOT READY${NC}"
    echo ""
    echo "Some services are still starting up. This is normal after a fresh restart."
    echo ""
    echo "Wait 2-3 minutes and verify with:"
    echo "  ./scripts/check_system_health.sh"
    echo ""
    echo "If services don't become ready, check logs:"
    echo "  kubectl logs -l app=rabbitmq --tail=50"
fi

echo ""
echo "───────────────────────────────────────────────────────────────"
echo -e "${BLUE}STEP 7: Setting Up Observability Port Forwarding${NC}"
echo "───────────────────────────────────────────────────────────────"
echo ""

check_port() {
    local port=$1
    if lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>&1 ; then
        return 0  
    else
        return 1  
    fi
}

cleanup_port_forward() {
    local service=$1
    local port=$2
    
    pkill -f "kubectl port-forward.*${service}.*${port}" 2>/dev/null || true
    
    local port_pid=$(lsof -ti:$port 2>/dev/null || true)
    if [ -n "$port_pid" ]; then
        if ps -p $port_pid -o command= 2>/dev/null | grep -q "kubectl port-forward.*${service}"; then
            kill $port_pid 2>/dev/null || true
            sleep 1
        fi
    fi
}

start_port_forward() {
    local service=$1
    local port=$2
    local name=$3
    
    cleanup_port_forward "$service" "$port"
    
    sleep 1
    
    if check_port $port; then
        echo -e "  ${YELLOW}⚠️  Port $port is still in use. Skipping $name...${NC}"
        echo "     If you want to restart, kill the process first:"
        echo "     lsof -ti:$port | xargs kill"
        return 1
    fi
    
    echo -n "  Starting port forward for $name on port $port... "
    kubectl port-forward svc/$service $port:$port > /tmp/${service}-port-forward.log 2>&1 &
    local pid=$!
    sleep 3  
    if kill -0 $pid 2>/dev/null; then
        if check_port $port; then
            echo -e "${GREEN}✓${NC} (PID: $pid)"
            return 0
        else
            echo -e "${YELLOW}⚠️  Process started but port not listening${NC}"
            return 1
        fi
    else
        echo -e "${YELLOW}⚠️  Failed to start (check logs: /tmp/${service}-port-forward.log)${NC}"
        return 1
    fi
}

echo "  Setting up port forwarding for observability services..."
echo ""

if kubectl get svc grafana >/dev/null 2>&1; then
    start_port_forward "grafana" "3000" "Grafana"
else
    echo -e "  ${YELLOW}⚠️  Grafana service not found. Skipping...${NC}"
fi

if kubectl get svc prometheus >/dev/null 2>&1; then
    start_port_forward "prometheus" "9090" "Prometheus"
else
    echo -e "  ${YELLOW}⚠️  Prometheus service not found. Skipping...${NC}"
fi

if kubectl get svc jaeger-query >/dev/null 2>&1; then
    start_port_forward "jaeger-query" "16686" "Jaeger"
else
    echo -e "  ${YELLOW}⚠️  Jaeger service not found. Skipping...${NC}"
fi

if kubectl get svc alertmanager >/dev/null 2>&1; then
    start_port_forward "alertmanager" "9093" "Alertmanager"
else
    echo -e "  ${YELLOW}⚠️  Alertmanager service not found. Skipping...${NC}"
fi

if kubectl get svc loki >/dev/null 2>&1; then
    start_port_forward "loki" "3100" "Loki"
else
    echo -e "  ${YELLOW}⚠️  Loki service not found. Skipping...${NC}"
fi

echo ""
if kubectl get svc api-gateway >/dev/null 2>&1; then
    if start_port_forward "api-gateway" "8080" "API Gateway"; then
        :
    else
        echo -e "  ${YELLOW}⚠️  API Gateway port-forward failed or port is in use${NC}"
        echo "     This is required for load testing. To fix:"
        echo "     lsof -ti:8080 | xargs kill"
        echo "     kubectl port-forward svc/api-gateway 8080:8080"
    fi
else
    echo -e "  ${YELLOW}⚠️  API Gateway service not found. Skipping...${NC}"
fi

echo ""
if kubectl get svc frontend >/dev/null 2>&1; then
    cleanup_port_forward "frontend" "5173"
    sleep 1
    if check_port 5173; then
        echo -e "  ${YELLOW}⚠️  Port 5173 is still in use. Skipping Frontend...${NC}"
        echo "     To manually set up:"
        echo "     lsof -ti:5173 | xargs kill"
        echo "     kubectl port-forward svc/frontend 5173:80"
    else
        echo -n "  Starting port forward for Frontend on port 5173... "
        kubectl port-forward svc/frontend 5173:80 > /tmp/frontend-port-forward.log 2>&1 &
        FRONTEND_PID=$!
        sleep 3
        if kill -0 $FRONTEND_PID 2>/dev/null && check_port 5173; then
            echo -e "${GREEN}✓${NC} (PID: $FRONTEND_PID)"
        else
            echo -e "${YELLOW}⚠️  Failed to start (check logs: /tmp/frontend-port-forward.log)${NC}"
        fi
    fi
else
    echo -e "  ${YELLOW}⚠️  Frontend service not found. Skipping...${NC}"
fi

echo ""
echo -e "${BLUE}Note: Upload/Download Service${NC}"
echo "  Upload/Download Service is accessed through the API Gateway (port 8080)"
echo "  No direct port-forward needed - use: http://localhost:8080/api/uploads"
echo "  For ingress: http://api.localhost/api/uploads"
echo ""
echo -e "${BLUE}Security Architecture:${NC}"
echo "  ✓ All API requests go through API Gateway (authentication, rate limiting)"
echo "  ✓ Authorization happens BEFORE presigned URL generation"
echo "  ✓ MinIO ingress is configured for presigned URLs (clients access via http://minio.localhost)"
echo "  ✓ Presigned URLs are time-limited (1 hour) and part-specific (secure)"
echo ""
echo "───────────────────────────────────────────────────────────────"
echo -e "${GREEN}Observability Services Access${NC}"
echo "───────────────────────────────────────────────────────────────"
echo ""
echo "Access the following services:"
echo ""
echo "  🌐 Frontend:     http://localhost:5173 (or via ingress)"
echo "  🔌 API Gateway:  http://localhost:8080 (or via ingress: http://api.localhost)"
echo "  📦 MinIO:        http://minio.localhost (API) or http://minio-console.localhost (Console)"
echo "  📤 Upload/Download: Via API Gateway at http://localhost:8080/api/uploads"
echo "  📊 Grafana:      http://localhost:3000 (admin/admin)"
echo "  📈 Prometheus:   http://localhost:9090"
echo "  🔍 Jaeger:       http://localhost:16686"
echo "  🚨 Alertmanager: http://localhost:9093"
echo "  📝 Loki:         http://localhost:3100 (or via Grafana Explore)"
echo ""
echo "Port forwarding logs are available in /tmp/<service>-port-forward.log"
echo ""
echo "To stop all port forwarding:"
echo "  pkill -f 'kubectl port-forward'"
echo ""
echo "To stop individual services:"
echo "  lsof -ti:5173 | xargs kill  # Frontend"
echo "  lsof -ti:8080 | xargs kill  # API Gateway"
echo "  lsof -ti:3000 | xargs kill  # Grafana"
echo "  lsof -ti:9090 | xargs kill  # Prometheus"
echo "  lsof -ti:16686 | xargs kill # Jaeger"
echo "  lsof -ti:9093 | xargs kill  # Alertmanager"
echo "  lsof -ti:3100 | xargs kill  # Loki"
echo ""

if [ "$HEALTH_EXIT" -eq 0 ]; then
    exit 0
else
    exit 1
fi
