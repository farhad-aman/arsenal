#!/bin/bash
# open-connect.sh — connect to Snapp VPN via OpenConnect (fortinet protocol)
# Usage: sudo bash open-connect.sh
# Requires: openconnect, .env

ENV_FILE="$(dirname "$0")/../.env"
[[ -f "$ENV_FILE" ]] || { echo "Missing .env — copy .env.example and fill values"; exit 1; }
source "$ENV_FILE"

printf "%s\n" "$OC_PASS" | sudo openconnect "$OC_SERVER" \
  --protocol=fortinet \
  --passwd-on-stdin \
  --servercert "$OC_PIN" \
  --user="$OC_USER" \
  --disable-ipv6 \
  --force-dpd=10 \
  --reconnect-timeout=5 \
  --token-mode=totp \
  --token-secret=base32:"$FORTI_TOTP_SECRET" \
  -q
