#!/bin/bash
set -e

echo "=== Parley Server Setup ==="
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Check Docker
if ! command -v docker &> /dev/null; then
    echo "Docker is not installed. Please install Docker first."
    echo "https://docs.docker.com/engine/install/"
    exit 1
fi

# Check Docker Compose
if ! docker compose version &> /dev/null; then
    echo "Docker Compose is not available. Please install Docker Compose."
    exit 1
fi

# Check NVIDIA Docker runtime
if ! docker info 2>/dev/null | grep -q "nvidia"; then
    echo -e "${YELLOW}Warning: NVIDIA Docker runtime not detected.${NC}"
    echo "Install nvidia-container-toolkit for GPU support:"
    echo "https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html"
    echo ""
    read -p "Continue without GPU? (y/N) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Generate self-signed certificate if not exists
CERT_DIR="./nginx/certs"
if [ ! -f "$CERT_DIR/cert.pem" ]; then
    echo -e "${GREEN}Generating self-signed SSL certificate...${NC}"
    mkdir -p "$CERT_DIR"
    openssl req -x509 -nodes -days 3650 \
        -newkey rsa:2048 \
        -keyout "$CERT_DIR/key.pem" \
        -out "$CERT_DIR/cert.pem" \
        -subj "/C=DE/ST=Local/L=Home/O=Parley/CN=parley.local" \
        -addext "subjectAltName=DNS:parley.local,DNS:localhost,IP:127.0.0.1"
    echo "Certificate generated."
else
    echo "SSL certificate already exists, skipping."
fi

# Create data directory
mkdir -p ./data
echo "Data directory ready."

# Build and start containers
echo ""
echo -e "${GREEN}Building and starting containers...${NC}"
docker compose build
docker compose up -d

# Wait for Ollama to be ready
echo ""
echo "Waiting for Ollama to start..."
OLLAMA_RETRIES=0
OLLAMA_MAX_RETRIES=30
until docker compose exec ollama ollama list &>/dev/null; do
    OLLAMA_RETRIES=$((OLLAMA_RETRIES + 1))
    if [ "$OLLAMA_RETRIES" -ge "$OLLAMA_MAX_RETRIES" ]; then
        echo -e "${YELLOW}Ollama did not become ready in time. Check logs: docker compose logs ollama${NC}"
        exit 1
    fi
    echo "  Ollama not ready yet, retrying ($OLLAMA_RETRIES/$OLLAMA_MAX_RETRIES)..."
    sleep 2
done
echo "Ollama is ready."

# Pull LLM model
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:7b}"
echo -e "${GREEN}Pulling Ollama model: ${OLLAMA_MODEL}${NC}"
echo "This may take a while on first run..."
docker compose exec ollama ollama pull "$OLLAMA_MODEL"

# Health check
echo ""
echo "Checking server health..."
sleep 3

SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
HEALTH_URL="https://localhost:7443/api/health"

if curl -sk "$HEALTH_URL" > /dev/null 2>&1; then
    echo -e "${GREEN}Server is running!${NC}"
else
    echo -e "${YELLOW}Server may still be starting (Whisper model download takes time on first run).${NC}"
    echo "Check logs with: docker compose logs -f parley"
fi

echo ""
echo "========================================="
echo -e "${GREEN}Setup complete!${NC}"
echo ""
echo "Access the web app:"
echo "  Local:   https://localhost:7443"
echo "  Network: https://${SERVER_IP}:7443"
echo ""
echo "Your browser will show a certificate warning — this is"
echo "expected with self-signed certs. Click 'Advanced' → 'Proceed'."
echo ""
echo "On Android: open the URL, tap ⋮ → 'Add to Home screen'"
echo ""
echo "Useful commands:"
echo "  docker compose logs -f    # View logs"
echo "  docker compose restart    # Restart services"
echo "  docker compose down       # Stop services"
echo "========================================="
