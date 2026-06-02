#!/bin/bash
# pirouz.sh — connect to vpn-aws via OpenConnect
# Usage: sudo bash pirouz.sh
# Requires: openconnect, .env

ENV_FILE="$(dirname "$0")/../.env"
[[ -f "$ENV_FILE" ]] || { echo "Missing .env — copy .env.example and fill values"; exit 1; }
source "$ENV_FILE"

echo "$PIROUZ_PASS" | sudo openconnect --user="$PIROUZ_USER" --passwd-on-stdin -q "$PIROUZ_SERVER"
