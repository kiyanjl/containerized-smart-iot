#!/usr/bin/env python3
"""Helper script to add a new warehouse to the catalog."""

import requests
import json
import sys

CATALOG_URL = "http://localhost:18080"

def add_warehouse(asset_id, name, warehouse_type="standard", location="Building C"):
    """Add a new warehouse to the catalog."""
    
    # Default rules based on warehouse type
    type_rules = {
        "cold": {
            "temp_warning": 8,
            "temp_critical": 12,
            "stock_low": 50,
            "stock_overload": 90,
            "temp_anomaly_high": 18,
            "temp_anomaly_low": -8,
            "humidity_anomaly_high": 95
        },
        "standard": {
            "temp_warning": 30,
            "temp_critical": 40,
            "stock_low": 20,
            "stock_overload": 90,
            "temp_anomaly_high": 46,
            "temp_anomaly_low": -5,
            "humidity_anomaly_high": 96
        },
        "hazard": {
            "temp_warning": 20,
            "temp_critical": 25,
            "stock_low": 5,
            "stock_overload": 90,
            "temp_anomaly_high": 47,
            "temp_anomaly_low": -12,
            "humidity_anomaly_high": 95
        }
    }
    
    new_asset = {
        "asset_id": asset_id,
        "name": name,
        "type": warehouse_type,
        "location": location,
        "capacity": 100,
        "owner": "New Owner",
        "contact": "new.owner@company.com",
        "mqtt_sensor_topic": f"assets/{asset_id}/sensors",
        "mqtt_actuator_topic": f"assets/{asset_id}/actuator",
        "rules": type_rules.get(warehouse_type, type_rules["standard"])
    }
    
    try:
        response = requests.post(
            f"{CATALOG_URL}/add_asset",
            json=new_asset,
            timeout=5
        )
        response.raise_for_status()
        print(f"✅ Successfully added warehouse: {name} ({asset_id})")
        print(f"📋 Type: {warehouse_type}")
        print(f"📍 Location: {location}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to add warehouse: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response: {e.response.text}")
        return False

def list_warehouses():
    """List all warehouses in the catalog."""
    try:
        response = requests.get(f"{CATALOG_URL}/assets", timeout=5)
        response.raise_for_status()
        assets = response.json()
        print(f"\n📦 Current Warehouses ({len(assets)}):")
        print("-" * 60)
        for asset in assets:
            print(f"  • {asset['name']} ({asset['asset_id']})")
            print(f"    Type: {asset.get('type', 'standard')} | Location: {asset.get('location', 'Unknown')}")
        print()
        return assets
    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to list warehouses: {e}")
        return []

def delete_warehouse(asset_id):
    """Delete a warehouse from the catalog."""
    try:
        response = requests.post(
            f"{CATALOG_URL}/delete_asset",
            json={"asset_id": asset_id},
            timeout=5
        )
        response.raise_for_status()
        print(f"✅ Successfully deleted warehouse: {asset_id}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to delete warehouse: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response: {e.response.text}")
        return False

def main():
    print("🏭 Smart IoT Warehouse - Catalog Manager")
    print("=" * 60)
    
    # Show current warehouses
    list_warehouses()
    
    # Example: Add a new warehouse
    print("Adding example warehouse...")
    add_warehouse(
        asset_id="warehouse_new",
        name="New Smart Warehouse",
        warehouse_type="standard",
        location="Building C, Floor 1"
    )
    
    print("\n✅ Done! Check the dashboard at http://localhost:18501")
    print("The sensor simulator will automatically start simulating for the new warehouse!")

if __name__ == "__main__":
    main()
