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


class ActuatorState:
    def __init__(self):
        self.fan = "OFF"
        self.heater = "OFF"
        self.dehumidifier = "OFF"
        self.door = "CLOSE"
        self.pause_deliveries = False
        self.temp_offset = 0.0
        self.humidity_offset = 0.0
        self.stock_trend = 0.5  # Natural growth

    def update(self, action):
        if "fan" in action:
            self.fan = action["fan"]
        if "heater" in action:
            self.heater = action["heater"]
        if "dehumidifier" in action:
            self.dehumidifier = action["dehumidifier"]
        if "door" in action:
            self.door = action["door"]
        if "pause_deliveries" in action:
            self.pause_deliveries = action["pause_deliveries"]

    def step(self):
        # Temperature effects
        if self.heater == "ON":
            self.temp_offset += 1.5  # Faster heating
        elif self.fan == "ON":
            self.temp_offset -= 1.2  # Faster cooling
        else:
            # Gradually return to 0 (ambient) - slightly faster ambient return
            self.temp_offset *= 0.90

        # Humidity effects
        if self.dehumidifier == "ON":
            self.humidity_offset -= 2.0  # Faster drying
        elif self.door == "OPEN":
            self.humidity_offset += 0.8  # Door open raises humidity
        else:
            self.humidity_offset *= 0.92

        # Limit offsets to realistic ranges
        self.temp_offset = max(-30.0, min(70.0, self.temp_offset))
        self.humidity_offset = max(-50.0, min(50.0, self.humidity_offset))


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Sensor connected to MQTT broker")
        client.subscribe("assets/+/actuator")
        client.subscribe("assets/+/events")
    else:
        print("MQTT connection failed:", rc)


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        asset_id = msg.topic.split("/")[1]
        
        # We need access to actuator_states
        states = userdata["actuator_states"]
        state = states.setdefault(asset_id, ActuatorState())

        if msg.topic.endswith("/actuator"):
            action = payload.get("action", {}).get("action", {})
            state.update(action)
            print(f"Simulator: Updated actuator state for {asset_id}: {action}")
        
        elif msg.topic.endswith("/events"):
            # Actuator confirmations also update state
            if payload.get("status") == "SUCCESS":
                # For manual commands, the status might contain the action
                pass # Already handled by /actuator topic usually
    except Exception as e:
        print(f"Simulator MQTT error: {e}")


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


def generate_sensor_data(asset, actuator_state):
    # Get profile based on warehouse type or asset_id
    asset_type = asset.get("type", "standard")
    
    # Type-based profiles
    type_profiles = {
        "cold": {"temperature": (2, 7), "humidity": (55, 80), "stock": (35, 95)},
        "standard": {"temperature": (18, 28), "humidity": (35, 75), "stock": (10, 85)},
        "hazard": {"temperature": (12, 19), "humidity": (30, 65), "stock": (10, 80)},
    }
    
    # First try asset_id, then type, then default
    profile = NORMAL_PROFILES.get(asset["asset_id"], type_profiles.get(asset_type, DEFAULT_NORMAL_PROFILE))
    
    # Base values
    temp = random.uniform(*profile["temperature"]) + actuator_state.temp_offset
    hum = random.uniform(*profile["humidity"]) + actuator_state.humidity_offset
    
    # Stock logic
    stock = profile.get("_last_stock", random.randint(*profile["stock"]))
    if not actuator_state.pause_deliveries:
        stock += random.randint(0, 3)
    else:
        stock -= random.randint(0, 2)
    
    stock = max(5, min(100, stock))
    profile["_last_stock"] = stock

    return {
        "warehouse_id": asset["asset_id"],
        "temperature": round(temp, 1),
        "humidity": round(hum, 1),
        "stock": stock,
        "door_open": 1 if actuator_state.door == "OPEN" else 0,
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
    actuator_states = {}
    
    client = mqtt.Client(client_id="sensor_simulator", userdata={"actuator_states": actuator_states})
    client.will_set(
        "system/device_status",
        json.dumps({"status": "sensor_offline"}),
        qos=1,
        retain=True,
    )
    client.on_connect = on_connect
    client.on_message = on_message
    
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
            act_state = actuator_states.setdefault(asset_id, ActuatorState())
            
            # Step the simulation for this asset
            act_state.step()
            
            topic = asset.get("mqtt_sensor_topic", f"assets/{asset_id}/sensors")

            payload = generate_sensor_data(asset, act_state)
            burst_started = state.maybe_start(asset_id)
            anomaly_profile = state.consume()
            if anomaly_profile:
                payload = apply_anomaly_profile(payload, anomaly_profile)

            client.publish(topic, json.dumps(payload), qos=1)
            # print(f"Published to {topic}: {payload}")

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
