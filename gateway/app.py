"""
Lumen Velocity GitHub App Protection Rule Service
Bridges GitHub Custom Deployment Protection Rules and Keysight Velocity.
Automates dynamic priority extraction, auto-refreshing Velocity auth,
asynchronous gate approval, and zero-leak teardown.
"""
import base64
import hmac
import hashlib
import json
import time
import re
import requests
import jwt
from fastapi import FastAPI, Request, Header, HTTPException
import uvicorn
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = FastAPI(title="Lumen Velocity Gate Service")

# ----------------- CONFIGURATION -----------------
GITHUB_APP_ID = "5360921"
GITHUB_INSTALLATION_ID = "172491464"
GITHUB_PRIVATE_KEY_PATH = "/home/kumar/velocity-gate.pem"
GITHUB_WEBHOOK_SECRET = "LumenVelocitySecret2026!"

CALLBACK_BASE_URL = "https://crinkly-playmate-crease.ngrok-free.dev"

# ----------------- KEYSIGHT VELOCITY CONFIG -----------------
VELOCITY_BASE_URL = "https://192.168.122.106"
VELOCITY_RESERVATION_ENDPOINT = f"{VELOCITY_BASE_URL}/velocity/api/reservation/v25/reservation"
VELOCITY_USERNAME = "administrator"
VELOCITY_PASSWORD = "Tosan@31"
FALLBACK_VELOCITY_TOKEN = "eyJwYWNrZXQiOiJ7XCJpZFwiOlwiYWRtaW5pc3RyYXRvclwiLFwidXNlcl9uYW1lXCI6XCJzdXBlci1hZG1pblwiLFwiZG9tYWluXCI6XCJsZGFwXCIsXCJzdGFydF90aW1lXCI6MTc5NDk1MTc3NixcImV4cGlyYXRpb25cIjoxNzk1NTU2ODc2LFwiaXNfcGFzc3dvcmRfY2hhbmdlX2FsbG93ZWRcIjp0cnVlfSIsInNpZ25hdHVyZSI6IjY0OGFhNGNjMTMzYmU3MDFhM2YzOThkOGJjYTUxNmE1ZjNkODQyYWFiMjY1MDc1YzQ2OWZhOTMwOGU3NTQ4NzE1ZjZkYjhkNDBjZWZlZWFlMDAzZDZlNTRjZTM1ODEwNjM5ZDM5MjJjMDQ1MzIxMGU0NjUzZWNkZjgxZTU1OWZiNDcwY2QxYmI0MzY1M2NlYTJmODlhZGYwNWIwMmM0Yjg0NjM2MjIzOGZlNWQ3NjA1OTAwZjE1NmZjYjgzNTAzNDMwYTBjY2ZkNDczNDUwZjE4MzQ4NzU5NTgyMWU1ODMzNWJlZWMzYjY5ODgwMWY4OGY1MzQ1Yzk5NjFlNGYyOTJlMTE1NTRmZGVkMzRkNTliYzhlZGM3OGE2MzM5NjNjMzUwOWRkMTA1NzY4OGRhYTFhMDNlZmRiOWJkZTY4MTU5ZGUzNjAzMWQ4MTBkYjFmYjExOWViZmMxYjE1Mjc2ZTQ1OTg2MWUwYWFmMDY0ZDFkZjdjZTI1MTdiMTU0MzYwZThkNTg4MGQwM2I3ZmNkOGZhYzNiOGU1Y2QxNzM4NGRiM2UyOTAzNGZiMDZlOTU3ODg2YTFjN2M1NDY1Njg1Nzg5ZTM2MzBhMTQzM2Y2ODc0ZDAwOGI3Nzk2YTc1N2VjYjI3NzhhODhiNDg2NDQ5MDI3NDVmNDcwZDNkYTNlNmRkIn0="
TOPOLOGY_UUID = "a51d26ad-ded0-4b0e-bad2-cb5ec3862e25"

# ----------------- EVE-NG CONFIG -----------------
EVE_IP = "192.168.122.19"
LAB_PATH = "Velocity-Integration-Lab/Base-POC-Lab.unl"
EVE_AUTH_URL = f"http://{EVE_IP}/api/auth/login"
EVE_STOP_URL = f"http://{EVE_IP}/api/labs/{LAB_PATH}/nodes/stop"
# -------------------------------------------------

active_deployments = {}
run_to_reservation = {}


class VelocityAuthManager:
    def __init__(self, base_url: str, username: str, password: str, initial_token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._token = initial_token
        self._expiry = self._parse_token_expiry(initial_token)

    @staticmethod
    def _parse_token_expiry(token: str) -> int:
        if not token:
            return 0
        try:
            padded = token + "=" * (-len(token) % 4)
            outer = json.loads(base64.b64decode(padded).decode("utf-8"))
            packet = json.loads(outer.get("packet", "{}"))
            return int(packet.get("expiration", 0))
        except Exception:
            return 0

    def get_token(self, force_refresh: bool = False) -> str:
        now = int(time.time())
        # Refresh proactively 10 minutes before token expiration
        if not force_refresh and self._token and (self._expiry == 0 or now < (self._expiry - 600)):
            return self._token

        new_token = self._login_basic_auth()
        if new_token:
            self._token = new_token
            self._expiry = self._parse_token_expiry(new_token) or (now + 3600)
            print(f"[VELOCITY AUTH] Token refreshed successfully (expires epoch: {self._expiry})")
            return self._token

        print("[VELOCITY AUTH WARNING] Refresh failed; using cached/fallback token.")
        return self._token

    def _login_basic_auth(self) -> str:
        # Primary: HTTP GET with Basic Auth against /velocity/api/auth/v2/login
        v2_url = f"{self.base_url}/velocity/api/auth/v2/login"
        try:
            res = requests.get(
                v2_url,
                auth=(self.username, self.password),
                headers={"Accept": "application/json"},
                verify=False,
                timeout=10
            )
            if res.status_code in (200, 201):
                token = res.headers.get("X-Auth-Token") or res.cookies.get("X-Auth-Token")
                if not token and res.text:
                    data = res.json()
                    token = data.get("token") or data.get("authToken") or data.get("id")
                if token:
                    return token
            else:
                print(f"[VELOCITY AUTH] v2/login returned HTTP {res.status_code}: {res.text[:120]}")
        except Exception as e:
            print(f"[VELOCITY AUTH] v2/login exception: {e}")

        # Secondary fallback: HTTP POST with trailing slash to /ito/authentication/v1/authentication/
        ito_url = f"{self.base_url}/ito/authentication/v1/authentication/"
        try:
            res = requests.post(
                ito_url,
                json={"username": self.username, "password": self.password},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                verify=False,
                timeout=10
            )
            if res.status_code in (200, 201):
                token = res.headers.get("X-Auth-Token") or res.cookies.get("X-Auth-Token")
                if not token and res.text:
                    data = res.json()
                    token = data.get("token") or data.get("authToken")
                if token:
                    return token
            else:
                print(f"[VELOCITY AUTH] ito/auth returned HTTP {res.status_code}: {res.text[:120]}")
        except Exception as e:
            print(f"[VELOCITY AUTH] ito/auth exception: {e}")

        return ""


velocity_auth = VelocityAuthManager(
    VELOCITY_BASE_URL,
    VELOCITY_USERNAME,
    VELOCITY_PASSWORD,
    FALLBACK_VELOCITY_TOKEN
)


def velocity_request(method: str, url: str, **kwargs) -> requests.Response:
    """Executes a Velocity API request with automatic token injection and 401/403 retry."""
    headers = kwargs.pop("headers", {})
    headers["X-Auth-Token"] = velocity_auth.get_token()
    res = requests.request(method, url, headers=headers, verify=False, **kwargs)

    if res.status_code in (401, 403):
        print(f"[VELOCITY AUTH] Received HTTP {res.status_code}; forcing token refresh and retrying...")
        headers["X-Auth-Token"] = velocity_auth.get_token(force_refresh=True)
        res = requests.request(method, url, headers=headers, verify=False, **kwargs)

    return res


def get_github_installation_token() -> str:
    with open(GITHUB_PRIVATE_KEY_PATH, "r") as f:
        private_key = f.read()

    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + (10 * 60),
        "iss": GITHUB_APP_ID
    }
    encoded_jwt = jwt.encode(payload, private_key, algorithm="RS256")

    url = f"https://api.github.com/app/installations/{GITHUB_INSTALLATION_ID}/access_tokens"
    headers = {
        "Authorization": f"Bearer {encoded_jwt}",
        "Accept": "application/vnd.github+json"
    }
    res = requests.post(url, headers=headers, timeout=10)
    if res.status_code != 201:
        raise HTTPException(status_code=500, detail=f"Failed to fetch installation token: {res.text}")

    return res.json()["token"]


def verify_github_signature(payload: bytes, signature_header: str):
    if not signature_header:
        raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256 header")
    sha_name, signature = signature_header.split("=")
    mac = hmac.new(GITHUB_WEBHOOK_SECRET.encode(), msg=payload, digestmod=hashlib.sha256)
    if not hmac.compare_digest(mac.hexdigest(), signature):
        raise HTTPException(status_code=401, detail="Invalid signature")


def stop_eveng_nodes():
    try:
        session = requests.Session()
        login_res = session.post(EVE_AUTH_URL, json={"username": "admin", "password": "eve", "html5": "-1"}, timeout=5)
        if login_res.status_code == 200:
            stop_res = session.get(EVE_STOP_URL, timeout=10)
            print(f"[TEARDOWN] EVE-NG nodes stop signal sent: HTTP {stop_res.status_code}")
    except Exception as e:
        print(f"[TEARDOWN ERROR] Failed to signal EVE-NG stop: {e}")


def execute_teardown(reservation_id: str):
    try:
        res = velocity_request(
            "POST",
            f"{VELOCITY_RESERVATION_ENDPOINT}/{reservation_id}/action?type=CANCEL",
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        print(f"[TEARDOWN] Reservation {reservation_id} canceled: HTTP {res.status_code}")
    except Exception as e:
        print(f"[TEARDOWN ERROR] Failed canceling Velocity reservation {reservation_id}: {e}")

    stop_eveng_nodes()
    active_deployments.pop(reservation_id, None)


def fetch_workflow_inputs(repo: str, run_id: str) -> dict:
    if not run_id:
        return {}
    try:
        token = get_github_installation_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json"
        }
        url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}"
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            data = r.json()
            return data.get("inputs") or {}
        else:
            print(f"[INPUT LOOKUP API] HTTP {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"[INPUT LOOKUP WARNING] Error: {e}")
    return {}


@app.post("/webhook/github")
async def handle_github_webhook(request: Request, x_hub_signature_256: str = Header(None)):
    body = await request.body()
    verify_github_signature(body, x_hub_signature_256)
    event = json.loads(body)

    action = event.get("action")

    # -------------------------------------------------------------
    # EVENT 1: Deployment Protection Rule Requested (Spin-Up Gate)
    # -------------------------------------------------------------
    if action == "requested" and ("deployment" in event or "deployment_callback_url" in event):
        deployment = event.get("deployment", {})
        repo = event.get("repository", {}).get("full_name", "kdahal/neteng-velocity-eveng-automation")
        callback_url = event.get("deployment_callback_url", "")
        environment = event.get("environment", "velocity-lab-env")

        run_id = ""
        if callback_url:
            match = re.search(r"/actions/runs/(\d+)/", callback_url)
            if match:
                run_id = match.group(1)

        if not run_id:
            run_id = str(
                deployment.get("workflow_run", {}).get("id") or
                deployment.get("run_id") or
                event.get("workflow_run", {}).get("id") or
                event.get("run_id") or
                ""
            )

        selected_priority = None

        inputs = (
            event.get("inputs") or
            event.get("workflow_run", {}).get("inputs") or
            deployment.get("workflow_run", {}).get("inputs") or
            deployment.get("payload", {}).get("inputs") or
            {}
        )
        if isinstance(inputs, dict) and "priority" in inputs:
            selected_priority = str(inputs["priority"]).upper()

        if not selected_priority and run_id:
            api_inputs = fetch_workflow_inputs(repo, run_id)
            if "priority" in api_inputs:
                selected_priority = str(api_inputs["priority"]).upper()

        if selected_priority not in ["LOW", "MEDIUM", "HIGH"]:
            selected_priority = "MEDIUM"

        reservation_name = f"GH-Run-{run_id}" if run_id else f"GH-Deployment-{int(time.time())}"

        reservation_payload = {
            "name": reservation_name,
            "description": f"CI reservation for {repo} run {run_id} ({selected_priority})",
            "duration": 3600,
            "priority": selected_priority,
            "topologyId": TOPOLOGY_UUID,
            "isQueued": True,
            "callbackURL": f"{CALLBACK_BASE_URL}/webhook/velocity"
        }

        res = velocity_request(
            "POST",
            VELOCITY_RESERVATION_ENDPOINT,
            json=reservation_payload,
            headers={"Content-Type": "application/json"},
            timeout=15
        )

        try:
            res_data = res.json()
        except Exception:
            res_data = {"raw_text": res.text}

        reservation_id = res_data.get("id") or res_data.get("reservationId")
        if not reservation_id:
            print(f"[ERROR] Velocity reservation creation failed ({res.status_code}): {res_data}")
            return {"status": "error_creating_reservation", "velocity_response": res_data}

        active_deployments[reservation_id] = {
            "github_callback_url": callback_url,
            "repo": repo,
            "run_id": run_id,
            "priority": selected_priority,
            "environment": environment
        }

        if run_id:
            run_to_reservation[run_id] = reservation_id

        print(f"[GATE] Successfully queued Velocity reservation {reservation_id} for run '{run_id}' (Priority: {selected_priority})")
        return {"status": "queued_in_velocity", "reservation_id": reservation_id, "priority": selected_priority}

    # -------------------------------------------------------------
    # EVENT 2: Workflow Run Completed (Automated App Teardown)
    # -------------------------------------------------------------
    if action == "completed" and "workflow_run" in event:
        completed_run_id = str(event["workflow_run"]["id"])
        conclusion = event["workflow_run"].get("conclusion")
        print(f"[WORKFLOW COMPLETED] Run {completed_run_id} finished with conclusion: {conclusion}")

        target_reservation_id = run_to_reservation.pop(completed_run_id, None)

        if not target_reservation_id:
            for r_id, ctx in list(active_deployments.items()):
                if ctx.get("run_id") == completed_run_id:
                    target_reservation_id = r_id
                    break

        if not target_reservation_id and active_deployments:
            target_reservation_id = next(iter(active_deployments))
            print(f"[APP TEARDOWN] Popped oldest active reservation: {target_reservation_id}")

        if target_reservation_id:
            print(f"[APP TEARDOWN] Tearing down reservation {target_reservation_id} for run {completed_run_id}")
            execute_teardown(target_reservation_id)
            return {"status": "auto_teardown_executed", "reservation_id": target_reservation_id}

        print(f"[INFO] No Velocity reservation tracked, shutting down EVE-NG nodes as safety precaution.")
        stop_eveng_nodes()
        return {"status": "eveng_stopped_precaution"}

    return {"status": "ignored"}


@app.post("/webhook/velocity")
async def handle_velocity_callback(request: Request):
    payload = await request.json()
    reservation_id = payload.get("reservationId") or payload.get("id")

    print(f"[VELOCITY CALLBACK] Received event for reservation: {reservation_id}")

    context = active_deployments.get(reservation_id)
    if not context:
        print(f"[WARNING] Velocity callback for unmapped reservation: {reservation_id}")
        return {"status": "unknown_reservation"}

    github_callback_url = context["github_callback_url"]
    environment = context.get("environment", "velocity-lab-env")
    priority = context.get("priority", "MEDIUM")

    token = get_github_installation_token()

    approval_payload = {
        "environment_name": environment,
        "state": "approved",
        "comment": f"Velocity reservation {reservation_id} is ACTIVE and ready (Priority: {priority})."
    }

    gh_res = requests.post(
        github_callback_url,
        json=approval_payload,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}"
        },
        timeout=10
    )

    print(f"[GATE APPROVED] GitHub response {gh_res.status_code}: {gh_res.text[:150]}")
    return {"status": "gate_approved", "github_status": gh_res.status_code}


@app.post("/teardown/{reservation_id}")
async def handle_teardown(reservation_id: str):
    if reservation_id == "active":
        target_ids = list(active_deployments.keys())
    else:
        target_ids = [reservation_id]

    for r_id in target_ids:
        execute_teardown(r_id)

    stop_eveng_nodes()
    return {"status": "released", "cleared_reservations": target_ids}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8443)
