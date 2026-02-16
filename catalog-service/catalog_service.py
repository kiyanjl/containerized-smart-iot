import cherrypy
import json
import os


class CatalogService:
    exposed = True

    def __init__(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.catalog_file = os.path.join(base_dir, "catalog.json")

    def _load(self):
        with open(self.catalog_file, "r") as f:
            return json.load(f)

    def _save(self, data):
        with open(self.catalog_file, "w") as f:
            json.dump(data, f, indent=2)

    @cherrypy.tools.json_out()
    def GET(self, *uri, **params):
        data = self._load()

        if not uri:
            return {"service": "Catalog Service Running"}

        if uri[0] == "assets":
            if len(uri) == 1:
                return data["assets"]

            asset_id = uri[1]
            for asset in data["assets"]:
                if asset["asset_id"] == asset_id:
                    return asset

            cherrypy.response.status = 404
            return {"error": "Asset not found"}

        return {"error": "Invalid endpoint"}

    @cherrypy.tools.json_in()
    @cherrypy.tools.json_out()
    def POST(self, *uri, **params):
        data = self._load()

        if uri and uri[0] == "add_asset":
            new_asset = cherrypy.request.json

            for asset in data["assets"]:
                if asset["asset_id"] == new_asset["asset_id"]:
                    raise cherrypy.HTTPError(409, "Asset already exists")

            data["assets"].append(new_asset)
            self._save(data)

            return {"status": "Asset added"}

        if uri and uri[0] == "delete_asset":
            asset_id = cherrypy.request.json.get("asset_id")
            data["assets"] = [
                a for a in data["assets"] if a["asset_id"] != asset_id
            ]
            self._save(data)

            return {"status": "Asset removed"}

        return {"error": "Invalid POST endpoint"}


if __name__ == "__main__":
    conf = {
        "/": {
            "request.dispatch": cherrypy.dispatch.MethodDispatcher()
        }
    }

    cherrypy.tree.mount(CatalogService(), "/", conf)

    cherrypy.config.update({
        "server.socket_host": "0.0.0.0",
        "server.socket_port": 8080
    })

    cherrypy.engine.start()
    cherrypy.engine.block()
