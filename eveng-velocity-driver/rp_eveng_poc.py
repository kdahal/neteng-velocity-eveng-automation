#!/usr/bin/env python3
import sys
import os
import json
import time
import argparse
import requests

class EveNgClient:
    """Minimal wrapper around EVE-NG Pro REST API."""
    def __init__(self, base_url, username, password):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})

    def login(self):
        login_url = f"{self.base_url}/auth/login"
        payload = json.dumps({"username": self.username, "password": self.password, "html5": -1})
        res = self.session.post(login_url, data=payload, timeout=15)
        if res.status_code not in (200, 201):
            raise RuntimeError(f"EVE-NG login failed ({res.status_code}): {res.text}")

    def logout(self):
        try:
            self.session.get(f"{self.base_url}/auth/logout", timeout=5)
        except Exception:
            pass

    def create_lab(self, lab_path, lab_name):
        url = f"{self.base_url}/labs"
        payload = json.dumps({
            "path": lab_path,
            "name": lab_name,
            "version": "1"
        })
        res = self.session.post(url, data=payload, timeout=15)
        if res.status_code not in (200, 201):
            raise RuntimeError(f"Failed to create lab {lab_name}: {res.text}")

    def add_node(self, full_lab_path, template, node_name, left=200, top=200):
        url = f"{self.base_url}/labs{full_lab_path}/nodes"
        payload = json.dumps({
            "type": "qemu",
            "template": template,
            "name": node_name,
            "left": left,
            "top": top
        })
        res = self.session.post(url, data=payload, timeout=20)
        data = res.json()
        if res.status_code not in (200, 201) or data.get("status") != "success":
            raise RuntimeError(f"Failed to add node {node_name}: {res.text}")
        return data["data"]["id"]

    def add_bridge_network(self, full_lab_path, net_name, left=400, top=200):
        url = f"{self.base_url}/labs{full_lab_path}/networks"
        payload = json.dumps({
            "type": "bridge",
            "name": net_name,
            "left": left,
            "top": top
        })
        res = self.session.post(url, data=payload, timeout=15)
        data = res.json()
        if res.status_code not in (200, 201) or data.get("status") != "success":
            raise RuntimeError(f"Failed to add bridge network: {res.text}")
        return int(data["data"]["id"])

    def connect_interface(self, full_lab_path, node_id, iface_id, network_id):
        url = f"{self.base_url}/labs{full_lab_path}/nodes/{node_id}/interfaces"
        # In EVE-NG Pro, network_id must be an integer, not a string
        payload = json.dumps({str(iface_id): int(network_id)})
        res = self.session.put(url, data=payload, timeout=15)
        if res.status_code not in (200, 201):
            raise RuntimeError(f"Failed to connect node {node_id} iface {iface_id}: {res.text}")

    def start_node(self, full_lab_path, node_id):
        url = f"{self.base_url}/labs{full_lab_path}/nodes/{node_id}/start"
        res = self.session.get(url, timeout=30)
        if res.status_code not in (200, 201):
            raise RuntimeError(f"Failed to start node {node_id}: {res.text}")

    def stop_all_nodes(self, full_lab_path):
        url = f"{self.base_url}/labs{full_lab_path}/nodes/stop"
        try:
            self.session.get(url, timeout=30)
        except Exception:
            pass

    def delete_lab(self, full_lab_path):
        url = f"{self.base_url}/labs{full_lab_path}"
        res = self.session.delete(url, timeout=20)
        return res.status_code in (200, 204)


def cmd_get_ports(args):
    """
    Returns discovery port definitions to Velocity Core.
    Per Velocity Portfolio Driver Reference Guide (p. 10), status must be 'online'.
    """
    ports = [
        {"name": "eth1", "status": "online", "properties": {"ipAddress": "192.168.122.19"}},
        {"name": "eth2", "status": "online", "properties": {"ipAddress": "192.168.122.19"}}
    ]
    sys.stdout.write(json.dumps({"ports": ports}))
    sys.exit(0)


def cmd_init_resource(args):
    """Instantiates dynamic lab, 2 nodes, 1 interconnect link, then boots nodes."""
    res_id = args.reservation_id or os.environ.get("VELOCITY_PARAM_reservationId", "poc_res")
    lab_name = f"Vel_POC_{res_id}"
    lab_full_path = f"/{lab_name}.unl"

    eve_url = args.eve_url or os.environ.get("VELOCITY_PARAM_property_eve_url", "http://192.168.122.19/api")
    eve_username = args.eve_username or os.environ.get("VELOCITY_PARAM_property_eve_username", "admin")
    eve_password = args.eve_password or os.environ.get("VELOCITY_PARAM_property_eve_password", "eve")
    node_template = args.node_template or os.environ.get("VELOCITY_PARAM_property_node_template", "vyos")

    client = EveNgClient(eve_url, eve_username, eve_password)
    try:
        client.login()
        # Clean up any stale lab with the same name if exists
        try:
            client.delete_lab(lab_full_path)
        except Exception:
            pass

        client.create_lab("/", lab_name)

        # 1. Create bridge network
        net_id = client.add_bridge_network(lab_full_path, "P2P_Link", left=400, top=200)

        # 2. Instantiate 2 nodes
        n1_id = client.add_node(lab_full_path, node_template, "V-VyOS-01", left=200, top=200)
        n2_id = client.add_node(lab_full_path, node_template, "V-VyOS-02", left=600, top=200)

        time.sleep(1)

        # 3. Wire both eth0 (index 0) to P2P_Link using integer net_id
        client.connect_interface(lab_full_path, n1_id, iface_id=0, network_id=net_id)
        client.connect_interface(lab_full_path, n2_id, iface_id=0, network_id=net_id)

        # 4. Boot both nodes
        client.start_node(lab_full_path, n1_id)
        client.start_node(lab_full_path, n2_id)

        response = {
            "status": "SUCCESS",
            "message": f"Orchestrated lab '{lab_name}' provisioned and powered on.",
            "data": {
                "lab_path": lab_full_path,
                "node_1_id": n1_id,
                "node_2_id": n2_id,
                "network_id": net_id
            }
        }
        sys.stdout.write(json.dumps(response))
        sys.exit(0)

    except Exception as exc:
        err = {"status": "FAILED", "error": str(exc)}
        sys.stderr.write(json.dumps(err))
        sys.exit(1)
    finally:
        client.logout()


def cmd_term_resource(args):
    """Stops running nodes and destroys the ephemeral lab."""
    res_id = args.reservation_id or os.environ.get("VELOCITY_PARAM_reservationId", "poc_res")
    lab_name = f"Vel_POC_{res_id}"
    lab_full_path = f"/{lab_name}.unl"

    eve_url = args.eve_url or os.environ.get("VELOCITY_PARAM_property_eve_url", "http://192.168.122.19/api")
    eve_username = args.eve_username or os.environ.get("VELOCITY_PARAM_property_eve_username", "admin")
    eve_password = args.eve_password or os.environ.get("VELOCITY_PARAM_property_eve_password", "eve")

    client = EveNgClient(eve_url, eve_username, eve_password)
    try:
        client.login()
        client.stop_all_nodes(lab_full_path)
        time.sleep(1)
        client.delete_lab(lab_full_path)

        response = {
            "status": "SUCCESS",
            "message": f"Lab '{lab_name}' successfully terminated and purged."
        }
        sys.stdout.write(json.dumps(response))
        sys.exit(0)

    except Exception as exc:
        err = {"status": "FAILED", "error": str(exc)}
        sys.stderr.write(json.dumps(err))
        sys.exit(1)
    finally:
        client.logout()


def main():
    parser = argparse.ArgumentParser(description="Velocity EVE-NG Pro Orchestration Driver")
    parser.add_argument("action", nargs="?", default="initOrchestratedResource",
                        choices=["getPorts", "initOrchestratedResource", "termOrchestratedResource"])
    parser.add_argument("--eve_url", "-eve_url", default=None)
    parser.add_argument("--eve_username", "-eve_username", default=None)
    parser.add_argument("--eve_password", "-eve_password", default=None)
    parser.add_argument("--node_template", "-node_template", default=None)
    parser.add_argument("--reservation_id", "-reservation_id", default=None)

    # Use parse_known_args to be resilient to extra Velocity flags
    parsed_args, _ = parser.parse_known_args()

    if parsed_args.action == "getPorts":
        cmd_get_ports(parsed_args)
    elif parsed_args.action == "initOrchestratedResource":
        cmd_init_resource(parsed_args)
    elif parsed_args.action == "termOrchestratedResource":
        cmd_term_resource(parsed_args)


if __name__ == "__main__":
    main()