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
    # api_statut=True : sur l'application, le bouton ecrit aussi en base.
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
        return {"miroir": st.actif(),
                "mot_de_passe_configure": bool(os.environ.get("RADAR_APP_MOT_DE_PASSE")),
                "cache_secondes": CACHE_S,
                "page_en_cache": bool(_cache["html"])}

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
        with st.connexion() as conn:
            _initialiser_une_fois(conn)
            st.definir_statut(conn, s.onglet.strip(), s.publication_number.strip(),
                              s.statut.strip(), s.motif.strip())
        # L'utilisateur doit VOIR son action au rafraichissement suivant.
        invalider_cache()
        return JSONResponse({"ok": True}, headers=EN_TETES_PRIVES)

    return app


app = creer_application()
