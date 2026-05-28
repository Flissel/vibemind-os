# :shield: VibeMind Security Lab

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

**Security research lab with 30 proof-of-concept modules for AI-augmented defense and adversarial testing.**

A comprehensive collection of security PoCs covering prompt injection analysis, network monitoring, vulnerability scanning, and distributed agent architectures. Built for research and education.

> **Disclaimer:** These modules are proof-of-concept tools for authorized security research only. Use responsibly and only on systems you own or have permission to test.

## Module Categories

### AI Adversarial Testing
- **AutoGen injection chain** -- multi-step prompt injection analysis
- **Red/blue team framework** -- automated adversarial attack and defense testing

### System Defense
- **OS shield** -- real-time OS-level threat detection
- **Network monitor** -- traffic analysis and anomaly detection
- **Vulnerability scanner** -- automated CVE and misconfiguration checks
- **Botnet detector** -- behavioral pattern analysis for botnet identification

### Forensics & Logging
- **Forensics toolkit** -- disk and memory artifact analysis
- **Canary tokens** -- honeypot token generation and tracking
- **Log analyzer** -- AI-powered log anomaly detection

### Infrastructure
- **Distributed AutoGen agents** -- gRPC-based multi-node agent execution
- **Keycloak auth integration** -- SSO and RBAC for all modules
- **Shared LLM client** -- all PoCs use `llm_client.py` for unified LLM access

## Installation

```bash
git clone https://github.com/Flissel/vibemind-security.git
cd vibemind-security
pip install -r requirements.txt
```

## Usage

### Run a specific PoC module

```bash
# Network monitoring
python -m pocs.network_monitor

# Vulnerability scan
python -m pocs.vuln_scanner --target 192.168.1.0/24

# Red/blue team exercise
python -m pocs.red_blue_team --scenario injection

# Botnet detection
python -m pocs.botnet_detector --interface eth0

# Canary token generation
python -m pocs.canary_tokens --type dns --count 10
```

### Distributed agents via gRPC

```bash
# Start agent nodes
python -m infra.grpc_agent --port 50051
python -m infra.grpc_agent --port 50052

# Orchestrate
python -m infra.orchestrator --agents localhost:50051,localhost:50052
```

## Project Structure

```
vibemind-security/
  llm_client.py              # Shared LLM access for all modules
  pocs/
    autogen_injection.py
    red_blue_team.py
    os_shield.py
    forensics.py
    network_monitor.py
    vuln_scanner.py
    botnet_detector.py
    canary_tokens.py
    log_analyzer.py
    ...                      # 30 modules total
  infra/
    grpc_agent.py
    orchestrator.py
    keycloak_auth.py
  requirements.txt
```

## License

MIT -- Felix Baumann ([@Flissel](https://github.com/Flissel))
