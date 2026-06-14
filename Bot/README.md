# Dofus 1.29 — Socket Farmer Bot

Bot de récolte automatique pour serveurs privés Dofus 1.29, basé sur l'analyse du trafic réseau via proxy MITM.

## Architecture

```
Client Dofus → [Proxy MITM :5555] → Serveur Dofus
                      ↓
              [Parseur de trames]
                      ↓
         [GameState] + [FarmerBot]
                      ↓
           [Interface Web :8080]
```

## Installation

```bash
pip install -r requirements.txt
```

## Démarrage

```bash
# Serveur privé local (ex: Dofus Retro)
python main.py --host 127.0.0.1 --port 5555

# Serveur distant
python main.py --host IP_SERVEUR --port 5555

# Mode debug (log tous les paquets)
python main.py --host IP_SERVEUR --debug
```

Puis ouvre **http://127.0.0.1:8080** dans ton navigateur.

## Configuration du client Dofus

### Option 1 — /etc/hosts (Linux/Mac)
```bash
# Ajouter dans /etc/hosts :
127.0.0.1   ip-serveur-dofus.com
```

### Option 2 — dofus.cfg
Modifie `dofus.cfg` dans le dossier du client :
```
server=127.0.0.1
port=5555
```

### Option 3 — Proxy réseau
Configure un proxy TCP (ex: `socat` ou `iptables`) pour rediriger le port 5555.

## Opcodes Dofus 1.29 utilisés

| Opcode | Nom | Rôle |
|--------|-----|------|
| `0x00B4` | GameMapDataMessage | Chargement d'une map |
| `0x00EA` | StatedMapObjectListMessage | Liste des ressources |
| `0x00E9` | StatedObjectUpdatedMessage | Pop/dépop d'une ressource |
| `0x00EE` | InteractiveElementUpdatedMessage | Changement d'état arbre/minerai |
| `0x00A6` | GameRolePlayActorPositionMessage | Position du joueur |
| `0x00AD` | GameMapMovementRequestMessage | *(injection)* Déplacement |
| `0x00EF` | InteractiveUseRequestMessage | *(injection)* Récolte |

## Structure des fichiers

```
dofus-farmer/
├── main.py              # Point d'entrée
├── requirements.txt
├── core/
│   ├── proxy.py         # Proxy MITM TCP
│   ├── parser.py        # Décodage des paquets Dofus 1.29
│   ├── game_state.py    # État de la map, ressources, joueur
│   └── bot.py           # Logique de farming automatique
└── web/
    ├── server.py         # Flask + WebSocket
    └── templates/
        └── index.html   # Interface de visualisation
```

## Ajuster pour votre métier

Dans `core/bot.py`, ligne `skill_id = 2`, changez selon :

| Métier | skill_id |
|--------|----------|
| Bûcheron | 6 |
| Mineur | 4 |
| Alchimiste | 3 |
| Paysan |  |
| Pêcheur | 9 |

## Notes importantes

- Testé sur protocole Dofus 1.29 (serveurs privés Retro)
- Les offsets des opcodes peuvent varier légèrement selon les versions de serveur
- Utilisez `--debug` pour capturer et ajuster les opcodes de votre serveur
- Le bot inclut des délais aléatoires pour l'humanisation des actions

## Disclaimer

Ce projet est à des fins éducatives (analyse de protocoles réseau).
L'utilisation de bots est interdite sur les serveurs officiels Ankama.
