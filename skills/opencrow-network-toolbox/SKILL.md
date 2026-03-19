---
name: opencrow-network-toolbox
description: Use the Anaconda `ctf` environment and installed network tooling for packet- and protocol-level CTF tasks. Use when Codex needs `scapy`, `tshark`, `tcpdump`, `nmap`, `nc`, or `socat` for packet work, capture analysis, or service triage.
---

# OpenCROW Network Toolbox

Use this skill for network artifact work that spans both Python packet tooling and native capture or triage tools: `scapy`, `tshark`, `tcpdump`, `nmap`, `nc`, and `socat`.

## Quick Start

Run inline Python in `ctf`:

```bash
python ~/.codex/skills/opencrow-network-toolbox/scripts/run_network_python.py --code 'from scapy.all import IP, TCP; print((IP(dst=\"127.0.0.1\")/TCP(dport=31337)).summary())'
```

Run a packet helper:

```bash
python ~/.codex/skills/opencrow-network-toolbox/scripts/run_network_python.py --file /absolute/path/to/packets.py
```

Verify the mapped stack:

```bash
python ~/.codex/skills/opencrow-network-toolbox/scripts/verify_toolkit.py
```

## Workflow

1. Use this toolbox when the target is packet or protocol logic, not a long-lived interactive TCP shell.
2. Use `tshark` or `tcpdump` first when you need quick visibility into a PCAP or live traffic.
3. Use `scapy` to decode captures, generate packets, or prototype custom protocol interactions.
4. Use `nmap`, `nc`, or `socat` for service discovery or socket-level experiments.
5. Use `netcat-async` or `ssh-async` separately when you need persistent line-oriented sessions rather than packet tooling.
6. Read [references/tooling.md](references/tooling.md) for quick guidance.

## Tool Selection

- Use `scapy` for packet crafting, sniffing, protocol parsing, PCAP analysis, and challenge-specific dissectors.
- Use `tshark` for quick PCAP summaries, display filters, and protocol-aware decoding.
- Use `tcpdump` for capture creation and fast packet inspection from the shell.
- Use `nmap` for host, port, and service discovery before deeper protocol analysis.
- Use `nc` or `socat` for quick socket experiments, port relays, or test servers.
- Use plain Python socket code only when the task is simpler than full packet work.
- Use `netcat-async` when the service is an interactive TCP stream and session persistence matters more than packet structure.

## Resources

- `scripts/run_network_python.py`: execute inline code or a `.py` file inside the `ctf` environment.
- `scripts/verify_toolkit.py`: confirm that `scapy` is available.
- `references/tooling.md`: quick selection notes for network workflows.
