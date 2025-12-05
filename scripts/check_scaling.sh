#!/bin/bash
# Quick script to check scaling status and metrics

set -e

echo "=== Kubernetes Scaling Status ==="
echo ""
echo "Current Pod Counts:"
kubectl get pods -o wide | grep -E "NAME|api-gateway|model-catalog|upload-download" || true

echo ""
echo "=== HPA Status ==="
kubectl get hpa

echo ""
echo "=== Resource Usage ==="
kubectl top pods 2>/dev/null || echo "Metrics server not available. Install with: kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml"

echo ""
echo "=== Service Endpoints ==="
echo "API Gateway:"
kubectl get svc api-gateway -o jsonpath='{.status.loadBalancer.ingress[0].ip}:{.spec.ports[0].port}' 2>/dev/null || echo "  ClusterIP: $(kubectl get svc api-gateway -o jsonpath='{.spec.clusterIP}:{.spec.ports[0].port}')"

echo ""
echo "Prometheus:"
kubectl get svc prometheus -o jsonpath='{.status.loadBalancer.ingress[0].ip}:{.spec.ports[0].port}' 2>/dev/null || echo "  ClusterIP: $(kubectl get svc prometheus -o jsonpath='{.spec.clusterIP}:{.spec.ports[0].port}')"

echo ""
echo "Grafana:"
kubectl get svc grafana -o jsonpath='{.status.loadBalancer.ingress[0].ip}:{.spec.ports[0].port}' 2>/dev/null || echo "  ClusterIP: $(kubectl get svc grafana -o jsonpath='{.spec.clusterIP}:{.spec.ports[0].port}')"

echo ""
echo "=== Quick Access Commands ==="
echo "# Port forward Prometheus:"
echo "  kubectl port-forward svc/prometheus 9090:9090"
echo ""
echo "# Port forward Grafana:"
echo "  kubectl port-forward svc/grafana 3000:3000"
echo ""
echo "# Port forward API Gateway:"
echo "  kubectl port-forward svc/api-gateway 8080:8080"
echo ""
echo "# Watch pods scaling:"
echo "  watch kubectl get pods"
echo ""
echo "# Watch HPA:"
echo "  watch kubectl get hpa"

