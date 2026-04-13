#!/bin/sh
# Auto-generate self-signed SSL certificate if not present
CERT_DIR="/etc/nginx/certs"
CERT_FILE="$CERT_DIR/cert.pem"
KEY_FILE="$CERT_DIR/key.pem"

if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
    echo "Generating self-signed SSL certificate..."
    mkdir -p "$CERT_DIR"
    openssl req -x509 -nodes -days 3650 \
        -newkey rsa:2048 \
        -keyout "$KEY_FILE" \
        -out "$CERT_FILE" \
        -subj "/C=DE/ST=Local/L=Home/O=Parley/CN=parley.local" \
        -addext "subjectAltName=DNS:parley.local,DNS:localhost,IP:127.0.0.1"
    echo "SSL certificate generated at $CERT_DIR"
else
    echo "SSL certificate already exists, skipping generation."
fi
