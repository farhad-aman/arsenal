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

### `net-reset` — Brutal Network Reset

Fixes broken macOS network state after VPN usage, network switches, or configd crashes — without rebooting.

Kills all VPN processes, tears down stale interfaces/routes, cycles WiFi, renews DHCP, flushes DNS, and restarts network daemons.

```
Usage: net-reset [options]

Options:
  --quick   Skip WiFi cycle (faster, less brutal)
  -h        Help

Examples:
  net-reset          full reset
  net-reset --quick  skip WiFi cycle (try this first)
```

After running, if proxy env vars are still stuck in your shell:
```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY no_proxy NO_PROXY
```

---

### `dl` — Download Manager

Moved out to its own repository: **[firelink](https://github.com/farhad-aman/firelink)**.

An IDM-style download manager in the terminal — queue, pause/resume, per-filetype
destinations, torrents, YouTube — it outgrew a folder among shell scripts.

```bash
brew install aria2
git clone https://github.com/farhad-aman/firelink.git
cd firelink && make install
```

---

### `vpn` — Multi-VPN Orchestrator

Manages OpenVPN + FortiVPN + sing-box connections with graceful startup/shutdown.

```
Usage: sudo vpn [options]

Options:
  -o          OpenVPN only
  -f          FortiVPN only
  -s          sing-box proxy on :2080 (fzf config picker)
  -s NAME     sing-box with NAME.json from singbox dir
  -t [NAME]   sing-box TUN mode — per-app routing
  -e, --edit  interactively edit the TUN app list (fzf add/remove)
  -c, --check test every sing-box config, list working ones + latency
  -a URL [NAME]  import a subscription (all nodes, auto-picks fastest); remembers URL
  -a          list saved subscriptions
  -u [NAME]   refresh saved sub(s) from their URL (all if no NAME)
  -of/-fs/-ofs  any combination

Examples:
  vpn              run all three
  vpn -s           sing-box proxy with fzf picker
  vpn -s free1     sing-box proxy with free1.json
  vpn -t           TUN mode, pick config via fzf
  vpn -t free1     TUN mode with free1.json
  vpn -of          OpenVPN + FortiVPN
```

**Two sing-box modes:**

| Mode | Flag | How apps use it |
|------|------|-----------------|
| Proxy | `-s` | Apps must be configured to use `127.0.0.1:2080` (SOCKS5/HTTP). System traffic untouched. |
| TUN | `-t` | Per-app routing. Apps listed in `~/singbox/tun_apps.txt` go through the tunnel automatically; everything else stays direct. No app config needed. |

**TUN per-app routing** — edit `~/singbox/tun_apps.txt`, one exact process name per line:
```
firefox
Telegram
```
Apps in the list tunnel through the proxy (incl. DNS); everything else uses the normal network. TUN mode also keeps the `:2080` proxy available.

**Easier: edit the list interactively** with `vpn -e`:
```
TUN tunneled apps  (~/singbox/tun_apps.txt)
──────────────────────────────────────────
 1. firefox
 2. Telegram

  a add    r remove    q quit
```
- `a` → fzf multi-select from running processes (TAB to mark multiple, ENTER to confirm) → appended
- `r` → fzf multi-select from current list → removed
- `q` → save & quit, then run `vpn -t` to apply

Manual find of a process name: `ps -axo comm= | sed 's#.*/##' | sort -u | grep -i <app>`.

**Find a working config** with `vpn -c`. Spins every `~/singbox/*.json` up on its own
loopback port in parallel, fetches through it, then tears it down. Working ones first,
fastest at the top:
```
  CONFIG           STATUS  LATENCY   EXIT IP / ERROR
  ─────────────────────────────────────────────────────────────
  lab              OK      291ms     193.108.119.137
  it-parvane8      OK      447ms     193.168.175.94
  de-vip1          FAIL    -         unexpected HTTP response status: 403
  ro-parvane2      FAIL    -         reality verification failed

[ok]  7/10 working — connect with: vpn -s <name>
```
Latency is real end-to-end request time through the proxy (not ICMP ping).

**Import a subscription** with `vpn -a <url> [name]`:
```bash
vpn -a https://example.com/sub/abc123        # -> ~/singbox/example.json
vpn -a https://example.com/sub/abc123 gorbe  # -> ~/singbox/gorbe.json
vpn -a ~/links.txt mine                      # local file of share links
```
Handles base64 or plaintext subscriptions; parses `vless://`, `vmess://`, `trojan://`.

All nodes land in **one** config behind a sing-box `urltest` group — it probes them
every 5m, routes through the fastest, and fails over automatically. No need to pick a
node by hand:
```bash
vpn -s gorbe    # proxy on :2080, auto-selected node (fastest, auto-failover)
vpn -t gorbe    # TUN mode, same auto-selection
```

**Pick one node by hand** instead of auto — add `P` to the flag:
```bash
vpn -sP gorbe   # fzf list of all nodes → route everything through the one you pick
vpn -tP gorbe   # same, in TUN mode
```
`P` drops you into an fzf picker of every node in the config; the chosen node handles
all traffic for that session (no urltest). Single-node configs skip the picker. If the
node you pick turns out dead, just re-run without `P` for auto-select.

`xhttp`, `kcp`, and `quic` nodes are skipped — sing-box has no such transports (they're
Xray-only). Reality nodes missing a public key are skipped too. The import prints the
counts.

**Saved & refreshable.** Every `vpn -a` remembers the URL in `~/singbox/subs.conf`
(`name<TAB>url`), so you never paste the link twice:
```bash
vpn -a               # list saved subscriptions
vpn -u gorbe         # re-fetch gorbe from its saved URL, rebuild the config
vpn -u               # refresh ALL saved subs
```
Subscription hosts are often filtered — the importer fetches direct first, then falls
back through the local sing-box proxy on `:2080`, so a proxy must be up when you import
or refresh a blocked sub. (Override the fallback with `SUB_PROXY=socks5://host:port`.)
`subs.conf` lives outside the git repo — the token URLs are never committed.

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
ln -sf ~/arsenal/net-reset ~/.local/bin/net-reset
```

## Dependencies

| Tool | Install |
|------|---------|
| `aria2` | `brew install aria2` |
| `openfortivpn` | `brew install openfortivpn` |
| `openconnect` | `brew install openconnect` |
| `openvpn` | `brew install openvpn` |
| `sing-box` | `brew install sing-box` |
| `oathtool` | `brew install oath-toolkit` |
| `fzf` | `brew install fzf` |
| `speedtest` | `brew install speedtest-cli` |
