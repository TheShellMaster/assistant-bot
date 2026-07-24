<div align="center">
  <img src="https://img.shields.io/badge/Python-3.8+-blue.svg" alt="Python Version" />
  <img src="https://img.shields.io/badge/opencode-Ready-success.svg" alt="opencode Integration" />
  <img src="https://img.shields.io/badge/Telegram-Bot-blue.svg" alt="Telegram Bot" />
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License MIT" />
</div>

# 🚀 Assistant Bot — opencode via Telegram

Une interface Telegram complète, interactive et ultra-stable pour [opencode](https://opencode.ai). Développée en Python, elle permet de contrôler entièrement vos sessions de développement IA depuis votre téléphone ou votre bureau, avec une gestion robuste des processus système (venv, systemd).

## ✨ Fonctionnalités Actuelles (v2.3)

Le bot a été entièrement réécrit pour offrir une stabilité maximale, sans crash, même avec de gros messages ou des requêtes intensives.

| Catégorie | Commandes / Explications |
|-----------|-------------------------|
| **Contrôle** | `/start` — Menu principal.<br>`/opencode_start` — Démarrer le serveur API opencode.<br>`/opencode_stop` — Stopper le serveur API opencode. |
| **Sécurité** | `/permissions` — Gérer l'approbation des commandes bash : Demander, Autoriser tout, Bloquer tout. |
| **Modèles** | `/models` — Sélectionner le modèle IA + variante (high, max, minimal, défaut) via un menu interactif. |
| **Sessions** | `/session` — Gérer vos conversations (Reprendre ou Supprimer).<br>`/new` — Démarrer une nouvelle session fraîche. |
| **Système** | `/version` — Version actuelle d'opencode.<br>`/stats` — Statistiques d'utilisation.<br>`/upgrade` — Mettre à jour opencode.<br>`/config` — Afficher la configuration. |
| **Administration** | `/grant` — Créer un code d'accès invité à usage unique.<br>`/auth` — S'authentifier avec un code invité. |

## 🔐 Accès privé

Le bot est privé par défaut :
- **Admin** : accès complet à toutes les commandes (défini via `ADMIN_CHAT_ID` dans `.env_bot`).
- **Invités** : accès restreint aux commandes `/start`, `/version`, `/stats` + envoi de messages à l'IA.
- **Non-authentifiés** : voient uniquement l'écran d'accueil avec instruction `/auth CODE`.

L'admin génère des codes avec `/grant`, l'invité les utilise avec `/auth CODE`.

## 🔮 Nouveautés à Venir (Roadmap)

Nous travaillons actuellement sur les prochaines grosses fonctionnalités qui seront bientôt intégrées au bot :

- **`/mcp`** : Intégration des serveurs MCP (Model Context Protocol).
- **`/fork`** : Dupliquer une session en cours pour explorer une autre idée sans perdre l'originale.
- **`/export` & `/import`** : Sauvegarder et restaurer vos sessions de développement.
- **`/find` & `/read`** : Navigation avancée dans les fichiers locaux.
- **`/vcs`** : Intégration des contrôles Git (commit, diff, etc.) directement depuis Telegram.

## ⚙️ Installation "Tout en un" (Serveur Linux / AWS)

Le script d'installation a été optimisé pour s'intégrer proprement sur les systèmes Linux modernes (Debian, Ubuntu, AWS) via un environnement virtuel (`venv`) respectant la norme PEP 668.

```bash
# 1. Cloner le projet
git clone https://github.com/TheShellMaster/assistant-bot.git
cd assistant-bot

# 2. Rendre le script exécutable
chmod +x install.sh

# 3. Lancer l'installation automatisée
sudo ./install.sh
```

Le script s'occupe de :
- Vérifier / Installer **opencode**.
- Créer un **environnement virtuel Python** (`venv`).
- Installer les dépendances (`python-telegram-bot`, `requests`).
- Demander et configurer votre **Token Telegram**.
- Créer et démarrer le **service systemd** (`assistant-bot.service`).

## 🛠 Commandes utiles du serveur

Une fois installé, le bot tourne silencieusement en arrière-plan.

```bash
# Voir les logs en direct (très utile pour le débogage) :
sudo journalctl -u assistant-bot.service -f

# Arrêter le bot :
sudo systemctl stop assistant-bot.service

# Redémarrer le bot :
sudo systemctl restart assistant-bot.service
```

## 🔑 Obtenir un token Telegram

1. Ouvre Telegram et cherche [@BotFather](https://t.me/BotFather)
2. Envoie `/newbot`
3. Choisis un nom et un `@username`
4. Copie le token reçu et donne-le à l'installateur !
