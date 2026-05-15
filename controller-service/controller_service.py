import cherrypy
import json
import os
import threading
import time

import paho.mqtt.client as mqtt
import requests
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from rule_engine import evaluate_rules

CATALOG_URL = "http://catalog-service:8080"
MQTT_BROKER = "mqtt-broker"
MQTT_PORT = 1883

INFLUX_URL = os.environ.get("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.environ.get("INFLUX_TOKEN")
INFLUX_ORG = os.environ.get("INFLUX_ORG", "smart-iot")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "warehouse_metrics")

STATE_CODES = {
    "NORMAL": 0,
    "WARNING": 1,
    "CRITICAL": 2,
    "OVERLOAD": 3,
    "ANOMALY": 4,
}

DEVICE_MONITOR_INTERVAL = 30
DEVICE_TIMEOUT_SECONDS = 120


class SmartController:
    def __init__(self):
        print("Initializing Smart Controller")

        self.client = mqtt.Client(client_id="smart_controller")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        self.state = {}
        self.rules_file = "rules_cache.json"

        try:
            with open(self.rules_file, "r") as handle:
                self.rules_cache = json.load(handle)
                print("Rules loaded from cache")
        except Exception:
            self.rules_cache = {}

        self.last_seen = {}
        self.device_status = {}
        self.known_assets = set(self.rules_cache.keys())
        self.controller_started_at = time.time()

        # ✅ FIX #1: Add resilience data structures
        self.pending_confirmations = {}  # Track commands waiting for ACK
        self.influx_write_queue = []     # Queue failed InfluxDB writes
        self.lock = threading.Lock()     # Thread-safe access

        self.bootstrap_rules_from_catalog()

        self.influx = None
        self.write_api = None
        if INFLUX_URL and INFLUX_TOKEN:
            try:
                self.influx = InfluxDBClient(
                    url=INFLUX_URL,
                    token=INFLUX_TOKEN,
                    org=INFLUX_ORG,
                )
                self.write_api = self.influx.write_api(
                    write_options=SYNCHRONOUS
                )
                print("Connected to InfluxDB")
            except Exception as exc:
                print("Failed to initialize InfluxDB client:", exc)

        # ✅ FIX #2: Start daemon threads for monitoring
        threading.Thread(target=self._monitor_confirmations,
                         daemon=True).start()
        threading.Thread(target=self._process_influx_queue,
                         daemon=True).start()
        threading.Thread(target=self._periodic_rule_sync, daemon=True).start()

    def save_rules_cache(self):
        with open(self.rules_file, "w") as handle:
            json.dump(self.rules_cache, handle)

    def bootstrap_rules_from_catalog(self):
        for _ in range(3):
            try:
                response = requests.get(f"{CATALOG_URL}/assets", timeout=2)
                response.raise_for_status()
                assets = response.json()
                self.rules_cache.update({
                    asset["asset_id"]: asset.get("rules", {})
                    for asset in assets
                })
                self.known_assets.update(
                    asset["asset_id"] for asset in assets
                )
                self.save_rules_cache()
                print("Rules loaded from catalog")
                return
            except Exception as exc:
                print("Failed to load rules from catalog:", exc)
                time.sleep(2)

    def on_connect(self, client, userdata, flags, rc):
        print("Controller connected to MQTT broker")
        client.subscribe("assets/+/sensors")
        print("Subscribed to assets/+/sensors")
        client.subscribe("warehouse/+/sensors")
        print("Subscribed to warehouse/+/sensors")
        client.subscribe("catalog/config_updated")
        client.subscribe("assets/+/events")
        client.subscribe("assets/+/heartbeat")

    # ✅ FIX #3: Add disconnect handler
    def on_disconnect(self, client, userdata, rc):
        """Handle MQTT broker disconnection"""
        if rc != 0:
            print(f"⚠ Unexpected MQTT disconnect, rc={rc}. Reconnecting...")
        else:
            print("Controller disconnected from MQTT broker (normal)")

    def on_message(self, client, userdata, msg):
        topic = msg.topic

        try:
            payload = json.loads(msg.payload.decode())
            print("MQTT:", topic, payload)
        except Exception as exc:
            print("Failed to decode payload:", exc)
            return

        if topic == "catalog/config_updated":
            asset_id = payload["asset_id"]
            rules = payload.get("rules", {})

            if rules:
                self.rules_cache[asset_id] = rules
                self.known_assets.add(asset_id)
            else:
                self.rules_cache.pop(asset_id, None)
                self.known_assets.discard(asset_id)
                self.last_seen.pop(asset_id, None)
                self.device_status.pop(asset_id, None)
                self.state.pop(asset_id, None)

            self.save_rules_cache()
            print(f"Rules updated for {asset_id}")
            return

        if topic.endswith("/events"):
            event = payload.get("event")
            if event:
                if event == "ANOMALY_DETECTED":
                    print(
                        "Anomaly event:",
                        payload.get("warehouse_id"),
                        payload.get("anomaly_type")
                    )
                else:
                    print(
                        "Device event:",
                        payload.get("warehouse_id"),
                        event
                    )
                try:
                    self.store_event_influx(payload)
                    print("Stored event to InfluxDB")
                except Exception as exc:
                    print("Failed to store event:", exc)
            elif payload.get("status"):
                # ✅ FIX #4: Record confirmation and remove from tracking
                command_id = payload.get("command_id")
                if command_id:
                    with self.lock:
                        self.pending_confirmations.pop(
                            command_id, None)  # Remove from tracking
                    print(f"✓ Confirmation received for command {command_id}")

                print("Actuator confirmation:", payload)
                try:
                    self.store_event_influx({
                        "warehouse_id": payload["warehouse_id"],
                        "event": "ACTUATOR_CONFIRMATION",
                        "source": "actuator_service",
                        "command_id": payload.get("command_id", ""),
                        "status": payload.get("status", ""),
                        "timestamp": payload.get("timestamp", time.time()),
                    })
                    print("Stored actuator confirmation to InfluxDB")
                except Exception as exc:
                    print("Failed to store actuator confirmation:", exc)
            else:
                print("Actuator confirmation:", payload)
            return

        if topic.endswith("/heartbeat"):
            asset_id = payload["warehouse_id"]
            self.last_seen[asset_id] = time.time()
            self.known_assets.add(asset_id)
            print(f"Heartbeat from {asset_id}")
            return

        asset_id = payload.get("warehouse_id")
        if not asset_id:
            return

        print(f"Sensor data received from {asset_id}")
        self.last_seen[asset_id] = time.time()
        self.known_assets.add(asset_id)

        rules = self.rules_cache.get(asset_id)
        if not rules:
            asset_config = self.get_asset_config(asset_id)
            rules = asset_config.get("rules", {}) if asset_config else {}
            if rules:
                self.rules_cache[asset_id] = rules
                self.known_assets.add(asset_id)
                self.save_rules_cache()

        config = {"rules": rules or {}}
        decision = self.apply_rules(payload, config)

        self.publish_command(asset_id, decision)
        self.state[asset_id] = {
            **decision,
            "temperature": float(payload.get("temperature", 0)),
            "humidity": float(payload.get("humidity", 0)),
            "stock": int(payload.get("stock", 0)),
            "door_open": payload.get("door_open", 0),
            "timestamp": time.time(),
        }

        try:
            self.store_influx(asset_id, payload, decision)
            print("Stored measurement to InfluxDB")
        except Exception as exc:
            print("Failed to store measurement to InfluxDB:", exc)

    def apply_rules(self, data, config):
        rules = config.get("rules", {})
        if not rules:
            print("Using default safety rules")

        decision = evaluate_rules(data, rules)
        print(f"Decision: {decision['state']}, Actions: {decision['action']}")
        return decision

    def publish_command(self, asset_id, decision):
        topic = f"assets/{asset_id}/actuator"
        print(f"Publishing to {topic}")
        command_id = str(time.time())
        command = {
            "command_id": command_id,
            "action": decision,
        }
        # ✅ FIX #5: Change QoS from 0 → 2 (GUARANTEED DELIVERY)
        self.client.publish(topic, json.dumps(command), qos=2, retain=True)

        # ✅ FIX #6: Track command in pending_confirmations
        with self.lock:
            self.pending_confirmations[command_id] = {
                "asset_id": asset_id,
                "sent_at": time.time()
            }

        try:
            self.store_event_influx({
                "warehouse_id": asset_id,
                "event": "ACTUATOR_COMMAND_DISPATCHED",
                "source": "smart_controller",
                "command_id": command_id,
                "status": decision.get("state", ""),
                "timestamp": time.time(),
            })
            print("Stored actuator command dispatch to InfluxDB")
        except Exception as exc:
            print("Failed to store actuator command dispatch:", exc)

        return command_id

    def store_influx(self, asset_id, data, decision):
        if not self.write_api:
            return

        print("Queuing to InfluxDB...")
        state = decision["state"]

        point = (
            Point("warehouse")
            .tag("warehouse_id", asset_id)
            .tag("state", state)
            .field("temperature", float(data["temperature"]))
            .field("humidity", float(data["humidity"]))
            .field("stock", int(data["stock"]))
            .field("door_open", int(data.get("door_open", 0)))
            .field("state_code", STATE_CODES.get(state, -1))
            .time(time.time_ns(), WritePrecision.NS)
        )

        # ✅ FIX #11: Use async write with retry queue
        try:
            self.write_api.write(
                bucket=INFLUX_BUCKET,
                org=INFLUX_ORG,
                record=point,
            )
            print("Write complete")
        except Exception as exc:
            print(f"⚠ InfluxDB write failed: {exc}")
            with self.lock:
                self.influx_write_queue.append(point)
            print("Write queued for retry")

    def store_event_influx(self, payload):
        if not self.write_api:
            return

        timestamp_ns = int(
            float(payload.get("timestamp", time.time())) * 1_000_000_000
        )

        point = (
            Point("warehouse_event")
            .tag("warehouse_id", payload["warehouse_id"])
            .tag("event", payload.get("event", ""))
            .tag("anomaly_type", payload.get("anomaly_type", ""))
            .tag("source", payload.get("source", ""))
            .tag("command_id", payload.get("command_id", ""))
            .tag("status", payload.get("status", ""))
            .field("value", 1)
            .time(timestamp_ns, WritePrecision.NS)
        )

        self.write_api.write(
            bucket=INFLUX_BUCKET,
            org=INFLUX_ORG,
            record=point,
        )

    def publish_device_event(self, asset_id, online, timestamp):
        event = "DEVICE_ONLINE" if online else "DEVICE_OFFLINE"
        self.client.publish(
            f"assets/{asset_id}/events",
            json.dumps({
                "warehouse_id": asset_id,
                "event": event,
                "source": "smart_controller",
                "timestamp": timestamp,
            }),
            qos=1,
            retain=True,
        )
        print(f"Device status event published: {asset_id} -> {event}")

    def store_device_health(self, asset_id, online, last_seen_age):
        if not self.write_api:
            return

        point = (
            Point("device_health")
            .tag("warehouse_id", asset_id)
            .tag("status", "ONLINE" if online else "OFFLINE")
            .field("online", 1 if online else 0)
            .field("last_seen_age_sec", float(last_seen_age))
            .time(time.time_ns(), WritePrecision.NS)
        )

        self.write_api.write(
            bucket=INFLUX_BUCKET,
            org=INFLUX_ORG,
            record=point,
        )

    def get_asset_config(self, asset_id):
        assets = []
        for _ in range(3):
            try:
                response = requests.get(f"{CATALOG_URL}/assets", timeout=2)
                response.raise_for_status()
                assets = response.json()
                break
            except Exception:
                print("Catalog unreachable, retrying...")
                time.sleep(2)

        for asset in assets:
            if asset["asset_id"] == asset_id:
                return asset
        return {}

    def monitor_devices(self):
        while True:
            time.sleep(DEVICE_MONITOR_INTERVAL)
            now = time.time()

            for asset_id in sorted(self.known_assets):
                last = self.last_seen.get(asset_id)
                if last is None:
                    last_seen_age = now - self.controller_started_at
                    if last_seen_age <= DEVICE_TIMEOUT_SECONDS:
                        continue
                    online = False
                else:
                    last_seen_age = now - last
                    online = last_seen_age <= DEVICE_TIMEOUT_SECONDS

                self.store_device_health(asset_id, online, last_seen_age)

                previous = self.device_status.get(asset_id)
                if previous is None or previous != online:
                    self.device_status[asset_id] = online
                    self.publish_device_event(asset_id, online, now)
                    if not online:
                        print(f"Device offline alert published: {asset_id}")

    # ✅ FIX #7: Monitor confirmation timeouts
    def _monitor_confirmations(self):
        """
        Monitor for confirmation timeouts.
        If command sent but no ACK within 30s, log warning.
        """
        while True:
            time.sleep(5)  # Check every 5 seconds
            now = time.time()

            with self.lock:
                timed_out = []
                for command_id, info in list(self.pending_confirmations.items()):
                    elapsed = now - info["sent_at"]
                    if elapsed > 30:  # 30 second timeout
                        timed_out.append(
                            (command_id, info["asset_id"], elapsed))
                        self.pending_confirmations.pop(command_id, None)

            for command_id, asset_id, elapsed in timed_out:
                print(f"⚠ TIMEOUT: No confirmation for command {command_id} "
                      f"on {asset_id} after {elapsed:.1f}s")

    # ✅ FIX #8: Retry InfluxDB writes
    def _process_influx_queue(self):
        """
        Retry InfluxDB writes that failed.
        Prevents data loss on transient network errors.
        """
        while True:
            time.sleep(10)  # Try every 10 seconds
            if not self.write_api or not self.influx_write_queue:
                continue

            with self.lock:
                queue_copy = list(self.influx_write_queue)
                self.influx_write_queue.clear()

            for point in queue_copy:
                try:
                    self.write_api.write(
                        bucket=INFLUX_BUCKET,
                        org=INFLUX_ORG,
                        record=point,
                    )
                    print(f"✓ Retried InfluxDB write successfully")
                except Exception as exc:
                    print(f"⚠ Retry write failed, re-queueing: {exc}")
                    with self.lock:
                        self.influx_write_queue.append(point)

    # ✅ FIX #9: Periodic rule sync from catalog
    def _periodic_rule_sync(self):
        """
        Refresh rules from catalog every 5 minutes.
        Ensures catalog updates are picked up even if MQTT message missed.
        """
        while True:
            time.sleep(300)  # 5 minutes
            try:
                response = requests.get(f"{CATALOG_URL}/assets", timeout=5)
                response.raise_for_status()
                assets = response.json()

                with self.lock:
                    for asset in assets:
                        self.rules_cache[asset["asset_id"]
                                         ] = asset.get("rules", {})

                self.save_rules_cache()
                print(f"✓ Rules synced from catalog ({len(assets)} assets)")
            except Exception as exc:
                print(f"⚠ Rule sync failed: {exc}")

    def start_mqtt(self):
        print("Connecting to MQTT broker...")
        # ✅ FIX #10: Add disconnect handler + exponential backoff reconnect
        self.client.on_disconnect = self.on_disconnect
        self.client.reconnect_delay_set(1, 32)  # Backoff: 1s → 32s

        self.client.connect(MQTT_BROKER, MQTT_PORT)
        self.client.loop_forever()


controller = None


def get_controller():
    global controller
    if controller is None:
        controller = SmartController()
    return controller


class RootAPI:
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def index(self):
        return {
            "service": "Smart Controller",
            "endpoints": ["/status", "/health", "/events", "/state_history", "/commands", "/manual_command"],
        }

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def status(self):
        return get_controller().state

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def health(self):
        current_controller = get_controller()
        return {
            "service": "Smart Controller",
            "status": "ok",
            "known_assets": sorted(current_controller.known_assets),
            "influx_enabled": bool(current_controller.write_api),
        }

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def update_rules(self):
        try:
            body = cherrypy.request.body.read().decode()
            data = json.loads(body)
            asset_id = data.get("asset_id")
            updated_rules = data.get("rules")

            if not asset_id or not updated_rules:
                return {"error": "Missing asset_id or rules"}

            mqtt_client = mqtt.Client()
            mqtt_client.connect(MQTT_BROKER, MQTT_PORT)
            mqtt_client.publish(
                "catalog/config_updated",
                json.dumps({
                    "asset_id": asset_id,
                    "rules": updated_rules,
                }),
                retain=True,
            )

            print(f"Published rule update for {asset_id}")
            return {"success": True, "asset_id": asset_id}
        except Exception as exc:
            return {"error": str(exc)}
            
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def events(self):
        """Get recent events from InfluxDB"""
        try:
            current_controller = get_controller()
            if not current_controller.influx:
                return {"events": [], "error": "InfluxDB client is not available"}

            query = (
                f'from(bucket:"{INFLUX_BUCKET}") '
                '|> range(start: -6h) '
                '|> filter(fn: (r) => r._measurement == "warehouse_event") '
                '|> filter(fn: (r) => r._field == "value") '
                '|> keep(columns: ["_time", "warehouse_id", "event", "anomaly_type", "source", "command_id", "status", "_value"]) '
                '|> group() '
                '|> sort(columns: ["_time"], desc: true) '
                '|> limit(n: 100)'
            )
            result = current_controller.influx.query_api().query(
                query,
                org=INFLUX_ORG,
            )
            
            events = []
            for table in result:
                for record in table.records:
                    events.append({
                        "time": record.get_time().isoformat(),
                        "warehouse_id": record.values.get("warehouse_id"),
                        "event": record.values.get("event"),
                        "anomaly_type": record.values.get("anomaly_type"),
                        "source": record.values.get("source"),
                        "command_id": record.values.get("command_id"),
                        "status": record.values.get("status"),
                        "value": record.get_value()
                    })
            
            return {"events": events}
        except Exception as exc:
            return {"error": str(exc)}
            
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def state_history(self, asset_id=None):
        """Get state history for all or specific warehouse"""
        try:
            current_controller = get_controller()
            if not current_controller.influx:
                return {"states": [], "error": "InfluxDB client is not available"}

            if asset_id:
                filter_condition = f' |> filter(fn: (r) => r.warehouse_id == "{asset_id}")'
            else:
                filter_condition = ""

            query = (
                f'from(bucket:"{INFLUX_BUCKET}") '
                '|> range(start: -6h) '
                '|> filter(fn: (r) => r._measurement == "warehouse") '
                f'{filter_condition}'
                '|> filter(fn: (r) => r._field == "temperature" or r._field == "humidity" or r._field == "stock" or r._field == "state_code") '
                '|> pivot(rowKey:["_time", "warehouse_id", "state"], columnKey: ["_field"], valueColumn: "_value") '
                '|> group() '
                '|> sort(columns: ["_time"], desc: true) '
                '|> limit(n: 500)'
            )
            result = current_controller.influx.query_api().query(
                query,
                org=INFLUX_ORG,
            )
            
            states = []
            for table in result:
                for record in table.records:
                    states.append({
                        "time": record.get_time().isoformat(),
                        "warehouse_id": record.values.get("warehouse_id"),
                        "state": record.values.get("state"),
                        "temperature": record.values.get("temperature"),
                        "humidity": record.values.get("humidity"),
                        "stock": record.values.get("stock"),
                        "state_code": record.values.get("state_code")
                    })
            
            return {"states": states}
        except Exception as exc:
            return {"error": str(exc)}
            
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def commands(self):
        """Get pending and recent commands"""
        controller = get_controller()
        with controller.lock:
            pending = []
            for command_id, info in controller.pending_confirmations.items():
                pending.append({
                    "command_id": command_id,
                    "asset_id": info["asset_id"],
                    "sent_at": info["sent_at"],
                    "elapsed": time.time() - info["sent_at"]
                })
                
        return {
            "pending_confirmations": len(pending),
            "pending_commands": pending,
            "total_known_assets": len(controller.known_assets)
        }

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def manual_command(self):
        """Dispatch a manual actuator command from the dashboard."""
        try:
            body = cherrypy.request.body.read().decode()
            data = json.loads(body or "{}")
            asset_id = data.get("asset_id")
            action_name = data.get("action")

            allowed_actions = {
                "fan_on": {"fan": "ON"},
                "fan_off": {"fan": "OFF"},
                "dehumidifier_on": {"dehumidifier": "ON"},
                "dehumidifier_off": {"dehumidifier": "OFF"},
                "heater_on": {"heater": "ON"},
                "heater_off": {"heater": "OFF"},
                "door_open": {"door": "OPEN"},
                "door_close": {"door": "CLOSE"},
                "pause_deliveries": {"pause_deliveries": True},
                "restock_alert": {"restock_alert": True},
                "emergency_shutdown": {"emergency_shutdown": True, "fan": "ON"},
            }

            if not asset_id or action_name not in allowed_actions:
                cherrypy.response.status = 400
                return {
                    "error": "asset_id and a valid action are required",
                    "allowed_actions": sorted(allowed_actions),
                }

            decision = {
                "state": "MANUAL",
                "action": allowed_actions[action_name],
                "timestamp": time.time(),
            }

            controller = get_controller()
            command_id = controller.publish_command(asset_id, decision)
            controller.client.publish(
                f"assets/{asset_id}/events",
                json.dumps({
                    "warehouse_id": asset_id,
                    "event": "MANUAL_COMMAND_REQUESTED",
                    "source": "dashboard",
                    "command_id": command_id,
                    "status": action_name,
                    "timestamp": time.time(),
                }),
                qos=1,
            )
            return {
                "success": True,
                "asset_id": asset_id,
                "action": action_name,
                "command_id": command_id,
                "decision": decision,
            }
        except Exception as exc:
            cherrypy.response.status = 500
            return {"error": str(exc)}


if __name__ == "__main__":
    controller = get_controller()
    threading.Thread(target=controller.start_mqtt, daemon=True).start()
    threading.Thread(target=controller.monitor_devices, daemon=True).start()

    cherrypy.config.update({
        "server.socket_host": "0.0.0.0",
        "server.socket_port": 8001,
        "engine.autoreload.on": False,
    })

    print("Starting CherryPy REST API on port 8001")
    cherrypy.quickstart(RootAPI())
