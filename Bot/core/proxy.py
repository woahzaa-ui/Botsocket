import socket
import threading
import logging
import argparse
from flask import Flask, jsonify, request
from flask_cors import CORS
from parser import DofusParser
from bot import DofusBot

# Configuration du logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger("proxy")
parser_logger = logging.getLogger("parser")

# Initialisation de Flask pour l'API de contrôle du bot
app = Flask(__name__)
CORS(app)

# Instance globale du bot qui sera initialisée lors de la connexion
current_bot = None

def proxy_stream(source, destination, direction, parser, bot):
    """
    Relie la socket source à la socket destination.
    Analyse les paquets au passage et maintient la socket du bot à jour.
    """
    # Liaison dynamique de la socket pour que le bot injecte toujours vers le SERVEUR
    if direction == "c2s":
        bot.set_server_socket(destination)
    elif direction == "s2c":
        bot.set_server_socket(source)

    buffer = b""
    while True:
        try:
            data = source.recv(4096)
            if not data:
                break
            
            buffer += data
            
            # Délimiteur : \x00 pour le serveur (s2c), \n (\x0a) pour le client (c2s)
            delimiter = b'\x00' if direction == "s2c" else b'\x0a'
            
            while delimiter in buffer:
                packet, buffer = buffer.split(delimiter, 1)
                if not packet:
                    continue
                
                try:
                    packet_str = packet.decode('utf-8', errors='ignore').strip()
                    if packet_str:
                        if direction == "c2s":
                            parser.parse_client_to_server(packet_str, bot)
                        else:
                            parser.parse_server_to_client(packet_str, bot)
                except Exception as e:
                    parser_logger.error(f"Erreur de décodage/parsing [{direction}]: {e}")
            
            destination.sendall(data)
        except Exception as e:
            logger.debug(f"Connexion fermée ou réinitialisée [{direction}]: {e}")
            break
            
    try: source.close()
    except: pass
    try: destination.close()
    except: pass

def handle_client(client_socket, target_host, target_port):
    """Gère une connexion client et la connecte au serveur Dofus cible."""
    global current_bot
    remote_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        remote_socket.connect((target_host, target_port))
        logger.info(f"[DOFUS] Tunnel intercepté vers {target_host}:{target_port}")
    except Exception as e:
        logger.error(f"Impossible de se connecter au serveur Dofus cible: {e}")
        client_socket.close()
        return

    # Initialisation unique du parser et du bot pour cette session
    parser = DofusParser()
    current_bot = DofusBot(parser)

    # Lancement des threads de redirection en leur passant l'instance du bot
    c2s_thread = threading.Thread(target=proxy_stream, args=(client_socket, remote_socket, "c2s", parser, current_bot), daemon=True)
    s2c_thread = threading.Thread(target=remote_socket, args=(remote_socket, client_socket, "s2c", parser, current_bot), daemon=True)

    c2s_thread.start()
    s2c_thread.start()

def start_socks5_proxy(local_port, target_host, target_port):
    """Démarre le serveur proxy d'écoute local."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind(('127.0.0.1', local_port))
        server.listen(5)
        logger.info(f"Proxy SOCKS5 en écoute sur 127.0.0.1:{local_port}")
        logger.info(f"Serveur Dofus cible : {target_host}:{target_port}")
    except Exception as e:
        logger.error(f"Erreur lors du bind du proxy sur le port {local_port}: {e}")
        return

    while True:
        try:
            client_socket, addr = server.accept()
            threading.Thread(target=handle_client, args=(client_socket, target_host, target_port), daemon=True).start()
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Erreur acceptation client SOCKS5: {e}")
            break
            
    server.close()

# ==============================================================================
# ROUTES API FLASK POUR LE CONTRÔLE DU BOT VIA L'INTERFACE WEB
# ==============================================================================

@app.route('/api/bot/status', methods=['GET'])
def get_status():
    if current_bot is None:
        return jsonify({"status": "NOT_CONNECTED", "message": "Le jeu Dofus n'est pas connecté via le proxy."})
    return jsonify({
        "status": current_bot.state,
        "is_running": current_bot.is_running,
        "current_map": current_bot.current_map
    })

@app.route('/api/bot/start', methods=['POST'])
def start_bot():
    if current_bot is None:
        return jsonify({"error": "Le bot n'est pas encore initialisé (connectez-vous en jeu d'abord)."}), 400
    current_bot.start()
    return jsonify({"status": "success", "message": "Bot démarré avec succès."})

@app.route('/api/bot/stop', methods=['POST'])
def stop_bot():
    if current_bot is None:
        return jsonify({"error": "Le bot n'est pas initialisé."}), 400
    current_bot.stop()
    return jsonify({"status": "success", "message": "Bot arrêté."})

@app.route('/api/bot/inject', methods=['POST'])
def inject_packet():
    if current_bot is None:
        return jsonify({"error": "Le bot n'est pas initialisé."}), 400
    data = request.get_json()
    if not data or 'packet' not in data:
        return jsonify({"error": "Paquet manquant dans la requête."}), 400
    
    success = current_bot.send_packet(data['packet'])
    if success:
        return jsonify({"status": "success", "message": f"Paquet {data['packet']} injecté."})
    else:
        return jsonify({"error": "Échec de l'injection du paquet."}), 500

def start_web_server(port):
    """Démarre le serveur web de l'API Flask."""
    logger.info(f"Interface web accessible sur : http://127.0.0.1:{port}")
    # Running Flask en mode discret threadé
    app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser(description="Dofus 1.29 Farmer Bot - Proxy Component")
    arg_parser.add_argument("--host", required=True, help="IP du serveur Dofus cible")
    arg_parser.add_argument("--port", type=int, required=True, help="Port du serveur Dofus cible")
    arg_parser.add_argument("--local-port", type=int, default=6969, help="Port local pour l'écoute du proxy")
    arg_parser.add_argument("--web-port", type=int, default=8080, help="Port pour l'interface de l'API Web")
    arg_parser.add_argument("--debug", action="store_true", help="Active les logs de debug complets")
    args = arg_parser.parse_args()

    if not args.debug:
        logging.getLogger("proxy").setLevel(logging.INFO)
        logging.getLogger("parser").setLevel(logging.INFO)

    # Lancement du serveur web Flask dans un thread séparé
    threading.Thread(target=start_web_server, args=(args.web_port,), daemon=True).start()

    # Lancement du proxy SOCKS5 principal (bloquant)
    start_socks5_proxy(args.local_port, args.host, args.port)
