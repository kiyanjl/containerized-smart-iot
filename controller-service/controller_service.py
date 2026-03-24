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
        self.state[asset_id] = decision

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
        self.client.publish(topic, json.dumps(command), retain=True)
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

    def store_influx(self, asset_id, data, decision):
        if not self.write_api:
            return

        print("Writing to InfluxDB...")
        state = decision["state"]

        point = (
            Point("warehouse")
            .tag("warehouse_id", asset_id)
            .tag("state", state)
            .field("temperature", float(data["temperature"]))
            .field("humidity", float(data["humidity"]))
            .field("stock", int(data["stock"]))
            .field("state_code", STATE_CODES.get(state, -1))
            .time(time.time_ns(), WritePrecision.NS)
        )

        self.write_api.write(
            bucket=INFLUX_BUCKET,
            org=INFLUX_ORG,
            record=point,
        )

        print("Write complete")

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

    def start_mqtt(self):
        print("Connecting to MQTT broker...")
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
            "endpoints": ["/status", "/health"],
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
