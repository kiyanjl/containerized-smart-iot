import cherrypy
import json
import os

import paho.mqtt.client as mqtt

MQTT_BROKER = "mqtt-broker"
MQTT_PORT = 1883


class CatalogService:
    exposed = True

    def __init__(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.catalog_file = os.path.join(base_dir, "catalog.json")

        self.mqtt = mqtt.Client(client_id="catalog_service")
        self.mqtt.connect(MQTT_BROKER, MQTT_PORT)
        self.mqtt.loop_start()

    def _load(self):
        with open(self.catalog_file, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def _save(self, data):
        with open(self.catalog_file, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)

    def _find_asset(self, data, asset_id):
        for asset in data["assets"]:
            if asset["asset_id"] == asset_id:
                return asset
        return None

    def _validate_asset_payload(self, asset):
        required = [
            "asset_id",
            "mqtt_sensor_topic",
            "mqtt_actuator_topic",
            "rules",
        ]
        missing = [field for field in required if field not in asset]
        if missing:
            raise cherrypy.HTTPError(
                400,
                f"Missing required fields: {', '.join(missing)}",
            )

        if not isinstance(asset["rules"], dict):
            raise cherrypy.HTTPError(400, "rules must be a JSON object")

    @cherrypy.tools.json_out()
    def GET(self, *uri, **params):
        data = self._load()

        if not uri:
            return {"service": "Catalog Service Running"}

        if uri[0] == "health":
            return {
                "service": "Catalog Service",
                "status": "ok",
                "asset_count": len(data.get("assets", [])),
            }

        if uri[0] == "assets":
            if len(uri) == 1:
                return data["assets"]

            asset = self._find_asset(data, uri[1])
            if asset:
                return asset

            cherrypy.response.status = 404
            return {"error": "Asset not found"}

        if uri[0] == "broker":
            return data.get("broker", MQTT_BROKER)

        if uri[0] == "port":
            return data.get("port", MQTT_PORT)

        return {"error": "Invalid endpoint"}

    @cherrypy.tools.json_in()
    @cherrypy.tools.json_out()
    def POST(self, *uri, **params):
        data = self._load()

        if uri and uri[0] == "add_asset":
            new_asset = cherrypy.request.json
            self._validate_asset_payload(new_asset)

            if self._find_asset(data, new_asset["asset_id"]):
                raise cherrypy.HTTPError(409, "Asset already exists")

            data["assets"].append(new_asset)
            self._save(data)

            self.mqtt.publish(
                "catalog/config_updated",
                json.dumps({
                    "asset_id": new_asset["asset_id"],
                    "rules": new_asset.get("rules", {}),
                }),
                retain=True,
            )

            print(f"Published config for {new_asset['asset_id']}")
            return {"status": "Asset added"}

        if uri and uri[0] == "delete_asset":
            asset_id = cherrypy.request.json.get("asset_id")
            if not asset_id:
                raise cherrypy.HTTPError(400, "Missing asset_id")
            if not self._find_asset(data, asset_id):
                raise cherrypy.HTTPError(404, "Asset not found")

            data["assets"] = [
                asset for asset in data["assets"] if asset["asset_id"] != asset_id
            ]
            self._save(data)

            self.mqtt.publish(
                "catalog/config_updated",
                json.dumps({
                    "asset_id": asset_id,
                    "rules": {},
                }),
                retain=True,
            )

            print(f"Removed asset config {asset_id}")
            return {"status": "Asset removed"}

        if uri and uri[0] == "update_rules":
            asset_id = cherrypy.request.json.get("asset_id")
            new_rules = cherrypy.request.json.get("rules")
            if not asset_id:
                raise cherrypy.HTTPError(400, "Missing asset_id")
            if not isinstance(new_rules, dict):
                raise cherrypy.HTTPError(400, "rules must be a JSON object")

            asset = self._find_asset(data, asset_id)
            if not asset:
                raise cherrypy.HTTPError(404, "Asset not found")

            asset["rules"] = new_rules
            self._save(data)

            self.mqtt.publish(
                "catalog/config_updated",
                json.dumps({
                    "asset_id": asset_id,
                    "rules": new_rules,
                }),
                retain=True,
            )

            print(f"Rules updated for {asset_id}")
            return {"status": "rules updated"}

        return {"error": "Invalid POST endpoint"}


if __name__ == "__main__":
    conf = {
        "/": {
            "request.dispatch": cherrypy.dispatch.MethodDispatcher(),
        }
    }

    cherrypy.tree.mount(CatalogService(), "/", conf)

    cherrypy.config.update({
        "server.socket_host": "0.0.0.0",
        "server.socket_port": 8080,
    })

    cherrypy.engine.start()
    cherrypy.engine.block()
