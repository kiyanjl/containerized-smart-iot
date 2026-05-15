# Smart Warehouse IoT System - Deployment Guide

## Overview

This containerized smart warehouse IoT system provides complete telemetry, control, visualization, and alerting capabilities for warehouse management with real-time monitoring, rule-based automation, and Telegram notifications.

## System Architecture

### Components

1. **MQTT Broker** (Eclipse Mosquitto)
   - Central message hub for all telemetry and commands
   - Ports: 1883 (MQTT), 9001 (WebSocket)
   - Provides pub/sub communication for all services

2. **Catalog Service** (Port 8080)
   - Maintains warehouse configurations and rules
   - Stores 3 warehouse types: cold, standard, hazard
   - Endpoints: `/assets`, `/health`, `/broker`, `/port`
   - Framework: CherryPy HTTP server

3. **Sensor Simulator**
   - Publishes realistic telemetry data for warehouses
   - Simulates anomalies (temperature spikes, humidity floods, etc.)
   - Topics: `assets/{warehouse_id}/sensors`, `assets/{warehouse_id}/events`, `assets/{warehouse_id}/heartbeat`

4. **Smart Controller** (Port 8001)
   - Evaluates rules and publishes actuator commands
   - Monitors device health with heartbeat timeouts
   - Stores telemetry and events to InfluxDB
   - REST API for dashboard integration
   - Endpoints: `/status`, `/health`, `/events`, `/state_history`, `/commands`, `/manual_command`

5. **Actuator Service**
   - Simulates warehouse equipment (fans, dehumidifiers, etc.)
   - Confirms command execution with confirmations
   - Handles retry logic for failed commands
   - Implements edge safety based on sensor data

6. **Alert Service** (Port 5002)
   - Monitors critical and overload states
   - Sends Telegram notifications for alerts
   - Provides HTTP API for alert status and history
   - Bot commands: `/start`, `/status`, `/alerts`, `/subscribe`, `/unsubscribe`, `/help`

7. **InfluxDB** (Port 8086)
   - Time-series database for all telemetry and events
   - Bucket: `warehouse_metrics`
   - Organization: `smart-iot`
   - Stores measurements: warehouse (telemetry), warehouse_event (events), device_health

8. **Grafana** (Port 3100 by default)
   - Visualization dashboard with live data and annotations
   - Dashboard: "Warehouse Metrics" with connectivity and actuation annotations
   - Anonymous viewer access enabled
   - Auto-provisions InfluxDB datasource

9. **Streamlit Dashboard** (Port 18501)
   - Web-based monitoring and manual control interface.
   - Configurable via `DASHBOARD_PORT` in `.env`.
   - Note: Port 8501 is often reserved on Windows.

## Setup & Deployment

### Prerequisites

- Docker and Docker Compose installed
- PowerShell 5.1+ (for Windows startup script)
- .env file with required environment variables

### Environment Configuration

Create or update `.env` file:

```env
# InfluxDB Configuration
INFLUXDB_URL=http://influxdb:8086
INFLUXDB_TOKEN=NnVSDg3Jxb8K1hpRyu8eMNCXf6Uix-T04fEti98mgN15sIjqn-qEWY8o72MLJwvVEZG1qG7ZD4cOlzL60Zyacg==
INFLUXDB_ORG=smart-iot
INFLUXDB_BUCKET=warehouse_metrics

# MQTT Configuration
MQTT_BROKER=mqtt-broker
MQTT_PORT=1883

# Telegram Bot Configuration (REQUIRED for alerts)
TELEGRAM_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here

# Grafana Configuration
GRAFANA_PORT=3100
GRAFANA_PUBLIC_URL=http://localhost:3100

# Service URLs
CATALOG_SERVICE_URL=http://catalog-service:8080
CONTROLLER_SERVICE_URL=http://controller-service:8001
ALERT_SERVICE_URL=http://alert-service:5002
DASHBOARD_PORT=8501

# Logging & Settings
LOG_LEVEL=INFO
ALERT_RATE_LIMIT=30
DASHBOARD_AUTO_REFRESH=True
DASHBOARD_REFRESH_INTERVAL=5
```

### Obtaining Telegram Credentials

1. Create a bot with [@BotFather](https://t.me/botfather) on Telegram
2. Get your Chat ID by messaging [@userinfobot](https://t.me/userinfobot)
3. Add both to your `.env` file

### Starting the System

#### On Windows (PowerShell)

```powershell
cd scripts
.\start_stack.ps1
```

This script automatically:

- Detects available ports for Grafana
- Creates/updates .env with selected port
- Starts all Docker Compose services

#### On Linux/macOS

```bash
docker compose up -d
```

### Verification

Check all services are running:

```bash
docker compose ps
```

Check service health:

```bash
curl http://localhost:8080/health      # Catalog
curl http://localhost:8001/health      # Controller
curl http://localhost:5002/status      # Alert Service
curl http://localhost:3100/api/health  # Grafana
curl http://localhost:7501/_stcore/health  # Dashboard
```

## Accessing the System

| Component               | URL                          | Purpose                   |
| ----------------------- | ---------------------------- | ------------------------- |
| **Streamlit Dashboard** | http://localhost:7501        | Main operations dashboard |
| **Grafana**             | http://localhost:3100        | Time-series visualization |
| **InfluxDB**            | http://localhost:8086        | Data query interface      |
| **Catalog API**         | http://localhost:8080/assets | Warehouse configuration   |
| **Controller API**      | http://localhost:8001        | System status & control   |
| **Alert Service**       | http://localhost:5002        | Alerts & Telegram status  |

## Dashboard Features

### KPI Cards

- **Assets**: Total registered warehouses
- **Active Telemetry**: Currently reporting warehouses
- **Recent Events**: Total event count from InfluxDB
- **Pending Commands**: Awaiting actuator confirmations

### Warehouse Status Cards

- Real-time temperature, humidity, stock levels
- Door status and last sample time
- Color-coded state indicators (OK, WARN, HOT, LOAD, ALERT, OFF)

### State Timeline

- 6-hour history of warehouse states
- Interactive chart with hover details
- Anomaly and state transitions visible

### Event Console

- Live event feed (80 most recent)
- Color-coded by event type
- Shows warehouse, timestamp, and source

### Quick Actions

- Manual actuator commands (Fan ON, Dehumidifier ON, etc.)
- Dropdown to select warehouse
- Real-time success/error feedback
- Pending confirmation counter

### System Health

- Service health status
- Warehouse asset count
- Alert statistics
- Telegram configuration status

## Data Flow

1. **Sensor Simulator** → MQTT: Publishes telemetry to `assets/{id}/sensors`
2. **Controller** subscribes and evaluates rules
3. **Controller** → InfluxDB: Stores measurements and events
4. **Controller** → MQTT: Publishes actuator commands to `assets/{id}/actuator`
5. **Actuator Service** executes actions and publishes confirmations
6. **Alert Service** monitors states and sends Telegram notifications
7. **Streamlit Dashboard** fetches data from REST APIs
8. **Grafana** queries InfluxDB for visualization

## Rule Engine

The Smart Controller evaluates warehouse rules to determine state and actions:

```python
States:      NORMAL, WARNING, CRITICAL, OVERLOAD, ANOMALY, OFFLINE, MANUAL
Actions:     emergency_shutdown, fan, dehumidifier, pause_deliveries, restock_alert
Triggers:
- Anomaly: Temperature out of range or high humidity
- Critical: High temperature
- Overload: Stock level > 90%
- Warning: Slightly elevated temperature or low stock
```

## Telegram Bot Commands

Once subscribed via `/start`:

- `/status`: System status summary
- `/alerts`: Last 10 alerts
- `/subscribe`: Enable alerts
- `/unsubscribe`: Disable alerts
- `/help`: Command list

## Troubleshooting

### Dashboard Not Loading

- Check Streamlit port 8501 is accessible
- Verify `GRAFANA_PUBLIC_URL` environment variable is set correctly
- Check docker logs: `docker compose logs dashboard`

### No Telemetry Data

- Ensure Sensor Simulator is running: `docker compose logs sensor-simulator`
- Check MQTT broker connectivity: `docker compose logs mqtt-broker`
- Verify catalog has assets: `curl http://localhost:8080/assets`

### Grafana Dashboard Empty

- Verify InfluxDB is healthy: `docker compose logs influxdb`
- Check datasource configuration in Grafana UI
- Run query in Grafana's query builder to debug

### Telegram Not Sending

- Verify `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` in .env
- Check Alert Service logs: `docker compose logs alert-service`
- Look for "Telegram bot verified" in logs

### Events Not Appearing in Dashboard

- Verify Controller service health
- Check InfluxDB write permissions
- Look for "Stored measurement to InfluxDB" in controller logs

## Performance Considerations

- **InfluxDB**: Stores ~2 data points per warehouse per 2 seconds
- **Dashboard Refresh**: Default 5 seconds, adjustable via toggle
- **MQTT QoS**: Set to 1-2 for reliability
- **Event Retention**: 6-hour history in dashboard queries
- **Confirmation Timeout**: 30 seconds for actuator commands

## Security Notes

- InfluxDB token hardcoded (for development)
- Grafana anonymous viewer enabled (restrict in production)
- MQTT broker has no authentication (add in production)
- Change default Telegram credentials before deployment

## Cleanup

To stop and remove all services:

```bash
docker compose down -v
```

To view logs:

```bash
docker compose logs -f [service_name]
```

## Testing

Run unit tests for rule engine:

```bash
python tests/test_controller_rules.py
```

## Support

For issues or questions, check:

1. Service logs: `docker compose logs`
2. System health endpoints (see Verification section)
3. InfluxDB queries for data presence
4. Network connectivity between services

---

**Status**: Production-ready | **Last Updated**: 2024
