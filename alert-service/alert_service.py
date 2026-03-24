import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt
import requests
from rule_engine import evaluate_rules

MQTT_BROKER = os.environ.get("MQTT_BROKER", "mqtt-broker")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
CATALOG_URL = os.environ.get("CATALOG_URL", "http://catalog-service:8080")
CONTROLLER_URL = os.environ.get("CONTROLLER_URL", "http://controller-service:8001")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
BOOTSTRAP_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
DATA_DIR = Path(os.environ.get("ALERT_DATA_DIR", "data"))
STATE_FILE = DATA_DIR / "alert_state.json"
READY_FILE = Path("/tmp/alert-service.ready")
HEALTH_FILE = Path("/tmp/alert-service.heartbeat")
POLL_TIMEOUT_SECONDS = 25
HTTP_TIMEOUT_SECONDS = 10
ALERT_HISTORY_LIMIT = 200
ALERT_STATES = {"ANOMALY", "CRITICAL", "OVERLOAD"}
DEVICE_EVENTS = {"DEVICE_OFFLINE", "DEVICE_ONLINE"}
TELEGRAM_MENU = {
    "keyboard": [
        ["/status", "/warehouses"],
        ["/alerts", "/help"],
        ["/mute 10", "/unmute"],
        ["Pause Bot 10m", "Resume Bot"],
    ],
    "resize_keyboard": True,
}


class AlertService:
    def __init__(self):
        if not TELEGRAM_TOKEN:
            raise RuntimeError("TELEGRAM_TOKEN is required")

        self.telegram_api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
        self.lock = threading.Lock()
        self.rules_cache = {}
        self.latest_sensor = {}
        self.latest_state = {}
        self.latest_anomaly_type = {}
        self.device_presence = {}
        self.last_device_event = {}
        self.subscribers = {}
        self.alert_history = []
        self.bot_pause_until = 0
        self.update_offset = None
        self.bot_username = "unknown"

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.load_state()

        if BOOTSTRAP_CHAT_ID:
            self.subscribers.setdefault(
                str(BOOTSTRAP_CHAT_ID),
                {"label": "bootstrap", "mute_until": 0},
            )

        self.client = mqtt.Client(client_id="alert_service")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def load_state(self):
        if not STATE_FILE.exists():
            return

        try:
            payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            self.subscribers = payload.get("subscribers", {})
            self.alert_history = payload.get("alert_history", [])[-ALERT_HISTORY_LIMIT:]
            self.bot_pause_until = float(payload.get("bot_pause_until", 0) or 0)
            self.update_offset = payload.get("update_offset")
        except Exception as exc:
            print("Failed to load alert state:", exc)

    def save_state(self):
        payload = {
            "subscribers": self.subscribers,
            "alert_history": self.alert_history[-ALERT_HISTORY_LIMIT:],
            "bot_pause_until": self.bot_pause_until,
            "update_offset": self.update_offset,
        }
        STATE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def is_bot_paused(self):
        return time.time() < self.bot_pause_until

    def bot_pause_text(self):
        if not self.is_bot_paused():
            return "ACTIVE"

        remaining_seconds = max(0, int(self.bot_pause_until - time.time()))
        remaining_minutes = max(1, (remaining_seconds + 59) // 60)
        return f"PAUSED ({remaining_minutes} min remaining)"

    def touch_health(self):
        HEALTH_FILE.write_text(str(time.time()), encoding="utf-8")

    def mark_ready(self):
        READY_FILE.write_text("ready", encoding="utf-8")
        self.touch_health()

    def validate_bot(self):
        response = requests.get(
            f"{self.telegram_api}/getMe",
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram getMe failed: {payload}")

        self.bot_username = payload["result"].get("username", "unknown")
        print(f"Telegram bot verified: @{self.bot_username}")

    def fetch_catalog_assets(self):
        try:
            response = requests.get(
                f"{CATALOG_URL}/assets",
                timeout=HTTP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            assets = response.json()
        except Exception as exc:
            print("Failed to fetch catalog assets:", exc)
            return []

        with self.lock:
            for asset in assets:
                self.rules_cache[asset["asset_id"]] = asset.get("rules", {})
        return assets

    def refresh_catalog_loop(self):
        while True:
            self.fetch_catalog_assets()
            self.touch_health()
            time.sleep(60)

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("Alert service connected to MQTT broker")
            client.subscribe("assets/+/sensors")
            client.subscribe("assets/+/events")
            client.subscribe("assets/+/heartbeat")
            client.subscribe("catalog/config_updated")
            print("Subscribed to sensor, event, heartbeat, and catalog topics")
            self.touch_health()
        else:
            print("Alert service MQTT connection failed:", rc)

    def on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except json.JSONDecodeError as exc:
            print("Failed to decode MQTT payload:", exc)
            return

        topic = msg.topic
        retained = bool(getattr(msg, "retain", False))

        if topic == "catalog/config_updated":
            asset_id = payload.get("asset_id")
            rules = payload.get("rules", {})
            if asset_id:
                with self.lock:
                    if rules:
                        self.rules_cache[asset_id] = rules
                    else:
                        self.rules_cache.pop(asset_id, None)
            self.touch_health()
            return

        if topic.endswith("/heartbeat"):
            asset_id = payload.get("warehouse_id")
            if asset_id:
                with self.lock:
                    self.device_presence[asset_id] = "ONLINE"
            self.touch_health()
            return

        if topic.endswith("/events"):
            self.handle_event(payload, retained)
            self.touch_health()
            return

        if topic.endswith("/sensors"):
            self.handle_sensor(payload)
            self.touch_health()

    def handle_sensor(self, payload):
        asset_id = payload.get("warehouse_id")
        if not asset_id:
            return

        with self.lock:
            self.latest_sensor[asset_id] = payload
            self.device_presence[asset_id] = "ONLINE"
            rules = self.rules_cache.get(asset_id, {})
            previous_state = self.latest_state.get(asset_id)

        decision = evaluate_rules(payload, rules)
        state = decision["state"]

        with self.lock:
            self.latest_state[asset_id] = state
            if state != "ANOMALY":
                self.latest_anomaly_type.pop(asset_id, None)

        if state not in ALERT_STATES:
            return

        if previous_state == state:
            return

        anomaly_type = self.latest_anomaly_type.get(asset_id)
        message = self.format_state_alert(asset_id, state, payload, decision["action"], anomaly_type)
        self.record_and_broadcast(asset_id, state, message)

    def handle_event(self, payload, retained):
        asset_id = payload.get("warehouse_id")
        event = payload.get("event")
        if not asset_id or not event:
            return

        if event == "ANOMALY_DETECTED":
            with self.lock:
                self.latest_anomaly_type[asset_id] = payload.get("anomaly_type", "unknown")
            return

        if event not in DEVICE_EVENTS:
            return

        with self.lock:
            self.device_presence[asset_id] = "OFFLINE" if event == "DEVICE_OFFLINE" else "ONLINE"
            if retained:
                self.last_device_event[asset_id] = event
                return
            if self.last_device_event.get(asset_id) == event:
                return
            self.last_device_event[asset_id] = event

        message = self.format_device_event(asset_id, event)
        self.record_and_broadcast(asset_id, event, message)

    def record_and_broadcast(self, asset_id, kind, message):
        entry = {
            "time": self.now_text(),
            "warehouse_id": asset_id,
            "kind": kind,
            "message": message,
        }

        with self.lock:
            self.alert_history.append(entry)
            self.alert_history = self.alert_history[-ALERT_HISTORY_LIMIT:]
            subscribers = dict(self.subscribers)
            paused = self.is_bot_paused()
            self.save_state()

        if paused:
            print(f"Alert recorded but bot is paused: {asset_id} -> {kind}")
            return

        for chat_id, meta in subscribers.items():
            mute_until = float(meta.get("mute_until", 0) or 0)
            if time.time() < mute_until:
                continue
            self.send_message(chat_id, message)

    def now_text(self):
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    def safe_get_json(self, url):
        try:
            response = requests.get(url, timeout=HTTP_TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            return {"error": str(exc)}

    def send_message(self, chat_id, text, include_menu=False):
        payload = {
            "chat_id": int(chat_id),
            "text": text,
        }
        if include_menu:
            payload["reply_markup"] = TELEGRAM_MENU

        try:
            response = requests.post(
                f"{self.telegram_api}/sendMessage",
                json=payload,
                timeout=HTTP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            result = response.json()
            if not result.get("ok"):
                print(f"Telegram sendMessage failed for {chat_id}: {result}")
        except Exception as exc:
            print(f"Failed to send Telegram message to {chat_id}: {exc}")

    def ensure_subscriber(self, chat_id, label):
        chat_id = str(chat_id)
        with self.lock:
            subscriber = self.subscribers.setdefault(
                chat_id,
                {"label": label or "telegram", "mute_until": 0},
            )
            subscriber["label"] = label or subscriber.get("label") or "telegram"
            self.save_state()

    def format_state_alert(self, asset_id, state, payload, actions, anomaly_type):
        lines = [
            "WAREHOUSE ALERT",
            f"Warehouse: {asset_id}",
            f"State: {state}",
            f"Temperature: {payload.get('temperature', 'n/a')} C",
            f"Humidity: {payload.get('humidity', 'n/a')} %",
            f"Stock: {payload.get('stock', 'n/a')}",
        ]

        if anomaly_type:
            lines.append(f"Anomaly Type: {anomaly_type}")

        if actions:
            action_text = ", ".join(sorted(actions.keys()))
            lines.append(f"Suggested Action: {action_text}")

        lines.append(f"Time: {self.now_text()}")
        return "\n".join(lines)

    def format_device_event(self, asset_id, event):
        label = "OFFLINE" if event == "DEVICE_OFFLINE" else "ONLINE"
        return "\n".join([
            "WAREHOUSE DEVICE EVENT",
            f"Warehouse: {asset_id}",
            f"Status: {label}",
            f"Time: {self.now_text()}",
        ])

    def format_status_message(self):
        controller_status = self.safe_get_json(f"{CONTROLLER_URL}/status")
        if isinstance(controller_status, dict) and controller_status.get("error"):
            return f"Controller status unavailable: {controller_status['error']}"

        with self.lock:
            rules_cache = dict(self.rules_cache)
            latest_sensor = dict(self.latest_sensor)
            device_presence = dict(self.device_presence)

        asset_ids = sorted(set(rules_cache) | set(controller_status.keys()) | set(latest_sensor.keys()))
        if not asset_ids:
            return "No warehouse state is available yet."

        lines = ["Live warehouse state"]
        lines.extend([
            "",
            f"Alert Bot: {self.bot_pause_text()}",
        ])
        for asset_id in asset_ids:
            decision = controller_status.get(asset_id, {})
            sensor = latest_sensor.get(asset_id, {})
            device_state = device_presence.get(asset_id, "UNKNOWN")
            actions = decision.get("action", {})
            action_text = ", ".join(sorted(actions.keys())) if actions else "none"
            lines.extend([
                "",
                f"{asset_id}",
                f"State: {decision.get('state', 'UNKNOWN')}",
                f"Device: {device_state}",
                f"Temp: {sensor.get('temperature', 'n/a')} C",
                f"Humidity: {sensor.get('humidity', 'n/a')} %",
                f"Stock: {sensor.get('stock', 'n/a')}",
                f"Actions: {action_text}",
            ])
        return "\n".join(lines)

    def format_warehouses_message(self):
        assets = self.fetch_catalog_assets()
        if not assets:
            return "Catalog is unavailable or empty."

        lines = ["Registered warehouses and thresholds"]
        for asset in assets:
            rules = asset.get("rules", {})
            lines.extend([
                "",
                asset["asset_id"],
                f"temp_warning: {rules.get('temp_warning', 'n/a')}",
                f"temp_critical: {rules.get('temp_critical', 'n/a')}",
                f"stock_low: {rules.get('stock_low', 'n/a')}",
                f"stock_overload: {rules.get('stock_overload', 'n/a')}",
                f"temp_anomaly_high: {rules.get('temp_anomaly_high', 'n/a')}",
                f"temp_anomaly_low: {rules.get('temp_anomaly_low', 'n/a')}",
                f"humidity_anomaly_high: {rules.get('humidity_anomaly_high', 'n/a')}",
            ])
        return "\n".join(lines)

    def format_alert_history(self):
        with self.lock:
            recent = list(self.alert_history[-10:])

        if not recent:
            return "No alerts have been recorded yet."

        lines = ["Last 10 alerts"]
        for entry in reversed(recent):
            lines.extend([
                "",
                f"[{entry['time']}] {entry['warehouse_id']} -> {entry['kind']}",
                entry["message"],
            ])
        return "\n".join(lines)

    def format_help(self):
        return "\n".join([
            "Smart Warehouse Bot commands",
            "/start - register this chat and show the menu",
            "/status - live state of all warehouses",
            "/warehouses - list warehouses and thresholds",
            "/alerts - show the last 10 alerts",
            "/pause 10 - pause automatic alerts globally for N minutes",
            "/resume - resume global automatic alerts",
            "/mute 10 - mute alerts for N minutes",
            "/unmute - re-enable alerts",
            "/help - show this help message",
        ])

    def handle_command(self, message):
        text = (message.get("text") or "").strip()
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        label = chat.get("username") or chat.get("first_name") or "telegram"
        if not chat_id:
            return

        self.ensure_subscriber(chat_id, label)

        lowered_text = text.lower()
        if lowered_text == "pause bot 10m":
            text = "/pause 10"
        elif lowered_text == "resume bot":
            text = "/resume"

        if text.startswith("/start"):
            reply = "\n".join([
                "Smart Warehouse Alert Bot is active.",
                "This chat is now subscribed to live warehouse alerts.",
                "Use /status, /warehouses, /alerts, /pause, /resume, /mute, /unmute, or /help.",
            ])
            self.send_message(chat_id, reply, include_menu=True)
            return

        if text.startswith("/status"):
            self.send_message(chat_id, self.format_status_message(), include_menu=True)
            return

        if text.startswith("/warehouses"):
            self.send_message(chat_id, self.format_warehouses_message(), include_menu=True)
            return

        if text.startswith("/alerts"):
            self.send_message(chat_id, self.format_alert_history(), include_menu=True)
            return

        if text.startswith("/pause"):
            parts = text.split(maxsplit=1)
            minutes = 10
            if len(parts) == 2:
                if not parts[1].isdigit():
                    self.send_message(chat_id, "Usage: /pause 10", include_menu=True)
                    return
                minutes = max(1, min(int(parts[1]), 1440))

            pause_until = time.time() + minutes * 60
            with self.lock:
                self.bot_pause_until = pause_until
                self.save_state()
            self.send_message(
                chat_id,
                f"Automatic alerts paused globally for {minutes} minutes. Commands still work.",
                include_menu=True,
            )
            return

        if text.startswith("/resume"):
            with self.lock:
                self.bot_pause_until = 0
                self.save_state()
            self.send_message(
                chat_id,
                "Automatic alerts resumed globally.",
                include_menu=True,
            )
            return

        if text.startswith("/mute"):
            parts = text.split(maxsplit=1)
            if len(parts) != 2 or not parts[1].isdigit():
                self.send_message(chat_id, "Usage: /mute 10", include_menu=True)
                return
            minutes = max(1, min(int(parts[1]), 1440))
            mute_until = time.time() + minutes * 60
            with self.lock:
                self.subscribers[str(chat_id)]["mute_until"] = mute_until
                self.save_state()
            self.send_message(chat_id, f"Alerts muted for {minutes} minutes.", include_menu=True)
            return

        if text.startswith("/unmute"):
            with self.lock:
                self.subscribers[str(chat_id)]["mute_until"] = 0
                self.save_state()
            self.send_message(chat_id, "Alerts re-enabled.", include_menu=True)
            return

        self.send_message(chat_id, self.format_help(), include_menu=True)

    def poll_telegram(self):
        print("Telegram command polling started")
        while True:
            params = {"timeout": POLL_TIMEOUT_SECONDS}
            if self.update_offset is not None:
                params["offset"] = self.update_offset

            try:
                response = requests.get(
                    f"{self.telegram_api}/getUpdates",
                    params=params,
                    timeout=POLL_TIMEOUT_SECONDS + 5,
                )
                response.raise_for_status()
                payload = response.json()
                if not payload.get("ok"):
                    print("Telegram getUpdates failed:", payload)
                    time.sleep(5)
                    continue

                for update in payload.get("result", []):
                    self.update_offset = update["update_id"] + 1
                    message = update.get("message")
                    if message:
                        self.handle_command(message)
                self.save_state()
                self.touch_health()
            except Exception as exc:
                print("Telegram polling error:", exc)
                time.sleep(5)

    def mqtt_loop(self):
        while True:
            try:
                print("Connecting alert service to MQTT broker...")
                self.client.connect(MQTT_BROKER, MQTT_PORT, 60)
                self.client.loop_forever()
            except Exception as exc:
                print("Alert service MQTT loop error:", exc)
                time.sleep(5)

    def announce_online(self):
        with self.lock:
            subscribers = dict(self.subscribers)

        if not subscribers:
            return

        message = "\n".join([
            "Alert Service Online",
            "Smart Warehouse monitoring is active.",
            "Send /status for a live snapshot.",
        ])
        for chat_id in subscribers:
            self.send_message(chat_id, message, include_menu=True)

    def run(self):
        print("Smart Warehouse Alert Service starting...")
        self.validate_bot()
        self.fetch_catalog_assets()

        threading.Thread(target=self.mqtt_loop, daemon=True).start()
        threading.Thread(target=self.refresh_catalog_loop, daemon=True).start()

        self.mark_ready()
        self.announce_online()
        self.poll_telegram()


if __name__ == "__main__":
    AlertService().run()
