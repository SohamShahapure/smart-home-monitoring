#!/bin/bash

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}🧹 Smart Home Monitoring Cleanup${NC}"
echo "================================="

# Function to ask for confirmation
confirm() {
    read -p "$(echo -e "${YELLOW}$1 [y/N]: ${NC}")" -n 1 -r
    echo
    [[ $REPLY =~ ^[Yy]$ ]]
}

# Stop and remove containers
if confirm "Stop and remove all containers?"; then
    echo -e "${YELLOW}🛑 Stopping containers...${NC}"
    docker compose down 2>/dev/null || true
    docker compose -f docker-compose.prod.yml down 2>/dev/null || true
fi

# Remove volumes (data will be lost!)
if confirm "Remove all volumes (THIS WILL DELETE ALL DATA)?"; then
    echo -e "${RED}🗑️  Removing volumes...${NC}"
    docker volume rm smart-home-monitoring_influxdb_data 2>/dev/null || true
    docker volume rm smart-home-monitoring_influxdb_config 2>/dev/null || true
    docker volume rm smart-home-monitoring_grafana_data 2>/dev/null || true
fi

# Remove local directories
if confirm "Remove local configuration directories?"; then
    echo -e "${YELLOW}📁 Removing directories...${NC}"
    rm -rf mosquitto/ grafana/ 2>/dev/null || true
fi

# Remove generated files
if confirm "Remove generated docker-compose files?"; then
    echo -e "${YELLOW}📄 Removing generated files...${NC}"
    rm -f docker-compose.prod.yml 2>/dev/null || true
fi

echo -e "${GREEN}✅ Cleanup completed!${NC}"
