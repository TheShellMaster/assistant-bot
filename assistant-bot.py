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
        f"Agent: {cfg.get('agent') or 'defaut'}\n"
        f"Session continue: {'oui' if cfg['continue_session'] else 'non'}\n"
        f"Variant: {cfg['variant'] or 'defaut'}\n\n"
        "📌 /model /variant /agent - Menus interactifs\n"
        "📌 /session - Gerer sessions\n"
        "📌 /config - Voir config"
    )

async def cmd_model(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    kb = [[InlineKeyboardButton("🎯 Defaut", callback_data="mod_sel_")]]
    for key in FREE_MODELS:
        name = FREE_MODELS[key]
        mark = " ✓" if name == cfg["model"] else ""
        kb.append([InlineKeyboardButton(f"{key}{mark}", callback_data=f"mod_sel_{key}")])
    cfg = load_config()
    await upd.message.reply_text(
        f"Modele actuel: **{cfg['model']}**\n\nChoisis un modele :",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
    )

async def cmd_variant(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    variants = {"defaut": "", "high": "high", "max": "max", "minimal": "minimal"}
    kb = []
    for label, val in variants.items():
        mark = " ✓" if cfg.get("variant", "") == val else ""
        kb.append([InlineKeyboardButton(f"{label}{mark}", callback_data=f"var_sel_{val or 'none'}")])
    await upd.message.reply_text(
        f"Variant actuel: **{cfg['variant'] or 'defaut'}**\n\nChoisis un variant :",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
    )

async def cmd_agent(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    kb = [[InlineKeyboardButton("🎯 Defaut", callback_data="agt_sel_none")]]
    for key in AGENTS:
        val = AGENTS[key]
        mark = " ✓" if cfg.get("agent", "") == val else ""
        kb.append([InlineKeyboardButton(f"{key}{mark}", callback_data=f"agt_sel_{key}")])
    await upd.message.reply_text(
        f"Agent actuel: **{cfg.get('agent') or 'defaut'}**\n\nChoisis un agent :",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
    )

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
        BotCommand("start", "Demarrer"),
        BotCommand("model", "Choisir modele"),
        BotCommand("variant", "Choisir variant"),
        BotCommand("agent", "Choisir agent"),
        BotCommand("session", "Gerer les sessions"),
        BotCommand("session_new", "Nouvelle session"),
        BotCommand("session_list", "Liste des sessions"),
        BotCommand("session_switch", "Changer de session"),
        BotCommand("session_delete", "Supprimer session"),
        BotCommand("continue_on", "Session continue ON"),
        BotCommand("continue_off", "Session continue OFF"),
        BotCommand("config", "Configuration"),
        BotCommand("thinking", "Raisonnement"),
        BotCommand("models", "Tous les modeles"),
        BotCommand("providers", "Fournisseurs AI"),
        BotCommand("stats", "Stats 7 jours"),
        BotCommand("export", "Exporter session"),
        BotCommand("import", "Importer session"),
        BotCommand("fork", "Fork session"),
        BotCommand("serve", "Serveur headless"),
        BotCommand("version", "Version"),
        BotCommand("upgrade", "Mise a jour"),
        BotCommand("github", "GitHub"),
        BotCommand("debug", "Debug"),
    ]
    await app.bot.set_my_commands(cmds)
    log.info("Assistant bot pret")



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

async def config_callbacks(upd, ctx):
    q = upd.callback_query
    await q.answer()
    data = q.data
    cfg = load_config()

    if data.startswith("mod_sel_"):
        key = data.replace("mod_sel_", "")
        if not key:
            cfg["model"] = DEFAULT_CONFIG["model"]
            save_config(cfg)
            await q.message.edit_text(f"Modele: {cfg['model']}")
            return
        cfg["model"] = FREE_MODELS[key]
        save_config(cfg)
        variants = {"defaut": "", "high": "high", "max": "max", "minimal": "minimal"}
        kb = [[InlineKeyboardButton(l, callback_data=f"var_sel_{v or 'none'}") for l, v in variants.items()]]
        await q.message.edit_text(
            f"Modele: **{cfg['model']}**\n\nChoisis un variant (ou defaut) :",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
        )

    elif data.startswith("var_sel_"):
        val = data.replace("var_sel_none", "")
        val = val.replace("var_sel_", "")
        cfg["variant"] = val
        save_config(cfg)
        await q.message.edit_text(f"Variant: **{val or 'defaut'}**", parse_mode="Markdown")

    elif data.startswith("agt_sel_"):
        key = data.replace("agt_sel_", "")
        if key == "none":
            cfg["agent"] = ""
        else:
            cfg["agent"] = AGENTS[key]
        save_config(cfg)
        await q.message.edit_text(f"Agent: **{cfg.get('agent') or 'defaut'}**", parse_mode="Markdown")

def main():
    token = get_token()
    if not token:
        log.error("Token manquant")
        return
    app = Application.builder().token(token).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("model", cmd_model))
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
    app.add_handler(CommandHandler("models", cmd_models))
    app.add_handler(CommandHandler("version", cmd_version))
    app.add_handler(CommandHandler("upgrade", cmd_upgrade))
    app.add_handler(CommandHandler("providers", cmd_providers))
    app.add_handler(CommandHandler("serve", cmd_serve))
    app.add_handler(CommandHandler("fork", cmd_fork))
    app.add_handler(CommandHandler("github", cmd_github))
    app.add_handler(CommandHandler("debug", cmd_debug))
    app.add_handler(CommandHandler("config", cmd_config))
    app.add_handler(CallbackQueryHandler(config_callbacks, pattern="^(mod_|var_|agt_)"))
    app.add_handler(CallbackQueryHandler(session_list_callback, pattern="^sl_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_handler))
    log.info("Assistant bot demarre...")
    app.run_polling()

if __name__ == "__main__":
    main()
