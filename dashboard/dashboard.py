import os
import time
from datetime import datetime, timezone

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
import streamlit.components.v1 as components


CATALOG_URL = os.environ.get("CATALOG_URL", "http://catalog-service:8080")
CONTROLLER_URL = os.environ.get(
    "CONTROLLER_URL", "http://controller-service:8001")
ALERT_URL = os.environ.get("ALERT_URL", "http://alert-service:5002")
GRAFANA_URL = os.environ.get("GRAFANA_PUBLIC_URL", "http://localhost:3100")
INFLUX_PUBLIC_URL = os.environ.get(
    "INFLUX_PUBLIC_URL", "http://localhost:8086")
GRAFANA_DASHBOARD_URL = f"{GRAFANA_URL}/d/warehouse-metrics/warehouse-metrics"

STATE_STYLE = {
    "NORMAL": ("#1fbf75", "OK"),
    "WARNING": ("#f5b942", "WARN"),
    "CRITICAL": ("#ff4d5e", "HOT"),
    "OVERLOAD": ("#b06cff", "LOAD"),
    "ANOMALY": ("#ff7a1a", "ALERT"),
    "MANUAL": ("#57a6ff", "MANUAL"),
    "OFFLINE": ("#6d7682", "OFF"),
    "UNKNOWN": ("#6d7682", "WAIT"),
}
STATE_CODES = {
    "NORMAL": 0,
    "WARNING": 1,
    "CRITICAL": 2,
    "OVERLOAD": 3,
    "ANOMALY": 4,
    "MANUAL": 5,
    "OFFLINE": -1,
    "UNKNOWN": -1,
}


st.set_page_config(page_title="Smart Warehouse IoT",
                   page_icon="🏭", layout="wide")

st.markdown(
    """
    <style>
    :root {
      --bg: #071018;
      --panel: #132231;
      --panel-2: #192b3d;
      --panel-3: #203548;
      --text: #f7fbff;
      --muted: #b9d3ee;
      --line: #36516b;
      --cyan: #39d5ff;
      --green: #27d17f;
      --orange: #ff8a24;
      --red: #ff4d5e;
    }
    .stApp { background: radial-gradient(circle at top left, #0d2231 0, var(--bg) 36rem); color: var(--text); }
    [data-testid="stHeader"] { background: rgba(7, 16, 24, 0.9); }
    .block-container {
      max-width: 1840px;
      padding: 1.35rem clamp(1rem, 3vw, 3rem) 1.5rem;
    }
    h1, h2, h3, [data-testid="stMarkdownContainer"] h1, [data-testid="stMarkdownContainer"] h2, [data-testid="stMarkdownContainer"] h3 {
      color: var(--text);
      letter-spacing: 0;
    }
    [data-testid="stMarkdownContainer"] p, label, .stCaptionContainer, [data-testid="stWidgetLabel"] {
      color: var(--muted) !important;
    }
    .iot-header {
      border-bottom: 1px solid var(--line);
      padding: 1rem 0 1.15rem;
      margin-bottom: 1.1rem;
    }
    .iot-header h1 {
      font-size: clamp(2.25rem, 4vw, 4rem);
      line-height: 1.05;
      margin: 0 0 0.85rem;
      color: #f8fbff;
      text-shadow: 0 3px 18px rgba(57, 213, 255, 0.12);
    }
    .iot-subtitle { color: #c3dbf5; font-size: 1.02rem; margin-top: -0.35rem; }
    .kpi-card {
      background: linear-gradient(180deg, #16283a 0%, #101d2a 100%);
      border: 1px solid #3c5974;
      border-radius: 8px;
      padding: 1.05rem 1.1rem;
      min-height: 118px;
      box-shadow: 0 10px 28px rgba(0, 0, 0, 0.22);
    }
    .kpi-label {
      color: #bfd7f0;
      font-size: 0.82rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .kpi-value {
      color: #ffffff;
      font-size: 2.35rem;
      line-height: 1.1;
      font-weight: 850;
      margin-top: 0.35rem;
    }
    .kpi-note {
      color: #89f7ff;
      font-size: 0.8rem;
      margin-top: 0.25rem;
    }
    .status-card {
      background: linear-gradient(180deg, var(--panel), #111f2d);
      border: 1px solid #35506a;
      border-radius: 8px;
      padding: 1.15rem;
      min-height: 232px;
      box-shadow: 0 12px 30px rgba(0, 0, 0, 0.25);
    }
    .status-top { display: flex; justify-content: space-between; gap: 0.75rem; align-items: flex-start; }
    .warehouse-name { font-size: 1.08rem; font-weight: 800; color: #ffffff; overflow-wrap: anywhere; }
    .warehouse-meta { color: #b8d7f6; font-size: 0.86rem; margin-top: 0.28rem; }
    .pill {
      color: #081018;
      border-radius: 999px;
      padding: 0.28rem 0.65rem;
      font-size: 0.72rem;
      font-weight: 900;
      white-space: nowrap;
    }
    .metric-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 0.5rem;
      margin-top: 0.9rem;
    }
    .mini-metric {
      background: var(--panel-3);
      border: 1px solid #405a74;
      border-radius: 8px;
      padding: 0.75rem;
    }
    .mini-label { color: #c0dcfa; font-size: 0.76rem; }
    .mini-value { color: #ffffff; font-size: 1.08rem; font-weight: 850; margin-top: 0.18rem; }
    .event-console {
      max-height: 380px;
      overflow-y: auto;
      background: #07111b;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0.75rem;
      font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
      font-size: 0.82rem;
    }
    .event-row { padding: 0.42rem 0; border-bottom: 1px solid rgba(255,255,255,0.06); }
    .event-time { color: #a7bfd9; }
    .event-name { font-weight: 800; }
    .event-source { color: #d7e7f8; }
    div[data-testid="stMetric"] {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0.75rem;
    }
    div[data-testid="stLinkButton"] a, div[data-testid="stButton"] button {
      background: linear-gradient(180deg, #20384d, #172738) !important;
      border: 1px solid #4d6f8e !important;
      color: #f9fcff !important;
      border-radius: 8px !important;
      min-height: 3.1rem;
      font-weight: 750 !important;
      box-shadow: 0 8px 20px rgba(0, 0, 0, 0.22);
    }
    div[data-testid="stLinkButton"] a:hover, div[data-testid="stButton"] button:hover {
      border-color: var(--cyan) !important;
      color: #ffffff !important;
      background: linear-gradient(180deg, #27516a, #1a3349) !important;
    }
    div[data-testid="stDataFrame"], div[data-testid="stJson"] {
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }
    div[data-testid="stExpander"] {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(19, 34, 49, 0.72);
    }
    @media (max-width: 900px) {
      .metric-grid { grid-template-columns: 1fr; }
      .status-card { min-height: auto; }
      .kpi-card { min-height: 94px; }
      .kpi-value { font-size: 1.85rem; }
      .iot-header h1 { font-size: 2.2rem; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def get_json(url, default, timeout=4):
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except Exception:
        return default


def get_api_result(url, key, timeout=8):
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            return [], payload["error"]
        return payload.get(key, []), None
    except Exception as exc:
        return [], str(exc)


def post_json(url, payload, timeout=4):
    try:
        response = requests.post(url, json=payload, timeout=timeout)
        if response.ok:
            return True, response.json()
        return False, response.text
    except Exception as exc:
        return False, str(exc)


def fmt_time(value):
    if not value:
        return "pending"
    try:
        text = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(text).astimezone(timezone.utc).strftime("%H:%M:%S UTC")
    except Exception:
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime("%H:%M:%S UTC")
        except Exception:
            return str(value)


def state_badge(state):
    color, label = STATE_STYLE.get(state, STATE_STYLE["UNKNOWN"])
    return f'<span class="pill" style="background:{color};">{label}</span>'


def kpi_card(label, value, note):
    st.markdown(
        f"""
        <div class="kpi-card">
          <div class="kpi-label">{label}</div>
          <div class="kpi-value">{value}</div>
          <div class="kpi-note">{note}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def status_card(asset, status):
    asset_id = asset.get("asset_id", "unknown")
    state = status.get("state", "OFFLINE")
    color, _ = STATE_STYLE.get(state, STATE_STYLE["UNKNOWN"])
    temp = float(status.get("temperature", 0) or 0)
    humidity = float(status.get("humidity", 0) or 0)
    stock = int(status.get("stock", 0) or 0)
    door = "Open" if status.get("door_open") else "Closed"
    age = time.time() - float(status.get("timestamp", 0) or 0)
    last_seen = f"{age:.0f}s ago" if status.get("timestamp") else "waiting"

    st.markdown(
        f"""
        <div class="status-card" style="box-shadow: inset 4px 0 0 {color};">
          <div class="status-top">
            <div>
              <div class="warehouse-name">🏭 {asset.get("name", asset_id)}</div>
              <div class="warehouse-meta">{asset_id} · {asset.get("location", "unknown location")}</div>
            </div>
            {state_badge(state)}
          </div>
          <div class="metric-grid">
            <div class="mini-metric"><div class="mini-label">Temperature</div><div class="mini-value">{temp:.1f}°C</div></div>
            <div class="mini-metric"><div class="mini-label">Humidity</div><div class="mini-value">{humidity:.1f}%</div></div>
            <div class="mini-metric"><div class="mini-label">Stock</div><div class="mini-value">{stock}%</div></div>
          </div>
          <div class="metric-grid" style="grid-template-columns: repeat(2, minmax(0, 1fr));">
            <div class="mini-metric"><div class="mini-label">Door</div><div class="mini-value">{door}</div></div>
            <div class="mini-metric"><div class="mini-label">Last sample</div><div class="mini-value">{last_seen}</div></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def event_console(events):
    if not events:
        st.info("No events recorded yet. The console will fill as devices publish status, commands, and confirmations.")
        return

    rows = []
    for event in events[:80]:
        name = event.get("event") or "EVENT"
        warehouse = event.get("warehouse_id") or "unknown"
        source = event.get("source") or "system"
        status = event.get("status") or event.get("anomaly_type") or ""
        color = "#7dd3fc"
        if "OFFLINE" in name or "ANOMALY" in name:
            color = "#ff7a1a"
        elif "MANUAL" in name:
            color = "#f5b942"
        elif "CONFIRMATION" in name or "ONLINE" in name:
            color = "#1fbf75"
        elif "COMMAND" in name:
            color = "#57a6ff"
        rows.append(
            f'<div class="event-row"><span class="event-time">{fmt_time(event.get("time"))}</span> '
            f'<span class="event-name" style="color:{color};">{name}</span> '
            f'<span class="event-source">{warehouse} · {source} {status}</span></div>'
        )

    st.markdown(
        f'<div class="event-console">{"".join(rows)}</div>', unsafe_allow_html=True)


def state_history_chart(states):
    if not states:
        st.info(
            "No state timeline data yet. Wait for the simulator to publish a few samples.")
        return

    df = pd.DataFrame(states)
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df["state_code"] = df["state"].map(
        STATE_CODES).fillna(df.get("state_code", -1))
    df = df.dropna(subset=["time"]).sort_values("time")
    if df.empty:
        st.info("State history returned no usable timestamps yet.")
        return

    fig = px.line(
        df,
        x="time",
        y="state_code",
        color="warehouse_id",
        markers=True,
        hover_data=["state", "temperature", "humidity", "stock"],
        title="State Timeline",
    )
    fig.update_traces(line_shape="hv")
    fig.update_yaxes(
        tickmode="array",
        tickvals=[0, 1, 2, 3, 4, 5],
        ticktext=["NORMAL", "WARNING", "CRITICAL",
                  "OVERLOAD", "ANOMALY", "MANUAL"],
    )
    fig.update_layout(
        height=340,
        paper_bgcolor="#0b1117",
        plot_bgcolor="#101923",
        font_color="#edf4fb",
        margin=dict(l=20, r=20, t=48, b=20),
        legend_title_text="Warehouse",
    )
    st.plotly_chart(fig, width="stretch")


assets = get_json(f"{CATALOG_URL}/assets", [])
if not assets:
    assets = [
        {"asset_id": "warehouse_cold",
            "name": "Cold Storage Warehouse", "location": "Building A"},
        {"asset_id": "warehouse_standard",
            "name": "Standard Warehouse", "location": "Building A"},
        {"asset_id": "warehouse_hazard",
            "name": "Hazardous Materials Warehouse", "location": "Building B"},
    ]

status = get_json(f"{CONTROLLER_URL}/status", {})
health = get_json(f"{CONTROLLER_URL}/health", {})
commands = get_json(f"{CONTROLLER_URL}/commands", {})
events, events_error = get_api_result(
    f"{CONTROLLER_URL}/events", "events", timeout=10)
states, states_error = get_api_result(
    f"{CONTROLLER_URL}/state_history", "states", timeout=12)
alerts = get_json(f"{ALERT_URL}/alerts", {}).get("alerts", [])
alert_status = get_json(f"{ALERT_URL}/status", {})

st.markdown(
    """
    <div class="iot-header">
      <h1>🏭 Smart Warehouse IoT Control Center</h1>
      <div class="iot-subtitle">Live telemetry, rules, actuation, alerts, and operations visibility.</div>
    </div>
    """,
    unsafe_allow_html=True,
)

top1, top2, top3, top4 = st.columns([1, 1, 1, 1])
with top1:
    kpi_card("Assets", len(assets), "Catalog registered")
with top2:
    kpi_card("Active Telemetry", len(status), "Live controller states")
with top3:
    kpi_card("Recent Events", len(events), "Influx event feed")
with top4:
    kpi_card("Pending Commands", commands.get(
        "pending_confirmations", 0), "Awaiting actuator ACK")

link1, link2, link3, link4 = st.columns(4)
link1.link_button("📊 Grafana", GRAFANA_DASHBOARD_URL, width="stretch")
link2.link_button("💾 InfluxDB", INFLUX_PUBLIC_URL, width="stretch")
link3.link_button(
    "📚 Catalog API", "http://localhost:8080/assets", width="stretch")
if link4.button("🔄 Refresh", width="stretch"):
    st.rerun()

st.subheader("Warehouse Status")
columns = st.columns(min(len(assets), 3))
for index, asset in enumerate(assets):
    with columns[index % len(columns)]:
        status_card(asset, status.get(asset.get("asset_id"), {}))

left, right = st.columns([1.45, 1])
with left:
    if states_error:
        st.warning(f"State history unavailable: {states_error}")
    state_history_chart(states)
with right:
    st.subheader("Live Event Log")
    if events_error:
        st.warning(f"Event service unavailable: {events_error}")
    else:
        event_console(events)

st.subheader("Quick Actions")
asset_options = [asset["asset_id"] for asset in assets]

# Defined actions exactly once
action_labels = {
    "fan_on": "Fan ON",
    "fan_off": "Fan OFF",
    "heater_on": "Heater ON",
    "heater_off": "Heater OFF",
    "door_open": "Open Door",
    "door_close": "Close Door",
    "dehumidifier_on": "Dehumidifier ON",
    "dehumidifier_off": "Dehumidifier OFF",
    "pause_deliveries": "Pause Deliveries",
    "restock_alert": "Restock Alert",
    "emergency_shutdown": "Emergency Shutdown",
}

control_col, pending_col = st.columns([1, 1])
with control_col:
    selected_asset = st.selectbox(
        "Warehouse", asset_options, label_visibility="collapsed")
    
    # Render buttons in a clean grid
    button_cols = st.columns(3)
    for index, (action, label) in enumerate(action_labels.items()):
        with button_cols[index % 3]:
            if st.button(label, key=f"btn-{selected_asset}-{action}", width="stretch"):
                ok, result = post_json(
                    f"{CONTROLLER_URL}/manual_command",
                    {"asset_id": selected_asset, "action": action},
                )
                if ok:
                    st.success(f"Command sent to {selected_asset}: {label}")
                else:
                    st.error(f"Command failed: {result}")
with pending_col:
    pending = commands.get("pending_commands", [])
    st.caption("Pending means the controller sent an actuator command and is waiting for the actuator confirmation. It should usually be 0 because confirmations are fast.")
    if pending:
        st.dataframe(pd.DataFrame(pending), width="stretch", hide_index=True)
    else:
        st.success("No pending actuator confirmations.")

st.subheader("Recent Alerts")
if alerts:
    alert_df = pd.DataFrame(alerts[:20])
    visible_columns = [col for col in ["time", "warehouse_id", "kind",
                                       "temperature", "humidity", "stock", "actions"] if col in alert_df.columns]
    st.dataframe(alert_df[visible_columns], width="stretch", hide_index=True)
else:
    st.success("No recent alerts. The alert feed is quiet.")

st.subheader("System Health")
health_cols = st.columns(4)
with health_cols[0]:
    # Controller Status
    if health.get("status") == "ok":
        st.success("✅ Smart Controller", icon="🏭")
        st.metric("Health", "Healthy", delta="Connected", delta_color="normal")
    else:
        st.error("❌ Smart Controller", icon="🏭")
        st.metric("Health", "Unhealthy", delta="", delta_color="inverse")
with health_cols[1]:
    # Alert Service Status
    alert_configured = alert_status.get("telegram_configured", False)
    warehouses_active = alert_status.get("warehouses_active", 0)
    if alert_configured:
        st.success("✅ Alert & Telegram Bot", icon="📱")
        st.metric("Status", "Active", delta=f"{warehouses_active} warehouses active", delta_color="normal")
    else:
        st.warning("⚠️ Alert Service", icon="📱")
        st.metric("Status", "Active", delta="Telegram not configured", delta_color="off")
with health_cols[2]:
    # Grafana & InfluxDB
    st.info("🎨 Visualization & Storage", icon="📊")
    st.metric("Grafana", "Online", delta="Connected", delta_color="normal")
with health_cols[3]:
    # Catalog & MQTT
    st.info("🗂️ Registry & Bus", icon="🔗")
    st.metric("Catalog", "Online", delta="Connected", delta_color="normal")

# Add Warehouse List to System Health
st.subheader("Active Warehouses")
warehouse_cols = st.columns(min(3, len(assets)))
for i, asset in enumerate(assets):
    with warehouse_cols[i % 3]:
        asset_id = asset.get("asset_id")
        asset_status = status.get(asset_id, {})
        state = asset_status.get("state", "UNKNOWN")
        state_icon = "🟢" if state == "NORMAL" else "🟡" if state in ["WARNING", "OVERLOAD"] else "🔴"
        st.markdown(f"""
        <div style="padding:10px; border-radius:8px; margin-bottom:8px; border:1px solid #444;">
            <strong>{state_icon} {asset.get('name', asset_id)}</strong><br>
            <small style="color:#888;">{asset.get('location', '')} ({asset_id})</small><br>
            <small>Current state: {state}</small>
        </div>
        """, unsafe_allow_html=True)

with st.expander("Grafana Preview"):
    components.html(
        f'<iframe src="{GRAFANA_DASHBOARD_URL}" height="520" width="100%" style="border:none;" seamless></iframe>',
        height=550
    )

if st.toggle("Auto refresh every 5 seconds", value=True):
    time.sleep(5)
    st.rerun()
