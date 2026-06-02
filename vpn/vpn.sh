#!/bin/bash
# vpn.sh — multi-VPN orchestrator: OpenVPN + FortiVPN + sing-box
#
# Usage:
#   vpn [-o] [-f] [-s] [-s NAME]   connect all or specific combo
#   vpn -k, --kill                 kill all VPN processes
#   vpn -p, --proxy                toggle shell proxy (127.0.0.1:2080)
#   vpn -o                         OpenVPN only
#   vpn -f                         FortiVPN only
#   vpn -s                         sing-box only (fzf config picker)
#   vpn -s NAME                    sing-box with NAME.json
#   vpn -ofs                       all three
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

# ── Dispatch special flags ────────────────────────────────────
case "${1:-}" in
    -k|--kill)  vpn_kill;  exit 0 ;;
    -p|--proxy) vpn_proxy; exit 0 ;;
esac

set -e

SINGBOX_CONFIG="${SINGBOX_DIR}/config.json"
OVPN_LOG=/tmp/openvpn.log
FORTI_LOG=/tmp/forti.log
SINGBOX_LOG=/tmp/singbox.log

# ── Parse flags ───────────────────────────────────────────────
RUN_O=0; RUN_F=0; RUN_S=0
SB_CONFIG_ARG=""

if [[ $# -eq 0 ]]; then
    RUN_O=1; RUN_F=1; RUN_S=1
else
    ARG="${1#-}"
    LETTERS="${ARG//[0-9]/}"
    [[ "$LETTERS" == *o* ]] && RUN_O=1
    [[ "$LETTERS" == *f* ]] && RUN_F=1
    [[ "$LETTERS" == *s* ]] && RUN_S=1

    if [[ $RUN_S -eq 1 && -n "$2" ]]; then
        NEXT="${2%.json}"
        NEXT="${NEXT##*/}"
        SB_CONFIG_ARG="$NEXT"
        shift
    fi

    if [[ $RUN_O -eq 0 && $RUN_F -eq 0 && $RUN_S -eq 0 ]]; then
        echo "Usage: vpn [-o] [-f] [-s] (combine: -of, -fs, -ofs)"
        echo "  -o          OpenVPN only"
        echo "  -f          FortiVPN only"
        echo "  -s          sing-box only (interactive config picker)"
        echo "  -s NAME     sing-box with NAME.json"
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
fi

TOTAL=$(( RUN_O + RUN_F + RUN_S ))
STEP=0

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

# ── 1. OpenVPN ────────────────────────────────────────────────
if [[ $RUN_O -eq 1 ]]; then
    STEP=$(( STEP + 1 ))
    log "[$STEP/$TOTAL] OpenVPN starting..."
    > "$OVPN_LOG"
    sudo openvpn --config "$OVPN_CONFIG" --askpass "$OVPN_KEYPASS" --verb 2 \
        >> "$OVPN_LOG" 2>&1 &

    tail -f "$OVPN_LOG" 2>/dev/null \
        | grep --line-buffered -iE "Initialization Sequence Completed|AUTH_FAILED|TLS Error|EXITING|WARNING|error" \
        | sed 's/^/  [ovpn] /' &
    TAIL_OVPN_PID=$!

    until ifconfig 2>/dev/null | grep -qE "^tun|^utun"; do
        sleep 1
        if grep -q "AUTH_FAILED\|TLS Error\|EXITING\|Connection refused\|No route to host" "$OVPN_LOG" 2>/dev/null; then
            echo ""; log "OpenVPN fatal error:"; cat "$OVPN_LOG"; exit 1
        fi
    done

    kill $TAIL_OVPN_PID 2>/dev/null; unset TAIL_OVPN_PID
    echo ""
    ok "[$STEP/$TOTAL] OpenVPN up  (tun interface ready)"
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
fi

# ── 3. sing-box ───────────────────────────────────────────────
if [[ $RUN_S -eq 1 ]]; then
    STEP=$(( STEP + 1 ))
    log "[$STEP/$TOTAL] sing-box starting..."
    pkill -x sing-box 2>/dev/null || true; sleep 1
    > "$SINGBOX_LOG"
    sing-box run -c "$SINGBOX_CONFIG" >> "$SINGBOX_LOG" 2>&1 &
    SINGBOX_PID=$!
    sleep 2

    if ! kill -0 $SINGBOX_PID 2>/dev/null; then
        log "sing-box failed to start:"; cat "$SINGBOX_LOG"; exit 1
    fi

    echo ""
    ok "[$STEP/$TOTAL] sing-box up  (proxy on :2080)"
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
