# Smart Warehouse IoT System Explanation

This project is a containerized IoT warehouse monitoring system. It simulates warehouse sensors, sends telemetry through MQTT, evaluates warehouse safety rules, writes data to InfluxDB, controls actuators, sends Telegram alerts, and shows everything in Streamlit and Grafana dashboards.

## Runtime Flow

1. `sensor-simulator/sensor_simulator.py` publishes temperature, humidity, stock, door, heartbeat, and anomaly events to MQTT.
2. `controller-service/controller_service.py` subscribes to sensor MQTT topics, evaluates rules with `controller-service/rule_engine.py`, publishes actuator commands, and stores telemetry/events in InfluxDB.
3. `actuator-service/actuator_service.py` subscribes to actuator commands, executes simulated actions, and publishes confirmations back to MQTT.
4. `alert-service/alert_service.py` listens to live sensor data and sends rich Telegram alerts for critical/overload/anomaly situations.
5. `dashboard/dashboard.py` displays live warehouse cards, state timeline, event log, alerts, health, quick actions, and Grafana/InfluxDB links.
6. Grafana reads the InfluxDB bucket and renders long-term time-series dashboards.

## File-by-File Explanation

### `docker-compose.yml`

This is the main orchestration file. It starts all services on one Docker network and wires dependencies together.

Important sections:

- Lines 2-13: `mqtt-broker` runs Eclipse Mosquitto. It exposes port `1883` for MQTT and `9001` for WebSocket MQTT.
- Lines 15-33: `catalog-service` exposes the asset catalog on port `8080` and includes a health check.
- Lines 35-44: `sensor-simulator` waits for MQTT and catalog before publishing simulated data.
- Lines 47-68: `influxdb` creates the `warehouse_metrics` bucket and exposes port `8086`.
- Lines 70-97: `controller-service` receives MQTT telemetry, stores InfluxDB points, and exposes API port `8001`.
- Lines 99-106: `actuator-service` subscribes to actuator commands.
- Lines 109-143: `alert-service` exposes port `5002`, connects to MQTT, and uses `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID`.
- Lines 146-170: `grafana` exposes `${GRAFANA_PORT:-3100}` on the host and allows embedding with anonymous viewer mode. The project uses `3100` because Windows can reserve/exclude port `3900`.
- Lines 172-200: `dashboard` exposes Streamlit on port `8501`.

Why it matters: compose is the backbone of the system. If a URL, token, port, or dependency is wrong here, services may run but not communicate.

### `catalog-service/catalog.json`

This file defines the warehouses and their rule thresholds.

Each asset contains:

- `asset_id`: unique warehouse identifier, for example `warehouse_cold`.
- `mqtt_sensor_topic`: where the simulator publishes telemetry.
- `mqtt_actuator_topic`: where the controller sends commands.
- `rules`: thresholds used by the controller rule engine.

Important rule fields:

- `temp_warning`: temperature where state becomes `WARNING`.
- `temp_critical`: temperature where state becomes `CRITICAL`.
- `stock_low`: stock level that triggers restock alert.
- `stock_overload`: stock level that triggers `OVERLOAD`.
- `temp_anomaly_high` / `temp_anomaly_low`: extreme values that trigger `ANOMALY`.
- `humidity_anomaly_high`: high humidity anomaly threshold.

### `catalog-service/catalog_service.py`

This is the REST API for warehouse configuration.

Important code:

- Line 15: `class CatalogService` owns catalog loading, saving, and MQTT publishing.
- Line 74: `GET()` handles `/health`, `/assets`, `/assets/{id}`, `/broker`, and `/port`.
- Line 100: `POST()` handles catalog changes.
- Lines 102-123: `add_asset` validates and adds a warehouse, then publishes `catalog/config_updated`.
- Lines 126-149: `delete_asset` removes a warehouse and publishes an empty rules update.
- Lines 153-174: `update_rules` changes thresholds and notifies the controller through MQTT.

Why it matters: the catalog makes rules dynamic. The controller does not need hardcoded warehouse IDs.

### `sensor-simulator/sensor_simulator.py`

This service creates realistic live sensor traffic.

Important code:

- Line 13: `HEARTBEAT_INTERVAL = 60` sends device heartbeats once per minute.
- Line 39: `ANOMALY_PROFILES` defines interesting failure modes like heat spikes, humidity flood, freezer overcool, and sensor faults.
- Line 62: `get_assets()` loads active warehouses from catalog.
- Line 83: `class AnomalyState` manages anomaly bursts so alerts feel realistic instead of random one-off noise.
- Line 112: `generate_sensor_data()` builds normal telemetry for each warehouse.
- Line 124: `apply_anomaly_profile()` modifies telemetry during anomaly bursts.
- Line 183: `client.publish(topic, json.dumps(payload), qos=1)` publishes sensor payloads to MQTT.
- Line 194: publishes `ANOMALY_DETECTED` events.
- Line 211: publishes heartbeat events.

Why it matters: it feeds the complete system. Without this service, dashboard values and InfluxDB charts stop updating.

### `controller-service/rule_engine.py`

This is the decision logic. It converts raw sensor values into states and actuator actions.

Important code:

- Line 4: `evaluate_rules(data, rules=None)` is the main rule function.
- Line 31: extreme high/low temperature becomes `ANOMALY` and triggers emergency shutdown plus fan.
- Line 35: extreme humidity becomes `ANOMALY` and turns on the dehumidifier.
- Line 38: critical temperature becomes `CRITICAL` and turns on the fan.
- Line 41: stock overload becomes `OVERLOAD` and pauses deliveries.
- Line 44: warning temperature becomes `WARNING`.
- Line 47: low stock adds `restock_alert`; if the state was normal, it upgrades to `WARNING`.
- Line 52: returns the final `state`, `action`, and `timestamp`.

Why it matters: this is the unique rule engine behavior. The state priority is intentional: anomaly beats critical, critical beats overload, and low stock can add an action without hiding a more serious state.

### `controller-service/controller_service.py`

This is the central brain of the stack. It subscribes to MQTT, applies rules, stores data, exposes dashboard APIs, and dispatches actuator commands.

Important code:

- Line 34: `class SmartController` initializes MQTT, rules cache, InfluxDB, command tracking, and background workers.
- Line 129: `on_message()` handles all incoming MQTT messages.
- Sensor payload path inside `on_message()`: it updates `last_seen`, loads rules, calls `apply_rules()`, sends an actuator command, stores live state, and writes to InfluxDB.
- Line 255: `publish_command()` sends commands to `assets/{asset_id}/actuator` with `qos=2` and tracks pending confirmations.
- Line 286: `store_influx()` writes `temperature`, `humidity`, `stock`, and `state_code` to the `warehouse` measurement.
- Line 318: `store_event_influx()` writes events such as actuator confirmations, device online/offline, and anomaly events.
- Line 530: `health()` returns service status, known assets, and whether InfluxDB is enabled.
- Line 569: `events()` queries recent `warehouse_event` rows from InfluxDB. It uses `group()` and `limit(n: 100)` so the dashboard receives a bounded event feed.
- Line 611: `state_history()` queries telemetry history and uses Flux `pivot()` so temperature, humidity, stock, and state_code appear together in one row.
- Line 679: `manual_command()` lets the dashboard send manual actions like `fan_on`, `pause_deliveries`, and `emergency_shutdown`.

Why it matters: this file connects the whole pipeline: MQTT in, rule decision, MQTT command out, InfluxDB persistence, and REST API for UI.

### `actuator-service/actuator_service.py`

This service simulates physical actuator behavior.

Important code:

- Line 31: `received_commands` stores processed command IDs so duplicate retained MQTT messages are ignored.
- Line 32: `pending_commands` tracks commands waiting for confirmation.
- Line 34: `on_connect()` subscribes to `assets/+/actuator`, `assets/+/sensors`, and `assets/+/events`.
- Line 49: `on_message()` processes sensor safety triggers, actuator commands, and confirmation events.
- Line 88: duplicate command protection prevents repeated execution.
- Line 128: `execute_actions()` prints simulated actions such as fan on, emergency shutdown, dehumidifier on, restock alert, and pause deliveries.
- Line 148: `start()` connects to MQTT.
- Line 151: `retry_unconfirmed()` retries pending commands.
- Line 184: starts the retry thread before `loop_forever()`, which is important because code after `loop_forever()` would never run.

Why it matters: this proves the controller is not just calculating states; it is actually publishing commands and receiving confirmations.

### `alert-service/alert_service.py`

This service handles live alerts and Telegram communication.

Important code:

- Line 41: `/status` returns active warehouse count, alert count, subscriber count, and Telegram configuration status.
- Line 57: `send_telegram()` posts messages to Telegram `sendMessage`.
- Line 68: `broadcast()` sends alerts to subscribers and the configured `TELEGRAM_CHAT_ID`.
- Line 75: `telegram_poll()` validates the bot and handles `/start`, `/status`, `/alerts`, `/subscribe`, `/unsubscribe`, and `/help`.
- Line 137: `handle_sensor()` detects alert states from live telemetry.
- Line 173: formats the rich Telegram alert message with warehouse, state, temperature, humidity, stock, door, suggested action, and UTC time.
- Line 197: `on_connect()` subscribes to MQTT topics.
- Line 203: `on_message()` routes MQTT sensor payloads into alert handling.
- Line 211: `main()` starts HTTP, Telegram polling, and MQTT loop.

Why it matters: it gives live human notification outside the dashboard. Telegram delivery depends on valid `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID`.

### `dashboard/dashboard.py`

This is the Streamlit operations dashboard.

Important code:

- Line 11: reads `CATALOG_URL`, `CONTROLLER_URL`, `ALERT_URL`, `GRAFANA_URL`, and InfluxDB public URL from environment variables.
- Line 18: `STATE_STYLE` maps controller states to readable colors and short badges.
- Lines 43-190: custom CSS defines the enhanced dark UI, high-contrast KPI cards, readable warehouse cards, event console, responsive layout, and stronger buttons.
- Line 201: `get_json()` safely calls REST APIs and returns defaults when a service is unavailable.
- Line 238: `kpi_card()` renders custom KPI cards. This replaced low-contrast Streamlit metrics.
- Line 251: `status_card()` renders each warehouse card with temperature, humidity, stock, door, state badge, and last sample age.
- Line 287: `event_console()` renders the scrolling live event log with colors for commands, confirmations, online/offline, and anomaly events.
- Line 314: `state_history_chart()` builds the Plotly state timeline from controller `/state_history`.
- Line 353: loads warehouse assets from catalog, with fallback demo assets.
- Lines 390-392: quick links to Grafana, InfluxDB, and Catalog API.
- Line 426: quick actions POST to controller `/manual_command`.
- Line 456: embeds Grafana with `st.iframe()`.
- Line 458: auto-refresh keeps the dashboard live.

Why it matters: this is the operator-facing control center. It now shows real sensor data, live events, timeline history, alerts, service health, Grafana access, and manual controls.

#### Pending Commands and Quick Actions

`Pending Commands` means: the controller has already sent an MQTT command to an actuator, but the actuator has not yet sent its confirmation event back.

Normal behavior:

- The value is usually `0`.
- It may briefly become `1` or more for a fraction of a second after a command is sent.
- If it stays above `0`, the actuator service may be down, MQTT may be delayed, or the command confirmation may not be returning.

Quick action flow:

1. The operator clicks a dashboard button such as `Fan ON` or `Pause Deliveries`.
2. `dashboard.py` sends `POST /manual_command` to `controller-service`.
3. `controller_service.py` publishes an MQTT command to `assets/{warehouse_id}/actuator`.
4. `actuator_service.py` receives it and prints the simulated physical action, for example `Fan turned ON`.
5. The actuator publishes a confirmation event to `assets/{warehouse_id}/events`.
6. The controller removes the command from `pending_confirmations`.
7. InfluxDB stores these events:
   - `ACTUATOR_COMMAND_DISPATCHED`
   - `ACTUATOR_CONFIRMATION`
   - `MANUAL_COMMAND_REQUESTED`
8. The dashboard live event log shows the command and confirmation.
9. `alert_service.py` sends a Telegram notification for `MANUAL_COMMAND_REQUESTED`.

Important note: quick actions do not permanently change `catalog.json` or any source-code file. They are runtime actuator commands. The next sensor reading can update the warehouse state again because the dashboard state cards show live controller state, not a permanent manual mode.

### `grafana/provisioning/datasources/datasource.yml`

This provisions Grafana's InfluxDB datasource automatically.

It points Grafana at:

- URL: `http://influxdb:8086`
- Organization: `smart-iot`
- Bucket: `warehouse_metrics`
- Token: same token used by controller and smoke tests.

Why it matters: Grafana starts ready to read time-series data without manual UI setup.

### `grafana/provisioning/dashboards/warehouse_dashboard.json`

This is the Grafana dashboard definition.

It contains panels for:

- Temperature trend.
- Humidity trend.
- Stock level.
- Device health.
- Connectivity event feed.
- Online/offline timeline.
- Actuation event feed.

Why it matters: Streamlit is the live control center, while Grafana is the time-series analytics view.

### `tests/test_controller_rules.py`

This validates the rule engine behavior.

Important tests:

- High temperature triggers `ANOMALY` and emergency shutdown.
- High humidity triggers dehumidifier.
- Critical temperature stays `CRITICAL` even when stock is low.
- Overload pauses deliveries.
- Low stock upgrades normal state to `WARNING`.
- Default rules still catch anomaly conditions.

Why it matters: these tests protect the most important logic from regressions.

### `scripts/smoke_test.py`

This is the end-to-end system test.

It checks:

- All Docker services are running.
- HTTP health endpoints work.
- Alert service and Telegram polling are active.
- InfluxDB has recent telemetry and device-health rows.
- A test anomaly can be injected through MQTT.
- Controller logs show `ANOMALY`.
- InfluxDB stores the anomaly.
- Actuator service receives and executes the command.
- Actuator confirmation is written back to InfluxDB.

Why it matters: this script proves the complete pipeline works, not just isolated files.

## Important API Endpoints

- Catalog: `GET http://localhost:8080/assets`
- Controller health: `GET http://localhost:8001/health`
- Controller live state: `GET http://localhost:8001/status`
- Controller events: `GET http://localhost:8001/events`
- Controller state timeline: `GET http://localhost:8001/state_history`
- Manual control: `POST http://localhost:8001/manual_command`
- Alert status: `GET http://localhost:5002/status`
- Alert history: `GET http://localhost:5002/alerts`
- Dashboard: `http://localhost:8501`
- Grafana: `http://localhost:3100` by default, or `http://localhost:{GRAFANA_PORT}` if `.env` changes it.
- InfluxDB: `http://localhost:8086`

Example manual command body:

```json
{
  "asset_id": "warehouse_standard",
  "action": "fan_on"
}
```

Allowed manual actions:

- `fan_on`
- `dehumidifier_on`
- `pause_deliveries`
- `restock_alert`
- `emergency_shutdown`

## Current Verification Checklist

The final system should be considered healthy when:

- `docker compose ps` shows every service running.
- `http://localhost:8501/_stcore/health` returns `ok`.
- `http://localhost:8001/status` shows live warehouse temperature/humidity/stock.
- `http://localhost:8001/events` returns recent actuation and device events.
- `http://localhost:8001/state_history` returns timeline rows.
- `python scripts/smoke_test.py` finishes with `SUCCESS: end-to-end stack verification passed`.
- Telegram logs show `Telegram bot verified` and `Telegram command polling started`.

## Known Operational Notes

- Telegram requires valid `.env` values for `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID`.
- Grafana embedding is enabled in compose with `GF_SECURITY_ALLOW_EMBEDDING=true`.
- On Windows, use `powershell -ExecutionPolicy Bypass -File .\scripts\start_stack.ps1` instead of plain `docker compose up -d` if Docker reports that a port is forbidden. The script checks Windows excluded TCP ranges and writes a safe `GRAFANA_PORT` into `.env` before starting the stack.
- The simulator intentionally creates anomalies, so seeing `ANOMALY`, `CRITICAL`, or `OVERLOAD` states is expected.
- The dashboard auto-refreshes every 5 seconds, so values should change continuously while the simulator is running.
