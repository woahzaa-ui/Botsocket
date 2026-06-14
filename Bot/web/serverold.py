"""
Serveur web Flask + SocketIO
Interface de contrôle et visualisation en temps réel
"""
import time
import threading
import logging
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO

logger = logging.getLogger("webserver")

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = "dofus-farmer-secret"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Références globales (injectées depuis main.py)
_bot = None
_game_state = None
_proxy = None


def set_references(bot, game_state, proxy):
    global _bot, _game_state, _proxy
    _bot = bot
    _game_state = game_state
    _proxy = proxy


# ── Routes HTTP ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
    if _game_state is None:
        return jsonify({"error": "non initialisé"})
    return jsonify(_game_state.to_dict())


@app.route("/api/bot")
def api_bot():
    if _bot is None:
        return jsonify({"error": "non initialisé"})
    return jsonify(_bot.to_dict())


@app.route("/api/logs")
def api_logs():
    if _bot is None:
        return jsonify([])
    n = int(request.args.get("n", 50))
    return jsonify(_bot.get_logs(n))


@app.route("/api/bot/start", methods=["POST"])
def bot_start():
    if _bot:
        _bot.start()
    return jsonify({"status": "started"})


@app.route("/api/bot/stop", methods=["POST"])
def bot_stop():
    if _bot:
        _bot.stop()
    return jsonify({"status": "stopped"})


@app.route("/api/bot/pause", methods=["POST"])
def bot_pause():
    if _bot:
        _bot.pause()
    return jsonify({"status": "paused"})


@app.route("/api/bot/resume", methods=["POST"])
def bot_resume():
    if _bot:
        _bot.resume()
    return jsonify({"status": "resumed"})


@app.route("/api/bot/config", methods=["POST"])
def bot_config():
    if _bot is None:
        return jsonify({"error": "non initialisé"})
    data = request.json or {}
    if "delay_min" in data:
        _bot.delay_min = float(data["delay_min"])
    if "delay_max" in data:
        _bot.delay_max = float(data["delay_max"])
    if "harvest_duration" in data:
        _bot.harvest_duration = float(data["harvest_duration"])
    return jsonify({"status": "ok"})


# ── Diffusion WebSocket ──────────────────────────────────────────────────────

def broadcast_loop():
    """Envoie l'état complet toutes les secondes via WebSocket"""
    while True:
        try:
            if _game_state and _bot:
                payload = {
                    "game": _game_state.to_dict(),
                    "bot": _bot.to_dict(),
                    "logs": _bot.get_logs(20),
                    "ts": time.time(),
                }
                socketio.emit("state_update", payload)
        except Exception as e:
            logger.warning(f"Broadcast error: {e}")
        time.sleep(1.0)


def start_server(host="127.0.0.1", port=8080):
    t = threading.Thread(target=broadcast_loop, daemon=True)
    t.start()
    logger.info(f"Interface web: http://{host}:{port}")
    socketio.run(app, host=host, port=port, debug=False, use_reloader=False)
