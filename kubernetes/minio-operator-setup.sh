#!/bin/bash
# MinIO Operator Setup Script
# This script installs the MinIO Operator and deploys a MinIO Tenant

set -e

echo "=========================================="
echo "MinIO Operator Setup"
echo "=========================================="

# Check for krew
if ! kubectl krew version &> /dev/null; then
    echo "ERROR: kubectl krew not found!"
    echo "Install krew first: https://krew.sigs.k8s.io/docs/user-guide/setup/install/"
    exit 1
fi

# Ensure krew is in PATH
export PATH="${KREW_ROOT:-$HOME/.krew}/bin:$PATH"

echo ""
echo "Step 1: Installing MinIO kubectl plugin..."
kubectl krew install minio 2>/dev/null || echo "MinIO plugin already installed"

echo ""
echo "Step 2: Initializing MinIO Operator..."
kubectl minio init --namespace minio-operator 2>/dev/null || echo "Operator may already be initialized"

echo ""
echo "Step 3: Waiting for Operator to be ready..."
kubectl wait --for=condition=available deployment/minio-operator -n minio-operator --timeout=120s 2>/dev/null || true
kubectl wait --for=condition=available deployment/console -n minio-operator --timeout=120s 2>/dev/null || true

echo ""
echo "Step 4: Creating configuration secret..."
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: minio-env-configuration
  namespace: default
type: Opaque
stringData:
  config.env: |
    export MINIO_ROOT_USER="minioadmin"
    export MINIO_ROOT_PASSWORD="minioadmin_password"
    export MINIO_PROMETHEUS_AUTH_TYPE="public"
    export MINIO_BROWSER="on"
EOF

echo ""
echo "Step 5: Deploying MinIO Tenant..."
kubectl apply -f kubernetes/minio-tenant.yml

echo ""
echo "Step 6: Waiting for Tenant to be ready..."
echo "This may take 2-3 minutes..."

# Wait for pods to be created
sleep 30

# Check tenant status
for i in {1..30}; do
    READY=$(kubectl get pods -l v1.min.io/tenant=minio --no-headers 2>/dev/null | grep -c "Running" || echo "0")
    TOTAL=$(kubectl get pods -l v1.min.io/tenant=minio --no-headers 2>/dev/null | wc -l | tr -d ' ' || echo "0")
    
    echo "MinIO pods: $READY/$TOTAL running..."
    
    if [ "$READY" -eq "4" ] && [ "$TOTAL" -eq "4" ]; then
        echo "✅ MinIO Tenant is ready!"
        break
    fi
    
    if [ "$i" -eq "30" ]; then
        echo "⚠️  Timeout waiting for MinIO. Check pods:"
        kubectl get pods -l v1.min.io/tenant=minio
        exit 1
    fi
    
    sleep 10
done

echo ""
echo "=========================================="
echo "MinIO Operator Setup Complete!"
echo "=========================================="
echo ""
echo "Access MinIO Console:"
echo "  kubectl minio proxy -n default"
echo ""
echo "Credentials:"
echo "  Username: minioadmin"
echo "  Password: minioadmin_password"
echo ""
echo "Check tenant status:"
echo "  kubectl get tenant minio"
echo "  kubectl get pods -l v1.min.io/tenant=minio"

