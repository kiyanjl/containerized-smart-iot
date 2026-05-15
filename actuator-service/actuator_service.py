import json
import time
from collections import deque
import threading

import paho.mqtt.client as mqtt

MQTT_BROKER = "mqtt-broker"
MQTT_PORT = 1883

# Command retry configuration
COMMAND_RETRY_DELAY = 15  # Wait 15 seconds before retrying
COMMAND_MAX_RETRIES = 3    # Maximum number of retries

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
        
        # ✅ FIX #13: Track received commands for confirmation
        self.received_commands = set()  # Track command IDs we've processed
        self.pending_commands = {}     # Track commands we're waiting to execute

    def on_connect(self, client, userdata, flags, rc):
        on_connect_status(rc)

        topic = "assets/+/actuator"
        print("ACTUATOR SUBSCRIBED TO", topic)
        client.subscribe(topic)
        print("Subscribed to assets/+/actuator")

        client.subscribe("assets/+/sensors")
        print("Subscribed to assets/+/sensors for edge safety")
        
        # Subscribe to events for confirmation feedback
        client.subscribe("assets/+/events")
        print("Subscribed to assets/+/events for confirmation feedback")

    def on_message(self, client, userdata, msg):
        print("ACTUATOR RECEIVED:", msg.topic, msg.payload)
        try:
            payload = json.loads(msg.payload.decode())
        except json.JSONDecodeError as exc:
            print("Failed to decode actuator payload:", exc)
            return

        asset_id = msg.topic.split("/")[1]

        # Handle sensor data for edge safety
        if msg.topic.endswith("/sensors"):
            if payload.get("temperature", 0) > 40:
                print("Edge safety triggered: temperature high")
                self.execute_actions({"fan": "ON"})
            return
            
        # Handle command confirmation feedback
        if msg.topic.endswith("/events"):
            if payload.get("event"):
                print(f"Ignoring informational event: {payload.get('event')}")
                return

            warehouse_id = payload.get("warehouse_id")
            command_id = payload.get("command_id")
            status = payload.get("status")
            print(f"Received confirmation for command {command_id} from {warehouse_id}: {status}")
            
            # Remove from pending commands if confirmed
            if warehouse_id in self.pending_commands and command_id in self.pending_commands[warehouse_id]:
                del self.pending_commands[warehouse_id][command_id]
                if not self.pending_commands[warehouse_id]:  # Clean up empty dict
                    del self.pending_commands[warehouse_id]
            return

        # Handle actuation commands
        command_id = payload.get("command_id")
        if not command_id:
            print("Received actuation command without command_id - ignoring")
            return
            
        # Skip if we've already processed this command (idempotency)
        command_key = f"{asset_id}:{command_id}"
        if command_key in self.received_commands:
            print(f"Duplicate command received: {command_id} - ignoring")
            return
            
        # Mark as received
        self.received_commands.add(command_key)
        
        # Store in pending commands for retry tracking
        if asset_id not in self.pending_commands:
            self.pending_commands[asset_id] = {}
        self.pending_commands[asset_id][command_id] = {
            "payload": payload,
            "timestamp": time.time(),
            "retries": 0
        }

        decision = payload.get("action", {})

        print(f"\nACTUATION RECEIVED for {asset_id}")
        print(f"Command ID: {command_id}")
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
        elif actions.get("fan") == "OFF":
            print("Fan turned OFF")

        if actions.get("heater") == "ON":
            print("Heater turned ON")
        elif actions.get("heater") == "OFF":
            print("Heater turned OFF")

        if actions.get("door") == "OPEN":
            print("Door OPENED")
        elif actions.get("door") == "CLOSE":
            print("Door CLOSED")

        if actions.get("dehumidifier") == "ON":
            print("Dehumidifier turned ON")
        elif actions.get("dehumidifier") == "OFF":
            print("Dehumidifier turned OFF")

        if actions.get("restock_alert"):
            print("Restock alert triggered")

        if actions.get("pause_deliveries"):
            print("Warehouse overload detected")
            print("Incoming deliveries paused")

        if not actions:
            print("No action needed")

    def start(self):
        self.client.connect(MQTT_BROKER, MQTT_PORT)

        def retry_unconfirmed():
            while True:
                time.sleep(COMMAND_RETRY_DELAY)
                now = time.time()
                
                for asset_id, commands in dict(self.pending_commands).items():
                    for command_id, cmd_data in dict(commands).items():
                        # Check if we should retry
                        if (now - cmd_data["timestamp"] > COMMAND_RETRY_DELAY and 
                            cmd_data["retries"] < COMMAND_MAX_RETRIES):
                            
                            # Re-send the command
                            payload = cmd_data["payload"]
                            print(f"Retrying command {command_id} for {asset_id} (attempt {cmd_data['retries'] + 1})")
                            
                            self.client.publish(
                                f"assets/{asset_id}/actuator",
                                json.dumps(payload),
                                qos=1
                            )
                            
                            # Update retry count
                            self.pending_commands[asset_id][command_id]["retries"] += 1
                            self.pending_commands[asset_id][command_id]["timestamp"] = now
                            
                        elif cmd_data["retries"] >= COMMAND_MAX_RETRIES:
                            # Remove after max retries
                            print(f"Max retries reached for command {command_id} - giving up")
                            del self.pending_commands[asset_id][command_id]
                            if not self.pending_commands[asset_id]:
                                del self.pending_commands[asset_id]
        
        # Start retry thread
        retry_thread = threading.Thread(target=retry_unconfirmed, daemon=True)
        retry_thread.start()
        self.client.loop_forever()


if __name__ == "__main__":
    ActuatorService().start()
