#!/bin/bash
# Application Services Restart Script
# Restarts all application services after infrastructure is healthy

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║        RLLabs Application Services Restart Script             ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Check if infrastructure is healthy first
echo -e "${BLUE}🔍 Checking infrastructure health first...${NC}"
if ! kubectl get pods -l app=postgres-primary --no-headers 2>/dev/null | grep -q Running; then
    echo -e "${RED}❌ PostgreSQL primary is not running!${NC}"
    echo "   Please run './scripts/restart_infrastructure.sh' first."
    exit 1
fi

if ! kubectl get pods -l app=redis-master --no-headers 2>/dev/null | grep -q Running; then
    echo -e "${RED}❌ Redis master is not running!${NC}"
    echo "   Please run './scripts/restart_infrastructure.sh' first."
    exit 1
fi

echo -e "${GREEN}✅ Infrastructure looks healthy${NC}"
echo ""

# Function to restart a deployment
restart_deployment() {
    local name=$1
    local description=$2
    
    echo -e "${BLUE}🔄 Restarting $description...${NC}"
    
    if kubectl get deployment $name &>/dev/null; then
        kubectl rollout restart deployment/$name
        echo -e "  ${GREEN}✅ $description restart initiated${NC}"
        return 0
    else
        echo -e "  ${YELLOW}⚠️  $description not found, skipping${NC}"
        return 1
    fi
}

# Function to wait for deployment to be ready
wait_for_deployment() {
    local name=$1
    local timeout=${2:-120}
    
    echo -n "  Waiting for $name to be ready..."
    if kubectl wait --for=condition=available --timeout=${timeout}s deployment/$name 2>/dev/null; then
        echo -e " ${GREEN}✅${NC}"
        return 0
    else
        echo -e " ${YELLOW}⚠️  (timeout or not ready)${NC}"
        return 1
    fi
}

echo -e "${BLUE}📱 Restarting Application Services${NC}"
echo "───────────────────────────────────────────────────────────────"

# Restart services in dependency order
restart_deployment "api-gateway" "API Gateway"
restart_deployment "model-catalog-service" "Model Catalog Service"
restart_deployment "upload-download-service" "Upload/Download Service"
restart_deployment "collaboration-service" "Collaboration Service"
restart_deployment "model-train-service" "Training Service"

echo ""
echo "Waiting for deployments to become ready..."
echo "───────────────────────────────────────────────────────────────"

wait_for_deployment "api-gateway" 120
wait_for_deployment "model-catalog-service" 120
wait_for_deployment "upload-download-service" 120
wait_for_deployment "collaboration-service" 120
wait_for_deployment "model-train-service" 120

echo ""
echo "───────────────────────────────────────────────────────────────"
echo -e "${GREEN}✅ Application services restart complete!${NC}"
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
    kubectl get pods -l 'app in (api-gateway,model-catalog-service,upload-download-service,collaboration-service,model-train-service)' --no-headers | head -20
fi

echo ""
echo -e "${GREEN}✨ Done! All application services have been restarted.${NC}"
echo ""
echo "Next steps:"
echo "  1. Run './scripts/check_system_health.sh' to verify everything is healthy"
echo "  2. Test the API: curl http://localhost/health"
echo "  3. Check Grafana dashboard: http://localhost:3000"

