# AMPTemplates-Rust — Rust (TeamKit) pour AMP

Template **AMP Generic Module** pour serveur dédié Rust, créé pour remplacer le module Rust
intégré d'AMP dont la détection des joueurs est cassée (Facepunch a changé le format des logs).

## Ce que ce template apporte par rapport au module officiel

- ✅ **Détection des joueurs fonctionnelle** : pseudos + SteamID dans AMP (regex au format de logs actuel)
- ✅ **Admins gérés depuis AMP** : champs Propriétaires/Modérateurs (SteamID64 séparés par des virgules)
  → `users.cfg` régénéré **à chaque démarrage**
- ✅ **Oxide / uMod en un clic** : case à cocher → la dernière version est téléchargée et installée
  à chaque Update (plugins à déposer dans `rust/258550/oxide/plugins/`)
- ✅ Toutes les options courantes en français : nom, description, site, image, tags, taille de map,
  seed, map custom, intervalle de sauvegarde, Rust+...
- ✅ Compatible conteneurs AMP (binaire Linux natif, image ampbase standard)

## Limite connue (à connaître avant de choisir)

Le module Generic ne parle pas le WebRCON (websocket) : la console AMP est **en lecture seule**
(logs, chat, connexions). Pour envoyer des commandes : console **F1 en jeu** (avec les droits
ownerid gérés par ce template), ou un client RCON externe (RustAdmin, webrcon.io) via le port
RCON et le mot de passe du champ 30.

## Installation

1. ADS → Configuration → Instance Deployment → Configuration Repositories → ajouter
   `hydrocut/AMPTemplates-Rust:main` → **Fetch Latest**
2. **Create Instance → Rust (TeamKit)**
3. Configurer (champ 30 mot de passe RCON obligatoire, champ 31 tes SteamID64 admins,
   cocher Oxide si besoin) → **Update** (≈ 8-10 Go via SteamCMD) → **Start**
4. Premier démarrage : génération de la map, plusieurs minutes à fort CPU — les ports de jeu
   ne s'ouvrent qu'à la fin (« Server startup complete » dans la console)

## Wipe

- **Wipe map** : changer la seed (champ 21) puis supprimer `server/<identité>/*.map` et `*.sav`
  via le File Manager (ou changer d'identité pour repartir totalement à neuf)
- **Wipe complet (BP)** : supprimer aussi `player.blueprints.*.db`

## Mises à jour du jeu

Cliquer **Update** : SteamCMD met à jour Rust, puis Oxide est automatiquement re-téléchargé et
re-patché par-dessus (si activé). À faire après chaque update forcé de Facepunch (jeudis de wipe).

Template par TeamKit.fr — [teamkit.fr](https://www.teamkit.fr)
