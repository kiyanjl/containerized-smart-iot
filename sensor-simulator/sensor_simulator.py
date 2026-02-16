import time
import json
import random
import requests
import paho.mqtt.client as mqtt

# ==============================
# CONFIGURATION
# ==============================
CATALOG_URL = "http://catalog-service:8080"
PUBLISH_INTERVAL = 2  # seconds

# ==============================
# FETCH CONFIG FROM CATALOG
# ==============================


def get_assets():
    try:
        r = requests.get(f"{CATALOG_URL}/assets")
        return r.json()
    except Exception as e:
        print("❌ Cannot reach Catalog Service:", e)
        return []


def get_broker():
    try:
        broker = requests.get(f"{CATALOG_URL}/broker").json()
        port = int(requests.get(f"{CATALOG_URL}/port").json())
        return broker, port
    except:
        return "mqtt-broker", 1883

# ==============================
# SENSOR DATA GENERATION
# ==============================


def generate_sensor_data(asset):
    return {
        # 🔑 MUST MATCH CONTROLLER EXPECTATION
        "warehouse_id": asset["asset_id"],
        "temperature": round(random.uniform(10, 40), 1),
        "humidity": round(random.uniform(30, 90), 1),
        "stock": random.randint(0, 100),        # 🔑 MATCH controller field name
        "door_open": random.choice([0, 1]),
        "timestamp": time.time()
    }

# ==============================
# MAIN LOOP
# ==============================


def main():
    assets = get_assets()
    if not assets:
        print("⚠ No assets found in catalog.")
        return

    broker, port = get_broker()
    client = mqtt.Client(client_id="sensor_simulator")
    client.connect(broker, port, 60)
    client.loop_start()

    print("📡 Sensor Simulator Started")

    while True:
        for asset in assets:
            asset_id = asset["asset_id"]

            # Use mqtt_sensor_topic from catalog if available, fallback to assets/{id}/sensors
            topic = asset.get("mqtt_sensor_topic",
                              f"assets/{asset_id}/sensors")
            print("SENSOR TOPIC:", topic)

            payload = generate_sensor_data(asset)
            client.publish(topic, json.dumps(payload))

            print(f"➡ Published to {topic}: {payload}")

        time.sleep(PUBLISH_INTERVAL)


if __name__ == "__main__":
    main()
