#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lancer-rust.py - pose par le modele AMP « Rust (TeamKit) » v8 (recree a chaque Update).

    python3 lancer-rust.py lancer <port jeu> <port requetes> <port rcon> <port rust+> <joueurs> [arguments perso...]
    python3 lancer-rust.py rcon <commande...>    envoie une commande au serveur qui tourne (RCON local)
    python3 lancer-rust.py periode               affiche le wipe en cours et le prochain (JSON)

« lancer » est la ligne de commande d'AMP. Avant de demarrer RustDedicated, il :
  1. lit reglages-rust.txt, que AMP ecrit a chaque demarrage a partir de AMP_reglages-rust.txt
     (onglet Configuration). Le mot de passe RCON n'arrive QUE par ce fichier : il n'est
     jamais dans la ligne de commande d'AMP, ni dans celle d'une etape de demarrage ;
  2. refuse de demarrer si le mot de passe RCON est vide, trop court ou laisse par defaut ;
  3. ecrit server/<identite>/cfg/users.cfg (proprietaires et moderateurs choisis dans AMP) ;
  4. fait le wipe s'il est du : archive datee de server/<identite>/ (les N dernieres gardees),
     effacement de la carte (et des plans si demande), nouvelle graine si « auto » ;
  5. au wipe seulement, met en service les plugins deposes dans oxide/plugins-au-prochain-wipe/
     par la page « Plugins Rust » du panel TeamKit (l'ancienne version va dans
     oxide/plugins-precedents/<Nom>.cs.<date>) ; cree oxide/data/copypaste/ s'il manque ;
     installe Oxide s'il manque, telecharge les plugins uMod coches (tous a chaque demarrage,
     ou seulement les manquants quand la saison est figee) et met de cote ceux qu'on a decoches ;
  6. lance RustDedicated, relaie la console d'AMP vers le RCON local du serveur (say, kick,
     status, oxide.reload... marchent depuis AMP). Deux commandes sont gardees pour TeamKit :
     « teamkit.wipe carte|plans|annuler » (wipe au prochain demarrage, aussi par l'API d'AMP
     pour le bot ou le site) et « teamkit.periode » ;
  7. a l'arret demande par AMP (SIGTERM) ou par « quit » dans la console : server.save puis
     quit par RCON, et seulement si le RCON ne repond pas, SIGTERM au serveur. RustDedicated rend
     -1 apres quit : un arret demande ET sauvegarde sort en 0 (sinon AMP croirait a un plantage) ;
  8. apres un wipe, une fois le serveur pret : « wp start » par RCON si WipeProtection est la
     (sinon sa protection dure 24 h au lieu de 3 h quand sa config arrive apres la nouvelle carte).

Les codes couleur ANSI de RustDedicated (\x1b[37m...) sont retires de chaque ligne avant de la
relayer a AMP et avant toute comparaison (« Server startup complete », « Saved ... ents »).
Rust ecrit TOUJOURS « Generating procedural map » : monde neuf = « Couldn't load .../proceduralmap
...sav - file doesn't exist », monde recharge = « Spawning N entities from save ».

Rust lui-meme ne lit le mot de passe RCON que sur SA ligne de commande (+rcon.password) :
il n'accepte pas de le prendre dans server.cfg. Il reste donc visible dans la liste des
processus de la machine pour qui a deja un acces SSH. Ce qui protege vraiment : le port RCON
ferme au public et un mot de passe long.
"""
import base64
import ctypes
import datetime as dt
import fnmatch
import json
import os
import random
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tarfile
import threading
import time
import zipfile

VERSION_MODELE = 8
ICI = os.path.dirname(os.path.abspath(__file__))
RACINE = os.path.dirname(ICI)
REGLAGES = os.path.join(ICI, 'reglages-rust.txt')
DEMANDE = os.path.join(ICI, 'wipe-demande.txt')
ETAT = os.path.join(ICI, 'teamkit-etat.json')
ARCHIVES = os.path.join(RACINE, 'archives-wipe')
TEMOIN = 'teamkit-wipe.json'
OXIDE_DLL = os.path.join(ICI, 'RustDedicated_Data', 'Managed', 'Oxide.Rust.dll')
OXIDE_URL = 'https://github.com/OxideMod/Oxide.Rust/releases/latest/download/Oxide.Rust-linux.zip'
UMOD_URL = 'https://umod.org/plugins/%s.cs'

FENETRE = 6 * 3600     # un wipe automatique n'est fait que dans les 6 h qui suivent l'heure prevue
AVANCE = 15 * 60       # un redemarrage jusqu'a 15 min avant l'heure compte deja pour ce wipe

# Fichiers effaces au wipe, dans server/<identite>/ seulement (jamais dans cfg/).
WIPE_CARTE = ('*.map', '*.sav', '*.sav.*', 'player.states.*.db*', 'sv.files.*.db*')
WIPE_PLANS = ('player.blueprints.*',)

MDP_MOTIF = re.compile(r'^[A-Za-z0-9._-]{12,64}$')
MDP_INTERDITS = {'changemoi', 'changeme', 'password', 'motdepasse', 'mot_de_passe', 'rcon',
                 'admin', 'teamkit', 'azertyuiop', '123456789012', 'rconpassword'}
IDENTITE_MOTIF = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
STEAMID_MOTIF = re.compile(r'^7656119\d{10}$')
PLUGIN_MOTIF = re.compile(r'^[A-Za-z0-9_]{2,64}$')
SAUVE_MOTIF = re.compile(r'^Saved [\d,. ]+ ents')
ANSI = re.compile(rb'\x1b\[[0-9;]*[A-Za-z]')
MONDE_NEUF = re.compile(r"^Couldn't load server/.*proceduralmap.*\.sav - file doesn't exist")
MONDE_RECHARGE = re.compile(r'^Spawning ([\d,.]+) entities from save')
AU_WIPE = os.path.join(ICI, 'oxide', 'plugins-au-prochain-wipe')
PRECEDENTS = os.path.join(ICI, 'oxide', 'plugins-precedents')

RYTHMES = {
    # valeur AMP : (tag officiel de Rust, fin du nom quand le nom contient {wipe})
    'hebdo': ('weekly', 'Wipe jeudi'),
    '2semaines': ('biweekly', 'Wipe 1 jeudi sur 2'),
    'mois': ('monthly', 'Wipe mensuel'),
    '2parsemaine': ('weekly', 'Wipe lundi + jeudi'),
}
TAGS_DE_WIPE = {'weekly', 'biweekly', 'monthly'}

_ecriture = threading.Lock()


def dire(texte):
    ecrire(('TeamKit : ' + texte).encode('utf-8', 'replace') + b'\n')


def ecrire(octets):
    with _ecriture:
        try:
            sys.stdout.buffer.write(octets)
            sys.stdout.buffer.flush()
        except (OSError, ValueError):
            pass


class Refus(Exception):
    pass


# ── Reglages ecrits par AMP ──────────────────────────────────────────────────────────────

def lire_reglages(texte):
    """Une ligne cle=valeur par reglage. La PREMIERE occurrence d'une cle gagne : une valeur
    qui contiendrait un retour a la ligne ne peut pas remplacer une cle ecrite plus haut."""
    vus = {}
    for ligne in texte.split('\n'):
        ligne = ligne.rstrip('\r')
        if not ligne or ligne.startswith('#') or '=' not in ligne:
            continue
        cle, valeur = ligne.split('=', 1)
        cle = cle.strip()
        # un {{Reglage}} laisse tel quel par AMP (reglage inconnu de cette version) = vide
        if re.match(r'^\s*\{\{[^{}]*\}\}\s*$', valeur):
            valeur = ''
        if cle and cle not in vus:
            vus[cle] = valeur
    return vus


def vrai(valeur):
    return str(valeur).strip().lower() in ('true', '1', 'oui', 'yes', 'on')


def texte_propre(valeur):
    """Texte envoye a Rust en argument : pas de guillemet double (la ligne de commande de Rust
    est relue par Facepunch.CommandLine, un " la couperait) ni de caractere de controle."""
    v = ''.join(c for c in str(valeur) if c >= ' ' or c == '\t').replace('\t', ' ')
    return v.replace('"', "'").strip()


def entier(reglages, cle, defaut, mini, maxi, avertir):
    brut = str(reglages.get(cle, '')).strip()
    try:
        v = int(brut)
    except ValueError:
        if brut:
            avertir('%s = « %s » n\'est pas un nombre : %d utilise.' % (cle, brut, defaut))
        return defaut
    if v < mini or v > maxi:
        avertir('%s = %d hors limites (%d a %d) : %d utilise.' % (cle, v, mini, maxi, defaut))
        return defaut
    return v


def verifier_mdp(mdp):
    mdp = (mdp or '').strip()
    if not mdp:
        raise Refus('le mot de passe RCON est vide.')
    if mdp.lower() in MDP_INTERDITS:
        raise Refus('le mot de passe RCON est encore celui par defaut (« %s »).' % ('*' * len(mdp)))
    if len(mdp) < 12:
        raise Refus('le mot de passe RCON est trop court (%d caracteres, 12 au moins).' % len(mdp))
    if not MDP_MOTIF.match(mdp):
        raise Refus('le mot de passe RCON contient un caractere refuse : seulement lettres sans '
                    'accent, chiffres, point, tiret et tiret bas (64 au plus).')
    if len(set(mdp)) < 5:
        raise Refus('le mot de passe RCON est trop simple (trop peu de caracteres differents).')
    return mdp


def verifier_identite(identite):
    identite = (identite or '').strip()
    if not IDENTITE_MOTIF.match(identite):
        raise Refus('identite du serveur « %s » refusee : lettres sans accent, chiffres, - et _ '
                    'seulement (c\'est un nom de dossier).' % texte_propre(identite))
    return identite


# ── Heure de Paris sans base de fuseaux (regle europeenne : dernier dimanche de mars et
#    d'octobre a 01:00 UTC) ───────────────────────────────────────────────────────────────

def _dernier_dimanche(annee, mois):
    d = dt.date(annee, mois, 31)
    return d - dt.timedelta(days=(d.weekday() + 1) % 7)


def decalage_paris(instant_utc):
    a = instant_utc.year
    debut = dt.datetime.combine(_dernier_dimanche(a, 3), dt.time(1, 0), dt.timezone.utc)
    fin = dt.datetime.combine(_dernier_dimanche(a, 10), dt.time(1, 0), dt.timezone.utc)
    return dt.timedelta(hours=2 if debut <= instant_utc < fin else 1)


def paris(instant_utc):
    return (instant_utc + decalage_paris(instant_utc)).replace(tzinfo=None)


def paris_vers_utc(local):
    for h in (1, 2):
        u = (local - dt.timedelta(hours=h)).replace(tzinfo=dt.timezone.utc)
        if decalage_paris(u) == dt.timedelta(hours=h):
            return u
    return (local - dt.timedelta(hours=1)).replace(tzinfo=dt.timezone.utc)


def lire_heure(texte):
    m = re.match(r'^\s*(\d{1,2})\s*(?:[:hH]\s*(\d{2})?)?\s*$', str(texte or ''))
    if not m or int(m.group(1)) > 23 or int(m.group(2) or 0) > 59:
        return None
    return int(m.group(1)), int(m.group(2) or 0)


def jour_de_wipe(rythme, jour):
    """Vrai si ce jour (date de Paris) est un jour de wipe pour ce rythme."""
    jeudi = jour.weekday() == 3
    if rythme == 'hebdo':
        return jeudi
    if rythme == '2parsemaine':
        return jeudi or jour.weekday() == 0
    if rythme == 'mois':
        return jeudi and jour.day <= 7
    if rythme == '2semaines':
        return jeudi and (jour.day <= 7 or 15 <= jour.day <= 21)
    return False


def instants(rythme, heure, depuis, jusqua):
    """Heures de wipe (UTC) entre deux dates de Paris, dans l'ordre."""
    h, m = heure
    j = depuis
    while j <= jusqua:
        if jour_de_wipe(rythme, j):
            u = paris_vers_utc(dt.datetime.combine(j, dt.time(h, m)))
            # le 1er jeudi du mois est le jour du wipe force de Facepunch
            yield u, (j.weekday() == 3 and j.day <= 7)
        j += dt.timedelta(days=1)


def periode(rythme, heure, maintenant):
    """(debut du wipe en cours en UTC, wipe force ?, prochain wipe en UTC)."""
    aujourdhui = paris(maintenant).date()
    tous = list(instants(rythme, heure, aujourdhui - dt.timedelta(days=45),
                         aujourdhui + dt.timedelta(days=45)))
    passes = [x for x in tous if x[0] <= maintenant + dt.timedelta(seconds=AVANCE)]
    futurs = [x for x in tous if x[0] > maintenant + dt.timedelta(seconds=AVANCE)]
    debut, force = passes[-1] if passes else (None, False)
    return debut, force, (futurs[0][0] if futurs else None)


def iso(u):
    return u.strftime('%Y-%m-%dT%H:%MZ') if u else ''


def lisible(u):
    if not u:
        return '?'
    p = paris(u)
    jours = ('lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche')
    return '%s %s a %s (Paris)' % (jours[p.weekday()], p.strftime('%d/%m/%Y'), p.strftime('%H:%M'))


# ── Wipe ─────────────────────────────────────────────────────────────────────────────────

def lire_temoin(dossier):
    try:
        with open(os.path.join(dossier, TEMOIN), encoding='utf-8') as f:
            t = json.load(f)
        return t if isinstance(t, dict) else {}
    except (OSError, ValueError):
        return {}


def ecrire_temoin(dossier, temoin):
    os.makedirs(dossier, exist_ok=True)
    tmp = os.path.join(dossier, TEMOIN + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(temoin, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, os.path.join(dossier, TEMOIN))


def fichiers_a_effacer(dossier, plans):
    motifs = WIPE_CARTE + (WIPE_PLANS if plans else ())
    try:
        noms = sorted(os.listdir(dossier))
    except OSError:
        return []
    return [n for n in noms if os.path.isfile(os.path.join(dossier, n))
            and any(fnmatch.fnmatchcase(n, m) for m in motifs)]


def monde_existe(dossier):
    return bool(fichiers_a_effacer(dossier, False))


def plus_fort(a, b):
    ordre = {None: 0, 'carte': 1, 'plans': 2}
    return a if ordre[a] >= ordre[b] else b


def decider_wipe(reglages, temoin, maintenant, monde, demande, avertir):
    """Renvoie (None | 'carte' | 'plans', raisons, nouveau temoin). Ne touche a aucun fichier."""
    nouveau = dict(temoin)
    choix, raisons = None, []

    # 1. fichier wipe-demande.txt pose par le site, le bot ou a la main
    if demande is not None:
        t = 'plans' if 'plan' in demande.lower() else 'carte'
        choix = plus_fort(choix, t)
        raisons.append('fichier wipe-demande.txt (%s)' % t)

    # 2. « Wipe au prochain demarrage » : fait une seule fois, quand la valeur change.
    #    AMP ne sait pas remettre ce reglage a « non » tout seul : le temoin retient la
    #    derniere valeur vue, et le wipe ne repart que si on repasse par « non ».
    prochain = str(reglages.get('WipeProchain', 'non')).strip() or 'non'
    if prochain not in ('non', 'carte', 'carte_plans'):
        avertir('Wipe au prochain demarrage = « %s » inconnu : ignore.' % prochain)
        prochain = 'non'
    vu = temoin.get('prochain_vu', 'non')
    if prochain != 'non' and prochain != vu:
        t = 'plans' if prochain == 'carte_plans' else 'carte'
        choix = plus_fort(choix, t)
        raisons.append('reglage « Wipe au prochain demarrage » (%s)' % t)
    elif prochain != 'non':
        avertir('« Wipe au prochain demarrage » est encore sur « %s », deja fait le %s : '
                'remets-le sur « non » (rien ne sera refait d\'ici la).'
                % (prochain, (temoin.get('dernier_wipe') or {}).get('le', '?')))
    nouveau['prochain_vu'] = prochain

    # 3. wipe automatique selon le rythme
    rythme = str(reglages.get('WipeRythme', 'hebdo')).strip()
    if rythme not in RYTHMES:
        avertir('Rythme du wipe « %s » inconnu : hebdo utilise.' % rythme)
        rythme = 'hebdo'
    heure = lire_heure(reglages.get('WipeHeure', '20:00'))
    if heure is None:
        avertir('Heure du wipe « %s » illisible (attendu 20:00) : 20:00 utilise.'
                % texte_propre(reglages.get('WipeHeure', '')))
        heure = (20, 0)
    auto = str(reglages.get('WipeAuto', 'non')).strip() or 'non'
    if auto not in ('non', 'carte', 'carte_plans_force', 'carte_plans'):
        avertir('Wipe automatique « %s » inconnu : non utilise.' % auto)
        auto = 'non'
    debut, force, _ = periode(rythme, heure, maintenant)
    cle = iso(debut)
    ancienne = temoin.get('periode')
    if auto != 'non' and debut is not None:
        if not ancienne:
            if monde:
                avertir('wipe automatique active sur un monde existant : pas de wipe maintenant, '
                        'le premier sera celui du %s.' % lisible(periode(rythme, heure, maintenant)[2]))
        elif ancienne != cle:
            retard = (maintenant - debut).total_seconds()
            if retard <= FENETRE:
                t = 'plans' if auto == 'carte_plans' or (auto == 'carte_plans_force' and force) else 'carte'
                choix = plus_fort(choix, t)
                raisons.append('wipe automatique du %s%s (%s)'
                               % (lisible(debut), ', wipe force du mois' if force else '', t))
            else:
                avertir('le wipe automatique du %s est passe depuis plus de %d h : il n\'est pas '
                        'fait maintenant (utilise « Wipe au prochain demarrage » si tu le veux).'
                        % (lisible(debut), FENETRE // 3600))
    if cle:
        nouveau['periode'] = cle
    return choix, raisons, nouveau


def taille_dossier(dossier):
    total = 0
    for base, _, noms in os.walk(dossier):
        for n in noms:
            try:
                total += os.path.getsize(os.path.join(base, n))
            except OSError:
                pass
    return total


def archiver(dossier, identite, type_wipe, maintenant, garder):
    """Archive datee de server/<identite>/ dans archives-wipe/, puis rotation. Renvoie le chemin."""
    os.makedirs(ARCHIVES, exist_ok=True)
    for reste in os.listdir(ARCHIVES):   # archive coupee par un arret brutal
        if reste.endswith('.partiel'):
            try:
                os.remove(os.path.join(ARCHIVES, reste))
            except OSError:
                pass
    besoin = taille_dossier(dossier)
    libre = shutil.disk_usage(ARCHIVES).free
    if libre < besoin + 512 * 1024 * 1024:
        raise Refus('pas assez de place pour l\'archive (%d Mo libres, %d Mo a archiver).'
                    % (libre // 2 ** 20, besoin // 2 ** 20))
    nom = '%s-%s-%s.tar.gz' % (identite, paris(maintenant).strftime('%Y-%m-%d_%Hh%M'), type_wipe)
    chemin = os.path.join(ARCHIVES, nom)
    partiel = chemin + '.partiel'
    with tarfile.open(partiel, 'w:gz', compresslevel=6) as tar:
        tar.add(dossier, arcname=identite)
    os.replace(partiel, chemin)
    # rotation : on garde les N plus recentes de CETTE identite
    miennes = sorted(n for n in os.listdir(ARCHIVES)
                     if n.startswith(identite + '-') and n.endswith('.tar.gz'))
    for vieux in miennes[:-garder] if garder > 0 else []:
        try:
            os.remove(os.path.join(ARCHIVES, vieux))
            dire('ancienne archive supprimee : %s' % vieux)
        except OSError:
            pass
    return chemin


def graine_existante(dossier, taille):
    """Graine lue dans le nom des fichiers du monde (proceduralmap.<taille>.<graine>.<v>.sav)."""
    for n in fichiers_a_effacer(dossier, False):
        m = re.match(r'^proceduralmap\.(\d+)\.(\d+)\.\d+\.(?:sav|map)', n)
        if m and int(m.group(1)) == taille:
            return int(m.group(2))
    return None


def tirer_graine(reglages, actuelle, hasard=random):
    liste = [int(x) for x in re.findall(r'\d+', str(reglages.get('GrainesListe', '')))
             if 0 < int(x) <= 2147483647]
    candidates = [g for g in liste if g != actuelle] or liste
    if candidates:
        return hasard.choice(candidates)
    return hasard.randint(1, 2147483647)


def choisir_graine(reglages, temoin, dossier, taille, wipe_fait, avertir, hasard=random):
    brut = str(reglages.get('WorldSeed', 'auto')).strip().lower()
    if brut.isdigit():
        g = int(brut)
        if 0 <= g <= 2147483647:
            return g, False
        avertir('graine %s trop grande : auto utilise.' % brut)
    elif brut not in ('', 'auto'):
        avertir('graine « %s » illisible (un nombre ou auto) : auto utilise.' % texte_propre(brut))
    actuelle = temoin.get('graine')
    if isinstance(actuelle, int) and not wipe_fait:
        return actuelle, False
    if not wipe_fait:
        trouvee = graine_existante(dossier, taille)
        if trouvee is not None:
            return trouvee, True
    return tirer_graine(reglages, actuelle if isinstance(actuelle, int) else None, hasard), True


def preparer_monde(reglages, dossier, identite, taille, maintenant, avertir, hasard=random):
    """Wipe eventuel + graine. Renvoie (graine, rapport)."""
    temoin = lire_temoin(dossier)
    demande = None
    if os.path.isfile(DEMANDE):
        try:
            with open(DEMANDE, encoding='utf-8', errors='replace') as f:
                demande = f.read(200)
        except OSError:
            demande = ''
    choix, raisons, nouveau = decider_wipe(reglages, temoin, maintenant, monde_existe(dossier),
                                           demande, avertir)
    rapport = {'wipe': None}
    wipe_fait = False
    if choix:
        a_effacer = fichiers_a_effacer(dossier, choix == 'plans')
        dire('WIPE (%s) demande par : %s.' % ('carte + plans' if choix == 'plans' else 'carte',
                                              ' ; '.join(raisons)))
        garder = entier(reglages, 'ArchivesGardees', 4, 1, 50, avertir)
        try:
            if a_effacer:
                dire('archive de server/%s/ en cours (le serveur demarre juste apres)...' % identite)
                chemin = archiver(dossier, identite, choix, maintenant, garder)
                dire('archive faite : %s (%d Mo).' % (os.path.relpath(chemin, RACINE),
                                                      os.path.getsize(chemin) // 2 ** 20))
            else:
                dire('aucun fichier de monde a effacer : pas d\'archive.')
            for n in a_effacer:
                os.remove(os.path.join(dossier, n))
            if a_effacer:
                dire('fichiers effaces (%d) : %s' % (len(a_effacer), ', '.join(a_effacer)))
            wipe_fait = True
        except (Refus, OSError, tarfile.TarError) as e:
            dire('ATTENTION : wipe ANNULE, rien n\'a ete efface : %s Le serveur demarre sur '
                 'l\'ancien monde ; le wipe sera retente au prochain demarrage.' % e)
            nouveau['prochain_vu'] = temoin.get('prochain_vu', 'non')
            if 'periode' in temoin:
                nouveau['periode'] = temoin['periode']
            else:
                nouveau.pop('periode', None)
        if wipe_fait:
            nouveau['dernier_wipe'] = {'le': paris(maintenant).strftime('%Y-%m-%d %H:%M'),
                                       'type': choix, 'raison': ' ; '.join(raisons)}
            rapport['wipe'] = choix
            if demande is not None:
                try:
                    os.remove(DEMANDE)
                except OSError:
                    pass
    graine, tiree = choisir_graine(reglages, nouveau, dossier, taille, wipe_fait, avertir, hasard)
    if str(reglages.get('WorldSeed', 'auto')).strip().lower() in ('', 'auto'):
        nouveau['graine'] = graine
        if tiree and wipe_fait:
            dire('nouvelle graine tiree : %d.' % graine)
    ecrire_temoin(dossier, nouveau)
    rapport['graine'] = graine
    return graine, rapport


# ── Admins, Oxide, plugins ───────────────────────────────────────────────────────────────

def ecrire_users_cfg(reglages, dossier, avertir):
    lignes = []
    for cle, mot in (('OwnerIds', 'ownerid'), ('ModeratorIds', 'moderatorid')):
        for brut in re.split(r'[,;\s]+', str(reglages.get(cle, ''))):
            if not brut:
                continue
            if STEAMID_MOTIF.match(brut):
                lignes.append('%s %s' % (mot, brut))
            else:
                avertir('%s : « %s » n\'est pas un SteamID64 (17 chiffres, 7656119...) : ignore.'
                        % (cle, texte_propre(brut)[:40]))
    cfg = os.path.join(dossier, 'cfg')
    os.makedirs(cfg, exist_ok=True)
    with open(os.path.join(cfg, 'users.cfg'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(''.join(l + '\n' for l in lignes))
    return lignes


def commande_curl():
    brut = os.environ.get('TK_CURL')
    if brut:
        return json.loads(brut)
    return ['curl']


def telecharger(url, cible):
    """curl (l'API de umod.org refuse les clients Python), vers un fichier temporaire."""
    tmp = cible + '.tmp'
    try:
        r = subprocess.run(commande_curl() + ['-sfL', '--max-time', '90', '-o', tmp, url],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not os.path.isfile(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None
    return tmp


def remettre_managed_si_absent():
    # L'Update met Managed de cote avant SteamCMD (Steam bute sur les DLL modifiees par Oxide).
    # Si SteamCMD a echoue ensuite, Managed manque : on remet l'ancien pour que le serveur demarre.
    data = os.path.join(ICI, 'RustDedicated_Data')
    managed, ancien = os.path.join(data, 'Managed'), os.path.join(data, 'Managed-avant-maj')
    if os.path.isfile(os.path.join(managed, 'Assembly-CSharp.dll')) or not os.path.isdir(ancien):
        return
    if os.path.isdir(managed):
        shutil.rmtree(managed, ignore_errors=True)
    os.rename(ancien, managed)
    dire('ATTENTION : la mise a jour de Steam n\'a pas abouti, ancien dossier Managed remis. '
         'Relance Update dans AMP.')


def installer_oxide_si_absent(reglages):
    if not vrai(reglages.get('InstallOxide', 'true')) or os.path.isfile(OXIDE_DLL):
        return
    dire('Oxide coche mais absent : telechargement de la derniere version...')
    tmp = telecharger(OXIDE_URL, os.path.join(ICI, 'Oxide.Rust-linux.zip'))
    if not tmp:
        dire('ATTENTION : Oxide n\'a pas pu etre telecharge : le serveur demarre SANS plugins. '
             'Lance Update dans AMP.')
        return
    try:
        with zipfile.ZipFile(tmp) as z:
            for nom in z.namelist():
                if nom.startswith('/') or '..' in nom.split('/'):
                    raise zipfile.BadZipFile('chemin refuse : ' + nom)
            z.extractall(ICI)
        dire('Oxide installe.')
    except (zipfile.BadZipFile, OSError) as e:
        dire('ATTENTION : archive Oxide illisible (%s) : serveur sans plugins.' % e)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def liste_plugins(reglages, avertir):
    coches, decoches = [], []
    for cle, valeur in reglages.items():
        if cle.startswith('UmodP_'):
            (coches if vrai(valeur) else decoches).append(cle[len('UmodP_'):])
    libres = []
    for brut in re.split(r'[,;\s]+', str(reglages.get('UmodPlugins', ''))):
        if not brut:
            continue
        nom = brut[:-3] if brut.lower().endswith('.cs') else brut
        if PLUGIN_MOTIF.match(nom):
            libres.append(nom)
        else:
            avertir('plugin « %s » refuse (nom uMod en lettres et chiffres, ex. BetterChat).'
                    % texte_propre(brut)[:40])
    voulus = sorted(set(coches) | set(libres))
    a_retirer = sorted(set(decoches) - set(voulus))
    return voulus, a_retirer


def poser_plugins_du_wipe(reglages, maintenant, avertir):
    """Au wipe, AVANT le demarrage : chaque <Nom>.cs de oxide/plugins-au-prochain-wipe/ (depose par la page
    « Plugins Rust » du panel TeamKit) passe dans oxide/plugins/ ; la version d'avant est gardee dans
    oxide/plugins-precedents/<Nom>.cs.<AAAAMMJJ-HHMMSS>. Un nom ou un contenu suspect reste ou il est."""
    if not os.path.isdir(AU_WIPE):
        return []
    plugins = os.path.join(ICI, 'oxide', 'plugins')
    os.makedirs(plugins, exist_ok=True)
    date = paris(maintenant).strftime('%Y%m%d-%H%M%S')
    poses = []
    for nom in sorted(os.listdir(AU_WIPE)):
        src = os.path.join(AU_WIPE, nom)
        if not (nom.endswith('.cs') and PLUGIN_MOTIF.match(nom[:-3])) or os.path.islink(src) or not os.path.isfile(src):
            avertir('plugins-au-prochain-wipe : « %s » ignore (attendu <Nom>.cs).' % texte_propre(nom)[:60])
            continue
        with open(src, 'rb') as f:
            contenu = f.read(2100000)
        if b'Oxide.Plugins' not in contenu or len(contenu) > 2000000:
            avertir('plugins-au-prochain-wipe : %s ignore (pas un plugin Oxide, ou plus de 2 Mo).' % nom)
            continue
        cible = os.path.join(plugins, nom)
        if os.path.isfile(cible):
            os.makedirs(PRECEDENTS, exist_ok=True)
            shutil.copy2(cible, os.path.join(PRECEDENTS, '%s.%s' % (nom, date)))
        os.replace(src, cible)
        poses.append(nom[:-3])
    if poses:
        dire('wipe : %d plugin(s) mis en service depuis oxide/plugins-au-prochain-wipe/ : %s '
             '(anciennes versions dans oxide/plugins-precedents/).' % (len(poses), ', '.join(poses)))
        if vrai(reglages.get('MajPluginsDemarrage', 'true')):
            avertir('« Mettre a jour les plugins au demarrage » est sur Oui : uMod va remplacer ces versions '
                    'par sa derniere. Pour une saison figee, mets ce reglage sur Non.')
    return poses


def creer_dossier_copypaste():
    """CopyPaste plante (copylist, paste) si oxide/data/copypaste/ n'existe pas (vu au banc local le 18/10/2026)."""
    d = os.path.join(ICI, 'oxide', 'data', 'copypaste')
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
        dire('dossier oxide/data/copypaste/ cree (CopyPaste en a besoin).')


def gerer_plugins(reglages, avertir):
    if not vrai(reglages.get('InstallOxide', 'true')):
        return
    plugins = os.path.join(ICI, 'oxide', 'plugins')
    cote = os.path.join(ICI, 'oxide', 'plugins-desactives')
    os.makedirs(plugins, exist_ok=True)
    voulus, a_retirer = liste_plugins(reglages, avertir)
    for nom in a_retirer:
        src = os.path.join(plugins, nom + '.cs')
        if os.path.isfile(src):
            os.makedirs(cote, exist_ok=True)
            os.replace(src, os.path.join(cote, nom + '.cs'))
            dire('plugin decoche, mis de cote : %s (oxide/plugins-desactives/)' % nom)
    maj = vrai(reglages.get('MajPluginsDemarrage', 'true'))
    ok, echecs, gardes = [], [], []
    for nom in voulus:
        cible = os.path.join(plugins, nom + '.cs')
        if not maj and os.path.isfile(cible):
            gardes.append(nom)
            continue
        tmp = telecharger(UMOD_URL % nom, cible)
        contenu = b''
        if tmp:
            with open(tmp, 'rb') as f:
                contenu = f.read(400000)
        if tmp and b'Oxide.Plugins' in contenu:
            os.replace(tmp, cible)
            ok.append(nom)
        else:
            if tmp:
                os.remove(tmp)
            echecs.append(nom)
    if ok:
        dire('plugins uMod telecharges (%d) : %s' % (len(ok), ', '.join(ok)))
    if gardes:
        dire('saison figee : %d plugins gardes tels quels (pas de mise a jour).' % len(gardes))
    for nom in echecs:
        dire('ATTENTION : plugin %s non telecharge (nom inconnu sur umod.org ou site injoignable)%s.'
             % (nom, ' : l\'ancienne version reste en place'
                if os.path.isfile(os.path.join(plugins, nom + '.cs')) else ''))


# ── Arguments de RustDedicated ───────────────────────────────────────────────────────────

def construire(reglages, ports, joueurs, graine, perso, avertir):
    """Liste des arguments de RustDedicated. Les champs vides sont SAUTES : une convar vide
    sur la ligne de commande devient « 1 » pour Rust (server.levelurl "" -> carte « 1 »)."""
    jeu, requetes, rcon, rustplus = ports
    mdp = verifier_mdp(reglages.get('RconPassword', ''))
    identite = verifier_identite(reglages.get('ServerIdentity', 'teamkit'))
    rythme = str(reglages.get('WipeRythme', 'hebdo')).strip()
    tag_rythme, fin_nom = RYTHMES.get(rythme, RYTHMES['hebdo'])

    nom = texte_propre(reglages.get('Hostname', '')) or '[FR] TeamKit | Rust'
    nom = nom.replace('{wipe}', fin_nom)
    tags = [tag_rythme]
    for t in re.split(r'[,\s]+', texte_propre(reglages.get('ServerTags', ''))):
        if t and t.lower() not in TAGS_DE_WIPE and t not in tags:
            tags.append(t)

    args = ['-batchmode', '-nographics', '-logfile', '/dev/stdout',
            '+server.ip', '0.0.0.0', '+server.port', str(jeu), '+server.queryport', str(requetes),
            '+rcon.web', 'true', '+rcon.port', str(rcon), '+rcon.password', mdp,
            '+app.port', str(rustplus), '+app.listenip', '0.0.0.0',
            '+server.identity', identite, '+server.hostname', nom, '+server.tags', ','.join(tags),
            '+server.maxplayers', str(joueurs),
            '+server.worldsize', str(entier(reglages, 'WorldSize', 4000, 1000, 6000, avertir)),
            '+server.seed', str(graine),
            '+server.saveinterval', str(entier(reglages, 'SaveInterval', 300, 60, 3600, avertir)),
            '+server.tickrate', str(entier(reglages, 'Tickrate', 30, 10, 60, avertir)),
            '+fps.limit', str(entier(reglages, 'FpsLimit', 60, 30, 256, avertir)),
            '+relationshipmanager.maxteamsize', str(entier(reglages, 'TailleEquipe', 4, 1, 32, avertir)),
            '+server.combatlogsize', str(entier(reglages, 'CombatLogSize', 100, 30, 1000, avertir)),
            # EAC impose : aucun reglage ne permet de le couper
            '+server.secure', 'true']
    optionnels = (('ServerDescription', 'server.description'), ('ServerUrl', 'server.url'),
                  ('HeaderImage', 'server.headerimage'), ('LogoImage', 'server.logoimage'),
                  ('PublicIp', 'app.publicip'), ('LevelUrl', 'server.levelurl'))
    for cle, convar in optionnels:
        v = texte_propre(reglages.get(cle, ''))
        if v:
            args += ['+' + convar, v]
    return args + filtrer_perso(perso, avertir)


# convars qu'un argument perso ne doit pas remplacer : le mot de passe passerait par la ligne
# de commande d'AMP, et EAC doit rester impose
PERSO_INTERDITS = {'+rcon.password', '-rcon.password', '+server.secure', '+rcon.port', '+server.identity'}


def filtrer_perso(perso, avertir):
    garde, sauter = [], False
    for a in perso:
        if sauter:
            sauter = False
            continue
        if a.lower() in PERSO_INTERDITS:
            avertir('argument perso %s retire (gere par le modele ; le mot de passe RCON ne passe '
                    'jamais par la ligne de commande d\'AMP).' % a)
            sauter = True
            continue
        garde.append(a)
    return garde


# ── RCON (WebSocket de Rust, client minimal sans bibliotheque) ───────────────────────────

class Rcon:
    def __init__(self, port, mdp, hote='127.0.0.1'):
        self.port, self.mdp, self.hote = port, mdp, hote
        self.sock = None
        self.verrou = threading.Lock()
        self.ident = 1000

    def fermer(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def _lire_exact(self, n):
        donnees = b''
        while len(donnees) < n:
            bloc = self.sock.recv(n - len(donnees))
            if not bloc:
                raise ConnectionError('connexion RCON fermee')
            donnees += bloc
        return donnees

    def _connecter(self):
        s = socket.create_connection((self.hote, self.port), timeout=5)
        cle = base64.b64encode(os.urandom(16)).decode()
        s.sendall(('GET /%s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n'
                   'Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n'
                   % (self.mdp, self.hote, self.port, cle)).encode())
        reponse = b''
        while b'\r\n\r\n' not in reponse:
            bloc = s.recv(1024)
            if not bloc:
                s.close()
                raise ConnectionError('le serveur a refuse la connexion RCON (mot de passe ?)')
            reponse += bloc
            if len(reponse) > 16384:
                break
        if b' 101 ' not in reponse.split(b'\r\n', 1)[0]:
            s.close()
            raise ConnectionError('reponse RCON inattendue')
        self.sock = s

    def _envoyer(self, opcode, charge):
        masque = os.urandom(4)
        n = len(charge)
        if n < 126:
            entete = struct.pack('!BB', 0x80 | opcode, 0x80 | n)
        elif n < 65536:
            entete = struct.pack('!BBH', 0x80 | opcode, 0x80 | 126, n)
        else:
            entete = struct.pack('!BBQ', 0x80 | opcode, 0x80 | 127, n)
        self.sock.sendall(entete + masque + bytes(b ^ masque[i % 4] for i, b in enumerate(charge)))

    def _recevoir(self):
        message = b''
        while True:
            b1, b2 = self._lire_exact(2)
            opcode, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack('!H', self._lire_exact(2))[0]
            elif n == 127:
                n = struct.unpack('!Q', self._lire_exact(8))[0]
            masque = self._lire_exact(4) if b2 & 0x80 else None
            charge = self._lire_exact(n) if n else b''
            if masque:
                charge = bytes(b ^ masque[i % 4] for i, b in enumerate(charge))
            if opcode == 8:
                raise ConnectionError('connexion RCON fermee par le serveur')
            if opcode == 9:
                self._envoyer(10, charge)
                continue
            if opcode in (0, 1, 2):
                message += charge
                if b1 & 0x80:
                    return message.decode('utf-8', 'replace')

    def commande(self, texte, attendre=True, delai=30):
        """Envoie une commande ; renvoie le texte de la reponse (ou '' sans attente)."""
        with self.verrou:
            for essai in (1, 2):
                try:
                    if not self.sock:
                        self._connecter()
                    self.ident += 1
                    ident = self.ident
                    self._envoyer(1, json.dumps({'Identifier': ident, 'Message': texte,
                                                 'Name': 'TeamKit'}).encode('utf-8'))
                    if not attendre:
                        return ''
                    fin = time.monotonic() + delai
                    while True:
                        reste = fin - time.monotonic()
                        if reste <= 0:
                            raise TimeoutError('pas de reponse du RCON en %d s' % delai)
                        self.sock.settimeout(reste)
                        try:
                            obj = json.loads(self._recevoir())
                        except ValueError:
                            continue
                        if isinstance(obj, dict) and obj.get('Identifier') == ident:
                            return str(obj.get('Message', ''))
                except (OSError, ConnectionError) as e:
                    self.fermer()
                    if essai == 2 or isinstance(e, (TimeoutError, socket.timeout)):
                        raise


# ── Lancement ────────────────────────────────────────────────────────────────────────────

def joueurs_max(brut):
    if re.match(r'^\d{1,4}$', str(brut)) and 1 <= int(brut) <= 1000:
        return int(brut)
    for kvp in (os.path.join(RACINE, '..', 'GenericModule.kvp'), os.path.join(RACINE, 'GenericModule.kvp')):
        try:
            with open(kvp, encoding='utf-8', errors='replace') as f:
                m = re.search(r'^App\.MaxUsers=(\d{1,4})\r?$', f.read(), re.M)
            if m:
                dire('joueurs maximum lus dans les reglages de l\'instance : %s' % m.group(1))
                return int(m.group(1))
        except OSError:
            pass
    return 150


def commande_serveur():
    brut = os.environ.get('TK_RUST_CMD')
    if brut:
        return json.loads(brut)
    return [os.path.join(ICI, 'RustDedicated')]


def maintenant_utc():
    brut = os.environ.get('TK_MAINTENANT')
    if brut:
        return dt.datetime.strptime(brut, '%Y-%m-%dT%H:%MZ').replace(tzinfo=dt.timezone.utc)
    return dt.datetime.now(dt.timezone.utc)


def lancer(argv):
    if len(argv) < 5 or not all(re.match(r'^\d{1,5}$', a) for a in argv[:4]):
        dire('ERREUR : arguments attendus : <port jeu> <port requetes> <port rcon> <port rust+> '
             '<joueurs> [arguments perso]. Recupere la derniere version du modele dans ADS.')
        return 1
    ports = tuple(int(a) for a in argv[:4])
    joueurs = joueurs_max(argv[4])
    perso = argv[5:]
    serveur = commande_serveur()
    if not os.environ.get('TK_RUST_CMD') and not os.access(serveur[0], os.X_OK):
        dire('ERREUR : RustDedicated absent. Lance Update dans AMP (environ 10 Go).')
        return 1
    if not os.path.isfile(REGLAGES):
        dire('ERREUR : reglages-rust.txt absent. AMP l\'ecrit au demarrage a partir de '
             'AMP_reglages-rust.txt : lance Update une fois, puis demarre.')
        return 1
    try:
        os.chmod(REGLAGES, 0o600)
    except OSError:
        pass
    with open(REGLAGES, encoding='utf-8', errors='replace') as f:
        reglages = lire_reglages(f.read())

    def avertir(m):
        dire('note : ' + m)

    try:
        mdp = verifier_mdp(reglages.get('RconPassword', ''))
        identite = verifier_identite(reglages.get('ServerIdentity', 'teamkit'))
    except Refus as e:
        dire('DEMARRAGE REFUSE : ' + str(e))
        dire('Va dans Configuration > Rust TeamKit > Admins et RCON, choisis un mot de passe RCON '
             'de 12 caracteres ou plus (lettres, chiffres, point, tiret), enregistre, puis demarre.')
        return 1
    dossier = os.path.join(ICI, 'server', identite)
    os.makedirs(os.path.join(dossier, 'cfg'), exist_ok=True)
    maintenant = maintenant_utc()
    taille = entier(reglages, 'WorldSize', 4000, 1000, 6000, lambda m: None)

    admins = ecrire_users_cfg(reglages, dossier, avertir)
    dire('users.cfg : %d proprietaire(s), %d moderateur(s).'
         % (sum(l.startswith('ownerid') for l in admins), sum(l.startswith('moderatorid') for l in admins)))
    graine, rapport = preparer_monde(reglages, dossier, identite, taille, maintenant, avertir)
    poses = poser_plugins_du_wipe(reglages, maintenant, avertir) if rapport.get('wipe') else []
    remettre_managed_si_absent()
    installer_oxide_si_absent(reglages)
    gerer_plugins(reglages, avertir)
    if vrai(reglages.get('InstallOxide', 'true')):
        creer_dossier_copypaste()
    try:
        args = construire(reglages, ports, joueurs, graine, perso, avertir)
    except Refus as e:
        dire('DEMARRAGE REFUSE : ' + str(e))
        return 1

    rythme = str(reglages.get('WipeRythme', 'hebdo')).strip()
    heure = lire_heure(reglages.get('WipeHeure', '20:00')) or (20, 0)
    _, _, prochain = periode(rythme if rythme in RYTHMES else 'hebdo', heure, maintenant)
    auto = str(reglages.get('WipeAuto', 'non')).strip()
    dire('modele Rust (TeamKit) v%d : identite %s, carte %d graine %d, %d places, rythme %s, '
         'wipe automatique %s, prochain jour de wipe %s.'
         % (VERSION_MODELE, identite, taille, graine, joueurs, rythme, auto or 'non', lisible(prochain)))
    with open(ETAT, 'w', encoding='utf-8') as f:
        json.dump({'rcon_port': ports[2], 'identite': identite, 'graine': graine,
                   'demarre_le': iso(maintenant), 'wipe': rapport.get('wipe'), 'plugins_poses_au_wipe': poses}, f)
    wp = bool(rapport.get('wipe')) and os.path.isfile(os.path.join(ICI, 'oxide', 'plugins', 'WipeProtection.cs'))
    return tourner(serveur + args, ports[2], mdp, apres_pret=['wp start'] if wp else [])


def tourner(commande, port_rcon, mdp, apres_pret=()):
    rcon = Rcon(port_rcon, mdp)
    pret = threading.Event()
    sauve = threading.Event()
    arret = {'demande': False}

    kwargs = {}
    if os.name == 'posix':
        libc = ctypes.CDLL(None, use_errno=True)

        def pdeathsig():
            libc.prctl(1, signal.SIGTERM)   # si ce script meurt, le serveur recoit SIGTERM
        kwargs = {'preexec_fn': pdeathsig, 'start_new_session': True}
    proc = subprocess.Popen(commande, cwd=ICI, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, **kwargs)

    def attendre_fin(secondes):
        fin = time.monotonic() + secondes
        while time.monotonic() < fin:
            if proc.poll() is not None:
                return True
            time.sleep(0.5)
        return proc.poll() is not None

    def arreter_proprement():
        if arret['demande']:
            return
        arret['demande'] = True
        dire('arret demande : sauvegarde du monde (server.save) par RCON...')
        sauve.clear()
        try:
            rcon.commande('server.save', attendre=True, delai=90)
            sauve.wait(10)
            dire('sauvegarde faite. Fermeture du serveur (quit).')
            try:
                rcon.commande('quit', attendre=False)
            except (OSError, ConnectionError):
                pass
            if attendre_fin(75):
                return
            dire('le serveur ne s\'est pas ferme apres quit : SIGTERM.')
        except (OSError, ConnectionError) as e:
            dire('RCON injoignable (%s) : SIGTERM envoye au serveur. La derniere sauvegarde '
                 'automatique date d\'au plus « Sauvegarde auto » secondes.' % e)
        if proc.poll() is None:
            proc.terminate()
            if not attendre_fin(15):
                dire('le serveur ne repond plus : arret force.')
                proc.kill()

    def sur_signal(signum, frame):
        threading.Thread(target=arreter_proprement, daemon=True).start()

    signal.signal(signal.SIGTERM, sur_signal)
    signal.signal(signal.SIGINT, sur_signal)

    def relais():
        # Console d'AMP -> RCON du serveur, une commande par ligne. os.read et pas sys.stdin :
        # un fil bloque dans sys.stdin garde son verrou et Python plante en s'arretant.
        reste = b''
        while True:
            try:
                bloc = os.read(0, 4096)
            except OSError:
                return
            if not bloc:
                return
            *lignes, reste = (reste + bloc).split(b'\n')
            for brute in lignes:
                l = brute.decode('utf-8', 'replace').strip()
                if not l:
                    continue
                if l.lower() in ('quit', 'exit', 'stop', 'arret', 'arrêt', 'global.quit'):
                    threading.Thread(target=arreter_proprement, daemon=True).start()
                    continue
                if l.lower().startswith('teamkit.'):
                    commande_teamkit(l)
                    continue
                if not pret.is_set():
                    dire('serveur pas encore pret (« Server startup complete ») : commande '
                         'envoyee quand meme.')
                try:
                    reponse = rcon.commande(l, attendre=True, delai=20)
                    for r in (reponse.rstrip('\n').split('\n') if reponse.strip() else []):
                        ecrire(('[RCON] ' + r).encode('utf-8', 'replace') + b'\n')
                except (OSError, ConnectionError) as e:
                    dire('commande non transmise (%s).' % e)
    threading.Thread(target=relais, daemon=True).start()

    def apres_demarrage():
        # Apres un wipe : WipeProtection compte ses 3 h depuis la creation de la carte ; si sa config arrive apres,
        # il garde 24 h. « wp start » remet le compteur au demarrage (constat du banc local, 18/10/2026).
        for c in apres_pret:
            try:
                reponse = rcon.commande(c, attendre=True, delai=20)
                premiere = reponse.strip().split('\n')[0][:120] if reponse.strip() else ''
                dire('apres le wipe : « %s » envoye par RCON%s' % (c, (' : ' + premiere) if premiere else ''))
            except (OSError, ConnectionError) as e:
                dire('apres le wipe : « %s » non transmis (%s) : le taper dans la console.' % (c, e))

    for brute in iter(proc.stdout.readline, b''):
        brute = ANSI.sub(b'', brute)   # codes couleur retires : pour les regex d'AMP et pour nos comparaisons
        ecrire(brute if brute.endswith(b'\n') else brute + b'\n')
        texte = brute.decode('utf-8', 'replace').strip()
        if texte == 'Server startup complete':
            if not pret.is_set() and apres_pret:
                threading.Thread(target=apres_demarrage, daemon=True).start()
            pret.set()
        elif SAUVE_MOTIF.match(texte):
            sauve.set()
        elif MONDE_NEUF.match(texte):
            dire('monde neuf : aucune sauvegarde a recharger (carte generee).')
        else:
            m = MONDE_RECHARGE.match(texte)
            if m:
                dire('monde recharge depuis la sauvegarde (%s entites).' % m.group(1))
    code = proc.wait()
    rcon.fermer()
    if arret['demande'] and sauve.is_set():
        # RustDedicated rend -1 apres « quit » (255 sous Linux, 4294967295 sous Windows) : l'arret a ete demande
        # et la sauvegarde vue, c'est un arret normal pour AMP.
        return 0
    if code and not arret['demande']:
        dire('le serveur s\'est arrete (code %d).' % code)
    return code


def commande_teamkit(ligne):
    """teamkit.wipe carte|plans|annuler et teamkit.periode, tapes dans la console d'AMP (ou
    envoyes par le bot avec l'API d'AMP). Le wipe se fait au PROCHAIN demarrage."""
    mots = ligne.split()
    if mots[0].lower() == 'teamkit.wipe' and len(mots) == 2 and mots[1].lower() in ('carte', 'plans'):
        with open(DEMANDE, 'w', encoding='utf-8') as f:
            f.write(mots[1].lower() + '\n')
        dire('wipe (%s) demande : il sera fait au prochain demarrage, apres une archive.'
             % ('carte + plans' if mots[1].lower() == 'plans' else 'carte'))
    elif mots[0].lower() == 'teamkit.wipe' and len(mots) == 2 and mots[1].lower() == 'annuler':
        try:
            os.remove(DEMANDE)
            dire('demande de wipe annulee.')
        except OSError:
            dire('aucune demande de wipe en attente.')
    elif mots[0].lower() == 'teamkit.periode':
        commande_periode()
    else:
        dire('commandes TeamKit : teamkit.wipe carte | teamkit.wipe plans | teamkit.wipe annuler '
             '| teamkit.periode')


# ── Commandes annexes ────────────────────────────────────────────────────────────────────

def commande_rcon(argv):
    try:
        with open(REGLAGES, encoding='utf-8', errors='replace') as f:
            mdp = lire_reglages(f.read()).get('RconPassword', '')
        with open(ETAT, encoding='utf-8') as f:
            port = int(json.load(f)['rcon_port'])
    except (OSError, ValueError, KeyError) as e:
        dire('ERREUR : serveur jamais demarre par ce lanceur (%s).' % e)
        return 1
    try:
        print(Rcon(port, mdp).commande(' '.join(argv), attendre=True, delai=30))
    except (OSError, ConnectionError) as e:
        dire('ERREUR RCON : %s' % e)
        return 1
    return 0


def commande_periode():
    reglages = {}
    try:
        with open(REGLAGES, encoding='utf-8', errors='replace') as f:
            reglages = lire_reglages(f.read())
    except OSError:
        pass
    rythme = str(reglages.get('WipeRythme', 'hebdo')).strip()
    rythme = rythme if rythme in RYTHMES else 'hebdo'
    heure = lire_heure(reglages.get('WipeHeure', '20:00')) or (20, 0)
    debut, force, prochain = periode(rythme, heure, maintenant_utc())
    dire(json.dumps({'rythme': rythme, 'tag': RYTHMES[rythme][0], 'wipe_en_cours': iso(debut),
                      'wipe_en_cours_force': force, 'prochain_wipe': iso(prochain),
                      'prochain_wipe_paris': lisible(prochain)}, ensure_ascii=False))
    return 0


def main(argv):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
    except AttributeError:
        pass
    if argv and argv[0] == 'lancer':
        code = lancer(argv[1:])
        sys.stdout.flush()
        os._exit(code if 0 <= code < 256 else 1)
    if argv and argv[0] == 'rcon' and len(argv) > 1:
        return commande_rcon(argv[1:])
    if argv and argv[0] == 'periode':
        return commande_periode()
    dire('Usage : lancer-rust.py lancer <jeu> <requetes> <rcon> <rust+> <joueurs> [perso] '
         '| rcon <commande> | periode')
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
