#!/usr/bin/env python3
"""
Dofus 1.29 - Socket Farmer Bot
Démarrage : python main.py [--host IP_SERVEUR] [--port 5555] [--web-port 8080]

Workflow :
  1. Ce script démarre le proxy MITM sur 127.0.0.1:5555
  2. Redirige le client Dofus vers 127.0.0.1:5555 (via etc/hosts ou config réseau)
  3. Lance l'interface web sur http://127.0.0.1:8080
"""
import argparse
import logging
import threading
import sys

from core.proxy import DofusProxy
from core.parser import ParsedPacket
from core.game_state import GameState
from core.bot import FarmerBot
from web.server import start_server, set_references

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("main")


def main():
    parser = argparse.ArgumentParser(description="Dofus 1.29 Farmer Bot")
    parser.add_argument("--host",     default="141.94.99.2", help="IP du serveur Dofus")
    parser.add_argument("--port",     type=int, default=5562,   help="Port du serveur Dofus")
    parser.add_argument("--local-port", type=int, default=5555, help="Port local du proxy")
    parser.add_argument("--web-port", type=int, default=8080,   help="Port de l'interface web")
    parser.add_argument("--web-host", default="127.0.0.1",      help="Host de l'interface web")
    parser.add_argument("--debug",    action="store_true",       help="Mode debug (tous les paquets)")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # ── Initialisation des composants ──────────────────────────────────────
    game_state = GameState()

    # Le bot sera initialisé après le proxy (besoin de inject_fn)
    bot = None

    def on_packet(packet: ParsedPacket):
        if bot:
            bot.on_packet(packet)

    proxy = DofusProxy(
        local_host="127.0.0.1",
        local_port=args.local_port,
        remote_host=args.host,
        remote_port=args.port,
        on_packet=on_packet,
    )

    bot = FarmerBot(
        game_state=game_state,
        inject_fn=proxy.inject,
    )

    # ── Démarrage ──────────────────────────────────────────────────────────
    proxy.start()

    set_references(bot, game_state, proxy)

    logger.info("=" * 55)
    logger.info("  Dofus 1.29 Farmer Bot")
    logger.info("=" * 55)
    logger.info(f"  Proxy MITM    : 127.0.0.1:{args.local_port} → {args.host}:{args.port}")
    logger.info(f"  Interface web : http://{args.web_host}:{args.web_port}")
    logger.info("-" * 55)
    logger.info("  Configurez votre client Dofus pour se connecter sur")
    logger.info(f"  127.0.0.1:{args.local_port} (via /etc/hosts ou dofus.cfg)")
    logger.info("=" * 55)

    # Le serveur web tourne en thread principal (bloquant)
    try:
        start_server(host=args.web_host, port=args.web_port)
    except KeyboardInterrupt:
        logger.info("Arrêt...")
        proxy.stop()
        if bot:
            bot.stop()
        sys.exit(0)


if __name__ == "__main__":
    main()
