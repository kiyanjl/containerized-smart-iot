import json
import paho.mqtt.client as mqtt

MQTT_BROKER = "mqtt-broker"
MQTT_PORT = 1883


class ActuatorService:

    def __init__(self):
        print("🔧 Actuator Service starting...")

        self.client = mqtt.Client(client_id="actuator_service")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def on_connect(self, client, userdata, flags, rc):
        print("✅ Actuator connected to MQTT broker")

        # 🔥 FIXED TOPIC
        topic = "assets/+/actuator"
        print("ACTUATOR SUBSCRIBED TO", topic)
        client.subscribe(topic)
        print("📡 Subscribed to assets/+/actuator")

    def on_message(self, client, userdata, msg):
        print("ACTUATOR RECEIVED:", msg.topic, msg.payload)
        payload = json.loads(msg.payload.decode())
        asset_id = msg.topic.split("/")[1]

        print(f"\n📦 ACTUATION RECEIVED for {asset_id}")
        print(f"State: {payload.get('state')}")
        print(f"Actions: {payload.get('action')}")

        self.execute_actions(payload.get("action", {}))

    def execute_actions(self, actions):
        if actions.get("fan") == "ON":
            print("🌀 Fan turned ON")

        if actions.get("restock_alert"):
            print("📢 Restock alert triggered")

        if not actions:
            print("✅ No action needed")

    def start(self):
        self.client.connect(MQTT_BROKER, MQTT_PORT)
        self.client.loop_forever()


if __name__ == "__main__":
    ActuatorService().start()
