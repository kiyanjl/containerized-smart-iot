import streamlit as st
import requests

CATALOG_URL = "http://catalog-service:8080"
CONTROLLER_URL = "http://controller-service:8001"

st.set_page_config(page_title="Smart IoT Dashboard", layout="wide")
st.title("🏭 Smart Warehouse Control Panel")

# -------------------------
# ADD WAREHOUSE (NO CODING!)
# -------------------------
st.header("➕ Add New Warehouse")

with st.form("add_warehouse"):
    wid = st.text_input("Warehouse ID")
    temp = st.number_input("Max Temperature", value=30)
    stock = st.number_input("Min Stock Level", value=20)
    submitted = st.form_submit_button("Add")

    if submitted:
        payload = {
            "warehouse_id": wid,
            "temp_max": temp,
            "stock_min": stock
        }
        r = requests.post(f"{CATALOG_URL}/add_warehouse", json=payload)
        st.success("Warehouse added dynamically!")

# -------------------------
# SYSTEM STATUS
# -------------------------
st.header("📊 Live System Status")

res = requests.get(f"{CONTROLLER_URL}/status")
if res.status_code == 200:
    data = res.json()
    st.json(data)
else:
    st.error("Controller not reachable")
