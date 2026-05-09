#!/usr/bin/env bash
# Einmalige Setup-Schritte auf dem VPS.
# Lokal aufrufen: scp deploy/setup-vps.sh root@vps:/root/ && ssh root@vps "bash /root/setup-vps.sh"

set -euo pipefail

INSTALL_DIR=${INSTALL_DIR:-/var/www/ai-provider-service}
LOG_DIR=${LOG_DIR:-/var/log/ai-provider}
SERVICE_NAME=ai-provider.service

echo "▶ Installations-Verzeichnis: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
mkdir -p "$LOG_DIR"
chown -R www-data:www-data "$LOG_DIR" || chown -R www-data:www-data "$LOG_DIR" || true

cd "$INSTALL_DIR"

# venv anlegen falls nicht da
if [ ! -d venv ]; then
    echo "▶ Lege venv an"
    python3 -m venv venv
fi

# Dependencies
./venv/bin/pip install --quiet --upgrade pip
if [ -f requirements.txt ]; then
    ./venv/bin/pip install --quiet -r requirements.txt
fi

# .env-Template falls noch nicht vorhanden
if [ ! -f .env ]; then
    echo "▶ Erstelle .env aus .env.example — bitte MASTER_KEY + SERVICE_TOKEN setzen!"
    cp .env.example .env
    chmod 600 .env
fi

# systemd-Unit installieren
if [ -f deploy/ai-provider.service ]; then
    cp deploy/ai-provider.service /etc/systemd/system/$SERVICE_NAME
    systemctl daemon-reload
    systemctl enable $SERVICE_NAME
fi

chown -R www-data:www-data "$INSTALL_DIR"

echo ""
echo "✓ Setup fertig."
echo ""
echo "Nächste Schritte:"
echo "  1) MASTER_KEY und SERVICE_TOKEN in $INSTALL_DIR/.env setzen"
echo "     MASTER_KEY=\$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
echo "     SERVICE_TOKEN=\$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
echo "  2) Apache-Config (deploy/apache-vhost.conf) in den vhost einfügen"
echo "  3) Service starten: systemctl start $SERVICE_NAME"
echo "  4) Status:          systemctl status $SERVICE_NAME"
echo "  5) Test:            curl http://127.0.0.1:8767/health"
