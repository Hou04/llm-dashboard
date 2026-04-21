Place your TLS certificate files here:

- fullchain.pem  — your SSL certificate + intermediate chain
- privkey.pem    — your private key

For Let's Encrypt (free):
  certbot certonly --standalone -d yourdomain.com
  cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem ./nginx/certs/
  cp /etc/letsencrypt/live/yourdomain.com/privkey.pem ./nginx/certs/

For development/testing (self-signed):
  openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout nginx/certs/privkey.pem \
    -out nginx/certs/fullchain.pem \
    -subj "/CN=localhost"

NEVER commit these files to version control.
