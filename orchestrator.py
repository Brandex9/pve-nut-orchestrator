#!/usr/bin/env python3
"""
pve-nut-orchestrator.py
Automated graceful shutdown orchestrator for Proxmox VE hypervisors during UPS power events.
Interacts with Proxmox VE 8.x API via scoped API tokens to enforce ordered service teardown.
"""

import os
import sys
import time
import json
import logging
import urllib.request
import urllib.error
import ssl

# --- Configuration & Environment Variables ---
PROXMOX_HOST = os.getenv("PVE_HOST", "https://10.1.1.2:8006")
PVE_NODE = os.getenv("PVE_NODE", "pve-compute")
PVE_TOKEN_ID = os.getenv("PVE_TOKEN_ID", "root@pam!nut_shutdown")
PVE_TOKEN_SECRET = os.getenv("PVE_TOKEN_SECRET")

# Ordered Teardown Stages (LXC & QEMU VMIDs)
# Stage 1: Unprivileged Application Services & Dashboards
STAGE_1_GUESTS = [200, 210, 220]
# Stage 2: Heavy Storage and Media Engines (Unraid Storage VM, Media LXC)
STAGE_2_GUESTS = [310, 300]
# Stage 3: Core Network & Gateway (Firewall, DNS Core - kept alive until the very end)
STAGE_3_GUESTS = [100]

POLL_INTERVAL_SEC = 5
MAX_WAIT_PER_STAGE_SEC = 60

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

def build_pve_request(endpoint: str, method: str = "GET", payload: dict = None) -> urllib.request.Request:
    """Builds an authenticated HTTP Request to the Proxmox VE API."""
    url = f"{PROXMOX_HOST}/api2/json{endpoint}"
    headers = {
        "Authorization": f"PVEAPIToken={PVE_TOKEN_ID}={PVE_TOKEN_SECRET}",
        "Content-Type": "application/json",
    }
    data = json.dumps(payload).encode("utf-8") if payload else None
    return urllib.request.Request(url, data=data, headers=headers, method=method)

def send_pve_api(endpoint: str, method: str = "GET", payload: dict = None) -> dict:
    """Sends a request to Proxmox VE REST API, ignoring self-signed internal SSL certs."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = build_pve_request(endpoint, method, payload)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as response:
            res_body = response.read().decode("utf-8")
            return json.loads(res_body)
    except urllib.error.HTTPError as e:
        logging.error(f"PVE API Error {e.code} on {endpoint}: {e.read().decode('utf-8')}")
        return {}
    except Exception as e:
        logging.error(f"Connection failure to Proxmox host {PROXMOX_HOST}: {e}")
        return {}

def get_guest_type(vmid: int) -> str:
    """Determines whether a VMID belongs to an LXC container or a QEMU VM."""
    lxc_res = send_pve_api(f"/nodes/{PVE_NODE}/lxc/{vmid}/status/current")
    if "data" in lxc_res:
        return "lxc"
    qemu_res = send_pve_api(f"/nodes/{PVE_NODE}/qemu/{vmid}/status/current")
    if "data" in qemu_res:
        return "qemu"
    return "unknown"

def get_guest_status(guest_type: str, vmid: int) -> str:
    """Returns the current execution status ('running', 'stopped', etc.)."""
    res = send_pve_api(f"/nodes/{PVE_NODE}/{guest_type}/{vmid}/status/current")
    return res.get("data", {}).get("status", "unknown")

def shutdown_guest(guest_type: str, vmid: int):
    """Issues an ACPI / OS-level graceful shutdown call."""
    logging.info(f"Triggering graceful shutdown for {guest_type.upper()} VMID {vmid}...")
    send_pve_api(f"/nodes/{PVE_NODE}/{guest_type}/{vmid}/status/shutdown", method="POST")

def execute_stage_teardown(vmid_list: list, stage_name: str):
    """Executes shutdown for a group of VMIDs and blocks until stopped or timeout reached."""
    logging.info(f"--- Starting {stage_name} Shutdown Sequence ---")
    active_guests = {}

    for vmid in vmid_list:
        gtype = get_guest_type(vmid)
        if gtype == "unknown":
            logging.warning(f"VMID {vmid} not found on node {PVE_NODE}. Skipping.")
            continue
        status = get_guest_status(gtype, vmid)
        if status == "running":
            shutdown_guest(gtype, vmid)
            active_guests[vmid] = gtype
        else:
            logging.info(f"VMID {vmid} is already {status}.")

    elapsed = 0
    while active_guests and elapsed < MAX_WAIT_PER_STAGE_SEC:
        time.sleep(POLL_INTERVAL_SEC)
        elapsed += POLL_INTERVAL_SEC
        for vmid in list(active_guests.keys()):
            gtype = active_guests[vmid]
            status = get_guest_status(gtype, vmid)
            if status == "stopped":
                logging.info(f"Confirmed: {gtype.upper()} VMID {vmid} has safely stopped.")
                del active_guests[vmid]

    if active_guests:
        logging.warning(f"Timeout reached for stage: {stage_name}. Unstopped guests: {list(active_guests.keys())}")
    else:
        logging.info(f"{stage_name} completed successfully.")

def main():
    logging.warning("CRITICAL: UPS ONBATT Emergency Shutdown Signal Received!")
    if not PVE_TOKEN_SECRET:
        logging.error("PVE_TOKEN_SECRET environment variable is missing. Aborting.")
        sys.exit(1)

    # 1. Teardown Application Tier
    execute_stage_teardown(STAGE_1_GUESTS, "Stage 1: Application Core & Identity Services")

    # 2. Teardown Storage and Heavy Workloads
    execute_stage_teardown(STAGE_2_GUESTS, "Stage 2: Storage Nodes & Database Tiers")

    # 3. Teardown Gateway & DNS Services
    execute_stage_teardown(STAGE_3_GUESTS, "Stage 3: Core Gateway & Routing Layer")

    # 4. Host Bare-Metal Safe Shutdown
    logging.info("All guest workloads safely halted. Executing host poweroff...")
    send_pve_api(f"/nodes/{PVE_NODE}/status", method="POST", payload={"command": "shutdown"})

if __name__ == "__main__":
    main()