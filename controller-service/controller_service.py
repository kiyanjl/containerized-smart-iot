import cherrypy
import json
import time
import requests
import threading
import paho.mqtt.client as mqtt
import os
from influxdb_client import InfluxDBClient, Point, WritePrecision

CATALOG_URL = "http://catalog-service:8080"
MQTT_BROKER = "mqtt-broker"
MQTT_PORT = 1883

# InfluxDB Configuration (URL is fixed inside the compose network)
INFLUX_URL = "http://influxdb:8086"
INFLUX_TOKEN = os.environ.get("INFLUX_TOKEN")
INFLUX_ORG = "smart-iot"
INFLUX_BUCKET = "warehouse_metrics"


class SmartController:
    def __init__(self):
        print("🧠 Initializing Smart Controller")

        self.client = mqtt.Client(client_id="smart_controller")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        self.state = {}
        # Initialize InfluxDB client if env vars are present
        self.influx = None
        self.write_api = None
        if INFLUX_URL and INFLUX_TOKEN:
            try:
                self.influx = InfluxDBClient(
                    url=INFLUX_URL,
                    token=INFLUX_TOKEN,
                    org=INFLUX_ORG
                )
                self.write_api = self.influx.write_api(
                    write_precision=WritePrecision.S)
                print("🗄️ Connected to InfluxDB")
            except Exception as e:
                print("❌ Failed to init InfluxDB client:", e)

    # ---------- MQTT ----------
    def on_connect(self, client, userdata, flags, rc):
        print("✅ Controller connected to MQTT broker")
        # Subscribe to both patterns to be tolerant of topic differences
        client.subscribe("assets/+/sensors")
        print("📡 Subscribed to assets/+/sensors")
        client.subscribe("warehouse/+/sensors")
        print("📡 Subscribed to warehouse/+/sensors")

    def on_message(self, client, userdata, msg):
        print("CONTROLLER RECEIVED:", msg.topic, msg.payload)
        try:
            payload = json.loads(msg.payload.decode())
        except Exception as e:
            print("❌ Failed to decode payload:", e)
            return
        asset_id = payload["warehouse_id"]

        print(f"📥 Sensor data received from {asset_id}")

        config = self.get_asset_config(asset_id)
        decision = self.apply_rules(payload, config)

        self.publish_command(asset_id, decision)
        self.state[asset_id] = decision
        # Persist measurement to InfluxDB if configured
        try:
            self.store_influx(asset_id, payload, decision)
            print("📝 Stored measurement to InfluxDB")
        except Exception as e:
            print("❌ Failed to store measurement to InfluxDB:", e)

    # ---------- RULE ENGINE ----------
    def apply_rules(self, data, config):
        rules = config.get("rules", {})

        temp_warning = rules.get("temp_warning", 999)
        temp_critical = rules.get("temp_critical", 999)
        stock_low = rules.get("stock_low", -1)

        state = "NORMAL"
        action = {}

        if data["temperature"] >= temp_critical:
            state = "CRITICAL"
            action["fan"] = "ON"
        elif data["temperature"] >= temp_warning:
            state = "WARNING"

        if data["stock"] <= stock_low:
            state = "WARNING"
            action["restock_alert"] = True

        print(f"➡ Decision: {state}, Actions: {action}")

        return {
            "state": state,
            "action": action,
            "timestamp": time.time()
        }

    # ---------- ACTUATION ----------
    def publish_command(self, asset_id, decision):
        topic = f"assets/{asset_id}/actuator"
        print(f"📤 Publishing to {topic}")
        self.client.publish(topic, json.dumps(decision))

    def store_influx(self, asset_id, data, decision):
        if not self.write_api:
            return
        print("📥 Writing to InfluxDB...")
        point = (
            Point("warehouse")
            .tag("warehouse_id", asset_id)
            .field("temperature", float(data["temperature"]))
            .field("humidity", float(data["humidity"]))
            .field("stock", int(data["stock"]))
            .field("state", decision["state"])
            .time(int(time.time()))
        )

        self.write_api.write(bucket=INFLUX_BUCKET, record=point)
        print("✅ Write complete")

    # ---------- CATALOG ----------
    def get_asset_config(self, asset_id):
        assets = requests.get(f"{CATALOG_URL}/assets").json()
        for a in assets:
            if a["asset_id"] == asset_id:
                return a
        return {}

    # ---------- RUN ----------
    def start_mqtt(self):
        print("🔌 Connecting to MQTT broker...")
        self.client.connect(MQTT_BROKER, MQTT_PORT)
        self.client.loop_forever()


controller = SmartController()


# ---------- REST API ROOT ----------
class RootAPI:
    @cherrypy.expose
    def index(self):
        cherrypy.response.headers["Content-Type"] = "application/json"
        return json.dumps({
            "service": "Smart Controller",
            "endpoints": ["/status"]
        })

    @cherrypy.expose
    def status(self):
        cherrypy.response.headers["Content-Type"] = "application/json"
        return json.dumps(controller.state)


if __name__ == "__main__":
    # Start MQTT in background
    t = threading.Thread(target=controller.start_mqtt, daemon=True)
    t.start()

    # HARD bind for Docker
    cherrypy.config.update({
        "server.socket_host": "0.0.0.0",
        "server.socket_port": 8001,
        "engine.autoreload.on": False
    })

    print("🌐 Starting CherryPy REST API on port 8001")
    cherrypy.quickstart(RootAPI())
