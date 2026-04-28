#!/usr/bin/env python3
import json
import os
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
import paho.mqtt.client as mqtt
import requests

MQTT_BROKER = os.environ.get("MQTT_BROKER", "mqtt-broker")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
ALERT_PORT = int(os.environ.get("ALERT_PORT", 5002))
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_API_BASE = "https://api.telegram.org/bot"

alert_history = []
latest_sensor = {}
subscribers = {}

class Handler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"status": "healthy"}).encode())
        elif self.path == "/alerts":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"alerts": list(reversed(alert_history))}).encode())
        elif self.path == "/status":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "warehouses_active": len(latest_sensor),
                "alerts_total": len(alert_history),
                "subscribers": len(subscribers),
                "telegram_configured": bool(TELEGRAM_TOKEN),
            }).encode())
        else:
            self.send_response(404)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Not found"}).encode())

def send_telegram(chat_id, text):
    if not TELEGRAM_TOKEN:
        return False
    try:
        url = f"{TELEGRAM_API_BASE}{TELEGRAM_TOKEN}/sendMessage"
        response = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
        return response.ok
    except Exception as exc:
        print(f"Telegram send failed: {exc}")
        return False

def broadcast(msg):
    targets = [cid for cid, info in subscribers.items() if info.get("subscribed")]
    if TELEGRAM_CHAT_ID and TELEGRAM_CHAT_ID not in targets:
        targets.append(TELEGRAM_CHAT_ID)
    for cid in targets:
        send_telegram(cid, msg)

def handle_event(payload):
    event = payload.get("event")
    if event != "MANUAL_COMMAND_REQUESTED":
        return

    asset_id = payload.get("warehouse_id", "unknown")
    action = payload.get("status", "manual_action")
    command_id = payload.get("command_id", "")
    timestamp = payload.get("timestamp", datetime.now(timezone.utc).timestamp())
    time_str = datetime.fromtimestamp(float(timestamp), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    message = (
        "🛠️ MANUAL COMMAND SENT\n\n"
        f"📍 Warehouse: {asset_id}\n"
        f"🎛️ Action: {action}\n"
        f"🧾 Command ID: {command_id}\n"
        f"✅ Status: dispatched to actuator\n"
        f"⏰ Time: {time_str}"
    )
    print(f"MANUAL COMMAND: {asset_id} -> {action}")
    alert_history.append({
        "time": time_str,
        "warehouse_id": asset_id,
        "kind": "MANUAL_COMMAND",
        "message": message,
        "command_id": command_id,
        "actions": [action],
    })
    broadcast(message)

def telegram_poll():
    if not TELEGRAM_TOKEN:
        print("Telegram disabled - no token")
        return
    try:
        resp = requests.get(f"{TELEGRAM_API_BASE}{TELEGRAM_TOKEN}/getMe", timeout=10)
        if resp.ok:
            print("Telegram bot verified")
        else:
            print(f"Telegram bot verification failed: {resp.text[:200]}")
    except Exception as exc:
        print(f"Telegram bot verification failed: {exc}")
    print("Telegram command polling started")
    offset = None
    while True:
        try:
            url = f"{TELEGRAM_API_BASE}{TELEGRAM_TOKEN}/getUpdates"
            resp = requests.get(url, params={"offset": offset, "limit": 100}, timeout=30)
            for update in resp.json().get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                chat = msg.get("chat", {})
                chat_id = chat.get("id")
                text = msg.get("text", "").lower().strip()
                name = chat.get("first_name", "user")
                if not chat_id:
                    continue
                subscribers[str(chat_id)] = {"name": name, "subscribed": True}
                
                if text == "/start":
                    welcome = f"Welcome {name}! You will receive CRITICAL and OVERLOAD alerts. Commands: /status /alerts /subscribe /unsubscribe /help"
                    send_telegram(chat_id, welcome)
                elif text == "/help":
                    help_text = "/start - Welcome\n/status - System status\n/alerts - Recent alerts\n/subscribe - Enable alerts\n/unsubscribe - Disable alerts\n/help - This message"
                    send_telegram(chat_id, help_text)
                elif text == "/status":
                    status = f"{len(latest_sensor)} warehouses active, {len(alert_history)} total alerts"
                    send_telegram(chat_id, status)
                elif text == "/alerts":
                    if not alert_history:
                        send_telegram(chat_id, "No alerts yet")
                    else:
                        recent = alert_history[-10:][::-1]
                        msg_text = "Recent alerts:\n"
                        for alert in recent:
                            msg_text += f"[{alert['kind']}] {alert['message'][:50]}...\n"
                        send_telegram(chat_id, msg_text)
                elif text == "/subscribe":
                    subscribers[str(chat_id)]["subscribed"] = True
                    send_telegram(chat_id, "Subscribed to alerts!")
                elif text == "/unsubscribe":
                    subscribers[str(chat_id)]["subscribed"] = False
                    send_telegram(chat_id, "Unsubscribed from alerts")
        except Exception as e:
            print(f"Telegram error: {e}")
        import time
        time.sleep(5)

def http_server():
    print(f"Starting HTTP server on port {ALERT_PORT}")
    HTTPServer(("0.0.0.0", ALERT_PORT), Handler).serve_forever()

def handle_sensor(payload):
    asset_id = payload.get("warehouse_id")
    if not asset_id:
        return
    temp = float(payload.get("temperature", 0))
    stock = int(payload.get("stock", 0))
    latest_sensor[asset_id] = payload
    
    state = "NORMAL"
    if temp > 40:
        state = "CRITICAL"
    elif stock > 90:
        state = "OVERLOAD"
    
    if state in ["CRITICAL", "OVERLOAD", "ANOMALY"]:
        # Get humidity
        humidity = float(payload.get("humidity", 0))
        door = "Open" if payload.get("door_open") else "Closed"
        timestamp = payload.get("timestamp", datetime.now(timezone.utc).timestamp())
        time_str = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        
        # Determine suggested action based on state
        actions = []
        if temp > 40:
            actions.append("emergency_shutdown")
            actions.append("fan")
        elif temp < -10:
            actions.append("heater")
        elif stock > 90:
            actions.append("pause_deliveries")
            actions.append("restock")
        
        action_text = ", ".join(actions) if actions else "monitor"
        
        # Create detailed alert message matching user's preferred format
        alert_msg = (
            f"🚨 WAREHOUSE ALERT\n\n"
            f"📍 Warehouse: {asset_id}\n"
            f"⚠️ State: {state}\n\n"
            f"🌡️ Temperature: {temp:.1f}°C\n"
            f"💧 Humidity: {humidity:.1f}%\n"
            f"📦 Stock: {stock}%\n"
            f"🚪 Door: {door}\n\n"
            f"🔧 Suggested Action: {action_text}\n"
            f"⏰ Time: {time_str}"
        )
        
        print(f"ALERT: {asset_id} -> {state}")
        alert_history.append({
            "time": time_str,
            "warehouse_id": asset_id,
            "kind": state,
            "temperature": temp,
            "humidity": humidity,
            "stock": stock,
            "message": alert_msg,
            "actions": actions
        })
        broadcast(alert_msg)

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("MQTT connected")
        client.subscribe("assets/#")
        client.subscribe("system/device_status")

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        if msg.topic.endswith("/sensors"):
            handle_sensor(payload)
        elif msg.topic.endswith("/events"):
            handle_event(payload)
    except Exception as e:
        print(f"Error: {e}")

def main():
    print("Alert Service starting")
    threading.Thread(target=http_server, daemon=True).start()
    threading.Thread(target=telegram_poll, daemon=True).start()
    client = mqtt.Client(client_id="alert_service")
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_forever()

if __name__ == "__main__":
    main()
