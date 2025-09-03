#!/bin/bash

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}☸️  Smart Home Monitoring - Kubernetes Deployment${NC}"
echo "=================================================="

# Configuration
DOCKERHUB_USERNAME="yourusername"  # Replace with your DockerHub username

# Check prerequisites
echo -e "${YELLOW}📋 Checking prerequisites...${NC}"

if ! command -v kubectl >/dev/null 2>&1; then
    echo -e "${RED}❌ kubectl is not installed${NC}"
    exit 1
fi

if ! command -v minikube >/dev/null 2>&1; then
    echo -e "${RED}❌ minikube is not installed${NC}"
    exit 1
fi

# Check if minikube is running
if ! minikube status >/dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  Minikube is not running. Starting...${NC}"
    minikube start --cpus=4 --memory=4096
    minikube addons enable ingress
fi

echo -e "${GREEN}✅ Prerequisites met${NC}"

# Update image references in deployment files
echo -e "${YELLOW}🔄 Updating image references...${NC}"

find k8s/deployments/ -name "*.yml" -exec sed -i "s|yourusername|${DOCKERHUB_USERNAME}|g" {} \;

# Apply Kubernetes manifests
echo -e "${YELLOW}⚙️ Deploying to Kubernetes...${NC}"

# Apply in order
kubectl apply -f k8s/namespace/
kubectl apply -f k8s/storage/
kubectl apply -f k8s/secrets/
kubectl apply -f k8s/configmaps/
kubectl apply -f k8s/deployments/
kubectl apply -f k8s/services/

# Wait for deployments to be ready
echo -e "${YELLOW}⏳ Waiting for deployments to be ready...${NC}"

kubectl wait --for=condition=available --timeout=300s deployment/influxdb -n smart-home
kubectl wait --for=condition=available --timeout=300s deployment/mosquitto -n smart-home
kubectl wait --for=condition=available --timeout=300s deployment/grafana -n smart-home
kubectl wait --for=condition=available --timeout=300s deployment/sensor-publisher -n smart-home
kubectl wait --for=condition=available --timeout=300s deployment/data-subscriber -n smart-home

# Get minikube IP
MINIKUBE_IP=$(minikube ip)

echo -e "\n${GREEN}🎉 Deployment completed successfully!${NC}"
echo "=============================================="

# Show status
kubectl get all -n smart-home

echo -e "\n${GREEN}📍 Access URLs:${NC}"
echo "🌐 Grafana: http://${MINIKUBE_IP}:30300 (admin/admin)"
echo "📊 InfluxDB: http://${MINIKUBE_IP}:30806"
echo "📡 MQTT Broker: ${MINIKUBE_IP}:31883"

echo -e "\n${GREEN}🔧 Management Commands:${NC}"
echo "View pods: kubectl get pods -n smart-home"
echo "View logs: kubectl logs -f deployment/[service] -n smart-home"
echo "Scale service: kubectl scale deployment/[service] --replicas=2 -n smart-home"
echo "Delete: kubectl delete namespace smart-home"

echo -e "\n${YELLOW}📝 Next Steps:${NC}"
echo "1. Access Grafana at http://${MINIKUBE_IP}:30300"
echo "2. InfluxDB datasource should be auto-configured"
echo "3. Create dashboards to visualize sensor data"
echo "4. Test MQTT: mosquitto_pub -h ${MINIKUBE_IP} -p 31883 -t test -m hello"
