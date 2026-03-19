# OpenCROW Network Toolbox

Use this reference when the problem is packet- or protocol-oriented and you need to choose between packet libraries, capture tools, and quick socket triage.

## Python in `ctf`

- `scapy`: packet crafting, packet decoding, PCAP parsing, protocol modeling, and lightweight sniff/send workflows.

## Native tools

- `tshark`: headless Wireshark dissectors and filtered PCAP inspection.
- `tcpdump`: capture traffic and inspect packets quickly from the shell.
- `nmap`: discover hosts, ports, and service banners.
- `nc`: quick socket checks and lightweight TCP/UDP interactions.
- `socat`: richer socket relays, listeners, and test endpoints.

## Practical selection

- Use `scapy` when you need visibility into headers, payload formats, or custom packet sequences.
- Use `tshark` first when you already have a PCAP and want fast filtered output.
- Use `tcpdump` when you need to capture traffic before analyzing it.
- Use `nmap` to map the surface before choosing a deeper protocol tool.
- Use this toolbox for PCAP analysis or synthetic packet generation.
- Use `netcat-async` instead when the service is just a persistent line-based TCP interaction.
