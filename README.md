<div align="center">
  <img src="https://img.shields.io/badge/Python-3.8+-blue.svg" alt="Python Version" />
  <img src="https://img.shields.io/badge/opencode-Ready-success.svg" alt="opencode Integration" />
  <img src="https://img.shields.io/badge/Telegram-Bot-blue.svg" alt="Telegram Bot" />
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License MIT" />
</div>

# 🚀 Assistant Bot — opencode via Telegram

Une interface Telegram complète, interactive et ultra-stable pour [opencode](https://opencode.ai). Développée en Python, elle permet de contrôler entièrement vos sessions de développement IA depuis votre téléphone ou votre bureau, avec une gestion robuste des processus système (venv, systemd).

> Ce projet est un client communautaire indépendant. Il n'est pas développé, maintenu ou officiellement affilié à l'équipe OpenCode.

## Fonctionnalités

Le bot a été entièrement réécrit pour offrir une stabilité maximale, sans crash, même avec de gros messages ou des requêtes intensives.

| Catégorie | Commandes / Explications |
|-----------|-------------------------|
| **Contrôle** | `/start` — Menu principal.<br>`/opencode_start` — Démarrer explicitement le serveur API.<br>`/opencode_stop` — Arrêter explicitement le serveur API. |
| **Modèles** | `/models` — Lire le catalogue OpenCode et choisir un modèle et sa variante/son effort pour la session active. |
| **Outils** | `/tools` — Lire les outils disponibles pour le modèle actif et activer ou désactiver chaque outil par session. Les paramètres, entrées, sorties et erreurs des appels sont affichés. |
| **Sessions** | `/session` — Reprendre ou supprimer une session.<br>`/session_control` — Actions explicites : nouvelle session, fork, compactage, résumé, abandon et messages.<br>`/new`, `/fork`, `/compact`, `/abort` — Contrôles directs. |
| **Validations** | `/permissions` — Contrôler les permissions bash.<br>Les questions OpenCode et leurs choix sont présentés avec des boutons Telegram. |
| **Système** | `/version` — Version actuelle d'OpenCode.<br>`/stats` — Statistiques d'utilisation.<br>`/upgrade` — Mise à jour explicite.<br>`/config` — Configuration active. |
| **Administration** | `/grant` — Créer un code d'accès invité à usage unique.<br>`/auth` — S'authentifier avec un code invité. |

## 🔐 Accès privé

Le bot est privé par défaut :
- **Admin** : accès complet à toutes les commandes (défini via `ADMIN_CHAT_ID` dans `.env_bot`).
- **Invités** : accès restreint aux commandes `/start`, `/version`, `/stats` + envoi de messages à l'IA.
- **Non-authentifiés** : voient uniquement l'écran d'accueil avec instruction `/auth CODE`.

L'admin génère des codes avec `/grant`, l'invité les utilise avec `/auth CODE`.

## Principe de fonctionnement

Telegram est la couche d'interface. OpenCode reste la source de vérité pour les sessions, messages, modèles, variantes, outils, permissions, questions, fichiers, commandes et changements de fichiers. Le bot ne crée pas de session automatiquement selon sa taille, ne supprime pas d'historique et ne remplace pas les paramètres décidés par OpenCode.

Le bot écoute le flux d'événements SSE d'OpenCode pour réveiller la session concernée dès qu'un raisonnement, un appel d'outil, une validation ou une réponse évolue. Une lecture de cohérence des messages reste utilisée pour garantir qu'aucun résultat ne soit perdu lors d'une reconnexion.

Les actions qui modifient l'état sont explicites et déclenchées par une commande ou un bouton. Les contrôles avancés utilisent les routes natives exposées par la version installée d'OpenCode ; les options indisponibles pour un modèle ne sont pas inventées par le bot.

## ⚙️ Installation "Tout en un" (Serveur Linux / AWS)

Le script d'installation a été optimisé pour s'intégrer proprement sur les systèmes Linux modernes (Debian, Ubuntu, AWS) via un environnement virtuel (`venv`) respectant la norme PEP 668.

```bash
# 1. Cloner le projet
git clone https://github.com/TheShellMaster/assistant-bot.git
cd assistant-bot

# 2. Rendre le script exécutable
chmod +x install.sh

# 3. Lancer l'installation automatisée avec ton utilisateur normal
bash install.sh
```

Le script demande `sudo` seulement pour installer le service systemd. Ne le lance pas avec `sudo`, afin qu'OpenCode, sa configuration et le bot restent associes au meme utilisateur Linux.

Le script s'occupe de :
- Vérifier / Installer **opencode**.
- Créer un **environnement virtuel Python** (`venv`).
- Installer les dépendances (`python-telegram-bot`, `requests`).
- Demander et configurer votre **Token Telegram**.
- Créer et démarrer le **service systemd** (`assistant-bot.service`).

Le chemin exact du binaire OpenCode est enregistre dans `.env_bot`. Le bot conserve l'etat du serveur lors d'un redemarrage systemd; seul `/opencode_stop` desactive volontairement son redemarrage automatique. Les erreurs de lancement OpenCode sont visibles avec `sudo journalctl -u assistant-bot.service -f`.

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
