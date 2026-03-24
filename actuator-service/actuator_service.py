import json
import time

import paho.mqtt.client as mqtt

MQTT_BROKER = "mqtt-broker"
MQTT_PORT = 1883


def on_connect_status(rc):
    if rc == 0:
        print("Actuator connected to MQTT broker")
    else:
        print("MQTT connection failed:", rc)


class ActuatorService:
    def __init__(self):
        print("Actuator Service starting...")

        self.client = mqtt.Client(client_id="actuator_service")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def on_connect(self, client, userdata, flags, rc):
        on_connect_status(rc)

        topic = "assets/+/actuator"
        print("ACTUATOR SUBSCRIBED TO", topic)
        client.subscribe(topic)
        print("Subscribed to assets/+/actuator")

        client.subscribe("assets/+/sensors")
        print("Subscribed to assets/+/sensors for edge safety")

    def on_message(self, client, userdata, msg):
        print("ACTUATOR RECEIVED:", msg.topic, msg.payload)
        try:
            payload = json.loads(msg.payload.decode())
        except json.JSONDecodeError as exc:
            print("Failed to decode actuator payload:", exc)
            return

        asset_id = msg.topic.split("/")[1]

        if msg.topic.endswith("/sensors"):
            if payload.get("temperature", 0) > 40:
                print("Edge safety triggered: temperature high")
                self.execute_actions({"fan": "ON"})
            return

        command_id = payload.get("command_id")
        decision = payload.get("action", {})

        print(f"\nACTUATION RECEIVED for {asset_id}")
        print(f"State: {decision.get('state')}")
        print(f"Actions: {decision.get('action')}")

        self.execute_actions(decision.get("action", {}))

        confirmation = {
            "warehouse_id": asset_id,
            "command_id": command_id,
            "status": "SUCCESS",
            "timestamp": time.time(),
        }

        client.publish(
            f"assets/{asset_id}/events",
            json.dumps(confirmation),
            qos=1,
            retain=True,
        )
        print(f"Sent confirmation for command {command_id}")

    def execute_actions(self, actions):
        if actions.get("emergency_shutdown"):
            print("EMERGENCY SHUTDOWN triggered - sensor anomaly detected!")

        if actions.get("fan") == "ON":
            print("Fan turned ON")

        if actions.get("dehumidifier") == "ON":
            print("Dehumidifier turned ON")

        if actions.get("restock_alert"):
            print("Restock alert triggered")

        if actions.get("pause_deliveries"):
            print("Warehouse overload detected")
            print("Incoming deliveries paused")

        if not actions:
            print("No action needed")

    def start(self):
        self.client.connect(MQTT_BROKER, MQTT_PORT)
        self.client.loop_forever()


if __name__ == "__main__":
    ActuatorService().start()
