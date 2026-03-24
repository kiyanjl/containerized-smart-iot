import csv
import os
from typing import Dict, List, Tuple

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
import streamlit.components.v1 as components

CATALOG_URL = os.environ.get("CATALOG_URL", "http://catalog-service:8080")
CONTROLLER_URL = os.environ.get("CONTROLLER_URL", "http://controller-service:8001")
INFLUX_URL = os.environ.get("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.environ.get("INFLUX_TOKEN", "")
INFLUX_ORG = os.environ.get("INFLUX_ORG", "smart-iot")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "warehouse_metrics")
GRAFANA_PUBLIC_URL = os.environ.get("GRAFANA_PUBLIC_URL", "http://localhost:3900")

TIME_WINDOWS = {
    "Last 15 minutes": "-15m",
    "Last 1 hour": "-1h",
    "Last 6 hours": "-6h",
    "Last 24 hours": "-24h",
}

STATE_NAMES = {
    0: "NORMAL",
    1: "WARNING",
    2: "CRITICAL",
    3: "OVERLOAD",
    4: "ANOMALY",
}

STATE_COLORS = {
    "NORMAL": "#2f855a",
    "WARNING": "#d69e2e",
    "CRITICAL": "#c53030",
    "OVERLOAD": "#805ad5",
    "ANOMALY": "#dd6b20",
    "UNKNOWN": "#4a5568",
}

EVENT_COLORS = {
    "ANOMALY_DETECTED": "#dd6b20",
    "DEVICE_OFFLINE": "#c53030",
    "DEVICE_ONLINE": "#2f855a",
    "ACTUATOR_COMMAND_DISPATCHED": "#5a67d8",
    "ACTUATOR_CONFIRMATION": "#2b6cb0",
}

CHART_TEMPLATE = "plotly_white"

st.set_page_config(
    page_title="Smart Warehouse Ops Hub",
    layout="wide",
)


def inject_styles() -> None:
    st.markdown(
        """
        <style>
            :root {
                --ink: #1f2a30;
                --muted: #5f6b73;
                --paper: #f6f1e7;
                --sand: #e7dccb;
                --accent: #c46827;
                --accent-dark: #8f4617;
                --panel: rgba(255, 252, 247, 0.9);
                --line: rgba(31, 42, 48, 0.10);
                --success: #2f855a;
                --danger: #c53030;
                --warning: #d69e2e;
            }

            html, body, [class*="css"] {
                font-family: "Trebuchet MS", "Aptos", sans-serif;
            }

            .stApp {
                background:
                    radial-gradient(circle at top left, rgba(196, 104, 39, 0.12), transparent 30%),
                    radial-gradient(circle at top right, rgba(47, 133, 90, 0.10), transparent 28%),
                    linear-gradient(180deg, #f6f1e7 0%, #efe5d6 100%);
                color: var(--ink);
            }

            .block-container {
                padding-top: 1.5rem;
                padding-bottom: 2rem;
            }

            div[data-testid="stMetric"] {
                background: var(--panel);
                border: 1px solid var(--line);
                border-radius: 18px;
                padding: 0.7rem 0.9rem;
                box-shadow: 0 14px 40px rgba(31, 42, 48, 0.08);
            }

            div[data-testid="stMetricLabel"] {
                color: var(--muted);
            }

            .ops-hero {
                background: linear-gradient(135deg, rgba(31, 42, 48, 0.96), rgba(64, 52, 42, 0.92));
                border-radius: 26px;
                padding: 1.6rem 1.8rem;
                margin-bottom: 1rem;
                color: #f7f2ea;
                border: 1px solid rgba(255, 255, 255, 0.08);
                box-shadow: 0 24px 60px rgba(31, 42, 48, 0.20);
            }

            .ops-eyebrow {
                letter-spacing: 0.14rem;
                text-transform: uppercase;
                font-size: 0.76rem;
                color: #e4b98b;
                margin-bottom: 0.6rem;
            }

            .ops-hero h1 {
                margin: 0;
                font-size: 2.4rem;
                line-height: 1.05;
            }

            .ops-hero p {
                margin: 0.7rem 0 0;
                max-width: 56rem;
                color: rgba(247, 242, 234, 0.82);
                font-size: 1.02rem;
            }

            .ops-chip-row {
                display: flex;
                gap: 0.55rem;
                flex-wrap: wrap;
                margin-top: 1rem;
            }

            .ops-chip {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 999px;
                padding: 0.4rem 0.75rem;
                font-size: 0.88rem;
                color: #f7f2ea;
            }

            .device-card {
                background: var(--panel);
                border: 1px solid var(--line);
                border-radius: 22px;
                padding: 1rem;
                box-shadow: 0 16px 35px rgba(31, 42, 48, 0.08);
                min-height: 220px;
            }

            .device-card h4 {
                margin: 0 0 0.6rem 0;
                font-size: 1.15rem;
                color: var(--ink);
            }

            .device-status {
                display: inline-flex;
                align-items: center;
                gap: 0.35rem;
                border-radius: 999px;
                padding: 0.32rem 0.7rem;
                font-size: 0.82rem;
                font-weight: 700;
                margin-bottom: 0.75rem;
            }

            .device-status.online {
                background: rgba(47, 133, 90, 0.12);
                color: var(--success);
            }

            .device-status.offline {
                background: rgba(197, 48, 48, 0.12);
                color: var(--danger);
            }

            .device-state {
                margin-bottom: 0.8rem;
                font-size: 0.9rem;
                color: var(--muted);
            }

            .device-grid {
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 0.55rem 0.9rem;
            }

            .device-metric {
                background: rgba(231, 220, 203, 0.45);
                border-radius: 14px;
                padding: 0.65rem 0.7rem;
                border: 1px solid rgba(31, 42, 48, 0.06);
            }

            .device-metric label {
                display: block;
                font-size: 0.72rem;
                text-transform: uppercase;
                letter-spacing: 0.08rem;
                color: var(--muted);
                margin-bottom: 0.18rem;
            }

            .device-metric strong {
                font-size: 1rem;
                color: var(--ink);
            }

            .section-head {
                margin-top: 0.35rem;
                margin-bottom: 0.25rem;
            }

            .section-head h3 {
                margin-bottom: 0.15rem;
            }

            .section-head p {
                color: var(--muted);
                margin-top: 0;
            }

            .streamlit-expanderHeader {
                font-size: 1rem;
                font-weight: 700;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def safe_get_json(url: str):
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        return response.json(), None
    except requests.RequestException as exc:
        return None, str(exc)


def parse_influx_csv(text: str) -> pd.DataFrame:
    frames = []
    header = None
    rows = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parsed = next(csv.reader([raw_line]))
        if parsed and parsed[0] == "" and len(parsed) > 2 and parsed[1] == "result":
            if header and rows:
                frames.append(pd.DataFrame(rows, columns=header))
            header = parsed
            rows = []
            continue

        if header is None:
            continue

        if len(parsed) < len(header):
            parsed += [""] * (len(header) - len(parsed))
        rows.append(parsed[: len(header)])

    if header and rows:
        frames.append(pd.DataFrame(rows, columns=header))

    if not frames:
        return pd.DataFrame()

    return pd.concat(frames, ignore_index=True, sort=False)


@st.cache_data(ttl=20)
def fetch_catalog_assets() -> Tuple[List[Dict], str]:
    payload, error = safe_get_json(f"{CATALOG_URL}/assets")
    if error:
        return [], error
    return payload or [], ""


@st.cache_data(ttl=10)
def fetch_controller_status() -> Tuple[Dict, str]:
    payload, error = safe_get_json(f"{CONTROLLER_URL}/status")
    if error:
        return {}, error
    return payload or {}, ""


def build_flux_filter(values: List[str], column: str) -> str:
    if not values:
        return "true"
    comparisons = [f'r.{column} == "{value}"' for value in values]
    return " or ".join(comparisons)


@st.cache_data(ttl=10)
def influx_query(query: str) -> Tuple[pd.DataFrame, str]:
    if not INFLUX_TOKEN:
        return pd.DataFrame(), "INFLUX_TOKEN is not configured for the dashboard service."

    try:
        response = requests.post(
            f"{INFLUX_URL}/api/v2/query",
            params={"org": INFLUX_ORG},
            headers={
                "Authorization": f"Token {INFLUX_TOKEN}",
                "Accept": "application/csv",
                "Content-Type": "application/vnd.flux",
            },
            data=query.encode("utf-8"),
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        return pd.DataFrame(), str(exc)

    text = response.text.strip()
    if not text:
        return pd.DataFrame(), ""

    frame = parse_influx_csv(text)
    if frame.empty:
        return frame, ""

    frame = frame.dropna(axis=1, how="all")
    frame = frame.drop(
        columns=["result", "table", "_start", "_stop"],
        errors="ignore",
    )
    if "_time" in frame.columns:
        frame["_time"] = pd.to_datetime(frame["_time"], errors="coerce")
    return frame, ""


@st.cache_data(ttl=10)
def load_influx_frames(selected_assets: Tuple[str, ...], window: str):
    warehouse_filter = build_flux_filter(list(selected_assets), "warehouse_id")

    history_query = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: {window})
  |> filter(fn: (r) => r._measurement == "warehouse")
  |> filter(fn: (r) => r._field == "temperature" or r._field == "humidity" or r._field == "stock" or r._field == "state_code")
  |> filter(fn: (r) => {warehouse_filter})
  |> keep(columns: ["_time", "warehouse_id", "state", "_field", "_value"])
'''

    health_latest_query = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -30m)
  |> filter(fn: (r) => r._measurement == "device_health")
  |> filter(fn: (r) => r._field == "online" or r._field == "last_seen_age_sec")
  |> filter(fn: (r) => {warehouse_filter})
  |> last()
  |> pivot(rowKey: ["warehouse_id", "status", "_time"], columnKey: ["_field"], valueColumn: "_value")
  |> keep(columns: ["warehouse_id", "status", "_time", "online", "last_seen_age_sec"])
'''

    health_history_query = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: {window})
  |> filter(fn: (r) => r._measurement == "device_health")
  |> filter(fn: (r) => r._field == "online")
  |> filter(fn: (r) => {warehouse_filter})
  |> keep(columns: ["_time", "warehouse_id", "status", "_field", "_value"])
'''

    events_query = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: {window})
  |> filter(fn: (r) => r._measurement == "warehouse_event")
  |> filter(fn: (r) => r._field == "value")
  |> filter(fn: (r) => r.event == "DEVICE_OFFLINE" or r.event == "DEVICE_ONLINE" or r.event == "ANOMALY_DETECTED" or r.event == "ACTUATOR_COMMAND_DISPATCHED" or r.event == "ACTUATOR_CONFIRMATION")
  |> filter(fn: (r) => {warehouse_filter})
  |> keep(columns: ["_time", "warehouse_id", "event", "anomaly_type", "source", "status", "command_id"])
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: 50)
'''

    history, history_error = influx_query(history_query)
    latest_health, health_error = influx_query(health_latest_query)
    health_history, health_history_error = influx_query(health_history_query)
    events, events_error = influx_query(events_query)

    return {
        "history": history,
        "latest_health": latest_health,
        "health_history": health_history,
        "events": events,
        "errors": [
            error for error in [history_error, health_error, health_history_error, events_error]
            if error
        ],
    }


def latest_metric_snapshot(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty:
        return pd.DataFrame()

    ordered = history.sort_values("_time")
    latest_values = (
        ordered.groupby(["warehouse_id", "_field"], as_index=False)
        .tail(1)
        .pivot(index="warehouse_id", columns="_field", values="_value")
        .reset_index()
    )

    latest_state = (
        ordered.groupby("warehouse_id", as_index=False)
        .tail(1)[["warehouse_id", "state", "_time"]]
        .rename(columns={"_time": "telemetry_time"})
    )

    return latest_values.merge(latest_state, on="warehouse_id", how="left")


def normalize_health_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame

    result = frame.copy()
    if "online" in result.columns:
        result["online"] = pd.to_numeric(result["online"], errors="coerce").fillna(0).astype(int)
    if "last_seen_age_sec" in result.columns:
        result["last_seen_age_sec"] = pd.to_numeric(result["last_seen_age_sec"], errors="coerce")
    return result.sort_values(["online", "warehouse_id"], ascending=[True, True])


def normalize_numeric_history(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame

    result = frame.copy()
    if "_value" in result.columns:
        result["_value"] = pd.to_numeric(result["_value"], errors="coerce")
    return result


def humanize_action(action: Dict) -> str:
    if not action:
        return "Stable"
    return ", ".join(sorted(key.replace("_", " ") for key, value in action.items() if value)) or "Stable"


def format_metric_value(value, suffix: str, decimals: int = 1) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{value:.{decimals}f} {suffix}".strip()


def render_device_card(asset_id: str, health_row: Dict, latest_row: Dict, status_row: Dict) -> None:
    online = bool(health_row.get("online", 0))
    status_class = "online" if online else "offline"
    online_label = "ONLINE" if online else "OFFLINE"
    state = status_row.get("state") or latest_row.get("state") or "UNKNOWN"
    last_seen_age = health_row.get("last_seen_age_sec")
    age_text = f"{last_seen_age:.0f}s" if pd.notna(last_seen_age) else "n/a"
    temp = latest_row.get("temperature")
    humidity = latest_row.get("humidity")
    stock = latest_row.get("stock")

    st.markdown(
        f"""
        <div class="device-card">
            <h4>{asset_id}</h4>
            <div class="device-status {status_class}">{online_label}</div>
            <div class="device-state">Controller state: <strong style="color:{STATE_COLORS.get(state, STATE_COLORS['UNKNOWN'])};">{state}</strong></div>
            <div class="device-grid">
                <div class="device-metric">
                    <label>Temperature</label>
                    <strong>{format_metric_value(temp, 'C')}</strong>
                </div>
                <div class="device-metric">
                    <label>Humidity</label>
                    <strong>{format_metric_value(humidity, '%')}</strong>
                </div>
                <div class="device-metric">
                    <label>Stock</label>
                    <strong>{format_metric_value(stock, '', 0)}</strong>
                </div>
                <div class="device-metric">
                    <label>Last Seen</label>
                    <strong>{age_text}</strong>
                </div>
            </div>
            <div class="device-state" style="margin-top:0.85rem;">Actions: {humanize_action(status_row.get('action', {}))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def empty_safe_number(value, decimals=1, default="n/a"):
    if value is None or pd.isna(value):
        return default
    return f"{value:.{decimals}f}"


inject_styles()

assets, assets_error = fetch_catalog_assets()
asset_ids = [asset["asset_id"] for asset in assets]

with st.sidebar:
    st.header("Control Room")
    selected_assets = st.multiselect(
        "Warehouses",
        asset_ids,
        default=asset_ids,
    )
    if not selected_assets:
        selected_assets = asset_ids

    selected_window_label = st.selectbox(
        "Telemetry window",
        list(TIME_WINDOWS.keys()),
        index=0,
    )
    selected_window = TIME_WINDOWS[selected_window_label]

    if st.button("Refresh live data", width="stretch"):
        st.cache_data.clear()
        st.rerun()

    st.caption("The dashboard polls services every few seconds through Streamlit caching. Use the refresh button for an immediate read.")

st.markdown(
    """
    <div class="ops-hero">
        <div class="ops-eyebrow">Warehouse operations cockpit</div>
        <h1>Smart Warehouse Ops Hub</h1>
        <p>Live controller decisions, device connectivity, anomaly activity, and warehouse telemetry in one place. This view mixes direct InfluxDB charts with a Grafana embed so operations can inspect both the curated dashboard and raw warehouse trends.</p>
        <div class="ops-chip-row">
            <div class="ops-chip">MQTT live control loop</div>
            <div class="ops-chip">InfluxDB analytics</div>
            <div class="ops-chip">Grafana board embedded</div>
            <div class="ops-chip">Device online/offline tracking</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

controller_status, controller_error = fetch_controller_status()
influx_frames = load_influx_frames(tuple(sorted(selected_assets)), selected_window)
history = influx_frames["history"]
latest_health = normalize_health_frame(influx_frames["latest_health"])
history = normalize_numeric_history(history)
health_history = normalize_numeric_history(influx_frames["health_history"])
events = influx_frames["events"]
latest_metrics = latest_metric_snapshot(history)

for error in influx_frames["errors"]:
    st.warning(f"InfluxDB query warning: {error}")
if assets_error:
    st.warning(f"Catalog warning: {assets_error}")
if controller_error:
    st.warning(f"Controller warning: {controller_error}")

current_asset_count = len(selected_assets)
online_count = int(latest_health["online"].sum()) if not latest_health.empty else 0
offline_count = max(current_asset_count - online_count, 0)
anomaly_count = sum(
    1 for asset_id, decision in controller_status.items()
    if asset_id in selected_assets and decision.get("state") == "ANOMALY"
)
latest_avg_temp = latest_metrics["temperature"].mean() if not latest_metrics.empty and "temperature" in latest_metrics else None

kpi_cols = st.columns(5)
kpi_cols[0].metric("Warehouses", current_asset_count)
kpi_cols[1].metric("Online devices", online_count)
kpi_cols[2].metric("Offline devices", offline_count)
kpi_cols[3].metric("Current anomalies", anomaly_count)
kpi_cols[4].metric("Average temperature", format_metric_value(latest_avg_temp, "C"))

with st.expander("Add a warehouse", expanded=False):
    with st.form("add_warehouse"):
        add_cols = st.columns(3)
        warehouse_id = add_cols[0].text_input("Warehouse ID")
        max_temp = add_cols[1].number_input("Critical temperature", value=30)
        min_stock = add_cols[2].number_input("Minimum stock", value=20)

        anomaly_cols = st.columns(3)
        anomaly_high = anomaly_cols[0].number_input("Anomaly high", value=45)
        anomaly_low = anomaly_cols[1].number_input("Anomaly low", value=-5)
        humidity_high = anomaly_cols[2].number_input("Humidity anomaly", value=95)

        submitted = st.form_submit_button("Create warehouse", width="stretch")

        if submitted:
            warehouse_id = warehouse_id.strip()
            if not warehouse_id:
                st.error("Warehouse ID is required.")
            else:
                payload = {
                    "asset_id": warehouse_id,
                    "mqtt_sensor_topic": f"assets/{warehouse_id}/sensors",
                    "mqtt_actuator_topic": f"assets/{warehouse_id}/actuator",
                    "rules": {
                        "temp_warning": int(max_temp * 0.8),
                        "temp_critical": int(max_temp),
                        "stock_low": int(min_stock),
                        "stock_overload": 90,
                        "temp_anomaly_high": int(anomaly_high),
                        "temp_anomaly_low": int(anomaly_low),
                        "humidity_anomaly_high": int(humidity_high),
                    },
                }
                try:
                    response = requests.post(
                        f"{CATALOG_URL}/add_asset",
                        json=payload,
                        timeout=5,
                    )
                    if response.ok:
                        st.success(f"Warehouse {warehouse_id} added to the catalog.")
                        st.cache_data.clear()
                    else:
                        st.error(f"Catalog rejected the request: {response.text}")
                except requests.RequestException as exc:
                    st.error(f"Catalog request failed: {exc}")

st.markdown(
    """
    <div class="section-head">
        <h3>Fleet Snapshot</h3>
        <p>Live warehouse cards combine controller decisions, latest telemetry, and device connectivity from InfluxDB.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

snapshot_by_asset = {
    row["warehouse_id"]: row
    for _, row in latest_metrics.iterrows()
} if not latest_metrics.empty else {}
health_by_asset = {
    row["warehouse_id"]: row
    for _, row in latest_health.iterrows()
} if not latest_health.empty else {}

if selected_assets:
    card_columns = st.columns(min(3, len(selected_assets)))
    for index, asset_id in enumerate(selected_assets):
        health_row = health_by_asset.get(asset_id, {})
        latest_row = snapshot_by_asset.get(asset_id, {})
        status_row = controller_status.get(asset_id, {})
        with card_columns[index % len(card_columns)]:
            render_device_card(asset_id, health_row, latest_row, status_row)
else:
    st.info("No warehouses selected.")

telemetry_tab, events_tab, grafana_tab = st.tabs([
    "Telemetry from InfluxDB",
    "Events and device health",
    "Grafana live board",
])

with telemetry_tab:
    if history.empty:
        st.info("No telemetry found in InfluxDB for the selected window yet.")
    else:
        chart_cols = st.columns(2)
        for position, field in enumerate(["temperature", "humidity"]):
            subset = history[history["_field"] == field].copy()
            unit = "C" if field == "temperature" else "%"
            title = "Temperature trend" if field == "temperature" else "Humidity trend"
            fig = px.line(
                subset,
                x="_time",
                y="_value",
                color="warehouse_id",
                template=CHART_TEMPLATE,
                markers=True,
                color_discrete_sequence=["#c46827", "#2b6cb0", "#2f855a", "#805ad5"],
            )
            fig.update_layout(
                title=title,
                legend_title_text="Warehouse",
                margin=dict(l=10, r=10, t=50, b=10),
                paper_bgcolor="rgba(255,255,255,0.75)",
                plot_bgcolor="rgba(255,255,255,0.92)",
                xaxis_title=None,
                yaxis_title=unit,
            )
            chart_cols[position].plotly_chart(fig, width="stretch")

        bottom_cols = st.columns([1.3, 1])

        stock_subset = history[history["_field"] == "stock"].copy()
        stock_fig = px.line(
            stock_subset,
            x="_time",
            y="_value",
            color="warehouse_id",
            template=CHART_TEMPLATE,
            line_shape="hv",
            color_discrete_sequence=["#c46827", "#2b6cb0", "#2f855a", "#805ad5"],
        )
        stock_fig.update_layout(
            title="Stock trend",
            legend_title_text="Warehouse",
            margin=dict(l=10, r=10, t=50, b=10),
            paper_bgcolor="rgba(255,255,255,0.75)",
            plot_bgcolor="rgba(255,255,255,0.92)",
            xaxis_title=None,
            yaxis_title="Units",
        )
        bottom_cols[0].plotly_chart(stock_fig, width="stretch")

        state_subset = history[history["_field"] == "state_code"].copy()
        state_subset["state_label"] = state_subset["_value"].round().astype(int).map(STATE_NAMES)
        state_fig = px.scatter(
            state_subset,
            x="_time",
            y="warehouse_id",
            color="state_label",
            template=CHART_TEMPLATE,
            color_discrete_map=STATE_COLORS,
        )
        state_fig.update_layout(
            title="Controller state changes",
            legend_title_text="State",
            margin=dict(l=10, r=10, t=50, b=10),
            paper_bgcolor="rgba(255,255,255,0.75)",
            plot_bgcolor="rgba(255,255,255,0.92)",
            xaxis_title=None,
            yaxis_title=None,
        )
        bottom_cols[1].plotly_chart(state_fig, width="stretch")

        if not latest_metrics.empty:
            latest_display = latest_metrics.copy()
            latest_display["temperature"] = latest_display["temperature"].map(lambda value: round(value, 1))
            latest_display["humidity"] = latest_display["humidity"].map(lambda value: round(value, 1))
            latest_display["stock"] = latest_display["stock"].map(lambda value: int(round(value)))
            st.dataframe(
                latest_display[["warehouse_id", "state", "temperature", "humidity", "stock"]],
                width="stretch",
                hide_index=True,
            )

with events_tab:
    top_cols = st.columns([1.4, 1])

    if health_history.empty:
        top_cols[0].info("No device health history available yet.")
    else:
        health_history = health_history.copy()
        health_history["_value"] = pd.to_numeric(health_history["_value"], errors="coerce")
        health_fig = px.line(
            health_history,
            x="_time",
            y="_value",
            color="warehouse_id",
            template=CHART_TEMPLATE,
            line_shape="hv",
            markers=True,
            color_discrete_sequence=["#c46827", "#2b6cb0", "#2f855a", "#805ad5"],
        )
        health_fig.update_layout(
            title="Device connectivity timeline",
            legend_title_text="Warehouse",
            margin=dict(l=10, r=10, t=50, b=10),
            paper_bgcolor="rgba(255,255,255,0.75)",
            plot_bgcolor="rgba(255,255,255,0.92)",
            xaxis_title=None,
            yaxis_title="Online = 1",
            yaxis=dict(range=[-0.1, 1.1], tickmode="array", tickvals=[0, 1]),
        )
        top_cols[0].plotly_chart(health_fig, width="stretch")

    if latest_health.empty:
        top_cols[1].info("No current device status rows found.")
    else:
        health_display = latest_health.copy()
        health_display["last_seen_age_sec"] = health_display["last_seen_age_sec"].map(lambda value: round(value, 1))
        top_cols[1].dataframe(
            health_display[["warehouse_id", "status", "online", "last_seen_age_sec", "_time"]]
            .rename(columns={
                "warehouse_id": "warehouse",
                "status": "status",
                "online": "online",
                "last_seen_age_sec": "last_seen_s",
                "_time": "updated_at",
            }),
            width="stretch",
            hide_index=True,
        )

    if events.empty:
        st.info("No recent events for the selected time window.")
    else:
        event_fig = px.scatter(
            events,
            x="_time",
            y="warehouse_id",
            color="event",
            symbol="event",
            template=CHART_TEMPLATE,
            hover_data=["source", "anomaly_type", "status", "command_id"],
            color_discrete_map=EVENT_COLORS,
        )
        event_fig.update_layout(
            title="Recent warehouse events",
            legend_title_text="Event",
            margin=dict(l=10, r=10, t=50, b=10),
            paper_bgcolor="rgba(255,255,255,0.75)",
            plot_bgcolor="rgba(255,255,255,0.92)",
            xaxis_title=None,
            yaxis_title=None,
        )
        st.plotly_chart(event_fig, width="stretch")

        event_table = events.copy()
        event_table["_time"] = event_table["_time"].dt.strftime("%Y-%m-%d %H:%M:%S")
        st.dataframe(
            event_table[["_time", "warehouse_id", "event", "anomaly_type", "source", "status", "command_id"]]
            .rename(columns={
                "_time": "time",
                "warehouse_id": "warehouse",
            }),
            width="stretch",
            hide_index=True,
        )

with grafana_tab:
    from_window = f"now{selected_window}"
    grafana_url = (
        f"{GRAFANA_PUBLIC_URL}/d/warehouse-metrics/warehouse-metrics"
        f"?orgId=1&from={from_window}&to=now&kiosk"
    )
    st.caption("Embedded Grafana uses anonymous viewer mode for this local demo so the panels can render inside Streamlit.")
    components.html(
        f'<iframe src="{grafana_url}" width="100%" height="900" frameborder="0"></iframe>',
        height=920,
    )

if assets:
    st.markdown(
        """
        <div class="section-head">
            <h3>Catalog configuration</h3>
            <p>Configured rules from the catalog service. This helps verify that what the controller enforces matches the registered warehouse profile.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    asset_rows = []
    for asset in assets:
        rules = asset.get("rules", {})
        asset_rows.append({
            "warehouse_id": asset["asset_id"],
            "temp_warning": rules.get("temp_warning"),
            "temp_critical": rules.get("temp_critical"),
            "stock_low": rules.get("stock_low"),
            "stock_overload": rules.get("stock_overload"),
            "temp_anomaly_high": rules.get("temp_anomaly_high"),
            "temp_anomaly_low": rules.get("temp_anomaly_low"),
            "humidity_anomaly_high": rules.get("humidity_anomaly_high"),
        })
    st.dataframe(pd.DataFrame(asset_rows), width="stretch", hide_index=True)
