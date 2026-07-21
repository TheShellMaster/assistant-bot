import os, re, json, logging, subprocess
from pathlib import Path

from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO, style="{", format="{asctime} [{levelname}] {message}")
log = logging.getLogger(__name__)

ENV_FILE = Path(__file__).parent.resolve() / ".env_bot"
CONFIG_FILE = Path("/home/ubuntu/.assistant_config.json")

DEFAULT_CONFIG = {
    "model": "opencode/deepseek-v4-flash-free",
    "variant": "",
    "agent": "",
    "continue_session": True,
    "thinking": False,
    "session_id": "",
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

def strip_ansi(s):
    s = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', s)
    s = re.sub(r'[▄▀│┌┐└┘├┤┬┴┼═║╒╓╔╕╖╗╘╙╚╛╜╝╞╟╠╡╢╣╤╥╦╧╨╩╪╫╬━┃┏┓┗┛┣┳┫┻▔▁]', '', s)
    s = re.sub(r'> build · [^\n]*', '', s)
    s = re.sub(r'timestamp=.*?level=\w+ run=\w+ message=.*', '', s)
    return '\n'.join(line.strip() for line in s.split('\n') if line.strip())

def opencode_run(prompt, timeout=120):
    cfg = load_config()
    cwd = "/home/ubuntu"
    cmd = ["opencode", "run"]

    sid = cfg.get("session_id", "")
    if sid:
        cmd.extend(["-s", sid, "--continue"])
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

def parse_sessions(raw):
    sessions = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line.startswith("ses_"):
            continue
        sid, rest = line.split(None, 1)
        rest = re.sub(r"\s{2,}\d+:\d+\s[AP]M.*$", "", rest)
        sessions.append((sid, rest.strip()))
    return sessions

def get_session_by_index(idx):
    raw = run_shell("opencode session list 2>&1", timeout=10)
    sessions = parse_sessions(raw)
    if not sessions:
        return None, "Aucune session trouvee"
    try:
        i = int(idx) - 1
        if i < 0 or i >= len(sessions):
            return None, f"Index invalide. Sessions: 1-{len(sessions)}"
        return sessions[i][0], None
    except ValueError:
        if any(s[0] == idx for s in sessions):
            return idx, None
        return None, "ID session invalide"

def run_shell(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or r.stderr or "").strip()
    except:
        return ""

async def cmd_start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    await upd.message.reply_text(
        "Assistant Bot + Opencode\n\n"
        "Envoie un message, je le passe a opencode.\n\n"
        f"Modele: {cfg['model']}\n"
        f"Agent: {cfg.get('agent') or 'aucun'}\n"
        f"Session continue: {'oui' if cfg['continue_session'] else 'non'}\n"
        f"Variant: {cfg['variant'] or 'defaut'}\n\n"
        "Tape / pour voir toutes les commandes disponibles."
    )

async def cmd_model(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        known = ", ".join(FREE_MODELS.keys())
        cfg = load_config()
        await upd.message.reply_text(
            f"Modele actuel: {cfg['model']}\n\n"
            f"Disponibles: {known}\n"
            "Usage: /model deepseek\n"
            "Ou utiliser /model_deepseek directement."
        )
        return
    name = args[0].lower()
    if name in FREE_MODELS:
        cfg = load_config()
        cfg["model"] = FREE_MODELS[name]
        save_config(cfg)
        await upd.message.reply_text(f"Modele change: {FREE_MODELS[name]}")
    else:
        known = ", ".join(FREE_MODELS.keys())
        await upd.message.reply_text(f"Modele inconnu. Disponibles: {known}")

async def cmd_variant(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        cfg = load_config()
        await upd.message.reply_text(
            f"Variant actuel: {cfg['variant'] or 'aucun'}\n\n"
            "Usage: /variant high (ou /variant_high)")
        return
    val = args[0].lower()
    cfg = load_config()
    if val == "none":
        cfg["variant"] = ""
        save_config(cfg)
        await upd.message.reply_text("Variant desactive (defaut)")
    elif val in ("high", "max", "minimal"):
        cfg["variant"] = val
        save_config(cfg)
        await upd.message.reply_text(f"Variant change: {val}")
    else:
        await upd.message.reply_text("Variants: high, max, minimal, none")

async def cmd_agent(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    cfg = load_config()
    if not args:
        await upd.message.reply_text(
            f"Agent actuel: {cfg.get('agent') or 'aucun (defaut)'}\n\n"
            "Agents disponibles:\n"
            "/agent_plan - Mode planification\n"
            "/agent_build - Mode developpement\n"
            "/agent_explore - Exploration (lecture seule)\n"
            "/agent_general - Agent general\n"
            "/agent_none - Agent par defaut"
        )
        return
    name = args[0].lower()
    cfg = load_config()
    if name == "none":
        cfg["agent"] = ""
        save_config(cfg)
        await upd.message.reply_text("Agent desactive (mode defaut)")
    elif name in AGENTS:
        cfg["agent"] = AGENTS[name]
        save_config(cfg)
        await upd.message.reply_text(f"Agent change: {name}")
    else:
        await upd.message.reply_text(f"Agents: {', '.join(AGENTS.keys())}, none")

async def cmd_session(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    cfg = load_config()
    if not args:
        sid = cfg.get("session_id", "")
        sid_info = f"\nID: {sid}" if sid else ""
        await upd.message.reply_text(
            f"Session continue: {'oui' if cfg['continue_session'] else 'non'}{sid_info}\n\n"
            "/continue_on - Activer session continue\n"
            "/continue_off - Desactiver\n"
            "/session_new - Nouvelle session\n"
            "/session_list - Naviguer avec boutons ◀ ▶\n"
            "/session_switch <num> - Changer de session\n"
            "/session_delete <num> - Supprimer une session"
        )
        return
    action = args[0].lower()
    if action == "on":
        cfg["continue_session"] = True
        cfg["session_id"] = ""
        save_config(cfg)
        await upd.message.reply_text("Session continue activee (derniere session)")
    elif action == "off":
        cfg["continue_session"] = False
        cfg["session_id"] = ""
        save_config(cfg)
        await upd.message.reply_text("Mode session unique (nouvelle a chaque message)")
    elif action == "new":
        cfg["continue_session"] = True
        cfg["session_id"] = ""
        save_config(cfg)
        await upd.message.reply_text("Nouvelle session fraiche au prochain message.")
    elif action == "list":
        raw = run_shell("opencode session list 2>&1", timeout=10)
        sessions = parse_sessions(raw)
        if not sessions:
            await upd.message.reply_text("Aucune session trouvee.")
            return
        ctx.user_data["session_list"] = sessions
        msg = await upd.message.reply_text("Chargement...")
        await show_session_card(msg, ctx, 0)
    elif action == "switch":
        if len(args) < 2:
            await upd.message.reply_text("Usage: /session switch <num>")
            return
        sid, err = get_session_by_index(args[1])
        if err:
            await upd.message.reply_text(err)
            return
        cfg["session_id"] = sid
        cfg["continue_session"] = True
        save_config(cfg)
        await upd.message.reply_text(f"Session changee: {sid}")
    elif action == "delete":
        if len(args) < 2:
            sid = cfg.get("session_id", "")
            if not sid:
                await upd.message.reply_text("Usage: /session delete <num>")
                return
        sid = args[1] if len(args) >= 2 else ""
        if sid:
            sid, err = get_session_by_index(sid)
            if err:
                await upd.message.reply_text(err)
                return
        result = run_shell(f"opencode session delete {sid} 2>&1")
        cleaned = strip_ansi(result) if result else "Supprimee"
        if cfg.get("session_id") == sid:
            cfg["session_id"] = ""
            save_config(cfg)
        await upd.message.reply_text(f"Session {sid}: {cleaned}")
    else:
        await upd.message.reply_text("Usage: /session on|off|new|list|switch|delete")

async def cmd_config(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    agent = cfg.get("agent", "")
    await upd.message.reply_text(
        "Configuration:\n\n"
        f"Modele: {cfg['model']}\n"
        f"Variant: {cfg['variant'] or 'defaut'}\n"
        f"Agent: {agent or 'defaut'}\n"
        f"Session continue: {'oui' if cfg['continue_session'] else 'non'}\n"
        f"Thinking: {'oui' if cfg.get('thinking', False) else 'non'}"
    )

async def cmd_thinking(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    cfg["thinking"] = not cfg.get("thinking", False)
    save_config(cfg)
    await upd.message.reply_text(f"Affichage du raisonnement: {'active' if cfg['thinking'] else 'desactive'}")

async def cmd_stats(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await upd.message.reply_text("Stats opencode...")
    raw = run_shell("opencode stats --days 7 2>&1", timeout=15)
    cleaned = strip_ansi(raw) if raw else "Aucune stats"
    await upd.message.reply_text(f"Stats (7 jours):\n\n{cleaned[:3500]}")

async def cmd_export(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    sid = cfg.get("session_id", "")
    if not sid:
        await upd.message.reply_text("Aucune session en cours. Utilise /session_list pour trouver un ID.")
        return
    await upd.message.reply_text(f"Export session {sid}...")
    raw = run_shell(f"opencode export {sid} 2>&1", timeout=15)
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
    raw = run_shell(f"opencode import {args[0]} 2>&1", timeout=15)
    cleaned = strip_ansi(raw) if raw else "Importe"
    await upd.message.reply_text(cleaned[:3500])

async def cmd_models(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = run_shell("opencode models 2>&1", timeout=10)
    cleaned = strip_ansi(raw) if raw else "Aucun modele"
    await upd.message.reply_text(f"Modeles disponibles:\n\n{cleaned[:3500]}")

async def cmd_version(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = run_shell("opencode --version 2>&1", timeout=5)
    await upd.message.reply_text(raw or "opencode")

async def cmd_upgrade(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await upd.message.reply_text("Mise a jour opencode...")
    raw = run_shell("opencode upgrade 2>&1", timeout=60)
    cleaned = strip_ansi(raw) if raw else "Fait"
    await upd.message.reply_text(cleaned[:3500])

async def cmd_providers(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = run_shell("opencode providers list 2>&1", timeout=10)
    cleaned = strip_ansi(raw) if raw else "Aucun provider"
    await upd.message.reply_text(f"Providers:\n\n{cleaned[:3500]}")

async def cmd_serve(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await upd.message.reply_text(
        "Demarrage serveur opencode...\n"
        "Le serveur tourne en arriere-plan.\n"
        "Utilise /attach <url> pour t'y connecter."
    )
    raw = run_shell("opencode serve --port 4096 &>/dev/null & disown", timeout=3)
    await upd.message.reply_text("Serveur demarre sur http://localhost:4096")

async def cmd_fork(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    sid = cfg.get("session_id", "")
    if not sid:
        await upd.message.reply_text("Aucune session a fork.")
        return
    cfg["session_id"] = ""
    save_config(cfg)
    await upd.message.reply_text(
        "Session forkee au prochain message.\n"
        "La session originale est preservee."
    )
    cfg["continue_session"] = True
    save_config(cfg)

async def cmd_github(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = run_shell("opencode github 2>&1", timeout=15)
    cleaned = strip_ansi(raw) if raw else "GitHub: voir https://github.com"
    await upd.message.reply_text(cleaned[:3500])

async def cmd_debug(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = run_shell("opencode debug 2>&1", timeout=15)
    cleaned = strip_ansi(raw) if raw else "Debug opencode"
    await upd.message.reply_text(cleaned[:3500])

async def chat_handler(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = upd.message.text.strip()
    if not msg:
        return
    status = await upd.message.reply_text("Opencode reflechit...")
    result = opencode_run(msg)
    await status.edit_text(result)

async def post_init(app: Application):
    cmds = [
        BotCommand("start", "Demarrer le bot"),
        BotCommand("model", "Voir/changer le modele"),
        BotCommand("model_deepseek", "DeepSeek V4 Flash"),
        BotCommand("model_mimo", "Mimo V2.5"),
        BotCommand("model_nemotron", "Nemotron 3 Ultra"),
        BotCommand("model_north", "North Mini Code"),
        BotCommand("model_bigpickle", "Big Pickle"),
        BotCommand("variant", "Voir/changer le variant"),
        BotCommand("variant_high", "Raisonnement eleve"),
        BotCommand("variant_max", "Raisonnement max"),
        BotCommand("variant_minimal", "Raisonnement minimal"),
        BotCommand("variant_none", "Variant par defaut"),
        BotCommand("agent", "Voir/changer l'agent"),
        BotCommand("agent_plan", "Mode planification"),
        BotCommand("agent_build", "Mode developpement"),
        BotCommand("agent_explore", "Mode exploration"),
        BotCommand("agent_general", "Agent general"),
        BotCommand("agent_none", "Agent par defaut"),
        BotCommand("session", "Gestion des sessions"),
        BotCommand("continue_on", "Activer session continue"),
        BotCommand("continue_off", "Desactiver session continue"),
        BotCommand("session_new", "Nouvelle session"),
        BotCommand("session_list", "Lister les sessions"),
        BotCommand("session_switch", "Changer de session (par num)"),
        BotCommand("session_delete", "Supprimer une session (par num)"),
        BotCommand("thinking", "Afficher le raisonnement"),
        BotCommand("config", "Voir la configuration"),
        BotCommand("models", "Lister tous les modeles"),
        BotCommand("version", "Version d'opencode"),
        BotCommand("providers", "Fournisseurs AI"),
        BotCommand("stats", "Stats utilisation (7j)"),
        BotCommand("export", "Exporter la session"),
        BotCommand("import", "Importer une session"),
        BotCommand("fork", "Fork la session courante"),
        BotCommand("serve", "Demarrer serveur headless"),
        BotCommand("github", "Integration GitHub"),
        BotCommand("debug", "Outils de debug"),
        BotCommand("upgrade", "Mettre a jour opencode"),
    ]
    await app.bot.set_my_commands(cmds)
    log.info("Assistant bot pret")

async def quick_set_model(upd, name):
    cfg = load_config()
    cfg["model"] = FREE_MODELS[name]
    save_config(cfg)
    await upd.message.reply_text(f"Modele change: {FREE_MODELS[name]}")

async def quick_set_variant(upd, val):
    cfg = load_config()
    cfg["variant"] = val
    save_config(cfg)
    await upd.message.reply_text(f"Variant: {val or 'defaut'}")

async def quick_set_agent(upd, name):
    cfg = load_config()
    if name == "none":
        cfg["agent"] = ""
        await upd.message.reply_text("Agent: defaut")
    else:
        cfg["agent"] = AGENTS[name]
        await upd.message.reply_text(f"Agent change: {AGENTS[name]}")
    save_config(cfg)

async def cmd_model_deepseek(upd, ctx): return await quick_set_model(upd, "deepseek")
async def cmd_model_mimo(upd, ctx): return await quick_set_model(upd, "mimo")
async def cmd_model_nemotron(upd, ctx): return await quick_set_model(upd, "nemotron")
async def cmd_model_north(upd, ctx): return await quick_set_model(upd, "north")
async def cmd_model_bigpickle(upd, ctx): return await quick_set_model(upd, "bigpickle")

async def cmd_variant_high(upd, ctx): return await quick_set_variant(upd, "high")
async def cmd_variant_max(upd, ctx): return await quick_set_variant(upd, "max")
async def cmd_variant_minimal(upd, ctx): return await quick_set_variant(upd, "minimal")
async def cmd_variant_none(upd, ctx): return await quick_set_variant(upd, "")

async def cmd_agent_plan(upd, ctx): return await quick_set_agent(upd, "plan")
async def cmd_agent_build(upd, ctx): return await quick_set_agent(upd, "build")
async def cmd_agent_explore(upd, ctx): return await quick_set_agent(upd, "explore")
async def cmd_agent_general(upd, ctx): return await quick_set_agent(upd, "general")
async def cmd_agent_none(upd, ctx): return await quick_set_agent(upd, "none")

async def cmd_continue_on(upd, ctx):
    cfg = load_config()
    cfg["continue_session"] = True
    cfg["session_id"] = ""
    save_config(cfg)
    await upd.message.reply_text("Session continue activee")

async def cmd_continue_off(upd, ctx):
    cfg = load_config()
    cfg["continue_session"] = False
    cfg["session_id"] = ""
    save_config(cfg)
    await upd.message.reply_text("Session unique activee")

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
    sid, err = get_session_by_index(args[0])
    if err:
        await upd.message.reply_text(err)
        return
    cfg = load_config()
    cfg["session_id"] = sid
    cfg["continue_session"] = True
    save_config(cfg)
    await upd.message.reply_text(f"Session changee: {sid}")

async def cmd_session_delete(upd, ctx):
    args = ctx.args
    cfg = load_config()
    if not args:
        sid = cfg.get("session_id", "")
        if not sid:
            await upd.message.reply_text("Usage: /session_delete <num>")
            return
        await upd.message.reply_text(f"Suppression de la session active...")
    else:
        sid, err = get_session_by_index(args[0])
        if err:
            await upd.message.reply_text(err)
            return
        await upd.message.reply_text(f"Suppression de la session {sid}...")
    result = run_shell(f"opencode session delete {sid}", timeout=15)
    cleaned = strip_ansi(result) if result else "Supprimee"
    if cfg.get("session_id") == sid:
        cfg["session_id"] = ""
        save_config(cfg)
    await upd.message.reply_text(f"Session: {cleaned[:1000]}")

async def cmd_session_list(upd, ctx):
    raw = run_shell("opencode session list 2>&1", timeout=10)
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
        result = run_shell(f"opencode session delete {sid}", timeout=15)
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
            f"✅ Session reprise :\n\n`{title[:200]}`\n\nID: `{sid}`",
            parse_mode="Markdown"
        )

def main():
    token = get_token()
    if not token:
        log.error("Token manquant")
        return
    app = Application.builder().token(token).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("model_deepseek", cmd_model_deepseek))
    app.add_handler(CommandHandler("model_mimo", cmd_model_mimo))
    app.add_handler(CommandHandler("model_nemotron", cmd_model_nemotron))
    app.add_handler(CommandHandler("model_north", cmd_model_north))
    app.add_handler(CommandHandler("model_bigpickle", cmd_model_bigpickle))
    app.add_handler(CommandHandler("variant", cmd_variant))
    app.add_handler(CommandHandler("variant_high", cmd_variant_high))
    app.add_handler(CommandHandler("variant_max", cmd_variant_max))
    app.add_handler(CommandHandler("variant_minimal", cmd_variant_minimal))
    app.add_handler(CommandHandler("variant_none", cmd_variant_none))
    app.add_handler(CommandHandler("agent", cmd_agent))
    app.add_handler(CommandHandler("agent_plan", cmd_agent_plan))
    app.add_handler(CommandHandler("agent_build", cmd_agent_build))
    app.add_handler(CommandHandler("agent_explore", cmd_agent_explore))
    app.add_handler(CommandHandler("agent_general", cmd_agent_general))
    app.add_handler(CommandHandler("agent_none", cmd_agent_none))
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
    app.add_handler(CommandHandler("models", cmd_models))
    app.add_handler(CommandHandler("version", cmd_version))
    app.add_handler(CommandHandler("upgrade", cmd_upgrade))
    app.add_handler(CommandHandler("providers", cmd_providers))
    app.add_handler(CommandHandler("serve", cmd_serve))
    app.add_handler(CommandHandler("fork", cmd_fork))
    app.add_handler(CommandHandler("github", cmd_github))
    app.add_handler(CommandHandler("debug", cmd_debug))
    app.add_handler(CommandHandler("config", cmd_config))
    app.add_handler(CallbackQueryHandler(session_list_callback, pattern="^sl_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_handler))
    log.info("Assistant bot demarre...")
    app.run_polling()

if __name__ == "__main__":
    main()
