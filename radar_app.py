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
# NOTE MULTI-CLIENT (a traiter le jour venu) : ce cache est GLOBAL au processus.
# Des lors que l'application servira plusieurs clients, la cle de cache devra
# inclure l'identite du client, sinon fuite de donnees entre comptes.
CACHE_S = int(os.environ.get("RADAR_APP_CACHE_S", "600"))

_cache = {"html": None, "t": 0.0}
_verrou = threading.Lock()
_schema_pret = False


def invalider_cache():
    """Force la regeneration a la prochaine demande (apres un statut pose)."""
    _cache["html"] = None
    _cache["t"] = 0.0


def _initialiser_une_fois(conn):
    """Le schema est idempotent mais inutile a rejouer a CHAQUE requete."""
    global _schema_pret
    if not _schema_pret:
        st.initialiser(conn)
        _schema_pret = True


def page_en_cache(frais=False):
    """(html, depuis_le_cache). Le verrou evite que dix rafraichissements
    simultanes declenchent dix generations completes."""
    if not frais and _cache["html"] and (time.time() - _cache["t"]) < CACHE_S:
        return _cache["html"], True
    with _verrou:
        # Un autre fil a pu regenerer pendant l'attente du verrou.
        if not frais and _cache["html"] and (time.time() - _cache["t"]) < CACHE_S:
            return _cache["html"], True
        with st.connexion() as conn:
            _initialiser_une_fois(conn)
            html = generer_page(conn)
        _cache["html"], _cache["t"] = html, time.time()
        return html, False


# ===========================================================================
# LECTURE POSTGRES : l'equivalent exact de dash.lire_onglets, cote base.
# ===========================================================================

def _onglet(conn, nom):
    return st.lire_onglet(conn, nom)


def lire_onglets_pg(conn):
    """Le meme 11-uplet que dash.lire_onglets, depuis radar_lignes.

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

    return (lignes_ted, lignes_bm, lignes_prive, lignes_attrib, enrichissement,
            lignes_rw, lignes_afdb, lignes_adb, lignes_ebrd, lignes_watchlist,
            lignes_ungm)


# Quel champ humain pour quel onglet (avis = statut_suivi ; attributions =
# statut_prospection). Le meme schema que le Sheet.
CHAMP_STATUT = {"attributions_radar": "statut_prospection"}


def superposer_statuts(conn, onglets_nommes):
    """Applique radar_statuts (zone humaine, en base) par-dessus les lignes de
    collecte. La base des STATUTS prime sur la valeur figee au rattrapage."""
    statuts = st.lire_statuts(conn)
    if not statuts:
        return
    for nom, lignes in onglets_nommes:
        champ = CHAMP_STATUT.get(nom, "statut_suivi")
        for ligne in lignes:
            cle = (nom, str(ligne.get("publication_number", "") or ""))
            if cle[1] and cle in statuts:
                ligne[champ] = statuts[cle]


def generer_page(conn):
    """Postgres -> HTML, en reutilisant le moteur du dashboard tel quel."""
    (lignes_ted, lignes_bm, lignes_prive, lignes_attrib, enrichissement,
     lignes_rw, lignes_afdb, lignes_adb, lignes_ebrd, lignes_watchlist,
     lignes_ungm) = lire_onglets_pg(conn)
    superposer_statuts(conn, [
        ("ted_radar", lignes_ted), ("bm_radar", lignes_bm),
        ("prive_radar", lignes_prive), ("attributions_radar", lignes_attrib),
        ("reliefweb_radar", lignes_rw), ("afdb_radar", lignes_afdb),
        ("adb_radar", lignes_adb), ("ebrd_radar", lignes_ebrd),
        ("ungm_radar", lignes_ungm)])
    leads = dash.construire_leads(
        lignes_ted, lignes_bm, lignes_prive, enrichissement, lignes_attrib,
        lignes_rw, lignes_afdb, lignes_adb, lignes_ebrd, lignes_ungm)
    # api_statut=True : sur l'application, le bouton ecrit aussi en base.
    return dash.generer_html(leads, lignes_watchlist, api_statut=True)


# ===========================================================================
# APPLICATION
# ===========================================================================

class Statut(BaseModel):
    onglet: str
    publication_number: str
    statut: str


_basic = HTTPBasic()


def _verifier(identifiants: HTTPBasicCredentials = Depends(_basic)):
    """Ferme par defaut : sans mot de passe configure, on refuse de servir.
    Comparaisons a temps constant (compare_digest) contre le timing."""
    attendu_mdp = os.environ.get("RADAR_APP_MOT_DE_PASSE", "")
    if not attendu_mdp:
        raise HTTPException(503, "RADAR_APP_MOT_DE_PASSE non configure : "
                                 "application verrouillee par defaut.")
    attendu_util = os.environ.get("RADAR_APP_UTILISATEUR", "radar")
    ok = (_secrets.compare_digest(identifiants.username, attendu_util) and
          _secrets.compare_digest(identifiants.password, attendu_mdp))
    if not ok:
        raise HTTPException(401, "Identifiants invalides.",
                            headers={"WWW-Authenticate": "Basic"})


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
        return Response(content=html, media_type="text/html; charset=utf-8")

    @app.post("/api/statut")
    def poser_statut(s: Statut, _: None = Depends(_verifier)):
        if not st.actif():
            raise HTTPException(503, "DATABASE_URL absent ou pilote manquant.")
        if not s.publication_number.strip():
            raise HTTPException(422, "publication_number requis.")
        with st.connexion() as conn:
            _initialiser_une_fois(conn)
            st.definir_statut(conn, s.onglet.strip(), s.publication_number.strip(),
                              s.statut.strip())
        # L'utilisateur doit VOIR son action au rafraichissement suivant.
        invalider_cache()
        return {"ok": True}

    return app


app = creer_application()
