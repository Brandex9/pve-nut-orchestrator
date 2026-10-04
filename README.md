# Proxmox VE Graceful UPS Shutdown Orchestrator

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Infrastructure: Proxmox VE](https://img.shields.io/badge/Hypervisor-Proxmox%20VE%208.x-orange.svg)](https://www.proxmox.com)

An automated Infrastructure-as-Code operational utility that coordinates multi-stage, ordered guest teardowns across Proxmox VE hypervisor nodes when triggered by Network UPS Tools (NUT) battery events.

---

## The Problem Statement

Standard hypervisor UPS actions issue blanket shutdown commands indiscriminately to all virtual machines. In high-availability enterprise environments, this triggers severe operational issues:

1. **Database Corruption:** Applications lose access to underlying storage pools before their database engines flush dirty writes.
2. **Network Blindness:** Terminating the virtualized router/gateway first kills DNS and monitoring telemetry, leaving the hypervisor unable to transmit status alerts or reach downstream nodes.
3. **Array Parity Invalidation:** Complex storage engines (such as Unraid VMs with passed-through SAS HBAs) require sufficient time to unmount disk arrays cleanly.

---

## Architectural Teardown Pipeline

When an `ONBATT` signal persists past the battery buffer threshold, the orchestrator traverses three strict dependency tiers via scoped API tokens:

```text
        [ NUT Master: CyberPower UPS Event ]
                        │
                        ▼
┌────────────────────────────────────────────────────────┐
│ Stage 1: Central Identity & Application Workloads      │
│ • Authentik, Vaultwarden, Telemetry Agents             │
│ • Timeout Buffer: 60s                                  │
└────────────────────────┬───────────────────────────────┘
                         ▼
┌────────────────────────────────────────────────────────┐
│ Stage 2: High-Density Storage & Transcode Engines      │
│ • Unraid Storage VM (Clean Cache Flush & Array Stop)   │
│ • Timeout Buffer: 60s                                  │
└────────────────────────┬───────────────────────────────┘
                         ▼
┌────────────────────────────────────────────────────────┐
│ Stage 3: Core Routing & Network Gateway Layer          │
│ • OPNsense Core Gateway & Resolvers                    │
│ • Kept alive to maintain WAN/LAN routing until end     │
└────────────────────────┬───────────────────────────────┘
                         ▼
┌────────────────────────────────────────────────────────┐
│ Host Level: Bare-Metal Hypervisor Poweroff             │
│ • PVE Node executes ACPI kernel shutdown               │
└────────────────────────────────────────────────────────┘
```

---

## Prerequisites & Installation

### 1. Scoped Proxmox RBAC Token

Create an unprivileged API token on Proxmox VE adhering to least privilege:

```bash
pveum role add NUT-Operator -privs "VM.Audit VM.PowerMgmt Sys.PowerMgmt"
pveum user add nut-agent@pam
pveum user token add nut-agent@pam shutdown-token
pveum acl modify / -user nut-agent@pam -role NUT-Operator
```

### 2. Environment Configuration

Provide credentials via environment variables or a secured root `.env` file:

```bash
export PVE_HOST="https://10.1.1.2:8006"
export PVE_NODE="pve-compute"
export PVE_TOKEN_ID="nut-agent@pam!shutdown-token"
export PVE_TOKEN_SECRET="xxxx-xxxx-xxxx-xxxx"
```

### 3. NUT Daemon Integration

Hook the orchestrator into `/etc/nut/upssched.conf` on your NUT Master or Client node:

```text
AT ONBATT * START-TIMER start-shutdown 120
AT ONLINE * CANCEL-TIMER start-shutdown
```

Configure `/etc/nut/upssched-cmd` to trigger execution:

```bash
case "$1" in
    start-shutdown)
        /usr/bin/python3 /opt/pve-nut-orchestrator/orchestrator.py >> /var/log/pve-shutdown.log 2>&1
        ;;
esac
```

---

## License

Distributed under the MIT License. See `LICENSE` for more information.
