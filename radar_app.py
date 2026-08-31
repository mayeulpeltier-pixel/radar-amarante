# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- APPLICATION WEB (etape 3 du cap produit).
============================================================

CE QUE C'EST
------------
Le dashboard servi comme une APPLICATION : lecture Postgres (plus de Sheet),
authentification obligatoire, et une API de statuts qui ecrit la zone humaine
EN BASE. C'est la piece qui transforme le radar en logiciel : la meme
application, avec une autre DATABASE_URL, servira un autre client.

CE QUE CE N'EST PAS (v1, honnete)
---------------------------------
Le bouton "Je contacte" de la page reste cable sur l'Apps Script tant que le
Sheet est la reference CRM. L'API POST /api/statut est prete et testee ; le
recablage du bouton viendra quand la bascule sera decidee.

ARCHITECTURE : ZERO DUPLICATION DE RENDU
----------------------------------------
L'application ne reinvente rien : elle lit les onglets dans radar_lignes
(forme PLATE canonique, celle du Sheet), superpose radar_statuts, puis passe
le tout aux fonctions EXISTANTES de radar_dashboard :
    lire (ici, Postgres) -> construire_leads -> generer_html
Toute evolution du dashboard profite donc aux deux mondes sans double
maintenance.

AUTHENTIFICATION (fermee par defaut)
------------------------------------
HTTP Basic. RADAR_APP_MOT_DE_PASSE est OBLIGATOIRE : sans lui, l'application
repond 503 partout (hors /sante) plutot que de servir l'intelligence
commerciale en clair. RADAR_APP_UTILISATEUR est optionnel (defaut "radar").

ENV : DATABASE_URL, RADAR_APP_MOT_DE_PASSE, RADAR_APP_UTILISATEUR (opt.)
LANCEMENT LOCAL :  uvicorn radar_app:app --port 8000
HEBERGEMENT      : Render (render.yaml a la racine du depot).
"""

import os
import secrets as _secrets
import threading
import time

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import radar_dashboard as dash
import radar_stockage as st


# ===========================================================================
# PERFORMANCE : COMPRESSION + CACHE
# ===========================================================================
# Mesures du 22/07/2026 sur les volumes reels (3 200 lignes, ~2 500 leads) :
#     page HTML brute .......... 2,6 Mo
#     page compressee (gzip) ... 96 Ko   (28x plus legere)
#     generation complete ...... 0,27 s en local (+ latence reseau vers Neon)
# Deux leviers, tous deux sans risque fonctionnel :
#   1. GZip : 2,6 Mo transferes a chaque chargement, c'est inacceptable en
#      mobilite. uvicorn ne compresse RIEN par defaut.
#   2. Cache memoire : les donnees ne changent que deux fois par semaine (runs
#      du lundi et du jeudi). Regenerer la page a chaque rafraichissement est
#      du gaspillage pur. TTL court malgre tout, et invalidation immediate des
#      qu'un statut est pose, pour que l'utilisateur voie toujours son action.
#
# ---------------------------------------------------------------------------
# NOTE MULTI-CLIENT (revue le 23/07/2026)
# ---------------------------------------------------------------------------
# La note precedente disait : "ce cache est GLOBAL au processus ; des lors que
# l'application servira plusieurs clients, la cle de cache devra inclure
# l'identite du client, sinon fuite de donnees entre comptes."
#
# C'est vrai, mais incomplet au point d'etre trompeur, et ca merite d'etre
# ecrit noir sur blanc plutot que corrige a moitie le jour de la bascule.
#
# LE CACHE N'EST PAS LA VULNERABILITE. Aujourd'hui `lire_onglets_pg` lit
# TOUTES les lignes de `radar_lignes`, sans aucune notion de client. Le jour ou
# deux comptes existeraient, ils verraient les memes donnees meme avec des
# caches parfaitement separes : la page generee pour l'un est identique a celle
# de l'autre. Cloisonner le cache reglerait donc un symptome et donnerait le
# sentiment que le sujet est traite, ce qui est pire que de ne rien faire.
#
# CE QU'UN VRAI MULTI-CLIENT EXIGE, dans cet ordre :
#   1. une dimension `client` dans le modele de donnees (colonne sur
#      `radar_lignes` et `radar_statuts`, ou une base par client) ;
#   2. le filtrage de CETTE dimension dans `lire_onglets_pg` et
#      `superposer_statuts` -- c'est la que se joue la confidentialite ;
#   3. des identifiants par client, la ou il n'y a aujourd'hui qu'un couple
#      RADAR_APP_UTILISATEUR / RADAR_APP_MOT_DE_PASSE ;
#   4. ALORS SEULEMENT, une cle de cache incluant l'identite (`_verifier`
#      renvoie deja l'identifiant authentifie, precisement pour cela).
#
# Tant que 1 a 3 ne sont pas tranches -- ce sont des decisions produit, pas
# techniques -- cacher la page une seule fois est CORRECT et economise de la
# memoire sur un plan gratuit. Le point 4 est un quart d'heure de travail le
# jour venu ; les trois premiers sont le vrai chantier.
CACHE_S = int(os.environ.get("RADAR_APP_CACHE_S", "600"))
# Delai minimal entre deux verifications de fraicheur en base. Sans cela, on
# interrogerait Postgres a chaque rafraichissement de page.
VERIF_S = int(os.environ.get("RADAR_APP_VERIF_S", "30"))

_cache = {"html": None, "t": 0.0, "verif": 0.0, "version": None}
_verrou = threading.Lock()
_schema_pret = False


def invalider_cache():
    """Force la regeneration a la prochaine demande (apres un statut pose)."""
    _cache["html"] = None
    _cache["t"] = 0.0
    _cache["verif"] = 0.0
    _cache["version"] = None


def version_donnees(conn):
    """Empreinte de fraicheur des donnees : date de la derniere ecriture et
    nombre de lignes. Une requete tres bon marche.

    POURQUOI : le cache ne reposait que sur un delai de 10 minutes. Quand le
    radar tournait et ecrivait de nouveaux leads, l'application continuait donc
    de servir l'ancienne page jusqu'a expiration, sans aucun moyen de le
    savoir. Constate le 22/07/2026 : un run termine, rien de neuf a l'ecran.
    Desormais la page se renouvelle des que la BASE change."""
    with conn.cursor() as cur:
        cur.execute("SELECT max(maj), count(*) FROM radar_lignes")
        ligne = cur.fetchone() or (None, 0)
    return "{}|{}".format(ligne[0], ligne[1])


def _initialiser_une_fois(conn):
    """Le schema est idempotent mais inutile a rejouer a CHAQUE requete."""
    global _schema_pret
    if not _schema_pret:
        st.initialiser(conn)
        _schema_pret = True


def page_en_cache(frais=False):
    """(html, depuis_le_cache). Trois niveaux, du moins cher au plus cher :
      1. moins de VERIF_S depuis la derniere verification -> on sert direct ;
      2. sinon, une requete de version : si la base n'a pas bouge, on sert
         quand meme (et on repousse l'echeance) ;
      3. sinon, regeneration complete.
    Le verrou evite que dix rafraichissements simultanes declenchent dix
    generations."""
    maintenant = time.time()
    if (not frais and _cache["html"]
            and (maintenant - _cache["verif"]) < VERIF_S):
        return _cache["html"], True
    with _verrou:
        if (not frais and _cache["html"]
                and (time.time() - _cache["verif"]) < VERIF_S):
            return _cache["html"], True
        with st.connexion() as conn:
            _initialiser_une_fois(conn)
            try:
                version = version_donnees(conn)
            except Exception:
                version = None
            frais_requis = (frais or not _cache["html"]
                            or version != _cache["version"]
                            or (time.time() - _cache["t"]) >= CACHE_S)
            if not frais_requis:
                _cache["verif"] = time.time()
                return _cache["html"], True
            html = generer_page(conn)
        _cache["html"] = html
        _cache["t"] = _cache["verif"] = time.time()
        _cache["version"] = version
        return html, False


# ===========================================================================
# LECTURE POSTGRES : l'equivalent exact de dash.lire_onglets, cote base.
# ===========================================================================

def _onglet(conn, nom):
    return st.lire_onglet(conn, nom)


def _normaliser_projet(donnees):
    """Delegue au cockpit : une seule definition du format, quelle que soit la
    source (Sheet cote Cloudflare, Postgres cote Render)."""
    import radar_cockpit
    return radar_cockpit._normaliser_projet(donnees)


def _normaliser_candidat(donnees):
    import radar_cockpit
    return radar_cockpit._normaliser_candidat(donnees)


def lire_onglets_pg(conn):
    """Les memes onglets que dash.lire_onglets (MIGA et IFC inclus), depuis
    radar_lignes.

    Les lignes sont deja des dicts plats (forme canonique) : celles du
    rattrapage portent aussi statut_suivi/date_detection, celles de la double
    ecriture seulement les colonnes de donnees -- les lecteurs du dashboard
    (.get partout) tolerent les deux."""
    lignes_ted = _onglet(conn, "ted_radar")
    lignes_bm = _onglet(conn, "bm_radar")
    lignes_prive = _onglet(conn, "prive_radar")
    lignes_attrib = _onglet(conn, "attributions_radar")
    lignes_rw = _onglet(conn, "reliefweb_radar")
    lignes_afdb = _onglet(conn, "afdb_radar")
    lignes_adb = _onglet(conn, "adb_radar")
    lignes_ebrd = _onglet(conn, "ebrd_radar")
    lignes_ungm = _onglet(conn, "ungm_radar")

    lignes_watchlist = [d for d in _onglet(conn, "comptes_cibles_bitd")
                        if str(d.get("entreprise", "")).strip()]

    # Enrichissement : firmographie + emails Hunter, meme fusion que le
    # dashboard (cle = entreprise en minuscules).
    enrichissement = {}
    for d in _onglet(conn, "entreprises_enrichies"):
        nom = str(d.get("entreprise", "")).strip().lower()
        if nom:
            enrichissement[nom] = d
    for d in _onglet(conn, "contacts_bitd"):
        nom = str(d.get("entreprise", "")).strip().lower()
        email = str(d.get("email_pro", "")).strip()
        if nom and email:
            enrichissement.setdefault(nom, {})["email_pro"] = email

    # Analyse LLM des attributions (attributions_analyse.py), miroir Postgres.
    # Absente = attribution pas encore analysee : le lead garde son score
    # deterministe, la page reste complete.
    analyses_attrib = _onglet(conn, "attributions_analyse")
    lignes_alertes = _onglet(conn, "alertes_radar")

    # Vague 2 : MIGA (garanties risque politique) et IFC (investissements
    # prives). Sources d'AVIS ecrites en base par leurs collecteurs. Absentes de
    # l'application jusqu'au 02/08 : lues par le dashboard statique mais PAS ici,
    # deux collecteurs valides restaient invisibles sur la surface applicative
    # (le motif "orphelin"). Meme ordre de retour que dash.lire_onglets.
    lignes_miga = _onglet(conn, "miga_radar")
    lignes_ifc = _onglet(conn, "ifc_radar")
    # IDB (Amérique latine) : source d'avis, alignée sur dash.lire_onglets.
    lignes_idb = _onglet(conn, "idb_radar")
    # BM Projects (amont) : projets approuves, aligne sur dash.lire_onglets.
    lignes_bmp = _onglet(conn, "bm_projets_radar")
    # Vague 3 : Proparco (DFI FR) et DFC (DFI US), alignes sur dash.lire_onglets.
    lignes_proparco = _onglet(conn, "proparco_radar")
    lignes_dfc = _onglet(conn, "dfc_radar")

    return (lignes_ted, lignes_bm, lignes_prive, lignes_attrib, enrichissement,
            lignes_rw, lignes_afdb, lignes_adb, lignes_ebrd, lignes_watchlist,
            lignes_ungm, analyses_attrib, lignes_alertes, lignes_miga, lignes_ifc,
            lignes_idb, lignes_bmp, lignes_proparco, lignes_dfc)


# Quel champ humain pour quel onglet (avis = statut_suivi ; attributions =
# statut_prospection). Le meme schema que le Sheet.
CHAMP_STATUT = {"attributions_radar": "statut_prospection"}


def superposer_statuts(conn, onglets_nommes):
    """Applique radar_statuts (zone humaine, en base) par-dessus les lignes de
    collecte. La base des STATUTS prime sur la valeur figee au rattrapage. Le
    MOTIF d'ecartement (« Pas pertinent ») est superpose dans `motif_ecart`
    pour que la section « Ecartes » affiche la raison, meme cross-device."""
    statuts = st.lire_statuts(conn)
    if not statuts:
        return
    motifs = st.lire_motifs(conn)
    for nom, lignes in onglets_nommes:
        champ = CHAMP_STATUT.get(nom, "statut_suivi")
        for ligne in lignes:
            cle = (nom, str(ligne.get("publication_number", "") or ""))
            if cle[1] and cle in statuts:
                ligne[champ] = statuts[cle]
                if cle in motifs:
                    ligne["motif_ecart"] = motifs[cle]


def generer_page(conn):
    """Postgres -> HTML, en reutilisant le moteur du dashboard tel quel."""
    (lignes_ted, lignes_bm, lignes_prive, lignes_attrib, enrichissement,
     lignes_rw, lignes_afdb, lignes_adb, lignes_ebrd, lignes_watchlist,
     lignes_ungm, analyses_attrib, lignes_alertes,
     lignes_miga, lignes_ifc, lignes_idb, lignes_bmp,
     lignes_proparco, lignes_dfc) = lire_onglets_pg(conn)
    superposer_statuts(conn, [
        ("ted_radar", lignes_ted), ("bm_radar", lignes_bm),
        ("prive_radar", lignes_prive), ("attributions_radar", lignes_attrib),
        ("reliefweb_radar", lignes_rw), ("afdb_radar", lignes_afdb),
        ("adb_radar", lignes_adb), ("ebrd_radar", lignes_ebrd),
        ("ungm_radar", lignes_ungm), ("miga_radar", lignes_miga),
        ("ifc_radar", lignes_ifc), ("idb_radar", lignes_idb),
        ("bm_projets_radar", lignes_bmp),
        ("proparco_radar", lignes_proparco), ("dfc_radar", lignes_dfc)])
    leads = dash.construire_leads(
        lignes_ted, lignes_bm, lignes_prive, enrichissement, lignes_attrib,
        lignes_rw, lignes_afdb, lignes_adb, lignes_ebrd, lignes_ungm,
        analyses_attrib, lignes_miga=lignes_miga, lignes_ifc=lignes_ifc,
        lignes_idb=lignes_idb, lignes_bmp=lignes_bmp,
        lignes_proparco=lignes_proparco, lignes_dfc=lignes_dfc)
    # Rendu : le COCKPIT (nouvelle interface) est desormais servi par defaut,
    # avec api=True -> le bouton ecrit en base via /api/statut. Repli possible
    # sur l'ancien dashboard sans redeploiement : poser RADAR_LEGACY=1 dans
    # l'environnement Render. Best-effort : si le cockpit echoue, on retombe
    # sur le dashboard plutot que de renvoyer une erreur.
    geo = dash.preparer_geo(lignes_alertes)
    if os.environ.get("RADAR_LEGACY") == "1":
        return dash.generer_html(leads, lignes_watchlist, api_statut=True,
                                 alertes=lignes_alertes)
    try:
        import radar_cockpit
        # Rehausse geopolitique : cablee jusqu'ici UNIQUEMENT dans
        # dash.generer_html, donc absente de l'application alors que l'onglet
        # Geopolitique affichait l'alerte. Appel APRES la branche legacy :
        # appliquer_boost_geo n'est pas idempotente, generer_html l'appelle
        # deja pour son compte, un double appel doublerait le boost.
        leads = radar_cockpit.appliquer_geo(leads, lignes_alertes)
        sante = radar_cockpit.etat_sante(leads)
        suivi = {"url": os.environ.get("SUIVI_WEBAPP_URL", "") or "",
                 "token": os.environ.get("SUIVI_TOKEN", "") or "",
                 "api": True}
        watch = radar_cockpit.charger_watchlist(None, None, lignes_watchlist)
        try:
            import candidats_probables
            idx_cand = candidats_probables.construire_index(leads)
        except Exception:
            idx_cand = {}
        try:
            import dossiers as _dossiers
            doss = _dossiers.serialiser(_dossiers.construire_dossiers(leads))
        except Exception:
            doss = []
        # Project Intelligence. TROISIEME chemin de generation du cockpit, a
        # cote de radar_dashboard (Cloudflare) et de radar_cockpit.main (jamais
        # appele). Sans ces lignes, l'app Render affichait une vue Projets vide
        # alors que le miroir Postgres etait correctement alimente (constate le
        # 24/08/2026). Ici on lit Postgres, pas le Sheet : c'est la source de
        # verite de l'app.
        try:
            projets_suivis = [_normaliser_projet(d)
                              for d in _onglet(conn, "projets_radar")
                              if isinstance(d, dict) and d.get("project_id")]
        except Exception as e:
            print("(app) projets indisponibles ({}).".format(str(e)[:80]))
            projets_suivis = []
        try:
            cand_projets = [_normaliser_candidat(d)
                            for d in _onglet(conn, "projets_candidats")
                            if isinstance(d, dict) and d.get("nom")]
        except Exception as e:
            print("(app) candidats projets indisponibles ({}).".format(str(e)[:80]))
            cand_projets = []
        print("(app) Project Intelligence : {} projet(s), {} candidat(s).".format(
            len(projets_suivis), len(cand_projets)))
        return radar_cockpit.generer_cockpit(leads, geo=geo, suivi=suivi,
                                             watchlist=watch, candidats=idx_cand,
                                             dossiers=doss,
                                             projets=projets_suivis,
                                             candidats_projets=cand_projets,
                                             sante=sante,
                                             geo_alertes=lignes_alertes)
    except Exception as e:
        print("(app) cockpit indisponible ({}), repli dashboard.".format(str(e)[:100]))
        return dash.generer_html(leads, lignes_watchlist, api_statut=True,
                                 alertes=lignes_alertes)


# ===========================================================================
# APPLICATION
# ===========================================================================

# ===========================================================================
# EN-TETES DE CONFIDENTIALITE
# ===========================================================================
# Constat du 23/07/2026 : la page servie ne portait AUCUNE directive de cache
# ni de confidentialite. Les seuls en-tetes etaient content-type,
# content-encoding et vary.
#
# Or cette page contient l'integralite des opportunites commerciales, soit
# ~2 500 leads et 2,6 Mo de renseignement concurrentiel. Sans `Cache-Control`,
# le navigateur la conserve dans son cache disque : sur un portable partage ou
# emprunte, la liste reste consultable APRES la session, sans avoir a se
# reauthentifier. C'est le contraire de ce que protege l'authentification
# Basic placee devant.
#
# Detail des quatre en-tetes :
#   - Cache-Control  : rien ne persiste, ni sur disque ni chez un intermediaire.
#   - Referrer-Policy: les fiches contiennent des liens vers les sources
#     (TED, UNGM, ReliefWeb...). Sans cette directive, chaque clic transmet
#     l'URL de l'application au site tiers.
#   - X-Frame-Options: le bouton "Je contacte" declenche un POST authentifie.
#     Une page tierce qui encadrerait le dashboard pourrait le faire cliquer a
#     l'insu de l'utilisateur (clickjacking).
#   - X-Content-Type-Options : pas d'interpretation devinee du type de contenu.
EN_TETES_PRIVES = {
    "Cache-Control": "private, no-store, max-age=0",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
}


class Statut(BaseModel):
    onglet: str
    publication_number: str
    statut: str
    motif: str = ""
    # Valeur estimee du marche, saisie a la main au passage en « contacte »
    # (P1.1). None = non renseignee, ce qui n'est PAS zero : un montant absent
    # ne doit pas etre compris comme un marche sans valeur.
    valeur_estimee: float | None = None
    # Champs d'AFFICHAGE, utilises UNIQUEMENT pour la replication vers le Sheet
    # (le script Apps Script refuse un envoi sans titre). Ils n'entrent jamais
    # dans l'ecriture en base, qui reste indexee sur (onglet, publication_number).
    contexte: dict = {}


# ===========================================================================
# REPLICATION VERS LE SHEET (P0.2, 26/08/2026)
# ===========================================================================
# AVANT : le NAVIGATEUR postait lui-meme vers l'Apps Script, en mode no-cors.
# Ce mode rend la reponse illisible par construction. Consequence : personne
# n'a jamais vu que le payload ne portait pas de `titre` et que le script
# repondait `missing_fields` a CHAQUE clic. Le bouton n'ecrivait rien dans le
# Sheet depuis le cockpit, et affichait un succes.
#
# MAINTENANT : Postgres est la seule ecriture faite par le navigateur, et elle
# rend des comptes. La replication vers le Sheet part d'ICI, cote serveur :
#   - pas de CORS, donc la reponse EST lisible ;
#   - un refus du script est journalise et compte, plus jamais avale ;
#   - elle tourne dans un fil separe : une replication lente ou en panne ne
#     retarde pas la reponse a l'utilisateur, et ne peut pas faire echouer
#     l'ecriture en base, qui fait autorite.
# Si SUIVI_WEBAPP_URL / SUIVI_TOKEN ne sont pas configures, la fonction ne fait
# rien : c'est l'etat par defaut sur Render et il est parfaitement valable
# (Postgres suffit). RADAR_REPLIQUER=0 la desactive explicitement.

REPLIQUER = os.environ.get("RADAR_REPLIQUER", "1") != "0"

# Journal des dernieres replications, expose par /sante. Un echec doit etre
# CONSULTABLE, sinon on a juste deplace le silence.
REPLICATION = {"tentees": 0, "ok": 0, "echecs": 0, "derniere_erreur": ""}
_verrou_repl = threading.Lock()


def _payload_apps_script(s, token):
    """Construit le corps attendu par le script Apps Script. Fonction PURE.

    `titre` est OBLIGATOIRE cote script (`if (!id || !d.titre)`). On refuse donc
    de partir sans lui plutot que d'envoyer un appel voue a `missing_fields`,
    ce qui etait exactement le defaut precedent."""
    ctx = s.contexte or {}
    ident = str(ctx.get("id") or "").strip() or s.publication_number.strip()
    titre = str(ctx.get("titre") or "").strip()
    if not (ident and titre):
        return None
    return {
        "token": token, "id": ident, "titre": titre,
        "statut": s.statut.strip(), "motif": s.motif.strip(),
        "source": ctx.get("source", ""), "pays": ctx.get("pays", ""),
        "zone": ctx.get("zone", ""), "agence": ctx.get("agence", ""),
        "lien": ctx.get("lien", ""), "date_det": ctx.get("date_det", ""),
        "score": ctx.get("score"), "surete": ctx.get("surete"),
        "comm": ctx.get("comm"), "action": ctx.get("action", ""),
        "fenetre": ctx.get("fenetre", ""), "priorite": ctx.get("action", ""),
        "contact": ctx.get("contact", ""), "email": ctx.get("email", ""),
        "valeur": ctx.get("valeur", ""),
    }


def _noter_replication(ok, erreur=""):
    with _verrou_repl:
        REPLICATION["tentees"] += 1
        if ok:
            REPLICATION["ok"] += 1
        else:
            REPLICATION["echecs"] += 1
            REPLICATION["derniere_erreur"] = erreur[:200]
    if not ok:
        print("(replication Sheet) ECHEC : {}".format(erreur[:200]))


def repliquer_vers_sheet(s):
    """Rejoue le statut vers le Sheet. Best-effort, non bloquant, JOURNALISE.

    N'est jamais appelee dans le chemin critique : l'ecriture en base a deja
    reussi quand on arrive ici. Un echec ne remonte donc pas a l'utilisateur,
    mais il est compte et lisible sur /sante -- la difference exacte avec
    l'ancien `.catch(function(){})`."""
    url = (os.environ.get("SUIVI_WEBAPP_URL") or "").strip()
    token = (os.environ.get("SUIVI_TOKEN") or "").strip()
    if not (REPLIQUER and url and token):
        return False
    payload = _payload_apps_script(s, token)
    if payload is None:
        _noter_replication(False, "contexte incomplet (id ou titre manquant)")
        return False
    try:
        import json as _json
        import urllib.request
        req = urllib.request.Request(
            url, data=_json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as rep:
            corps = rep.read().decode("utf-8", "replace")[:400]
        # Le script repond TOUJOURS 200, y compris pour un refus : c'est le
        # champ `ok` du JSON qui fait foi. Se fier au code HTTP recreerait le
        # silence qu'on vient de supprimer.
        try:
            data = _json.loads(corps)
        except Exception:
            _noter_replication(False, "reponse illisible : " + corps)
            return False
        if data.get("ok") is True:
            _noter_replication(True)
            return True
        _noter_replication(False, "refus du script : " + str(data.get("error")))
        return False
    except Exception as e:
        _noter_replication(False, "{}: {}".format(type(e).__name__, str(e)[:150]))
        return False


_basic = HTTPBasic()


def _verifier(identifiants: HTTPBasicCredentials = Depends(_basic)):
    """Ferme par defaut : sans mot de passe configure, on refuse de servir.

    DEUX DURCISSEMENTS (23/07/2026)
    -------------------------------
    1. COMPARAISON SUR OCTETS. `compare_digest` sur des `str` LEVE une
       TypeError des qu'un caractere n'est pas ASCII. Aujourd'hui starlette
       decode l'en-tete Basic et rejette le non-ASCII avant d'arriver ici, si
       bien que le defaut ne se voit pas -- mais on depend alors d'un detail
       d'implementation d'une dependance. Le jour ou starlette suivrait la
       RFC 7617 (qui autorise UTF-8), un mot de passe accentue deviendrait
       inutilisable et produirait des 500 au lieu de 401. On encode donc en
       UTF-8 avant de comparer : plus aucune hypothese sur la couche du dessus.

    2. PAS DE COURT-CIRCUIT. La version precedente ecrivait
       `compare_digest(user) and compare_digest(mdp)` : quand l'identifiant
       etait faux, la comparaison du mot de passe n'avait pas lieu. L'ecart de
       temps mesurable indiquait donc si un identifiant existe -- exactement ce
       que `compare_digest` sert a eviter, annule par le `and`. Les deux
       comparaisons sont desormais toujours evaluees.

    Renvoie l'identifiant authentifie : les routes en ont besoin, et c'est lui
    qui servira de portee le jour d'un vrai multi-client (voir la note en tete
    de la section CACHE)."""
    attendu_mdp = os.environ.get("RADAR_APP_MOT_DE_PASSE", "")
    if not attendu_mdp:
        raise HTTPException(503, "RADAR_APP_MOT_DE_PASSE non configure : "
                                 "application verrouillee par defaut.")
    attendu_util = os.environ.get("RADAR_APP_UTILISATEUR", "radar")
    util_ok = _secrets.compare_digest(identifiants.username.encode("utf-8"),
                                      attendu_util.encode("utf-8"))
    mdp_ok = _secrets.compare_digest(identifiants.password.encode("utf-8"),
                                     attendu_mdp.encode("utf-8"))
    if not (util_ok and mdp_ok):
        raise HTTPException(401, "Identifiants invalides.",
                            headers={"WWW-Authenticate": "Basic"})
    return identifiants.username


def creer_application():
    app = FastAPI(title="Radar Amarante", docs_url=None, redoc_url=None)
    # 2,6 Mo -> 96 Ko. Le seuil evite de compresser les petites reponses JSON.
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    @app.get("/sante")
    def sante():
        """Diagnostic sans authentification NI donnees : juste l'etat des
        branchements, pour Render et pour le depannage."""
        # `replication` : compteurs de la replication vers le Sheet. C'est ce
        # qui rend un echec CONSULTABLE. Aucune donnee commerciale ici, donc
        # cet endpoint peut rester ouvert : uniquement des compteurs et le
        # libelle de la derniere erreur.
        with _verrou_repl:
            repl = dict(REPLICATION)
        repl["configuree"] = bool(REPLIQUER
                                  and os.environ.get("SUIVI_WEBAPP_URL")
                                  and os.environ.get("SUIVI_TOKEN"))
        return {"miroir": st.actif(),
                "mot_de_passe_configure": bool(os.environ.get("RADAR_APP_MOT_DE_PASSE")),
                "cache_secondes": CACHE_S,
                "page_en_cache": bool(_cache["html"]),
                "replication": repl}

    @app.head("/")
    def reveil():
        """Reveil du service, SANS generer la page ni authentifier.

        Les journaux du 26/08 montraient `HEAD /?frais=1 -> 405` en boucle :
        FastAPI n'expose pas HEAD sur une route declaree en GET, donc le ping
        de reveil echouait a chaque appel. Un pinger qui recoit 405 ne
        maintient rien eveille.

        On repond 200 sans rien calculer : le but d'un HEAD est de reveiller
        l'instance, pas de payer la generation d'une page de plusieurs
        megaoctets a chaque ping -- ce qui reveillerait le service pour le
        saturer aussitot."""
        return Response(status_code=200, headers=EN_TETES_PRIVES)

    @app.get("/")
    def accueil(frais: int = 0, _: None = Depends(_verifier)):
        """La page. `?frais=1` force la regeneration (utile juste apres un run
        du radar, sans attendre l'expiration du cache)."""
        if not st.actif():
            raise HTTPException(503, "DATABASE_URL absent ou pilote manquant.")
        html, _cache_utilise = page_en_cache(frais=bool(frais))
        return Response(content=html, media_type="text/html; charset=utf-8",
                        headers=EN_TETES_PRIVES)

    @app.post("/api/statut")
    def poser_statut(s: Statut, _: None = Depends(_verifier)):
        if not st.actif():
            raise HTTPException(503, "DATABASE_URL absent ou pilote manquant.")
        if not s.publication_number.strip():
            raise HTTPException(422, "publication_number requis.")
        # VOCABULAIRE FERME (P1.1). Un statut hors liste serait ecrit en base
        # et polluerait durablement l'apprentissage : mieux vaut refuser.
        statut = s.statut.strip().lower()
        if not st.statut_valide(statut):
            raise HTTPException(422, "Statut inconnu : {}. Attendus : {}.".format(
                statut, ", ".join(st.STATUTS_VALIDES)))
        # Motif de perte : liste fermee aussi. Un champ libre produit vingt
        # formulations de la meme raison et zero statistique exploitable.
        if statut == "perdu" and not st.motif_perte_valide(s.motif):
            raise HTTPException(422, "Motif de perte inconnu : {}. Attendus : {}.".format(
                s.motif.strip(), ", ".join(st.MOTIFS_PERTE)))
        with st.connexion() as conn:
            _initialiser_une_fois(conn)
            # Une issue suppose que le lead a ete TRAVAILLE. Sans cette garde,
            # le journal se remplirait de « perdu » qui ne sont en fait que des
            # desinteressements -- lesquels ont deja leur statut : non_pertinent.
            if statut in st.STATUTS_ISSUE:
                courant = st.lire_statuts(conn).get(
                    (s.onglet.strip(), s.publication_number.strip()), "")
                if courant not in st.STATUTS_AVANT_ISSUE:
                    raise HTTPException(409, (
                        "Un lead doit avoir été contacté ou surveillé avant "
                        "d'être marqué {}. Statut actuel : « {} »."
                    ).format(statut, courant or "nouveau"))
            st.definir_statut(conn, s.onglet.strip(), s.publication_number.strip(),
                              statut, s.motif.strip(), s.valeur_estimee)
        # L'utilisateur doit VOIR son action au rafraichissement suivant.
        invalider_cache()
        # Replication vers le Sheet dans un fil separe : l'ecriture qui FAIT
        # AUTORITE a deja reussi, la reponse ne l'attend pas. Un echec de
        # replication est journalise et visible sur /sante, jamais avale.
        threading.Thread(target=repliquer_vers_sheet, args=(s,),
                         daemon=True).start()
        return JSONResponse({"ok": True}, headers=EN_TETES_PRIVES)

    return app


app = creer_application()
