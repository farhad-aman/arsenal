# arsenal

Personal CLI tools for macOS.

## Commands

### `netinfo` — Network Info

Displays public IP, geolocation, local interfaces, gateway latency, DNS, and active VPN processes.

```
Usage: netinfo [options]

Options:
  -p, --ping    HTTP latency check for common services
  -s, --speed   Run Ookla speedtest
  -h, --help    Show help

Examples:
  netinfo
  netinfo -p
  netinfo -s
  netinfo -p -s
```

---

### `arz` — Live Iranian Market Prices

Fetches live USD, EUR, Gold 18K, and Emami coin prices in Toman.
Primary source: tabdeal.org — falls back to tgju.org automatically.
Results cached for 60 seconds.

```
Usage: arz [-h|--help]
```

---

### `vpn` — Multi-VPN Orchestrator

Manages OpenVPN + FortiVPN + sing-box connections with graceful startup/shutdown.

```
Usage: sudo vpn [options]

Options:
  -o          OpenVPN only
  -f          FortiVPN only
  -s          sing-box only (fzf config picker)
  -s NAME     sing-box with NAME.json from singbox dir
  -of/-fs/-ofs  any combination

Examples:
  vpn              run all three
  vpn -s           sing-box with fzf picker
  vpn -s free1     sing-box with free1.json
  vpn -of          OpenVPN + FortiVPN
```

**Requires `.env`** — copy `.env.example` and fill your credentials.

---

## Setup

```bash
git clone <repo> ~/arsenal
cd ~/arsenal
cp .env.example .env
# fill .env with your credentials

# symlink commands
ln -sf ~/arsenal/netinfo ~/.local/bin/netinfo
ln -sf ~/arsenal/arz ~/.local/bin/arz
```

## Dependencies

| Tool | Install |
|------|---------|
| `openfortivpn` | `brew install openfortivpn` |
| `openconnect` | `brew install openconnect` |
| `openvpn` | `brew install openvpn` |
| `sing-box` | `brew install sing-box` |
| `oathtool` | `brew install oath-toolkit` |
| `fzf` | `brew install fzf` |
| `speedtest` | `brew install speedtest-cli` |
