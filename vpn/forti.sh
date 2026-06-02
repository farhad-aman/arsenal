#!/bin/bash
# forti.sh — connect to FortiVPN using TOTP
# Usage: sudo bash forti.sh
# Requires: openfortivpn, oathtool, .env

set -e
ENV_FILE="$(dirname "$0")/../.env"
[[ -f "$ENV_FILE" ]] || { echo "Missing .env — copy .env.example and fill values"; exit 1; }
source "$ENV_FILE"

OTP=$(oathtool -b --totp "$FORTI_TOTP_SECRET")
sudo openfortivpn -c "$FORTI_CONFIG" --otp "$OTP" -q
