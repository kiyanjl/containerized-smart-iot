import time


def evaluate_rules(data, rules=None):
    rules = rules or {}

    if not rules:
        temp_warning = 25
        temp_critical = 30
        stock_low = 20
        stock_overload = 90
        temp_anomaly_high = 45
        temp_anomaly_low = -10
        humidity_anomaly = 95
    else:
        temp_warning = rules.get("temp_warning", 999)
        temp_critical = rules.get("temp_critical", 999)
        stock_low = rules.get("stock_low", -1)
        stock_overload = rules.get("stock_overload", 90)
        temp_anomaly_high = rules.get("temp_anomaly_high", 45)
        temp_anomaly_low = rules.get("temp_anomaly_low", -10)
        humidity_anomaly = rules.get("humidity_anomaly_high", 95)

    temperature = float(data["temperature"])
    humidity = float(data.get("humidity", 50))
    stock = int(data["stock"])

    state = "NORMAL"
    action = {}

    if temperature >= temp_anomaly_high or temperature <= temp_anomaly_low:
        state = "ANOMALY"
        action["emergency_shutdown"] = True
        action["fan"] = "ON"
    elif humidity >= humidity_anomaly:
        state = "ANOMALY"
        action["dehumidifier"] = "ON"
    elif temperature >= temp_critical:
        state = "CRITICAL"
        action["fan"] = "ON"
    elif stock >= stock_overload:
        state = "OVERLOAD"
        action["pause_deliveries"] = True
    elif temperature >= temp_warning:
        state = "WARNING"

    if stock <= stock_low:
        if state == "NORMAL":
            state = "WARNING"
        action["restock_alert"] = True

    return {
        "state": state,
        "action": action,
        "timestamp": time.time(),
    }
