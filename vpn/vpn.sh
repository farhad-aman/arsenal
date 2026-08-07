#!/bin/bash
# vpn.sh — multi-VPN orchestrator: OpenVPN + FortiVPN + sing-box
#
# Usage:
#   vpn [-o] [-f] [-s] [-s NAME]   connect all or specific combo
#   vpn -k, --kill                 kill all VPN processes
#   vpn -p, --proxy                toggle shell proxy (127.0.0.1:2080)
#   vpn -o                         OpenVPN only
#   vpn -f                         FortiVPN only
#   vpn -s                         sing-box proxy :2080 (fzf config picker)
#   vpn -s NAME                    sing-box with NAME.json
#   vpn -t [NAME]                  sing-box TUN mode — per-app routing
#                                  (apps in ~/singbox/tun_apps.txt → proxy,
#                                   everything else → direct)
#   vpn -sP [NAME] / -tP [NAME]    add P to pick ONE node (fzf) from a multi-node
#                                  subscription config, instead of urltest auto
#   vpn -e, --edit                 interactively edit the TUN app list (fzf)
#   vpn -c, --check                test every config, list working ones + latency
#   vpn -a URL [NAME]              import a subscription -> one config, all nodes
#                                  behind a urltest group (auto-picks fastest)
#   vpn -ofs                       all three
#
# NOTE — proxy flag:
#   -p/--proxy must be sourced (not run in subprocess) to export env vars
#   into the current shell. The vpn() wrapper in .zshrc handles this:
#     vpn() {
#       case "${1:-}" in
#         -p|--proxy) source /path/to/vpn.sh "$@" ;;
#         *)          sudo bash /path/to/vpn.sh "$@" ;;
#       esac
#     }
#
# Requires: openvpn, openfortivpn, oathtool, sing-box, fzf, .env

ENV_FILE="$(dirname "$0")/../.env"
[[ -f "$ENV_FILE" ]] || { echo "Missing .env — copy .env.example and fill values"; exit 1; }
source "$ENV_FILE"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RESET='\033[0m'

log()  { echo -e "${CYAN}[vpn]${RESET} $*"; }
ok()   { echo -e "${GREEN}[ok]${RESET}  $*"; }

# ── Kill ──────────────────────────────────────────────────────
vpn_kill() {
    log "Killing all VPN processes..."

    sudo killall -TERM openfortivpn 2>/dev/null || true
    sudo killall -TERM pppd         2>/dev/null || true

    local i=0
    while ifconfig ppp0 &>/dev/null && (( i < 10 )); do
        sleep 1; (( i++ ))
    done

    if ifconfig ppp0 &>/dev/null; then
        sudo ifconfig ppp0 down 2>/dev/null || true
        sleep 1
        netstat -rn 2>/dev/null | awk '/ppp0/{print $1}' | while read -r r; do
            sudo route delete "$r" 2>/dev/null || true
            sleep 0.2
        done
    fi

    sudo killall -TERM openvpn 2>/dev/null || true
    sleep 2
    sudo killall -9 openvpn   2>/dev/null || true

    pkill -x sing-box 2>/dev/null || true

    ok "VPN state cleared"
}

# ── Proxy toggle ──────────────────────────────────────────────
vpn_proxy() {
    if [ -z "$http_proxy" ]; then
        export http_proxy="http://127.0.0.1:2080"
        export https_proxy="http://127.0.0.1:2080"
        export HTTP_PROXY="$http_proxy"
        export HTTPS_PROXY="$https_proxy"
        export ALL_PROXY="socks5://127.0.0.1:2080"
        export no_proxy="localhost,127.0.0.1,::1,.local,.svc,.cluster.local,.snapp,.snapp.ir,.snapp.tech,.snappcloud.io,.baly"
        export NO_PROXY="$no_proxy"
        ok "Proxy ON  (127.0.0.1:2080)"
    else
        unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY no_proxy NO_PROXY
        ok "Proxy OFF"
    fi
}

# ── Edit TUN app list (interactive) ───────────────────────────
vpn_edit() {
    local f="${SINGBOX_DIR}/tun_apps.txt"
    command -v fzf >/dev/null 2>&1 || { echo "fzf required for -e"; return 1; }
    touch "$f"

    _apps()   { grep -vE '^[[:space:]]*#|^[[:space:]]*$' "$f"; }
    _header() { grep -E '^[[:space:]]*#' "$f"; }
    _save()   {   # stdin = app lines → dedupe, keep comment header on top
        local hdr; hdr="$(_header)"
        { [[ -n "$hdr" ]] && printf '%s\n\n' "$hdr"; sort -u | grep -v '^$'; } \
            > "$f.tmp" && mv "$f.tmp" "$f"
    }

    while true; do
        echo ""
        echo -e "${CYAN}TUN tunneled apps${RESET}  ($f)"
        echo    "──────────────────────────────────────────"
        if _apps | grep -q .; then _apps | nl -w2 -s'. '
        else echo "  (empty — all traffic stays direct)"; fi
        echo ""
        echo -e "  ${GREEN}a${RESET} add    ${GREEN}r${RESET} remove    ${GREEN}q${RESET} quit"
        read -r -n1 -p "> " c; echo ""
        case "$c" in
            a)
                local picks
                picks=$(ps -axo comm= 2>/dev/null | sed 's#.*/##' | sort -u \
                    | fzf --multi --prompt="add (TAB=multi, ENTER=confirm) > " \
                          --height=70% --border --header="running processes")
                [[ -n "$picks" ]] && { _apps; echo "$picks"; } | _save
                ;;
            r)
                local picks
                picks=$(_apps | fzf --multi --prompt="remove (TAB=multi) > " \
                          --height=70% --border --header="current tunneled apps")
                [[ -n "$picks" ]] && _apps | grep -vxF -f <(echo "$picks") | _save
                ;;
            q|"") break ;;
        esac
    done
    ok "Saved. Run 'vpn -t' to apply."
}

# ── Import a subscription ─────────────────────────────────────
# Fetches a v2ray/xray subscription URL (or local file of share links) and
# writes ONE sing-box config holding every node behind a urltest group, so
# sing-box auto-picks the fastest and fails over on its own.
vpn_sub() {
    local url="$2" name="$3"
    [[ -n "$url" ]] || { echo "Usage: vpn -a <sub-url|file> [name]"; return 1; }
    if [[ -z "$name" ]]; then                      # gorbe.rnziscoding.baby -> gorbe
        name="$(echo "$url" | sed -E 's#^[a-z]+://##; s#[:/].*##; s#\..*##')"
        [[ -n "$name" ]] || name="sub"
    fi
    local out="${SINGBOX_DIR}/${name}.json"

    log "Importing subscription -> $out"
    python3 "$(dirname "$0")/sub2singbox.py" "$url" "$out" || return 1
    if sing-box check -c "$out" >/dev/null 2>&1; then
        ok "$name.json valid — use it with: vpn -s $name   (or vpn -t $name)"
    else
        echo "Config written but sing-box check failed:"
        sing-box check -c "$out"
        return 1
    fi
}

# ── Test all sing-box configs ─────────────────────────────────
# Spins each config up on its own loopback port, fetches through it,
# records latency + exit IP, then tears it down. All configs in parallel.
_sb_probe() {   # $1=config  $2=port  $3=tmpdir
    local cfg="$1" port="$2" tmp="$3"
    local name; name="$(basename "$cfg" .json)"
    local tcfg="$tmp/$name.cfg.json"

    if ! python3 - "$cfg" "$tcfg" "$port" 2>/dev/null <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
c["log"] = {"level": "error"}
c["inbounds"] = [{"type": "mixed", "tag": "probe-in",
                  "listen": "127.0.0.1", "listen_port": int(sys.argv[3])}]
json.dump(c, open(sys.argv[2], "w"))
PY
    then
        echo "FAIL|99999|$name|invalid json" > "$tmp/$name.res"; return
    fi

    sing-box run -c "$tcfg" >"$tmp/$name.log" 2>&1 &
    local pid=$!

    local n=0                       # wait for listener, max 3s
    while ! nc -z 127.0.0.1 "$port" 2>/dev/null; do
        n=$((n + 1)); [[ $n -gt 30 ]] && break
        sleep 0.1
    done

    local out
    out=$(https_proxy="http://127.0.0.1:$port" \
          curl -s --max-time 12 -w '\n%{time_total}' https://api.ipify.org 2>/dev/null)

    kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null

    local ip t
    ip="$(echo "$out" | head -1)"
    t="$(echo "$out"  | tail -1)"
    if [[ -n "$ip" && "$ip" != "$t" ]]; then
        local ms; ms="$(python3 -c "print(int(float('$t')*1000))" 2>/dev/null || echo 0)"
        echo "OK|$ms|$name|$ip" > "$tmp/$name.res"
    else
        local err
        err="$(sed 's/\x1b\[[0-9;]*m//g' "$tmp/$name.log" \
               | grep -oE 'reality verification failed|unexpected HTTP response status: [0-9]+|connection refused|no such host|i/o timeout' \
               | tail -1)"
        echo "FAIL|99999|$name|${err:-no response (timeout)}" > "$tmp/$name.res"
    fi
}

vpn_test() {
    local dir="${SINGBOX_DIR}"
    local configs=("$dir"/*.json)
    [[ -e "${configs[0]}" ]] || { echo "No .json configs in $dir"; return 1; }

    local tmp; tmp="$(mktemp -d)"

    log "Testing ${#configs[@]} sing-box configs..."
    local i=0
    for cfg in "${configs[@]}"; do
        _sb_probe "$cfg" $((29100 + i)) "$tmp" &
        i=$((i + 1))
    done
    wait

    echo ""
    printf "  %-16s %-7s %-9s %s\n" "CONFIG" "STATUS" "LATENCY" "EXIT IP / ERROR"
    echo "  ─────────────────────────────────────────────────────────────"
    # OK before FAIL (reverse alpha), then fastest first
    cat "$tmp"/*.res 2>/dev/null | sort -t'|' -k1,1r -k2,2n \
    | while IFS='|' read -r st ms name val; do
        if [[ "$st" == "OK" ]]; then
            printf "  ${GREEN}%-16s %-7s %-9s %s${RESET}\n" "$name" "OK" "${ms}ms" "$val"
        else
            printf "  ${YELLOW}%-16s %-7s %-9s %s${RESET}\n" "$name" "FAIL" "-" "$val"
        fi
    done

    local good; good="$(grep -l '^OK' "$tmp"/*.res 2>/dev/null | wc -l | tr -d ' ')"
    echo ""
    ok "$good/${#configs[@]} working — connect with: vpn -s <name>"
    rm -rf "$tmp"
}

# ── Dispatch special flags ────────────────────────────────────
case "${1:-}" in
    -k|--kill)  vpn_kill;  return 0 2>/dev/null || exit 0 ;;
    -p|--proxy) vpn_proxy; return 0 2>/dev/null || exit 0 ;;
    -e|--edit)  vpn_edit;  return 0 2>/dev/null || exit 0 ;;
    -c|--check) vpn_test;  return 0 2>/dev/null || exit 0 ;;
    -a|--add)   vpn_sub "$@"; return 0 2>/dev/null || exit 0 ;;
esac

set -e

SINGBOX_CONFIG="${SINGBOX_DIR}/config.json"
OVPN_LOG=/tmp/openvpn.log
FORTI_LOG=/tmp/forti.log
SINGBOX_LOG=/tmp/singbox.log

# ── Parse flags ───────────────────────────────────────────────
RUN_O=0; RUN_F=0; RUN_S=0; RUN_T=0; PICK=0
SB_CONFIG_ARG=""

if [[ $# -eq 0 ]]; then
    RUN_O=1; RUN_F=1; RUN_S=1
else
    ARG="${1#-}"
    LETTERS="${ARG//[0-9]/}"
    [[ "$LETTERS" == *o* ]] && RUN_O=1
    [[ "$LETTERS" == *f* ]] && RUN_F=1
    [[ "$LETTERS" == *s* ]] && RUN_S=1
    [[ "$LETTERS" == *t* ]] && RUN_T=1
    [[ "$LETTERS" == *P* ]] && PICK=1   # pick one node instead of urltest auto
    [[ $RUN_T -eq 1 ]] && RUN_S=1   # TUN mode implies sing-box

    if [[ $RUN_S -eq 1 && -n "$2" ]]; then
        NEXT="${2%.json}"
        NEXT="${NEXT##*/}"
        SB_CONFIG_ARG="$NEXT"
        shift
    fi

    if [[ $RUN_O -eq 0 && $RUN_F -eq 0 && $RUN_S -eq 0 ]]; then
        echo "Usage: vpn [-o] [-f] [-s] [-t] (combine: -of, -fs, -st, -ofs)"
        echo "  -o          OpenVPN only"
        echo "  -f          FortiVPN only"
        echo "  -s          sing-box proxy on :2080 (interactive config picker)"
        echo "  -s NAME     sing-box with NAME.json"
        echo "  -t          sing-box TUN mode: per-app routing via ~/singbox/tun_apps.txt"
        echo "  -t NAME     TUN mode with NAME.json"
        echo "  -sP / -tP   pick one node (fzf) from a subscription config, not urltest auto"
        exit 0
    fi
fi

# ── sing-box config selection ─────────────────────────────────
if [[ $RUN_S -eq 1 ]]; then
    if [[ -n "$SB_CONFIG_ARG" ]]; then
        SINGBOX_CONFIG="${SINGBOX_DIR}/${SB_CONFIG_ARG}.json"
        [[ ! -f "$SINGBOX_CONFIG" ]] && { echo "sing-box config not found: $SINGBOX_CONFIG"; exit 1; }
    else
        CHOSEN=$(ls "${SINGBOX_DIR}"/*.json 2>/dev/null \
            | xargs -I{} basename {} \
            | fzf --prompt="sing-box config > " --height=40% --border --no-sort)
        [[ -z "$CHOSEN" ]] && { echo "No config selected."; exit 1; }
        SINGBOX_CONFIG="${SINGBOX_DIR}/${CHOSEN}"
    fi
    log "sing-box config: $(basename "$SINGBOX_CONFIG")"

    # Pick one node manually (fzf) instead of urltest auto-select. Rewrites a
    # temp config keeping only the chosen node (first, so TUN tags it "proxy")
    # + direct, with route.final -> chosen. Skipped for single-node configs.
    if [[ $PICK -eq 1 ]]; then
        SB_PICK_TMP=/tmp/singbox_pick.json
        NODE_TAGS=$(python3 -c '
import json, sys
c = json.load(open(sys.argv[1]))
for o in c.get("outbounds", []):
    if o.get("type") not in ("urltest", "selector", "direct", "block", "dns"):
        print(o.get("tag", ""))
' "$SINGBOX_CONFIG")
        NCOUNT=$(echo "$NODE_TAGS" | grep -c .)
        if [[ "$NCOUNT" -le 1 ]]; then
            log "  Only one node in this config — nothing to pick."
        else
            PICKED_TAG=$(echo "$NODE_TAGS" | fzf --prompt="pick node ($NCOUNT) > " \
                --height=70% --border --no-sort --header="route ALL traffic through one node")
            [[ -z "$PICKED_TAG" ]] && { echo "No node picked."; exit 1; }
            python3 -c '
import json, sys
c = json.load(open(sys.argv[1])); tag = sys.argv[2]
obs = c.get("outbounds", [])
chosen  = [o for o in obs if o.get("tag") == tag]
special = [o for o in obs if o.get("type") in ("direct", "block", "dns")]
c["outbounds"] = chosen + special
c.setdefault("route", {})["final"] = tag
json.dump(c, open(sys.argv[3], "w"), indent=2)
' "$SINGBOX_CONFIG" "$PICKED_TAG" "$SB_PICK_TMP"
            SINGBOX_CONFIG="$SB_PICK_TMP"
            log "  Node: ${GREEN}${PICKED_TAG}${RESET} — all traffic routed through it."
        fi
    fi
fi

TOTAL=$(( RUN_O + RUN_F + RUN_S ))
STEP=0
OVPN_UTUN=""   # detected after OpenVPN connects

# ── Cleanup ───────────────────────────────────────────────────
CLEANED=0
cleanup() {
    [[ $CLEANED -eq 1 ]] && return
    CLEANED=1
    echo ""
    log "Disconnecting..."

    [[ -n $TAIL_OVPN_PID ]]  && kill $TAIL_OVPN_PID  2>/dev/null
    [[ -n $TAIL_FORTI_PID ]] && kill $TAIL_FORTI_PID 2>/dev/null
    [[ -n $TAIL_SB_PID ]]    && kill $TAIL_SB_PID    2>/dev/null

    [[ -n $SINGBOX_PID ]] && kill $SINGBOX_PID 2>/dev/null
    pkill -x sing-box 2>/dev/null || true

    if [[ -n $FORTI_PID ]]; then
        sudo kill -TERM $FORTI_PID 2>/dev/null
        for i in $(seq 1 5); do
            kill -0 $FORTI_PID 2>/dev/null || break
            sleep 1
        done
    fi
    if ifconfig ppp0 &>/dev/null 2>&1; then
        sudo killall pppd 2>/dev/null || true
        sleep 1
        netstat -rn 2>/dev/null | awk '/ppp0/ {print $1}' | while read -r r; do
            sudo route delete "$r" 2>/dev/null || true
        done
        sudo ifconfig ppp0 down 2>/dev/null || true
    fi

    if [[ $RUN_O -eq 1 ]]; then
        sudo killall -TERM openvpn 2>/dev/null || true
        sleep 3
        sudo killall -9 openvpn 2>/dev/null || true
    fi

    log "Done."
}
trap cleanup INT TERM EXIT

# ── 1. OpenVPN (proxy-only: --route-nopull) ───────────────────
if [[ $RUN_O -eq 1 ]]; then
    STEP=$(( STEP + 1 ))
    log "[$STEP/$TOTAL] OpenVPN starting (proxy-only, no route takeover)..."

    # Snapshot existing utun interfaces so we can detect the new one
    UTUNS_BEFORE=$(ifconfig -l 2>/dev/null | tr ' ' '\n' | grep '^utun' | sort)

    > "$OVPN_LOG"
    # --route-nopull: ignore all pushed routes (incl. redirect-gateway)
    # tunnel comes up, local/remote IPs assigned, but routing table unchanged
    sudo openvpn --config "$OVPN_CONFIG" --askpass "$OVPN_KEYPASS" --verb 2 \
        --route-nopull \
        >> "$OVPN_LOG" 2>&1 &

    tail -f "$OVPN_LOG" 2>/dev/null \
        | grep --line-buffered -iE "Initialization Sequence Completed|AUTH_FAILED|TLS Error|EXITING|WARNING|error" \
        | sed 's/^/  [ovpn] /' &
    TAIL_OVPN_PID=$!

    # Wait for a NEW utun to appear (avoids false-positive on utun0/1/2 which always exist)
    until comm -13 <(echo "$UTUNS_BEFORE") \
                   <(ifconfig -l 2>/dev/null | tr ' ' '\n' | grep '^utun' | sort) \
        | grep -q .; do
        sleep 1
        if grep -q "AUTH_FAILED\|TLS Error\|EXITING\|Connection refused\|No route to host" "$OVPN_LOG" 2>/dev/null; then
            echo ""; log "OpenVPN fatal error:"; cat "$OVPN_LOG"; exit 1
        fi
    done

    OVPN_UTUN=$(comm -13 <(echo "$UTUNS_BEFORE") \
                          <(ifconfig -l 2>/dev/null | tr ' ' '\n' | grep '^utun' | sort) | head -1)

    kill $TAIL_OVPN_PID 2>/dev/null; unset TAIL_OVPN_PID
    echo ""
    ok "[$STEP/$TOTAL] OpenVPN up  ($OVPN_UTUN — proxy tunnel ready)"

    # If sing-box is also starting, route its proxy server IPs through OpenVPN's utun
    # so sing-box reaches its upstream via OpenVPN while everything else stays direct/forti
    if [[ $RUN_S -eq 1 && -f "$SINGBOX_CONFIG" ]]; then
        log "  Routing sing-box proxy servers via $OVPN_UTUN..."
        python3 - "$SINGBOX_CONFIG" <<'PYEOF' | while read -r ip; do
import json, sys, socket
with open(sys.argv[1]) as f:
    cfg = json.load(f)
for ob in cfg.get("outbounds", []):
    srv = ob.get("server", "")
    if srv:
        try:
            print(socket.gethostbyname(srv))
        except Exception:
            pass
PYEOF
            sudo route add "$ip" -interface "$OVPN_UTUN" 2>/dev/null \
                && log "    route → $ip via $OVPN_UTUN" || true
        done
    fi
fi

# ── 2. FortiVPN ───────────────────────────────────────────────
if [[ $RUN_F -eq 1 ]]; then
    STEP=$(( STEP + 1 ))
    log "[$STEP/$TOTAL] FortiVPN starting..."
    > "$FORTI_LOG"
    OTP=$(oathtool -b --totp "$FORTI_TOTP_SECRET")
    sudo openfortivpn -c "$FORTI_CONFIG" --otp "$OTP" -q \
        >> "$FORTI_LOG" 2>&1 &
    FORTI_PID=$!

    tail -f "$FORTI_LOG" 2>/dev/null \
        | grep --line-buffered -iE "established|connected|error|warn|failed|tunnel" \
        | sed 's/^/  [forti] /' &
    TAIL_FORTI_PID=$!

    until ifconfig ppp0 &>/dev/null 2>&1; do
        sleep 1
        if ! kill -0 $FORTI_PID 2>/dev/null; then
            echo ""; log "FortiVPN exited unexpectedly:"; cat "$FORTI_LOG"; exit 1
        fi
    done

    kill $TAIL_FORTI_PID 2>/dev/null; unset TAIL_FORTI_PID
    echo ""
    ok "[$STEP/$TOTAL] FortiVPN up  (ppp0 ready)"

    # FortiVPN pushes a default route via ppp0 — remove it so regular internet stays direct
    # Snapp-specific subnet routes (10.x.x.x etc.) remain intact on ppp0
    sudo route delete default -interface ppp0 2>/dev/null && \
        log "  Removed ppp0 default route — internet stays direct" || true
fi

# ── TUN config builder ────────────────────────────────────────
# Wraps chosen config's outbound with a tun inbound + process_name route
# rules so listed apps go through the proxy and everything else stays direct.
TUN_APPS_FILE="${SINGBOX_DIR}/tun_apps.txt"
TUN_CONFIG=/tmp/singbox_tun.json

build_tun_config() {
    python3 - "$SINGBOX_CONFIG" "$TUN_APPS_FILE" "$TUN_CONFIG" <<'PYEOF'
import json, sys

src_path, apps_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]

with open(src_path) as f:
    cfg = json.load(f)

# Read app list (skip comments/blanks)
apps = []
try:
    with open(apps_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                apps.append(line)
except FileNotFoundError:
    pass

outbounds = cfg.get("outbounds", [])
if not outbounds:
    sys.exit("no outbounds in source config")

# Pick first real proxy outbound as the tunnel target; tag it "proxy"
proxy_tag = None
for ob in outbounds:
    if ob.get("type") not in ("direct", "block", "dns"):
        ob["tag"] = "proxy"
        proxy_tag = "proxy"
        break
if proxy_tag is None:
    sys.exit("no proxy outbound found in source config")

# Ensure direct outbound exists
if not any(ob.get("type") == "direct" for ob in outbounds):
    outbounds.append({"type": "direct", "tag": "direct"})
else:
    for ob in outbounds:
        if ob.get("type") == "direct":
            ob["tag"] = "direct"
            break

# Keep existing proxy inbounds (so :2080 still works) + add tun inbound
inbounds = cfg.get("inbounds", [])
# Collect tags of explicit proxy inbounds (mixed/socks/http) — these must
# ALWAYS route to proxy regardless of which app connected to them.
proxy_inbound_tags = [
    ib.get("tag") for ib in inbounds
    if ib.get("type") in ("mixed", "socks", "http") and ib.get("tag")
]
if not any(ib.get("type") == "tun" for ib in inbounds):
    inbounds.append({
        "type": "tun",
        "tag": "tun-in",
        "address": ["172.18.0.1/30", "fdfe:dcba:9876::1/126"],
        "auto_route": True,
        "strict_route": False,
        "stack": "gvisor"
    })
cfg["inbounds"] = inbounds

# Route: :2080 proxy inbound -> always proxy; listed apps -> proxy; rest -> direct
route = cfg.get("route", {})
route["auto_detect_interface"] = True
route["default_domain_resolver"] = "local-dns"  # resolve outbound server domains via system resolver
route["final"] = "direct"
rules = [r for r in route.get("rules", []) if "process_name" not in r and "inbound" not in r
         and r.get("action") != "reject"]
if apps:
    rules.insert(0, {"process_name": apps, "outbound": "proxy"})
if proxy_inbound_tags:
    # Anything hitting :2080 goes through the proxy, whatever app it is
    rules.insert(0, {"inbound": proxy_inbound_tags, "outbound": "proxy"})
# Guard against a routing loop: traffic destined to the TUN's own gateway must
# be rejected, never sent to "direct" (which would dial the TUN and re-enter it,
# pinning several cores). Must stay FIRST so it wins before final=direct.
rules.insert(0, {"ip_cidr": ["172.18.0.1/30", "fdfe:dcba:9876::1/126"], "action": "reject"})
route["rules"] = rules
cfg["route"] = route

# DNS: proxy inbound + proxied apps resolve via proxy (no leak), rest via system resolver
dns_rules = []
if proxy_inbound_tags:
    dns_rules.append({"inbound": proxy_inbound_tags, "server": "proxy-dns"})
if apps:
    dns_rules.append({"process_name": apps, "server": "proxy-dns"})
cfg["dns"] = {
    "servers": [
        {"type": "udp", "tag": "proxy-dns", "server": "1.1.1.1", "detour": "proxy"},
        {"type": "local", "tag": "local-dns"}
    ],
    "rules": dns_rules,
    "final": "local-dns",
    "strategy": "prefer_ipv4"
}

with open(out_path, "w") as f:
    json.dump(cfg, f, indent=2)

print(",".join(apps) if apps else "<none>")
PYEOF
}

# ── 3. sing-box ───────────────────────────────────────────────
if [[ $RUN_S -eq 1 ]]; then
    STEP=$(( STEP + 1 ))
    RUN_CONFIG="$SINGBOX_CONFIG"

    if [[ $RUN_T -eq 1 ]]; then
        log "[$STEP/$TOTAL] sing-box starting (TUN mode)..."
        APPS=$(build_tun_config) || { echo "TUN config build failed: $APPS"; exit 1; }
        RUN_CONFIG="$TUN_CONFIG"
        if [[ "$APPS" == "<none>" ]]; then
            log "  ${YELLOW}No apps listed in tun_apps.txt — ALL traffic stays direct.${RESET}"
            log "  Edit ${TUN_APPS_FILE} to add apps."
        else
            echo ""
            log "  Tunneled apps (→ proxy):"
            echo "$APPS" | tr ',' '\n' | while read -r app; do
                [[ -n "$app" ]] && echo -e "    ${GREEN}•${RESET} $app"
            done
            log "  Everything else → direct"
            echo ""
        fi
    else
        log "[$STEP/$TOTAL] sing-box starting..."
    fi

    pkill -x sing-box 2>/dev/null || true; sleep 1
    > "$SINGBOX_LOG"
    sing-box run -c "$RUN_CONFIG" >> "$SINGBOX_LOG" 2>&1 &
    SINGBOX_PID=$!
    sleep 2

    if ! kill -0 $SINGBOX_PID 2>/dev/null; then
        log "sing-box failed to start:"; cat "$SINGBOX_LOG"; exit 1
    fi

    echo ""
    if [[ $RUN_T -eq 1 ]]; then
        ok "[$STEP/$TOTAL] sing-box up  (TUN + proxy :2080)"
    else
        ok "[$STEP/$TOTAL] sing-box up  (proxy on :2080)"
    fi
fi

# ── Running ───────────────────────────────────────────────────
echo ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log "Connected. Ctrl+C disconnects."
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [[ $RUN_S -eq 1 && -n $SINGBOX_PID ]]; then
    log "sing-box live log:"
    tail -f "$SINGBOX_LOG" \
        | grep --line-buffered -iE "started|stopped|listen|closed|warn|error|fatal" \
        | sed 's/^/  [singbox] /' &
    TAIL_SB_PID=$!
fi

if [[ -n $FORTI_PID ]]; then
    wait $FORTI_PID
elif [[ -n $SINGBOX_PID ]]; then
    wait $SINGBOX_PID
else
    wait
fi
