"""
Parseur Dofus 1.29 — Protocole texte ASCII
Format: [OPCODE][données]\n\0
Chemin de déplacement encodé en paires de caractères base64-like
"""
import logging
from dataclasses import dataclass, field
from typing import Optional, Callable, Dict, Any

# ── Couleurs ANSI ─────────────────────────────────────────────────────────────

class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"

    # Couleurs texte
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN    = "\033[96m"
    WHITE   = "\033[97m"
    GRAY    = "\033[90m"

    # Shortcuts sémantiques
    SERVER  = CYAN       # paquet serveur → client
    CLIENT  = YELLOW     # paquet client → serveur
    FARMING = GREEN      # opcode farming important
    MOVE    = BLUE       # déplacement
    HARVEST = MAGENTA    # récolte
    ERROR   = RED        # erreur / inconnu
    INFO    = WHITE
    MUTED   = GRAY


def color_opcode(opcode: str, direction: str) -> str:
    if direction == "server":
        return f"{C.SERVER}{C.BOLD}{opcode}{C.RESET}"
    return f"{C.CLIENT}{C.BOLD}{opcode}{C.RESET}"


def color_data(data: str, max_len: int = 100) -> str:
    return f"{C.MUTED}{data[:max_len]}{C.RESET}"


# ── Logger coloré ─────────────────────────────────────────────────────────────

class ColorFormatter(logging.Formatter):
    LEVEL_COLORS = {
        logging.DEBUG:    C.MUTED,
        logging.INFO:     C.WHITE,
        logging.WARNING:  C.YELLOW,
        logging.ERROR:    C.RED,
        logging.CRITICAL: C.RED + C.BOLD,
    }

    def format(self, record):
        color = self.LEVEL_COLORS.get(record.levelno, C.RESET)
        levelname = f"{color}{record.levelname:<8}{C.RESET}"
        name = f"{C.CYAN}{record.name}{C.RESET}"
        msg = super().format(record)
        # Remplace le message brut par la version colorée
        record.levelname = record.levelname  # conservé tel quel pour le format
        formatted = f"{levelname} {name} │ {color}{record.getMessage()}{C.RESET}"
        return formatted


def setup_color_logging(level=logging.INFO):
    """Installe le formatter coloré sur le logger racine."""
    handler = logging.StreamHandler()
    handler.setFormatter(ColorFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


logger = logging.getLogger("parser")

# ── Clé d'encodage ───────────────────────────────────────────────────────────

HASH_KEYS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"

# ── Opcodes ───────────────────────────────────────────────────────────────────

OPCODES = {
    # Serveur → Client
    "HC":  "HelloConnect",
    "ATK": "AuthTokenKey",
    "AV":  "AccountVersion",
    "ALK": "AccountListKey",
    "ASK": "AccountSelectKey",
    "Af":  "AccountFriends",
    "Im":  "InfoMessage",
    "BT":  "BasicTime",
    "BN":  "BasicNoOperation",
    "ZS":  "ZoneState",
    "GCK": "GameContextCreate",
    "GM":  "GameMapActors",
    "GA":  "GameAction",
    "GDK": "GameDataKey",
    "GDF": "GameDataFinish",       # ← fin de récolte
    "GI":  "GameInventory",
    "JS":  "JobSkills",
    "JX":  "JobSkillsExtra",       # ← ressources disponibles sur la map
    "Rx":  "RoleplayExtra",
    "CW":  "CellsWalkable",
    "fC":  "FightContext",
    "Gz":  "GameZone",
    "al":  "SpellList",
    "As":  "CharacterStats",
    "RMD": "RoleplayMapData",
    "cMK": "ChatKiosk",
    "cs":  "ChatServer",

    # Client → Serveur
    "AT":  "AuthToken",
    "Ak":  "AuthKey",
    "AL":  "AccountList",
    "AS":  "AccountSelect",
    "GC":  "GameContext",
    "BD":  "BasicDate",
    "Ir":  "InfoRequest",
    "GKK": "GameActionAck",
    "CWJ": "CellsWalkableJoin",
    "GP":  "GamePosition",
}

# Opcodes farming — affichés en INFO (couleur FARMING)
FARMING_OPCODES = {"GM", "GA", "GCK", "JS", "JX", "GDF", "Rx", "Im", "ZS", "As"}


# ── DataClass paquet parsé ────────────────────────────────────────────────────

@dataclass
class ParsedPacket:
    opcode: str
    name: str
    direction: str
    raw: str
    data: Dict[str, Any] = field(default_factory=dict)


# ── Encodage / décodage cellules ──────────────────────────────────────────────

def decode_cell_path(encoded: str) -> list:
    """Décode un chemin de cellules encodé en paires base64-like."""
    cells = []
    for i in range(0, len(encoded) - 1, 2):
        c1 = HASH_KEYS.find(encoded[i])
        c2 = HASH_KEYS.find(encoded[i+1])
        if c1 >= 0 and c2 >= 0:
            cells.append(c1 * len(HASH_KEYS) + c2)
    return cells


def encode_cell(cell_id: int) -> str:
    """Encode un cell_id en paire base64-like."""
    return HASH_KEYS[cell_id // len(HASH_KEYS)] + HASH_KEYS[cell_id % len(HASH_KEYS)]


# ── Parser principal ──────────────────────────────────────────────────────────

class PacketParser:
    def __init__(self, on_packet: Optional[Callable] = None):
        self.on_packet = on_packet

    def feed(self, buf: bytes, direction: str) -> bytes:
        """Consomme le buffer ligne par ligne (\n = fin de message)."""
        try:
            text = buf.decode("latin-1").replace("\x00", "")
        except Exception:
            return buf

        if "\n" not in text:
            return buf

        lines = text.split("\n")
        remainder = lines[-1]

        for line in lines[:-1]:
            for msg in self._split_messages(line):
                msg = msg.strip()
                if not msg:
                    continue
                packet = self._parse_line(msg, direction)
                if packet and self.on_packet:
                    self.on_packet(packet)

        return remainder.encode("latin-1")

    def _split_messages(self, line: str) -> list:
        """Sépare les messages collés dans une même ligne."""
        all_ops = sorted(OPCODES.keys(), key=len, reverse=True)
        messages = []
        current = line
        i = 0

        while i < len(current):
            found = False
            if i > 0:
                for op in all_ops:
                    if current[i:i+len(op)] == op:
                        if i > 0 and current[i-1] not in '|;,~^':
                            messages.append(current[:i])
                            current = current[i:]
                            i = 0
                            found = True
                            break
            if not found:
                i += 1

        if current:
            messages.append(current)

        return messages if messages else [line]

    def _parse_line(self, line: str, direction: str) -> Optional[ParsedPacket]:
        if not line:
            return None

        opcode = None
        data_str = ""
        for length in (3, 2):
            candidate = line[:length]
            if candidate in OPCODES:
                opcode = candidate
                data_str = line[length:]
                break

        if opcode is None:
            logger.debug(f"{C.MUTED}[{direction}] Inconnu: {line[:8]!r}{C.RESET}")
            return None

        name = OPCODES[opcode]
        dir_label = "S→C" if direction == "server" else "C→S"
        dir_color = C.SERVER if direction == "server" else C.CLIENT

        if opcode in FARMING_OPCODES:
            # Coloration spéciale par type d'opcode farming
            op_color = C.FARMING
            if opcode == "GA":
                op_color = C.MOVE
            elif opcode in ("GDF", "Im"):
                op_color = C.HARVEST
            elif opcode in ("JX", "JS"):
                op_color = C.MAGENTA

            logger.info(
                f"{dir_color}[{dir_label}]{C.RESET} "
                f"{op_color}{C.BOLD}{opcode}{C.RESET} "
                f"{C.DIM}({name}){C.RESET} "
                f"{C.MUTED}│{C.RESET} {data_str[:120]}"
            )
        else:
            logger.debug(
                f"{dir_color}[{dir_label}]{C.RESET} "
                f"{C.BOLD}{opcode}{C.RESET} "
                f"{C.MUTED}│ {data_str[:60]}{C.RESET}"
            )

        packet = ParsedPacket(opcode=opcode, name=name,
                              direction=direction, raw=line, data={})
        try:
            packet.data = self._parse_data(opcode, data_str)
        except Exception as e:
            logger.warning(f"{C.YELLOW}Erreur parse {opcode}: {e}{C.RESET}")

        return packet

    def _parse_data(self, opcode: str, data: str) -> dict:

        if opcode == "GCK":
            parts = data.lstrip("|").split("|")
            return {"character": parts[1] if len(parts) > 1 else ""}

        elif opcode == "GM":
            return self._parse_gm(data)

        elif opcode == "GA":
            return self._parse_ga(data)

        elif opcode == "JS":
            return self._parse_js(data)

        elif opcode == "JX":
            return self._parse_jx(data)

        elif opcode == "GDF":
            # GDF|element_id;?;?
            parts = data.lstrip("|").split(";")
            return {"element_id": self._int(parts[0])}

        elif opcode == "As":
            parts = data.split(",", 1)
            return {"cell_id": self._int(parts[0])}

        elif opcode == "Im":
            parts = data.split(";", 1)
            return {
                "code": self._int(parts[0]),
                "message": parts[1] if len(parts) > 1 else ""
            }

        elif opcode == "Rx":
            return {}

        elif opcode == "ZS":
            return {"state": data}

        return {"raw": data[:80]}

    def _parse_gm(self, data: str) -> dict:
        actors = []
        resources = []
        raw = data.lstrip("|")

        for entry in raw.split("^"):
            if not entry:
                continue
            sign = "+"
            if entry[0] in "+-":
                sign = entry[0]
                entry = entry[1:]

            parts = entry.split(";")
            if len(parts) < 2:
                continue

            try:
                cell_id = self._int(parts[0])
                val = self._int(parts[1])

                actor = {"cell_id": cell_id, "type": val, "sign": sign}
                if len(parts) > 3:
                    actor["name"] = parts[3] if len(parts) > 3 else ""
                    actor["level"] = self._int(parts[4]) if len(parts) > 4 else 0
                actors.append(actor)
            except Exception:
                pass

        return {"actors": actors, "resources": resources}

    def _parse_ga(self, data: str) -> dict:
        """
        GA0;action_id;actor_id;extra
        action_id=1   → déplacement (chemin encodé)
        action_id=500 → récolte (envoi client: GA500element_id;skill_id)
        action_id=501 → récolte confirmée serveur
        """
        raw = data.lstrip("0")
        parts = raw.split(";")

        if len(parts) < 2:
            return {"raw": data}

        action_id = self._int(parts[0])
        actor_id  = self._int(parts[1]) if len(parts) > 1 else 0
        extra     = parts[2] if len(parts) > 2 else ""

        result = {"action_id": action_id, "actor_id": actor_id, "extra": extra}

        if action_id == 1 and extra:
            cells = decode_cell_path(extra)
            result["path"] = cells
            result["destination"] = cells[-1] if cells else None
            logger.info(
                f"{C.MOVE}{C.BOLD}→ Déplacement{C.RESET} "
                f"acteur {C.BOLD}{actor_id}{C.RESET} "
                f"chemin={cells} "
                f"{C.GREEN}dest={result['destination']}{C.RESET}"
            )

        elif action_id == 500:
            # Client → récolte: GA500element_id;skill_id
            extra_parts = extra.split(";")
            result["element_id"] = self._int(extra_parts[0])
            result["skill_id"]   = self._int(extra_parts[1]) if len(extra_parts) > 1 else 0
            logger.info(
                f"{C.HARVEST}{C.BOLD}⚒ Récolte envoyée{C.RESET} "
                f"elem={result['element_id']} skill={result['skill_id']}"
            )

        elif action_id == 501:
            # Serveur → récolte confirmée: GA0;501;actor_id;element_id,cell_id
            extra_parts = extra.split(",")
            result["element_id"] = self._int(extra_parts[0])
            result["cell_id"]    = self._int(extra_parts[1]) if len(extra_parts) > 1 else 0
            logger.info(
                f"{C.HARVEST}{C.BOLD}✓ Récolte confirmée{C.RESET} "
                f"elem={result['element_id']} cell={result['cell_id']}"
            )

        return result

    def _parse_js(self, data: str) -> dict:
        """JS|job_id;level~elem_id~?~?~cell_id,..."""
        elements = []
        raw = data.lstrip("|")

        for job_entry in raw.split("|"):
            if ";" not in job_entry:
                continue
            job_parts = job_entry.split(";", 1)
            job_id = self._int(job_parts[0])
            skills_raw = job_parts[1] if len(job_parts) > 1 else ""

            for skill_group in skills_raw.split(","):
                parts = skill_group.split("~")
                if len(parts) >= 5:
                    element_id = self._int(parts[0])
                    cell_id    = self._int(parts[4])
                    if cell_id > 0:
                        elements.append({
                            "element_id": element_id,
                            "cell_id": cell_id,
                            "available": True,
                            "job_id": job_id,
                        })

        logger.info(
            f"{C.MAGENTA}{C.BOLD}JS{C.RESET} "
            f"{C.GREEN}{len(elements)} ressources détectées{C.RESET}"
        )
        return {"elements": elements, "count": len(elements)}

    def _parse_jx(self, data: str) -> dict:
        """
        JX|job_id;level;cell1;cell2;cell3;...
        Ressources disponibles sur la map courante.
        """
        elements = []
        raw = data.lstrip("|")

        for job_entry in raw.split("|"):
            parts = job_entry.split(";")
            if len(parts) < 3:
                continue
            job_id = self._int(parts[0])
            # parts[1] = level, parts[2:] = cell_ids
            for cell_str in parts[2:]:
                cell_id = self._int(cell_str)
                if cell_id > 0:
                    elements.append({
                        "element_id": 0,   # JX ne donne pas l'element_id
                        "cell_id": cell_id,
                        "available": True,
                        "job_id": job_id,
                    })

        logger.info(
            f"{C.MAGENTA}{C.BOLD}JX{C.RESET} "
            f"{C.GREEN}{len(elements)} ressources map{C.RESET} "
            f"→ cells {[e['cell_id'] for e in elements]}"
        )
        return {"elements": elements, "count": len(elements)}

    def _int(self, s: str) -> int:
        try:
            return int(str(s).strip())
        except (ValueError, AttributeError):
            return 0
