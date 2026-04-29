# Smart Warehouse IoT System Explanation

The most important files in this project are `docker-compose.yml`, `catalog-service/catalog.json`, `controller-service/controller_service.py`, `controller-service/rule_engine.py`, `sensor-simulator/sensor_simulator.py`, `actuator-service/actuator_service.py`, `alert-service/alert_service.py`, `dashboard/dashboard.py`, `grafana/provisioning/datasources/datasource.yml`, `grafana/provisioning/dashboards/warehouse_dashboard.json`, and `scripts/smoke_test.py`. If you understand those files, you understand the whole system: Docker starts the services, the catalog defines warehouses, the simulator creates sensor data, the controller decides, the actuator responds, InfluxDB stores history, Grafana and Streamlit visualize it, Telegram alerts humans, and the smoke test proves the full loop works.

This project is a containerized smart warehouse IoT platform. It simulates warehouse sensors, sends telemetry through MQTT, evaluates warehouse safety rules, writes data to InfluxDB, controls actuators, sends Telegram alerts, and displays live and historical data in Streamlit and Grafana dashboards.

The main closed-loop idea is:

```text
Sense -> Publish -> Decide -> Act -> Confirm -> Store -> Visualize -> Alert
```

The system is not a monolith. It is built from separate microservices, and each service has one clear responsibility.

## 1. High-Level Architecture

```text
Configuration Layer
  Catalog Service
  catalog.json

Device / Simulation Layer
  Sensor Simulator
  Actuator Service

Messaging Layer
  MQTT Broker

Intelligence Layer
  Smart Controller
  Rule Engine

Persistence Layer
  InfluxDB

Human Interaction / Observability Layer
  Streamlit Dashboard
  Grafana
  Alert Service / Telegram Bot

Verification Layer
  Unit Tests
  Smoke Test
```

The most important architectural point is that real-time IoT communication uses MQTT, while human/UI/API communication uses REST.

MQTT is used for asynchronous event messages:

```text
sensor data
heartbeat
anomaly events
actuator commands
actuator confirmations
catalog update notifications
```

REST is used when one component asks another for a specific resource:

```text
GET /assets
GET /status
GET /events
GET /state_history
POST /manual_command
```

In simple words:

- MQTT is for live events that happen continuously.
- REST is for request/response API calls.
- `GET` means read information.
- `POST` means send data or request a change/action.

### Important Architecture Corrections for the Diagram

These points answer the common architecture questions about arrows and communication direction:

1. `Controller -> Streamlit` is not a push connection.

   The controller does not push directly into Streamlit through WebSocket or SSE. Streamlit refreshes and calls the controller REST API:

   ```text
   Streamlit -> Controller
   GET /status
   GET /events
   GET /state_history
   GET /commands
   GET /health
   POST /manual_command
   ```

   The controller writes telemetry/events to InfluxDB, and also queries InfluxDB when answering `/events` and `/state_history`.

2. `Catalog -> Streamlit` is also not a push connection.

   Streamlit calls the Catalog REST API when it needs the warehouse list:

   ```text
   Streamlit -> Catalog
   GET /assets
   ```

   The correct arrow direction in the architecture diagram is from Streamlit to Catalog for REST. Catalog only publishes MQTT for configuration updates:

   ```text
   Catalog -> MQTT Broker
   Topic: catalog/config_updated
   Subscriber: Controller Service
   ```

3. Alert Service source is the MQTT broker.

   Real code in `alert-service/alert_service.py` subscribes to:

   ```text
   assets/#
   system/device_status
   ```

   In practice, the alert service listens to warehouse sensor messages and warehouse event messages. It sends Telegram alerts for safety states and for manual command notifications.

4. Controller MQTT subscriptions are explicit.

   Real code in `controller-service/controller_service.py` subscribes to:

   ```text
   assets/+/sensors
   warehouse/+/sensors
   catalog/config_updated
   assets/+/events
   assets/+/heartbeat
   ```

5. There is no `Controller -> Catalog` feedback path by design.

   Catalog is the source of truth for warehouse configuration. The controller should not write runtime state back into the catalog because that would mix configuration with live operations. Runtime state goes to InfluxDB and controller REST endpoints instead.

6. `Actuator -> Broker` re-enters the system because this is closed-loop control.

   The actuator publishes confirmation events to:

   ```text
   assets/{asset_id}/events
   ```

   The controller receives those confirmations, removes pending commands, stores proof in InfluxDB, and the dashboard can show what happened. This proves:

   ```text
   command sent -> actuator processed -> confirmation received
   ```

## 2. Complete Runtime Flow

### Step 1: Catalog Defines Warehouses

File:

```text
catalog-service/catalog.json
```

This file defines the real warehouses known by the system.

Current warehouses:

```text
warehouse_cold
warehouse_standard
warehouse_hazard
```

Each warehouse has:

```json
{
  "asset_id": "warehouse_standard",
  "name": "Standard Warehouse",
  "type": "standard",
  "location": "Building A, Floor 2",
  "capacity": 100,
  "owner": "Jane Doe",
  "contact": "jane.doe@company.com",
  "mqtt_sensor_topic": "assets/warehouse_standard/sensors",
  "mqtt_actuator_topic": "assets/warehouse_standard/actuator",
  "rules": {
    "temp_warning": 30,
    "temp_critical": 40,
    "stock_low": 20,
    "stock_overload": 90,
    "temp_anomaly_high": 46,
    "temp_anomaly_low": -5,
    "humidity_anomaly_high": 96
  }
}
```

Why this matters:

- The simulator reads this file through the Catalog API to know which warehouses to simulate.
- The controller reads the rules to decide whether a warehouse is `NORMAL`, `WARNING`, `CRITICAL`, `OVERLOAD`, or `ANOMALY`.
- MQTT topics come from this configuration.

### Step 2: Catalog Service Exposes the Warehouse Configuration

File:

```text
catalog-service/catalog_service.py
```

Important real code locations:

- `class CatalogService` around line 15.
- `_validate_asset_payload()` around line 57.
- `GET()` around line 74.
- `POST()` around line 100.
- `add_asset` logic around line 102.
- `delete_asset` logic around line 126.
- `update_rules` logic around line 153.
- MQTT `catalog/config_updated` publish calls around lines 112, 139, and 168.

The Catalog Service is a REST API built with CherryPy. It reads and writes `catalog.json`.

REST endpoints:

```text
GET  /health
GET  /assets
GET  /assets/{asset_id}
GET  /broker
GET  /port
POST /add_asset
POST /delete_asset
POST /update_rules
```

Who calls it:

```text
Sensor Simulator -> GET /assets
Sensor Simulator -> GET /broker
Sensor Simulator -> GET /port
Controller -> GET /assets
Dashboard -> GET /assets
```

When a warehouse or rule changes, Catalog Service also publishes an MQTT event:

```text
Topic: catalog/config_updated
Publisher: Catalog Service
Subscribers: Controller Service
Purpose: tell controller that rules/assets changed
```

That means the system can update rules without restarting the controller.

### Step 3: Sensor Simulator Publishes Live Data

File:

```text
sensor-simulator/sensor_simulator.py
```

Important real code locations:

- `NORMAL_PROFILES` around line 15.
- `ANOMALY_PROFILES` around line 39.
- `get_assets()` around line 62.
- `generate_sensor_data()` around line 112.
- `apply_anomaly_profile()` around line 124.
- Sensor `client.publish(...)` around line 183.
- `ANOMALY_DETECTED` event around line 189.
- Heartbeat publish around lines 204-213.

The simulator is the fake physical world. It continuously creates:

```text
temperature
humidity
stock
door_open
timestamp
```

It publishes sensor data every 2 seconds.

MQTT publish:

```text
Topic: assets/{warehouse_id}/sensors
Publisher: Sensor Simulator
Subscribers: Controller Service, Actuator Service, Alert Service
```

Example MQTT payload:

```json
{
  "warehouse_id": "warehouse_cold",
  "temperature": 5.1,
  "humidity": 67.0,
  "stock": 51,
  "door_open": 1,
  "timestamp": 1777039834.59
}
```

It also sends heartbeat messages:

```text
Topic: assets/{warehouse_id}/heartbeat
Publisher: Sensor Simulator
Subscriber: Controller Service
Purpose: prove that the device is still alive
```

It also sends anomaly events:

```text
Topic: assets/{warehouse_id}/events
Publisher: Sensor Simulator
Subscribers: Controller Service, Alert Service
Purpose: announce abnormal simulated behavior
```

Example anomaly event:

```json
{
  "warehouse_id": "warehouse_standard",
  "event": "ANOMALY_DETECTED",
  "anomaly_type": "HEAT_SPIKE",
  "source": "sensor_simulator",
  "timestamp": 1777039834.59
}
```

### Step 4: MQTT Broker Distributes Events

File:

```text
mqtt-broker/mosquitto.conf
```

Docker service:

```text
mqtt-broker
```

Host ports from `docker-compose.yml`:

```text
1883:1883
9001:9001
```

MQTT Broker is the message bus. It does not calculate or store anything. It receives published messages and sends them to every subscriber.

Why MQTT instead of REST for live sensor data:

```text
MQTT allows asynchronous, low-latency, decoupled communication.
```

This means:

- Sensor does not need to know controller internals.
- Controller and alert service can both receive the same sensor message.
- Actuator can listen for commands separately.
- Services are easier to replace or scale.

Main MQTT topic map:

```text
assets/{id}/sensors
  Publisher: Sensor Simulator
  Subscribers: Controller, Actuator, Alert Service

assets/{id}/heartbeat
  Publisher: Sensor Simulator
  Subscriber: Controller

assets/{id}/events
  Publishers: Sensor Simulator, Controller, Actuator
  Subscribers: Controller, Actuator, Alert Service

assets/{id}/actuator
  Publisher: Controller
  Subscriber: Actuator

catalog/config_updated
  Publisher: Catalog Service
  Subscriber: Controller
```

### Step 5: Controller Receives Sensor Data and Decides

Files:

```text
controller-service/controller_service.py
controller-service/rule_engine.py
controller-service/rules_cache.json
```

The controller is the brain of the system.

Important real code locations in `controller_service.py`:

- `class SmartController` around line 34.
- `on_message()` around line 129.
- `publish_command()` around line 255.
- `store_influx()` around line 288.
- `store_event_influx()` around line 320.
- REST `events()` around line 571.
- REST `state_history()` around line 613.
- REST `manual_command()` around line 681.
- CherryPy startup around line 736.

The controller subscribes to these MQTT topics:

```text
assets/+/sensors
assets/+/events
assets/+/heartbeat
catalog/config_updated
```

The `+` means wildcard. For example:

```text
assets/+/sensors
```

matches:

```text
assets/warehouse_cold/sensors
assets/warehouse_standard/sensors
assets/warehouse_hazard/sensors
```

When a sensor message arrives, `on_message()` does this:

1. Decode the JSON MQTT payload.
2. Read `warehouse_id`.
3. Load the correct rules from memory/cache/catalog.
4. Call the rule engine.
5. Publish an actuator command.
6. Store latest state in `self.state`.
7. Write telemetry to InfluxDB.

### Step 6: Rule Engine Classifies the Warehouse State

File:

```text
controller-service/rule_engine.py
```

Important real code locations:

- `evaluate_rules(data, rules=None)` around line 4.
- `ANOMALY` high/low temperature logic around line 32.
- `ANOMALY` high humidity logic around line 36.
- `CRITICAL` logic around line 39.
- `OVERLOAD` logic around line 42.
- `WARNING` logic around line 45.
- low-stock warning logic around line 49.
- return object around line 52.

The function:

```python
evaluate_rules(data, rules)
```

takes sensor data and rules, then returns a decision like:

```json
{
  "state": "CRITICAL",
  "action": {
    "fan": "ON"
  },
  "timestamp": 1777046073.82
}
```

Possible states:

```text
NORMAL
WARNING
CRITICAL
OVERLOAD
ANOMALY
MANUAL
```

Priority order:

```text
ANOMALY -> CRITICAL -> OVERLOAD -> WARNING -> NORMAL
```

This is important because an anomaly is more serious than a normal warning.

Examples:

- Very high temperature or very low temperature gives `ANOMALY`.
- High humidity gives `ANOMALY`.
- Critical temperature gives `CRITICAL`.
- Too much stock gives `OVERLOAD`.
- Warning temperature gives `WARNING`.
- Low stock adds a `restock_alert`.

### Step 7: Controller Sends Commands to Actuator

File:

```text
controller-service/controller_service.py
```

Important code:

```text
publish_command() around line 255
```

The controller publishes to:

```text
Topic: assets/{warehouse_id}/actuator
Publisher: Controller Service
Subscriber: Actuator Service
```

Example command:

```json
{
  "command_id": "1777046073.8243685",
  "action": {
    "state": "MANUAL",
    "action": {
      "fan": "ON"
    },
    "timestamp": 1777046073.82
  }
}
```

The controller also tracks this command in:

```text
self.pending_confirmations
```

That is why the dashboard has `Pending Commands`.

`Pending Commands` means:

```text
The controller sent an actuator command, but the actuator has not confirmed it yet.
```

Normal behavior is `0`, because confirmations usually return quickly.

### Step 8: Actuator Executes and Confirms

File:

```text
actuator-service/actuator_service.py
```

Important real code locations:

- `received_commands` around line 31.
- `pending_commands` around line 32.
- `on_connect()` around line 34.
- `on_message()` around line 49.
- edge safety fan logic around line 63.
- informational event ignore logic around line 69.
- duplicate command prevention around line 92.
- `execute_actions()` around line 132.
- retry logic around line 155.
- retry thread starts around line 188.

The actuator subscribes to:

```text
assets/+/actuator
assets/+/sensors
assets/+/events
```

Main purpose:

```text
Receive command -> execute simulated action -> publish confirmation
```

Supported simulated actions:

```text
fan: ON
dehumidifier: ON
restock_alert: true
pause_deliveries: true
emergency_shutdown: true
```

Confirmation publish:

```text
Topic: assets/{warehouse_id}/events
Publisher: Actuator Service
Subscribers: Controller Service, Alert Service
```

Example confirmation:

```json
{
  "warehouse_id": "warehouse_standard",
  "command_id": "1777046073.8243685",
  "status": "SUCCESS",
  "timestamp": 1777046073.82
}
```

This creates closed-loop control:

```text
Controller command -> Actuator action -> Actuator confirmation -> Controller verification
```

The system does not assume commands succeed. It verifies them.

### Step 9: Controller Stores Telemetry and Events in InfluxDB

File:

```text
controller-service/controller_service.py
```

Important code:

- `store_influx()` around line 288.
- `store_event_influx()` around line 320.

Database:

```text
InfluxDB
Bucket: warehouse_metrics
Organization: smart-iot
```

InfluxDB is used because IoT data is time-series data. Every sensor reading and event has a timestamp.

Measurements:

```text
warehouse
warehouse_event
device_health
```

`warehouse` stores:

```text
temperature
humidity
stock
state_code
warehouse_id tag
state tag
timestamp
```

`warehouse_event` stores:

```text
event
anomaly_type
source
command_id
status
warehouse_id
timestamp
```

Examples of events:

```text
ANOMALY_DETECTED
ACTUATOR_COMMAND_DISPATCHED
ACTUATOR_CONFIRMATION
MANUAL_COMMAND_REQUESTED
DEVICE_ONLINE
DEVICE_OFFLINE
```

`device_health` stores:

```text
online
last_seen_age_sec
warehouse_id
status
timestamp
```

Why this matters:

```text
We can reconstruct the full history:
sensor reading -> decision -> command sent -> actuator confirmed.
```

### Step 10: Alert Service Sends Telegram Notifications

File:

```text
alert-service/alert_service.py
```

Important real code locations:

- `send_telegram()` around line 57.
- `broadcast()` around line 68.
- `handle_event()` around line 75.
- manual command message format around line 86.
- `telegram_poll()` around line 104.
- `handle_sensor()` around line 166.
- warehouse alert message format around line 202.
- `on_connect()` around line 226.
- `on_message()` around line 232.

The alert service subscribes to MQTT:

```text
assets/+/sensors
assets/+/events
system/device_status
```

It sends Telegram alerts for important situations:

```text
CRITICAL
OVERLOAD
ANOMALY
MANUAL_COMMAND_REQUESTED
```

Telegram message is sent by:

```text
send_telegram()
```

It calls the Telegram HTTP API:

```text
https://api.telegram.org/bot<TOKEN>/sendMessage
```

REST endpoints exposed by Alert Service:

```text
GET /health
GET /alerts
GET /status
```

Dashboard uses:

```text
GET /alerts
GET /status
```

Telegram bot commands:

```text
/start
/status
/alerts
/subscribe
/unsubscribe
/help
```

Important note:

```text
Core safety does not need the internet.
Only Telegram delivery needs internet.
```

### Step 11: Dashboard Shows Live Status and Lets User Send Commands

File:

```text
dashboard/dashboard.py
```

Important real code locations:

- environment URLs around line 11.
- `STATE_STYLE` around line 18.
- `get_json()` around line 201.
- `get_api_result()` around line 210.
- `kpi_card()` around line 250.
- `status_card()` around line 263.
- `event_console()` around line 299.
- `state_history_chart()` around line 328.
- `GET /assets` call around line 367.
- `GET /status` around line 375.
- `GET /health` around line 376.
- `GET /commands` around line 377.
- `GET /events` around line 378.
- `GET /state_history` around line 379.
- `GET /alerts` around line 380.
- `GET /status` from alert service around line 381.
- KPI cards around lines 395-401.
- warehouse status cards around line 414.
- event console around line 426.
- `POST /manual_command` around line 445.
- Grafana iframe around line 476.
- auto-refresh around line 478.

Dashboard communicates through REST, not MQTT.

Dashboard REST calls:

```text
Dashboard -> Catalog Service
GET /assets

Dashboard -> Controller Service
GET /status
GET /health
GET /commands
GET /events
GET /state_history
POST /manual_command

Dashboard -> Alert Service
GET /alerts
GET /status
```

Dashboard sections:

```text
KPI cards
Warehouse status cards
State timeline
Live event log
Quick actions
Pending actuator confirmations
Recent alerts
System health
Grafana preview
```

#### How Dashboard Quick Actions Work

Quick action buttons:

```text
Fan ON
Dehumidifier ON
Pause Deliveries
Restock Alert
Emergency Shutdown
```

When you click a quick action, no source-code file changes.

Instead, this runtime flow happens:

```text
Dashboard button
  -> POST /manual_command to Controller
  -> Controller publishes MQTT actuator command
  -> Actuator receives command
  -> Actuator executes simulated action
  -> Actuator publishes confirmation event
  -> Controller stores confirmation in InfluxDB
  -> Controller publishes/stores MANUAL_COMMAND_REQUESTED
  -> Alert Service sends Telegram manual-command notification
  -> Dashboard Live Event Log shows the action
```

Example REST request made by the dashboard:

```json
{
  "asset_id": "warehouse_cold",
  "action": "pause_deliveries"
}
```

This goes to:

```text
POST http://controller-service:8001/manual_command
```

Then the controller publishes:

```text
Topic: assets/warehouse_cold/actuator
```

And the event history shows:

```text
ACTUATOR_COMMAND_DISPATCHED
ACTUATOR_CONFIRMATION
MANUAL_COMMAND_REQUESTED
```

Important:

```text
Quick actions are not permanent configuration changes.
They are runtime actuator commands.
The next sensor reading may update the warehouse state again.
```

### Step 12: Grafana Shows Historical Time-Series Dashboards

Files:

```text
grafana/provisioning/datasources/datasource.yml
grafana/provisioning/dashboards/dashboard.yml
grafana/provisioning/dashboards/warehouse_dashboard.json
```

Datasource file:

```text
grafana/provisioning/datasources/datasource.yml
```

It connects Grafana to:

```text
URL: http://influxdb:8086
Organization: smart-iot
Bucket: warehouse_metrics
```

Dashboard provider file:

```text
grafana/provisioning/dashboards/dashboard.yml
```

It tells Grafana to load dashboards from:

```text
/var/lib/grafana/dashboards
```

Grafana dashboard file:

```text
grafana/provisioning/dashboards/warehouse_dashboard.json
```

Important real query locations:

- connectivity annotations around line 24.
- actuation annotations around line 41.
- temperature query around line 135.
- humidity query around line 219.
- stock query around line 273.
- device health query around line 359.
- connectivity event table around line 420.
- online/offline timeline around line 518.
- actuation event table around line 579.

Grafana panels show:

```text
Temperature trend
Humidity trend
Stock trend
Device health
Connectivity events
Online/offline timeline
Actuator command/confirmation feed
```

Grafana is for historical and professional time-series visualization.
Streamlit is for live operator control.

Current Grafana URL:

```text
http://localhost:3100
```

### Step 13: Docker Starts and Connects Everything

File:

```text
docker-compose.yml
```

Important real code locations:

- `mqtt-broker` around line 2.
- `catalog-service` around line 15.
- `sensor-simulator` around line 35.
- `influxdb` around line 47.
- `controller-service` around line 70.
- `actuator-service` around line 99.
- `alert-service` around line 109.
- `grafana` around line 146.
- Grafana port `${GRAFANA_PORT:-3100}:3000` around line 151.
- `dashboard` around line 172.
- dashboard environment URLs around lines 179-185.

Docker Compose creates one internal Docker network:

```text
iot-network
```

Inside Docker, services use service names:

```text
http://catalog-service:8080
http://controller-service:8001
http://influxdb:8086
mqtt-broker:1883
```

From your browser, you use localhost:

```text
Dashboard: http://localhost:8501
Grafana: http://localhost:3100
InfluxDB: http://localhost:8086
Catalog: http://localhost:8080
Controller: http://localhost:8001
Alert API: http://localhost:5002
MQTT: localhost:1883
```

The startup helper:

```text
scripts/start_stack.ps1
```

starts the stack safely on Windows. It checks excluded TCP port ranges and writes a safe `GRAFANA_PORT` into `.env`.

Use:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_stack.ps1
```

### Dockerfiles and Requirements Files

Each Python service has its own Dockerfile and requirements file.

Files:

```text
catalog-service/Dockerfile
catalog-service/requirements.txt
controller-service/Dockerfile
controller-service/requirements.txt
sensor-simulator/Dockerfile
sensor-simulator/requirements.txt
actuator-service/Dockerfile
actuator-service/requirements.txt
alert-service/Dockerfile
alert-service/requirements.txt
dashboard/Dockerfile
dashboard/requirements.txt
```

Purpose:

- `Dockerfile` defines how to build the container.
- `requirements.txt` defines Python dependencies.
- `docker-compose.yml` builds/runs these containers together.

Example pattern:

```text
FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD [...]
```

This means every service has isolated dependencies and does not depend on your local Python environment.

## 3. REST API Map

REST is HTTP request/response communication.

### Catalog Service REST

```text
GET /health
  Used by Docker healthcheck.

GET /assets
  Used by Sensor Simulator, Controller, Dashboard.
  Returns all warehouse configurations.

GET /assets/{asset_id}
  Returns one warehouse.

GET /broker
  Used by Sensor Simulator.
  Returns MQTT broker hostname.

GET /port
  Used by Sensor Simulator.
  Returns MQTT port.

POST /add_asset
  Adds a new warehouse.

POST /delete_asset
  Deletes a warehouse.

POST /update_rules
  Updates rule thresholds.
```

### Controller Service REST

```text
GET /health
  Service health.

GET /status
  Latest live state for every warehouse.

GET /events
  Recent InfluxDB event history.

GET /state_history
  Historical warehouse state timeline.

GET /commands
  Pending actuator confirmations.

POST /manual_command
  Dashboard quick actions.

POST /update_rules
  Publishes rule update through MQTT.
```

### Alert Service REST

```text
GET /health
  Service health.

GET /alerts
  Recent alert history.

GET /status
  Alert service status, active warehouses, Telegram configuration.
```

## 4. MQTT Publisher and Subscriber Map

### Sensor Simulator Publishes

```text
assets/{id}/sensors
assets/{id}/heartbeat
assets/{id}/events
```

### Controller Subscribes

```text
assets/+/sensors
warehouse/+/sensors
assets/+/events
assets/+/heartbeat
catalog/config_updated
```

### Controller Publishes

```text
assets/{id}/actuator
assets/{id}/events
```

### Actuator Subscribes

```text
assets/+/actuator
assets/+/sensors
assets/+/events
```

### Actuator Publishes

```text
assets/{id}/events
```

### Alert Service Subscribes

```text
assets/#
system/device_status
```

### Catalog Service Publishes

```text
catalog/config_updated
```

## 5. How to Add a New Warehouse

There are two correct ways. Do not do both at the same time.

### Option A: Edit `catalog-service/catalog.json` Before Starting the Stack

File to edit:

```text
catalog-service/catalog.json
```

Add a new object inside the `assets` array.

Example:

```json
{
  "asset_id": "warehouse_new",
  "name": "New Warehouse",
  "type": "standard",
  "location": "Building C, Floor 1",
  "capacity": 100,
  "owner": "Operator",
  "contact": "operator@company.com",
  "mqtt_sensor_topic": "assets/warehouse_new/sensors",
  "mqtt_actuator_topic": "assets/warehouse_new/actuator",
  "rules": {
    "temp_warning": 30,
    "temp_critical": 40,
    "stock_low": 20,
    "stock_overload": 90,
    "temp_anomaly_high": 46,
    "temp_anomaly_low": -5,
    "humidity_anomaly_high": 96
  }
}
```

Then restart:

```powershell
docker compose restart catalog-service sensor-simulator controller-service
```

What happens:

- Catalog reads the new asset.
- Sensor simulator sees it from `GET /assets`.
- Simulator publishes to `assets/warehouse_new/sensors`.
- Controller receives the new sensor topic because it subscribes to `assets/+/sensors`.
- Dashboard shows the new asset because it reads `GET /assets`.

Optional improvement:

If you want the new warehouse to have a custom realistic sensor profile, edit:

```text
sensor-simulator/sensor_simulator.py
```

Add the asset to:

```text
NORMAL_PROFILES
ANOMALY_PROFILES
```

If you do not add custom profiles, the simulator uses `DEFAULT_NORMAL_PROFILE`, so the warehouse still works.

### Option B: Use REST `POST /add_asset` While Running

This is better for a live professor demo because it proves dynamic configuration.

PowerShell example:

```powershell
$body = @{
  asset_id = "warehouse_new"
  name = "New Warehouse"
  type = "standard"
  location = "Building C, Floor 1"
  capacity = 100
  owner = "Operator"
  contact = "operator@company.com"
  mqtt_sensor_topic = "assets/warehouse_new/sensors"
  mqtt_actuator_topic = "assets/warehouse_new/actuator"
  rules = @{
    temp_warning = 30
    temp_critical = 40
    stock_low = 20
    stock_overload = 90
    temp_anomaly_high = 46
    temp_anomaly_low = -5
    humidity_anomaly_high = 96
  }
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Method Post -Uri http://localhost:8080/add_asset -ContentType "application/json" -Body $body
```

What code handles this:

- `catalog-service/catalog_service.py` `POST()` around line 100.
- `add_asset` branch around line 102.
- `_validate_asset_payload()` around line 57.
- MQTT publish `catalog/config_updated` around line 112.

What happens internally:

```text
POST /add_asset
  -> catalog.json updated
  -> Catalog publishes catalog/config_updated
  -> Controller updates rules
  -> Sensor simulator sees the asset from GET /assets
  -> Dashboard sees the asset from GET /assets
```

## 6. What Changes When Dashboard Actions Are Clicked?

Nothing in the source code changes.

No file is edited.

Instead, runtime state changes:

```text
MQTT messages are published
Actuator logs show action
InfluxDB stores events
Dashboard event log updates
Telegram notification is sent
Pending Commands briefly changes
```

Example: click `Fan ON` for `warehouse_standard`.

Flow:

```text
dashboard/dashboard.py
  POST /manual_command

controller-service/controller_service.py
  manual_command()
  publish_command()
  store_event_influx()

MQTT Broker
  assets/warehouse_standard/actuator

actuator-service/actuator_service.py
  on_message()
  execute_actions()
  publishes confirmation

controller-service/controller_service.py
  receives ACTUATOR_CONFIRMATION
  removes pending command
  writes event to InfluxDB

alert-service/alert_service.py
  handle_event()
  sends Telegram MANUAL COMMAND message

dashboard/dashboard.py
  GET /events
  event_console()
```

Visible result:

- Actuator logs show `Fan turned ON`.
- Controller `/events` shows command and confirmation.
- Dashboard Live Event Log shows manual command/confirmation.
- Telegram receives a manual-command notification.
- Grafana actuation event feed can show command/confirmation history.

## 7. Complete One-Reading Example

Example sensor reading:

```text
warehouse_standard temperature = 52 C
```

Full flow:

```text
1. Sensor Simulator publishes assets/warehouse_standard/sensors.
2. MQTT Broker distributes the message.
3. Controller receives it in on_message().
4. Controller calls evaluate_rules().
5. Rule Engine returns ANOMALY + emergency action.
6. Controller writes warehouse telemetry to InfluxDB.
7. Controller publishes assets/warehouse_standard/actuator.
8. Actuator receives the command.
9. Actuator executes emergency_shutdown / fan ON.
10. Actuator publishes confirmation to assets/warehouse_standard/events.
11. Controller receives confirmation.
12. Controller stores ACTUATOR_CONFIRMATION in InfluxDB.
13. Dashboard shows the state and event.
14. Grafana shows historical trend/event.
15. Alert Service sends Telegram alert.
```

This is the strongest demonstration point:

```text
Sense -> Decide -> Act -> Verify
```

## 8. Verification Files

### `tests/test_controller_rules.py`

This tests the rule engine without Docker, MQTT, or InfluxDB.

It validates:

```text
high temperature -> ANOMALY
high humidity -> ANOMALY
critical temperature -> CRITICAL
overload stock -> OVERLOAD
low stock -> WARNING
default rules still work
```

Run:

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

### `scripts/smoke_test.py`

This tests the running system end to end.

Important behavior:

- Verifies all Docker services are running.
- Calls Catalog, Controller, Grafana, Dashboard health endpoints.
- Checks Alert Service and Telegram polling.
- Queries InfluxDB for recent data.
- Injects an anomaly through MQTT.
- Verifies controller decision.
- Verifies actuator execution.
- Verifies command dispatch and confirmation in InfluxDB.

Run:

```powershell
python scripts\smoke_test.py
```

Success output:

```text
SUCCESS: end-to-end stack verification passed
```

## 9. Project File Index

### Root Files

```text
docker-compose.yml
```

Starts and connects the whole system.

```text
.env.example
```

Example environment variables. Real `.env` is local and ignored by Git.

```text
.gitignore
```

Ignores runtime data, backups, Python cache, and local secrets.

```text
README.md
```

Quick project overview and run instructions.

```text
SYSTEM_EXPLANATION.md
```

This full explanation document.

### Service Folders

```text
catalog-service/
```

Warehouse configuration API and `catalog.json`.

```text
sensor-simulator/
```

Fake IoT sensor publisher.

```text
controller-service/
```

Main decision service and rule engine.

```text
actuator-service/
```

Simulated physical response layer.

```text
alert-service/
```

Telegram and alert API service.

```text
dashboard/
```

Streamlit operator dashboard.

```text
grafana/
```

Grafana datasource and dashboard provisioning.

```text
mqtt-broker/
```

Mosquitto MQTT broker config.

```text
database/
```

InfluxDB setup environment file.

```text
tests/
```

Unit tests.

```text
scripts/
```

Startup and smoke-test scripts.

## 10. Demo Script for Professor

Start:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_stack.ps1
```

Check containers:

```powershell
docker compose ps
```

Open:

```text
Dashboard: http://localhost:8501
Grafana: http://localhost:3100
InfluxDB: http://localhost:8086
```

Show:

1. Warehouse status cards.
2. State timeline.
3. Live event log.
4. Quick action button.
5. Telegram alert/manual-command message.
6. Grafana trends.
7. Smoke test:

```powershell
python scripts\smoke_test.py
```

Key sentence to say:

```text
The system is closed-loop: it senses, decides, acts, and verifies actuator success through confirmation events.
```

## 11. Known Operational Notes

- Grafana runs on `http://localhost:3100` by default because Windows may block port `3900`.
- Use `scripts/start_stack.ps1` to avoid Windows port-exclusion problems.
- Telegram needs valid `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.
- The simulator intentionally creates anomalies, so `ANOMALY`, `CRITICAL`, and `OVERLOAD` states are expected.
- Dashboard quick actions are runtime commands, not permanent rule changes.
- To permanently change rules, update `catalog.json` or call `POST /update_rules`.
- To add a warehouse, update `catalog.json` before startup or call `POST /add_asset` while running.
