# AMPTemplates-Rust — Rust (TeamKit) pour AMP, version 8

Modèle **AMP Generic** pour un serveur dédié Rust, pensé pour un gros serveur PvP
(150 places et plus). Il remplace le module Rust intégré d'AMP, dont la détection des joueurs
est cassée depuis que Facepunch a changé le format de ses journaux.

## Ce que le modèle fait

- **Joueurs visibles dans AMP** : pseudo et SteamID, au format des journaux actuels.
- **Console d'AMP reliée au serveur** : ce que tu tapes (`say`, `status`, `kick`,
  `oxide.reload`...) part au serveur par son RCON local, et la réponse s'affiche.
- **Mot de passe RCON obligatoire** : le serveur refuse de démarrer s'il est vide, trop court
  ou laissé par défaut. Il passe par un fichier de réglages, plus par la ligne de commande d'AMP.
- **Arrêt propre** : le bouton Stop d'AMP envoie `server.save` puis `quit` par RCON avant de
  fermer le serveur. Si le RCON ne répond pas, le serveur reçoit SIGTERM.
- **Wipe réglable, avec archive** : au prochain démarrage, ou tout seul selon le rythme choisi.
- **Plugins uMod en cases à cocher**, avec un groupe « PvP compétitif », et la possibilité de
  les figer pendant une saison.
- **Admins gérés depuis AMP** : champs Propriétaires et Modérateurs, `users.cfg` réécrit à
  chaque démarrage (les bannissements, dans `bans.cfg`, ne sont pas touchés).
- Rust+ (adresse publique), logo, taille d'équipe, tickrate, limite de FPS, EAC imposé
  (`server.secure true`, aucun réglage ne le coupe).

## Installation

1. ADS → Configuration → Instance Deployment → Configuration Repositories :
   `hydrocut/AMPTemplates-Rust:main` → **Fetch Latest**.
2. **Create Instance → Rust (TeamKit) - recommandé**.
3. Configuration → **Admins & RCON** : un mot de passe RCON de 12 caractères ou plus
   (lettres sans accent, chiffres, point, tiret), tes SteamID64 en propriétaires.
4. Configuration → **Serveur** : « Adresse publique (Rust+) » = l'adresse IP publique de la machine.
5. **Update** (environ 10 Go par SteamCMD, puis Oxide, puis le lanceur TeamKit), puis **Start**.
6. Premier démarrage : la carte est générée, plusieurs minutes à fort processeur. Les ports de
   jeu ne s'ouvrent qu'à la fin (« Server startup complete »). Rust écrit **toujours**
   « Generating procedural map », même quand il recharge une sauvegarde : c'est
   « Couldn't load server/<identité>/proceduralmap…sav - file doesn't exist » qui dit « monde neuf »,
   et « Spawning N entities from save » qui dit « monde rechargé » (le lanceur l'écrit en clair).

**Une instance existante ne reçoit pas cette version** : ADS ne recopie pas `App.*` sur une
instance déjà créée. Il faut recréer l'instance, ou recopier `App.*` et lancer Update.

## Ports

| Port | Rôle | Ouvert au public ? |
|---|---|---|
| 28015/UDP | jeu | oui |
| 28016/UDP | requêtes de la liste des serveurs | oui |
| 28017/TCP | RCON | **non** : seulement depuis la machine (bot, RustAdmin par tunnel SSH). La console d'AMP passe par lui en local |
| 28082/TCP | Rust+ | oui |

Le pare-feu n'est pas fait par le modèle : fermer 28017 sur la machine (IPv4 et IPv6, et la
chaîne DOCKER-USER si l'instance est en réseau ponté).

## Le mot de passe RCON

Il n'est **ni dans la ligne de commande d'AMP, ni dans une étape de démarrage**. AMP l'écrit dans
`rust/258550/reglages-rust.txt` (droits 600, remis à chaque démarrage), et le lanceur le lit.

Limite qu'on ne peut pas contourner : **Rust ne lit son mot de passe RCON que sur sa propre ligne
de commande** (`+rcon.password`), il ne le prend pas dans `server.cfg`. Il reste donc visible dans
la liste des processus de la machine, pour quelqu'un qui a déjà un accès SSH. Ce qui protège
vraiment : le port 28017 fermé au public et un mot de passe long. Les utilisateurs qui ont accès
au gestionnaire de fichiers de l'instance dans AMP peuvent aussi lire `reglages-rust.txt`.

## Le wipe

Trois façons de le déclencher. Toutes font la même chose, **au démarrage**, avant de lancer le jeu :

1. archive datée de `server/<identité>/` dans `rust/archives-wipe/`
   (`teamkit-2026-12-03_20h05-plans.tar.gz`), seules les N dernières sont gardées (4 par défaut).
   **Sans archive, rien n'est effacé** (par exemple si le disque est plein) : le serveur démarre
   sur l'ancien monde et le dit dans la console ;
2. effacement de la carte : `*.map`, `*.sav`, `*.sav.*`, `player.states.*.db*`, `sv.files.*.db*` ;
   avec les plans : aussi `player.blueprints.*`. `cfg/`, `player.tokens.db` (jumelages Rust+) et
   les données des plugins (`oxide/data`) sont gardés ;
3. nouvelle graine si « Graine de la carte » vaut `auto` : tirée dans « Graines vérifiées » si la
   liste est remplie (jamais la même que la carte qui s'en va), sinon au hasard.

### 1. « Wipe au prochain démarrage » (non / carte / carte + plans)

Fait **une fois**. AMP ne sait pas remettre un réglage sur « non » tout seul : le serveur note
la valeur dans `server/<identité>/teamkit-wipe.json` (le témoin) et ne refait pas le wipe tant
que le réglage n'est pas repassé par « non ». Remets-le sur « non » après le wipe.

### 2. « Wipe automatique » selon le rythme

| Rythme | Jours | Tag du serveur | `{wipe}` dans le nom |
|---|---|---|---|
| Chaque semaine (défaut) | jeudi | weekly | Wipe jeudi |
| Toutes les 2 semaines | 1er et 3e jeudi du mois | biweekly | Wipe 1 jeudi sur 2 |
| Chaque mois | 1er jeudi | monthly | Wipe mensuel |
| 2 fois par semaine | lundi et jeudi | weekly | Wipe lundi + jeudi |

Le wipe est fait au **premier démarrage après l'heure du wipe** (heure de Paris, 20:00 par
défaut ; jusqu'à 15 minutes d'avance acceptées), **dans les 6 heures** qui suivent. Un redémarrage
plus tard ne wipe pas : un serveur relancé le vendredi pour une panne garde son monde. Le témoin
retient le wipe fait, un second démarrage le même soir ne refait rien.
« Carte, et plans au wipe forcé du mois » efface les plans le 1er jeudi du mois (wipe forcé de
Facepunch). Quand on active le wipe automatique sur un monde qui existe déjà, le premier wipe
sera le suivant du calendrier, jamais tout de suite.

**Le planificateur d'AMP ne sait pas changer un réglage**, et il n'en a pas besoin : il suffit
qu'il redémarre le serveur à l'heure. Dans l'instance → Schedule :

- tous les jeudis (et lundis pour « 2 fois par semaine ») à 19:00, 19:30, 19:50, 19:55, 19:59 :
  *Send a console message* `say Wipe dans 60 minutes` (puis 30, 10, 5, 1) ;
- tous les jeudis à 20:00 : *Update the application* (arrêt propre, mise à jour de Rust et
  d'Oxide, puis redémarrage, qui fait le wipe). Si AMP ne relance pas le serveur après
  l'Update, ajouter *Start the application* juste après.

Le jour du wipe forcé, Facepunch publie sa mise à jour vers 20 h en hiver (21 h en été) et Oxide
suit parfois avec du retard : relancer Update une fois Oxide disponible (la fenêtre de 6 h laisse
le temps).

### 3. Une demande posée par le site, le bot ou à la main

Dans la console d'AMP (ou par l'API d'AMP, *Core/SendConsoleMessage*) :

- `teamkit.wipe carte` ou `teamkit.wipe plans` : wipe au prochain démarrage ;
- `teamkit.wipe annuler` : retire la demande ;
- `teamkit.periode` : wipe en cours et prochain wipe, en JSON.

La demande est le fichier `rust/258550/wipe-demande.txt` (contenu `carte` ou `plans`), retiré
une fois le wipe fait. Il peut aussi être déposé avec le gestionnaire de fichiers.

## Plugins

- **Installer Oxide** (coché par défaut) : Oxide est réinstallé à chaque Update (la mise à jour
  de Rust l'efface) et au démarrage s'il manque.
- **Mettre à jour les plugins au démarrage** (oui par défaut) : non = saison figée, les plugins
  restent tels quels, seuls les manquants sont téléchargés. Oxide, lui, suit forcément la
  version du jeu : pendant une saison, ne cliquer sur Update qu'au wipe.
- **PvP compétitif** (cochés par défaut, noms vérifiés sur umod.org le 17/10/2026) : Clans,
  Better Chat, No Escape, Wipe Protection, Raid Tracker, Server Armour, CombatLog Info, Duelist,
  Death Notes, Crafting Controller, Furnace Splitter, BGrade, Auto Doors, Remover Tool,
  Zone Manager, Copy Paste, Vanish, Admin Radar, Player Administration, Image Library.
  Simple Kill Feed en option (décoché).
- **Autres plugins** (décochés) et **Anciens plugins** marqués « (ancien) » : contraires au
  « vanilla+ », hors sujet en PvP, boutique en jeu ou abandonnés.
- Une case **décochée** met le plugin de côté dans `oxide/plugins-desactives/` au démarrage.
- Les réglages de chaque plugin sont dans `oxide/config/` : à faire une fois (Clans limité à 4
  sans alliance, Remover Tool limité à ses propres pièces, Gather Manager à ×2...).
- Les permissions Oxide ne sont pas les droits d'admin du serveur : `oxide.grant group admin <perm>`.
- **Plugins au prochain wipe** : la page « 🔄 Plugins Rust » du panel TeamKit dépose une version
  préparée dans `oxide/plugins-au-prochain-wipe/<Nom>.cs`. Au wipe seulement, avant le démarrage, le
  lanceur la met en service dans `oxide/plugins/` et garde l'ancienne dans
  `oxide/plugins-precedents/<Nom>.cs.<AAAAMMJJ-HHMMSS>` (un nom autre que `<Nom>.cs`, ou un fichier
  qui n'est pas un plugin Oxide, reste où il est). À utiliser avec « Mettre à jour les plugins au
  démarrage » sur **Non**, sinon uMod remplace aussitôt la version posée par sa dernière.
- `oxide/data/copypaste/` est créé s'il manque : sans lui, Copy Paste plante (`copylist`, `paste`).
- **Wipe Protection** compte ses 3 h depuis la création de la carte ; si sa config arrive après,
  il garde 24 h. Après chaque wipe, le lanceur envoie donc `wp start` par RCON dès que le serveur
  est prêt (à taper soi-même si le message dit « non transmis »).

## Le lanceur

`rust/258550/lancer-rust.py` (Python 3, sans bibliothèque en plus), posé par l'Update.
`python3 lancer-rust.py rcon status` envoie une commande au serveur qui tourne, depuis la machine.
Les codes couleur ANSI de RustDedicated (`[37m`…) sont retirés de chaque ligne avant de la
relayer à AMP : les regex de la console (prêt, arrivées, départs) et nos repères les voient en clair.
Après `quit`, RustDedicated rend -1 : quand l'arrêt a été demandé et la sauvegarde vue
(« Saved … ents »), le lanceur sort en 0, pour qu'AMP ne croie pas à un plantage.
Le mot de passe RCON passe par `+rcon.password` (obligatoire : écrit dans server.cfg, il n'est
pas pris et Rust répond « RCON is disabled ») ; Rust le masque dans son journal.

## Vérifier avant de pousser

`python verifier.py` contrôle le modèle et fait un essai complet dans un dossier factice (faux
serveur Rust avec un vrai RCON, faux téléchargements). Il doit finir par « Tout est bon. ».
Un push sur `main` est une mise en production : ADS tire ce dépôt.

Serveur communautaire non officiel, non affilié à Facepunch Studios. Modèle par TeamKit.fr —
[teamkit.fr](https://www.teamkit.fr)
