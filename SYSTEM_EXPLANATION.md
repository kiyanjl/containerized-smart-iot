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

```

### Human Interaction / Observability Layer

- **Streamlit Dashboard**: A real-time operator UI. By default, it runs on host port **18501** (mappped to container port 8501). This port is configurable via `DASHBOARD_PORT` in `.env` to avoid common Windows port reservation conflicts.
- **Grafana**: Advanced visualization for historical trends and metrics. Runs on port **3100** by default (configurable via `GRAFANA_PORT`).
- **Alert Service / Telegram Bot**: Listens to MQTT events and sends notifications to Telegram. It also supports interactive commands like `/status` and `/alerts`.

### Architecture: MQTT vs REST

The system uses a hybrid communication model:

1. **MQTT (Real-time events)**: Used for high-frequency sensor data, heartbeat signals, anomaly events, actuator commands, and confirmations. It is asynchronous and lightweight.
2. **REST (Request/Response)**: Used for human-facing UIs (Streamlit) and configuration management (Catalog). For example, Streamlit asks the Controller for current status via REST.

**Important Architectural Corrections:**

- `Controller -> Streamlit` is **not** a push connection. Streamlit polls the Controller's REST API.
- `Catalog -> Streamlit` is **not** a push connection. Streamlit polls the Catalog REST API.
- `Alert Service` source is the **MQTT Broker**. It subscribes to all asset topics to detect alerts.
- `Actuator -> Broker` feedback loop: The actuator publishes confirmation events to MQTT, which the controller receives to verify command execution.

### Telegram Integration

The Alert Service (`alert-service/alert_service.py`) provides a bidirectional Telegram integration:

1. **Outbound Alerts**: When a warehouse enters a `CRITICAL` or `OVERLOAD` state, the service sends a detailed message.
2. **Smart Alert Management**: To prevent spamming, the service implements **Rate Limiting** (default 60s) and **State Change Detection**. You only get a repeat alert if the problem persists for a long time OR if the warehouse state changes.
3. **Action Feedback**: When you take a manual action, the alert service **suppresses repetitive alerts** for 15 seconds to give the simulation time to improve.
4. **Improvement Notifications**: When a warehouse returns to a `NORMAL` state after an alert, the system sends a "✅ SITUATION IMPROVED" notification.
5. **Inbound Commands**: Users can interact with the bot using commands like `/status` and `/alerts`.

**Crucial Setup Note**: To receive alerts, you **must** obtain your unique Telegram Chat ID:

1. Message `@userinfobot` on Telegram to get your **Chat ID**.
2. Update the `TELEGRAM_CHAT_ID` field in your `.env` file with this number.
3. Restart the stack (`docker compose up -d --build`).
4. Find your bot (`@smartwarehouse_alert_bot`) and send the `/start` command.

Without a valid Chat ID, the bot will not know where to send the alerts!

### Port Management on Windows

The system includes a smart startup script (`scripts/start_stack.ps1`) that automatically detects if the configured Grafana or Dashboard ports are reserved by the Windows OS (e.g., by Hyper-V or other services). If a port is blocked, the script will automatically find the next available port and update the `.env` file before starting the Docker stack.

- **Default Dashboard Port**: 18501
- **Default Grafana Port**: 3100
- **InfluxDB Port**: 8086 (fixed)
- **MQTT Port**: 1883 (fixed)

---

## 2. Detailed Manual Control Flow (Step-by-Step)

When an operator pushes a button on the [dashboard.py](file:///g:/Term3-4/IOT/iot_new_project/containerized-smart-iot/dashboard/dashboard.py) (e.g., "Heater ON"), the following sequence occurs across the microservices:

### **Step 1: The UI Trigger**

In [dashboard.py](file:///g:/Term3-4/IOT/iot_new_project/containerized-smart-iot/dashboard/dashboard.py), the code defines action labels and buttons.

```python
# dashboard.py:L444-465
action_labels = {
    "fan_on": "Fan ON",
    "heater_on": "Heater ON",
    "door_open": "Open Door",
    # ... other actions
}
# When button is clicked:
if st.button(label, key=f"{selected_asset}-{action}", width="stretch"):
    ok, result = post_json(
        f"{CONTROLLER_URL}/manual_command",
        {"asset_id": selected_asset, "action": action},
    )
```

The dashboard sends an HTTP POST request to the Controller's REST API.

### **Step 2: Controller Processing**

In [controller_service.py](file:///g:/Term3-4/IOT/iot_new_project/containerized-smart-iot/controller-service/controller_service.py), the `manual_command` endpoint receives the request.

```python
# controller_service.py:L684-739
@cherrypy.expose
@cherrypy.tools.json_out()
def manual_command(self):
    # 1. Validate the action
    allowed_actions = { "heater_on": {"heater": "ON"}, ... }
    # 2. Prepare the decision
    decision = { "state": "MANUAL", "action": allowed_actions[action_name], ... }
    # 3. Publish to MQTT for the Actuator
    command_id = controller.publish_command(asset_id, decision)
    # 4. Notify the system that a manual command was requested
    controller.client.publish(f"assets/{asset_id}/events", json.dumps({...}))
```

### **Step 3: Actuator Execution**

In [actuator_service.py](file:///g:/Term3-4/IOT/iot_new_project/containerized-smart-iot/actuator-service/actuator_service.py), the service is listening for MQTT commands on `assets/+/actuator`.

```python
# actuator_service.py:L89-135
def on_message(self, client, userdata, msg):
    # 1. Decode payload
    payload = json.loads(msg.payload.decode())
    # 2. Extract actions and execute
    decision = payload.get("action", {})
    self.execute_actions(decision.get("action", {}))
    # 3. Send confirmation back to MQTT
    confirmation = { "warehouse_id": asset_id, "status": "SUCCESS", ... }
    client.publish(f"assets/{asset_id}/events", json.dumps(confirmation))
```

### Step 4: Alerting (Telegram)

In [alert_service.py](file:///g:/Term3-4/IOT/iot_new_project/containerized-smart-iot/alert-service/alert_service.py), the service monitors both sensor data and events to provide smart notifications.

**1. Manual Command Tracking (L114-130):**
When you push a button, the Alert Service broadcasts the command issued and suppresses repeat alerts for 15 seconds to allow the simulator to react.
```python
def handle_event(payload):
    if event == "MANUAL_COMMAND_REQUESTED":
        manual_command_times[asset_id] = time.time() # Suppression trigger
        broadcast(message) # Notify Telegram
```

**2. Smart Trend & Suggestion Logic (L231-280):**
Every alert now includes trend analysis and intelligent door suggestions based on the current state.
```python
# alert_service.py:L233-255
# Logic for intelligent suggestions:
if temp > 40:
    actions.append("fan")
    actions.append("door_open") # Ventilation
elif temp < -10:
    actions.append("heater")
    actions.append("door_close") # Insulation
elif hum > 90:
    actions.append("dehumidifier")
    actions.append("door_close")
```

**3. Progress Updates (L211-230):**
If a manual command was recently issued and the sensors show improvement, the service sends a progress notification.
```python
# alert_service.py:L215-225
if improving:
    progress_msg = "✅ PROGRESS UPDATE\n⚙️ Your action is working!"
    broadcast(progress_msg)
```

### **Step 5: Visualization & Persistence**

- **InfluxDB**: The Controller stores both the dispatch and the confirmation in the `warehouse_event` measurement.
- **Grafana**: The dashboard automatically shows these events as "Annotations" (vertical blue lines) on the charts.
- **Dashboard**: The "Live Event Log" and "Pending Commands" sections update to show the command's lifecycle.

---

### How to Add a New Warehouse Manually

To add a new warehouse and demonstrate the system's scalability to your professor, you only need to modify **one file**: `catalog-service/catalog.json`.

**Steps:**
1. Open [catalog.json](file:///g:/Term3-4/IOT/iot_new_project/containerized-smart-iot/catalog-service/catalog.json).
2. Add a new object to the `assets` array.
3. **Key Parameters**:
   - `asset_id`: Unique identifier (e.g., `warehouse_prof_demo`).
   - `mqtt_sensor_topic`: `assets/warehouse_prof_demo/sensors`.
   - `rules`: Define the specific thresholds for this warehouse.
4. **Result**: The Simulator will immediately detect the new entry and start publishing data. The Dashboard will show it in the dropdown within 5 seconds.

---

## 4. Key Code Locations & Explanations

### **Intelligence: The Smart Controller**
- **File**: [controller_service.py](file:///g:/Term3-4/IOT/iot_new_project/containerized-smart-iot/controller-service/controller_service.py)
- **L115-160**: `on_message` - The entry point for all sensor data. It decodes MQTT JSON and routes it to the rule engine.
- **L255-286**: `publish_command` - How the controller talks to the physical world. It uses **QoS 2** (Guaranteed Delivery) to ensure actuators receive commands.
- **L680-715**: `manual_command` - The REST API endpoint that allows the Streamlit Dashboard to override automatic logic.

### **The Physical World: Sensor Simulator**
- **File**: [sensor_simulator.py](file:///g:/Term3-4/IOT/iot_new_project/containerized-smart-iot/sensor-simulator/sensor_simulator.py)
- **L12-60**: `ActuatorState` Class - This stores the "current reality" of the warehouse (Is heater on? Is door open?).
- **L70-90**: `step()` function - The **Physics Engine**. It calculates how temperature and humidity change based on actuator states (e.g., `temp_offset += 1.5` if heater is ON).

### **Communication Layer: Alert Service**
- **File**: [alert_service.py](file:///g:/Term3-4/IOT/iot_new_project/containerized-smart-iot/alert-service/alert_service.py)
- **L114-130**: `handle_event` - Listens for manual commands and broadcasts them to Telegram.
- **L211-280**: `handle_sensor` - Performs the trend analysis (📈/📉) and intelligent door suggestions.

---

## 5. System Connections: MQTT vs REST

The architecture is a **Hybrid Distributed System**:

1.  **MQTT (Asynchronous/Event-Driven)**: 
    - Used for: `Simulator -> Controller`, `Controller -> Actuator`, `Actuator -> Alert Service`.
    - **Benefit**: Extremely fast, low bandwidth, and handles many devices easily.
2.  **REST (Synchronous/Request-Response)**:
    - Used for: `Dashboard -> Controller`, `Dashboard -> Catalog`.
    - **Benefit**: Standard web protocol, easy to secure, and perfect for UI-to-Backend queries.

---

## 6. Final Health Check Status
- **Docker**: All 9 containers built and running.
- **Connectivity**: MQTT Broker verified on port 1883.
- **Persistence**: InfluxDB bucket `warehouse_metrics` is active.
- **UI**: Dashboard active on port 18501.
- **Telegram**: Bot polling verified.

The project is ready for submission.

### Step 3: Sensor Simulator Publishes Live Data

File:

```text
sensor-simulator/sensor_simulator.py
```

The simulator is the fake physical world. It continuously creates telemetry data and, most importantly, **reacts to actuator actions in real-time**.

**Simulator Feedback Loop:**

1. The simulator subscribes to `assets/+/actuator` MQTT topics.
2. It tracks the state of each warehouse (e.g., Is the heater ON? Is the door OPEN?).
3. **Trend Simulation**:
   - If **Heater is ON**, the temperature trend increases (+0.8°C per step).
   - If **Fan is ON**, the temperature trend decreases (-0.6°C per step).
   - If **Dehumidifier is ON**, humidity decreases significantly.
   - If **Door is OPEN**, humidity increases and temperature trends toward ambient.
   - If **Pause Deliveries** is active, stock levels begin to decrease.

This creates a "Real-World" feedback loop where your manual actions on the dashboard actually improve the warehouse situation.

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

It also sends anomaly events and heartbeats to prove device health.

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

The actuator is the simulated physical hardware. It performs three critical roles:

1. **Edge Safety**: It listens directly to sensor data. If temperature exceeds 40°C, it turns on the fan immediately _without_ waiting for the controller. This simulates local safety logic.
2. **Action Execution**: It processes commands like `heater`, `fan`, `door`, and `dehumidifier`.
3. **Command Reliability**: It includes a **Retry Thread**. If it receives a command but cannot confirm it immediately, it will retry up to 3 times to ensure the system reaches the desired state.

Supported actions:

```text
fan: ON/OFF
heater: ON/OFF
door: OPEN/CLOSE
dehumidifier: ON/OFF
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

The system does not assume commands succeed. It verifies them and retries if necessary.

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

When you click a quick action, this real-world feedback loop occurs:

```text
Dashboard button
  -> POST /manual_command to Controller
  -> Controller publishes MQTT actuator command
  -> Actuator receives command & executes simulated hardware action
  -> Sensor Simulator receives command & updates physical trend logic
     (e.g., Temperature begins to rise if Heater was turned ON)
  -> Alert Service receives request & broadcasts "🛠️ MANUAL COMMAND ISSUED"
  -> Alert Service suppresses repetitive alerts for 15s to allow recovery
  -> Simulator publishes improved telemetry
  -> Controller & Dashboard show the warehouse returning to NORMAL
  -> Alert Service sends "✅ SITUATION IMPROVED" notification
```

This ensures the system is not just a "display" but a true **Closed-Loop Control System**.

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
Dashboard: http://localhost:18501 (Configurable via DASHBOARD_PORT)
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
Dashboard: http://localhost:18501 (or configured DASHBOARD_PORT)
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
