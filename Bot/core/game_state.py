"""
GameState - État courant du jeu
Maintient la carte, les ressources et la position du joueur en temps réel
"""
import time
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# Dofus 1.29 : map 14 colonnes × 20 lignes = 280 cellules
MAP_WIDTH = 14
MAP_HEIGHT = 20
MAP_SIZE = MAP_WIDTH * MAP_HEIGHT  # 280


@dataclass
class Resource:
    cell_id: int
    element_id: int
    available: bool = True
    last_harvested: Optional[float] = None
    harvest_count: int = 0

    @property
    def cell_x(self) -> int:
        return self.cell_id % MAP_WIDTH

    @property
    def cell_y(self) -> int:
        return self.cell_id // MAP_WIDTH

    @property
    def respawn_eta(self) -> Optional[float]:
        """Estimation du respawn en secondes (Dofus 1.29 : ~10 min par défaut)"""
        if self.available or self.last_harvested is None:
            return None
        elapsed = time.time() - self.last_harvested
        respawn_delay = 600  # 10 minutes
        remaining = respawn_delay - elapsed
        return max(0, remaining)

    def to_dict(self) -> dict:
        return {
            "cell_id": self.cell_id,
            "element_id": self.element_id,
            "cell_x": self.cell_x,
            "cell_y": self.cell_y,
            "available": self.available,
            "harvest_count": self.harvest_count,
            "respawn_eta": self.respawn_eta,
            "last_harvested": self.last_harvested,
        }


@dataclass
class GameState:
    map_id: int = 0
    player_cell_id: int = 0
    player_id: int = 0
    resources: Dict[int, Resource] = field(default_factory=dict)  # cell_id → Resource
    total_harvested: int = 0
    session_start: float = field(default_factory=time.time)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def player_x(self) -> int:
        return self.player_cell_id % MAP_WIDTH

    @property
    def player_y(self) -> int:
        return self.player_cell_id // MAP_WIDTH

    @property
    def available_resources(self) -> List[Resource]:
        with self._lock:
            return [r for r in self.resources.values() if r.available]

    @property
    def session_duration(self) -> float:
        return time.time() - self.session_start

    def set_map(self, map_id: int):
        with self._lock:
            if self.map_id != map_id:
                self.map_id = map_id
                self.resources.clear()

    def set_resources_from_list(self, objects: list):
        """Charge la liste complète des ressources de la map (0x00EA)"""
        with self._lock:
            new_resources = {}
            for obj in objects:
                cell_id = obj["cell_id"]
                # Préserve les données de récolte si la ressource existait
                existing = self.resources.get(cell_id)
                r = Resource(
                    cell_id=cell_id,
                    element_id=obj["element_id"],
                    available=obj["available"],
                    last_harvested=existing.last_harvested if existing else None,
                    harvest_count=existing.harvest_count if existing else 0,
                )
                new_resources[cell_id] = r
            self.resources = new_resources

    def update_resource(self, cell_id: int, element_id: int, available: bool):
        """Mise à jour d'une ressource (0x00E9, 0x00EE)"""
        with self._lock:
            r = self.resources.get(cell_id)
            if r is None:
                r = Resource(cell_id=cell_id, element_id=element_id, available=available)
                self.resources[cell_id] = r
            else:
                was_available = r.available
                r.available = available
                r.element_id = element_id
                # Si la ressource vient d'être récoltée
                if was_available and not available:
                    r.last_harvested = time.time()
                    r.harvest_count += 1
                    self.total_harvested += 1

    def set_player_position(self, actor_id: int, cell_id: int):
        with self._lock:
            if self.player_id == 0 or self.player_id == actor_id:
                self.player_id = actor_id
                self.player_cell_id = cell_id

    def set_player_id(self, player_id: int):
        with self._lock:
            self.player_id = player_id

    def get_nearest_available(self, from_cell: Optional[int] = None) -> Optional[Resource]:
        """Trouve la ressource disponible la plus proche du joueur"""
        origin = from_cell if from_cell is not None else self.player_cell_id
        origin_x = origin % MAP_WIDTH
        origin_y = origin // MAP_WIDTH

        available = self.available_resources
        if not available:
            return None

        def dist(r: Resource):
            return abs(r.cell_x - origin_x) + abs(r.cell_y - origin_y)

        return min(available, key=dist)

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "map_id": self.map_id,
                "player_cell_id": self.player_cell_id,
                "player_x": self.player_x,
                "player_y": self.player_y,
                "player_id": self.player_id,
                "resources": [r.to_dict() for r in self.resources.values()],
                "total_harvested": self.total_harvested,
                "available_count": len(self.available_resources),
                "session_duration": round(self.session_duration),
            }
