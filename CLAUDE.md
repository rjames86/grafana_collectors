# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a multi-service data collection platform that gathers environmental and home automation data from various sources and stores it in InfluxDB for visualization in Grafana. The system runs as a Docker Compose stack with the following key components:

- **Data Collectors**: Services that gather data from external APIs and IoT devices
- **InfluxDB**: Time-series database for storing metrics (both v1.8 and v2)
- **Grafana**: Visualization dashboard
- **MQTT**: Message broker for IoT device communication
- **API**: Flask-based service for data ingestion and notifications

## Architecture

### Data Flow
1. Collectors gather data from external sources (APIs, MQTT, etc.)
2. Data is written to InfluxDB databases
3. Grafana reads from InfluxDB for visualization
4. API service provides endpoints for data writes and Pushover notifications

### Service Dependencies
- All collectors depend on InfluxDB services
- MQTT client depends on Mosquitto broker
- API service provides centralized data ingestion and notification endpoints

## Development Commands

### Docker Compose Operations

The project is organized into multiple compose files for better maintainability:

- `docker-compose.yml`: Core infrastructure (InfluxDB, Grafana, MQTT)
- `docker-compose.collectors.yml`: Data collection services
- `docker-compose.override.yml`: Development overrides (auto-loaded)

```bash
# Start core infrastructure only
docker-compose up -d

# Start all services (infrastructure + collectors)
docker-compose -f docker-compose.yml -f docker-compose.collectors.yml up -d

# Start specific collector for development
docker-compose -f docker-compose.yml -f docker-compose.collectors.yml up -d ecobee-collector

# View logs for specific service
docker-compose logs -f <service_name>

# Rebuild specific service
docker-compose -f docker-compose.yml -f docker-compose.collectors.yml build <service_name>

# Stop all services
docker-compose -f docker-compose.yml -f docker-compose.collectors.yml down

# Development mode (uses override file automatically)
docker-compose -f docker-compose.yml -f docker-compose.collectors.yml up -d
```

### Python Services Testing
For Python services with tests (e.g., mqtt_client):
```bash
cd mqtt_client
python -m pytest tests/ -v
# or
python -m unittest tests.test_mqtt_handlers -v
```

### Go Services
For the Ecobee connector:
```bash
cd ecobee_influx_connector
go build -o ecobee_influx_connector .
go mod tidy  # Update dependencies
```

## Service Architecture

### Data Collectors
- **ecobee_influx_connector** (Go): Collects thermostat data from Ecobee API
- **solaredge** (Python): Gathers solar panel data from SolarEdge API
- **august** (Python): Monitors smart lock activity from August API
- **purpleair** (Python): Air quality data from PurpleAir sensors
- **weatherflow-collector** (External image): Weather station data
- **mqtt_client** (Python): Processes OpenSprinkler irrigation system messages

### Core Infrastructure
- **api** (Flask): Central API for data ingestion and Pushover notifications
- **grafana**: Custom Grafana setup with persistent data
- **influxdb** (v1.8): Primary time-series database
- **influxdb2**: Secondary InfluxDB v2 instance for newer collectors
- **mosquitto**: MQTT broker for IoT device communication

### External Services Integration
- **Pushover**: Mobile notifications for sprinkler and system alerts

## Configuration Management

### Environment Variables
Services are configured via environment variables defined in:
- `weatherflow_collector_v2.env`: Production environment variables
- `weatherflow_collector_v2.dev`: Development overrides
- Docker Compose environment sections

### Secrets Management
Sensitive data is stored in environment files (not committed):
- API keys for external services (Ecobee, SolarEdge, August, etc.)
- Database credentials
- Pushover tokens

## Data Storage

### InfluxDB Databases
- `weatherflow`: Weather station data
- `ecobee`: Thermostat and HVAC data
- `solar_edge`: Solar panel production/consumption
- `purpleair`: Air quality metrics

### Persistent Volumes
- `./data/grafana`: Grafana dashboards and configuration
- `./data/influxdb`: InfluxDB v1.8 data
- `./data/influxdb2`: InfluxDB v2 data
- `./data/mosquitto`: MQTT broker configuration
- `./data/*_collector`: Service-specific data and cache

## Key Files and Patterns

### Python Services Structure
- `main.py`: Entry point
- `requirements.txt`: Dependencies
- `Dockerfile`: Container configuration
- Service-specific modules for API integration

### Go Services Structure
- `main.go`: Entry point with configuration and main loop
- `go.mod`: Dependencies
- `ecobee/`: Package for Ecobee API integration

### MQTT Message Handling
The mqtt_client service uses a modular approach:
- `messages.py`: Defines topic handlers and message processing
- `main.py`: MQTT client setup and subscription management
- Handlers for different OpenSprinkler topics (stations, system, rain delay, etc.)

## Network Configuration

### Port Mappings
- 3000: Grafana web interface
- 8086: InfluxDB v1.8 API
- 8087: InfluxDB v2 API
- 5000: Flask API (production)
- 8080: Flask API (development override)
- 1883: MQTT broker
- 50222/UDP: WeatherFlow collector

### Service Communication
Services communicate via Docker network using container names:
- `influxdb`: InfluxDB v1.8
- `influxdb2`: InfluxDB v2
- `mosquitto`: MQTT broker
- `api`: Flask API service
- `grafana`: Grafana dashboard service