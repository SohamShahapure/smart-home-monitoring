#!/bin/bash

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
PROJECT_NAME="smart-home-monitoring"
DOCKERHUB_USERNAME="yourusername"  # Replace with your DockerHub username

echo -e "${GREEN}🏠 Smart Home Monitoring Deployment Script${NC}"
echo "=============================================="

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check prerequisites
echo -e "${YELLOW}📋 Checking prerequisites...${NC}"

if ! command_exists docker; then
    echo -e "${RED}❌ Docker is not installed. Please install Docker first.${NC}"
    exit 1
fi

if ! command_exists docker compose; then
    echo -e "${RED}❌ Docker Compose is not installed. Please install Docker Compose first.${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Prerequisites met${NC}"

# Create directory structure
echo -e "${YELLOW}📁 Setting up directory structure...${NC}"

mkdir -p mosquitto/{config,data,log}
mkdir -p grafana/provisioning/{dashboards,datasources}

# Create mosquitto configuration
echo -e "${YELLOW}⚙️ Creating Mosquitto configuration...${NC}"

cat > mosquitto/config/mosquitto.conf << 'EOF'
# Mosquitto Configuration
persistence true
persistence_location /mosquitto/data/

# Logging
log_dest file /mosquitto/log/mosquitto.log
log_dest stdout
log_type error
log_type warning
log_type notice
log_type information

# Listeners
listener 1883 0.0.0.0
protocol mqtt

listener 9001 0.0.0.0
protocol websockets

# Security (allow anonymous for development)
allow_anonymous true

# Connection settings
max_connections 1000
EOF

# Create Grafana datasource
echo -e "${YELLOW}📊 Creating Grafana datasource configuration...${NC}"

cat > grafana/provisioning/datasources/influxdb.yml << 'EOF'
apiVersion: 1
datasources:
  - name: InfluxDB
    type: influxdb
    access: proxy
    url: http://influxdb:8086
    jsonData:
      version: Flux
      organization: smarthome
      defaultBucket: sensor_data
      tlsSkipVerify: true
    secureJsonData:
      token: my-super-secret-auth-token
EOF

# Set permissions
echo -e "${YELLOW}🔐 Setting permissions...${NC}"
chmod -R 755 mosquitto/ || true
chmod -R 755 grafana/ || true

# Update docker-compose to use registry images if available
echo -e "${YELLOW}🐳 Preparing Docker Compose...${NC}"

cat > docker-compose.prod.yml << EOF
services:
  mosquitto:
    image: eclipse-mosquitto:2.0
    container_name: mqtt-broker
    ports:
      - "1883:1883"
      - "9001:9001"
    volumes:
      - ./mosquitto/config:/mosquitto/config
      - ./mosquitto/data:/mosquitto/data
      - ./mosquitto/log:/mosquitto/log
    restart: unless-stopped
    networks:
      - smart-home
    healthcheck:
      test: ["CMD-SHELL", "timeout 5 mosquitto_pub -h localhost -t health/check -m 'health' || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

  influxdb:
    image: influxdb:2.7
    container_name: influxdb
    ports:
      - "8086:8086"
    environment:
      - DOCKER_INFLUXDB_INIT_MODE=setup
      - DOCKER_INFLUXDB_INIT_USERNAME=admin
      - DOCKER_INFLUXDB_INIT_PASSWORD=adminpassword
      - DOCKER_INFLUXDB_INIT_ORG=smarthome
      - DOCKER_INFLUXDB_INIT_BUCKET=sensor_data
      - DOCKER_INFLUXDB_INIT_RETENTION=1w
      - DOCKER_INFLUXDB_INIT_ADMIN_TOKEN=my-super-secret-auth-token
    volumes:
      - influxdb_data:/var/lib/influxdb2
      - influxdb_config:/etc/influxdb2
    restart: unless-stopped
    networks:
      - smart-home
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost:8086/health || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s

  grafana:
    image: grafana/grafana:latest
    container_name: grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
    volumes:
      - grafana_data:/var/lib/grafana
      - ./grafana/provisioning:/etc/grafana/provisioning
    restart: unless-stopped
    networks:
      - smart-home
    depends_on:
      influxdb:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost:3000/api/health || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s

  sensor-publisher:
    image: ${DOCKERHUB_USERNAME}/smart-home-publisher:latest
    container_name: sensor-publisher
    environment:
      - MQTT_BROKER=mosquitto
      - MQTT_PORT=1883
    restart: unless-stopped
    networks:
      - smart-home
    depends_on:
      mosquitto:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost:8080/health || exit 1"]
      interval: 60s
      timeout: 15s
      retries: 3
      start_period: 120s

  data-subscriber:
    image: ${DOCKERHUB_USERNAME}/smart-home-subscriber:latest
    container_name: data-subscriber
    environment:
      - MQTT_BROKER=mosquitto
      - MQTT_PORT=1883
      - INFLUXDB_URL=http://influxdb:8086
      - INFLUXDB_TOKEN=my-super-secret-auth-token
      - INFLUXDB_ORG=smarthome
      - INFLUXDB_BUCKET=sensor_data
    restart: unless-stopped
    networks:
      - smart-home
    depends_on:
      mosquitto:
        condition: service_healthy
      influxdb:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost:8080/health || exit 1"]
      interval: 60s
      timeout: 15s
      retries: 3
      start_period: 120s

volumes:
  influxdb_data:
  influxdb_config:
  grafana_data:

networks:
  smart-home:
    driver: bridge
EOF

# Deploy the stack
echo -e "${YELLOW}🚀 Deploying the stack...${NC}"

# Check if we should use production images or build locally
if docker pull "${DOCKERHUB_USERNAME}/smart-home-publisher:latest" >/dev/null 2>&1; then
    echo -e "${GREEN}✅ Using production images from DockerHub${NC}"
    export DOCKERHUB_USERNAME="${DOCKERHUB_USERNAME}"
    docker compose -f docker-compose.prod.yml down 2>/dev/null || true
    docker compose -f docker-compose.prod.yml up -d
    COMPOSE_FILE="docker-compose.prod.yml"
else
    echo -e "${YELLOW}⚠️  Production images not available, using local build${NC}"
    docker compose down 2>/dev/null || true
    docker compose up -d --build
    COMPOSE_FILE="docker-compose.yml"
fi

# Wait for services to be ready
echo -e "${YELLOW}⏳ Waiting for services to start...${NC}"
sleep 30

# Health check
echo -e "${YELLOW}🏥 Performing health checks...${NC}"

check_service() {
    local service=$1
    local url=$2
    local max_attempts=30
    local attempt=1
    
    while [ $attempt -le $max_attempts ]; do
        if curl -f "$url" >/dev/null 2>&1; then
            echo -e "${GREEN}✅ $service is healthy${NC}"
            return 0
        fi
        echo -e "${YELLOW}⏳ Waiting for $service... (attempt $attempt/$max_attempts)${NC}"
        sleep 5
        attempt=$((attempt + 1))
    done
    
    echo -e "${RED}❌ $service failed to start${NC}"
    return 1
}

# Check services
check_service "Grafana" "http://localhost:3000/api/health"
check_service "InfluxDB" "http://localhost:8086/health"

# Show final status
echo -e "\n${GREEN}🎉 Deployment completed!${NC}"
echo "=============================================="

if [ "$COMPOSE_FILE" = "docker-compose.prod.yml" ]; then
    docker compose -f docker-compose.prod.yml ps
else
    docker compose ps
fi

echo -e "\n${GREEN}📍 Access URLs:${NC}"
echo "🌐 Grafana: http://localhost:3000 (admin/admin)"
echo "📊 InfluxDB: http://localhost:8086"
echo "📡 MQTT Broker: localhost:1883"

echo -e "\n${GREEN}🔧 Management Commands:${NC}"
echo "View logs: docker compose logs -f"
echo "Stop: docker compose down"
echo "Restart: docker compose restart [service]"

echo -e "\n${YELLOW}📝 Next Steps:${NC}"
echo "1. Login to Grafana at http://localhost:3000"
echo "2. InfluxDB datasource should be auto-configured"
echo "3. Create dashboards to visualize sensor data"
echo "4. Monitor MQTT messages: docker compose logs -f sensor-publisher"
EOF

chmod +x deploy.sh
