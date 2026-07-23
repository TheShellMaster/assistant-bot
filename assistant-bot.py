"""
assistant-bot.py — Interface Telegram complète pour opencode
Auteur  : Assistant
Version : 2.1 (Gestion stricte du cycle de vie et anti-crash)
"""

import asyncio
import json
import logging
import os
import secrets
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import requests
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    style="{",
    format="{asctime} [{levelname}] {message}",
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Chemins & constantes
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent.resolve()
ENV_FILE      = BASE_DIR / ".env_bot"
CONFIG_FILE   = Path.home() / ".assistant_config.json"
OPENCODE_PORT = 4097
BASE_URL      = f"http://127.0.0.1:{OPENCODE_PORT}"

FREE_MODELS = {
    "deepseek":  ("opencode", "deepseek-v4-flash-free"),
    "mimo":      ("opencode", "mimo-v2.5-free"),
    "nemotron":  ("opencode", "nemotron-3-ultra-free"),
    "north":     ("opencode", "north-mini-code-free"),
    "bigpickle": ("opencode", "big-pickle"),
    "laguna":    ("opencode", "laguna-s-2.1-free"),
}

AGENTS    = ["build", "plan", "explore", "general"]
VARIANTS  = {"Défaut": "", "high": "high", "max": "max", "minimal": "minimal"}
ADMIN_LINK = "t.me/King_premium_N5"

DEFAULT_CONFIG = {
    "model_provider":   "opencode",
    "model_id":         "deepseek-v4-flash-free",
    "variant":          "",
    "agent":            "",
    "continue_session": True,
    "session_id":       "",
}

# ─────────────────────────────────────────────────────────────────────────────
# Authentification (admin + invités)
# ─────────────────────────────────────────────────────────────────────────────
AUTH_CODES_FILE = BASE_DIR / ".auth_codes.json"
AUTHORIZED_FILE = BASE_DIR / ".authorized_ids.json"

auth_codes = {}
authorized_ids = set()
if AUTH_CODES_FILE.exists():
    auth_codes = json.loads(AUTH_CODES_FILE.read_text())
if AUTHORIZED_FILE.exists():
    authorized_ids = set(json.loads(AUTHORIZED_FILE.read_text()))

def _save_codes():
    AUTH_CODES_FILE.write_text(json.dumps(auth_codes))

def _save_auth():
    AUTHORIZED_FILE.write_text(json.dumps(list(authorized_ids)))

def _get_admin():
    v = os.getenv("ADMIN_CHAT_ID")
    if v: return v.strip()
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().strip().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                if k.strip() == "ADMIN_CHAT_ID":
                    return v.strip().strip("\"'")
    return None

def is_admin(cid):
    a = _get_admin()
    return a is not None and str(cid) == a

def is_authorized(cid):
    return is_admin(cid) or cid in authorized_ids

async def _req_admin(upd):
    if not is_admin(upd.effective_chat.id):
        await upd.message.reply_text(f"\u26d4 Reserve a l'admin.\nAdmin : {ADMIN_LINK}")
        return False
    return True

async def _req_auth(upd):
    if not is_authorized(upd.effective_chat.id):
        await upd.message.reply_text(f"\u26d4 Acces refuse. Contacte l'admin : {ADMIN_LINK}\nou utilise /auth CODE.")
        return False
    return True

# ─────────────────────────────────────────────────────────────────────────────
# État global
# ─────────────────────────────────────────────────────────────────────────────
opencode_proc: Optional[subprocess.Popen] = None
_main_loop:    Optional[asyncio.AbstractEventLoop] = None
opencode_enabled: bool = True  # Contrôlé par /opencode_start et /opencode_stop

active_pollers:      dict[str, "SessionPoller"] = {}
pending_permissions: dict[str, int] = {}

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text())
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)

def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

def get_token() -> Optional[str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if token:
        return token
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().strip().splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                if k.strip() == "TELEGRAM_BOT_TOKEN":
                    return v.strip().strip("\"'")
    return None

# ─────────────────────────────────────────────────────────────────────────────
# Gestion du serveur opencode
# ─────────────────────────────────────────────────────────────────────────────
def _server_cwd() -> str:
    return str(Path.home())

def _is_server_alive() -> bool:
    try:
        r = requests.get(f"{BASE_URL}/global/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False

def start_opencode() -> bool:
    global opencode_proc, opencode_enabled
    opencode_enabled = True
    if opencode_proc and opencode_proc.poll() is None and _is_server_alive():
        log.info("opencode déjà actif (pid=%d)", opencode_proc.pid)
        return True
    
    log.info("Démarrage opencode serve sur le port %d…", OPENCODE_PORT)
    opencode_proc = subprocess.Popen(
        ["opencode", "serve", "--port", str(OPENCODE_PORT)],
        cwd=_server_cwd(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(30):
        time.sleep(0.5)
        if _is_server_alive():
            log.info("opencode prêt (pid=%d).", opencode_proc.pid)
            return True
    log.warning("opencode n'a pas répondu dans les délais.")
    return False

def stop_opencode() -> None:
    global opencode_proc, opencode_enabled
    opencode_enabled = False
    if opencode_proc:
        opencode_proc.terminate()
        try:
            opencode_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            opencode_proc.kill()
        opencode_proc = None
    log.info("opencode arrêté par l'utilisateur.")

def ensure_opencode() -> bool:
    """Relance opencode uniquement si l'utilisateur ne l'a pas désactivé."""
    if not opencode_enabled:
        return False
    if not _is_server_alive():
        return start_opencode()
    return True

# ─────────────────────────────────────────────────────────────────────────────
# opencode API
# ─────────────────────────────────────────────────────────────────────────────
def _api(method: str, path: str, **kwargs) -> requests.Response:
    return requests.request(method, f"{BASE_URL}{path}", timeout=30, **kwargs)

def _create_session() -> str:
    r = _api("POST", "/session")
    r.raise_for_status()
    sid = r.json()["id"]
    cfg = load_config()
    cfg["session_id"] = sid
    save_config(cfg)
    return sid

def get_or_create_session() -> str:
    cfg = load_config()
    sid = cfg.get("session_id", "")
    if sid and cfg.get("continue_session"):
        try:
            r = _api("GET", f"/session/{sid}")
            if r.status_code == 200:
                return sid
        except Exception:
            pass
    return _create_session()

def list_sessions() -> list[dict]:
    try:
        r = _api("GET", "/session")
        if r.status_code == 200:
            return r.json() or []
    except Exception:
        pass
    return []

def delete_session(sid: str) -> bool:
    try:
        r = _api("DELETE", f"/session/{sid}")
        return r.status_code in (200, 204)
    except Exception:
        return False

def send_prompt(sid: str, text: str) -> bool:
    cfg = load_config()
    payload: dict = {"parts": [{"type": "text", "text": text}]}
    if cfg.get("model_provider") and cfg.get("model_id"):
        payload["model"] = {"providerID": cfg["model_provider"], "modelID": cfg["model_id"]}
    if cfg.get("variant"): payload["variant"] = cfg["variant"]
    if cfg.get("agent"):   payload["agent"]   = cfg["agent"]
    
    try:
        r = _api("POST", f"/session/{sid}/prompt_async", json=payload)
        if r.status_code in (200, 204):
            return True
        log.error("send_prompt failed: %d %s", r.status_code, r.text)
    except Exception as e:
        log.error("send_prompt error: %s", e)
    return False

def get_messages(sid: str) -> list[dict]:
    try:
        r = _api("GET", f"/session/{sid}/message")
        if r.status_code == 200:
            return r.json() or []
    except Exception:
        pass
    return []

def get_permissions() -> list[dict]:
    try:
        r = _api("GET", "/permission")
        if r.status_code == 200:
            return r.json() or []
    except Exception:
        pass
    return []

def reply_permission(pid: str, reply: str) -> bool:
    try:
        r = _api("POST", f"/permission/{pid}/reply", json={"reply": reply})
        return r.status_code == 200
    except Exception as e:
        log.error("reply_permission error : %s", e)
        return False

def _set_opencode_bash_mode(mode: str) -> bool:
    config_path = Path.home() / ".config" / "opencode" / "opencode.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(config_path.read_text()) if config_path.exists() else {}
        if "permission" not in data: data["permission"] = {}
        data["permission"]["bash"] = mode
        config_path.write_text(json.dumps(data, indent=2))
        return True
    except Exception as e:
        log.error("_set_opencode_bash_mode error: %s", e)
        return False

# ─────────────────────────────────────────────────────────────────────────────
# SessionPoller
# ─────────────────────────────────────────────────────────────────────────────
class SessionPoller:
    def __init__(self, sid: str, chat_id: int, status_msg_id: int, bot):
        self.sid           = sid
        self.chat_id       = chat_id
        self.status_msg_id = status_msg_id
        self.bot           = bot
        self._stop         = False
        self._seen_perms:  set[str] = set()
        self._done_tools:  set[str] = set()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop = True

    def _run(self) -> None:
        while not self._stop:
            time.sleep(1.5)
            try:
                self._check_permissions()
                if self._check_messages():
                    self._stop = True
            except Exception as e:
                log.error("SessionPoller[%s] error: %s", self.sid[:8], e)

    def _check_permissions(self) -> None:
        for perm in get_permissions():
            pid = perm["id"]
            if pid not in self._seen_perms:
                self._seen_perms.add(pid)
                pending_permissions[pid] = self.chat_id
                self._schedule(self._send_perm_request(perm))

    def _check_messages(self) -> bool:
        msgs = get_messages(self.sid)
        if not msgs: return False

        last = next((m for m in reversed(msgs) if m.get("role") != "user"), None)
        if not last: return False

        parts = last.get("parts", [])
        tool_lines = []
        for p in parts:
            if p.get("type") != "tool": continue
            pid    = p.get("id", "")
            state  = p.get("state", {})
            status = state.get("status", "")
            title  = state.get("title") or state.get("input", {}).get("description", "") or p.get("tool", "tool")
            
            if status in ("completed", "error") and pid not in self._done_tools:
                self._done_tools.add(pid)
                if status == "completed":
                    out = state.get("output") or state.get("metadata", {}).get("output") or "OK"
                    tool_lines.append(f"🔧 <b>{title}</b> → {str(out).strip()[:100]}")
                else:
                    err = state.get("error", "erreur")
                    tool_lines.append(f"❌ <b>{title}</b> → {str(err)[:100]}")

        if tool_lines:
            self._schedule(self._edit_status("⚙️ <i>Opencode travaille…</i>\n\n" + "\n".join(tool_lines[-5:])))

        final_text = next((p["text"] for p in parts if p.get("type") == "text" and p.get("text")), "")
        running = [p for p in parts if p.get("type") == "tool" and p.get("state", {}).get("status") in ("running", "pending")]
        perms_pending = [p for p in get_permissions() if p.get("sessionID") == self.sid]

        if final_text and not running and not perms_pending:
            self._schedule(self._send_final(final_text))
            return True
        return False

    def _schedule(self, coro) -> None:
        if _main_loop and not _main_loop.is_closed():
            asyncio.run_coroutine_threadsafe(coro, _main_loop)

    async def _edit_status(self, text: str) -> None:
        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id, message_id=self.status_msg_id,
                text=text[:4000], parse_mode="HTML",
            )
        except Exception:
            pass

    async def _send_final(self, text: str) -> None:
        try: await self.bot.delete_message(chat_id=self.chat_id, message_id=self.status_msg_id)
        except Exception: pass

        for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
            sent = False
            # Essai 1: Markdown
            try:
                await self.bot.send_message(chat_id=self.chat_id, text=chunk, parse_mode="Markdown")
                sent = True
            except Exception: pass
            
            # Essai 2: MarkdownV2
            if not sent:
                try:
                    await self.bot.send_message(chat_id=self.chat_id, text=chunk, parse_mode="MarkdownV2")
                    sent = True
                except Exception: pass
            
            # Essai 3: Fallback sans formatage (Anti-Crash garanti)
            if not sent:
                try:
                    await self.bot.send_message(chat_id=self.chat_id, text=chunk)
                except Exception as e:
                    log.error("send_final plain text error: %s", e)

    async def _send_perm_request(self, perm: dict) -> None:
        pid  = perm["id"]
        cmd  = perm.get("metadata", {}).get("command", "") or ", ".join(perm.get("patterns", []))
        desc = perm.get("metadata", {}).get("description", "")
        text = f"⚠️ <b>Opencode demande une permission</b>\n\n🔧 <b>Outil</b> : <code>{perm.get('permission', 'bash')}</code>\n📋 <b>Commande</b> : <code>{cmd}</code>\n"
        if desc: text += f"📝 <b>Description</b> : {desc}\n"
        text += "\nAutorises-tu cette action ?"

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Une fois", callback_data=f"perm_once_{pid}"), InlineKeyboardButton("✅✅ Toujours", callback_data=f"perm_always_{pid}")],
            [InlineKeyboardButton("❌ Refuser", callback_data=f"perm_reject_{pid}")]
        ])
        try:
            await self.bot.send_message(chat_id=self.chat_id, text=text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            log.error("send_perm_request error: %s", e)

# ─────────────────────────────────────────────────────────────────────────────
# Handlers Telegram
# ─────────────────────────────────────────────────────────────────────────────
async def cmd_start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cid = upd.effective_chat.id
    if not is_authorized(cid):
        await upd.message.reply_text(
            "🤖 <b>Assistant Opencode</b>\n\n"
            "⛔ <b>Accès refusé</b>\n"
            f"Ce bot est privé. Contacte l'admin : {ADMIN_LINK}\n\n"
            "Utilise <code>/auth VOTRE_CODE</code> pour te connecter.",
            parse_mode="HTML"
        )
        return
    if is_admin(cid):
        await _cmd_start_admin(upd, ctx)
    else:
        await _cmd_start_guest(upd, ctx)

async def _cmd_start_admin(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = load_config()
    srv = "🟢 Allumé" if _is_server_alive() else "🔴 Éteint"
    var_name = next((k for k, v in VARIANTS.items() if v == cfg.get("variant", "")), cfg.get("variant", "défaut"))
    text = (
        f"🤖 <b>Assistant Opencode</b>\n\n"
        f"Serveur  : {srv}\n"
        f"Modèle   : <code>{cfg['model_provider']}/{cfg['model_id']}</code>\n"
        f"Variante : <code>{var_name}</code>\n\n"
        "<b>Contrôle :</b>\n"
        "/opencode_start – allumer opencode\n"
        "/opencode_stop – éteindre opencode\n"
        "/permissions – mode permission bash\n"
        "/models – changer de modèle\n"
        "/session – gérer les sessions\n"
        "/new – nouvelle session\n"
        "/abort – annuler\n\n"
        "<b>Admin :</b>\n"
        "/grant – créer un code invité\n\n"
        "Envoie un message pour parler à l'IA."
    )
    await upd.message.reply_text(text, parse_mode="HTML")

async def _cmd_start_guest(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = load_config()
    srv = "🟢 Allumé" if _is_server_alive() else "🔴 Éteint"
    text = (
        f"🤖 <b>Assistant Opencode</b>\n\n"
        f"Serveur  : {srv}\n\n"
        "Envoie un message pour parler à l'IA.\n\n"
        "/version – version opencode\n"
        "/stats – statistiques"
    )
    await upd.message.reply_text(text, parse_mode="HTML")

async def cmd_opencode_start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _req_admin(upd): return
    msg = await upd.message.reply_text("⏳ Démarrage opencode…")
    ok = start_opencode()
    await msg.edit_text("🟢 opencode est allumé et prêt." if ok else "❌ Échec du démarrage.")

async def cmd_opencode_stop(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _req_admin(upd): return
    stop_opencode()
    await upd.message.reply_text("🔴 opencode est éteint. (Il ne se rallumera plus tout seul).")

async def cmd_grant(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _req_admin(upd): return
    code = secrets.token_hex(4)
    auth_codes[code] = str(upd.effective_chat.id)
    _save_codes()
    admin_link = os.getenv("ADMIN_LINK", "")
    msg = f"🔑 Code invité : <code>{code}</code>\n\nLe destinataire utilise <code>/auth {code}</code>"
    if admin_link:
        msg += f"\nAdmin : {admin_link}"
    await upd.message.reply_text(msg, parse_mode="HTML")

async def cmd_auth(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cid = upd.effective_chat.id
    if is_admin(cid):
        await upd.message.reply_text("✅ Tu es l'admin, aucun code requis.")
        return
    if cid in authorized_ids:
        await upd.message.reply_text("✅ Tu es déjà autorisé.")
        return
    parts = ctx.args or []
    if not parts:
        await upd.message.reply_text("Utilisation : /auth CODE")
        return
    code = parts[0].strip()
    if code in auth_codes:
        authorized_ids.add(cid)
        _save_auth()
        del auth_codes[code]
        _save_codes()
        await upd.message.reply_text("✅ Accès accordé ! Envoie /start pour commencer.")
    else:
        await upd.message.reply_text(f"❌ Code invalide ou déjà utilisé.\nContacte l'admin : {ADMIN_LINK}")

async def cmd_permissions(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _req_admin(upd): return
    mode = "ask"
    try:
        cfg_path = Path.home() / ".config" / "opencode" / "opencode.json"
        if cfg_path.exists(): mode = json.loads(cfg_path.read_text()).get("permission", {}).get("bash", "ask")
    except Exception: pass

    kb = [
        [InlineKeyboardButton("🟡 Demander via Telegram (ask)", callback_data="perm_mode_ask")],
        [InlineKeyboardButton("🟢 Autoriser automatiquement (allow)", callback_data="perm_mode_allow")],
        [InlineKeyboardButton("🔴 Bloquer automatiquement (reject)", callback_data="perm_mode_reject")],
        [InlineKeyboardButton("❌ Fermer", callback_data="close_msg")],
    ]
    await upd.message.reply_text(f"🔐 <b>Permission bash</b>\nActuel : <b>{mode}</b>\n\nChoisis le comportement :", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))

async def cmd_models(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _req_admin(upd): return
    cfg = load_config()
    cur = f"{cfg['model_provider']}/{cfg['model_id']}"
    cur_var = cfg.get("variant", "")
    var_name = next((k for k, v in VARIANTS.items() if v == cur_var), "défaut")
    kb = [[InlineKeyboardButton(f"{k}" + (" ✓" if f"{p}/{m}" == cur else ""), callback_data=f"mod_{k}")] for k, (p, m) in FREE_MODELS.items()]
    kb.append([InlineKeyboardButton("❌ Fermer", callback_data="close_msg")])
    await upd.message.reply_text(f"Sélectionne le modèle :\nVariante actuelle : <b>{var_name}</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))

def _build_session_list_ui() -> tuple[str, list]:
    sessions = list_sessions()
    if not sessions:
        return "Aucune session active.", []
    cur = load_config().get("session_id", "")
    lines, kb = [], []
    for i, s in enumerate(sessions[:20]):
        sid = s.get("id", "")
        title = (s.get("title") or sid[:16]).strip()[:40]
        lines.append(f"{i+1}. <code>{title}</code>" + (" ◀ active" if sid == cur else ""))
        kb.append([InlineKeyboardButton(f"▶ Reprendre {i+1}", callback_data=f"ses_switch_{sid[:22]}"), InlineKeyboardButton(f"🗑 Supprimer {i+1}", callback_data=f"ses_delete_{sid[:22]}")])
    kb.append([InlineKeyboardButton("❌ Fermer", callback_data="close_msg")])
    return "📋 <b>Sessions</b>\n\n" + "\n".join(lines), kb

async def cmd_session(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _req_admin(upd): return
    text, kb = _build_session_list_ui()
    await upd.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb) if kb else None)

async def cmd_new(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _req_admin(upd): return
    sid = get_or_create_session()
    if sid in active_pollers: active_pollers.pop(sid).stop()
    _create_session()
    await upd.message.reply_text("✅ Nouvelle session propre démarrée.")

async def cmd_abort(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _req_admin(upd): return
    sid = load_config().get("session_id", "")
    if sid in active_pollers: active_pollers.pop(sid).stop()
    if sid:
        try: _api("POST", f"/session/{sid}/abort")
        except: pass
    await upd.message.reply_text("🛑 Requête annulée.")

async def cmd_version(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        r = subprocess.run(["opencode", "version"], capture_output=True, text=True, timeout=10)
        await upd.message.reply_text(f"📦 <b>Version opencode</b>\n<pre>{r.stdout.strip()}</pre>", parse_mode="HTML")
    except Exception as e:
        await upd.message.reply_text(f"❌ Erreur : {e}")

async def cmd_stats(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        r = subprocess.run(["opencode", "stats"], capture_output=True, text=True, timeout=10)
        await upd.message.reply_text(f"📊 <b>Statistiques opencode</b>\n<pre>{r.stdout.strip()[:3500]}</pre>", parse_mode="HTML")
    except Exception as e:
        await upd.message.reply_text(f"❌ Erreur : {e}")

async def cmd_upgrade(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _req_admin(upd): return
    msg = await upd.message.reply_text("⏳ Mise à jour opencode…")
    stop_opencode()
    try:
        r = subprocess.run(["opencode", "upgrade"], capture_output=True, text=True, timeout=120)
        out = (r.stdout or r.stderr or "Fait").strip()
        start_opencode()
        await msg.edit_text(f"✅ Mise à jour :\n\n<pre>{out[:2000]}</pre>", parse_mode="HTML")
    except Exception as e:
        start_opencode()
        await msg.edit_text(f"❌ Erreur : {e}")

async def cmd_config(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _req_admin(upd): return
    try:
        r = _api("GET", "/global/config")
        if r.status_code == 200:
            raw = json.dumps(r.json(), indent=2, ensure_ascii=False)
            await upd.message.reply_text(f"⚙️ <b>Config opencode</b>\n\n<pre>{raw[:3500]}</pre>", parse_mode="HTML")
            return
    except Exception:
        pass
    config_path = Path.home() / ".config" / "opencode" / "opencode.json"
    if config_path.exists():
        await upd.message.reply_text(f"⚙️ <b>Config (fichier)</b>\n\n<pre>{config_path.read_text()[:3500]}</pre>", parse_mode="HTML")
    else:
        await upd.message.reply_text("Fichier config introuvable.")

async def message_handler(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cid = upd.effective_chat.id
    if not is_authorized(cid):
        await upd.message.reply_text(f"⛔ Accès refusé. Contacte l'admin : {ADMIN_LINK}\nou utilise /auth CODE.")
        return
    text = upd.message.text.strip()
    
    if not ensure_opencode():
        await upd.message.reply_text("🔴 opencode est éteint. Envoie /opencode_start pour l'allumer.")
        return

    try: sid = get_or_create_session()
    except Exception as e:
        await upd.message.reply_text(f"❌ Erreur serveur: {e}")
        return

    if sid in active_pollers: active_pollers.pop(sid).stop()
    status_msg = await upd.message.reply_text("🤔 <i>Opencode réfléchit…</i>", parse_mode="HTML")

    if not send_prompt(sid, text):
        await status_msg.edit_text("❌ opencode n'a pas répondu.")
        return

    active_pollers[sid] = SessionPoller(sid, upd.effective_chat.id, status_msg.message_id, ctx.application.bot)

async def callback_handler(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = upd.callback_query
    await q.answer()
    data = q.data

    if data == "close_msg":
        try: await q.message.delete()
        except Exception: pass
        return

    if data.startswith("perm_mode_"):
        if not is_admin(q.from_user.id):
            await q.message.edit_text("⛔ Réservé à l'admin.")
            return
        mode = data[10:]
        ok = _set_opencode_bash_mode(mode)
        await q.message.edit_text(f"✅ Mode <b>{mode}</b> activé." if ok else "❌ Erreur de configuration.", parse_mode="HTML")
        return

    if data.startswith("perm_"):
        _, action, pid = data.split("_", 2)
        ok = reply_permission(pid, action)
        await q.message.edit_text({"once": "✅ Autorisé", "always": "✅✅ Toujours", "reject": "❌ Refusé"}.get(action, "Fait") if ok else "❌ Erreur de réponse.")
        if ok: pending_permissions.pop(pid, None)
        return

    if data.startswith("mod_"):
        if not is_admin(q.from_user.id):
            await q.message.edit_text("⛔ Réservé à l'admin.")
            return
        key = data[4:]
        if key not in FREE_MODELS:
            await q.message.edit_text("❌ Modèle inconnu.")
            return
        prov, mid = FREE_MODELS[key]
        ctx.user_data['sel_model'] = key
        cur_var = load_config().get("variant", "")
        kb = [[InlineKeyboardButton(f"{k}" + (" ✓" if v == cur_var else ""), callback_data=f"var_{v}")] for k, v in VARIANTS.items()]
        kb.append([InlineKeyboardButton("⬅️ Retour", callback_data="mod_back")])
        await q.message.edit_text(f"Sélectionne la variante pour <b>{key}</b> :", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("var_"):
        if not is_admin(q.from_user.id):
            await q.message.edit_text("⛔ Réservé à l'admin.")
            return
        variant = data[4:]
        key = ctx.user_data.get('sel_model')
        if not key or key not in FREE_MODELS:
            await q.message.edit_text("❌ Modèle non sélectionné.")
            return
        prov, mid = FREE_MODELS[key]
        cfg = load_config(); cfg["model_provider"] = prov; cfg["model_id"] = mid; cfg["variant"] = variant; save_config(cfg)
        var_name = next((k for k, v in VARIANTS.items() if v == variant), variant or "Défaut")
        await q.message.edit_text(f"✅ Modèle : <code>{prov}/{mid}</code> ({var_name})", parse_mode="HTML")
        return

    if data == "mod_back":
        if not is_admin(q.from_user.id): return
        cfg = load_config()
        cur = f"{cfg['model_provider']}/{cfg['model_id']}"
        kb = [[InlineKeyboardButton(f"{k}" + (" ✓" if f"{p}/{m}" == cur else ""), callback_data=f"mod_{k}")] for k, (p, m) in FREE_MODELS.items()]
        kb.append([InlineKeyboardButton("❌ Fermer", callback_data="close_msg")])
        await q.message.edit_text("Sélectionne le modèle :", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("ses_switch_") or data.startswith("ses_delete_"):
        if not is_admin(q.from_user.id):
            await q.message.edit_text("⛔ Réservé à l'admin.")
            return

    if data.startswith("ses_switch_"):
        prefix = data[11:]
        full_sid = next((s["id"] for s in list_sessions() if s["id"].startswith(prefix)), prefix)
        cfg = load_config(); cfg["session_id"] = full_sid; cfg["continue_session"] = True; save_config(cfg)
        text, kb = _build_session_list_ui()
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb) if kb else None)
        return

    if data.startswith("ses_delete_"):
        prefix = data[11:]
        full_sid = next((s["id"] for s in list_sessions() if s["id"].startswith(prefix)), prefix)
        delete_session(full_sid)
        cfg = load_config()
        if cfg.get("session_id", "").startswith(prefix):
            cfg["session_id"] = ""
            save_config(cfg)
        text, kb = _build_session_list_ui()
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb) if kb else None)
        return

async def error_handler(upd: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Exception while handling an update:", exc_info=ctx.error)
    try:
        if isinstance(upd, Update) and upd.effective_chat:
            await ctx.bot.send_message(chat_id=upd.effective_chat.id, text="⚠️ Une erreur interne est survenue, mais le bot continue de fonctionner.")
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────
async def post_init(app: Application) -> None:
    global _main_loop
    _main_loop = asyncio.get_event_loop()
    if opencode_enabled:
        threading.Thread(target=start_opencode, daemon=True).start()
    
    await app.bot.set_my_commands([
        BotCommand("start",          "Menu principal"),
        BotCommand("opencode_start", "Allumer opencode"),
        BotCommand("opencode_stop",  "Éteindre opencode"),
        BotCommand("permissions",    "Mode de permission bash"),
        BotCommand("models",         "Choisir le modèle"),
        BotCommand("session",        "Gérer les sessions"),
        BotCommand("new",            "Nouvelle session"),
        BotCommand("abort",          "Annuler la requête"),
        BotCommand("version",        "Version opencode"),
        BotCommand("stats",          "Statistiques opencode"),
        BotCommand("upgrade",        "Mettre à jour opencode"),
        BotCommand("config",         "Afficher la configuration"),
        BotCommand("grant",          "Créer un code invité"),
        BotCommand("auth",           "S'authentifier avec un code"),
    ])
    log.info("Assistant bot prêt.")

async def post_shutdown(app: Application) -> None:
    stop_opencode()

def main() -> None:
    token = get_token()
    if not token:
        log.error("TELEGRAM_BOT_TOKEN manquant.")
        return

    app = Application.builder().token(token).post_init(post_init).post_shutdown(post_shutdown).build()
    app.add_handler(CommandHandler("start",          cmd_start))
    app.add_handler(CommandHandler("opencode_start", cmd_opencode_start))
    app.add_handler(CommandHandler("opencode_stop",  cmd_opencode_stop))
    app.add_handler(CommandHandler("permissions",    cmd_permissions))
    app.add_handler(CommandHandler("models",         cmd_models))
    app.add_handler(CommandHandler("session",        cmd_session))
    app.add_handler(CommandHandler("new",            cmd_new))
    app.add_handler(CommandHandler("abort",          cmd_abort))
    app.add_handler(CommandHandler("version",        cmd_version))
    app.add_handler(CommandHandler("stats",          cmd_stats))
    app.add_handler(CommandHandler("upgrade",        cmd_upgrade))
    app.add_handler(CommandHandler("config",         cmd_config))
    app.add_handler(CommandHandler("grant",          cmd_grant))
    app.add_handler(CommandHandler("auth",           cmd_auth))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    app.add_error_handler(error_handler)
    
    log.info("Démarrage du bot…")
    app.run_polling()

if __name__ == "__main__":
    main()
