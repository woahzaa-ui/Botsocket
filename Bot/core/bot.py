"""
Bot Farmer Dofus 1.29 — Protocole texte
Bûcheron automatique : détecte ressources JX/JS → se déplace GA001 → récolte GA500
"""
import re
import time
import threading
import random
import logging
from enum import Enum, auto
from typing import Optional, Callable

from core.game_state import GameState, Resource
from core.parser import ParsedPacket, decode_cell_path, encode_cell, C

logger = logging.getLogger("bot")

# Regex pour supprimer les codes ANSI — les logs stockés dans self.logs vont dans le HTML
_ANSI_RE = re.compile(r'\033\[[0-9;]*m')

def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub('', s)


# ── Helpers log colorés (console seulement) ───────────────────────────────────

def _log_state(state_name: str) -> str:
    colors = {
        "IDLE":             C.WHITE,
        "MOVING":           C.BLUE,
        "HARVESTING":       C.MAGENTA,
        "WAITING_RESPAWN":  C.YELLOW,
        "PAUSED":           C.GRAY,
        "ERROR":            C.RED,
    }
    c = colors.get(state_name, C.WHITE)
    return f"{c}{C.BOLD}[{state_name}]{C.RESET}"


# ── États ─────────────────────────────────────────────────────────────────────

class BotState(Enum):
    IDLE            = auto()
    MOVING          = auto()
    HARVESTING      = auto()
    WAITING_RESPAWN = auto()
    PAUSED          = auto()
    ERROR           = auto()


# ── Bot principal ─────────────────────────────────────────────────────────────

class FarmerBot:
    def __init__(self, game_state: GameState, inject_fn: Callable[[bytes], None]):
        self.state = BotState.IDLE
        self.gs = game_state
        self.inject = inject_fn
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.logs: list = []
        self.current_target: Optional[Resource] = None

        # Paramètres configurables
        self.delay_min         = 0.8
        self.delay_max         = 1.8
        self.harvest_duration  = 5.5
        self.wait_no_resources = 15
        self.skill_id          = 6   # 6=bûcheron, 4=mineur, 3=alchimiste

    # ── Démarrage / arrêt ────────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running = True
        self.state = BotState.IDLE
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.log("Bot démarré")

    def stop(self):
        self._running = False
        self.state = BotState.PAUSED
        self.log("Bot arrêté")

    def pause(self):
        self.state = BotState.PAUSED
        self.log("Bot mis en pause")

    def resume(self):
        if self.state == BotState.PAUSED:
            self.state = BotState.IDLE
            self.log("Bot repris")

    # ── Boucle principale ─────────────────────────────────────────────────────

    def _loop(self):
        while self._running:
            try:
                if   self.state == BotState.IDLE:
                    self._tick_idle()
                elif self.state == BotState.MOVING:
                    self._tick_moving()
                elif self.state == BotState.HARVESTING:
                    self._tick_harvesting()
                elif self.state == BotState.WAITING_RESPAWN:
                    self._tick_waiting()
                elif self.state == BotState.PAUSED:
                    time.sleep(0.5)
            except Exception as e:
                logger.error(f"{C.RED}Erreur bot: {e}{C.RESET}")
                self.state = BotState.ERROR
                time.sleep(2)
                self.state = BotState.IDLE
            time.sleep(0.2)

    def _tick_idle(self):
        target = self.gs.get_nearest_available()
        if target is None:
            self.log("Aucune ressource disponible, attente respawn…")
            self.state = BotState.WAITING_RESPAWN
            return

        self.current_target = target
        self.log(f"Cible: cellule {target.cell_id} elem={target.element_id}")

        if target.cell_id == self.gs.player_cell_id:
            self._do_harvest(target)
        else:
            self._do_move(target.cell_id)
            self.state = BotState.MOVING

    def _tick_moving(self):
        if self.current_target is None:
            self.state = BotState.IDLE
            return
        if self.gs.player_cell_id == self.current_target.cell_id:
            self._human_delay()
            self._do_harvest(self.current_target)

    def _tick_harvesting(self):
        time.sleep(self.harvest_duration + random.uniform(0, 1.0))
        cell = self.current_target.cell_id if self.current_target else "?"
        self.log(f"Récolte terminée (fallback timer) cellule {cell}")
        self.current_target = None
        self.state = BotState.IDLE

    def _tick_waiting(self):
        time.sleep(self.wait_no_resources)
        self.state = BotState.IDLE

    # ── Actions ───────────────────────────────────────────────────────────────

    def _do_move(self, cell_id: int):
        """Format client: GA001[encoded_path]\n\0"""
        origin = self.gs.player_cell_id
        path   = encode_cell(origin) + encode_cell(cell_id)
        msg    = f"GA001{path}\n\x00"
        self._human_delay()
        self.inject(msg.encode("latin-1"))
        self.log(f"→ Déplacement {origin} → {cell_id} (path={path})")

    def _do_harvest(self, resource: Resource):
        """Format client: GA500[element_id];[skill_id]\n\0"""
        msg = f"GA500{resource.element_id};{self.skill_id}\n\x00"
        self._human_delay()
        self.inject(msg.encode("latin-1"))
        self.log(f"⚒ Récolte elem={resource.element_id} cellule={resource.cell_id} skill={self.skill_id}")
        self.state = BotState.HARVESTING

    # ── Réception paquets ─────────────────────────────────────────────────────

    def on_packet(self, packet: ParsedPacket):
        op = packet.opcode
        d  = packet.data

        if op == "GCK":
            self.log(f"Contexte jeu OK: {d.get('character','')}")

        elif op == "As":
            cell = d.get("cell_id", 0)
            if cell > 0:
                self.gs.player_cell_id = cell
                self.log(f"Position initiale: cellule {cell}")

        elif op == "JX":
            elements = d.get("elements", [])
            if elements:
                objects = [
                    {"cell_id": e["cell_id"], "element_id": e["element_id"], "available": True}
                    for e in elements if e["cell_id"] > 0
                ]
                if objects:
                    self.gs.set_resources_from_list(objects)
                    cells = [o["cell_id"] for o in objects]
                    self.log(f"JX: {len(objects)} ressources chargées → {cells}")

        elif op == "JS":
            elements = d.get("elements", [])
            if elements:
                objects = [
                    {"cell_id": e["cell_id"], "element_id": e["element_id"], "available": True}
                    for e in elements if e["cell_id"] > 0
                ]
                if objects:
                    self.gs.set_resources_from_list(objects)
                    self.log(f"JS: {len(objects)} ressources chargées")

        elif op == "GA":
            action_id = d.get("action_id", 0)
            actor_id  = d.get("actor_id", 0)

            if action_id == 1:
                dest = d.get("destination")
                if dest and actor_id == self.gs.player_id:
                    self.gs.player_cell_id = dest
                    self.log(f"Arrivée cellule {dest}")

            elif action_id in (500, 501):
                self.log(f"Récolte démarrée (GA {action_id})")

        elif op == "GDF":
            elem_id = d.get("element_id", 0)
            self.log(f"✓ Ressource récoltée (GDF elem={elem_id})")
            if self.current_target:
                self.gs.update_resource(
                    self.current_target.cell_id,
                    self.current_target.element_id,
                    False
                )
            self.current_target = None
            self.state = BotState.IDLE

        elif op == "Im":
            code = d.get("code", 0)
            if code == 116:
                self.log("Ressource récoltée (Im 116 fallback)")
                if self.current_target:
                    self.gs.update_resource(
                        self.current_target.cell_id,
                        self.current_target.element_id,
                        False
                    )

    # ── Utilitaires ───────────────────────────────────────────────────────────

    def _human_delay(self):
        time.sleep(random.uniform(self.delay_min, self.delay_max))

    def log(self, msg: str):
        """
        - Console : message coloré selon l'état
        - self.logs (→ HTML) : message sans codes ANSI
        """
        clean_msg = strip_ansi(msg)
        state_label = _log_state(self.state.name)
        entry = {"time": time.time(), "msg": clean_msg, "state": self.state.name}
        self.logs.append(entry)
        if len(self.logs) > 200:
            self.logs.pop(0)
        logger.info(f"{state_label} {msg}")

    def get_logs(self, n: int = 50) -> list:
        return self.logs[-n:]

    def to_dict(self) -> dict:
        return {
            "state":            self.state.name,
            "running":          self._running,
            "current_target":   self.current_target.to_dict() if self.current_target else None,
            "delay_min":        self.delay_min,
            "delay_max":        self.delay_max,
            "harvest_duration": self.harvest_duration,
            "skill_id":         self.skill_id,
        }
