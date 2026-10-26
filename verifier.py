# -*- coding: utf-8 -*-
"""
Contrôle du modèle « Rust (TeamKit) » avant de le pousser (même rôle que ceux de DDNet,
ClassiCube, Armagetron). Un push sur GitHub = mise en production dans ADS : rien ne part
tant que ce script ne dit pas « Tout est bon. ».

    python verifier.py

1. tous les JSON se parsent ; le .kvp ne référence que des fichiers présents ; ports,
   bloc Console.* / Limits.* complet ; version 8, sortie de Beta, « recommandé », ids inchangés ;
2. secret : le mot de passe RCON n'est ni dans la ligne de commande d'AMP, ni dans une étape
   de démarrage, ni dans un champ envoyé en ligne de commande ;
3. chaque réglage exposé dans AMP est écrit UNE fois dans scripts/AMP_reglages-rust.txt et lu
   par le lanceur ; valeurs par défaut du cahier (150 places, carte 4000, équipe 4...) ;
4. aller-retour : modèle rendu comme AMP -> lu par le lanceur -> mêmes valeurs ; champs vides
   SAUTÉS (aucune convar vide, qui deviendrait « 1 ») ; regex de la console sur de vraies lignes ;
5. calendrier des wipes (heure de Paris, été/hiver, wipe forcé du 1er jeudi) et décisions ;
6. fichiers livrés : URL du dépôt, fins de ligne LF, compilation Python ;
7. ESSAI DE BOUT EN BOUT dans un dossier factice : vrai lanceur, faux RustDedicated avec un
   vrai RCON WebSocket, faux curl. Archive faite, bons fichiers effacés, rotation des archives,
   mot de passe absent de la ligne de commande d'AMP, refus de démarrer, arrêt propre
   (server.save puis quit), console d'AMP relayée par RCON.
"""
import datetime as dt
import importlib.util
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time

ICI = os.path.dirname(os.path.abspath(__file__))
NOM = 'rustteamkit'
LANCEUR = os.path.join('scripts', 'lancer-rust.py')
MODELE = os.path.join('scripts', 'AMP_reglages-rust.txt')
DEPOT_RAW = 'https://raw.githubusercontent.com/hydrocut/AMPTemplates-Rust/main/'
APP_CONFIG_ID = '36b0971a-67f7-4e19-86cc-731858e5a6c6'      # v7 : ne JAMAIS changer
MANIFEST_ID = 'bf9f13ff-c8ea-4056-8a10-7c1ad63c9a14'
# noms de fichier vérifiés sur https://umod.org/plugins/<Nom>.cs (curl, 17/10/2026)
PVP = ['Clans', 'BetterChat', 'NoEscape', 'WipeProtection', 'RaidTracker', 'ServerArmour',
       'CombatLogInfo', 'Duelist', 'DeathNotes', 'CraftingController', 'FurnaceSplitter', 'BGrade',
       'AutoDoors', 'RemoverTool', 'ZoneManager', 'CopyPaste', 'Vanish', 'AdminRadar',
       'PlayerAdministration', 'ImageLibrary']
MDP_ESSAI = 'Essai-Rcon.2026xyz'      # mot de passe factice des essais, jamais un vrai
soucis = []


def ok(texte):
    print('ok   ' + texte)


def non(texte):
    soucis.append(texte)
    print('NON  ' + texte)


def lire(nom):
    with io.open(os.path.join(ICI, nom), encoding='utf-8') as f:
        return f.read()


# ── 1. JSON, .kvp, ports ─────────────────────────────────────────────────────────────────
data = {}
for nom in ('manifest.json', NOM + 'config.json', NOM + 'metaconfig.json', NOM + 'ports.json',
            NOM + 'updates.json'):
    try:
        data[nom] = json.loads(lire(nom))
        ok('%-26s JSON valide' % nom)
    except Exception as e:
        non('%s : JSON invalide (%s)' % (nom, e))
if soucis:
    sys.exit(1)

kvp = lire(NOM + '.kvp')
K = dict(re.findall(r'^([A-Za-z]+\.[A-Za-z]+)=(.*?)\r?$', kvp, re.M))
for ref in re.findall(r'@IncludeJson\[([^\]]+)\]', kvp):
    (ok if os.path.isfile(os.path.join(ICI, ref)) else non)('@IncludeJson[%s]' % ref)
for cle in ('ConfigManifest', 'MetaConfigManifest'):
    (ok if os.path.isfile(os.path.join(ICI, K.get('Meta.' + cle, '?'))) else non)(
        'Meta.%s = %s' % (cle, K.get('Meta.' + cle)))

attendu = {'Meta.ConfigVersion': '8', 'Meta.ConfigReleaseState': 'NotSpecified',
           'Meta.AppConfigId': APP_CONFIG_ID, 'App.ExitMethod': 'String', 'App.ExitString': 'quit',
           'App.HasWriteableConsole': 'True', 'App.MonitorChildProcessName': 'RustDedicated',
           'App.ExecutableLinux': '/usr/bin/python3', 'App.MaxUsers': '150', 'App.PreStartStages': '[]'}
for cle, v in attendu.items():
    (ok if K.get(cle) == v else non)('%s = %s (attendu %s)' % (cle, K.get(cle), v))
(ok if 'recommandé' in K.get('Meta.DisplayName', '') else non)('nom affiché : %s' % K.get('Meta.DisplayName'))
(ok if re.search(r'\b(serveur|joueurs|sauvegarde)\b', K.get('Meta.Description', '')) else non)(
    'description en français')
(ok if 'python3' in K.get('Meta.ExtraContainerPackages', '') and 'curl' in K.get('Meta.ExtraContainerPackages', '')
 else non)('paquets du conteneur : %s' % K.get('Meta.ExtraContainerPackages'))
(ok if data['manifest.json'].get('id') == MANIFEST_ID else non)('id du manifest inchangé')
if int(K.get('App.ExitTimeout', '0')) < 180:
    non('App.ExitTimeout trop court pour sauvegarder une grande carte')

refs = {p['Ref'] for p in data[NOM + 'ports.json']}
for cle in ('AdminPortRef', 'PrimaryApplicationPortRef', 'UniversalSleepApplicationUDPPortRef',
            'UniversalSleepSteamQueryPortRef'):
    (ok if K.get('App.' + cle) in refs else non)('App.%s = %s' % (cle, K.get('App.' + cle)))
rcon_port = [p for p in data[NOM + 'ports.json'] if p['Ref'] == 'RconPort']
(ok if rcon_port and 'FERMER AU PUBLIC' in rcon_port[0]['Description'] else non)(
    'port RCON documenté « à fermer au public »')

ATTENDUES = ['App.SupportsUniversalSleep', 'App.WakeupMode', 'App.ApplicationReadyMode',
             'Console.FilterMatchRegex', 'Console.FilterMatchReplacement', 'Console.ThrowawayMessageRegex',
             'Console.AppReadyRegex', 'Console.UserJoinRegex', 'Console.UserLeaveRegex', 'Console.UserChatRegex',
             'Console.UpdateAvailableRegex', 'Console.PreConnectRegex', 'Console.ConnectIPRegex',
             'Console.MetricsRegex', 'Console.HideFromConsoleRegex', 'Console.SuppressLogAtStart',
             'Console.UserActions', 'Limits.SleepMode', 'Limits.SleepOnStart', 'Limits.SleepDelayMinutes',
             'Limits.DozeDelay', 'Limits.AutoRetryCount', 'Limits.SleepStartThresholdSeconds']
absentes = [c for c in ATTENDUES if c not in K]
(non if absentes else ok)('kvp complet' + (' : manque %s' % ', '.join(absentes) if absentes else ''))

# ── 2. le secret ne passe jamais par une ligne de commande d'AMP ─────────────────────────
config = data[NOM + 'config.json']
ligne = K.get('App.CommandLineArgs', '')
for interdit in ('RconPassword', 'rcon.password', 'FormattedArgs'):
    (non if interdit in ligne else ok)('ligne de commande d\'AMP sans « %s »' % interdit)
for etape in json.loads(K.get('App.PreStartStages', '[]')) + data[NOM + 'updates.json']:
    if 'RconPassword' in json.dumps(etape):
        non('étape « %s » : le mot de passe passe par sa ligne de commande' % etape.get('UpdateStageName'))
ok('aucune étape de démarrage ni d\'Update ne reçoit le mot de passe')
for c in config:
    if c.get('IncludeInCommandLine'):
        non('%s envoyé en ligne de commande par AMP (IncludeInCommandLine)' % c['FieldName'])
    if c.get('SkipIfEmpty') and c['FieldName'] != 'CustomServerArgs':
        non('%s : SkipIfEmpty sur un champ du fichier (sans effet, le lanceur saute les vides)' % c['FieldName'])
mdp_champ = [c for c in config if c['FieldName'] == 'RconPassword']
(ok if mdp_champ and mdp_champ[0]['InputType'] == 'password' and mdp_champ[0]['DefaultValue'] == ''
 else non)('mot de passe RCON : champ masqué, vide par défaut (refus de démarrer tant qu\'il est vide)')
for jeton in ('{{$GamePort}}', '{{$QueryPort}}', '{{$RconPort}}', '{{$RustPlusPort}}', '{{$MaxUsers}}',
              'lancer-rust.py', '{{CustomServerArgs}}'):
    (ok if jeton in ligne else non)('ligne de commande : %s' % jeton)

# ── 3. champs <-> modèle <-> lanceur ; valeurs par défaut ────────────────────────────────
modele = lire(MODELE)
code = lire(LANCEUR)
lignes_modele = [l for l in modele.split('\n') if l and not l.startswith('#')]
cles_modele = [l.split('=', 1)[0] for l in lignes_modele]
noms = [c['FieldName'] for c in config]
doubles = sorted({n for n in noms if noms.count(n) > 1})
(non if doubles else ok)('champs sans doublon' + (' : %s' % doubles if doubles else ''))
hors_fichier = {'$MaxUsers', 'CustomServerArgs'}
for c in config:
    n = c['FieldName']
    if c.get('ParamFieldName') != n:
        non('%s : ParamFieldName %s différent (le modèle de réglages utilise le nom du champ)'
            % (n, c.get('ParamFieldName')))
    if n in hors_fichier:
        continue
    if lignes_modele.count('%s={{%s}}' % (n, n)) != 1:
        non('%s absent (ou en double) de %s' % (n, MODELE))
    elif not n.startswith('UmodP_') and ("'%s'" % n) not in code:
        non('%s écrit dans le fichier mais jamais lu par le lanceur' % n)
orphelines = [k for k in cles_modele if k not in noms]
(non if orphelines else ok)('%d lignes dans le modèle, toutes liées à un champ%s'
                            % (len(cles_modele), ' (orphelines : %s)' % orphelines if orphelines else ''))
(ok if cles_modele and cles_modele[0] == 'RconPassword' else non)('mot de passe en première ligne du modèle')

defauts = {c['FieldName']: c['DefaultValue'] for c in config}
cahier = {'$MaxUsers': '150', 'WorldSize': '4000', 'TailleEquipe': '4', 'Tickrate': '30', 'FpsLimit': '60',
          'SaveInterval': '300', 'ServerTags': 'EU', 'WorldSeed': 'auto', 'ArchivesGardees': '4',
          'InstallOxide': 'true', 'MajPluginsDemarrage': 'true', 'WipeProchain': 'non', 'PublicIp': '',
          'LogoImage': '', 'HeaderImage': '', 'WipeRythme': 'hebdo', 'CombatLogSize': '100'}
for n, v in cahier.items():
    (ok if defauts.get(n) == v else non)('défaut %-20s = %r' % (n, defauts.get(n)))
(non if re.search(r'hebdo|vanilla', defauts.get('Hostname', '') + defauts.get('ServerTags', ''), re.I)
 else ok)('nom et tags sans « hebdo » ni « vanilla »')
enums = {c['FieldName']: set(c.get('EnumValues', {})) for c in config if c['InputType'] == 'enum'}
for n, valeurs in (('WipeProchain', {'non', 'carte', 'carte_plans'}),
                   ('WipeAuto', {'non', 'carte', 'carte_plans_force', 'carte_plans'}),
                   ('WipeRythme', {'hebdo', '2semaines', 'mois', '2parsemaine'})):
    (ok if enums.get(n) == valeurs else non)('choix de %s : %s' % (n, sorted(enums.get(n, []))))
    for v in valeurs:
        if ("'%s'" % v) not in code:
            non('valeur %s=%s jamais traitée par le lanceur' % (n, v))
pvp = [c for c in config if c.get('Subcategory') == 'PvP compétitif' and c['DefaultValue'] == 'true']
(ok if sorted(c['FieldName'][6:] for c in pvp) == sorted(PVP) else non)(
    'groupe PvP compétitif : %d plugins cochés par défaut' % len(pvp))
for c in config:
    if c['FieldName'].startswith('UmodP_'):
        nom = c['FieldName'][6:]
        if 'https://umod.org/plugins/%s' % nom not in c['Description']:
            non('%s : lien umod.org absent ou faux dans la description' % nom)
        if c.get('Subcategory', '').startswith('Anciens') and not c['DisplayName'].count('(ancien)'):
            non('%s : plugin déconseillé sans la mention (ancien)' % nom)
        if c.get('Subcategory', '').startswith('Anciens') and c['DefaultValue'] != 'false':
            non('%s : plugin déconseillé coché par défaut' % nom)
v7 = subprocess.run(['git', '-C', ICI, 'show', '8369690:%sconfig.json' % NOM], capture_output=True)
if v7.returncode == 0:
    perdus = [c['FieldName'] for c in json.loads(v7.stdout.decode('utf-8'))
              if c['FieldName'] not in noms and c['FieldName'] not in ('SaveInterval',)]
    perdus = [p for p in perdus if p.startswith('UmodP_')]
    (non if perdus else ok)('toutes les cases de plugins de la v7 sont gardées' + (' : %s' % perdus if perdus else ''))

# ── 4. aller-retour du modèle, champs vides, regex de la console ─────────────────────────
spec = importlib.util.spec_from_file_location('lancer_rust', os.path.join(ICI, LANCEUR))
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)


def rendre(valeurs):
    """Comme AMP : remplacement brut de {{Champ}} par la valeur."""
    texte = modele
    for n, v in valeurs.items():
        texte = texte.replace('{{%s}}' % n, v)
    return texte


valeurs = dict(defauts)
valeurs.update({'RconPassword': MDP_ESSAI, 'Hostname': '[FR] Nom = avec égal, « guillemets » & $(rien)',
                'ServerDescription': 'Ligne 1\\nLigne 2 : 100 % "gratuit"', 'LogoImage': '',
                'HeaderImage': 'https://exemple.fr/entete.png', 'UmodPlugins': 'Kits, Economics.cs ,  ,bad/../nom'})
lu = L.lire_reglages(rendre(valeurs))
diff = [n for n in valeurs if n not in hors_fichier and lu.get(n) != valeurs[n]]
(non if diff else ok)('aller-retour du modèle (%d réglages relus à l\'identique)%s'
                      % (len(valeurs) - len(hors_fichier), ' ; différents : %s' % diff if diff else ''))
injecte = L.lire_reglages('RconPassword=bon\nHostname=x\nRconPassword=mauvais\n')
(ok if injecte['RconPassword'] == 'bon' else non)('une ligne injectée ne remplace pas le mot de passe')
(ok if L.lire_reglages('LogoImage={{LogoImage}}\n')['LogoImage'] == '' else non)(
    'un {{Champ}} laissé tel quel par AMP est lu comme vide')

notes = []
vides = dict(lu, ServerDescription='', ServerUrl='', HeaderImage='', LogoImage='', PublicIp='',
             LevelUrl='  ', WipeRythme='mois')
args = L.construire(vides, (28015, 28016, 28017, 28082), 150, 1234, [], notes.append)
convars = [a for a in args if a.startswith('+')]
sautes = [c for c in ('+server.description', '+server.url', '+server.headerimage', '+server.logoimage',
                      '+app.publicip', '+server.levelurl') if c in convars]
(non if sautes else ok)('champs vides SAUTÉS (aucune convar vide)' + (' : %s' % sautes if sautes else ''))
(non if '' in args else ok)('aucun argument vide envoyé à Rust')
pleins = dict(lu, PublicIp='51.254.47.151', LogoImage='https://exemple.fr/logo.png')
args2 = L.construire(pleins, (28015, 28016, 28017, 28082), 150, 1234, ['+server.pve', 'false'], notes.append)
for convar, v in (('+app.publicip', '51.254.47.151'), ('+server.logoimage', 'https://exemple.fr/logo.png'),
                  ('+relationshipmanager.maxteamsize', '4'), ('+server.tickrate', '30'), ('+fps.limit', '60'),
                  ('+server.secure', 'true'), ('+server.maxplayers', '150'), ('+server.worldsize', '4000'),
                  ('+server.saveinterval', '300'), ('+server.tags', 'weekly,EU'), ('+server.seed', '1234'),
                  ('+server.headerimage', 'https://exemple.fr/entete.png')):
    i = args2.index(convar) if convar in args2 else -1
    (ok if i >= 0 and args2[i + 1] == v else non)('%s %s' % (convar, v))
(ok if args[args.index('+server.tags') + 1] == 'monthly,EU' else non)('rythme « mois » -> tag monthly')
nom = args2[args2.index('+server.hostname') + 1]
(ok if '{wipe}' not in nom and '"' not in nom else non)('nom : {wipe} remplacé, sans guillemet double')
(ok if args2[-2:] == ['+server.pve', 'false'] else non)('arguments personnalisés en fin de ligne')
args3 = L.construire(pleins, (28015, 28016, 28017, 28082), 150, 1234,
                     ['+rcon.password', 'fuite', '+server.secure', 'false', '+server.pve', 'false'], notes.append)
(ok if 'fuite' not in args3 and args3.count('+server.secure') == 1 and args3[-2:] == ['+server.pve', 'false']
 else non)('arguments perso : +rcon.password et +server.secure retirés, le reste gardé')
(ok if [i for i, a in enumerate(args2) if MDP_ESSAI in a] == [args2.index('+rcon.password') + 1] else non)(
    'mot de passe seulement après +rcon.password (Rust ne le lit que là)')

for mauvais in ('', 'changemoi', 'court', 'avec espace 12345', 'aaaaaaaaaaaaaa', 'accentué-1234567', 'a/b?c#d12345'):
    try:
        L.verifier_mdp(mauvais)
        non('mot de passe accepté à tort : %r' % mauvais)
    except L.Refus:
        pass
ok('mots de passe refusés : vide, défaut, court, espace, trop simple, accent, / ? #')
try:
    L.verifier_identite('../etc')
    non('identité « ../etc » acceptée')
except L.Refus:
    ok('identité avec ../ refusée')

class NetRegex:
    """Une regex au format .NET (celui d'AMP) : un même nom de groupe peut revenir dans deux branches d'une alternative,
    ce que le module re de Python refuse. Chaque occurrence reçoit un suffixe, et group() rend la première remplie."""
    def __init__(self, motif):
        vus = {}

        def renomme(m):
            n = m.group(1)
            vus[n] = vus.get(n, 0) + 1
            return '(?P<%s__%d>' % (n, vus[n])
        self.r = re.compile(re.sub(r'\(\?<([A-Za-z_]\w*)>', renomme, motif))

    def match(self, ligne):
        m = self.r.match(ligne)
        if not m:
            return None
        d = m.groupdict()
        return type('M', (), {'group': lambda _s, n: next((v for k, v in d.items() if k.split('__')[0] == n and v is not None), None)})()


joint = NetRegex(K['Console.UserJoinRegex'])
part = NetRegex(K['Console.UserLeaveRegex'])
m = joint.match('Kévin [TK] with steamid 76561197994679825 joined from ip 88.12.34.56:52011 with platform Steam')
(ok if m and m.group('username') == 'Kévin [TK]' and m.group('userid') == '76561197994679825' else non)(
    'regex d\'arrivée sur une vraie ligne de Rust (« with steamid … joined from ip »)')
# L'autre forme, vue sur le serveur le 18 oct. 2026 (AMP affichait 0 joueur alors que Kevin était connecté)
m = joint.match('88.12.34.56:50413/76561197994679825/Kévin [TK] joined [windows/76561197994679825]')
(ok if m and m.group('username') == 'Kévin [TK]' and m.group('userid') == '76561197994679825' and m.group('ip') == '88.12.34.56' else non)(
    'regex d\'arrivée sur la forme « ip:port/steamid/pseudo joined [windows/…] »')
m = part.match('88.12.34.56:52011/76561197994679825/Kévin [TK] disconnecting: closing')
(ok if m and m.group('userid') == '76561197994679825' and m.group('username') == 'Kévin [TK]' else non)(
    'regex de départ sur une vraie ligne de Rust')
for l in ('TeamKit : WIPE (carte) demande par : fichier wipe-demande.txt (carte).',
          '[RCON] hostname: [FR] TeamKit', 'TeamKit : users.cfg : 1 proprietaire(s), 0 moderateur(s).'):
    if joint.match(l) or part.match(l) or re.match(K['Console.AppReadyRegex'], l):
        non('une ligne du lanceur est prise pour un événement : %r' % l)
ok('les lignes du lanceur ne déclenchent ni arrivée, ni départ, ni « prêt »')

# ── 5. calendrier des wipes ──────────────────────────────────────────────────────────────


def U(texte):
    return dt.datetime.strptime(texte, '%Y-%m-%d %H:%M').replace(tzinfo=dt.timezone.utc)


cas = [  # rythme, maintenant UTC, début attendu UTC, forcé ?
    ('hebdo', '2026-12-03 19:05', '2026-12-03 19:00', True),    # 1er jeudi, 20:05 Paris (hiver)
    ('hebdo', '2026-12-09 12:00', '2026-12-03 19:00', True),
    ('hebdo', '2026-12-10 18:50', '2026-12-10 19:00', False),   # 15 min d'avance acceptées
    ('hebdo', '2026-12-10 18:30', '2026-12-03 19:00', True),
    ('hebdo', '2027-07-01 18:05', '2027-07-01 18:00', True),    # été : 20:00 Paris = 18:00 UTC
    ('mois', '2026-12-17 20:00', '2026-12-03 19:00', True),
    ('2semaines', '2026-12-17 19:30', '2026-12-17 19:00', False),
    ('2semaines', '2026-12-24 19:30', '2026-12-17 19:00', False),
    ('2parsemaine', '2026-12-07 19:10', '2026-12-07 19:00', False),  # lundi
]
for rythme, m, attendu_debut, force in cas:
    debut, f, _ = L.periode(rythme, (20, 0), U(m))
    (ok if debut == U(attendu_debut) and f == force else non)(
        'calendrier %-11s %s -> %s%s' % (rythme, m, L.iso(debut), ' (forcé)' if f else ''))
_, _, prochain = L.periode('hebdo', (20, 0), U('2026-12-03 19:05'))
(ok if prochain == U('2026-12-10 19:00') else non)('prochain wipe hebdo après le 03/12 : %s' % L.iso(prochain))

base = {'WipeRythme': 'hebdo', 'WipeHeure': '20:00'}
scenarios = [  # reglages, temoin, maintenant, monde ?, demande, attendu
    (dict(base, WipeAuto='carte_plans_force'), {'periode': '2026-11-26T19:00Z'}, '2026-12-03 19:05', True, None, 'plans'),
    (dict(base, WipeAuto='carte_plans_force'), {'periode': '2026-12-10T19:00Z'}, '2026-12-10 21:05', True, None, None),
    (dict(base, WipeAuto='carte_plans_force'), {'periode': '2026-12-03T19:00Z'}, '2026-12-10 19:05', True, None, 'carte'),
    (dict(base, WipeAuto='carte'), {'periode': '2026-12-03T19:00Z'}, '2026-12-03 23:00', True, None, None),
    (dict(base, WipeAuto='carte'), {'periode': '2026-12-03T19:00Z'}, '2026-12-11 08:00', True, None, None),  # > 6 h
    (dict(base, WipeAuto='carte'), {}, '2026-12-10 19:05', True, None, None),        # 1re fois sur un monde
    (dict(base, WipeProchain='carte'), {'prochain_vu': 'non'}, '2026-12-08 10:00', True, None, 'carte'),
    (dict(base, WipeProchain='carte'), {'prochain_vu': 'carte'}, '2026-12-08 11:00', True, None, None),
    (dict(base, WipeProchain='carte_plans'), {'prochain_vu': 'carte'}, '2026-12-08 11:00', True, None, 'plans'),
    (dict(base), {}, '2026-12-08 11:00', True, 'plans', 'plans'),
]
for i, (r, t, m, monde, demande, attendu_wipe) in enumerate(scenarios, 1):
    choix, raisons, nouveau = L.decider_wipe(r, t, U(m), monde, demande, lambda x: None)
    (ok if choix == attendu_wipe else non)('décision %2d : %-6s (%s)' % (i, choix, '; '.join(raisons) or 'pas de wipe'))
# 2 : déjà fait pour cette période ; 5 : fenêtre de 6 h dépassée ; 6 : 1re activation sur un monde existant

# ── 6. fichiers livrés ───────────────────────────────────────────────────────────────────
for etape in data[NOM + 'updates.json']:
    if etape.get('UpdateSource') == 'FetchURL' and etape['UpdateSourceData'].startswith(DEPOT_RAW):
        chemin = etape['UpdateSourceData'][len(DEPOT_RAW):]
        (ok if os.path.isfile(os.path.join(ICI, chemin)) else non)('Update télécharge %s' % chemin)
        brut = open(os.path.join(ICI, chemin), 'rb').read()
        (non if b'\r\n' in brut else ok)('%s en LF' % chemin)
        if chemin.endswith('AMP_reglages-rust.txt'):
            meta = data[NOM + 'metaconfig.json'][0]
            (ok if etape['UpdateSourceTarget'] == '{{$FullRootDir}}'
             and meta['ConfigFileTemplate'] == 'rust/' + etape['UpdateSourceArgs'] else non)(
                'modèle posé là où metaconfig le cherche (rust/%s)' % etape['UpdateSourceArgs'])
# Steam bute sur les DLL modifiées par Oxide (« Corrupt game files », état 0x486, vu le 26/10/2026) :
# Managed est mis de côté AVANT SteamCMD, et le lanceur le remet si SteamCMD a échoué ensuite.
etapes = data[NOM + 'updates.json']
noms = [e.get('UpdateSource') for e in etapes]
mise = etapes[0] if etapes else {}
(ok if noms[:2] == ['Executable', 'SteamCMD'] and 'Managed-avant-maj' in mise.get('UpdateSourceArgs', '')
 else non)('Update : Managed mis de côté juste avant SteamCMD')
corps = mise.get('UpdateSourceArgs', '')
(ok if corps.startswith('-c "') and corps.endswith('"') and '"' not in corps[4:-1] and mise.get('SkipOnFailure')
 else non)('étape de mise de côté : aucun guillemet double interne, sans bloquer l\'Update')
with tempfile.TemporaryDirectory() as t:
    data_dir = os.path.join(t, 'RustDedicated_Data')
    os.makedirs(os.path.join(data_dir, 'Managed-avant-maj'))
    open(os.path.join(data_dir, 'Managed-avant-maj', 'Assembly-CSharp.dll'), 'w').close()
    ancien_ici, L.ICI = L.ICI, t
    try:
        L.remettre_managed_si_absent()
        (ok if os.path.isfile(os.path.join(data_dir, 'Managed', 'Assembly-CSharp.dll'))
         and not os.path.exists(os.path.join(data_dir, 'Managed-avant-maj')) else non)(
            'SteamCMD en échec : le lanceur remet l\'ancien Managed')
        os.makedirs(os.path.join(data_dir, 'Managed-avant-maj'))
        L.remettre_managed_si_absent()
        (ok if os.path.isdir(os.path.join(data_dir, 'Managed-avant-maj')) else non)(
            'Update réussie : le lanceur ne touche pas au Managed neuf')
    finally:
        L.ICI = ancien_ici
manif = data['manifest.json']
(ok if manif['url'] == 'https://github.com/hydrocut/AMPTemplates-Rust' else non)('URL du manifest')
try:
    compile(code, LANCEUR, 'exec')
    ok('lancer-rust.py compile')
except SyntaxError as e:
    non('lancer-rust.py : %s' % e)

# ── 7. essai de bout en bout ─────────────────────────────────────────────────────────────
FAUX_RUST = r'''
import base64, hashlib, json, os, socket, struct, sys, threading, time
args = sys.argv[1:]
def val(nom):
    return args[args.index(nom) + 1] if nom in args else None
with open('faux-argv.json', 'w') as f:
    json.dump(args, f)
port, mdp = int(val('+rcon.port')), val('+rcon.password')
def dire(t):
    sys.stdout.write('\x1b[37m' + t + '\x1b[0m\n'); sys.stdout.flush()
def lire_exact(c, n):
    d = b''
    while len(d) < n:
        b = c.recv(n - len(d))
        if not b: raise ConnectionError
        d += b
    return d
def envoyer(c, obj):
    p = json.dumps(obj).encode()
    h = struct.pack('!BB', 0x81, len(p)) if len(p) < 126 else struct.pack('!BBH', 0x81, 126, len(p))
    c.sendall(h + p)
def client(c):
    req = b''
    while b'\r\n\r\n' not in req:
        req += c.recv(1024)
    chemin = req.split(b' ')[1].decode()
    if chemin != '/' + mdp:
        c.sendall(b'HTTP/1.1 401 Unauthorized\r\n\r\n'); c.close(); return
    cle = [l.split(b': ')[1] for l in req.split(b'\r\n') if l.lower().startswith(b'sec-websocket-key')][0]
    rep = base64.b64encode(hashlib.sha1(cle + b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
    c.sendall(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ' + rep + b'\r\n\r\n')
    while True:
        try:
            b1, b2 = lire_exact(c, 2)
        except ConnectionError:
            return
        n = b2 & 0x7F
        if n == 126: n = struct.unpack('!H', lire_exact(c, 2))[0]
        m = lire_exact(c, 4)
        p = bytes(x ^ m[i % 4] for i, x in enumerate(lire_exact(c, n)))
        msg = json.loads(p.decode())
        cmd, ident = msg['Message'], msg['Identifier']
        with open('faux-commandes.txt', 'a') as f:
            f.write(cmd + '\n')
        envoyer(c, {'Identifier': 0, 'Message': 'bruit de la console', 'Type': 'Generic'})
        if cmd == 'server.save':
            time.sleep(0.3)
            dire('Saved 12,345 ents, cache(0.01), write(0.01), disk(0.01).')
            envoyer(c, {'Identifier': ident, 'Message': 'Saved', 'Type': 'Generic'})
        elif cmd == 'quit':
            dire('quitting')
            os._exit(255)   # le vrai RustDedicated rend -1 apres quit (constat du banc local, 18/10/2026)
        else:
            envoyer(c, {'Identifier': ident, 'Message': 'reponse a ' + cmd + '\nplayers : 0 (150 max)', 'Type': 'Generic'})
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', port)); s.listen(5)
dire('RCON Started on ' + str(port))
dire('Generating procedural map of size 4000 with seed ' + str(val('+server.seed')))
import glob
if glob.glob(os.path.join('server', '*', 'proceduralmap.*.sav')):
    dire('Spawning 3,386 entities from save')
else:
    dire("Couldn't load server/teamkit/proceduralmap.4000." + str(val('+server.seed')) + ".270.sav - file doesn't exist")
dire('Server startup complete')
while True:
    c, _ = s.accept()
    threading.Thread(target=client, args=(c,), daemon=True).start()
'''
FAUX_CURL = r'''
import sys
args = sys.argv[1:]
sortie, url = args[args.index('-o') + 1], args[-1]
nom = url.rsplit('/', 1)[-1][:-3]
if nom == 'Inconnu':
    sys.exit(22)
with open(sortie, 'w') as f:
    f.write('namespace Oxide.Plugins { [Info("%s", "essai", "1.0.0")] class %s {} }\n' % (nom, nom))
'''


def port_libre():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def essai():
    print()
    print('Essai de bout en bout (dossier factice, faux RustDedicated, faux curl)')
    tmp = tempfile.mkdtemp(prefix='essai-rust-')
    racine = os.path.join(tmp, 'rust')
    base = os.path.join(racine, '258550')
    monde = os.path.join(base, 'server', 'teamkit')
    os.makedirs(os.path.join(base, 'RustDedicated_Data', 'Managed'))
    open(os.path.join(base, 'RustDedicated_Data', 'Managed', 'Oxide.Rust.dll'), 'w').close()
    shutil.copy(os.path.join(ICI, LANCEUR), base)
    for nom, texte in (('faux-rust.py', FAUX_RUST), ('faux-curl.py', FAUX_CURL)):
        with open(os.path.join(tmp, nom), 'w', encoding='utf-8') as f:
            f.write(texte)
    plugins = os.path.join(base, 'oxide', 'plugins')
    os.makedirs(plugins)
    with open(os.path.join(plugins, 'Kits.cs'), 'w') as f:      # décoché -> mis de côté
        f.write('namespace Oxide.Plugins {}')

    def poser_monde(graine):
        os.makedirs(monde, exist_ok=True)
        for n in ('proceduralmap.4000.%d.270.map' % graine, 'proceduralmap.4000.%d.270.sav' % graine,
                  'proceduralmap.4000.%d.270.sav.1' % graine, 'player.states.270.db',
                  'player.blueprints.5.db', 'player.blueprints.5.db-wal', 'player.tokens.db', 'sv.files.270.db'):
            with open(os.path.join(monde, n), 'wb') as f:
                f.write(os.urandom(2048))
        os.makedirs(os.path.join(monde, 'cfg'), exist_ok=True)
        with open(os.path.join(monde, 'cfg', 'bans.cfg'), 'w') as f:
            f.write('banid 76561190000000000 "x" "triche"\n')

    def lancer(reglages, maintenant, commandes=(), attendre_pret=True):
        valeurs = dict(defauts)
        valeurs.update({'RconPassword': MDP_ESSAI, 'OwnerIds': '76561197994679825, pas-un-id',
                        'UmodPlugins': 'Inconnu', 'GrainesListe': '111,222,333'})
        valeurs.update(reglages)
        with open(os.path.join(base, 'reglages-rust.txt'), 'w', encoding='utf-8') as f:
            f.write(rendre(valeurs))
        for n in ('faux-commandes.txt', 'faux-argv.json'):
            if os.path.exists(os.path.join(base, n)):
                os.remove(os.path.join(base, n))
        env = dict(os.environ, TK_RUST_CMD=json.dumps([sys.executable, '-u', os.path.join(tmp, 'faux-rust.py')]),
                   TK_CURL=json.dumps([sys.executable, os.path.join(tmp, 'faux-curl.py')]),
                   TK_MAINTENANT=maintenant, PYTHONIOENCODING='utf-8')
        rcon = port_libre()
        argv_amp = [sys.executable, '-u', os.path.join(base, 'lancer-rust.py'), 'lancer',
                    '28015', '28016', str(rcon), '28082', '150']
        p = subprocess.Popen(argv_amp, cwd=base, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
        sortie = []
        fin = time.monotonic() + 60
        pret = False
        while time.monotonic() < fin:
            l = p.stdout.readline()
            if not l:
                break
            l = l.decode('utf-8', 'replace').rstrip()
            sortie.append(l)
            if l == 'Server startup complete':
                pret = True
                break
        if pret:
            for c in list(commandes) + ['quit']:
                p.stdin.write((c + '\n').encode())
                p.stdin.flush()
                time.sleep(0.4)
        try:
            reste, _ = p.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            p.kill()
            reste, _ = p.communicate()
        sortie += reste.decode('utf-8', 'replace').splitlines()
        cmds = []
        if os.path.exists(os.path.join(base, 'faux-commandes.txt')):
            cmds = open(os.path.join(base, 'faux-commandes.txt')).read().split()
        argv_rust = json.load(open(os.path.join(base, 'faux-argv.json'))) if os.path.exists(
            os.path.join(base, 'faux-argv.json')) else None
        return p.returncode, sortie, cmds, argv_amp, argv_rust

    def montrer(sortie):
        for l in sortie:
            if l.startswith('TeamKit :') or l.startswith('[RCON]'):
                print('       | ' + l)

    def archives():
        d = os.path.join(racine, 'archives-wipe')
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    try:
        # A. mot de passe vide : démarrage refusé, le serveur n'est jamais lancé
        poser_monde(4242)
        code_a, sortie, _, _, argv_rust = lancer({'RconPassword': ''}, '2026-12-01T10:00Z')
        print('  A. mot de passe RCON vide')
        montrer(sortie)
        (ok if code_a == 1 and argv_rust is None and any('DEMARRAGE REFUSE' in l for l in sortie) else non)(
            'A : démarrage refusé, message clair, RustDedicated jamais lancé')

        # B. jeudi 3/12 20:05 (1er jeudi = wipe forcé) : wipe auto « carte, plans au wipe forcé »
        with open(os.path.join(monde, 'teamkit-wipe.json'), 'w') as f:
            json.dump({'periode': '2026-11-26T19:00Z', 'prochain_vu': 'non', 'graine': 4242}, f)
        code_b, sortie, cmds, argv_amp, argv_rust = lancer(
            {'WipeAuto': 'carte_plans_force', 'ArchivesGardees': '2', 'UmodP_Duelist': 'false'},
            '2026-12-03T19:05Z', commandes=['status'])
        print('  B. jeudi 03/12 20:05, wipe forcé du mois')
        montrer(sortie)
        restants = sorted(os.listdir(monde))
        (ok if len(archives()) == 1 else non)('B : archive datée créée : %s' % archives())
        if archives():
            with tarfile.open(os.path.join(racine, 'archives-wipe', archives()[0])) as t:
                dedans = t.getnames()
            (ok if 'teamkit/proceduralmap.4000.4242.270.sav' in dedans and 'teamkit/player.blueprints.5.db' in dedans
             else non)('B : l\'archive contient la carte, la sauvegarde et les plans (%d fichiers)' % len(dedans))
        effaces = [n for n in ('proceduralmap.4000.4242.270.map', 'proceduralmap.4000.4242.270.sav',
                               'proceduralmap.4000.4242.270.sav.1', 'player.states.270.db', 'sv.files.270.db',
                               'player.blueprints.5.db', 'player.blueprints.5.db-wal') if n in restants]
        (non if effaces else ok)('B : carte, sauvegardes et plans effacés' + (' (restent %s)' % effaces if effaces else ''))
        (ok if 'player.tokens.db' in restants and os.path.isfile(os.path.join(monde, 'cfg', 'bans.cfg')) else non)(
            'B : gardés : player.tokens.db (Rust+) et cfg/bans.cfg')
        graine_b = int(argv_rust[argv_rust.index('+server.seed') + 1]) if argv_rust else None
        (ok if graine_b in (111, 222, 333) else non)('B : nouvelle graine tirée dans la liste vérifiée : %s' % graine_b)
        (non if any(MDP_ESSAI in a for a in argv_amp) else ok)(
            'B : mot de passe ABSENT de la ligne de commande d\'AMP : %s' % ' '.join(argv_amp[2:]))
        (ok if argv_rust and argv_rust[argv_rust.index('+rcon.password') + 1] == MDP_ESSAI else non)(
            'B : RustDedicated le reçoit par +rcon.password (seule voie que Rust accepte)')
        sans_wp = ' '.join(cmds).replace('wp start', ' ').split()   # faux-commandes.txt est relu mot à mot
        (ok if sans_wp[:3] == ['status', 'server.save', 'quit'] and code_b == 0 else non)(
            'B : console d\'AMP relayée, puis arrêt propre (RustDedicated rend -1, le lanceur 0) : %s, code %s' % (' -> '.join(cmds), code_b))
        (ok if 'wp start' in ' '.join(cmds) and any('wp start' in l and 'RCON' in l for l in sortie) else non)(
            'B : après le wipe, « wp start » envoyé par RCON une fois le serveur prêt (WipeProtection : 3 h, pas 24 h)')
        (ok if not any('\x1b' in l for l in sortie) and 'Server startup complete' in sortie else non)(
            'B : codes couleur ANSI retirés avant de relayer à AMP (« Server startup complete » reconnu tel quel)')
        (ok if any('monde neuf' in l for l in sortie) else non)('B : monde neuf reconnu (« Couldn\'t load … file doesn\'t exist »)')
        (ok if os.path.isdir(os.path.join(base, 'oxide', 'data', 'copypaste')) else non)(
            'B : oxide/data/copypaste/ créé (CopyPaste plante sans lui)')
        (ok if any(l.startswith('[RCON] reponse a status') for l in sortie)
         and not any('bruit de la console' in l for l in sortie if l.startswith('[RCON]')) else non)(
            'B : réponse RCON affichée, bruit de la console ignoré')
        users = open(os.path.join(monde, 'cfg', 'users.cfg')).read()
        (ok if users == 'ownerid 76561197994679825\n' else non)('B : users.cfg = %r (id invalide ignoré)' % users)
        (ok if os.path.isfile(os.path.join(base, 'oxide', 'plugins-desactives', 'Kits.cs'))
         and not os.path.exists(os.path.join(plugins, 'Kits.cs')) else non)('B : plugin décoché (Kits) mis de côté')
        presents = sorted(n[:-3] for n in os.listdir(plugins))
        (ok if presents == sorted(set(PVP) - {'Duelist'}) else non)(
            'B : %d plugins PvP téléchargés, Duelist (décoché) absent' % len(presents))
        (ok if any('Inconnu non telecharge' in l for l in sortie) else non)('B : plugin inconnu signalé')
        (ok if (stat_mode(os.path.join(base, 'reglages-rust.txt')) & 0o077) == 0 or os.name != 'posix' else non)(
            'B : reglages-rust.txt lisible par l\'instance seule (600)')

        # C. même soirée, redémarrage : pas de second wipe ; un plugin attend le prochain wipe (page « Plugins Rust »)
        poser_monde(graine_b)
        au_wipe = os.path.join(base, 'oxide', 'plugins-au-prochain-wipe')
        os.makedirs(au_wipe)
        with open(os.path.join(au_wipe, 'Clans.cs'), 'w') as f:
            f.write('namespace Oxide.Plugins { [Info("Clans", "k1lly0u", "9.9.9")] class Clans {} }\n')
        with open(os.path.join(au_wipe, 'Faux.cs'), 'w') as f:
            f.write('pas un plugin')
        with open(os.path.join(au_wipe, 'mal nom.cs'), 'w') as f:
            f.write('namespace Oxide.Plugins {}')
        code_c, sortie, cmds, _, argv_rust = lancer({'WipeAuto': 'carte_plans_force', 'ArchivesGardees': '2'},
                                                    '2026-12-03T21:30Z')
        print('  C. même soir, 22:30 : redémarrage')
        montrer(sortie)
        (ok if len(archives()) == 1 and os.path.isfile(os.path.join(monde, 'proceduralmap.4000.%d.270.sav' % graine_b))
         else non)('C : pas de second wipe dans la même période')
        (ok if argv_rust and int(argv_rust[argv_rust.index('+server.seed') + 1]) == graine_b else non)(
            'C : même graine gardée')
        (ok if 'wp start' not in ' '.join(cmds) and os.path.isfile(os.path.join(au_wipe, 'Clans.cs'))
         and '9.9.9' not in open(os.path.join(plugins, 'Clans.cs')).read() else non)(
            'C : sans wipe, ni « wp start » ni plugin posé : Clans 9.9.9 attend dans plugins-au-prochain-wipe/')
        (ok if any('monde recharge' in l for l in sortie) else non)('C : monde rechargé reconnu (« Spawning N entities from save »)')

        # D. jeudi 10/12 20:10 : wipe de carte seul (pas le 1er jeudi), plans gardés
        code_d, sortie, _, _, _ = lancer({'WipeAuto': 'carte_plans_force', 'ArchivesGardees': '2',
                                          'MajPluginsDemarrage': 'false'}, '2026-12-10T19:10Z')
        print('  D. jeudi 10/12 20:10, wipe de carte, saison figée')
        montrer(sortie)
        restants = sorted(os.listdir(monde))
        (ok if len(archives()) == 2 and 'player.blueprints.5.db' in restants
         and not any(n.endswith('.sav') for n in restants) else non)('D : carte effacée, plans gardés, 2 archives')
        (ok if any('saison figee' in l for l in sortie) else non)('D : plugins gardés tels quels (saison figée)')
        precedents = os.path.join(base, 'oxide', 'plugins-precedents')
        anciens = sorted(os.listdir(precedents)) if os.path.isdir(precedents) else []
        (ok if '9.9.9' in open(os.path.join(plugins, 'Clans.cs')).read() and not os.path.exists(os.path.join(au_wipe, 'Clans.cs'))
         and len(anciens) == 1 and re.match(r'^Clans\.cs\.\d{8}-\d{6}$', anciens[0])
         and '1.0.0' in open(os.path.join(precedents, anciens[0])).read() else non)(
            'D : au wipe, Clans 9.9.9 passe de plugins-au-prochain-wipe/ à plugins/, l\'ancien gardé : %s' % anciens)
        (ok if sorted(os.listdir(au_wipe)) == ['Faux.cs', 'mal nom.cs'] and any('ignore' in l and 'Faux.cs' in l for l in sortie) else non)(
            'D : fichiers suspects laissés de côté (pas un plugin Oxide, nom refusé)')
        etat = json.load(open(os.path.join(base, 'teamkit-etat.json')))
        (ok if etat.get('plugins_poses_au_wipe') == ['Clans'] else non)('D : teamkit-etat.json note les plugins posés au wipe')

        # E. « Wipe au prochain démarrage » = carte + plans, 3 archives demandées -> rotation à 2
        poser_monde(999)
        code_e, sortie, _, _, _ = lancer({'WipeProchain': 'carte_plans', 'ArchivesGardees': '2'},
                                         '2026-12-12T15:00Z')
        print('  E. samedi 12/12, « Wipe au prochain démarrage » = carte + plans')
        montrer(sortie)
        (ok if len(archives()) == 2 and not any(n.startswith('player.blueprints') for n in os.listdir(monde)) else non)(
            'E : wipe complet fait, rotation : 2 archives gardées %s' % archives())
        poser_monde(999)
        _, sortie, cmds, _, _ = lancer({'WipeProchain': 'carte_plans', 'ArchivesGardees': '2'},
                                       '2026-12-12T16:00Z', commandes=['teamkit.wipe carte'])
        (ok if os.path.isfile(os.path.join(monde, 'player.blueprints.5.db')) else non)(
            'E : réglage laissé sur « carte + plans » : pas refait au démarrage suivant')
        (ok if os.path.isfile(os.path.join(base, 'wipe-demande.txt')) and 'teamkit.wipe' not in ' '.join(cmds)
         else non)('E : « teamkit.wipe carte » tapé dans la console : demande posée, pas envoyée au serveur')

        # F. la demande (fichier wipe-demande.txt, posé par teamkit.wipe, le site ou le bot)
        code_f, sortie, _, _, _ = lancer({'WipeProchain': 'carte_plans'}, '2026-12-13T15:00Z')
        print('  F. demande de wipe posée par « teamkit.wipe carte »')
        montrer([l for l in sortie if 'WIPE' in l or 'archive' in l])
        (ok if not os.path.exists(os.path.join(base, 'wipe-demande.txt'))
         and os.path.isfile(os.path.join(monde, 'player.blueprints.5.db'))
         and not any(n.endswith('.map') for n in os.listdir(monde)) else non)(
            'F : wipe de carte fait, fichier de demande retiré')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def stat_mode(chemin):
    return os.stat(chemin).st_mode


essai()

# ── verdict ──────────────────────────────────────────────────────────────────────────────
print()
if soucis:
    print('%d probleme(s) :' % len(soucis))
    for s in soucis:
        print('  NON  ' + s)
    sys.exit(1)
print('Tout est bon.')
