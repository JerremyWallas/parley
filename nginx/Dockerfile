FROM nginx:alpine

# Install openssl for auto-generating self-signed certificates
RUN apk add --no-cache openssl

# Copy nginx config
COPY nginx/nginx.conf /etc/nginx/conf.d/default.conf

# Copy PWA frontend
COPY web/ /usr/share/nginx/html/

# Copy entrypoint script that auto-generates certs if missing
COPY nginx/docker-entrypoint.sh /docker-entrypoint.d/99-generate-certs.sh
RUN chmod +x /docker-entrypoint.d/99-generate-certs.sh

EXPOSE 80 443
