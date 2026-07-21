import os, re, json, logging, subprocess, uuid, time, sqlite3
from pathlib import Path

from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO, style="{", format="{asctime} [{levelname}] {message}")
log = logging.getLogger(__name__)

ENV_FILE = Path(__file__).parent.resolve() / ".env_bot"
CONFIG_FILE = Path("/home/ubuntu/.assistant_config.json")
OPENCODE_DB = Path.home() / ".local/share/opencode/opencode.db"
AUTH_JSON = Path.home() / ".local/share/opencode/auth.json"

DEFAULT_CONFIG = {
    "model": "opencode/deepseek-v4-flash-free",
    "variant": "",
    "agent": "",
    "continue_session": True,
    "thinking": False,
    "session_id": "",
    "free_models": {},
}

FREE_MODELS = {
    "deepseek": "opencode/deepseek-v4-flash-free",
    "mimo": "opencode/mimo-v2.5-free",
    "nemotron": "opencode/nemotron-3-ultra-free",
    "north": "opencode/north-mini-code-free",
    "bigpickle": "opencode/big-pickle",
}

AGENTS = {
    "build": "build",
    "plan": "plan",
    "explore": "explore",
    "general": "general",
}

COMMON_PROVIDERS = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google Gemini",
    "groq": "Groq",
    "mistral": "Mistral",
    "cohere": "Cohere",
    "deepseek": "DeepSeek",
    "openrouter": "OpenRouter",
    "together": "Together AI",
    "xai": "xAI (Grok)",
    "github": "GitHub Models",
    "custom": "Custom Provider",
}

# ---------- state keys ----------
STATE_ADD_PROVIDER = "add_provider"
STATE_ADD_PROVIDER_KEY = "add_provider_key"
STATE_ADD_PROVIDER_CONFIRM = "add_provider_confirm"
STATE_ADD_MCP = "add_mcp"
STATE_INSTALL_PLUGIN = "install_plugin"

# ---------- config ----------
def load_config():
    if CONFIG_FILE.exists():
        cfg = json.loads(CONFIG_FILE.read_text())
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    return dict(DEFAULT_CONFIG)

def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

def get_token():
    t = os.getenv("TELEGRAM_BOT_TOKEN")
    if t:
        return t
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().strip().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                if k.strip() == "TELEGRAM_BOT_TOKEN":
                    return v.strip().strip("\"'")
    return None

# ---------- shell ----------
def strip_ansi(s):
    s = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', s)
    s = re.sub(r'[▄▀│┌┐└┘├┤┬┴┼═║╒╓╔╕╖╗╘╙╚╛╜╝╞╟╠╡╢╣╤╥╦╧╨╩╪╫╬━┃┏┓┗┛┣┳┫┻▔▁]', '', s)
    return '\n'.join(line.strip() for line in s.split('\n') if line.strip())

def run(cmd, timeout=10, cwd="/home/ubuntu"):
    try:
        r = subprocess.run(cmd if isinstance(cmd, list) else cmd.split(),
                           cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or r.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return "TIMEOUT"
    except FileNotFoundError:
        return "FILE_NOT_FOUND"
    except Exception as e:
        return f"ERROR: {e}"

def run_shell(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or r.stderr or "").strip()
    except:
        return ""

# ---------- opencode runner ----------
def opencode_run(prompt, timeout=120):
    cfg = load_config()
    cwd = "/home/ubuntu"
    cmd = ["opencode", "run"]

    sid = cfg.get("session_id", "")
    if sid:
        cmd.extend(["-s", sid])
    elif cfg["continue_session"]:
        cmd.append("--continue")

    model = cfg.get("model", DEFAULT_CONFIG["model"])
    cmd.extend(["-m", model])

    variant = cfg.get("variant", "")
    if variant:
        cmd.extend(["--variant", variant])

    agent = cfg.get("agent", "")
    if agent:
        cmd.extend(["--agent", agent])

    if cfg.get("thinking", False):
        cmd.append("--thinking")

    cmd.append(prompt)

    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        raw = r.stdout or r.stderr or ""
        result = strip_ansi(raw)
        if len(result) > 4000:
            result = result[:4000] + "\n... (tronque)"
        return result or "Aucun resultat"
    except subprocess.TimeoutExpired:
        return "Timeout: opencode a pris trop de temps"
    except FileNotFoundError:
        return "Erreur: opencode n'est pas installe"
    except Exception as e:
        return f"Erreur: {e}"

# ---------- session helpers ----------
def parse_sessions(raw):
    sessions = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line.startswith("ses_"):
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        sid, rest = parts
        rest = re.sub(r"\s{2,}\d+:\d+\s[AP]M.*$", "", rest)
        sessions.append((sid, rest.strip()))
    return sessions

# ---------- credential / provider DB helpers ----------
def write_credential(integration_id, value, label="api_key", method_id="api_key"):
    cid = str(uuid.uuid4())
    now = int(time.time() * 1000)
    try:
        with sqlite3.connect(str(OPENCODE_DB)) as db:
            db.execute(
                "INSERT INTO credential (id, integration_id, label, value, method_id, active, time_created, time_updated) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (cid, integration_id, label, value, method_id, now, now)
            )
            db.commit()
    except Exception as e:
        log.error("write_credential failed: %s", e)

def read_credentials():
    creds = {}
    try:
        with sqlite3.connect(str(OPENCODE_DB)) as db:
            rows = db.execute(
                "SELECT integration_id, label, value FROM credential WHERE active=1"
            ).fetchall()
            for provider, label, value in rows:
                if provider not in creds:
                    creds[provider] = {}
                creds[provider][label] = value
    except Exception as e:
        log.error("read_credentials failed: %s", e)
    return creds

def get_free_models_config():
    cfg = load_config()
    fm = cfg.get("free_models", {})
    return {**FREE_MODELS, **fm}

# ---------- command handlers ----------
async def cmd_start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    m = cfg["model"]
    a = cfg.get("agent") or "defaut"
    v = cfg.get("variant") or "defaut"
    await upd.message.reply_text(
        "Assistant Bot + Opencode\n\n"
        "Envoie un message pour le passer a opencode.\n\n"
        f"Modele: `{m}`\n"
        f"Variant: `{v}`\n"
        f"Agent: `{a}`\n"
        f"Session continue: {'oui' if cfg['continue_session'] else 'non'}\n\n"
        "`/models` - Choisir modele\n"
        "`/variant` - Choisir variant\n"
        "`/agent` - Choisir agent\n"
        "`/session` - Gerer sessions\n"
        "`/providers` - Gerer fournisseurs\n"
        "`/mcp` - Gerer serveurs MCP\n"
        "`/config` - Voir config\n"
        "`/thinking` - Basculer raisonnement",
        parse_mode="Markdown"
    )

async def cmd_models(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    fm = get_free_models_config()
    kb = [[InlineKeyboardButton("🎯 Default", callback_data="mod_sel_")]]
    for key in sorted(fm):
        name = fm[key]
        mark = " ✓" if name == cfg["model"] else ""
        kb.append([InlineKeyboardButton(f"{key}{mark}", callback_data=f"mod_sel_{key}")])
    await upd.message.reply_text(
        f"Modele: **{cfg['model']}**\nChoisis un modele :",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
    )

async def cmd_variant(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    variants = {"Default": "", "High": "high", "Max": "max", "Minimal": "minimal"}
    kb = []
    for label, val in variants.items():
        mark = " ✓" if cfg.get("variant", "") == val else ""
        kb.append([InlineKeyboardButton(f"{label}{mark}", callback_data=f"var_sel_{val or 'none'}")])
    await upd.message.reply_text(
        f"Variant: **{cfg['variant'] or 'default'}**\nChoisis un variant :",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
    )

async def cmd_agent(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    kb = [[InlineKeyboardButton("🎯 Default", callback_data="agt_sel_none")]]
    for key in AGENTS:
        val = AGENTS[key]
        mark = " ✓" if cfg.get("agent", "") == val else ""
        kb.append([InlineKeyboardButton(f"{key}{mark}", callback_data=f"agt_sel_{key}")])
    await upd.message.reply_text(
        f"Agent: **{cfg.get('agent') or 'default'}**\nChoisis un agent :",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
    )

async def cmd_thinking(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    cfg["thinking"] = not cfg.get("thinking", False)
    save_config(cfg)
    s = "active" if cfg["thinking"] else "desactive"
    await upd.message.reply_text(f"Affichage du raisonnement: {s}")

async def cmd_config(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    await upd.message.reply_text(
        "**Configuration**\n\n"
        f"Modele: `{cfg['model']}`\n"
        f"Variant: `{cfg.get('variant') or 'default'}`\n"
        f"Agent: `{cfg.get('agent') or 'default'}`\n"
        f"Session continue: {'oui' if cfg['continue_session'] else 'non'}\n"
        f"Thinking: {'oui' if cfg.get('thinking', False) else 'non'}\n"
        f"Session ID: `{cfg.get('session_id', '') or 'aucune'}`",
        parse_mode="Markdown"
    )

# ---------- session commands ----------
async def cmd_session(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    sid = cfg.get("session_id", "")
    info = f"\nActive: `{sid}`" if sid else ""
    await upd.message.reply_text(
        f"Session continue: {'oui' if cfg['continue_session'] else 'non'}{info}\n\n"
        "`/session_new` - Nouvelle session\n"
        "`/session_list` - Naviguer les sessions\n"
        "`/session_switch <num>` - Changer\n"
        "`/session_delete <num>` - Supprimer\n"
        "`/share` - Partager session",
        parse_mode="Markdown"
    )

async def cmd_continue_on(upd, ctx):
    cfg = load_config()
    cfg["continue_session"] = True
    cfg["session_id"] = ""
    save_config(cfg)
    await upd.message.reply_text("Session continue activee (derniere session)")

async def cmd_continue_off(upd, ctx):
    cfg = load_config()
    cfg["continue_session"] = False
    cfg["session_id"] = ""
    save_config(cfg)
    await upd.message.reply_text("Session unique (nouvelle a chaque message)")

async def cmd_session_new(upd, ctx):
    cfg = load_config()
    cfg["continue_session"] = True
    cfg["session_id"] = ""
    save_config(cfg)
    await upd.message.reply_text("Nouvelle session fraiche au prochain message.")

async def cmd_session_switch(upd, ctx):
    args = ctx.args
    if not args:
        await upd.message.reply_text("Usage: /session_switch <num>")
        return
    raw = run("opencode session list 2>&1", timeout=10)
    sessions = parse_sessions(raw)
    if not sessions:
        await upd.message.reply_text("Aucune session trouvee.")
        return
    try:
        i = int(args[0]) - 1
        if i < 0 or i >= len(sessions):
            await upd.message.reply_text(f"Index invalide. Sessions: 1-{len(sessions)}")
            return
        sid, title = sessions[i]
    except ValueError:
        await upd.message.reply_text("Usage: /session_switch <num>")
        return
    cfg = load_config()
    cfg["session_id"] = sid
    cfg["continue_session"] = True
    save_config(cfg)
    await upd.message.reply_text(f"Session changee: `{sid}`", parse_mode="Markdown")

async def cmd_session_delete(upd, ctx):
    args = ctx.args
    cfg = load_config()
    if not args:
        sid = cfg.get("session_id", "")
        if not sid:
            await upd.message.reply_text("Usage: /session_delete <num>")
            return
    else:
        raw = run("opencode session list 2>&1", timeout=10)
        sessions = parse_sessions(raw)
        if not sessions:
            await upd.message.reply_text("Aucune session trouvee.")
            return
        try:
            i = int(args[0]) - 1
            if i < 0 or i >= len(sessions):
                await upd.message.reply_text(f"Index invalide. Sessions: 1-{len(sessions)}")
                return
            sid, _ = sessions[i]
        except ValueError:
            await upd.message.reply_text("Usage: /session_delete <num>")
            return
    result = run(f"opencode session delete {sid}", timeout=15)
    cleaned = strip_ansi(result) if result else "Supprimee"
    if cfg.get("session_id") == sid:
        cfg["session_id"] = ""
        save_config(cfg)
    await upd.message.reply_text(f"Session {sid}: {cleaned[:1000]}")

async def cmd_session_list(upd, ctx):
    raw = run("opencode session list 2>&1", timeout=10)
    sessions = parse_sessions(raw)
    if not sessions:
        await upd.message.reply_text("Aucune session trouvee.")
        return
    ctx.user_data["session_list"] = sessions
    msg = await upd.message.reply_text("Chargement...")
    await show_session_card(msg, ctx, 0)

async def show_session_card(msg, ctx, idx):
    sessions = ctx.user_data.get("session_list", [])
    if idx < 0 or idx >= len(sessions):
        return
    sid, title = sessions[idx]
    cfg = load_config()
    active = cfg.get("session_id", "")
    kb = []
    row = []
    if idx > 0:
        row.append(InlineKeyboardButton("◀ Precedent", callback_data=f"sl_nav_{idx-1}"))
    if idx < len(sessions) - 1:
        row.append(InlineKeyboardButton("Suivant ▶", callback_data=f"sl_nav_{idx+1}"))
    if row:
        kb.append(row)
    kb.append([
        InlineKeyboardButton("🗑 Supprimer", callback_data=f"sl_delete_{idx}"),
        InlineKeyboardButton("▶ Reprendre", callback_data=f"sl_switch_{idx}"),
    ])
    marker = " ◀ Active" if sid == active else ""
    card = (
        f"**Session {idx+1}/{len(sessions)}**{marker}\n\n"
        f"`{title[:200]}`\n\n"
        f"ID: `{sid}`"
    )
    await msg.edit_text(card, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def session_list_callback(upd, ctx):
    q = upd.callback_query
    await q.answer()
    data = q.data
    idx = int(data.split("_")[-1])
    sessions = ctx.user_data.get("session_list", [])
    if idx < 0 or idx >= len(sessions):
        return
    sid, title = sessions[idx]
    if data.startswith("sl_nav"):
        await show_session_card(q.message, ctx, idx)
    elif data.startswith("sl_delete"):
        result = run(f"opencode session delete {sid}", timeout=15)
        cleaned = strip_ansi(result) if result else "Supprimee"
        del sessions[idx]
        ctx.user_data["session_list"] = sessions
        if not sessions:
            await q.message.edit_text("Toutes les sessions supprimees.", reply_markup=None)
            return
        new_idx = min(idx, len(sessions) - 1)
        await show_session_card(q.message, ctx, new_idx)
    elif data.startswith("sl_switch"):
        cfg = load_config()
        cfg["session_id"] = sid
        cfg["continue_session"] = True
        save_config(cfg)
        await q.message.edit_text(
            f"Session reprise :\n\n`{title[:200]}`\n\nID: `{sid}`",
            parse_mode="Markdown"
        )

async def cmd_share(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    sid = cfg.get("session_id", "")
    if not sid:
        await upd.message.reply_text("Aucune session a partager. Lance d'abord une conversation.")
        return
    await upd.message.reply_text("Generation du lien de partage...")
    raw = run(f"opencode run --share -s {sid}", timeout=15)
    if "TIMEOUT" in raw:
        await upd.message.reply_text("Timeout lors du partage.")
        return
    # The output contains the share URL
    cleaned = strip_ansi(raw)
    await upd.message.reply_text(f"Session partagee:\n\n`{cleaned[:3000]}`", parse_mode="Markdown")

# ---------- providers ----------
async def cmd_providers(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    creds = read_credentials()
    raw = run("opencode providers list 2>&1", timeout=10)
    kb = []
    providers_raw = strip_ansi(raw) if raw else ""
    lines = providers_raw.split("\n")
    has_creds = False
    for line in lines:
        if "|" in line or not line.strip():
            continue
        ls = line.strip()
        if ls and not ls.startswith("┌") and not ls.startswith("└") and not ls.startswith("│"):
            has_creds = True
    if creds:
        for provider in sorted(creds):
            kb.append([InlineKeyboardButton(f"🔑 {provider}", callback_data=f"prov_view_{provider}")])
    # If providers list shows credentials, also check those
    # For now, always show known providers from DB
    kb.append([InlineKeyboardButton("➕ Add Provider", callback_data="prov_add_")])
    text = "**Fournisseurs configures:**\n\n" if creds else "**Aucun fournisseur configure.**\n\n"
    text += "Ajoute un fournisseur pour utiliser ses modeles."
    await upd.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def provider_callback(upd, ctx):
    q = upd.callback_query
    await q.answer()
    data = q.data
    cfg = load_config()

    if data == "prov_add_":
        kb = []
        for pid, pname in COMMON_PROVIDERS.items():
            kb.append([InlineKeyboardButton(pname, callback_data=f"prov_pick_{pid}")])
        await q.message.edit_text(
            "Choisis un fournisseur a ajouter :",
            reply_markup=InlineKeyboardMarkup(kb)
        )

    elif data.startswith("prov_pick_"):
        provider = data.split("prov_pick_", 1)[1]
        if provider == "custom":
            ctx.user_data[STATE_ADD_PROVIDER] = True
            await q.message.edit_text("Envoie le nom du fournisseur (ex: openai, anthropic, groq...)")
        else:
            ctx.user_data[STATE_ADD_PROVIDER] = provider
            await q.message.edit_text(
                f"Envoie ta cle API pour **{provider}** :\n\n"
                "_(ou /cancel pour annuler)_",
                parse_mode="Markdown"
            )

    elif data.startswith("prov_view_"):
        provider = data.split("prov_view_", 1)[1]
        creds = read_credentials()
        if provider not in creds:
            await q.message.edit_text(f"Aucune credential trouvee pour {provider}.")
            return
        info = "\n".join(f"`{k}`: `{v[:20]}...`" for k, v in creds[provider].items())
        kb = [
            [InlineKeyboardButton("🔄 Test / Lister modeles", callback_data=f"prov_test_{provider}")],
            [InlineKeyboardButton("🗑 Supprimer", callback_data=f"prov_rm_{provider}")],
            [InlineKeyboardButton("⬅ Retour", callback_data="prov_back_")],
        ]
        await q.message.edit_text(
            f"**{provider}**\n\n{info}",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
        )

    elif data.startswith("prov_test_"):
        provider = data.split("prov_test_", 1)[1]
        await q.message.edit_text(f"Test de {provider}... (liste les modeles)")
        raw = run(f"opencode models {provider} 2>&1", timeout=15)
        cleaned = strip_ansi(raw) if raw else "Aucun modele trouve"
        if "TIMEOUT" in cleaned:
            await q.message.edit_text(f"Timeout pour {provider}. Verifie la cle.")
            return
        models = [l.strip() for l in cleaned.split("\n") if l.strip() and not l.strip().startswith("┌") and not l.strip().startswith("└") and not l.strip().startswith("│") and "/" in l]
        if not models:
            await q.message.edit_text(f"Aucun modele trouve pour {provider}.\n`{cleaned[:500]}`", parse_mode="Markdown")
            return
        kb = []
        for m in models[:20]:
            fm = get_free_models_config()
            mark = " ✓" if any(fm[k] == m for k in fm) else ""
            short = m.split("/")[-1][:20]
            kb.append([InlineKeyboardButton(f"{short}{mark}", callback_data=f"prov_addmod_{provider}|{m}")])
        kb.append([InlineKeyboardButton("⬅ Retour", callback_data=f"prov_view_{provider}")])
        await q.message.edit_text(
            f"**{provider}** - `{len(models)}` modeles\n\nChoisis un modele a ajouter aux favoris :",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
        )

    elif data.startswith("prov_addmod_"):
        rest = data.split("prov_addmod_", 1)[1]
        provider, model = rest.split("|", 1)
        fm = load_config().get("free_models", {})
        key = model.split("/")[-1].replace("-", "_").replace(".", "_")
        fm[key] = model
        cfg = load_config()
        cfg["free_models"] = fm
        save_config(cfg)
        await q.message.edit_text(
            f"Modele ajoute aux favoris :\n`{model}`\n\nUtilise `/models` pour le selectionner.",
            parse_mode="Markdown"
        )

    elif data.startswith("prov_rm_"):
        provider = data.split("prov_rm_", 1)[1]
        try:
            with sqlite3.connect(str(OPENCODE_DB)) as db:
                db.execute("DELETE FROM credential WHERE integration_id=?", (provider,))
                db.commit()
        except Exception as e:
            log.error("delete credential failed: %s", e)
        await q.message.edit_text(f"Credentials supprimees pour {provider}.")

    elif data == "prov_done_":
        await q.message.edit_text("Ajout de fournisseur termine.")

    elif data == "prov_back_":
        await cmd_providers(upd, ctx)

# ---------- MCP ----------
async def cmd_mcp(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = run("opencode mcp list 2>&1", timeout=10)
    cleaned = strip_ansi(raw) if raw else "Aucun serveur MCP"
    # Parse MCP servers
    servers = []
    for line in cleaned.split("\n"):
        ls = line.strip()
        if ls and not ls.startswith("┌") and not ls.startswith("└") and not ls.startswith("│") and not ls.startswith("▲"):
            servers.append(ls)
    kb = []
    for s in servers[:20]:
        kb.append([InlineKeyboardButton(f"🖥 {s[:30]}", callback_data=f"mcp_view_{s.split()[0]}")])
    kb.append([InlineKeyboardButton("➕ Add MCP", callback_data="mcp_add_")])
    await upd.message.reply_text(
        f"**Serveurs MCP:**\n\n`{cleaned[:500]}`",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
    )

async def mcp_callback(upd, ctx):
    q = upd.callback_query
    await q.answer()
    data = q.data

    if data == "mcp_add_":
        ctx.user_data[STATE_ADD_MCP] = True
        await q.message.edit_text(
            "Envoie le nom du serveur MCP a ajouter.\n\n"
            "_(il sera ajoute avec `opencode mcp add <nom>`, puis configure-le dans opencode)_\n"
            "/cancel pour annuler"
        )
    elif data.startswith("mcp_view_"):
        name = data.split("mcp_view_", 1)[1]
        kb = [
            [InlineKeyboardButton("🔐 Auth", callback_data=f"mcp_auth_{name}")],
            [InlineKeyboardButton("🚪 Logout", callback_data=f"mcp_logout_{name}")],
            [InlineKeyboardButton("🐛 Debug", callback_data=f"mcp_debug_{name}")],
            [InlineKeyboardButton("⬅ Retour", callback_data="mcp_back_")],
        ]
        await q.message.edit_text(
            f"**MCP: {name}**\n\nChoisis une action :",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
        )
    elif data.startswith("mcp_auth_"):
        name = data.split("mcp_auth_", 1)[1]
        await q.message.edit_text(f"Auth pour {name}...")
        raw = run(f"opencode mcp auth {name} 2>&1", timeout=30)
        cleaned = strip_ansi(raw) if raw else "Fait"
        await q.message.edit_text(f"MCP auth {name}:\n\n`{cleaned[:2000]}`", parse_mode="Markdown")
    elif data.startswith("mcp_logout_"):
        name = data.split("mcp_logout_", 1)[1]
        await q.message.edit_text(f"Logout MCP {name}...")
        raw = run(f"opencode mcp logout {name} 2>&1", timeout=15)
        cleaned = strip_ansi(raw) if raw else "Fait"
        await q.message.edit_text(f"MCP logout {name}:\n\n`{cleaned[:2000]}`", parse_mode="Markdown")
    elif data.startswith("mcp_debug_"):
        name = data.split("mcp_debug_", 1)[1]
        await q.message.edit_text(f"Debug MCP {name}...")
        raw = run(f"opencode mcp debug {name} 2>&1", timeout=20)
        cleaned = strip_ansi(raw) if raw else "Pas de debug info"
        await q.message.edit_text(f"MCP debug {name}:\n\n`{cleaned[:3000]}`", parse_mode="Markdown")
    elif data == "mcp_back_":
        await cmd_mcp(upd, ctx)

# ---------- plugins ----------
async def cmd_plugins(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data[STATE_INSTALL_PLUGIN] = True
    await upd.message.reply_text(
        "Envoie le nom du module npm a installer.\n"
        "Exemple: `opencode-plugin-example`\n\n"
        "_(installera avec `opencode plugin <module>`)_\n"
        "/cancel pour annuler"
    )

# ---------- other commands ----------
async def cmd_stats(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await upd.message.reply_text("Stats opencode...")
    raw = run("opencode stats --days 7 2>&1", timeout=15)
    cleaned = strip_ansi(raw) if raw else "Aucune stats"
    await upd.message.reply_text(f"Stats (7 jours):\n\n{cleaned[:3500]}")

async def cmd_export(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    sid = cfg.get("session_id", "")
    if not sid:
        await upd.message.reply_text("Aucune session en cours. Utilise /session_list.")
        return
    await upd.message.reply_text(f"Export session `{sid}`...", parse_mode="Markdown")
    raw = run(f"opencode export {sid} 2>&1", timeout=15)
    cleaned = strip_ansi(raw) if raw else "Erreur export"
    if len(cleaned) > 3500:
        cleaned = cleaned[:3500] + "\n... (tronque)"
    await upd.message.reply_text(f"Export:\n\n{cleaned}")

async def cmd_import(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await upd.message.reply_text("Usage: /import <fichier_json_ou_url>")
        return
    await upd.message.reply_text("Import session...")
    raw = run(f"opencode import {args[0]} 2>&1", timeout=15)
    cleaned = strip_ansi(raw) if raw else "Importe"
    await upd.message.reply_text(cleaned[:3500])

async def cmd_list_models(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = run("opencode models 2>&1", timeout=10)
    cleaned = strip_ansi(raw) if raw else "Aucun modele"
    await upd.message.reply_text(f"Modeles disponibles:\n\n{cleaned[:3500]}")

async def cmd_version(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = run("opencode --version 2>&1", timeout=5)
    await upd.message.reply_text(raw or "opencode")

async def cmd_upgrade(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await upd.message.reply_text("Mise a jour opencode...")
    raw = run("opencode upgrade 2>&1", timeout=120)
    cleaned = strip_ansi(raw) if raw else "Fait"
    await upd.message.reply_text(cleaned[:3500])

async def cmd_serve(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await upd.message.reply_text("Demarrage serveur opencode...")
    run("opencode serve --port 4096 &>/dev/null & disown", timeout=3)
    await upd.message.reply_text("Serveur demarre sur http://localhost:4096")

async def cmd_fork(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    sid = cfg.get("session_id", "")
    if not sid:
        await upd.message.reply_text("Aucune session a fork.")
        return
    cfg["session_id"] = ""
    cfg["continue_session"] = True
    save_config(cfg)
    await upd.message.reply_text(
        "Session forkee au prochain message.\n"
        "La session originale est preservee."
    )

async def cmd_github(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = run("opencode github 2>&1", timeout=15)
    cleaned = strip_ansi(raw) if raw else "GitHub: voir https://github.com"
    await upd.message.reply_text(cleaned[:3500])

async def cmd_debug(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = run("opencode debug 2>&1", timeout=15)
    cleaned = strip_ansi(raw) if raw else "Debug opencode"
    await upd.message.reply_text(cleaned[:3500])

async def cmd_cancel(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    for key in [STATE_ADD_PROVIDER, STATE_ADD_PROVIDER_KEY, STATE_ADD_PROVIDER_CONFIRM, STATE_ADD_MCP, STATE_INSTALL_PLUGIN]:
        ctx.user_data.pop(key, None)
    await upd.message.reply_text("Action annulee.")

# ---------- message handler (state machine + chat) ----------
async def message_handler(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = upd.message.text.strip()
    if not msg:
        return

    # ---- state: add provider (provider name) ----
    if ctx.user_data.get(STATE_ADD_PROVIDER) is True:
        provider = msg.strip().lower().replace(" ", "")
        if not provider:
            await upd.message.reply_text("Nom invalide.")
            return
        ctx.user_data[STATE_ADD_PROVIDER] = provider
        await upd.message.reply_text(
            f"Envoie ta cle API pour **{provider}** :\n\n"
            "_(/cancel pour annuler)_",
            parse_mode="Markdown"
        )
        return

    # ---- state: add provider (API key) ----
    if isinstance(ctx.user_data.get(STATE_ADD_PROVIDER), str) and ctx.user_data.get(STATE_ADD_PROVIDER_KEY) is None:
        provider = ctx.user_data[STATE_ADD_PROVIDER]
        key = msg.strip()
        if not key or len(key) < 8:
            await upd.message.reply_text("Cle API trop courte. Reessaie ou /cancel.")
            return
        ctx.user_data[STATE_ADD_PROVIDER_KEY] = key
        # Write to DB
        write_credential(provider, key)
        await upd.message.reply_text(
            f"Cle ajoutee pour **{provider}** !\n\n"
            "Test de connexion en cours...",
            parse_mode="Markdown"
        )
        # Test by listing models
        raw = run(f"opencode models {provider} 2>&1", timeout=15)
        cleaned = strip_ansi(raw) if raw else ""
        if "TIMEOUT" in cleaned:
            await upd.message.reply_text(
                f"⚠ Timeout pour {provider}. La cle est peut-etre invalide.\n"
                "Tu peux reessayer avec /cancel puis /providers."
            )
        else:
            models = [l.strip() for l in cleaned.split("\n") if l.strip() and "/" in l and not l.strip().startswith("┌") and not l.strip().startswith("└") and not l.strip().startswith("│")]
            if models:
                kb = []
                for m in models[:15]:
                    fm = get_free_models_config()
                    mark = " ✓" if any(fm[k] == m for k in fm) else ""
                    short = m.split("/")[-1][:20]
                    kb.append([InlineKeyboardButton(f"{short}{mark}", callback_data=f"prov_addmod_{provider}|{m}")])
                kb.append([InlineKeyboardButton("Skip", callback_data="prov_done_")])
                await upd.message.reply_text(
                    f"**{provider}** - `{len(models)}` modeles\n\n"
                    "Choisis un modele a ajouter aux favoris :",
                    reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
                )
            else:
                await upd.message.reply_text(
                    f"Cle ajoutee pour {provider}, mais aucun modele trouve.\n"
                    f"Sortie: `{cleaned[:500]}`",
                    parse_mode="Markdown"
                )
        # Clean up state
        ctx.user_data.pop(STATE_ADD_PROVIDER, None)
        ctx.user_data.pop(STATE_ADD_PROVIDER_KEY, None)
        return

    # ---- state: add MCP ----
    if ctx.user_data.get(STATE_ADD_MCP):
        name = msg.strip()
        if not name:
            await upd.message.reply_text("Nom invalide.")
            return
        ctx.user_data.pop(STATE_ADD_MCP, None)
        await upd.message.reply_text(f"Ajout du serveur MCP `{name}`...", parse_mode="Markdown")
        raw = run(f"opencode mcp add {name} 2>&1", timeout=20)
        cleaned = strip_ansi(raw) if raw else "Ajoute"
        await upd.message.reply_text(f"MCP add `{name}`:\n\n`{cleaned[:2000]}`", parse_mode="Markdown")
        return

    # ---- state: install plugin ----
    if ctx.user_data.get(STATE_INSTALL_PLUGIN):
        module = msg.strip()
        if not module:
            await upd.message.reply_text("Nom de module invalide.")
            return
        ctx.user_data.pop(STATE_INSTALL_PLUGIN, None)
        await upd.message.reply_text(f"Installation du plugin `{module}`...", parse_mode="Markdown")
        raw = run(f"opencode plugin {module} 2>&1", timeout=60)
        cleaned = strip_ansi(raw) if raw else "Installe"
        await upd.message.reply_text(f"Plugin `{module}`:\n\n`{cleaned[:2000]}`", parse_mode="Markdown")
        return

    # ---- default: send to opencode ----
    status = await upd.message.reply_text("Opencode reflechit...")
    result = opencode_run(msg)
    await status.edit_text(result)

# ---------- model/variant/agent callbacks ----------
async def config_callbacks(upd, ctx):
    q = upd.callback_query
    await q.answer()
    data = q.data
    cfg = load_config()
    fm = get_free_models_config()

    if data.startswith("mod_sel_"):
        key = data.replace("mod_sel_", "")
        if not key:
            cfg["model"] = DEFAULT_CONFIG["model"]
            save_config(cfg)
            await q.message.edit_text(f"Modele: `{cfg['model']}`", parse_mode="Markdown")
            return
        full = fm.get(key)
        if not full:
            await q.message.edit_text(f"Modele `{key}` introuvable.", parse_mode="Markdown")
            return
        cfg["model"] = full
        save_config(cfg)
        variants = {"Default": "", "High": "high", "Max": "max", "Minimal": "minimal"}
        kb = [[InlineKeyboardButton(f"{l}", callback_data=f"var_sel_{v or 'none'}") for l, v in variants.items()]]
        await q.message.edit_text(
            f"Modele: **{full}**\n\nVariant :",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
        )

    elif data.startswith("var_sel_"):
        val = data.replace("var_sel_none", "").replace("var_sel_", "")
        cfg["variant"] = val
        save_config(cfg)
        await q.message.edit_text(f"Variant: **{val or 'default'}**", parse_mode="Markdown")

    elif data.startswith("agt_sel_"):
        key = data.replace("agt_sel_", "")
        cfg["agent"] = AGENTS.get(key, "")
        save_config(cfg)
        await q.message.edit_text(f"Agent: **{cfg.get('agent') or 'default'}**", parse_mode="Markdown")

# ---------- post_init ----------
async def post_init(app: Application):
    cmds = [
        BotCommand("start", "Demarrer"),
        BotCommand("models", "Choisir modele"),
        BotCommand("variant", "Choisir variant"),
        BotCommand("agent", "Choisir agent"),
        BotCommand("session", "Gerer sessions"),
        BotCommand("session_new", "Nouvelle session"),
        BotCommand("session_list", "Naviguer sessions"),
        BotCommand("session_switch", "Changer session"),
        BotCommand("session_delete", "Supprimer session"),
        BotCommand("continue_on", "Session continue ON"),
        BotCommand("continue_off", "Session continue OFF"),
        BotCommand("share", "Partager session"),
        BotCommand("thinking", "Raisonnement on/off"),
        BotCommand("config", "Configuration"),
        BotCommand("providers", "Fournisseurs AI"),
        BotCommand("mcp", "Serveurs MCP"),
        BotCommand("plugins", "Installer plugin"),
        BotCommand("stats", "Stats 7 jours"),
        BotCommand("export", "Exporter session"),
        BotCommand("import", "Importer session"),
        BotCommand("fork", "Fork session"),
        BotCommand("serve", "Serveur headless"),
        BotCommand("list_models", "Tous modeles dispo"),
        BotCommand("version", "Version opencode"),
        BotCommand("upgrade", "Mise a jour"),
        BotCommand("github", "GitHub agent"),
        BotCommand("debug", "Debug"),
        BotCommand("cancel", "Annuler action"),
    ]
    await app.bot.set_my_commands(cmds)
    log.info("Assistant bot pret")

# ---------- main ----------
def main():
    token = get_token()
    if not token:
        log.error("Token manquant")
        return
    app = Application.builder().token(token).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("models", cmd_models))
    app.add_handler(CommandHandler("variant", cmd_variant))
    app.add_handler(CommandHandler("agent", cmd_agent))
    app.add_handler(CommandHandler("session", cmd_session))
    app.add_handler(CommandHandler("continue_on", cmd_continue_on))
    app.add_handler(CommandHandler("continue_off", cmd_continue_off))
    app.add_handler(CommandHandler("session_new", cmd_session_new))
    app.add_handler(CommandHandler("session_list", cmd_session_list))
    app.add_handler(CommandHandler("session_switch", cmd_session_switch))
    app.add_handler(CommandHandler("session_delete", cmd_session_delete))
    app.add_handler(CommandHandler("thinking", cmd_thinking))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("import", cmd_import))
    app.add_handler(CommandHandler("list_models", cmd_list_models))
    app.add_handler(CommandHandler("version", cmd_version))
    app.add_handler(CommandHandler("upgrade", cmd_upgrade))
    app.add_handler(CommandHandler("providers", cmd_providers))
    app.add_handler(CommandHandler("mcp", cmd_mcp))
    app.add_handler(CommandHandler("plugins", cmd_plugins))
    app.add_handler(CommandHandler("serve", cmd_serve))
    app.add_handler(CommandHandler("fork", cmd_fork))
    app.add_handler(CommandHandler("share", cmd_share))
    app.add_handler(CommandHandler("github", cmd_github))
    app.add_handler(CommandHandler("debug", cmd_debug))
    app.add_handler(CommandHandler("config", cmd_config))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    app.add_handler(CallbackQueryHandler(config_callbacks, pattern="^(mod_|var_|agt_)"))
    app.add_handler(CallbackQueryHandler(provider_callback, pattern="^prov_"))
    app.add_handler(CallbackQueryHandler(mcp_callback, pattern="^mcp_"))
    app.add_handler(CallbackQueryHandler(session_list_callback, pattern="^sl_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    log.info("Assistant bot demarre...")
    app.run_polling()

if __name__ == "__main__":
    main()
