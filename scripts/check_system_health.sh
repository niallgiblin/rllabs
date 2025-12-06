#!/bin/bash
# System Health Check Script
# Provides a clear, easy-to-understand view of system status

set -e

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║           RLLabs System Health Check                        ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to check pod status
check_pods() {
    local app=$1
    local name=$2
    
    echo -n "  $name: "
    local pods=$(kubectl get pods -l app=$app --no-headers 2>/dev/null || echo "")
    
    if [ -z "$pods" ]; then
        echo -e "${RED}❌ NOT FOUND${NC}"
        return 1
    fi
    
    local total=$(echo "$pods" | wc -l | tr -d ' ')
    local ready=$(echo "$pods" | awk '$2 ~ /^[1-9]/ {count++} END {print count+0}')
    local running=$(echo "$pods" | awk '$3 == "Running" {count++} END {print count+0}')
    local crashed=$(echo "$pods" | awk '$3 == "CrashLoopBackOff" || $3 == "Error" {count++} END {print count+0}')
    
    if [ "$ready" -eq "$total" ] && [ "$crashed" -eq 0 ]; then
        echo -e "${GREEN}✅ $ready/$total Ready${NC}"
        return 0
    elif [ "$crashed" -gt 0 ]; then
        echo -e "${RED}❌ $ready/$total Ready, $crashed Crashed${NC}"
        return 1
    elif [ "$running" -lt "$total" ]; then
        echo -e "${YELLOW}⚠️  $ready/$total Ready, $running/$total Running${NC}"
        return 1
    else
        echo -e "${YELLOW}⚠️  $ready/$total Ready (Starting...)${NC}"
        return 1
    fi
}

# Function to check infrastructure service
check_infrastructure() {
    local name=$1
    local selector=$2
    
    echo -n "  $name: "
    local pods=$(kubectl get pods $selector --no-headers 2>/dev/null || echo "")
    
    if [ -z "$pods" ]; then
        echo -e "${RED}❌ NOT FOUND${NC}"
        return 1
    fi
    
    local total=$(echo "$pods" | wc -l | tr -d ' ')
    local ready=$(echo "$pods" | awk '$2 ~ /^[1-9]/ {count++} END {print count+0}')
    local crashed=$(echo "$pods" | awk '$3 == "CrashLoopBackOff" || $3 == "Error" {count++} END {print count+0}')
    local running=$(echo "$pods" | awk '$3 == "Running" {count++} END {print count+0}')
    
    if [ "$ready" -eq "$total" ] && [ "$crashed" -eq 0 ]; then
        echo -e "${GREEN}✅ $ready/$total Ready${NC}"
        return 0
    elif [ "$crashed" -gt 0 ]; then
        echo -e "${RED}❌ $ready/$total Ready, $crashed Crashed${NC}"
        return 1
    else
        echo -e "${YELLOW}⚠️  $ready/$total Ready${NC}"
        return 1
    fi
}

# Overall health status
HEALTHY=true

echo -e "${BLUE}📊 INFRASTRUCTURE SERVICES${NC}"
echo "───────────────────────────────────────────────────────────────"

# PostgreSQL - Check by pod name since they're StatefulSets
if kubectl get pod postgres-primary-0 &>/dev/null; then
    check_infrastructure "PostgreSQL Primary" "postgres-primary-0" || HEALTHY=false
else
    check_infrastructure "PostgreSQL Primary" "-l app=postgres-primary" || HEALTHY=false
fi

if kubectl get pod postgres-replica-0 &>/dev/null; then
    check_infrastructure "PostgreSQL Replicas" "postgres-replica-0 postgres-replica-1" || HEALTHY=false
else
    check_infrastructure "PostgreSQL Replicas" "-l app=postgres-replica" || HEALTHY=false
fi

# Redis - Check by pod name since they're StatefulSets
if kubectl get pod redis-master-0 &>/dev/null; then
    check_infrastructure "Redis Master" "redis-master-0" || HEALTHY=false
else
    check_infrastructure "Redis Master" "-l app=redis-master" || HEALTHY=false
fi

if kubectl get pod redis-replica-0 &>/dev/null; then
    check_infrastructure "Redis Replicas" "redis-replica-0" || HEALTHY=false
else
    check_infrastructure "Redis Replicas" "-l app=redis-replica" || HEALTHY=false
fi

if check_infrastructure "Redis Sentinel" "-l app=redis-sentinel"; then
    :
else
    HEALTHY=false
fi

# MongoDB
if check_infrastructure "MongoDB" "-l app=mongodb"; then
    :
else
    HEALTHY=false
fi

# RabbitMQ
if check_infrastructure "RabbitMQ" "-l app=rabbitmq"; then
    :
else
    HEALTHY=false
fi

# MinIO
if check_infrastructure "MinIO" "-l app=minio"; then
    :
else
    HEALTHY=false
fi

echo ""
echo -e "${BLUE}📱 APPLICATION SERVICES${NC}"
echo "───────────────────────────────────────────────────────────────"

# Application services
if check_pods "api-gateway" "API Gateway"; then
    :
else
    HEALTHY=false
fi

if check_pods "model-catalog-service" "Model Catalog"; then
    :
else
    HEALTHY=false
fi

if check_pods "upload-download-service" "Upload/Download"; then
    :
else
    HEALTHY=false
fi

if check_pods "collaboration-service" "Collaboration"; then
    :
else
    HEALTHY=false
fi

if check_pods "model-train-service" "Training Service"; then
    :
else
    HEALTHY=false
fi

echo ""
echo -e "${BLUE}📈 OBSERVABILITY SERVICES${NC}"
echo "───────────────────────────────────────────────────────────────"

if check_pods "prometheus" "Prometheus"; then
    :
else
    HEALTHY=false
fi

if check_pods "grafana" "Grafana"; then
    :
else
    HEALTHY=false
fi

if check_pods "jaeger" "Jaeger"; then
    :
else
    HEALTHY=false
fi

if check_pods "loki" "Loki"; then
    :
else
    HEALTHY=false
fi

echo ""
echo "───────────────────────────────────────────────────────────────"

# Health check summary
# Count issues
CRITICAL_ISSUES=0
NOT_READY_ISSUES=0

# Check for critical issues (crashed services)
if kubectl get pods --no-headers 2>/dev/null | grep -q "CrashLoopBackOff\|Error"; then
    CRITICAL_ISSUES=$((CRITICAL_ISSUES + 1))
fi

# Check for pods that are Running but not Ready (this is a problem!)
NOT_READY_PODS=$(kubectl get pods --no-headers 2>/dev/null | awk '$3 == "Running" && $2 !~ /^[1-9]/ {print $1}' | wc -l | tr -d ' ')
if [ "$NOT_READY_PODS" -gt 0 ]; then
    NOT_READY_ISSUES=$NOT_READY_PODS
    echo ""
    echo -e "${YELLOW}⚠️  Pods Running but NOT Ready:${NC}"
    kubectl get pods --no-headers 2>/dev/null | awk '$3 == "Running" && $2 !~ /^[1-9]/ {print "    - " $1 " (" $2 " Ready)"}'
fi

# Check infrastructure - if core services are not ready, it's critical
if ! kubectl get pod postgres-primary-0 2>/dev/null | grep -q "1/1.*Running"; then
    CRITICAL_ISSUES=$((CRITICAL_ISSUES + 1))
fi

if ! kubectl get pod redis-master-0 2>/dev/null | grep -q "1/1.*Running"; then
    CRITICAL_ISSUES=$((CRITICAL_ISSUES + 1))
fi

# Determine status - STRICT: must be fully ready to be healthy
if [ "$CRITICAL_ISSUES" -eq 0 ] && [ "$NOT_READY_ISSUES" -eq 0 ] && [ "$HEALTHY" = true ]; then
    echo ""
    echo -e "${GREEN}✅ SYSTEM STATUS: HEALTHY${NC}"
    echo ""
    echo "All services are running and ready!"
    exit 0
elif [ "$CRITICAL_ISSUES" -eq 0 ] && [ "$NOT_READY_ISSUES" -eq 0 ]; then
    # This shouldn't happen, but if it does, report as healthy
    echo ""
    echo -e "${GREEN}✅ SYSTEM STATUS: HEALTHY${NC}"
    echo ""
    echo "All services are ready!"
    exit 0
elif [ "$CRITICAL_ISSUES" -eq 0 ]; then
    echo ""
    echo -e "${YELLOW}⚠️  SYSTEM STATUS: STARTING (NOT READY)${NC}"
    echo ""
    echo "Some pods are running but not ready yet."
    echo "This means services are still initializing."
    echo ""
    echo "Wait 2-3 minutes and run this check again:"
    echo "  ./scripts/check_system_health.sh"
    exit 1
else
    echo ""
    echo -e "${RED}❌ SYSTEM STATUS: UNHEALTHY${NC}"
    echo ""
    echo "Critical services are down or crashing."
    echo "Run './scripts/restart_infrastructure.sh' to fix infrastructure issues."
    echo "Run './scripts/restart_services.sh' to restart application services."
    exit 1
fi

