#!/usr/bin/env bash
set -euo pipefail

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BOT_DIR"

BOT_NAME="assistant-bot"
SERVICE_NAME="${BOT_NAME}.service"
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME"
ENV_FILE="$BOT_DIR/.env_bot"
PYTHON="python3"

# ---- Couleurs ----
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}   $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()   { echo -e "${RED}[ERR]${NC}  $*" >&2; exit 1; }

# ---- Vérifications préalables ----
info "Vérification de l'environnement..."
command -v "$PYTHON"  >/dev/null 2>&1 || err "Python3 n'est pas installé. Installe-le d'abord."
command -v sudo       >/dev/null 2>&1 || err "sudo requis."

# ---- 1. Installer opencode ----
if ! command -v opencode &>/dev/null; then
    info "Installation d'opencode..."
    curl -fsSL https://opencode.ai/install.sh | bash
    echo 'export PATH="$HOME/.opencode/bin:$PATH"' >> ~/.bashrc
    export PATH="$HOME/.opencode/bin:$PATH"
    ok "opencode installé."
else
    ok "opencode déjà présent ($(opencode --version 2>/dev/null || echo '?'))"
fi

# ---- 2. Environnement Virtuel Python ----
info "Configuration de l'environnement virtuel (venv)..."
if [ ! -d "$BOT_DIR/venv" ]; then
    $PYTHON -m venv "$BOT_DIR/venv"
    ok "Environnement virtuel créé."
else
    ok "Environnement virtuel déjà existant."
fi

info "Installation des dépendances Python dans le venv..."
"$BOT_DIR/venv/bin/pip" install -r "$BOT_DIR/requirements.txt"
ok "Dépendances installées."

# ---- 3. Token Telegram ----
if [ ! -f "$ENV_FILE" ]; then
    echo ""
    warn "=== Configuration du bot Telegram ==="
    echo "1. Ouvre Telegram et cherche @BotFather"
    echo "2. Envoie /newbot, choisis un nom et un username"
    echo "3. Copie le token reçu (ex: 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11)"
    echo ""
    read -rp "Colle ton token Telegram ici : " TOKEN
    while [ -z "$TOKEN" ]; do
        warn "Token requis."
        read -rp "Colle ton token Telegram : " TOKEN
    done
    echo "TELEGRAM_BOT_TOKEN=$TOKEN" > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    ok "Token enregistré dans $ENV_FILE"
else
    ok "Fichier de token existant : $ENV_FILE"
fi

# ---- 4. Admin Telegram ----
CURRENT_ADMIN=$(grep "^ADMIN_CHAT_ID=" "$ENV_FILE" 2>/dev/null | cut -d= -f2 || true)
if [ -z "$CURRENT_ADMIN" ]; then
    echo ""
    warn "=== Configuration de l'administrateur ==="
    echo "Pour restreindre l'accès au bot, entre ton identifiant Telegram (admin)."
    echo "Tu peux l'obtenir en écrivant à @userinfobot ou @getmyid_bot."
    echo "Laisse vide pour que le bot soit public (tout le monde peut l'utiliser)."
    echo ""
    read -rp "ID Telegram admin : " ADMIN_ID
    if [ -n "$ADMIN_ID" ]; then
        echo "ADMIN_CHAT_ID=$ADMIN_ID" >> "$ENV_FILE"
        ok "Admin ID enregistré."
    else
        warn "Aucun ID admin — le bot sera public. Tu pourras ajouter ADMIN_CHAT_ID plus tard dans $ENV_FILE"
    fi
else
    ok "Admin ID déjà configuré : $CURRENT_ADMIN"
fi

# ---- 5. Service systemd ----
info "Installation du service systemd..."
sudo tee "$SERVICE_FILE" >/dev/null << EOF
[Unit]
Description=Assistant Bot Telegram (opencode)
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$BOT_DIR
ExecStart=$BOT_DIR/venv/bin/python3 $BOT_DIR/assistant-bot.py
Restart=always
RestartSec=10
EnvironmentFile=$ENV_FILE

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

sleep 2
if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    ok "Service $SERVICE_NAME actif et démarré."
else
    warn "Le service n'a pas démarré. Vérifie: sudo journalctl -u $SERVICE_NAME -n 20"
fi

# ---- 6. Résumé ----
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Assistant Bot installé avec succès !${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "  Répertoire : $BOT_DIR"
echo "  Service    : sudo systemctl status $SERVICE_NAME"
echo "  Logs       : sudo journalctl -u $SERVICE_NAME -f"
echo "  Config     : $ENV_FILE"
echo ""
echo "  Pour envoyer un message au bot :"
echo "    1. Ouvre Telegram"
echo "    2. Cherche le username de ton bot"
echo "    3. Envoie /start"
echo "    4. Tape / pour voir les commandes disponibles"
echo ""
echo "  Pour arrêter le bot : sudo systemctl stop $SERVICE_NAME"
echo "  Pour le redémarrer : sudo systemctl restart $SERVICE_NAME"
echo ""
opencode --version 2>/dev/null && echo "  opencode est bien installé" || true
