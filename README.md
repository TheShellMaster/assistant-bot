# Assistant Bot — opencode via Telegram

Interface Telegram complète pour [opencode](https://opencode.ai). Envoie un message, opencode répond. Change de modèle, d'agent, de session — tout depuis Telegram.

## Fonctionnalités

| Categorie | Commandes |
|-----------|-----------|
| **Chat** | Envoie un message → opencode répond |
| **Modeles** | `/model`, `/model_deepseek`, `/model_mimo`, `/model_nemotron`, `/model_north`, `/model_bigpickle`, `/models` |
| **Agents** | `/agent`, `/agent_plan`, `/agent_build`, `/agent_explore`, `/agent_general` |
| **Variants** | `/variant`, `/variant_high`, `/variant_max`, `/variant_minimal` |
| **Sessions** | `/continue_on`, `/continue_off`, `/session_new`, `/session_list`, `/fork`, `/export`, `/import` |
| **Systeme** | `/config`, `/thinking`, `/stats`, `/version`, `/providers`, `/serve`, `/upgrade`, `/github`, `/debug` |

## Installation

```bash
# 1. Cloner et installer
git clone <url-du-projet>
cd assistant-bot
chmod +x install.sh
sudo ./install.sh
```

Le script installe automatiquement :
- opencode (via le script officiel)
- Les dépendances Python (python-telegram-bot)
- Configure le token Telegram
- Met en place le service systemd

### Installation manuelle

```bash
# Dependances
pip install -r requirements.txt

# Token Telegram
echo "TELEGRAM_BOT_TOKEN=votre_token_ici" > .env_assistant

# Lancer
python3 assistant-bot.py
```

### Service systemd

```bash
sudo tee /etc/systemd/system/assistant-bot.service <<EOF
[Unit]
Description=Assistant Bot Telegram (opencode)
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$(pwd)
ExecStart=/usr/bin/python3 $(pwd)/assistant-bot.py
Restart=always
RestartSec=10
EnvironmentFile=$(pwd)/.env_assistant

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now assistant-bot
```

## Modeles disponibles

| Commande | Modele |
|----------|--------|
| `/model_deepseek` | `opencode/deepseek-v4-flash-free` |
| `/model_mimo` | `opencode/mimo-v2.5-free` |
| `/model_nemotron` | `opencode/nemotron-3-ultra-free` |
| `/model_north` | `opencode/north-mini-code-free` |
| `/model_bigpickle` | `opencode/big-pickle` |

## Agents opencode

| Commande | Agent | Usage |
|----------|-------|-------|
| `/agent_plan` | `plan` | Planifie, ne modifie rien |
| `/agent_build` | `build` | Developpement complet |
| `/agent_explore` | `explore` | Lecture seule, exploration |
| `/agent_general` | `general` | Agent general |
| `/agent_none` | (defaut) | Comportement standard |

## Variants (effort de raisonnement)

| Commande | Effet |
|----------|-------|
| `/variant_high` | Raisonnement eleve |
| `/variant_max` | Raisonnement maximum |
| `/variant_minimal` | Raisonnement minimal |
| `/variant_none` | Par defaut |

## Gestion des sessions

| Commande | Action |
|----------|--------|
| `/continue_on` | Active la session continue (conserve le contexte) |
| `/continue_off` | Nouvelle session a chaque message |
| `/session_new` | Cree une nouvelle session fraiche |
| `/session_list` | Liste toutes les sessions existantes |
| `/session_switch <id>` | Changer de session active |
| `/session_delete <id>` | Supprimer une session |
| `/fork` | Fork la session en cours |
| `/export` | Exporte la session en cours en JSON |
| `/import <fichier/url>` | Importe une session depuis un fichier JSON |

## Commandes systeme

| Commande | Action |
|----------|--------|
| `/config` | Affiche la configuration actuelle |
| `/thinking` | Active/desactive l'affichage du raisonnement |
| `/models` | Liste tous les modeles disponibles |
| `/version` | Affiche la version d'opencode |
| `/providers` | Liste les fournisseurs AI configures |
| `/stats` | Statistiques d'utilisation (7 jours) |
| `/upgrade` | Met a jour opencode |
| `/serve` | Demarre le serveur headless opencode |
| `/github` | Integration GitHub |
| `/debug` | Outils de debug opencode |

## Obtenir un token Telegram

1. Ouvre Telegram et cherche [@BotFather](https://t.me/BotFather)
2. Envoie `/newbot`
3. Choisis un nom (ex: `Mon Assistant`)
4. Choisis un username (ex: `mon_assistant_bot`)
5. Copie le token et colle-le dans l'installateur

## Dependances

- Python 3.8+
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) >= 22
- [opencode](https://opencode.ai) >= 1.18

## Structure du projet

```
assistant-bot/
├── assistant-bot.py    # Bot Telegram principal
├── install.sh          # Script d'installation complet
├── requirements.txt    # Dependances Python
├── README.md           # Ce fichier
├── .gitignore          # Fichiers ignores par git
└── .env_assistant      # Token Telegram (genere par install.sh)
```
