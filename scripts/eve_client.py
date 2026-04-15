"""EVE-NG REST API client.

Handles authentication, lab management, node creation, and network wiring.
All credentials come from the credentials module — never hardcoded.

Usage:
    from scripts.eve_client import EveNgClient
    from scripts.credentials import require_credentials

    creds = require_credentials("eve_ng_host", "eve_ng_password")
    client = EveNgClient(creds.eve_ng_host, creds.eve_ng_username, creds.eve_ng_password)
    client.login()
"""

from __future__ import annotations

from typing import Any

import requests
import urllib3

# EVE-NG uses self-signed certs — suppress the warning globally for this module.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class EveNgClient:
    """REST API client for EVE-NG."""

    def __init__(self, host: str, username: str, password: str) -> None:
        self.base_url = f"https://{host}/api"
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        # EVE-NG uses a self-signed certificate — disable verification.
        self.session.verify = False
        self._authenticated = False

    def login(self) -> None:
        """Authenticate and establish a session cookie."""
        resp = self.session.post(
            f"{self.base_url}/auth/login",
            json={"username": self.username, "password": self.password, "html5": "-1"},
        )
        resp.raise_for_status()
        self._authenticated = True

    def logout(self) -> None:
        """Close the API session."""
        if self._authenticated:
            self.session.get(f"{self.base_url}/auth/logout")
            self._authenticated = False

    def _request_with_retry(
        self, method: str, path: str, data: dict | None = None, retries: int = 3
    ) -> Any:
        """Make an API request with retry on lab-lock (400/412) errors.

        EVE-NG holds a file lock during lab saves. Rapid successive calls
        can hit this lock. Retry with a short backoff to let it release.
        """
        import time

        url = f"{self.base_url}{path}"
        for attempt in range(retries):
            if method == "GET":
                resp = self.session.get(url)
            elif method == "POST":
                resp = self.session.post(url, json=data or {})
            elif method == "PUT":
                resp = self.session.put(url, json=data or {})
            elif method == "DELETE":
                resp = self.session.delete(url)
            else:
                raise ValueError(f"Unknown method: {method}")

            if resp.status_code in (400, 412) and attempt < retries - 1:
                body = resp.text.lower()
                if "lock" in body or "busy" in body or "60061" in body:
                    time.sleep(0.5 * (attempt + 1))
                    continue
            resp.raise_for_status()
            return resp.json().get("data", resp.json())

        resp.raise_for_status()
        return resp.json().get("data", resp.json())

    def _get(self, path: str) -> Any:
        return self._request_with_retry("GET", path)

    def _post(self, path: str, data: dict | None = None) -> Any:
        return self._request_with_retry("POST", path, data)

    def _put(self, path: str, data: dict | None = None) -> Any:
        return self._request_with_retry("PUT", path, data)

    def _delete(self, path: str) -> Any:
        resp = self.session.delete(f"{self.base_url}{path}")
        resp.raise_for_status()
        return resp.json().get("data", resp.json())

    # --- Lab management ---

    def create_lab(self, name: str, path: str = "/", description: str = "") -> dict:
        """Create a new lab.

        Args:
            name: Lab name (without .unl extension).
            path: Folder path where the lab is created. Defaults to root "/".
            description: Lab description.
        """
        return self._post(
            "/labs",
            {"path": path, "name": name, "description": description},
        )

    def get_lab(self, lab_path: str) -> dict:
        """Get lab details."""
        return self._get(f"/labs{lab_path}")

    def delete_lab(self, lab_path: str) -> dict:
        """Delete a lab."""
        return self._delete(f"/labs{lab_path}")

    def list_labs(self, path: str = "/") -> dict:
        """List labs in a folder."""
        return self._get(f"/folders{path}")

    # --- Node management ---

    def add_node(
        self,
        lab_path: str,
        *,
        name: str,
        template: str,
        image: str,
        ram: int = 1024,
        cpu: int = 1,
        ethernet: int = 4,
        left: int = 0,
        top: int = 0,
        startup_config: str = "0",
    ) -> dict:
        """Add a node to a lab.

        Args:
            startup_config: EVE-NG startup config mode.
                "0" = no startup config, just boot (MUST be "0", not "none").
                "none" maps to "Unconfigured" in EVE-NG which causes boot failure.
                Pass a base64-encoded config string for startup injection.
        """
        data: dict[str, Any] = {
            "type": "qemu",
            "template": template,
            "image": image,
            "name": name,
            "ram": ram,
            "cpu": cpu,
            "ethernet": ethernet,
            "left": left,
            "top": top,
            "config": startup_config,
        }
        return self._post(f"/labs{lab_path}/nodes", data)

    def get_nodes(self, lab_path: str) -> dict:
        """Get all nodes in a lab."""
        return self._get(f"/labs{lab_path}/nodes")

    def start_node(self, lab_path: str, node_id: int) -> dict:
        """Start a node."""
        return self._get(f"/labs{lab_path}/nodes/{node_id}/start")

    def stop_node(self, lab_path: str, node_id: int) -> dict:
        """Stop a node."""
        return self._get(f"/labs{lab_path}/nodes/{node_id}/stop")

    def start_all_nodes(self, lab_path: str) -> dict:
        """Start all nodes in a lab."""
        return self._get(f"/labs{lab_path}/nodes/start")

    def stop_all_nodes(self, lab_path: str) -> dict:
        """Stop all nodes in a lab."""
        return self._get(f"/labs{lab_path}/nodes/stop")

    # --- Network management ---

    def add_network(
        self,
        lab_path: str,
        *,
        name: str,
        net_type: str = "bridge",
        left: int = 0,
        top: int = 0,
    ) -> dict:
        """Add a network (bridge) to a lab."""
        return self._post(
            f"/labs{lab_path}/networks",
            {"type": net_type, "name": name, "left": left, "top": top},
        )

    def get_networks(self, lab_path: str) -> dict:
        """Get all networks in a lab."""
        return self._get(f"/labs{lab_path}/networks")

    # --- Interface wiring ---

    def connect_node_to_network(
        self, lab_path: str, node_id: int, interface_id: int, network_id: int
    ) -> dict:
        """Connect a node interface to a network/bridge."""
        return self._put(
            f"/labs{lab_path}/nodes/{node_id}/interfaces",
            {str(interface_id): str(network_id)},
        )

    def get_node_interfaces(self, lab_path: str, node_id: int) -> dict:
        """Get interfaces for a node."""
        return self._get(f"/labs{lab_path}/nodes/{node_id}/interfaces")

    # --- Utility ---

    def get_templates(self) -> dict:
        """List available node templates."""
        return self._get("/list/templates/")

    def get_images(self, template: str) -> list:
        """List available images for a template."""
        return self._get(f"/list/templates/{template}")

    def status(self) -> dict:
        """Get EVE-NG server status."""
        return self._get("/status")
