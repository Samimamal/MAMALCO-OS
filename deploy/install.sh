#!/usr/bin/env bash
# MAMAL Central — one-time install on a fresh Ubuntu 22.04/24.04 server (IONOS VPS).
#
#   sudo bash /opt/mamal/deploy/install.sh mamalco.com you@example.com
#
# Sets up: Python app (systemd), nginx reverse proxy, HTTPS (Let's Encrypt),
# firewall, daily backups. Safe to re-run.
set -euo pipefail

DOMAIN="${1:?usage: install.sh DOMAIN EMAIL}"
EMAIL="${2:?usage: install.sh DOMAIN EMAIL}"
APP_DIR=/opt/mamal
DATA_DIR=/var/lib/mamal
BACKUP_DIR=/var/backups/mamal
ENV_FILE=/etc/mamal.env
SERVICE=mamal
APP_USER=mamal

[[ $EUID -eq 0 ]] || { echo "Run with sudo"; exit 1; }
[[ -f "$APP_DIR/requirements.txt" ]] || { echo "Code not found in $APP_DIR"; exit 1; }

echo "==> Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3-venv python3-pip nginx certbot python3-certbot-nginx ufw sqlite3 git

echo "==> App user and folders"
id "$APP_USER" &>/dev/null || useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$DATA_DIR" "$BACKUP_DIR"
chown -R "$APP_USER:$APP_USER" "$DATA_DIR"
chmod 750 "$DATA_DIR"

echo "==> Python environment"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "==> Settings ($ENV_FILE)"
  cat > "$ENV_FILE" <<CONF
MAMAL_DATA_DIR=$DATA_DIR
MAMAL_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
MAMAL_SECURE_COOKIES=1
CONF
  chmod 640 "$ENV_FILE"
  chown root:"$APP_USER" "$ENV_FILE"
fi

cli() { sudo -u "$APP_USER" env $(grep -v '^#' "$ENV_FILE" | xargs) "$APP_DIR/.venv/bin/python" -m mamal_central.cli "$@"; }

echo "==> Initial data"
cd "$APP_DIR"
if [[ ! -f "$DATA_DIR/mamal.db" ]]; then
  cli init-structures
  cli init-catalog
  cli import-city-points
  cli init-lodge-site
  echo
  echo "Create the admin account (password at least 10 characters):"
  read -rp "Admin username [sami]: " ADMIN
  cli create-user "${ADMIN:-sami}" --full-name "Sami Mamal" --admin
else
  echo "Database exists — keeping it."
fi

echo "==> Service"
cat > /etc/systemd/system/$SERVICE.service <<UNIT
[Unit]
Description=MAMAL Central
After=network.target

[Service]
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/.venv/bin/uvicorn --factory mamal_central.main:app_factory --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1
Restart=always
RestartSec=3
NoNewPrivileges=true
ProtectSystem=full
ReadWritePaths=$DATA_DIR

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now $SERVICE
systemctl restart $SERVICE

echo "==> nginx"
cat > /etc/nginx/sites-available/mamal <<NGINX
server {
    listen 80;
    server_name $DOMAIN www.$DOMAIN;
    client_max_body_size 12m;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGINX
ln -sf /etc/nginx/sites-available/mamal /etc/nginx/sites-enabled/mamal
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "==> Firewall"
ufw allow OpenSSH >/dev/null
ufw allow 'Nginx Full' >/dev/null
ufw --force enable >/dev/null

echo "==> HTTPS"
if certbot --nginx -d "$DOMAIN" -d "www.$DOMAIN" -m "$EMAIL" --agree-tos --non-interactive --redirect; then
  echo "HTTPS is on."
else
  echo "!! HTTPS failed. Check that $DOMAIN and www.$DOMAIN point to this server's IP, then run:"
  echo "   sudo certbot --nginx -d $DOMAIN -d www.$DOMAIN -m $EMAIL --agree-tos --redirect"
fi

echo "==> Daily backup (03:30, kept 14 days)"
cat > /etc/cron.d/mamal-backup <<CRON
30 3 * * * root d=\$(date +\\%F); sqlite3 $DATA_DIR/mamal.db ".backup '$BACKUP_DIR/mamal-\$d.db'" && tar -czf $BACKUP_DIR/uploads-\$d.tgz -C $DATA_DIR uploads 2>/dev/null; find $BACKUP_DIR -type f -mtime +14 -delete
CRON

echo
echo "Done. Site: https://$DOMAIN   Admin: https://$DOMAIN/login"
systemctl --no-pager --lines=3 status $SERVICE || true
