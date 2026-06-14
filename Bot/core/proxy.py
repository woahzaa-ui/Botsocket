"""
Proxy MITM pour Dofus 1.29 — SOCKS5 avec destination intelligente
- Si destination = serveur Dofus → intercepte et parse les trames
- Sinon → tunnel transparent (pass-through)
"""
import socket
import struct
import threading
import logging
from core.parser import PacketParser

logger = logging.getLogger("proxy")


class DofusProxy:
    def __init__(self, local_host="127.0.0.1", local_port=6969,
                 remote_host="185.38.151.71", remote_port=6968,
                 on_packet=None):
        self.local_host = local_host
        self.local_port = local_port
        self.remote_host = remote_host
        self.remote_port = remote_port
        self.on_packet = on_packet
        self.parser = PacketParser(on_packet=self.on_packet)
        self._running = False
        self._server_sock = None
        self.client_sock = None
        self.remote_sock = None

    def start(self):
        self._running = True
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self.local_host, self.local_port))
        self._server_sock.listen(50)
        logger.info(f"Proxy SOCKS5 en écoute sur {self.local_host}:{self.local_port}")
        logger.info(f"Serveur Dofus cible : {self.remote_host}:{self.remote_port}")

        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()

    def _accept_loop(self):
        while self._running:
            try:
                self._server_sock.settimeout(1.0)
                try:
                    client_sock, addr = self._server_sock.accept()
                except socket.timeout:
                    continue
                t = threading.Thread(
                    target=self._handle_client,
                    args=(client_sock, addr),
                    daemon=True
                )
                t.start()
            except Exception as e:
                if self._running:
                    logger.error(f"Erreur accept: {e}")

    def _socks5_handshake(self, client_sock):
        """
        Gère le handshake SOCKS5.
        Retourne (dest_host, dest_port) ou (None, None) si échec.
        """
        try:
            # Étape 1 — greeting
            header = client_sock.recv(2)
            if not header or len(header) < 2:
                return None, None

            if header[0] != 0x05:
                # Pas du SOCKS5 — on ignore
                logger.warning(f"Protocole inconnu: {header[0]:#x}")
                return None, None

            n_methods = header[1]
            client_sock.recv(n_methods)

            # Réponse : SOCKS5, no auth
            client_sock.send(b'\x05\x00')

            # Étape 2 — requête
            req = client_sock.recv(4)
            if not req or len(req) < 4:
                return None, None

            ver, cmd, rsv, atyp = req[0], req[1], req[2], req[3]

            if atyp == 0x01:        # IPv4
                addr_bytes = client_sock.recv(4)
                port_bytes = client_sock.recv(2)
                dest_host = socket.inet_ntoa(addr_bytes)
            elif atyp == 0x03:      # Domaine
                length = client_sock.recv(1)[0]
                dest_host = client_sock.recv(length).decode()
                port_bytes = client_sock.recv(2)
            elif atyp == 0x04:      # IPv6
                addr_bytes = client_sock.recv(16)
                port_bytes = client_sock.recv(2)
                dest_host = socket.inet_ntop(socket.AF_INET6, addr_bytes)
            else:
                logger.error(f"ATYP inconnu: {atyp}")
                return None, None

            dest_port = struct.unpack(">H", port_bytes)[0]

            # Réponse succès
            client_sock.send(b'\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00')

            return dest_host, dest_port

        except Exception as e:
            logger.error(f"Erreur handshake SOCKS5: {e}")
            return None, None

    def _handle_client(self, client_sock, addr):
        try:
            dest_host, dest_port = self._socks5_handshake(client_sock)

            if dest_host is None:
                client_sock.close()
                return

            is_dofus = (dest_host == self.remote_host and dest_port == self.remote_port)

            if is_dofus:
                logger.info(f"[DOFUS] Tunnel intercepté vers {dest_host}:{dest_port}")
            else:
                logger.debug(f"[PASS] Tunnel transparent vers {dest_host}:{dest_port}")

            # Connexion vers la vraie destination
            remote_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            remote_sock.connect((dest_host, dest_port))

            if is_dofus:
                self.remote_sock = remote_sock

            t1 = threading.Thread(
                target=self._forward,
                args=(client_sock, remote_sock, "CLIENT→SERVEUR", is_dofus),
                daemon=True
            )
            t2 = threading.Thread(
                target=self._forward,
                args=(remote_sock, client_sock, "SERVEUR→CLIENT", is_dofus),
                daemon=True
            )
            t1.start()
            t2.start()
            t1.join()
            t2.join()

        except Exception as e:
            logger.error(f"Erreur connexion vers {dest_host}:{dest_port} → {e}")
        finally:
            client_sock.close()
            if 'remote_sock' in dir() and remote_sock:
                remote_sock.close()
            if is_dofus:
                logger.info("Connexion Dofus fermée")

    def _forward(self, src, dst, direction, parse=False):
        buf = b""
        while True:
            try:
                data = src.recv(4096)
                if not data:
                    break
                if parse:
                    buf += data
                    # LOG BRUT temporaire
                    logger.debug(f"[{direction}] RAW {len(data)}B : {data[:64].hex()}")
                    try:
                        buf = self.parser.feed(buf, direction)
                    except Exception as e:
                        logger.error(f"Erreur parseur [{direction}]: {e}")
                        buf = b""
                dst.send(data)
            except Exception:
                break

    def inject(self, packet_bytes: bytes):
        if self.remote_sock:
            try:
                self.remote_sock.send(packet_bytes)
                logger.debug(f"Paquet injecté: {packet_bytes.hex()}")
            except Exception as e:
                logger.error(f"Erreur injection: {e}")

    def stop(self):
        self._running = False
        if self._server_sock:
            self._server_sock.close()
        logger.info("Proxy arrêté")
        