"""
assistant-bot.py — Interface Telegram complète pour opencode
Auteur  : Assistant
Version : 2.1 (Gestion stricte du cycle de vie et anti-crash)
"""

import asyncio
import html
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


class _TelegramTokenFilter(logging.Filter):
    """Keep Telegram bot tokens out of systemd/journal logs."""

    def filter(self, record):
        return "api.telegram.org/bot" not in record.getMessage()


for _handler in logging.getLogger().handlers:
    _handler.addFilter(_TelegramTokenFilter())

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Chemins & constantes
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent.resolve()
ENV_FILE      = BASE_DIR / ".env_bot"
CONFIG_FILE   = Path.home() / ".assistant_config.json"
OPENCODE_PORT = 4097
BASE_URL      = f"http://127.0.0.1:{OPENCODE_PORT}"
OPENCODE_BIN  = os.getenv("OPENCODE_BIN", "opencode").strip() or "opencode"

FREE_MODELS = {
    "mimo":       ("opencode", "mimo-v2.5-free"),
    "nemotron":   ("opencode", "nemotron-3-ultra-free"),
    "lightning":  ("opencode", "nemotron-3.5-lightning-free"),
    "ling":       ("opencode", "ling-3.0-flash-fin-free"),
    "bigpickle":  ("opencode", "big-pickle"),
    "musespark":  ("opencode", "muse-spark-1.2-contributor-free"),
}

AGENTS    = ["build", "plan", "explore", "general"]
VARIANTS  = {"Défaut": "", "high": "high", "max": "max", "minimal": "minimal"}
ADMIN_LINK = "t.me/King_premium_N5"

DEFAULT_CONFIG = {
    "model_provider":   "opencode",
    "model_id":         "mimo-v2.5-free",
    "variant":          "",
    "agent":            "",
    "continue_session": True,
    "session_id":       "",
    "opencode_enabled": True,
    "session_options": {},
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
opencode_last_error: str = ""
opencode_lock = threading.Lock()

active_pollers:      dict[str, "SessionPoller"] = {}
pending_permissions: dict[str, int] = {}
pending_questions:   dict[str, dict] = {}
event_stop = threading.Event()
event_thread: Optional[threading.Thread] = None

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


def get_session_options(sid: str) -> dict:
    cfg = load_config()
    options = dict(cfg.get("session_options", {}).get(sid, {}))
    for key in ("model_provider", "model_id", "variant", "agent", "system", "no_reply", "tools"):
        if key not in options and key in cfg:
            options[key] = cfg[key]
    return options


def save_session_options(sid: str, changes: dict) -> None:
    cfg = load_config()
    all_options = cfg.setdefault("session_options", {})
    options = all_options.setdefault(sid, get_session_options(sid))
    options.update(changes)
    save_config(cfg)

def _load_opencode_enabled() -> bool:
    try:
        return load_config().get("opencode_enabled", True)
    except Exception:
        return True

def _save_opencode_enabled(val: bool) -> None:
    try:
        cfg = load_config()
        cfg["opencode_enabled"] = val
        save_config(cfg)
    except Exception:
        pass

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
    global opencode_proc, opencode_enabled, opencode_last_error
    with opencode_lock:
        opencode_enabled = True
        _save_opencode_enabled(True)

        if _is_server_alive():
            pid = opencode_proc.pid if opencode_proc and opencode_proc.poll() is None else "externe"
            log.info("opencode déjà actif (pid=%s)", pid)
            return True

        log.info("Démarrage opencode serve sur le port %d avec %s…", OPENCODE_PORT, OPENCODE_BIN)
        try:
            # The child inherits systemd stdout/stderr so startup errors remain diagnosable.
            opencode_proc = subprocess.Popen(
                [
                    OPENCODE_BIN,
                    "serve",
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    str(OPENCODE_PORT),
                    "--print-logs",
                    "--log-level",
                    "INFO",
                ],
                cwd=_server_cwd(),
            )
        except OSError as exc:
            opencode_last_error = str(exc)
            log.exception("Impossible de lancer opencode")
            return False

        for _ in range(30):
            time.sleep(0.5)
            if _is_server_alive():
                log.info("opencode prêt (pid=%d).", opencode_proc.pid)
                opencode_last_error = ""
                return True
            if opencode_proc.poll() is not None:
                opencode_last_error = f"opencode s'est arrêté (code {opencode_proc.returncode})"
                log.error(opencode_last_error)
                opencode_proc = None
                return False

        opencode_last_error = "opencode n'a pas répondu dans les délais"
        log.error(opencode_last_error)
        return False

def stop_opencode(*, persist_disabled: bool = True) -> None:
    global opencode_proc, opencode_enabled
    if persist_disabled:
        opencode_enabled = False
        _save_opencode_enabled(False)
    if opencode_proc:
        opencode_proc.terminate()
        try:
            opencode_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            opencode_proc.kill()
        opencode_proc = None
    log.info(
        "opencode arrêté %s.",
        "par l'utilisateur" if persist_disabled else "pour l'arrêt du bot",
    )

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
    cfg = get_session_options(sid)
    payload: dict = {"parts": [{"type": "text", "text": text}]}
    if cfg.get("model_provider") and cfg.get("model_id"):
        payload["model"] = {"providerID": cfg["model_provider"], "modelID": cfg["model_id"]}
    if cfg.get("variant"): payload["variant"] = cfg["variant"]
    if cfg.get("agent"):   payload["agent"]   = cfg["agent"]
    if cfg.get("system"):   payload["system"]   = cfg["system"]
    if cfg.get("no_reply"): payload["noReply"] = True
    if isinstance(cfg.get("tools"), dict): payload["tools"] = cfg["tools"]
    
    try:
        r = _api("POST", f"/session/{sid}/prompt_async", json=payload)
        if r.status_code in (200, 204):
            return True
        log.error("send_prompt failed: %d %s", r.status_code, r.text)
    except Exception as e:
        log.error("send_prompt error: %s", e)
    return False


def session_action(sid: str, action: str, payload: Optional[dict] = None) -> bool:
    try:
        r = _api("POST", f"/session/{sid}/{action}", json=payload or {})
        if r.status_code in (200, 204):
            return True
        log.error("session %s failed: %d %s", action, r.status_code, r.text[:500])
    except Exception as e:
        log.error("session %s error: %s", action, e)
    return False


def fork_session(sid: str, message_id: Optional[str] = None) -> Optional[str]:
    try:
        payload = {"messageID": message_id} if message_id else {}
        r = _api("POST", f"/session/{sid}/fork", json=payload)
        if r.status_code == 200:
            data = r.json()
            return data.get("id") if isinstance(data, dict) else None
    except Exception as e:
        log.error("fork session error: %s", e)
    return None


def set_active_session(sid: str) -> None:
    cfg = load_config()
    cfg["session_id"] = sid
    cfg["continue_session"] = True
    save_config(cfg)


def current_session_messages(sid: str, limit: int = 10) -> list[dict]:
    return get_messages(sid)[-limit:]


def resolve_message_id(sid: str, prefix: str) -> Optional[str]:
    for message in get_messages(sid):
        info = message.get("info", {}) if isinstance(message, dict) else {}
        message_id = info.get("id", "")
        if message_id.startswith(prefix):
            return message_id
    return None

def _get_msg_role(m: dict) -> str:
    if isinstance(m, dict):
        if 'role' in m and m['role']:
            return m['role']
        if 'info' in m and isinstance(m['info'], dict):
            return m['info'].get('role', '')
    return ''

def get_messages(sid: str) -> list[dict]:
    try:
        r = _api("GET", f"/session/{sid}/message")
        if r.status_code == 200:
            payload = r.json() or []
            # OpenCode 1.18 returns {info, parts}; older versions returned
            # message objects directly. Keep both formats usable.
            return payload if isinstance(payload, list) else payload.get("messages", [])
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


def get_provider_catalog() -> list[dict]:
    """Read the installed OpenCode providers instead of maintaining a model list."""
    for path in ("/provider", "/api/provider"):
        try:
            r = _api("GET", path)
            if r.status_code != 200:
                continue
            data = r.json() or {}
            providers = data.get("providers", data) if isinstance(data, dict) else data
            if isinstance(providers, dict):
                providers = [dict(value, id=key) for key, value in providers.items()]
            if isinstance(providers, list):
                return [p for p in providers if isinstance(p, dict)]
        except Exception as e:
            log.debug("provider catalog unavailable at %s: %s", path, e)
    return []


def model_options() -> list[dict]:
    options = []
    for provider in get_provider_catalog():
        provider_id = provider.get("id") or provider.get("providerID") or provider.get("name", "")
        models = provider.get("models", {})
        if isinstance(models, dict):
            models = [dict(value, id=key) for key, value in models.items()]
        for model in models if isinstance(models, list) else []:
            model_id = model.get("id") or model.get("modelID")
            if provider_id and model_id:
                options.append({"provider": provider_id, "id": model_id, "name": model.get("name", model_id), "variants": model.get("variants", {})})
    if options:
        return options
    return [{"provider": p, "id": m, "name": key, "variants": {}} for key, (p, m) in FREE_MODELS.items()]


def get_tool_catalog(sid: str) -> list[str]:
    options = get_session_options(sid)
    provider = options.get("model_provider", "")
    model = options.get("model_id", "")
    try:
        r = _api("GET", "/experimental/tool", params={"provider": provider, "model": model})
        if r.status_code == 200:
            data = r.json() or {}
            tools = data.get("tools", data) if isinstance(data, dict) else data
            if isinstance(tools, dict):
                return list(tools)
            if isinstance(tools, list):
                return [str(t.get("id", t.get("name", t))) if isinstance(t, dict) else str(t) for t in tools]
    except Exception as e:
        log.error("get_tool_catalog error: %s", e)
    try:
        r = _api("GET", "/experimental/tool/ids")
        if r.status_code == 200:
            data = r.json() or {}
            tools = data.get("tools", data.get("ids", data)) if isinstance(data, dict) else data
            if isinstance(tools, list):
                return [str(t) for t in tools]
    except Exception as e:
        log.error("get_tool_ids error: %s", e)
    return []

def reply_permission(pid: str, reply: str) -> bool:
    try:
        r = _api("POST", f"/permission/{pid}/reply", json={"reply": reply})
        return r.status_code == 200
    except Exception as e:
        log.error("reply_permission error : %s", e)
        return False


def get_questions(sid: str) -> list[dict]:
    try:
        r = _api("GET", f"/session/{sid}/question")
        if r.status_code == 200:
            data = r.json() or {}
            questions = data.get("data", data) if isinstance(data, dict) else data
            return questions if isinstance(questions, list) else []
    except Exception as e:
        log.error("get_questions error: %s", e)
    return []


def reply_question(sid: str, request_id: str, answers: list[list[str]]) -> bool:
    try:
        r = _api("POST", f"/session/{sid}/question/{request_id}/reply", json={"answers": answers})
        return r.status_code in (200, 204)
    except Exception as e:
        log.error("reply_question error: %s", e)
        return False


def reject_question(sid: str, request_id: str) -> bool:
    try:
        r = _api("POST", f"/session/{sid}/question/{request_id}/reject", json={})
        return r.status_code in (200, 204)
    except Exception as e:
        log.error("reject_question error: %s", e)
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
    def __init__(self, sid: str, chat_id: int, status_msg_id: int, bot, started_at_ms: int):
        self.sid           = sid
        self.chat_id       = chat_id
        self.status_msg_id = status_msg_id
        self.bot           = bot
        self.started_at_ms = started_at_ms
        self._stop         = False
        self._seen_perms:  set[str] = set()
        self._seen_questions: set[str] = set()
        self._done_tools:  set[str] = set()
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while not self._stop:
            self._wake.wait(1.5)
            self._wake.clear()
            try:
                self._check_questions()
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

    def _check_questions(self) -> None:
        for request in get_questions(self.sid):
            request_id = request.get("id") or request.get("requestID")
            if not request_id or request_id in self._seen_questions:
                continue
            self._seen_questions.add(request_id)
            questions = request.get("questions", request.get("question", []))
            if isinstance(questions, dict):
                questions = [questions]
            if not isinstance(questions, list):
                continue
            pending_questions[request_id] = {"sid": self.sid, "chat_id": self.chat_id, "questions": questions}
            self._schedule(self._send_question_request(request_id, questions))

    def _check_messages(self) -> bool:
        msgs = get_messages(self.sid)
        if not msgs: return False

        last = next(
            (
                m for m in reversed(msgs)
                if _get_msg_role(m) == "assistant"
                and m.get("info", {}).get("time", {}).get("created", 0) >= self.started_at_ms
            ),
            None,
        )
        if not last: return False

        parts = last.get("parts", []) or []
        tool_lines = []
        for p in parts:
            if p.get("type") != "tool": continue
            pid    = p.get("id", "")
            state  = p.get("state", {})
            status = state.get("status", "")
            tool_input = state.get("input", {}) if isinstance(state.get("input", {}), dict) else {}
            title  = state.get("title") or tool_input.get("description", "") or p.get("tool", "tool")
            input_preview = json.dumps(tool_input, ensure_ascii=False, separators=(",", ":"))[:300]
            
            if status in ("completed", "error") and pid not in self._done_tools:
                self._done_tools.add(pid)
                if status == "completed":
                    out = state.get("output") or state.get("metadata", {}).get("output") or "OK"
                    tool_lines.append(f"🔧 <b>{html.escape(str(title))}</b>\n<code>{html.escape(input_preview)}</code>\n→ {html.escape(str(out).strip()[:100])}")
                else:
                    err = state.get("error", "erreur")
                    tool_lines.append(f"❌ <b>{html.escape(str(title))}</b>\n<code>{html.escape(input_preview)}</code>\n→ {html.escape(str(err)[:100])}")

        if tool_lines:
            self._schedule(self._edit_status("⚙️ <i>Opencode travaille…</i>\n\n" + "\n".join(tool_lines[-5:])))

        reasoning = "\n".join(
            p.get("text", "") for p in parts
            if p.get("type") == "reasoning" and p.get("text")
        ).strip()
        if reasoning and not tool_lines:
            self._schedule(self._edit_status(
                "🧠 <i>Raisonnement en cours…</i>\n\n" + html.escape(reasoning[-2500:])
            ))

        final_text = "\n".join(
            p.get("text", "")
            for p in parts
            if p.get("type") == "text" and p.get("text")
        ).strip()
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

    async def _send_question_request(self, request_id: str, questions: list[dict]) -> None:
        for number, question in enumerate(questions):
            prompt = question.get("question") or question.get("text") or "Choisis une option"
            options = question.get("options", [])
            kb = []
            for index, option in enumerate(options):
                label = option.get("label", option) if isinstance(option, dict) else str(option)
                kb.append([InlineKeyboardButton(str(label)[:55], callback_data=f"question_{request_id[:22]}_{number}_{index}")])
            kb.append([InlineKeyboardButton("❌ Refuser", callback_data=f"question_reject_{request_id[:22]}")])
            try:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=f"❓ <b>Question OpenCode</b>\n\n{html.escape(str(prompt))}",
                    parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb),
                )
            except Exception as e:
                log.error("send_question_request error: %s", e)


def _event_session_id(value) -> Optional[str]:
    if isinstance(value, dict):
        for key in ("sessionID", "sessionId", "session_id"):
            if isinstance(value.get(key), str) and value[key].startswith("ses"):
                return value[key]
        for child in value.values():
            found = _event_session_id(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _event_session_id(child)
            if found:
                return found
    return None


def _event_watcher() -> None:
    """Wake the matching session immediately when OpenCode emits an SSE event."""
    while not event_stop.is_set():
        try:
            with requests.get(f"{BASE_URL}/event", stream=True, timeout=(5, None)) as response:
                response.raise_for_status()
                for raw in response.iter_lines(decode_unicode=True):
                    if event_stop.is_set():
                        return
                    if not raw or not raw.startswith("data:"):
                        continue
                    try:
                        event = json.loads(raw[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    sid = _event_session_id(event)
                    if sid in active_pollers:
                        active_pollers[sid].wake()
        except Exception as e:
            log.debug("OpenCode event stream indisponible: %s", e)
            event_stop.wait(3)


def start_event_watcher() -> None:
    global event_thread
    if event_thread and event_thread.is_alive():
        return
    event_stop.clear()
    event_thread = threading.Thread(target=_event_watcher, name="opencode-events", daemon=True)
    event_thread.start()

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
    active_sid = cfg.get("session_id", "")
    session_cfg = get_session_options(active_sid) if active_sid else cfg
    srv = "🟢 Allumé" if _is_server_alive() else "🔴 Éteint"
    var_name = session_cfg.get("variant", "défaut") or "défaut"
    text = (
        f"🤖 <b>Assistant Opencode</b>\n\n"
        f"Serveur  : {srv}\n"
        f"Session  : <code>{active_sid or 'aucune'}</code>\n"
        f"Modèle   : <code>{session_cfg.get('model_provider', '')}/{session_cfg.get('model_id', '')}</code>\n"
        f"Variante : <code>{var_name}</code>\n\n"
        "<b>Contrôle :</b>\n"
        "/opencode_start – allumer opencode\n"
        "/opencode_stop – éteindre opencode\n"
        "/permissions – mode permission bash\n"
        "/models – changer de modèle\n"
        "/tools – contrôler les outils de la session\n"
        "/session – gérer les sessions\n"
        "/session_control – contrôler la session active\n"
        "/new – nouvelle session\n"
        "/compact /fork – actions explicites sur la session\n"
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
    detail = f"\n<code>{opencode_last_error}</code>" if opencode_last_error else ""
    await msg.edit_text(
        "🟢 opencode est allumé et prêt." if ok else f"❌ Échec du démarrage.{detail}",
        parse_mode="HTML",
    )

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
    sid = load_config().get("session_id", "")
    cfg = get_session_options(sid) if sid else load_config()
    cur = f"{cfg['model_provider']}/{cfg['model_id']}"
    options = model_options()
    ctx.user_data["model_options"] = options
    kb = []
    for index, option in enumerate(options[:40]):
        label = option["name"]
        if f"{option['provider']}/{option['id']}" == cur:
            label += " ✓"
        kb.append([InlineKeyboardButton(label[:55], callback_data=f"modi_{index}")])
    kb.append([InlineKeyboardButton("❌ Fermer", callback_data="close_msg")])
    await upd.message.reply_text(f"Sélectionne le modèle OpenCode :\nActuel : <code>{cur}</code>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


async def cmd_tools(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _req_admin(upd): return
    sid = load_config().get("session_id", "")
    if not sid:
        await upd.message.reply_text("❌ Aucune session active.")
        return
    tools = get_tool_catalog(sid)
    if not tools:
        await upd.message.reply_text("Aucun catalogue d'outils disponible pour ce modèle.")
        return
    options = get_session_options(sid)
    enabled = options.get("tools", {}) if isinstance(options.get("tools"), dict) else {}
    ctx.user_data["tool_options"] = tools
    kb = []
    for index, tool in enumerate(tools[:60]):
        state = enabled.get(tool, True)
        kb.append([InlineKeyboardButton(f"{'✅' if state else '❌'} {tool}"[:55], callback_data=f"tool_{index}")])
    kb.append([InlineKeyboardButton("✅ Activer tout", callback_data="tools_all_on"), InlineKeyboardButton("❌ Désactiver tout", callback_data="tools_all_off")])
    kb.append([InlineKeyboardButton("🔄 Actualiser", callback_data="tools_refresh"), InlineKeyboardButton("❌ Fermer", callback_data="close_msg")])
    await upd.message.reply_text("🧰 <b>Outils de la session active</b>\n\nChaque bouton contrôle la valeur <code>tools</code> envoyée à OpenCode.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))

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


def _build_session_control_ui() -> tuple[str, list]:
    sid = load_config().get("session_id", "")
    if not sid:
        return "Aucune session active.", [[InlineKeyboardButton("➕ Nouvelle session", callback_data="sess_new")]]
    messages = current_session_messages(sid, 5)
    text = f"🎛 <b>Session active</b>\n<code>{sid}</code>\nMessages : {len(get_messages(sid))}\n\n"
    if messages:
        text += "<b>Derniers messages :</b>\n"
        for message in messages:
            info = message.get("info", {})
            role = info.get("role", "?")
            parts = message.get("parts", [])
            body = " ".join(p.get("text", "") for p in parts if p.get("type") == "text")
            text += f"• {role}: {body[:90] or '[outil]'}\n"
    kb = [
        [InlineKeyboardButton("➕ Nouvelle", callback_data="sess_new"), InlineKeyboardButton("🔀 Fork", callback_data="sess_fork")],
        [InlineKeyboardButton("🧠 Compacter", callback_data="sess_compact"), InlineKeyboardButton("📝 Résumer", callback_data="sess_summarize")],
        [InlineKeyboardButton("🛑 Abandonner", callback_data="sess_abort"), InlineKeyboardButton("🔄 Actualiser", callback_data="sess_refresh")],
        [InlineKeyboardButton("💬 Options des messages", callback_data="sess_messages")],
        [InlineKeyboardButton("🧰 Outils de la session", callback_data="sess_tools")],
        [InlineKeyboardButton("📋 Toutes les sessions", callback_data="sess_list")],
        [InlineKeyboardButton("❌ Fermer", callback_data="close_msg")],
    ]
    return text, kb


def _build_message_control_ui(sid: str) -> tuple[str, list]:
    messages = current_session_messages(sid, 8)
    if not messages:
        return "Aucun message dans cette session.", [[InlineKeyboardButton("⬅️ Session", callback_data="sess_refresh")]]
    lines, kb = [], []
    for message in messages:
        info = message.get("info", {})
        message_id = info.get("id", "")
        role = info.get("role", "?")
        parts = message.get("parts", [])
        body = " ".join(p.get("text", "") for p in parts if p.get("type") == "text")
        label = f"{role}: {(body or '[outil]')[:45]}"
        lines.append(f"• <code>{message_id[:16]}</code> {label}")
        if message_id:
            prefix = message_id[:22]
            kb.append([
                InlineKeyboardButton("🔀 Fork", callback_data=f"msg_fork_{prefix}"),
                InlineKeyboardButton("↩️ Revenir", callback_data=f"msg_revert_{prefix}"),
            ])
    kb.append([InlineKeyboardButton("⬅️ Session", callback_data="sess_refresh")])
    return "💬 <b>Messages de la session</b>\n\n" + "\n".join(lines), kb

async def cmd_session(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _req_admin(upd): return
    text, kb = _build_session_list_ui()
    await upd.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb) if kb else None)


async def cmd_session_control(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _req_admin(upd): return
    text, kb = _build_session_control_ui()
    await upd.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


async def cmd_compact(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _req_admin(upd): return
    sid = load_config().get("session_id", "")
    ok = bool(sid) and session_action(sid, "compact")
    await upd.message.reply_text("✅ Session compactée." if ok else "❌ Compactage impossible.")


async def cmd_fork(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _req_admin(upd): return
    sid = load_config().get("session_id", "")
    new_sid = fork_session(sid) if sid else None
    if new_sid:
        set_active_session(new_sid)
        await upd.message.reply_text(f"✅ Fork créé et sélectionné : <code>{new_sid}</code>", parse_mode="HTML")
    else:
        await upd.message.reply_text("❌ Fork impossible.")

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
        r = subprocess.run([OPENCODE_BIN, "version"], capture_output=True, text=True, timeout=10)
        await upd.message.reply_text(f"📦 <b>Version opencode</b>\n<pre>{r.stdout.strip()}</pre>", parse_mode="HTML")
    except Exception as e:
        await upd.message.reply_text(f"❌ Erreur : {e}")

async def cmd_stats(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        r = subprocess.run([OPENCODE_BIN, "stats"], capture_output=True, text=True, timeout=10)
        await upd.message.reply_text(f"📊 <b>Statistiques opencode</b>\n<pre>{r.stdout.strip()[:3500]}</pre>", parse_mode="HTML")
    except Exception as e:
        await upd.message.reply_text(f"❌ Erreur : {e}")

async def cmd_upgrade(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _req_admin(upd): return
    msg = await upd.message.reply_text("⏳ Mise à jour opencode…")
    stop_opencode(persist_disabled=False)
    try:
        r = subprocess.run([OPENCODE_BIN, "upgrade"], capture_output=True, text=True, timeout=120)
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

    try:
        sid = get_or_create_session()
    except Exception as e:
        await upd.message.reply_text(f"❌ Erreur serveur: {e}")
        return

    if sid in active_pollers: active_pollers.pop(sid).stop()
    status_msg = await upd.message.reply_text("🤔 <i>Opencode réfléchit…</i>", parse_mode="HTML")

    started_at_ms = int(time.time() * 1000)
    if not send_prompt(sid, text):
        await status_msg.edit_text("❌ opencode n'a pas répondu.")
        return

    active_pollers[sid] = SessionPoller(
        sid, upd.effective_chat.id, status_msg.message_id, ctx.application.bot, started_at_ms
    )

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

    if data.startswith("question_"):
        if not is_admin(q.from_user.id):
            await q.message.edit_text("⛔ Réservé à l'admin.")
            return
        rest = data[len("question_"):]
        if rest.startswith("reject_"):
            prefix = rest[len("reject_"):]
            request_id, entry = next(((key, value) for key, value in pending_questions.items() if key.startswith(prefix)), (None, None))
            ok = bool(entry) and reject_question(entry["sid"], request_id)
            if request_id: pending_questions.pop(request_id, None)
            await q.message.edit_text("❌ Question refusée." if ok else "❌ Question introuvable.")
            return
        try:
            prefix, number, option_index = rest.rsplit("_", 2)
            number = int(number)
            option_index = int(option_index)
        except ValueError:
            await q.message.edit_text("❌ Réponse invalide.")
            return
        request_id, entry = next(((key, value) for key, value in pending_questions.items() if key.startswith(prefix)), (None, None))
        if not entry:
            await q.message.edit_text("❌ Question expirée.")
            return
        questions = entry["questions"]
        try:
            options = questions[number].get("options", [])
            choice = options[option_index]
            answer = choice.get("label", choice) if isinstance(choice, dict) else str(choice)
            answers = [[] for _ in questions]
            answers[number] = [str(answer)]
        except (IndexError, AttributeError, TypeError):
            await q.message.edit_text("❌ Option invalide.")
            return
        ok = reply_question(entry["sid"], request_id, answers)
        if ok: pending_questions.pop(request_id, None)
        await q.message.edit_text("✅ Réponse envoyée à OpenCode." if ok else "❌ OpenCode a refusé la réponse.")
        return

    if data.startswith("tool_") or data.startswith("tools_"):
        if not is_admin(q.from_user.id):
            await q.message.edit_text("⛔ Réservé à l'admin.")
            return
        sid = load_config().get("session_id", "")
        tools = get_tool_catalog(sid) if sid else []
        if not sid or not tools:
            await q.message.edit_text("❌ Catalogue d'outils indisponible.")
            return
        options = get_session_options(sid)
        enabled = dict(options.get("tools", {})) if isinstance(options.get("tools"), dict) else {tool: True for tool in tools}
        if data.startswith("tool_"):
            try:
                index = int(data[5:])
                tool = tools[index]
            except (ValueError, IndexError):
                await q.message.edit_text("❌ Outil introuvable. Actualise la liste.")
                return
            enabled[tool] = not enabled.get(tool, True)
        elif data == "tools_all_on":
            enabled = {tool: True for tool in tools}
        elif data == "tools_all_off":
            enabled = {tool: False for tool in tools}
        save_session_options(sid, {"tools": enabled})
        if data == "tools_refresh":
            pass
        kb = []
        for index, tool in enumerate(tools[:60]):
            kb.append([InlineKeyboardButton(f"{'✅' if enabled.get(tool, True) else '❌'} {tool}"[:55], callback_data=f"tool_{index}")])
        kb.append([InlineKeyboardButton("✅ Activer tout", callback_data="tools_all_on"), InlineKeyboardButton("❌ Désactiver tout", callback_data="tools_all_off")])
        kb.append([InlineKeyboardButton("🔄 Actualiser", callback_data="tools_refresh"), InlineKeyboardButton("❌ Fermer", callback_data="close_msg")])
        await q.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("perm_"):
        _, action, pid = data.split("_", 2)
        ok = reply_permission(pid, action)
        await q.message.edit_text({"once": "✅ Autorisé", "always": "✅✅ Toujours", "reject": "❌ Refusé"}.get(action, "Fait") if ok else "❌ Erreur de réponse.")
        if ok: pending_permissions.pop(pid, None)
        return

    if data.startswith("modi_"):
        if not is_admin(q.from_user.id):
            await q.message.edit_text("⛔ Réservé à l'admin.")
            return
        try:
            option = ctx.user_data.get("model_options", [])[int(data[5:])]
        except (ValueError, IndexError, TypeError):
            await q.message.edit_text("❌ Modèle introuvable. Relance /models.")
            return
        variants = option.get("variants", {})
        if isinstance(variants, dict):
            variant_names = list(variants)
        elif isinstance(variants, list):
            variant_names = [str(v) for v in variants]
        else:
            variant_names = []
        if not variant_names:
            sid = load_config().get("session_id", "")
            if not sid:
                await q.message.edit_text("❌ Aucune session active.")
                return
            save_session_options(sid, {"model_provider": option["provider"], "model_id": option["id"], "variant": ""})
            await q.message.edit_text(f"✅ Modèle : <code>{option['provider']}/{option['id']}</code>", parse_mode="HTML")
            return
        ctx.user_data["selected_model"] = option
        ctx.user_data["model_variants"] = variant_names
        kb = [[InlineKeyboardButton(str(v)[:55], callback_data=f"vari_{i}")] for i, v in enumerate(variant_names[:20])]
        kb.append([InlineKeyboardButton("⬅️ Retour", callback_data="mod_back")])
        await q.message.edit_text(
            f"Modèle : <code>{option['provider']}/{option['id']}</code>\nSélectionne sa variante / son effort :",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    if data.startswith("vari_"):
        if not is_admin(q.from_user.id):
            await q.message.edit_text("⛔ Réservé à l'admin.")
            return
        try:
            option = ctx.user_data["selected_model"]
            variant = ctx.user_data["model_variants"][int(data[5:])]
        except (KeyError, ValueError, IndexError, TypeError):
            await q.message.edit_text("❌ Variante introuvable. Relance /models.")
            return
        sid = load_config().get("session_id", "")
        if not sid:
            await q.message.edit_text("❌ Aucune session active.")
            return
        save_session_options(sid, {"model_provider": option["provider"], "model_id": option["id"], "variant": variant})
        await q.message.edit_text(
            f"✅ Modèle : <code>{option['provider']}/{option['id']}</code>\nEffort/variante : <code>{variant}</code>",
            parse_mode="HTML",
        )
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
        sid = load_config().get("session_id", "")
        cfg = get_session_options(sid) if sid else load_config()
        cur = f"{cfg['model_provider']}/{cfg['model_id']}"
        options = model_options()
        ctx.user_data["model_options"] = options
        kb = []
        for index, option in enumerate(options[:40]):
            label = option["name"] + (" ✓" if f"{option['provider']}/{option['id']}" == cur else "")
            kb.append([InlineKeyboardButton(label[:55], callback_data=f"modi_{index}")])
        kb.append([InlineKeyboardButton("❌ Fermer", callback_data="close_msg")])
        await q.message.edit_text("Sélectionne le modèle :", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("sess_"):
        if not is_admin(q.from_user.id):
            await q.message.edit_text("⛔ Réservé à l'admin.")
            return
        action = data[5:]
        sid = load_config().get("session_id", "")
        if action == "new":
            if sid in active_pollers: active_pollers.pop(sid).stop()
            sid = _create_session()
            await q.message.edit_text(f"✅ Nouvelle session active : <code>{sid}</code>", parse_mode="HTML")
            return
        if action == "list":
            text, kb = _build_session_list_ui()
            await q.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb) if kb else None)
            return
        if action == "refresh":
            text, kb = _build_session_control_ui()
            await q.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
            return
        if not sid:
            await q.message.edit_text("❌ Aucune session active.")
            return
        if action == "messages":
            text, kb = _build_message_control_ui(sid)
            await q.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
            return
        if action == "tools":
            tools = get_tool_catalog(sid)
            if not tools:
                await q.message.edit_text("❌ Catalogue d'outils indisponible.")
                return
            options = get_session_options(sid)
            enabled = options.get("tools", {}) if isinstance(options.get("tools"), dict) else {}
            ctx.user_data["tool_options"] = tools
            kb = [[InlineKeyboardButton(f"{'✅' if enabled.get(tool, True) else '❌'} {tool}"[:55], callback_data=f"tool_{i}")] for i, tool in enumerate(tools[:60])]
            kb.append([InlineKeyboardButton("✅ Activer tout", callback_data="tools_all_on"), InlineKeyboardButton("❌ Désactiver tout", callback_data="tools_all_off")])
            kb.append([InlineKeyboardButton("⬅️ Session", callback_data="sess_refresh")])
            await q.message.edit_text("🧰 <b>Outils de la session active</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
            return
        if action in {"compact", "summarize", "abort"}:
            if action == "abort" and sid in active_pollers:
                active_pollers.pop(sid).stop()
            ok = session_action(sid, action)
            await q.message.edit_text(
                {"compact": "✅ Session compactée.", "summarize": "✅ Session résumée.", "abort": "🛑 Requête abandonnée."}[action]
                if ok else "❌ Action refusée par OpenCode."
            )
            return
        if action == "fork":
            new_sid = fork_session(sid)
            if new_sid:
                set_active_session(new_sid)
                await q.message.edit_text(f"✅ Fork créé et sélectionné : <code>{new_sid}</code>", parse_mode="HTML")
            else:
                await q.message.edit_text("❌ Fork impossible.")
            return

    if data.startswith("msg_fork_") or data.startswith("msg_revert_"):
        if not is_admin(q.from_user.id):
            await q.message.edit_text("⛔ Réservé à l'admin.")
            return
        sid = load_config().get("session_id", "")
        prefix = data.split("_", 2)[2]
        message_id = resolve_message_id(sid, prefix) if sid else None
        if not message_id:
            await q.message.edit_text("❌ Message introuvable. Actualise la liste.")
            return
        if data.startswith("msg_fork_"):
            new_sid = fork_session(sid, message_id)
            if new_sid:
                set_active_session(new_sid)
                await q.message.edit_text(f"✅ Fork depuis le message créé : <code>{new_sid}</code>", parse_mode="HTML")
            else:
                await q.message.edit_text("❌ Fork impossible.")
            return
        ok = session_action(sid, "revert", {"messageID": message_id})
        await q.message.edit_text("✅ Message révoqué et état restauré." if ok else "❌ Révocation impossible.")
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
    start_event_watcher()
    
    await app.bot.set_my_commands([
        BotCommand("start",          "Menu principal"),
        BotCommand("opencode_start", "Allumer opencode"),
        BotCommand("opencode_stop",  "Éteindre opencode"),
        BotCommand("permissions",    "Mode de permission bash"),
        BotCommand("models",         "Choisir le modèle"),
        BotCommand("tools",          "Contrôler les outils"),
        BotCommand("session",        "Gérer les sessions"),
        BotCommand("session_control", "Contrôler la session active"),
        BotCommand("new",            "Nouvelle session"),
        BotCommand("compact",        "Compacter la session"),
        BotCommand("fork",            "Fork de la session"),
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
    # A service restart must not turn a manually enabled OpenCode server off forever.
    event_stop.set()
    stop_opencode(persist_disabled=False)

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
    app.add_handler(CommandHandler("tools",          cmd_tools))
    app.add_handler(CommandHandler("session",        cmd_session))
    app.add_handler(CommandHandler("session_control", cmd_session_control))
    app.add_handler(CommandHandler("new",            cmd_new))
    app.add_handler(CommandHandler("compact",         cmd_compact))
    app.add_handler(CommandHandler("fork",            cmd_fork))
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

# Charger l'etat persiste d'opencode (apres la definition de load_config)
try:
    opencode_enabled = _load_opencode_enabled()
except Exception:
    pass

if __name__ == "__main__":
    main()
