#!/usr/bin/env python3
import sys
import os
import json
import time
import traceback
import argparse
import requests


STARTUP_CONFIGS = {
    "V-VyOS-Core": """set system host-name V-VyOS-Core
set interfaces dummy dum0 address 10.255.0.1/32
set interfaces ethernet eth0 address 10.10.1.1/30
set interfaces ethernet eth1 address 10.10.2.1/30
commit
save
""",
    "V-VyOS-Edge1": """set system host-name V-VyOS-Edge1
set interfaces dummy dum0 address 10.255.0.2/32
set interfaces ethernet eth0 address 10.10.1.2/30
set interfaces ethernet eth1 address 192.168.10.1/24
commit
save
""",
    "V-VyOS-Edge2": """set system host-name V-VyOS-Edge2
set interfaces dummy dum0 address 10.255.0.3/32
set interfaces ethernet eth0 address 10.10.2.2/30
set interfaces ethernet eth1 address 192.168.20.1/24
commit
save
""",
    "V-PC-01": "ip 192.168.10.2/24 192.168.10.1\nsave\n",
    "V-PC-02": "ip 192.168.20.2/24 192.168.20.1\nsave\n"
}


def _debug_log(msg):
    try:
        with open("/tmp/vel_driver_debug.log", "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


class EveNgClient:
    """Wrapper around EVE-NG Pro REST API supporting multi-node QEMU & VPCS topologies."""
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
        payload = json.dumps({"path": lab_path, "name": lab_name, "version": "1"})
        res = self.session.post(url, data=payload, timeout=15)
        if res.status_code not in (200, 201):
            raise RuntimeError(f"Failed to create lab {lab_name}: {res.text}")

    def add_node(self, full_lab_path, node_type, template, node_name, left=200, top=200):
        url = f"{self.base_url}/labs{full_lab_path}/nodes"
        body = {
            "type": node_type,
            "template": template,
            "name": node_name,
            "left": left,
            "top": top
        }
        if node_type == "qemu":
            body["ethernet"] = 2
        res = self.session.post(url, data=json.dumps(body), timeout=20)
        data = res.json()
        if res.status_code not in (200, 201) or data.get("status") != "success":
            raise RuntimeError(f"Failed to add node {node_name}: {res.text}")
        return data["data"]["id"]

    def set_startup_config(self, full_lab_path, node_id, config_text):
        cfg_url = f"{self.base_url}/labs{full_lab_path}/configs/{node_id}"
        res_cfg = self.session.put(cfg_url, data=json.dumps({"data": config_text}), timeout=25)
        if res_cfg.status_code not in (200, 201):
            raise RuntimeError(f"Failed to upload config for node {node_id}: {res_cfg.text}")

        node_url = f"{self.base_url}/labs{full_lab_path}/nodes/{node_id}"
        res_node = self.session.put(node_url, data=json.dumps({"config": "1"}), timeout=25)
        if res_node.status_code not in (200, 201):
            raise RuntimeError(f"Failed to enable startup config on node {node_id}: {res_node.text}")

    def add_bridge_network(self, full_lab_path, net_name, left=400, top=200):
        url = f"{self.base_url}/labs{full_lab_path}/networks"
        payload = json.dumps({
            "type": "bridge",
            "name": net_name,
            "left": left,
            "top": top,
            "visibility": "1"
        })
        res = self.session.post(url, data=payload, timeout=15)
        data = res.json()
        if res.status_code not in (200, 201) or data.get("status") != "success":
            raise RuntimeError(f"Failed to add bridge network {net_name}: {res.text}")
        return int(data["data"]["id"])

    def connect_interface(self, full_lab_path, node_id, iface_id, network_id):
        url = f"{self.base_url}/labs{full_lab_path}/nodes/{node_id}/interfaces"
        payload = json.dumps({str(iface_id): str(network_id)})
        res = self.session.put(url, data=payload, timeout=15)
        if res.status_code not in (200, 201):
            raise RuntimeError(f"Failed to connect node {node_id} iface {iface_id}: {res.text}")

    def start_all_nodes(self, full_lab_path):
        url = f"{self.base_url}/labs{full_lab_path}/nodes/start"
        for attempt in range(3):
            res = self.session.get(url, timeout=30)
            if res.status_code in (200, 201):
                return
            time.sleep(2)
        raise RuntimeError(f"Failed to start lab nodes ({res.status_code}): {res.text}")

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


def _resolve_lab_name(args):
    res_id = (
        args.reservation_id
        or os.environ.get("VELOCITY_PARAM_reservationId")
        or str(int(time.time() * 1000))
    ).strip()
    return f"Vel_POC_{res_id}"


def cmd_get_ports(args):
    ports = [
        {"name": "eth1", "status": "online", "properties": {"ipAddress": "192.168.122.19"}},
        {"name": "eth2", "status": "online", "properties": {"ipAddress": "192.168.122.19"}}
    ]
    sys.stdout.write(json.dumps({"ports": ports}))
    sys.exit(0)


def cmd_get_properties(args):
    sys.stdout.write(json.dumps({
        "properties": {
            "manufacturerName": "EVE-NG",
            "modelName": "EVE-NG-Pro-Dynamic"
        }
    }))
    sys.exit(0)


def cmd_init_resource(args, action_name):
    lab_name = _resolve_lab_name(args)
    lab_full_path = f"/{lab_name}.unl"
    try:
        with open("/tmp/vel_active_lab.txt", "w") as sf:
            sf.write(lab_full_path)
    except Exception as e:
        _debug_log(f"Warning writing state file: {e}")

    eve_url = args.eve_url or os.environ.get("VELOCITY_PARAM_property_eve_url", "http://192.168.122.19/api")
    eve_username = args.eve_username or os.environ.get("VELOCITY_PARAM_property_eve_username", "admin")
    eve_password = args.eve_password or os.environ.get("VELOCITY_PARAM_property_eve_password", "eve")
    node_template = args.node_template or os.environ.get("VELOCITY_PARAM_property_node_template", "vyos")

    client = EveNgClient(eve_url, eve_username, eve_password)
    try:
        client.login()
        client.create_lab("/", lab_name)

        # 1. Create 4 bridge networks for the hub-and-spoke spider web
        net_core1 = client.add_bridge_network(lab_full_path, "Link_Core1", left=325, top=220)
        net_core2 = client.add_bridge_network(lab_full_path, "Link_Core2", left=575, top=220)
        net_pc1 = client.add_bridge_network(lab_full_path, "Link_PC1", left=200, top=140)
        net_pc2 = client.add_bridge_network(lab_full_path, "Link_PC2", left=700, top=140)

        # 2. Instantiate 3 VyOS routers + 2 VPCS client endpoints
        core_id = client.add_node(lab_full_path, "qemu", node_template, "V-VyOS-Core", left=450, top=280)
        edge1_id = client.add_node(lab_full_path, "qemu", node_template, "V-VyOS-Edge1", left=250, top=180)
        edge2_id = client.add_node(lab_full_path, "qemu", node_template, "V-VyOS-Edge2", left=650, top=180)
        pc1_id = client.add_node(lab_full_path, "vpcs", "vpcs", "V-PC-01", left=120, top=80)
        pc2_id = client.add_node(lab_full_path, "vpcs", "vpcs", "V-PC-02", left=780, top=80)
        time.sleep(1)

        # 3. Inject startup configs before first boot
        client.set_startup_config(lab_full_path, core_id, STARTUP_CONFIGS["V-VyOS-Core"])
        client.set_startup_config(lab_full_path, edge1_id, STARTUP_CONFIGS["V-VyOS-Edge1"])
        client.set_startup_config(lab_full_path, edge2_id, STARTUP_CONFIGS["V-VyOS-Edge2"])
        client.set_startup_config(lab_full_path, pc1_id, STARTUP_CONFIGS["V-PC-01"])
        client.set_startup_config(lab_full_path, pc2_id, STARTUP_CONFIGS["V-PC-02"])

        # 4. Wire the spider-web links
        # Core <-> Edge1
        client.connect_interface(lab_full_path, core_id, iface_id=0, network_id=net_core1)
        client.connect_interface(lab_full_path, edge1_id, iface_id=0, network_id=net_core1)
        # Core <-> Edge2
        client.connect_interface(lab_full_path, core_id, iface_id=1, network_id=net_core2)
        client.connect_interface(lab_full_path, edge2_id, iface_id=0, network_id=net_core2)
        # Edge1 <-> V-PC-01
        client.connect_interface(lab_full_path, edge1_id, iface_id=1, network_id=net_pc1)
        client.connect_interface(lab_full_path, pc1_id, iface_id=0, network_id=net_pc1)
        # Edge2 <-> V-PC-02
        client.connect_interface(lab_full_path, edge2_id, iface_id=1, network_id=net_pc2)
        client.connect_interface(lab_full_path, pc2_id, iface_id=0, network_id=net_pc2)

        time.sleep(1)
        client.start_all_nodes(lab_full_path)

        if action_name == "initVelocitySelected":
            response = {
                "reservationProperties": {
                    "eve_lab_path": lab_full_path,
                    "node_count": "5"
                }
            }
        else:
            response = {
                "properties": {
                    "eve_lab_path": lab_full_path,
                    "node_count": "5"
                },
                "ports": [],
                "switchPorts": [],
                "connections": [],
                "reservationProperties": {
                    "eve_lab_path": lab_full_path
                }
            }

        _debug_log(f"SUCCESS init 5-node spider-web {lab_full_path}")
        sys.stdout.write(json.dumps(response))
        sys.exit(0)

    except Exception as exc:
        _debug_log(f"ERROR init {lab_full_path}: {exc}\n{traceback.format_exc()}")
        sys.stderr.write(str(exc))
        sys.exit(1)
    finally:
        client.logout()


def cmd_term_resource(args):
    if os.path.exists("/tmp/vel_active_lab.txt"):
        with open("/tmp/vel_active_lab.txt") as sf:
            lab_full_path = sf.read().strip()
    else:
        lab_full_path = f"/{_resolve_lab_name(args)}.unl"

    eve_url = args.eve_url or os.environ.get("VELOCITY_PARAM_property_eve_url", "http://192.168.122.19/api")
    eve_username = args.eve_username or os.environ.get("VELOCITY_PARAM_property_eve_username", "admin")
    eve_password = args.eve_password or os.environ.get("VELOCITY_PARAM_property_eve_password", "eve")

    client = EveNgClient(eve_url, eve_username, eve_password)
    try:
        client.login()
        client.stop_all_nodes(lab_full_path)
        time.sleep(1)
        client.delete_lab(lab_full_path)
        _debug_log(f"SUCCESS term {lab_full_path}")
        sys.stdout.write(json.dumps({}))
        sys.exit(0)
    except Exception as exc:
        _debug_log(f"ERROR term {lab_full_path}: {exc}\n{traceback.format_exc()}")
        sys.stderr.write(str(exc))
        sys.exit(1)
    finally:
        client.logout()


def main():
    parser = argparse.ArgumentParser(description="Velocity EVE-NG Pro Orchestration Driver")
    parser.add_argument("action", nargs="?", default=None)
    parser.add_argument("--eve_url", "-eve_url", default=None)
    parser.add_argument("--eve_username", "-eve_username", default=None)
    parser.add_argument("--eve_password", "-eve_password", default=None)
    parser.add_argument("--node_template", "-node_template", default=None)
    parser.add_argument("--reservation_id", "-reservation_id", default=None)

    parsed_args, _ = parser.parse_known_args()

    env_call = os.environ.get("VELOCITY_PARAM_call_0", "").strip()
    env_action = env_call.split()[0] if env_call else ""
    action = (parsed_args.action or env_action or "initVelocitySelected").strip()

    if action == "getPorts":
        cmd_get_ports(parsed_args)
    elif action == "getProperties":
        cmd_get_properties(parsed_args)
    elif action in ("initOrchestratedResource", "initVelocitySelected"):
        cmd_init_resource(parsed_args, action)
    elif action in ("termOrchestratedResource", "termVelocitySelected"):
        cmd_term_resource(parsed_args)
    else:
        sys.stdout.write(json.dumps({}))
        sys.exit(0)


if __name__ == "__main__":
    main()
