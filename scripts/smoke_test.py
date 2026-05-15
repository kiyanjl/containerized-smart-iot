import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SERVICES = {
    "mqtt-broker",
    "catalog-service",
    "sensor-simulator",
    "influxdb",
    "controller-service",
    "actuator-service",
    "alert-service",
    "grafana",
    "dashboard",
}


class SmokeTestError(RuntimeError):
    pass


def run_command(args):
    completed = subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SmokeTestError(
            f"Command failed: {' '.join(args)}\n{completed.stdout}\n{completed.stderr}"
        )
    return completed.stdout.strip()


def http_json(url, headers=None):
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise SmokeTestError(f"HTTP request failed for {url}: {exc}") from exc


def http_text(url, headers=None, method="GET", data=None):
    request = urllib.request.Request(
        url,
        headers=headers or {},
        method=method,
        data=data,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise SmokeTestError(f"HTTP request failed for {url}: {exc}") from exc


def parse_env_file(path):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


ENV_SETTINGS = parse_env_file(ROOT / ".env") if (ROOT / ".env").exists() else {}
GRAFANA_PORT = ENV_SETTINGS.get("GRAFANA_PORT", "3100")
DASHBOARD_PORT = ENV_SETTINGS.get("DASHBOARD_PORT", "18501")


def influx_query(flux):
    settings = parse_env_file(ROOT / "database" / "influxdb.env")
    headers = {
        "Authorization": f"Token {settings['DOCKER_INFLUXDB_INIT_ADMIN_TOKEN']}",
        "Accept": "application/csv",
        "Content-Type": "application/vnd.flux",
    }
    return http_text(
        "http://localhost:8086/api/v2/query?org=smart-iot",
        headers=headers,
        method="POST",
        data=flux.encode("utf-8"),
    )


def wait_for(description, predicate, timeout=15, interval=0.5):
    deadline = time.time() + timeout
    last_value = None
    while time.time() < deadline:
        last_value = predicate()
        if last_value:
            return last_value
        time.sleep(interval)
    raise SmokeTestError(f"Timed out waiting for: {description}")


def assert_running_services():
    output = run_command(["docker", "compose", "ps", "--services", "--filter", "status=running"])
    running = {line.strip() for line in output.splitlines() if line.strip()}
    missing = EXPECTED_SERVICES - running
    if missing:
        raise SmokeTestError(f"Missing running services: {sorted(missing)}")
    print("PASS: all compose services are running")


def check_http_endpoints():
    catalog_health = http_json("http://localhost:8080/health")
    controller_health = http_json("http://localhost:8001/health")
    grafana_health = http_json(f"http://localhost:{GRAFANA_PORT}/api/health")
    dashboard_health = http_text(f"http://localhost:{DASHBOARD_PORT}/_stcore/health")
    assets = http_json("http://localhost:8080/assets")

    if catalog_health.get("status") != "ok":
        raise SmokeTestError("Catalog health endpoint did not report ok")
    if controller_health.get("status") != "ok":
        raise SmokeTestError("Controller health endpoint did not report ok")
    if grafana_health.get("database") != "ok":
        raise SmokeTestError("Grafana health endpoint did not report database ok")
    if "ok" not in dashboard_health.lower():
        raise SmokeTestError("Dashboard health endpoint did not report ok")
    if len(assets) < 3:
        raise SmokeTestError("Catalog does not contain the expected warehouse assets")

    print("PASS: HTTP endpoints are healthy")
    return assets


def check_alert_service():
    status = http_json("http://localhost:5002/status")
    if not status.get("telegram_configured"):
        raise SmokeTestError("Alert service reports Telegram is not configured")
    if status.get("warehouses_active", 0) < 1:
        raise SmokeTestError("Alert service has not received warehouse telemetry")

    logs = run_command(["docker", "compose", "logs", "alert-service", "--tail", "800"])
    if "Telegram bot verified" not in logs:
        raise SmokeTestError("Alert service logs do not show Telegram bot validation")
    if "Telegram command polling started" not in logs:
        raise SmokeTestError("Alert service logs do not show Telegram polling startup")
    print("PASS: Alert Service API is healthy and Telegram polling is active")


def check_recent_influx_data(expected_assets):
    def has_recent_rows():
        telemetry = influx_query(
            'from(bucket: "warehouse_metrics") '
            '|> range(start: -5m) '
            '|> filter(fn: (r) => r._measurement == "warehouse") '
            '|> filter(fn: (r) => r._field == "state_code") '
            '|> last()'
        )
        device_health = influx_query(
            'from(bucket: "warehouse_metrics") '
            '|> range(start: -5m) '
            '|> filter(fn: (r) => r._measurement == "device_health") '
            '|> filter(fn: (r) => r._field == "online") '
            '|> last()'
        )

        for asset in expected_assets:
            if asset not in telemetry or asset not in device_health:
                return False
        return True

    wait_for(
        "recent telemetry and device-health rows",
        has_recent_rows,
        timeout=75,
        interval=5,
    )

    print("PASS: InfluxDB contains recent telemetry and device-health rows")


def inject_test_anomaly():
    payload = {
        "warehouse_id": "warehouse_standard",
        "temperature": 52.0,
        "humidity": 55.0,
        "stock": 30,
        "door_open": 0,
        "timestamp": time.time(),
    }
    python_code = (
        "import paho.mqtt.client as mqtt; "
        f"payload = {json.dumps(json.dumps(payload))}; "
        "client = mqtt.Client(); "
        "client.connect('mqtt-broker', 1883, 60); "
        "client.loop_start(); "
        "info = client.publish('assets/warehouse_standard/sensors', payload, qos=1); "
        "info.wait_for_publish(); "
        "client.loop_stop(); "
        "client.disconnect()"
    )
    run_command(["docker", "exec", "controller-service", "python", "-c", python_code])
    print("PASS: injected anomaly test message through MQTT")


def verify_control_loop():
    controller_logs = wait_for(
        "controller anomaly logs",
        lambda: run_command(["docker", "compose", "logs", "controller-service", "--tail", "120"]),
        timeout=5,
        interval=1,
    )
    if (
        "temperature': 52.0" not in controller_logs and
        'temperature": 52.0' not in controller_logs
    ):
        raise SmokeTestError("Controller logs do not show the injected anomaly payload")
    if "Decision: ANOMALY" not in controller_logs:
        raise SmokeTestError("Controller logs do not show an ANOMALY decision")

    anomaly_rows = wait_for(
        "Influx anomaly row",
        lambda: influx_query(
            'from(bucket: "warehouse_metrics") '
            '|> range(start: -3m) '
            '|> filter(fn: (r) => r._measurement == "warehouse") '
            '|> filter(fn: (r) => r._field == "state_code") '
            '|> filter(fn: (r) => r.warehouse_id == "warehouse_standard") '
            '|> filter(fn: (r) => r.state == "ANOMALY") '
            '|> last()'
        ),
        timeout=12,
        interval=1,
    )
    if "warehouse_standard" not in anomaly_rows or "ANOMALY" not in anomaly_rows:
        raise SmokeTestError("Controller anomaly decision was not written to InfluxDB")

    actuator_logs = wait_for(
        "actuator execution logs",
        lambda: run_command(["docker", "compose", "logs", "actuator-service", "--tail", "80"]),
        timeout=5,
        interval=1,
    )
    if (
        "ACTUATION RECEIVED for warehouse_standard" not in actuator_logs and
        "EMERGENCY SHUTDOWN triggered - sensor anomaly detected!" not in actuator_logs
    ):
        raise SmokeTestError("Actuator logs do not show the injected command being processed")

    confirmation_rows = wait_for(
        "actuator confirmation in InfluxDB",
        lambda: influx_query(
            'from(bucket: "warehouse_metrics") '
            '|> range(start: -3m) '
            '|> filter(fn: (r) => r._measurement == "warehouse_event") '
            '|> filter(fn: (r) => r._field == "value") '
            '|> filter(fn: (r) => r.warehouse_id == "warehouse_standard") '
            '|> filter(fn: (r) => r.event == "ACTUATOR_CONFIRMATION") '
            '|> last()'
        ),
        timeout=12,
        interval=1,
    )
    if "ACTUATOR_CONFIRMATION" not in confirmation_rows:
        raise SmokeTestError("Actuator confirmation was not written to InfluxDB")

    command_rows = wait_for(
        "actuator command dispatch in InfluxDB",
        lambda: influx_query(
            'from(bucket: "warehouse_metrics") '
            '|> range(start: -3m) '
            '|> filter(fn: (r) => r._measurement == "warehouse_event") '
            '|> filter(fn: (r) => r._field == "value") '
            '|> filter(fn: (r) => r.warehouse_id == "warehouse_standard") '
            '|> filter(fn: (r) => r.event == "ACTUATOR_COMMAND_DISPATCHED") '
            '|> last()'
        ),
        timeout=12,
        interval=1,
    )
    if "ACTUATOR_COMMAND_DISPATCHED" not in command_rows:
        raise SmokeTestError("Actuator command dispatch was not written to InfluxDB")

    print("PASS: controller, actuator, and InfluxDB completed the full command-confirmation loop")


def main():
    print("Running stack smoke test...")
    assert_running_services()
    assets = check_http_endpoints()
    check_alert_service()
    check_recent_influx_data([asset["asset_id"] for asset in assets])
    inject_test_anomaly()
    verify_control_loop()
    print("SUCCESS: end-to-end stack verification passed")


if __name__ == "__main__":
    try:
        main()
    except SmokeTestError as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)
