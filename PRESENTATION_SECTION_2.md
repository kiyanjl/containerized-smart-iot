# Presentation Section 2: Observability, UI & Alert Logic
**Presenter: Technical Specialist (Section 2)**

## 1. Human Interface: Streamlit Dashboard
**File**: [dashboard.py](file:///g:/Term3-4/IOT/iot_new_project/containerized-smart-iot/dashboard/dashboard.py)
Our dashboard provides real-time monitoring and manual override capabilities.

*   **Line 367**: `get_json` calls to the Catalog to populate the warehouse list.
*   **Line 444**: **Manual Commands** - When an operator clicks a button (like "Heater ON"), the dashboard sends a REST request to the Controller.
*   **Line 476**: **Grafana Integration** - We embed advanced analytics directly into the dashboard using an iframe, giving the operator both live and historical views.

## 2. Advanced Analytics: Grafana & InfluxDB
**File**: [warehouse_dashboard.json](file:///g:/Term3-4/IOT/iot_new_project/containerized-smart-iot/grafana/provisioning/dashboards/warehouse_dashboard.json)
We use InfluxDB as a Time-Series Database. Grafana queries this data to show:
*   **Trend History**: Long-term temperature and humidity patterns.
*   **Actuation Feed**: A log of every time the system (or a human) took an action.
*   **Safety State Timeline**: A color-coded history of warehouse health.

## 3. Smart Alerting: Alert Service
**File**: [alert_service.py](file:///g:/Term3-4/IOT/iot_new_project/containerized-smart-iot/alert-service/alert_service.py)
Our Telegram bot isn't just a simple messenger; it's a smart assistant.

*   **Line 211**: **Trend Detection** - The code compares the current sensor value with the previous one to report if values are 📈 rising or 📉 falling.
*   **Line 215**: **Progress Updates** - If you take an action, the code monitors the result. If the situation improves, it sends a "✅ Your action is working!" message.
*   **Line 233**: **Intelligent Suggestions** - Based on the alert type, the bot suggests specific actions (e.g., "suggested action: heater" if it's too cold).

## 4. Key Takeaway
We transform raw sensor data into actionable insights. The operator always knows what is happening and exactly how to fix it through the dashboard or Telegram.
