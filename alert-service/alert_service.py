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
last_alert_time = {}
last_alert_state = {}
manual_command_times = {}
last_sensor_values = {}
muted_until = {}

ALERT_RATE_LIMIT = int(os.environ.get("ALERT_RATE_LIMIT", 60))

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
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "healthy"}).encode())
        elif self.path == "/alerts":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"alerts": list(reversed(alert_history[-50:]))}).encode())
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
    
    def log_message(self, format, *args):
        pass

def send_telegram(chat_id, text):
    if not TELEGRAM_TOKEN:
        return False
    try:
        url = f"{TELEGRAM_API_BASE}{TELEGRAM_TOKEN}/sendMessage"
        response = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=15)
        if not response.ok:
            print(f"Telegram API error: {response.text}")
        return response.ok
    except Exception as e:
        print(f"Telegram send failed: {e}")
        return False

def broadcast(msg):
    targets = []
    if TELEGRAM_CHAT_ID:
        targets.append(str(TELEGRAM_CHAT_ID))
    for cid in subscribers:
        if subscribers[cid].get("subscribed", True) and cid not in targets:
            targets.append(cid)
    
    if not targets:
        print("No Telegram targets to broadcast to")
        return
    
    for cid in targets:
        now = time.time()
        if cid in muted_until and muted_until[cid] > now:
            print(f"Skipping broadcast to {cid} (muted)")
            continue
        success = send_telegram(cid, msg)
        if success:
            print(f"Broadcast sent to {cid}")
        else:
            print(f"Broadcast failed for {cid}")

def handle_event(payload):
    event = payload.get("event")
    if event != "MANUAL_COMMAND_REQUESTED":
        return
    
    asset_id = payload.get("warehouse_id", "unknown")
    action = payload.get("status", "manual_action")
    command_id = payload.get("command_id", "")
    timestamp = payload.get("timestamp", time.time())
    time_str = datetime.fromtimestamp(float(timestamp), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    
    display_action = action.replace("_", " ").title()
    
    message = (
        "🛠️ MANUAL COMMAND ISSUED\n\n"
        f"📍 Warehouse: {asset_id}\n"
        f"🎛️ Action: {display_action}\n"
        f"🧾 Command ID: {command_id}\n"
        f"✅ Status: Dispatched to actuator\n"
        f"⏰ Time: {time_str}"
    )
    print(f"MANUAL COMMAND BROADCAST: {asset_id} -> {action}")
    
    alert_history.append({
        "time": time_str,
        "warehouse_id": asset_id,
        "kind": "MANUAL_COMMAND",
        "message": message,
        "command_id": command_id,
        "actions": [action],
    })
    
    manual_command_times[asset_id] = time.time()
    broadcast(message)

def get_warehouse_list():
    try:
        assets = []
        try:
            response = requests.get("http://catalog-service:8080/assets", timeout=5)
            response.raise_for_status()
            assets = response.json()
        except Exception:
            pass
        
        if not assets:
            return "⚠️ No warehouses found in catalog"
        
        msg = "🏭 Active Warehouses:\n\n"
        for asset in assets:
            aid = asset["asset_id"]
            name = asset["name"]
            state = latest_sensor.get(aid, {}).get("state", "UNKNOWN")
            temp = latest_sensor.get(aid, {}).get("temperature", "N/A")
            hum = latest_sensor.get(aid, {}).get("humidity", "N/A")
            stock = latest_sensor.get(aid, {}).get("stock", "N/A")
            
            msg += f"• {name} ({aid})\n"
            if temp != "N/A":
                msg += f"  🌡️ Temp: {temp}°C\n"
            if hum != "N/A":
                msg += f"  💧 Humidity: {hum}%\n"
            if stock != "N/A":
                msg += f"  📦 Stock: {stock}%\n"
            msg += "\n"
        return msg
    except Exception as e:
        return f"❌ Failed to get warehouse list: {e}"

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
    except Exception as e:
        print(f"Telegram bot verification failed: {e}")
    
    print("Telegram command polling started")
    offset = None
    while True:
        try:
            url = f"{TELEGRAM_API_BASE}{TELEGRAM_TOKEN}/getUpdates"
            resp = requests.get(url, params={"offset": offset, "limit": 100, "timeout": 30}, timeout=35)
            for update in resp.json().get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                chat = msg.get("chat", {})
                chat_id = str(chat.get("id"))
                text = msg.get("text", "").lower().strip()
                first_name = chat.get("first_name", "User")
                
                if chat_id not in subscribers:
                    subscribers[chat_id] = {"name": first_name, "subscribed": True}
                
                response_text = ""
                
                if text == "/start":
                    response_text = (
                        f"👋 Welcome {first_name}!\n\n"
                        f"You will now receive all warehouse alerts.\n\n"
                        f"📋 Available Commands:\n"
                        f"/status - System status\n"
                        f"/warehouses - List all warehouses\n"
                        f"/alerts - Recent alerts\n"
                        f"/subscribe - Enable alerts\n"
                        f"/unsubscribe - Disable alerts\n"
                        f"/mute <minutes> - Mute alerts\n"
                        f"/unmute - Unmute alerts\n"
                        f"/help - Show this help"
                    )
                    subscribers[chat_id]["subscribed"] = True
                
                elif text == "/help":
                    response_text = (
                        "📋 Available Commands:\n\n"
                        "/status - System status overview\n"
                        "/warehouses - List all warehouses\n"
                        "/alerts - Show recent alerts\n"
                        "/subscribe - Enable all alerts\n"
                        "/unsubscribe - Disable all alerts\n"
                        "/mute <minutes> - Mute alerts for N minutes\n"
                        "/unmute - Unmute alerts immediately\n"
                        "/help - Show this help message"
                    )
                
                elif text == "/status":
                    num_warehouses = len(latest_sensor)
                    num_alerts = len(alert_history)
                    response_text = (
                        "📊 System Status:\n\n"
                        f"🏭 Warehouses Active: {num_warehouses}\n"
                        f"🔔 Total Alerts: {num_alerts}\n"
                        f"📱 Subscribers: {len(subscribers)}\n"
                        f"✅ System: Healthy"
                    )
                
                elif text == "/warehouses":
                    response_text = get_warehouse_list()
                
                elif text == "/alerts":
                    if not alert_history:
                        response_text = "✅ No alerts yet - everything is quiet!"
                    else:
                        recent = alert_history[-10:][::-1]
                        response_text = "🔔 Recent Alerts:\n\n"
                        for alert in recent:
                            response_text += f"⏰ {alert['time']}\n"
                            response_text += f"📍 {alert['warehouse_id']}\n"
                            response_text += f"📌 {alert['kind']}\n"
                            response_text += "---\n"
                
                elif text.startswith("/mute"):
                    parts = text.split()
                    minutes = 10
                    if len(parts) > 1:
                        try:
                            minutes = int(parts[1])
                        except ValueError:
                            minutes = 10
                    muted_until[chat_id] = time.time() + (minutes * 60)
                    response_text = f"🔇 Alerts muted for {minutes} minutes"
                
                elif text == "/unmute":
                    if chat_id in muted_until:
                        del muted_until[chat_id]
                    response_text = "🔊 Alerts unmuted!"
                
                elif text == "/subscribe":
                    subscribers[chat_id]["subscribed"] = True
                    response_text = "✅ Subscribed to alerts!"
                
                elif text == "/unsubscribe":
                    subscribers[chat_id]["subscribed"] = False
                    response_text = "❌ Unsubscribed from alerts"
                
                elif "pause bot" in text:
                    minutes = 10
                    muted_until[chat_id] = time.time() + (minutes * 60)
                    response_text = "🔇 Bot paused for 10 minutes"
                
                elif "resume bot" in text:
                    if chat_id in muted_until:
                        del muted_until[chat_id]
                    response_text = "▶️ Bot resumed!"
                
                if response_text:
                    send_telegram(chat_id, response_text)
        
        except Exception as e:
            print(f"Telegram polling error: {e}")
        
        time.sleep(2)

def handle_sensor(payload):
    asset_id = payload.get("warehouse_id")
    if not asset_id:
        return
    
    latest_sensor[asset_id] = payload
    
    temp = float(payload.get("temperature", 0))
    hum = float(payload.get("humidity", 0))
    stock = int(payload.get("stock", 0))
    door_open = payload.get("door_open", False)
    
    state = "NORMAL"
    if temp > 40 or temp < -10:
        state = "CRITICAL"
    elif stock > 90:
        state = "OVERLOAD"
    
    last_vals = last_sensor_values.get(asset_id, {})
    temp_trend = ""
    if "temperature" in last_vals:
        diff = temp - last_vals["temperature"]
        if diff > 0.1:
            temp_trend = "📈 rising"
        elif diff < -0.1:
            temp_trend = "📉 falling"
        else:
            temp_trend = "➡️ stable"
    
    stock_trend = ""
    if "stock" in last_vals:
        diff = stock - last_vals["stock"]
        if diff > 0:
            stock_trend = "📈 increasing"
        elif diff < 0:
            stock_trend = "📉 decreasing"
        else:
            stock_trend = "➡️ stable"
    
    last_sensor_values[asset_id] = {"temperature": temp, "humidity": hum, "stock": stock}
    
    now = time.time()
    last_time = last_alert_time.get(asset_id, 0)
    last_state = last_alert_state.get(asset_id, "NORMAL")
    manual_time = manual_command_times.get(asset_id, 0)
    
    if now - manual_time < 60:
        improving = False
        if temp < -10 and "rising" in temp_trend:
            improving = True
        if temp > 40 and "falling" in temp_trend:
            improving = True
        if stock > 90 and "decreasing" in stock_trend:
            improving = True
        
        if improving:
            progress_msg = (
                "✅ SITUATION IMPROVED\n\n"
                f"📍 Warehouse: {asset_id}\n"
                f"✅ State: NORMAL\n\n"
                f"🌡️ Temperature: {temp:.1f}°C {temp_trend}\n"
                f"📦 Stock: {stock}% {stock_trend}\n"
                f"⏰ Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
            if now - manual_time < 5:
                broadcast(progress_msg)
                manual_command_times[asset_id] = 0
            return
    
    if state in ["CRITICAL", "OVERLOAD", "ANOMALY"]:
        if state == last_state and (now - last_time) < ALERT_RATE_LIMIT:
            return
        
        last_alert_time[asset_id] = now
        last_alert_state[asset_id] = state
        
        door = "Open" if door_open else "Closed"
        timestamp = payload.get("timestamp", datetime.now(timezone.utc).timestamp())
        time_str = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        
        actions = []
        if temp > 40:
            actions.append("Turn on fan")
            actions.append("Open door")
            actions.append("Emergency shutdown")
        elif temp < -10:
            actions.append("Turn on heater")
            actions.append("Close door")
        elif hum > 90:
            actions.append("Turn on dehumidifier")
            actions.append("Close door")
        elif stock > 90:
            actions.append("Pause deliveries")
            actions.append("Restock alert")
        
        action_text = "\n".join([f"  🔧 {a}" for a in actions]) if actions else "  Monitor closely"
        
        alert_msg = (
            "🚨 WAREHOUSE ALERT\n\n"
            f"📍 Warehouse: {asset_id}\n"
            f"⚠️ State: {state}\n\n"
            f"🌡️ Temperature: {temp:.1f}°C {temp_trend}\n"
            f"💧 Humidity: {hum:.1f}%\n"
            f"📦 Stock: {stock}% {stock_trend}\n"
            f"🚪 Door: {door}\n\n"
            f"🔧 Suggested Actions:\n{action_text}\n\n"
            f"⏰ Time: {time_str}"
        )
        
        print(f"ALERT BROADCAST: {asset_id} -> {state}")
        alert_history.append({
            "time": time_str,
            "warehouse_id": asset_id,
            "kind": state,
            "temperature": temp,
            "humidity": hum,
            "stock": stock,
            "message": alert_msg,
            "actions": actions,
        })
        broadcast(alert_msg)
    else:
        if last_state != "NORMAL":
            msg = (
                "✅ SITUATION IMPROVED\n\n"
                f"📍 Warehouse: {asset_id}\n"
                f"✅ State: NORMAL\n\n"
                f"🌡️ Temperature: {temp:.1f}°C\n"
                f"📦 Stock: {stock}%\n"
                f"⏰ Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
            print(f"{asset_id} returned to NORMAL")
            last_alert_state[asset_id] = "NORMAL"
            broadcast(msg)

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("MQTT connected")
        client.subscribe("assets/#")
        client.subscribe("system/device_status")
    else:
        print(f"MQTT connection failed: {rc}")

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        if msg.topic.endswith("/sensors"):
            handle_sensor(payload)
        elif msg.topic.endswith("/events"):
            handle_event(payload)
    except Exception as e:
        print(f"MQTT message error: {e}")

def http_server():
    print(f"HTTP server starting on port {ALERT_PORT}")
    HTTPServer(("0.0.0.0", ALERT_PORT), Handler).serve_forever()

def main():
    print("Alert Service starting...")
    threading.Thread(target=http_server, daemon=True).start()
    threading.Thread(target=telegram_poll, daemon=True).start()
    
    client = mqtt.Client(client_id="alert_service")
    client.on_connect = on_connect
    client.on_message = on_message
    
    connected = False
    while not connected:
        try:
            client.connect(MQTT_BROKER, MQTT_PORT, 60)
            connected = True
            print("Connected to MQTT broker")
        except Exception as e:
            print(f"Waiting for MQTT broker... {e}")
            time.sleep(2)
    
    client.loop_forever()

if __name__ == "__main__":
    main()
