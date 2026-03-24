import json
import random
import time

import paho.mqtt.client as mqtt
import requests


CATALOG_URL = "http://catalog-service:8080"
PUBLISH_INTERVAL = 2
ANOMALY_PROBABILITY = 0.04
ANOMALY_BURST_COUNT = 4
HEARTBEAT_INTERVAL = 60

NORMAL_PROFILES = {
    "warehouse_cold": {
        "temperature": (2, 7),
        "humidity": (55, 80),
        "stock": (35, 95),
    },
    "warehouse_standard": {
        "temperature": (18, 28),
        "humidity": (35, 75),
        "stock": (10, 85),
    },
    "warehouse_hazard": {
        "temperature": (12, 19),
        "humidity": (30, 65),
        "stock": (10, 80),
    },
}

DEFAULT_NORMAL_PROFILE = {
    "temperature": (18, 28),
    "humidity": (35, 75),
    "stock": (10, 85),
}

ANOMALY_PROFILES = {
    "warehouse_cold": [
        {"type": "COMPRESSOR_FAILURE", "temperature": (18, 30)},
        {"type": "FREEZER_OVERCOOL", "temperature": (-15, -8)},
    ],
    "warehouse_standard": [
        {"type": "HEAT_SPIKE", "temperature": (46, 55)},
        {"type": "HUMIDITY_FLOOD", "humidity": (96, 100)},
    ],
    "warehouse_hazard": [
        {"type": "CHEMICAL_HEAT", "temperature": (47, 60)},
        {"type": "SENSOR_FAULT", "temperature": (-20, -12)},
    ],
}


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Sensor connected to MQTT broker")
    else:
        print("MQTT connection failed:", rc)


def get_assets():
    try:
        response = requests.get(f"{CATALOG_URL}/assets", timeout=5)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        print("Cannot reach Catalog Service:", exc)
        return []


def get_broker():
    try:
        broker_response = requests.get(f"{CATALOG_URL}/broker", timeout=5)
        port_response = requests.get(f"{CATALOG_URL}/port", timeout=5)
        broker_response.raise_for_status()
        port_response.raise_for_status()
        return broker_response.json(), int(port_response.json())
    except Exception:
        return "mqtt-broker", 1883


class AnomalyState:
    def __init__(self):
        self.remaining = 0
        self.profile = None

    def maybe_start(self, asset_id):
        if self.remaining > 0:
            return False

        profiles = ANOMALY_PROFILES.get(asset_id, [])
        if profiles and random.random() < ANOMALY_PROBABILITY:
            self.profile = random.choice(profiles)
            self.remaining = ANOMALY_BURST_COUNT
            return True

        return False

    def consume(self):
        if self.remaining <= 0 or not self.profile:
            return None

        profile = self.profile
        self.remaining -= 1
        if self.remaining == 0:
            self.profile = None

        return profile


def generate_sensor_data(asset):
    profile = NORMAL_PROFILES.get(asset["asset_id"], DEFAULT_NORMAL_PROFILE)
    return {
        "warehouse_id": asset["asset_id"],
        "temperature": round(random.uniform(*profile["temperature"]), 1),
        "humidity": round(random.uniform(*profile["humidity"]), 1),
        "stock": random.randint(*profile["stock"]),
        "door_open": random.choice([0, 1]),
        "timestamp": time.time(),
    }


def apply_anomaly_profile(payload, profile):
    anomaly_payload = payload.copy()

    if "temperature" in profile:
        low, high = profile["temperature"]
        anomaly_payload["temperature"] = round(random.uniform(low, high), 1)

    if "humidity" in profile:
        low, high = profile["humidity"]
        anomaly_payload["humidity"] = round(random.uniform(low, high), 1)

    return anomaly_payload


def main():
    broker, port = get_broker()
    client = mqtt.Client(client_id="sensor_simulator")
    client.will_set(
        "system/device_status",
        json.dumps({"status": "sensor_offline"}),
        qos=1,
        retain=True,
    )
    client.on_connect = on_connect
    client.connect(broker, port, 60)
    client.loop_start()

    print("Sensor Simulator Started")

    last_heartbeat = time.time()
    anomaly_states = {}

    while True:
        assets = get_assets()
        if not assets:
            print("No assets found in catalog.")
            time.sleep(PUBLISH_INTERVAL)
            continue

        active_assets = {asset["asset_id"] for asset in assets}
        anomaly_states = {
            asset_id: state
            for asset_id, state in anomaly_states.items()
            if asset_id in active_assets
        }

        current_time = time.time()

        for asset in assets:
            asset_id = asset["asset_id"]
            state = anomaly_states.setdefault(asset_id, AnomalyState())
            topic = asset.get("mqtt_sensor_topic", f"assets/{asset_id}/sensors")

            payload = generate_sensor_data(asset)
            burst_started = state.maybe_start(asset_id)
            anomaly_profile = state.consume()
            if anomaly_profile:
                payload = apply_anomaly_profile(payload, anomaly_profile)

            client.publish(topic, json.dumps(payload), qos=1)
            print(f"Published to {topic}: {payload}")

            if anomaly_profile and burst_started:
                event_payload = {
                    "warehouse_id": asset_id,
                    "event": "ANOMALY_DETECTED",
                    "anomaly_type": anomaly_profile["type"],
                    "source": "sensor_simulator",
                    "timestamp": payload["timestamp"],
                }
                client.publish(
                    f"assets/{asset_id}/events",
                    json.dumps(event_payload),
                    qos=1,
                )
                print(
                    f"Published anomaly event for {asset_id}: "
                    f"{anomaly_profile['type']}"
                )

        if current_time - last_heartbeat >= HEARTBEAT_INTERVAL:
            for asset in assets:
                asset_id = asset["asset_id"]
                heartbeat = {
                    "warehouse_id": asset_id,
                    "timestamp": time.time(),
                }
                client.publish(
                    f"assets/{asset_id}/heartbeat",
                    json.dumps(heartbeat),
                    qos=1,
                )
                print(f"Heartbeat sent for {asset_id}")

            last_heartbeat = current_time

        time.sleep(PUBLISH_INTERVAL)


if __name__ == "__main__":
    main()
