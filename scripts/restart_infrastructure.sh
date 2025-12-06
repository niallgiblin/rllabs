#!/bin/bash
# Infrastructure Restart Script
# Safely restarts all infrastructure services in the correct order

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║        RLLabs Infrastructure Restart Script                   ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Function to wait for pods to be ready
wait_for_ready() {
    local selector=$1
    local name=$2
    local timeout=${3:-120}  # Default 2 minutes
    local count=0
    
    echo -n "  Waiting for $name to be ready..."
    while [ $count -lt $timeout ]; do
        local ready=$(kubectl get pods $selector --no-headers 2>/dev/null | awk '$2 ~ /^[1-9]/ {count++} END {print count+0}')
        local total=$(kubectl get pods $selector --no-headers 2>/dev/null | wc -l | tr -d ' ')
        
        if [ "$ready" -eq "$total" ] && [ "$total" -gt 0 ]; then
            echo -e " ${GREEN}✅${NC}"
            return 0
        fi
        
        sleep 2
        count=$((count + 2))
        echo -n "."
    done
    
    echo -e " ${YELLOW}⚠️  (timeout after ${timeout}s)${NC}"
    return 1
}

# Function to restart a deployment/statefulset
restart_resource() {
    local type=$1
    local name=$2
    local description=$3
    
    echo -e "${BLUE}🔄 Restarting $description...${NC}"
    
    if kubectl get $type $name &>/dev/null; then
        kubectl delete pod -l app=$name --wait=false 2>/dev/null || true
        sleep 3
        echo -e "  ${GREEN}✅ $description restart initiated${NC}"
    else
        echo -e "  ${YELLOW}⚠️  $description not found, skipping${NC}"
    fi
}

echo -e "${BLUE}📦 STEP 1: Restarting PostgreSQL${NC}"
echo "───────────────────────────────────────────────────────────────"

# Delete problematic PostgreSQL primary pod
if kubectl get pod postgres-primary-0 &>/dev/null; then
    echo "  Deleting crashed PostgreSQL primary pod..."
    kubectl delete pod postgres-primary-0 --wait=false 2>/dev/null || true
    sleep 5
fi

# Restart PostgreSQL replicas
restart_resource "statefulset" "postgres-replica" "PostgreSQL Replicas"
wait_for_ready "-l app=postgres-replica" "PostgreSQL Replicas" 60

echo ""
echo -e "${BLUE}📦 STEP 2: Restarting Redis${NC}"
echo "───────────────────────────────────────────────────────────────"

# Restart Redis Sentinel (critical for Redis HA)
echo "  Restarting Redis Sentinel..."
kubectl delete pod -l app=redis-sentinel --wait=false 2>/dev/null || true
sleep 5
wait_for_ready "-l app=redis-sentinel" "Redis Sentinel" 60

# Restart Redis master and replicas
restart_resource "statefulset" "redis-master" "Redis Master"
restart_resource "statefulset" "redis-replica" "Redis Replicas"
wait_for_ready "-l app=redis-master" "Redis Master" 60
wait_for_ready "-l app=redis-replica" "Redis Replicas" 60

echo ""
echo -e "${BLUE}📦 STEP 3: Restarting MongoDB${NC}"
echo "───────────────────────────────────────────────────────────────"

# Restart MongoDB StatefulSet
if kubectl get statefulset mongodb &>/dev/null; then
    echo "  Restarting MongoDB StatefulSet..."
    kubectl delete pod -l app=mongodb --wait=false 2>/dev/null || true
    sleep 5
    wait_for_ready "-l app=mongodb" "MongoDB" 120
else
    echo -e "  ${YELLOW}⚠️  MongoDB StatefulSet not found${NC}"
fi

echo ""
echo -e "${BLUE}📦 STEP 4: Restarting RabbitMQ${NC}"
echo "───────────────────────────────────────────────────────────────"

restart_resource "statefulset" "rabbitmq" "RabbitMQ"
wait_for_ready "-l app=rabbitmq" "RabbitMQ" 90

echo ""
echo -e "${BLUE}📦 STEP 5: Restarting MinIO${NC}"
echo "───────────────────────────────────────────────────────────────"

restart_resource "statefulset" "minio" "MinIO"
wait_for_ready "-l app=minio" "MinIO" 90

echo ""
echo -e "${BLUE}📦 STEP 6: Forming RabbitMQ Cluster${NC}"
echo "───────────────────────────────────────────────────────────────"

# Form RabbitMQ cluster if multiple replicas
REPLICAS=$(kubectl get statefulset rabbitmq -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "1")
if [ "$REPLICAS" -gt 1 ]; then
    echo "  Forming RabbitMQ cluster with $REPLICAS nodes..."
    
    # Wait for all pods to be ready
    wait_for_ready "-l app=rabbitmq" "RabbitMQ" 120
    
    # Join nodes to cluster
    for i in $(seq 1 $((REPLICAS - 1))); do
        POD_NAME="rabbitmq-$i"
        echo -n "  Joining $POD_NAME to cluster..."
        
        # Check if already in cluster
        if kubectl exec $POD_NAME -- rabbitmqctl cluster_status 2>&1 | grep -q "rabbitmq-0@rabbitmq-0" 2>/dev/null; then
            echo -e " ${GREEN}✅ Already in cluster${NC}"
            continue
        fi
        
        # Join cluster
        kubectl exec $POD_NAME -- rabbitmqctl stop_app 2>&1 | grep -v "Defaulted container" >/dev/null || true
        kubectl exec $POD_NAME -- rabbitmqctl join_cluster rabbitmq-0@rabbitmq-0.rabbitmq.default.svc.cluster.local 2>&1 | grep -v "Defaulted container" >/dev/null && \
            echo -e " ${GREEN}✅ Joined${NC}" || echo -e " ${YELLOW}⚠️  May have failed${NC}"
        kubectl exec $POD_NAME -- rabbitmqctl start_app 2>&1 | grep -v "Defaulted container" >/dev/null || true
        sleep 2
    done
    
    # Set HA policy
    echo "  Setting HA policy..."
    kubectl exec rabbitmq-0 -- rabbitmqctl set_policy ha-all ".*" '{"ha-mode":"all","ha-sync-mode":"automatic"}' 2>&1 | grep -v "Defaulted container" >/dev/null || true
    echo -e "  ${GREEN}✅ HA policy configured${NC}"
else
    echo "  Single replica, skipping cluster formation"
fi

echo ""
echo "───────────────────────────────────────────────────────────────"
echo -e "${GREEN}✅ Infrastructure restart complete!${NC}"
echo ""
echo "Waiting 30 seconds for services to stabilize..."
sleep 30

echo ""
echo -e "${BLUE}📊 Checking final status...${NC}"
echo "───────────────────────────────────────────────────────────────"

# Run health check
if [ -f "./scripts/check_system_health.sh" ]; then
    ./scripts/check_system_health.sh
else
    echo "  Running quick status check..."
    kubectl get pods -l 'app in (postgres-primary,postgres-replica,redis-master,redis-replica,redis-sentinel,mongodb,rabbitmq,minio)' --no-headers | head -20
fi

echo ""
echo -e "${GREEN}✨ Done! Infrastructure services have been restarted.${NC}"
echo ""
echo "Next steps:"
echo "  1. Wait 1-2 minutes for all services to fully start"
echo "  2. Run './scripts/restart_services.sh' to restart application services"
echo "  3. Run './scripts/check_system_health.sh' to verify everything is healthy"

