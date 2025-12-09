#!/bin/bash
# MongoDB Replica Set Fix Script
# Use this if the automatic sidecar initialization fails

set -e

echo "=========================================="
echo "MongoDB Replica Set Fix"
echo "=========================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo ""
echo "Step 1: Checking MongoDB pods..."

# Check pod status
PODS=$(kubectl get pods -l app=mongodb --no-headers 2>/dev/null | wc -l | tr -d ' ')
if [ "$PODS" -lt 3 ]; then
    echo -e "${RED}ERROR: Not all MongoDB pods are running. Found $PODS/3 pods.${NC}"
    echo "Wait for pods to be ready or check pod events:"
    echo "  kubectl get pods -l app=mongodb"
    echo "  kubectl describe pod mongodb-0"
    exit 1
fi

echo -e "${GREEN}✓ All 3 MongoDB pods found${NC}"

echo ""
echo "Step 2: Checking current replica set status..."

RS_STATUS=$(kubectl exec mongodb-0 -c mongodb -- mongosh --quiet --eval "rs.status().ok" 2>/dev/null || echo "0")

if [ "$RS_STATUS" == "1" ]; then
    echo -e "${GREEN}✓ Replica set is already initialized!${NC}"
    echo ""
    echo "Current status:"
    kubectl exec mongodb-0 -c mongodb -- mongosh --quiet --eval "rs.status().members.forEach(m => print('  ' + m.name + ': ' + m.stateStr))"
    echo ""
    echo "Nothing to do. Replica set is healthy."
    exit 0
fi

echo -e "${YELLOW}Replica set not initialized. Proceeding with initialization...${NC}"

echo ""
echo "Step 3: Waiting for all MongoDB instances to be ready..."

for i in 0 1 2; do
    echo "Checking mongodb-$i..."
    until kubectl exec mongodb-$i -c mongodb -- mongosh --quiet --eval "db.adminCommand('ping').ok" 2>/dev/null; do
        echo "  Waiting for mongodb-$i to be ready..."
        sleep 5
    done
    echo -e "  ${GREEN}✓ mongodb-$i is ready${NC}"
done

echo ""
echo "Step 4: Initializing replica set from mongodb-0..."

kubectl exec mongodb-0 -c mongodb -- mongosh --eval "
rs.initiate({
  _id: 'rs0',
  members: [
    { _id: 0, host: 'mongodb-0.mongodb:27017', priority: 2 },
    { _id: 1, host: 'mongodb-1.mongodb:27017', priority: 1 },
    { _id: 2, host: 'mongodb-2.mongodb:27017', priority: 1 }
  ]
})
"

echo ""
echo "Step 5: Waiting for replica set to stabilize..."

sleep 10

# Wait for primary election
for i in {1..30}; do
    PRIMARY=$(kubectl exec mongodb-0 -c mongodb -- mongosh --quiet --eval "rs.status().members.find(m => m.stateStr === 'PRIMARY')?.name" 2>/dev/null || echo "")
    
    if [ -n "$PRIMARY" ] && [ "$PRIMARY" != "null" ]; then
        echo -e "${GREEN}✓ Primary elected: $PRIMARY${NC}"
        break
    fi
    
    echo "  Waiting for primary election... ($i/30)"
    sleep 5
done

echo ""
echo "Step 6: Final verification..."

echo ""
echo "Replica set members:"
kubectl exec mongodb-0 -c mongodb -- mongosh --quiet --eval "rs.status().members.forEach(m => print('  ' + m.name + ': ' + m.stateStr + ' (health: ' + m.health + ')'))"

echo ""
echo "=========================================="
echo -e "${GREEN}MongoDB Replica Set Fix Complete!${NC}"
echo "=========================================="
echo ""
echo "Test connectivity from application:"
echo "  kubectl exec mongodb-0 -c mongodb -- mongosh --eval \"db.test.insertOne({test: 'data'})\""
echo "  kubectl exec mongodb-1 -c mongodb -- mongosh --eval \"db.test.find()\""

