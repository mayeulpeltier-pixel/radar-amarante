# -*- coding: utf-8 -*-
"""
Radar Amarante -- Generateur du COCKPIT (nouvelle interface).
===============================================================================

ROLE : produire `public/cockpit.html`, la nouvelle interface multi-vues, EN
PARALLELE du dashboard historique (`radar_dashboard.py` -> index.html). Ce
generateur ne fait que LIRE le moteur existant et ECRIRE un HTML : il n'altere
jamais radar_dashboard, si bien que l'ancien tableau reste disponible en
permanence (rollback total, migration progressive lot par lot).

REUTILISATION DU MOTEUR (aucune duplication de logique metier) :
  - dash.charger_leads(sheet_id, fichier)  -> lit les onglets + fusionne
  - dash._valeur_en_millions(txt)          -> conversion montant -> M€ (EUR)
  - dash.RISQUE_ZONE                        -> posture par theatre

Le cockpit consomme le MEME schema de lead que le dashboard (cle `final` pour
le score, `action` pour la priorite, `sect` pour le secteur, etc.). La seule
donnee ajoutee ici est `valeur_meur` (float), pre-calculee cote Python pour que
le front n'ait pas a re-parser les montants.

USAGE (identique au dashboard, variables d'env deja en place) :
    TED_SHEET_ID=... GOOGLE_SERVICE_ACCOUNT_FILE=... \
        COCKPIT_OUTPUT=public/cockpit.html python radar_cockpit.py
"""

import json
import os
import sys

import radar_dashboard as dash


# Coordonnees (nom FR -> [lat, lng]) reprises telles quelles du dashboard :
# le front place les marqueurs via COORDS[lead.pays]. Donnee stable.
COORDS = {
    "Mali": [17.6, -3.5], "Niger": [17.6, 9.4], "Burkina Faso": [12.2, -1.6],
    "Tchad": [15.5, 18.7], "Mauritanie": [20.3, -10.9], "Côte d'Ivoire": [7.5, -5.5],
    "Nigeria": [9.1, 8.7], "Sénégal": [14.5, -14.5], "Ghana": [7.9, -1.0],
    "Togo": [8.6, 0.8], "Bénin": [9.3, 2.3], "Guinée": [9.9, -9.7], "Libéria": [6.4, -9.4],
    "RDC": [-4.0, 21.8], "Congo-Brazzaville": [-0.7, 15.8], "Cameroun": [5.6, 12.4],
    "Centrafrique": [6.6, 20.9], "Gabon": [-0.8, 11.6], "Éthiopie": [9.1, 40.5],
    "Kenya": [0.0, 37.9], "Ouganda": [1.4, 32.3], "Tanzanie": [-6.4, 34.9],
    "Somalie": [5.2, 46.2], "Soudan du Sud": [7.3, 30.3], "Rwanda": [-1.9, 29.9],
    "Djibouti": [11.8, 42.6], "Mozambique": [-18.7, 35.5], "Madagascar": [-18.8, 46.9],
    "Afrique du Sud": [-30.6, 22.9], "Zambie": [-13.1, 27.8], "Zimbabwe": [-19.0, 29.2],
    "Malawi": [-13.3, 34.3], "Angola": [-11.2, 17.9], "Botswana": [-22.3, 24.7],
    "Égypte": [26.8, 30.8], "Maroc": [31.8, -7.1], "Tunisie": [33.9, 9.6],
    "Algérie": [28.0, 1.7], "Libye": [26.3, 17.2], "Cisjordanie et Gaza": [31.9, 35.2],
    "Jordanie": [30.6, 36.2], "Liban": [33.9, 35.9], "Irak": [33.2, 43.7],
    "Yémen": [15.6, 48.0], "Turquie": [39.0, 35.2], "Oman": [21.5, 55.9],
    "Ouzbékistan": [41.4, 64.6], "Tadjikistan": [38.9, 71.3], "Kirghizistan": [41.2, 74.8],
    "Kazakhstan": [48.0, 66.9], "Bangladesh": [23.7, 90.4], "Pakistan": [30.4, 69.3],
    "Inde": [22.4, 78.9], "Népal": [28.4, 84.1], "Indonésie": [-2.5, 118.0],
    "Philippines": [12.9, 121.8], "Ukraine": [48.4, 31.2], "Moldavie": [47.2, 28.5],
    "Albanie": [41.2, 20.0], "Macédoine du Nord": [41.6, 21.7], "Serbie": [44.0, 21.0],
    "Géorgie": [42.3, 43.4], "Arménie": [40.1, 45.0], "Azerbaïdjan": [40.1, 47.6],
    "Haïti": [19.0, -72.3], "Jamaïque": [18.1, -77.3], "Mexique": [23.6, -102.6],
    "Honduras": [14.8, -86.2], "Guatemala": [15.5, -90.2], "El Salvador": [13.8, -88.9],
    "Nicaragua": [12.9, -85.2], "Panama": [8.5, -80.1], "Mongolie": [46.9, 103.8],
    "Équateur": [-1.8, -78.2], "Brésil": [-10.3, -53.2], "Colombie": [4.6, -74.3],
    "France": [46.6, 2.2], "Allemagne": [51.2, 10.4], "Danemark": [56.0, 9.5],
    "Nouvelle-Calédonie": [-21.3, 165.5], "Afghanistan": [33.9, 67.7],
    "Arabie Saoudite": [24.0, 45.1], "Argentine": [-38.4, -63.6], "Bahreïn": [26.1, 50.6],
    "Biélorussie": [53.7, 27.9], "Bolivie": [-16.3, -63.6], "Bosnie-Herzégovine": [43.9, 17.7],
    "Burundi": [-3.4, 29.9], "Cambodge": [12.6, 104.9], "Cap-Vert": [16.0, -24.0],
    "Chili": [-35.7, -71.5], "Comores": [-11.6, 43.3], "Eswatini": [-26.5, 31.5],
    "Fidji": [-17.7, 178.0], "Gambie": [13.4, -15.3], "Guinée équatoriale": [1.6, 10.3],
    "Guinée-Bissau": [12.0, -15.0], "Guyana": [4.9, -58.9], "Guyane": [4.0, -53.0],
    "Iran": [32.4, 53.7], "Israël": [31.4, 35.0], "Kosovo": [42.6, 20.9],
    "Koweït": [29.3, 47.6], "Laos": [19.9, 102.5], "Lesotho": [-29.6, 28.2],
    "Liberia": [6.4, -9.4], "Maurice": [-20.3, 57.6], "Mayotte": [-12.8, 45.2],
    "Monténégro": [42.7, 19.4], "Myanmar": [21.9, 95.9], "Namibie": [-22.6, 17.1],
    "Papouasie-Nouvelle-Guinée": [-6.3, 143.9], "Paraguay": [-23.4, -58.4],
    "Pérou": [-9.2, -75.0], "Qatar": [25.3, 51.2], "Russie": [61.5, 90.0],
    "Sao Tomé-et-Principe": [0.2, 6.6], "Seychelles": [-4.7, 55.5],
    "Sierra Leone": [8.5, -11.8], "Soudan": [15.5, 30.2], "Sri Lanka": [7.9, 80.8],
    "Suriname": [4.0, -56.0], "Syrie": [35.0, 38.5], "Trinité-et-Tobago": [10.5, -61.3],
    "Turkménistan": [38.9, 59.6], "Uruguay": [-32.5, -55.8], "Vanuatu": [-16.3, 167.7],
    "Venezuela": [6.4, -66.6], "Émirats Arabes Unis": [24.0, 54.0],
    "Érythrée": [15.2, 39.8], "Îles Salomon": [-9.6, 160.2],
}


def enrichir(leads):
    """Ajoute `valeur_meur` (montant marche) et `enveloppe_meur` (enveloppe
    projet BM, champ distinct) a chaque lead, via le convertisseur du dashboard.
    Les avis sans montant recoivent 0.0. Ne modifie pas l'entree."""
    out = []
    for l in leads:
        d = dict(l)
        brut = l.get("valeur", "")
        try:
            d["valeur_meur"] = round(dash._valeur_en_millions(brut), 2) if brut else 0.0
        except Exception:
            d["valeur_meur"] = 0.0
        env = l.get("enveloppe", "")
        try:
            d["enveloppe_meur"] = round(dash._valeur_en_millions(env), 2) if env else 0.0
        except Exception:
            d["enveloppe_meur"] = 0.0
        out.append(d)
    return out


def charger_watchlist(sheet_id, fichier, lignes_bitd=None):
    """Liste unifiee des entreprises SUIVIES pour la vue Watchlist :
    comptes_cibles_bitd (defense, deja en memoire) + watchlist_prives
    (multi-secteurs, lecture legere). Best-effort : si watchlist_prives est
    illisible (quota, onglet absent), on se rabat sur le BITD seul. Dedup par
    nom (insensible a la casse). Retour : [{entreprise, secteur, wl}]."""
    ents = {}
    for d in (lignes_bitd or []):
        nom = (d.get("entreprise") or d.get("nom") or "").strip()
        if nom:
            ents.setdefault(nom.lower(), {
                "entreprise": nom,
                "secteur": (d.get("secteur") or "Défense / BITD").strip(),
                "wl": "bitd"})
    # Lecture de watchlist_prives seulement si un classeur est fourni (chemin
    # dashboard). Sur Render (lecture Postgres), sheet_id/fichier sont absents :
    # on se limite au BITD deja passe, sans tentative Sheet.
    if sheet_id and fichier:
        try:
            import signaux_prives as sp
            classeur = sp._ouvrir_classeur(sheet_id, fichier)
            vals = classeur.worksheet("watchlist_prives").get_all_values()
            for c in sp.lire_watchlist_multisecteurs(vals):
                nom = (c.get("entreprise") or "").strip()
                if nom:
                    ents.setdefault(nom.lower(), {
                        "entreprise": nom,
                        "secteur": (c.get("secteur") or "Autre").strip(),
                        "wl": "prives"})
        except Exception as e:
            print("(cockpit) watchlist_prives non lue ({}) : BITD + signaux seuls.".format(
                str(e)[:70]))
    return list(ents.values())


def _normaliser_projet(d):
    """Ligne brute (Sheet ou miroir) -> projet exploitable par le front.
    Les numeriques arrivent en TEXTE depuis gspread : repli 0 systematique,
    sinon une cellule inattendue casse tout le rendu (piege connu du projet)."""
    d = dict(d)
    for champ in ("maturite", "opportunite", "nb_signaux", "valeur_musd"):
        try:
            d[champ] = float(str(d.get(champ, "")).replace(",", ".") or 0)
        except ValueError:
            d[champ] = 0
    if not isinstance(d.get("timeline"), list):
        try:
            d["timeline"] = json.loads(d.get("timeline_json") or "[]")
        except (TypeError, ValueError):
            d["timeline"] = []
    d.pop("timeline_json", None)
    return d


def _normaliser_candidat(d):
    """Ligne brute (Sheet ou miroir) -> candidat exploitable par le front."""
    d = dict(d)
    for champ in ("confiance", "nb_signaux", "nb_sources", "montant_musd"):
        try:
            d[champ] = float(str(d.get(champ, "")).replace(",", ".") or 0)
        except ValueError:
            d[champ] = 0
    if not isinstance(d.get("signaux"), list):
        try:
            d["signaux"] = json.loads(d.get("signaux_json") or "[]")
        except (TypeError, ValueError):
            d["signaux"] = []
    d.pop("signaux_json", None)
    return d


def _lire_miroir_pg(onglet):
    """Lignes du miroir Postgres d'un onglet, ou []. BEST-EFFORT.

    Filet de securite constate utile au premier run de production : l'ecriture
    Sheet avait echoue (portee OAuth) alors que le miroir Postgres, lui, etait
    bien alimente. Sans ce repli, la vue restait vide malgre des donnees
    disponibles."""
    try:
        import radar_stockage
        lire = getattr(radar_stockage, "lire_miroir", None)
        if not callable(lire):
            return []
        return lire(onglet) or []
    except Exception as e:
        print("(cockpit) miroir pg '{}' indisponible ({}).".format(onglet, str(e)[:60]))
        return []


def charger_candidats_projets(sheet_id, fichier):
    """Candidats de decouverte depuis l'onglet `projets_candidats` (ecrit par
    decouverte_projets). BEST-EFFORT comme charger_projets.

    Un candidat n'est PAS un projet suivi : c'est une piste que la decouverte
    a reperee et que l'analyste doit trancher. Les afficher est le maillon
    manquant -- sans cela, le systeme decouvre dans le vide."""
    if not (sheet_id and fichier):
        return []
    try:
        import signaux_prives as sp
        classeur = sp._ouvrir_classeur(sheet_id, fichier)
        valeurs = classeur.worksheet("projets_candidats").get_all_values()
    except Exception as e:
        print("(cockpit) onglet projets_candidats non lu ({}) : repli miroir.".format(
            str(e)[:60]))
        valeurs = None
    if valeurs is None:
        return [_normaliser_candidat(d) for d in _lire_miroir_pg("projets_candidats")
                if d.get("nom")]
    if len(valeurs) < 2:
        return []
    entetes = [str(c).strip() for c in valeurs[0]]
    out = []
    for ligne in valeurs[1:]:
        d = {entetes[i]: (ligne[i] if i < len(ligne) else "")
             for i in range(len(entetes))}
        if not d.get("nom"):
            continue
        out.append(_normaliser_candidat(d))
    return out


def charger_projets(sheet_id, fichier):
    """Projets suivis depuis l'onglet `projets_radar` (ecrit par
    collecteur_projets). BEST-EFFORT : onglet absent, quota, structure
    inattendue -> liste vide et cockpit normal. Le Project Intelligence est un
    ajout : son indisponibilite ne doit jamais coter une page."""
    if not (sheet_id and fichier):
        return []
    try:
        import signaux_prives as sp
        classeur = sp._ouvrir_classeur(sheet_id, fichier)
        valeurs = classeur.worksheet("projets_radar").get_all_values()
    except Exception as e:
        print("(cockpit) onglet projets_radar non lu ({}) : repli miroir.".format(
            str(e)[:70]))
        valeurs = None
    if valeurs is None:
        # Le miroir Postgres rend deja des dictionnaires : on court-circuite
        # la conversion depuis les lignes du Sheet.
        return [_normaliser_projet(d) for d in _lire_miroir_pg("projets_radar")
                if d.get("project_id")]
    if len(valeurs) < 2:
        return []
    entetes = [str(c).strip() for c in valeurs[0]]
    out = []
    for ligne in valeurs[1:]:
        d = {entetes[i]: (ligne[i] if i < len(ligne) else "")
             for i in range(len(entetes))}
        if not d.get("project_id"):
            continue
        out.append(_normaliser_projet(d))
    return out


# ===========================================================================
# RAPATRIEMENT DES ORPHELINS DE SURFACE (25/08/2026)
# ===========================================================================
# Deux mecanismes valides, testes et en production etaient cables UNIQUEMENT
# dans `radar_dashboard.generer_html`, c'est-a-dire sur l'ancien tableau
# Cloudflare. Le cockpit -- la surface reellement servie par l'application --
# ne les voyait pas :
#
#   1. `sante_run` : etat du dernier run par source. Sans lui, une source qui
#      se tait ne se voit nulle part sur l'app. C'est le motif ADB, et le
#      dispositif construit pour l'eviter etait lui-meme invisible.
#   2. `appliquer_boost_geo` : un pays en aggravation recente rehausse ses
#      avis. Sur l'app, les scores ne bougeaient pas, alors que l'onglet
#      Geopolitique affichait l'alerte juste a cote. Deux onglets, deux
#      lectures du meme pays.
#
# Les deux sont ici de simples DELEGATIONS au dashboard : aucune logique n'est
# dupliquee, on rend juste le resultat au cockpit.

SANTE_ON = os.environ.get("RADAR_COCKPIT_SANTE", "1") != "0"
GEO_BOOST_ON = os.environ.get("RADAR_COCKPIT_GEO", "1") != "0"


def etat_sante(leads):
    """Etat du dernier run par source, pour le bandeau du cockpit. Delegue a
    `dash.sante_run` (source de verite unique des seuils et des etats).

    Best-effort : un moteur sans `sante_run` (ancienne version en cache) ou une
    erreur de calcul renvoie {}, ce qui MASQUE le bandeau au lieu de casser la
    page. RADAR_COCKPIT_SANTE=0 le desactive sans redeploiement."""
    if not SANTE_ON:
        return {}
    try:
        return dash.sante_run(leads or [])
    except Exception as e:
        print("(cockpit) etat de sante indisponible ({}) : bandeau masque.".format(
            str(e)[:70]))
        return {}


def appliquer_geo(leads, alertes):
    """Rehausse les avis des pays en aggravation recente, AVANT serialisation.

    ATTENTION, POINT DE VIGILANCE : `dash.appliquer_boost_geo` n'est PAS
    idempotente (elle preserve `final_base` mais ajoute le boost a `final` a
    chaque appel). Elle ne doit donc etre appelee QU'UNE fois par rendu, et
    JAMAIS avant `dash.generer_html`, qui l'appelle deja pour son compte. D'ou
    l'appel ici, dans le chemin cockpit exclusivement.

    Best-effort et sans effet si aucune alerte : renvoie `leads` inchange.
    RADAR_COCKPIT_GEO=0 restitue le comportement d'avant."""
    if not GEO_BOOST_ON or not alertes:
        return leads
    try:
        return dash.appliquer_boost_geo(leads, alertes)
    except Exception as e:
        print("(cockpit) boost geo non applique ({}) : scores bruts.".format(
            str(e)[:70]))
        return leads


def postures(leads, alertes, risque=None):
    """Zone -> posture EFFECTIVE, contexte du moment inclus. Fonction PURE.

    LE DEFAUT CORRIGE
    -----------------
    Les tuiles de theatre lisaient `RISQUE_ZONE`, une table CONSTANTE : la
    posture d'un theatre ne bougeait jamais. L'application pouvait donc
    afficher « Posture jaune » sur une zone dont l'onglet Geopolitique
    signalait, deux clics plus loin, une aggravation severe. Depuis le
    rapatriement du boost geo, les SCORES bougeaient mais pas la posture :
    l'incoherence etait devenue interne a la meme page.

    Regles :
      - socle = `RISQUE_ZONE[zone]` (0-5), la doctrine de fond, inchangee ;
      - rehausse = plus forte aggravation RECENTE d'un pays de la zone, via
        `dash._boost_par_pays` (meme source, memes seuils, meme decroissance
        dans le temps que la rehausse des scores : une seule verite) ;
      - pas d'empilement : la plus forte aggravation gagne, on n'additionne
        pas deux pays ;
      - un allegement ne DESCEND jamais une posture. Baisser la garde sur un
        signal d'amelioration serait le mauvais sens de l'erreur ;
      - le niveau reste borne a 5, l'echelle de la table de fond.

    Le rattachement pays -> zone vient des LEADS eux-memes (chaque lead porte
    son pays et sa zone), donc aucun referentiel supplementaire a maintenir :
    une alerte sur un pays ou le radar n'a jamais rien vu n'a pas de theatre a
    rehausser, et c'est le comportement voulu.

    Renvoie {zone: {base, boost, niveau, pays, motif, date}}. Sans alerte, ou
    avec RADAR_COCKPIT_GEO=0, boost vaut 0 partout : les tuiles retrouvent
    exactement leur affichage d'avant."""
    risque = risque if risque is not None else getattr(dash, "RISQUE_ZONE", {})
    zones = {}
    zone_de = {}
    for l in (leads or []):
        z = (l.get("zone") or "").strip() or "Non classé"
        zones[z] = float(risque.get(z, 1.5))
        p = (l.get("pays") or "").strip()
        if p:
            zone_de[p] = z
    for z in risque:
        zones.setdefault(z, float(risque.get(z, 1.5)))

    par_pays = {}
    if GEO_BOOST_ON:
        try:
            par_pays = dash._boost_par_pays(alertes or [])
        except Exception as e:
            print("(cockpit) postures : rehausse non calculee ({}).".format(
                str(e)[:60]))
            par_pays = {}

    out = {}
    for z, base in zones.items():
        out[z] = {"base": round(base, 2), "boost": 0.0,
                  "niveau": round(base, 2), "pays": "", "motif": "", "date": ""}
    for pays, (boost, motif, maj) in par_pays.items():
        z = zone_de.get(pays)
        if not z or z not in out or boost <= out[z]["boost"]:
            continue
        out[z].update(boost=round(float(boost), 2), pays=pays,
                      motif=motif or "", date=maj or "")
        out[z]["niveau"] = round(min(5.0, out[z]["base"] + float(boost)), 2)
    return out


def generer_cockpit(leads, geo=None, suivi=None, risque=None, watchlist=None,
                    candidats=None, dossiers=None, projets=None,
                    candidats_projets=None, sante=None, posture=None,
                    geo_alertes=None):
    """leads (schema dashboard) -> HTML autonome. Fonction PURE.
    dossiers : liste compacte (dossiers.serialiser) pour la vue Ecosysteme
               (projets BM suivis a travers leurs phases). Defaut [].
    projets  : projets suivis (Project Intelligence) pour la vue PROJETS /
               TOP 20. Defaut [] : sans projets, le cockpit s'affiche
               exactement comme avant (additif).
    candidats_projets : pistes issues de la DECOUVERTE, non encore promues.
               Affichees a part, car elles demandent un arbitrage humain.
    geo_alertes : lignes d'alertes BRUTES (schema collecteur), servant a
               calculer la posture des theatres. Distinct de `geo`, qui est le
               payload deja mis en forme pour l'onglet Geopolitique.
    posture  : zone -> posture effective (voir `postures`). Defaut : calcule
               ici. Passer {} fige les tuiles sur la table de risque constante.
    sante    : etat du dernier run par source (dash.sante_run). Defaut : calcule
               ici depuis les leads. C'est le detecteur de source muette : il
               existait depuis le 02/08 mais n'etait cable QUE sur le dashboard
               legacy, donc invisible sur la surface reellement utilisee. Passer
               {} le desactive (bandeau masque, page inchangee)."""
    risque = risque if risque is not None else getattr(dash, "RISQUE_ZONE", {})
    suivi = suivi or {}
    if sante is None:
        sante = etat_sante(leads)
    if posture is None:
        posture = postures(leads, geo_alertes, risque)
    payload = enrichir(leads)
    # SECRETS : une page STATIQUE ne porte jamais le jeton de suivi. Elle est
    # non authentifiee, donc en lecture seule. Voir dash.assainir_suivi.
    _url, _token = dash.assainir_suivi(suivi.get("url", ""),
                                       suivi.get("token", ""),
                                       bool(suivi.get("api")))
    return (GABARIT
            .replace("__POSTURE_JSON__", json.dumps(posture or {}, ensure_ascii=False))
            .replace("__SANTE_JSON__", json.dumps(sante or {}, ensure_ascii=False))
            .replace("__LEADS_JSON__", json.dumps(payload, ensure_ascii=False))
            .replace("__PROJETS_JSON__", json.dumps(projets or [], ensure_ascii=False))
            .replace("__CANDPROJ_JSON__", json.dumps(candidats_projets or [], ensure_ascii=False))
            .replace("__COORDS_JSON__", json.dumps(COORDS, ensure_ascii=False))
            .replace("__RISQUE_JSON__", json.dumps(risque, ensure_ascii=False))
            .replace("__GEO_JSON__", json.dumps(geo or [], ensure_ascii=False))
            .replace("__WATCHLIST_JSON__", json.dumps(
                [dict(w, ent_cle=dash._norm_ent(w.get("entreprise", "")))
                 for w in (watchlist or [])], ensure_ascii=False))
            .replace("__CANDIDATS_JSON__", json.dumps(candidats or {}, ensure_ascii=False))
            .replace("__DOSSIERS_JSON__", json.dumps(dossiers or [], ensure_ascii=False))
            .replace("__SUIVI_URL__", json.dumps(_url))
            .replace("__SUIVI_TOKEN__", json.dumps(_token))
            .replace("__API_STATUT__", "true" if suivi.get("api") else "false"))


def main():
    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    sortie = os.environ.get("COCKPIT_OUTPUT", "public/cockpit.html")
    if not sheet_id or not fichier:
        print("ERREUR : TED_SHEET_ID et GOOGLE_SERVICE_ACCOUNT_FILE sont requis.")
        sys.exit(1)
    print("Cockpit : lecture du moteur de donnees (charger_leads)...")
    leads, onglets = dash.charger_leads(sheet_id, fichier)
    # Flux geopolitique : les alertes sont a la 13e position du tuple onglets
    # (meme ordre que radar_dashboard : ... analyses_attrib, lignes_alertes ...).
    # preparer_geo ne garde que la semaine en cours. Best-effort : une structure
    # inattendue n'empeche pas la generation.
    geo, lignes_alertes = [], []
    try:
        lignes_alertes = onglets[12]
        geo = dash.preparer_geo(lignes_alertes)
    except Exception as e:
        print("(cockpit) flux geo indisponible ({}) -- vue Geo vide.".format(
            str(e)[:80]))
    # Bouton de statut : api=False car cette page est servie en STATIQUE
    # (Cloudflare). Consequence assumee depuis le 26/08 : `assainir_suivi`
    # vide url et jeton, la page publiee est donc en LECTURE SEULE et les
    # boutons d'action n'apparaissent pas. Ils restent sur Render (api=True),
    # derriere authentification. On garde la lecture des variables ici pour
    # que le garde-fou ait quelque chose a comparer si elles reapparaissent.
    suivi = {
        "url": os.environ.get("SUIVI_WEBAPP_URL", "") or "",
        "token": os.environ.get("SUIVI_TOKEN", "") or "",
        "api": False,
    }
    projets = charger_projets(sheet_id, fichier)
    cand_proj = charger_candidats_projets(sheet_id, fichier)
    # Meme traitement que l'application : le cockpit statique et le cockpit
    # servi par Render doivent afficher le MEME score pour le meme avis.
    leads = appliquer_geo(leads, lignes_alertes)
    html = generer_cockpit(leads, geo=geo, suivi=suivi, projets=projets,
                           candidats_projets=cand_proj,
                           sante=etat_sante(leads),
                           geo_alertes=lignes_alertes)
    # GARDE-FOU : ce fichier part sur Cloudflare Pages. On refuse de l'ecrire
    # s'il contient un secret. Faire echouer le run est preferable a publier.
    dash.verifier_absence_secret(html, ou="le cockpit ({})".format(sortie))
    dossier = os.path.dirname(sortie)
    if dossier:
        os.makedirs(dossier, exist_ok=True)
    with open(sortie, "w", encoding="utf-8") as f:
        f.write(html)
    print("Cockpit ecrit : {} ({} leads, {} octets)".format(
        sortie, len(leads), len(html)))


# ===========================================================================
# GABARIT HTML (cockpit). Placeholders : __LEADS_JSON__ __COORDS_JSON__
# __RISQUE_JSON__. Le front normalise le schema dashboard a l'ingestion.
# ===========================================================================
GABARIT = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Radar Amarante · Cockpit</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{--bg:#F4F6F8;--surface:#FFFFFF;--surface-2:#FAFBFC;--ink:#15181F;--ink-2:#586173;--ink-3:#8B93A2;--line:#E6E9EE;--line-2:#EEF1F5;--amarante:#8E2649;--amarante-2:#A83258;--amarante-soft:#F5E7ec;--red:#C0392B;--red-soft:#FBEAE8;--amber:#B07419;--amber-soft:#FBF1E2;--green:#237A57;--green-soft:#E4F2EB;--blue:#33628F;--blue-soft:#E7EEF6;--display:'Space Grotesk',sans-serif;--body:'Inter',sans-serif;--mono:'IBM Plex Mono',monospace;--sh:0 1px 2px rgba(20,24,31,.04),0 2px 8px rgba(20,24,31,.04);--sh-2:0 4px 20px rgba(20,24,31,.10)}
*{box-sizing:border-box;margin:0;padding:0}html,body{height:100%}
body{background:var(--bg);color:var(--ink);font-family:var(--body);font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased}
button{font-family:inherit;cursor:pointer;border:none;background:none;color:inherit}::selection{background:var(--amarante-soft)}
.app{display:grid;grid-template-columns:236px 1fr;min-height:100vh}
.side{background:var(--surface);border-right:1px solid var(--line);display:flex;flex-direction:column;position:sticky;top:0;height:100vh}
.main{min-width:0;display:flex;flex-direction:column}
.brand{padding:22px 22px 18px;display:flex;align-items:center;gap:12px;border-bottom:1px solid var(--line-2)}
.mark{width:34px;height:34px;border-radius:8px;background:linear-gradient(150deg,var(--amarante),var(--amarante-2));position:relative;flex:none;box-shadow:0 2px 8px rgba(142,38,73,.35)}
.mark::after{content:"";position:absolute;inset:9px;border:2px solid rgba(255,255,255,.9);border-radius:50%}
.mark::before{content:"";position:absolute;left:16px;top:4px;width:2px;height:26px;background:rgba(255,255,255,.9)}
.brand h1{font-family:var(--display);font-size:16px;font-weight:600;letter-spacing:-.01em}
.brand .tag{font-size:11px;color:var(--ink-3);font-family:var(--mono);letter-spacing:.02em}
.nav{padding:12px 12px;display:flex;flex-direction:column;gap:2px;flex:1}
.nav-lbl{font-size:10px;font-weight:600;letter-spacing:.09em;text-transform:uppercase;color:var(--ink-3);padding:14px 12px 6px}
.nav a{display:flex;align-items:center;gap:11px;padding:9px 12px;border-radius:8px;font-size:13.5px;font-weight:500;color:var(--ink-2);transition:.12s;cursor:pointer}
.nav a svg{width:17px;height:17px;flex:none;stroke-width:1.9}
.nav a:hover{background:var(--surface-2);color:var(--ink)}
.nav a.on{background:var(--amarante-soft);color:var(--amarante);font-weight:600}
.nav a .cnt{margin-left:auto;font-family:var(--mono);font-size:11px;background:var(--line-2);color:var(--ink-2);padding:1px 7px;border-radius:20px}
.nav a.on .cnt{background:var(--amarante);color:#fff}
.side-foot{padding:14px 18px;border-top:1px solid var(--line-2);font-size:11px;color:var(--ink-3);font-family:var(--mono);display:flex;align-items:center;gap:7px}
.dot-live{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 0 3px var(--green-soft)}
.top{height:60px;border-bottom:1px solid var(--line);background:var(--surface);display:flex;align-items:center;gap:18px;padding:0 26px;position:sticky;top:0;z-index:20}
.top h2{font-family:var(--display);font-size:18px;font-weight:600;letter-spacing:-.01em}
.top .crumb{font-size:12px;color:var(--ink-3);font-family:var(--mono)}.top .spacer{flex:1}
.search{display:flex;align-items:center;gap:9px;background:var(--surface-2);border:1px solid var(--line);border-radius:9px;padding:8px 13px;width:300px;transition:.15s}
.search:focus-within{border-color:var(--amarante);box-shadow:0 0 0 3px var(--amarante-soft)}
.search svg{width:15px;height:15px;color:var(--ink-3);flex:none}
.search input{border:none;outline:none;background:none;font-family:var(--body);font-size:13px;width:100%;color:var(--ink)}
.btn{display:inline-flex;align-items:center;gap:7px;padding:8px 14px;border-radius:8px;font-size:13px;font-weight:600;border:1px solid var(--line);background:var(--surface);color:var(--ink-2);transition:.12s}
.btn:hover{border-color:var(--ink-3);color:var(--ink)}.btn.pri{background:var(--amarante);border-color:var(--amarante);color:#fff}.btn.pri:hover{background:var(--amarante-2)}
.btn.on-watch{background:var(--blue-soft);border-color:var(--blue);color:var(--blue)}
.view{padding:26px;display:none}.view.on{display:block;animation:fade .3s ease}
@keyframes fade{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.theatres{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-bottom:22px}
.th{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:15px 16px;position:relative;overflow:hidden;box-shadow:var(--sh);transition:.15s;cursor:pointer}
.th:hover{box-shadow:var(--sh-2);transform:translateY(-2px)}
.th .bar{position:absolute;left:0;top:0;bottom:0;width:4px}
.th .zone{font-family:var(--display);font-weight:600;font-size:13.5px;margin-bottom:2px}
.th .post{font-size:10px;font-family:var(--mono);letter-spacing:.03em;text-transform:uppercase;font-weight:600}
.th .big{font-family:var(--display);font-size:28px;font-weight:600;letter-spacing:-.02em;margin-top:12px;line-height:1}
.th .big small{font-size:11px;color:var(--ink-3);font-weight:500;font-family:var(--body)}
.th .val{font-family:var(--mono);font-size:11.5px;color:var(--ink-2);margin-top:6px}
.post.p-rouge{color:var(--red)}.bar.p-rouge{background:var(--red)}.post.p-orange{color:var(--amber)}.bar.p-orange{background:var(--amber)}.post.p-jaune{color:var(--blue)}.bar.p-jaune{background:var(--blue)}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:22px}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:17px 18px;box-shadow:var(--sh)}
.kpi .k-top{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
.kpi .k-lbl{font-size:11.5px;font-weight:600;color:var(--ink-2)}
.kpi .k-ico{width:32px;height:32px;border-radius:8px;display:grid;place-items:center}.kpi .k-ico svg{width:16px;height:16px;stroke-width:2}
.kpi .k-val{font-family:var(--display);font-size:29px;font-weight:600;letter-spacing:-.02em;line-height:1}
.kpi .k-sub{font-size:12px;color:var(--ink-3);margin-top:5px;font-family:var(--mono)}
.grid-2{display:grid;grid-template-columns:1.35fr 1fr;gap:16px;margin-bottom:16px}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:12px;box-shadow:var(--sh)}
.p-head{display:flex;align-items:center;justify-content:space-between;padding:15px 18px;border-bottom:1px solid var(--line-2)}
.p-head h3{font-family:var(--display);font-size:14px;font-weight:600}.p-head .hint{font-size:11px;color:var(--ink-3);font-family:var(--mono)}
.p-body{padding:16px 18px}.chart-wrap{position:relative;height:230px}
.funnel{display:flex;flex-direction:column;gap:9px;padding:4px 0}
.fn-row{display:grid;grid-template-columns:130px 1fr 42px;align-items:center;gap:12px}
.fn-lbl{font-size:12.5px;font-weight:500;color:var(--ink-2)}
.fn-track{height:26px;background:var(--line-2);border-radius:6px;overflow:hidden}
.fn-fill{height:100%;border-radius:6px;display:flex;align-items:center;padding-left:10px;color:#fff;font-family:var(--mono);font-size:11px;font-weight:600;transition:width .6s}
.fn-n{font-family:var(--mono);font-size:13px;font-weight:600;text-align:right}
.hot{display:flex;flex-direction:column}
.hot-row{display:flex;align-items:center;gap:14px;padding:12px 18px;border-bottom:1px solid var(--line-2);transition:.12s;cursor:pointer}
.hot-row:last-child{border-bottom:none}.hot-row:hover{background:var(--surface-2)}
.score-badge{width:40px;height:40px;border-radius:9px;display:grid;place-items:center;font-family:var(--display);font-weight:700;font-size:16px;flex:none;color:#fff}
.hot-mid{flex:1;min-width:0}.hot-title{font-weight:600;font-size:13.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hot-meta{font-size:11.5px;color:var(--ink-3);font-family:var(--mono);margin-top:2px}
.hot-val{font-family:var(--display);font-weight:600;font-size:14px;flex:none;color:var(--ink-2)}
.filters{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:18px}
.facet{position:relative}
.facet select{appearance:none;background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:8px 32px 8px 13px;font-family:var(--body);font-size:13px;font-weight:500;color:var(--ink);cursor:pointer}
.facet select:hover{border-color:var(--ink-3)}
.facet::after{content:"";position:absolute;right:12px;top:50%;transform:translateY(-25%) rotate(45deg);width:6px;height:6px;border-right:2px solid var(--ink-3);border-bottom:2px solid var(--ink-3);pointer-events:none}
.seg{display:inline-flex;background:var(--surface-2);border:1px solid var(--line);border-radius:8px;padding:3px}
.seg button{padding:6px 13px;border-radius:6px;font-size:12.5px;font-weight:600;color:var(--ink-2);transition:.12s}
.seg button.on{background:var(--surface);color:var(--amarante);box-shadow:var(--sh)}
.chip-clear{margin-left:auto;font-size:12.5px;color:var(--ink-3);font-weight:600}.chip-clear:hover{color:var(--amarante)}
.tbl-wrap{background:var(--surface);border:1px solid var(--line);border-radius:12px;overflow:hidden;box-shadow:var(--sh)}
table{width:100%;border-collapse:collapse;font-size:13px}
thead th{text-align:left;font-size:11px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--ink-3);padding:12px 16px;border-bottom:1px solid var(--line);background:var(--surface-2);white-space:nowrap;cursor:pointer;user-select:none}
thead th:hover{color:var(--ink)}.ar{opacity:.4;font-size:9px;margin-left:3px}
tbody td{padding:13px 16px;border-bottom:1px solid var(--line-2);vertical-align:middle}
tbody tr{transition:.1s;cursor:pointer}tbody tr:hover{background:var(--surface-2)}tbody tr:last-child td{border-bottom:none}
.t-title{font-weight:600;max-width:340px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.t-sub{font-size:11px;color:var(--ink-3);font-family:var(--mono);margin-top:2px}
.t-val{font-family:var(--mono);font-weight:600;white-space:nowrap}.t-score{font-family:var(--display);font-weight:700;font-size:15px}
.pill{display:inline-flex;align-items:center;gap:5px;padding:3px 9px;border-radius:20px;font-size:11px;font-weight:600;font-family:var(--mono);white-space:nowrap}
/* Priorite : UNE seule semantique de couleur, partagee avec PRIO_COLOR (JS).
   Amarante = a traiter en priorite. Le rouge reste reserve aux ALERTES
   (echeance imminente, aggravation geopolitique), jamais a une priorite. */
.pill.contacter{background:var(--amarante-soft);color:var(--amarante)}.pill.surveiller{background:var(--amber-soft);color:var(--amber)}.pill.ignorer{background:var(--line-2);color:var(--ink-3)}
.pill.neutre{background:var(--surface-2);color:var(--ink-2);border:1px solid var(--line)}
.tag-src{font-family:var(--mono);font-size:10.5px;font-weight:600;padding:2px 7px;border-radius:5px;background:var(--blue-soft);color:var(--blue)}
.tag-src.ATTRIB{background:var(--amarante-soft);color:var(--amarante)}.tag-src.PRIVÉ{background:var(--amber-soft);color:var(--amber)}
.flag{font-size:11px;font-weight:600;font-family:var(--mono);color:var(--red)}
.mini-badges{display:flex;gap:5px;margin-top:4px;flex-wrap:wrap}
.mb{font-size:9.5px;font-family:var(--mono);font-weight:600;padding:1px 6px;border-radius:4px;text-transform:uppercase}
.mb.renouv{background:var(--amber-soft);color:var(--amber)}.mb.etr{background:var(--blue-soft);color:var(--blue)}.mb.secu{background:var(--red-soft);color:var(--red)}
.mb.surv{background:var(--blue-soft);color:var(--blue)}.mb.attribp{background:var(--green-soft);color:var(--green)}
.chip-toggle{padding:8px 13px;border-radius:8px;font-size:12.5px;font-weight:600;border:1px solid var(--line);background:var(--surface);color:var(--ink-2)}
.chip-toggle.on{background:var(--blue-soft);color:var(--blue);border-color:var(--blue)}
.map-view{display:grid;grid-template-columns:250px 1fr;gap:16px;height:calc(100vh - 60px - 52px)}
#map{border-radius:12px;border:1px solid var(--line);box-shadow:var(--sh);height:100%}
.map-side{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px;box-shadow:var(--sh);overflow:auto}
.map-side h4{font-family:var(--display);font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--ink-3);margin:16px 0 8px}.map-side h4:first-child{margin-top:0}
.leg{display:flex;align-items:center;gap:9px;padding:6px 0;font-size:12.5px;font-weight:500}.leg .sw{width:12px;height:12px;border-radius:50%;flex:none}
.chk{display:flex;align-items:center;gap:9px;padding:5px 0;font-size:12.5px;cursor:pointer}.chk input{accent-color:var(--amarante);width:15px;height:15px}
.leaflet-popup-content-wrapper{border-radius:10px;box-shadow:var(--sh-2)}.leaflet-popup-content{margin:13px 15px;font-family:var(--body)}
.pop-t{font-family:var(--display);font-weight:600;font-size:13px;margin-bottom:5px;color:var(--ink)}.pop-m{font-size:11.5px;color:var(--ink-2);font-family:var(--mono);line-height:1.6}
.soon{display:grid;place-items:center;height:60vh;text-align:center}
.soon .ico{width:64px;height:64px;border-radius:16px;background:var(--amarante-soft);display:grid;place-items:center;margin:0 auto 18px}.soon .ico svg{width:30px;height:30px;color:var(--amarante);stroke-width:1.7}
.soon h3{font-family:var(--display);font-size:20px;font-weight:600;margin-bottom:8px}.soon p{color:var(--ink-2);max-width:440px;font-size:13.5px}
.drawer-ov{position:fixed;inset:0;background:rgba(20,24,31,.35);opacity:0;pointer-events:none;transition:.2s;z-index:50}.drawer-ov.on{opacity:1;pointer-events:auto}
.drawer{position:fixed;top:0;right:0;bottom:0;width:440px;max-width:92vw;background:var(--surface);box-shadow:-8px 0 32px rgba(20,24,31,.18);transform:translateX(100%);transition:.28s cubic-bezier(.4,0,.2,1);z-index:51;overflow:auto}.drawer.on{transform:none}
.dr-head{padding:22px 24px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--surface)}
.dr-close{position:absolute;top:20px;right:20px;width:30px;height:30px;border-radius:7px;display:grid;place-items:center;color:var(--ink-3)}.dr-close:hover{background:var(--surface-2);color:var(--ink)}
.dr-src{font-family:var(--mono);font-size:11px;font-weight:600;color:var(--amarante)}
.dr-head h3{font-family:var(--display);font-size:18px;font-weight:600;margin:6px 0 10px;line-height:1.3;padding-right:34px}
.dr-body{padding:22px 24px}.dr-sec{margin-bottom:22px}
.dr-sec h5{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--ink-3);margin-bottom:10px}
.dr-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.dr-field .l{font-size:11px;color:var(--ink-3);font-family:var(--mono)}.dr-field .v{font-size:14px;font-weight:600;margin-top:2px}
.dr-analyse{background:var(--surface-2);border:1px solid var(--line-2);border-radius:10px;padding:14px 16px;font-size:13px;line-height:1.6;color:var(--ink-2)}
.dr-actions{display:flex;gap:10px;margin-top:8px}.dr-actions .btn{flex:1;justify-content:center}
.cand-list{display:flex;flex-direction:column;gap:7px}
.cand{background:var(--surface-2);border:1px solid var(--line-2);border-radius:9px;padding:10px 13px;cursor:pointer;transition:.12s}
.cand:hover{border-color:var(--amarante);background:var(--amarante-soft)}
.cand-n{font-weight:600;font-size:13px}
.cand-m{font-size:11px;color:var(--ink-3);font-family:var(--mono);margin-top:2px}
.cand-etr{color:var(--blue);font-weight:600}
.cand-note{font-size:10.5px;color:var(--ink-3);font-family:var(--mono);margin-top:8px;font-style:italic}
.doss{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:14px;box-shadow:var(--sh)}
.doss-top{display:flex;align-items:flex-start;gap:12px;margin-bottom:14px}
.doss-tit{flex:1;min-width:0}
.doss-nom{font-weight:600;font-size:14px;line-height:1.3}
.doss-meta{font-size:11px;color:var(--ink-3);font-family:var(--mono);margin-top:3px}
.doss-pid{font-family:var(--mono);font-size:11px;font-weight:600;padding:3px 9px;border-radius:20px;background:var(--amarante-soft);color:var(--amarante);flex:none}
.pipe{display:flex;align-items:center;margin:6px 0 14px}
.ph{flex:1;text-align:center;font-family:var(--mono);font-size:10.5px;font-weight:600;text-transform:uppercase;padding:7px 4px;border-radius:7px;background:var(--line-2);color:var(--ink-3);position:relative}
.ph.on{background:var(--amarante-soft);color:var(--amarante)}
.ph.cur{background:var(--amarante);color:#fff}
.ph-link{width:26px;height:2px;background:var(--line);flex:none}
.ph-link.on{background:var(--amarante)}
.tl{display:flex;flex-direction:column;gap:0}
.tl-row{display:flex;gap:11px;padding:9px 0;border-top:1px solid var(--line-2)}
.tl-dot{width:9px;height:9px;border-radius:50%;flex:none;margin-top:5px}
.tl-body{flex:1;min-width:0}
.tl-t{font-size:12.5px;line-height:1.35}
.tl-m{font-size:10.5px;color:var(--ink-3);font-family:var(--mono);margin-top:2px}
.tl-ph{font-size:9px;font-family:var(--mono);font-weight:700;text-transform:uppercase;padding:1px 6px;border-radius:4px;background:var(--surface-2);color:var(--ink-3);margin-right:6px}
.empty{padding:60px;text-align:center;color:var(--ink-3);font-family:var(--mono);font-size:13px}
.firmo-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}
.fbadges{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 4px}
.fbadge{font-family:var(--mono);font-size:10px;font-weight:600;padding:2px 8px;border-radius:5px;background:var(--surface-2);border:1px solid var(--line);color:var(--ink-2)}
.fbadge.suivi{background:var(--amarante-soft);color:var(--amarante);border-color:transparent}
.fbadge.etr{background:var(--blue-soft);color:var(--blue);border-color:transparent}
.fbadge.sig{background:var(--amber-soft);color:var(--amber);border-color:transparent}
.fbadge.zero{color:var(--ink-3)}
.fchip.alt{background:var(--blue-soft);color:var(--blue)}
.fi-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:18px}
.fi-stat{background:var(--surface-2);border:1px solid var(--line);border-radius:10px;padding:12px 10px;text-align:center}
.fi-stat .n{font-family:var(--display);font-size:20px;font-weight:700;color:var(--ink)}
.fi-stat .l{font-size:10px;color:var(--ink-3);text-transform:uppercase;letter-spacing:.04em;margin-top:3px}
.fi-tag{font-family:var(--mono);font-size:10px;font-weight:600;padding:1px 7px;border-radius:5px;margin-right:8px}
.fi-tag.gagne{background:var(--green-soft);color:var(--green)}
.fi-tag.sig{background:var(--amber-soft);color:var(--amber)}
.fi-empty{font-size:13px;color:var(--ink-3);padding:14px;text-align:center;background:var(--surface-2);border-radius:10px}
.fcard{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:18px;box-shadow:var(--sh);transition:.15s;cursor:pointer}
.fcard:hover{box-shadow:var(--sh-2);transform:translateY(-2px)}
.fcard-top{display:flex;align-items:flex-start;gap:12px;margin-bottom:14px}
.fmono{width:42px;height:42px;border-radius:10px;background:var(--amarante-soft);color:var(--amarante);display:grid;place-items:center;font-family:var(--display);font-weight:700;font-size:16px;flex:none}
.fname{font-family:var(--display);font-weight:600;font-size:15px;line-height:1.25}
.fmeta{font-size:11.5px;color:var(--ink-3);font-family:var(--mono);margin-top:3px}
.fstats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:14px 0;padding:12px 0;border-top:1px solid var(--line-2);border-bottom:1px solid var(--line-2)}
.fstat{text-align:center}.fstat .n{font-family:var(--display);font-weight:600;font-size:19px}.fstat .l{font-size:10px;color:var(--ink-3);font-family:var(--mono);text-transform:uppercase;margin-top:2px}
.fchips{display:flex;flex-wrap:wrap;gap:6px}
.fchip{font-size:10.5px;font-family:var(--mono);font-weight:600;padding:2px 8px;border-radius:5px;background:var(--surface-2);color:var(--ink-2);border:1px solid var(--line)}
.geo-head{display:flex;gap:14px;margin-bottom:20px;flex-wrap:wrap}
.geo-kpi{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px 20px;box-shadow:var(--sh);flex:1;min-width:150px}
.geo-kpi .n{font-family:var(--display);font-weight:600;font-size:26px}.geo-kpi .l{font-size:11.5px;color:var(--ink-2);font-weight:600;margin-top:2px}
.geo-zone{margin-bottom:22px}
.geo-zone h3{font-family:var(--display);font-size:15px;font-weight:600;margin-bottom:10px;display:flex;align-items:center;gap:8px}
.geo-zone h3 .zdot{width:9px;height:9px;border-radius:50%}
.geo-row{display:flex;align-items:center;gap:14px;background:var(--surface);border:1px solid var(--line);border-left-width:3px;border-radius:9px;padding:13px 16px;margin-bottom:8px;box-shadow:var(--sh)}
.geo-sev{width:36px;height:36px;border-radius:8px;display:grid;place-items:center;font-family:var(--display);font-weight:700;color:#fff;flex:none;font-size:14px}
.geo-mid{flex:1;min-width:0}.geo-pays{font-weight:600;font-size:14px}
.geo-motif{font-size:12px;color:var(--ink-2);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.geo-sens{font-family:var(--mono);font-size:11px;font-weight:600;padding:3px 9px;border-radius:20px;white-space:nowrap;flex:none}
.geo-sens.up{background:var(--red-soft);color:var(--red)}.geo-sens.down{background:var(--green-soft);color:var(--green)}
.geo-date{font-family:var(--mono);font-size:11px;color:var(--ink-3);flex:none}
.w-dom{margin-bottom:26px}
.w-dom-h{font-family:var(--display);font-size:15px;font-weight:600;margin-bottom:12px;display:flex;align-items:center;gap:9px;padding-bottom:8px;border-bottom:2px solid var(--amarante-soft)}
.w-dom-h .c{margin-left:auto;font-family:var(--mono);font-size:12px;color:var(--ink-3);font-weight:500}
.w-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.went{background:var(--surface);border:1px solid var(--line);border-radius:11px;padding:14px 16px;box-shadow:var(--sh);transition:.15s}
.went.hot{border-left:3px solid var(--amarante)}
.went-top{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.went-nom{font-weight:600;font-size:13.5px;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.went-n{font-family:var(--mono);font-size:11px;font-weight:600;padding:2px 8px;border-radius:20px;background:var(--amarante-soft);color:var(--amarante);flex:none}
.went-n.zero{background:var(--line-2);color:var(--ink-3)}
.wsig{display:flex;gap:9px;align-items:flex-start;padding:8px 0;border-top:1px solid var(--line-2);cursor:pointer}
.wsig:hover{opacity:.75}
.wsig-ico{width:22px;height:22px;border-radius:6px;display:grid;place-items:center;font-size:11px;flex:none;margin-top:1px}
.wsig-txt{flex:1;min-width:0}
.wsig-t{font-size:12.5px;line-height:1.35;color:var(--ink)}
.wsig-m{font-size:10.5px;color:var(--ink-3);font-family:var(--mono);margin-top:2px}
.wsig-none{font-size:12px;color:var(--ink-3);font-family:var(--mono);padding:4px 0}
.tact{font-size:9.5px;font-family:var(--mono);font-weight:600;padding:1px 6px;border-radius:4px;text-transform:uppercase;background:var(--blue-soft);color:var(--blue)}
.tact.delegation_mission{background:var(--amarante-soft);color:var(--amarante)}
.tact.recrutement_local,.tact.contrat_export{background:var(--green-soft);color:var(--green)}
.tact.incident{background:var(--red-soft);color:var(--red)}
.opps-bar{display:flex;align-items:center;gap:14px;margin-bottom:14px;flex-wrap:wrap}
.opps-intro{flex:1;min-width:220px;font-size:12.5px;color:var(--ink-2);background:var(--surface-2);border:1px solid var(--line-2);border-left:3px solid var(--amarante);border-radius:9px;padding:10px 14px;line-height:1.5}
.opps-intro b{color:var(--ink)}
.t-date{font-family:var(--mono);font-size:12px;color:var(--ink-2);white-space:nowrap}
.jauge{display:flex;align-items:center;gap:8px;min-width:96px}
.jauge-bar{flex:1;height:6px;border-radius:3px;background:var(--line-2);overflow:hidden}
.jauge-bar span{display:block;height:100%;border-radius:3px}
.jauge b{font-family:var(--mono);font-size:12px;min-width:22px;text-align:right}
.cp-wrap{background:var(--surface);border:1px solid var(--line);border-left:3px solid var(--amber);border-radius:12px;padding:16px 18px;margin-bottom:18px;box-shadow:var(--sh)}
.cp-titre{font-family:var(--display);font-size:15px;font-weight:700;margin-bottom:12px}
.cp-sub{display:block;font-family:var(--mono);font-size:11px;font-weight:500;color:var(--ink-3);margin-top:3px}
.cp{border-top:1px solid var(--line-2);padding:11px 0}
.cp.promu{background:var(--amber-soft);margin:0 -10px;padding:11px 10px;border-radius:8px}
.cp-h{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.cp-n{font-weight:600;font-size:13.5px}
.cp-badges{display:flex;gap:5px;flex-wrap:wrap}
.cp-conf{margin-left:auto;font-family:var(--mono);font-weight:700;font-size:14px}
.cp-m{font-size:11.5px;color:var(--ink-2);margin-top:4px;font-family:var(--mono)}
.cp-arts{margin-top:7px;padding-left:10px;border-left:2px solid var(--line-2)}
.cp-art{font-size:11.5px;color:var(--ink-2);padding:2px 0}
.cp-d{font-family:var(--mono);color:var(--ink-3);margin-right:7px}
.cp-todo{margin-top:7px;font-size:11.5px;color:var(--amber);font-weight:600}
.ph-pill{display:inline-block;font-family:var(--mono);font-size:11px;font-weight:600;padding:3px 9px;border-radius:6px;background:var(--surface-2);border:1px solid var(--line);color:var(--ink-2);white-space:nowrap}
.mb.recul{background:var(--red-soft);color:var(--red)}
.mb.prosp{background:var(--green-soft);color:var(--green)}
.k-sub{font-size:10px;color:var(--ink-3);font-family:var(--mono);margin-top:4px}
.tl-an{margin-bottom:14px}
.tl-an-h{font-family:var(--mono);font-size:12px;font-weight:700;color:var(--amarante);padding-bottom:5px;margin-bottom:5px;border-bottom:1px solid var(--line-2)}
.t-env{font-family:var(--mono);font-size:12px;font-weight:600;color:var(--blue);white-space:nowrap}
.t-nc{font-family:var(--mono);font-size:12px;color:var(--ink-3)}
.mb.neuf{background:var(--amarante-soft);color:var(--amarante)}
.mb.proj{background:var(--blue-soft);color:var(--blue);cursor:pointer}.mb.proj:hover{background:var(--blue);color:#fff}
.mb.inc{background:var(--red-soft);color:var(--red)}
.tag-orig{font-family:var(--mono);font-size:11px;font-weight:600;padding:2px 8px;border-radius:5px;background:var(--surface-2);border:1px solid var(--line);color:var(--ink-2)}
.doss-explain{display:flex;gap:14px;align-items:flex-start;background:var(--surface);border:1px solid var(--line);border-left:3px solid var(--amarante);border-radius:12px;padding:15px 18px;margin-bottom:16px;box-shadow:var(--sh);font-size:13px;line-height:1.55;color:var(--ink-2)}
.doss-explain b{color:var(--ink)}
.doss-explain-ic{font-size:22px;line-height:1;flex:none}
.mono-inline{font-family:var(--mono);font-size:12px;background:var(--line-2);padding:1px 6px;border-radius:4px;color:var(--ink-2)}
.doss-hl{outline:3px solid var(--amarante);outline-offset:2px;transition:outline .3s}
.tl-row[onclick]:hover{background:var(--surface-2);border-radius:7px}
.tl-go{margin-left:7px;color:var(--amarante);font-weight:700;opacity:0;transition:.12s}
.tl-row[onclick]:hover .tl-go{opacity:1}
/* Etat du dernier run par source : volume + fraicheur du plus recent lead.
   Une source a 0, ou qui n'a plus rien produit depuis longtemps, se voit d'un
   coup d'oeil. Ce n'est pas un diagnostic de panne, c'est un coup d'oeil. */
.sante{margin-bottom:18px;background:var(--surface);border:1px solid var(--line);border-left:3px solid var(--ink-3);border-radius:12px;padding:12px 16px;box-shadow:var(--sh)}
.sante:empty{display:none}
.sante.alerte{border-left-color:var(--amber)}
.sante-tete{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:10px}
.sante-titre{font-family:var(--display);font-size:14px;font-weight:700}
.sante-sub{font-family:var(--mono);font-size:11px;color:var(--ink-3)}
.sante-sub .warn{color:var(--amber);font-weight:600}
.sante-grid{display:flex;flex-wrap:wrap;gap:6px}
.sc{display:inline-flex;align-items:center;gap:7px;padding:4px 9px;border-radius:7px;border:1px solid var(--line);background:var(--surface-2);font-family:var(--mono);font-size:11px;color:var(--ink-2)}
.sc .src{color:var(--ink);font-weight:700}
.sc .ag{color:var(--ink-3)}
.sc .dot{width:7px;height:7px;border-radius:50%;flex:none;background:var(--ink-3)}
.sc.frais{border-color:rgba(35,122,87,.45)}.sc.frais .dot{background:var(--green)}
.sc.ancien{border-color:rgba(176,116,25,.55)}.sc.ancien .dot{background:var(--amber)}
.sc.absent{opacity:.5}.sc.absent .dot{background:var(--red)}
/* Echeance de l'avis : le champ le plus operationnel d'un marche a saisir. */
.k-note{color:var(--ink-3);font-size:9.5px}
.post-up{display:block;font-family:var(--mono);font-size:9.5px;font-weight:700;color:var(--red);text-transform:none;letter-spacing:0;margin-top:2px;cursor:help;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fmeta-sect{color:var(--ink-2);font-weight:600}
.fmeta-warn{color:var(--amber);font-weight:700;cursor:help}
.ech{display:block;font-family:var(--mono);font-size:9px;color:var(--ink-3);text-transform:uppercase;letter-spacing:.04em;margin-top:1px;cursor:help}
.issue-g{color:var(--green);border-color:rgba(35,122,87,.45)}.issue-g:hover{background:var(--green-soft);border-color:var(--green)}
.issue-p{color:var(--red);border-color:rgba(192,57,43,.4)}.issue-p:hover{background:var(--red-soft);border-color:var(--red)}
.issue-note{font-size:11px;color:var(--ink-3);font-family:var(--mono);margin-top:9px;line-height:1.5}
.issue-close{padding:12px 14px;border-radius:10px;font-weight:700;font-size:14px}
.issue-close.gagne{background:var(--green-soft);color:var(--green)}
.issue-close.perdu{background:var(--line-2);color:var(--ink-2)}
.issue-close .issue-sub{font-size:11px;font-weight:400;font-family:var(--mono);color:var(--ink-3);margin-top:4px}
.mp-ov{position:fixed;inset:0;background:rgba(20,24,31,.45);z-index:120;display:grid;place-items:center;padding:20px}
.mp-box{background:var(--surface);border-radius:14px;box-shadow:var(--sh-2);padding:24px;width:min(430px,100%)}
.mp-t{font-family:var(--display);font-size:17px;font-weight:600}
.mp-s{font-size:12px;color:var(--ink-3);font-family:var(--mono);margin:4px 0 16px}
.mp-list{display:flex;flex-direction:column;gap:2px}
.mp-opt{display:flex;align-items:center;gap:10px;padding:9px 11px;border-radius:8px;font-size:13.5px;cursor:pointer;transition:.1s}
.mp-opt:hover{background:var(--surface-2)}.mp-opt input{accent-color:var(--amarante);width:16px;height:16px}
.mp-note{font-size:11px;color:var(--ink-3);font-family:var(--mono);margin:14px 0 4px;line-height:1.5}
.mp-btns{display:flex;gap:10px;margin-top:14px}.mp-btns .btn{flex:1;justify-content:center}
.pill.gagne{background:var(--green-soft);color:var(--green)}.pill.perdu{background:var(--line-2);color:var(--ink-3)}
.fn-d{font-size:10px;color:var(--ink-3);font-family:var(--mono);font-weight:400;margin-top:1px}
.jx{display:inline-block;font-family:var(--mono);font-size:11px;font-weight:700;padding:2px 7px;border-radius:5px}
.jx.urgent{background:var(--red-soft);color:var(--red)}
.jx.proche{background:var(--amber-soft);color:var(--amber)}
.jx.large{background:var(--surface-2);color:var(--ink-2);border:1px solid var(--line)}
.jx.clos{background:var(--line-2);color:var(--ink-3);text-decoration:line-through}
.mb.geo{background:var(--red-soft);color:var(--red)}
.sfbase{font-family:var(--mono);font-size:11px;color:var(--ink-3);text-decoration:line-through;margin-right:4px}
@media(max-width:1100px){.kpis{grid-template-columns:repeat(2,1fr)}.grid-2{grid-template-columns:1fr}.map-view{grid-template-columns:1fr;height:auto}#map{height:60vh}}
@media(max-width:720px){.app{grid-template-columns:1fr}.side{display:none}}
</style>
</head>
<body>
<div class="app">
  <aside class="side">
    <div class="brand"><div class="mark"></div><div><h1>Radar Amarante</h1><div class="tag">SALLE DE SITUATION</div></div></div>
    <nav class="nav" id="nav">
      <div class="nav-lbl">Pilotage</div>
      <a data-view="overview" class="on"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></svg>Vue d'ensemble</a>
      <a data-view="opps"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M3 6h18M3 12h18M3 18h18"/></svg>Opportunités<span class="cnt" id="cnt-opps"></span></a>
      <a data-view="map"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M9 3L3 6v15l6-3 6 3 6-3V3l-6 3-6-3z"/><path d="M9 3v15M15 6v15"/></svg>Carte des théâtres</a>
      <div class="nav-lbl">Renseignement</div>
      <a data-view="proj"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M3 20h18M6 20V10M11 20V4M16 20v-8M21 20v-5"/></svg>Projets<span class="cnt" id="cnt-proj"></span></a>
      <a data-view="attrib"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M6 21V7a2 2 0 012-2h8a2 2 0 012 2v14"/><path d="M6 21h12M10 9h4M10 13h4M10 17h4"/></svg>Attributions<span class="cnt" id="cnt-attrib"></span></a>
      <a data-view="firmo"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/></svg>Entreprises<span class="cnt" id="cnt-firmo"></span></a>
      <a data-view="doss"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M4 7h16M4 12h16M4 17h10"/><circle cx="19" cy="17" r="2"/></svg>Dossiers<span class="cnt" id="cnt-doss"></span></a>
      <a data-view="geo"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 2l9 5v6c0 5-4 8-9 9-5-1-9-4-9-9V7z"/></svg>Géopolitique</a>
    </nav>
    <div class="side-foot"><span class="dot-live"></span> <span id="run-meta">Cockpit</span></div>
  </aside>
  <div class="main">
    <div class="top">
      <div><h2 id="top-title">Vue d'ensemble</h2></div>
      <div class="crumb" id="top-crumb"></div>
      <div class="spacer"></div>
      <label class="search"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg><input id="search" placeholder="Rechercher un marché, pays, titulaire..."></label>
      <button class="btn pri" onclick="exportCSV()"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12m0 0l-4-4m4 4l4-4M4 21h16"/></svg>Exporter</button>
    </div>
    <section class="view on" id="v-overview">
      <div class="sante" id="santeRun"></div>
      <div class="theatres" id="theatres"></div>
      <div class="kpis" id="kpis"></div>
      <div class="grid-2">
        <div class="panel"><div class="p-head"><h3>Marchés par théâtre</h3><span class="hint">à saisir · valeur M€ (hors attributions)</span></div><div class="p-body"><div class="chart-wrap"><canvas id="c-zone"></canvas></div></div></div>
        <div class="panel"><div class="p-head"><h3>Secteurs</h3><span class="hint">part des marchés à saisir</span></div><div class="p-body"><div class="chart-wrap"><canvas id="c-sect"></canvas></div></div></div>
      </div>
      <div class="grid-2">
        <div class="panel"><div class="p-head"><h3>Détections par mois</h3><span class="hint">avis vs attributions</span></div><div class="p-body"><div class="chart-wrap"><canvas id="c-time"></canvas></div></div></div>
        <div class="panel"><div class="p-head"><h3>Pipeline de prospection</h3><span class="hint">du signal au contrat</span></div><div class="p-body"><div class="funnel" id="funnel"></div></div></div>
      </div>
      <div class="panel"><div class="p-head"><h3>À contacter en priorité</h3><span class="hint">score le plus élevé, non traités</span></div><div class="hot" id="hot"></div></div>
    </section>
    <section class="view" id="v-opps">
      <div class="opps-bar">
        <div class="opps-intro">Marchés à saisir : avis d'appel d'offres et signaux privés (les <b>attributions</b> ont leur propre onglet). Une ligne = une action.</div>
        <button class="chip-toggle" id="f-neuf" onclick="toggleNeuf()">✦ Nouveautés du dernier run</button>
      </div>
      <div class="filters">
        <div class="facet"><select id="f-zone"><option value="">Tous les théâtres</option></select></div>
        <div class="facet"><select id="f-sect"><option value="">Tous les secteurs</option></select></div>
        <div class="facet"><select id="f-type"><option value="">Avis + signaux</option><option value="avis">Avis de marché</option><option value="prive">Signaux privés</option></select></div>
        <div class="facet"><select id="f-src"><option value="">Toutes les sources</option></select></div>
        <div class="seg" id="f-prio"><button data-p="traiter" class="on">À traiter</button><button data-p="contacter">À contacter</button><button data-p="surveiller">À surveiller</button><button data-p="">Tout</button></div>
        <button class="chip-toggle" id="f-surv" onclick="toggleSurv()">👁 Surveillés</button>
        <button class="chip-clear" onclick="resetFilters()">Réinitialiser</button>
      </div>
      <div class="tbl-wrap"><table><thead><tr>
        <th data-sort="titre">Marché<span class="ar">↕</span></th>
        <th data-sort="ts">Détecté<span class="ar">↕</span></th>
        <th data-sort="zone">Théâtre<span class="ar">↕</span></th>
        <th data-sort="secteur">Secteur<span class="ar">↕</span></th>
        <th data-sort="valeur">Montant<span class="ar">↕</span></th>
        <th data-sort="score" title="Trois échelles distinctes selon l'origine de la ligne. Survole un score pour savoir laquelle s'applique.">Score<span class="ar">↕</span></th>
        <th data-sort="prio">Action<span class="ar">↕</span></th>
      </tr></thead><tbody id="tbody"></tbody></table></div>
      <div style="padding:12px 4px;font-family:var(--mono);font-size:12px;color:var(--ink-3)" id="tbl-count"></div>
    </section>
    <section class="view" id="v-map">
      <div class="map-view">
        <div class="map-side">
          <h4>Priorité</h4>
          <div class="leg"><span class="sw" style="background:#8E2649"></span>À contacter</div>
          <div class="leg"><span class="sw" style="background:#B07419"></span>À surveiller</div>
          <div class="leg"><span class="sw" style="background:#8B93A2"></span>À écarter</div>
          <h4>Théâtres</h4><div id="map-zones"></div>
          <h4>Sources</h4><div id="map-srcs"></div>
        </div>
        <div id="map"></div>
      </div>
    </section>
    <section class="view" id="v-attrib">
      <div class="kpis" id="kpis-attrib"></div>
      <div class="opps-bar"><div class="opps-intro">Qui a gagné quoi en zone à risque. Un titulaire étranger = un déploiement à démarcher. Trié du plus récent au plus ancien.</div></div>
      <div class="filters">
        <div class="facet"><select id="af-zone"><option value="">Tous les théâtres</option></select></div>
        <div class="facet"><select id="af-sect"><option value="">Tous les secteurs</option></select></div>
        <div class="facet"><select id="af-orig"><option value="">Toutes les origines</option></select></div>
        <div class="seg" id="af-etr"><button data-e="" class="on">Tous</button><button data-e="1">Étrangers</button><button data-e="renouv">Renouvellement</button></div>
        <button class="chip-clear" onclick="resetAttrib()">Réinitialiser</button>
      </div>
      <div class="tbl-wrap"><table><thead><tr><th data-asort="titulaire">Titulaire<span class="ar">↕</span></th><th>Marché gagné</th><th data-asort="ts">Détecté<span class="ar">↕</span></th><th data-asort="zone">Théâtre<span class="ar">↕</span></th><th data-asort="pays_tit">Origine<span class="ar">↕</span></th><th data-asort="valeur">Montant<span class="ar">↕</span></th><th>Statut</th></tr></thead><tbody id="tbody-attrib"></tbody></table></div>
      <div style="padding:12px 4px;font-family:var(--mono);font-size:12px;color:var(--ink-3)" id="attrib-count"></div>
    </section>
    <section class="view" id="v-firmo">
      <div class="opps-bar"><div class="opps-intro">Chaque entreprise, dédupliquée par entité (une même société regroupée quelles que soient ses variantes de nom) : marchés gagnés, signaux de déploiement et comptes suivis, réunis. Clique une fiche pour son historique 360.</div></div>
      <div class="filters">
        <label class="search" style="max-width:240px"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg><input id="ff-q" placeholder="Rechercher une entreprise..."></label>
        <div class="facet"><select id="ff-tri"><option value="activite">Activité récente</option><option value="marches">Marchés gagnés</option><option value="signaux">Signaux</option><option value="valeur">Valeur</option><option value="theatres">Présence</option></select></div>
        <div class="seg" id="ff-etr"><button data-e="" class="on">Toutes</button><button data-e="1">Étrangères</button></div>
        <button class="chip-toggle" id="ff-suivis" onclick="toggleSuivis()">👁 Suivies</button>
        <span class="chip-clear" id="ff-count"></span>
      </div>
      <div id="firmo-grid" class="firmo-grid"></div>
    </section>
    <section class="view" id="v-doss">
      <div class="doss-explain">
        <div class="doss-explain-ic">📁</div>
        <div><b>Un dossier suit un même projet Banque Mondiale à travers ses phases</b> : étude amont, avis d'appel d'offres, puis attribution. Le rattachement est automatique via l'identifiant projet BM (<span class="mono-inline">P######</span>), sans rapprochement de texte. Par défaut, seuls les dossiers <b>multi-phases</b> sont affichés (là où on voit le marché évoluer). Chaque ligne de la frise est cliquable.</div>
      </div>
      <div class="filters"><div class="seg" id="d-mp"><button data-m="1" class="on">Suivi actif (2+ phases)</button><button data-m="">Tous (dont mono-phase)</button></div><span class="chip-clear" id="d-count"></span></div>
      <div id="doss-body"></div>
    </section>
    <section class="view" id="v-proj">
      <div class="opps-bar">
        <div class="opps-intro"><b>Grands projets suivis avant l'appel d'offres.</b> Un projet regroupe tous ses signaux (annonces, financements, consultants, EPC) sous une même entité, avec sa phase, sa trajectoire et les entreprises qui vont y déployer du personnel. <b>Maturité</b> = où en est le projet. <b>Opportunité</b> = ce qu'il vaut pour Amarante. Les deux sont indépendants.</div>
        <button class="chip-toggle" id="p-top" onclick="toggleTop()" style="display:none">★ Top 20 opportunités</button>
      </div>
      <div class="kpis" id="kpis-proj"></div>
      <div id="cand-proj"></div>
      <div class="filters" id="p-filtres">
        <label class="search" style="max-width:220px"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg><input id="p-q" placeholder="Rechercher un projet..."></label>
        <div class="facet"><select id="p-pays"><option value="">Tous les pays</option></select></div>
        <div class="facet"><select id="p-sect"><option value="">Tous les secteurs</option></select></div>
        <div class="facet"><select id="p-phase"><option value="">Toutes les phases</option></select></div>
        <div class="seg" id="p-alerte"><button data-a="" class="on">Toutes</button><button data-a="haute">🔴 Haute</button><button data-a="moyenne">🟠 Moyenne</button><button data-a="signal_precoce">🟡 Précoce</button></div>
        <button class="chip-clear" onclick="resetProj()">Réinitialiser</button>
      </div>
      <div class="tbl-wrap"><table><thead><tr>
        <th data-psort="libelle">Projet<span class="ar">↕</span></th>
        <th data-psort="pays">Pays<span class="ar">↕</span></th>
        <th data-psort="valeur_musd">Valeur<span class="ar">↕</span></th>
        <th data-psort="rangPhase">Phase<span class="ar">↕</span></th>
        <th data-psort="maturite" title="Où en est le PROJET, indépendamment d'Amarante.">Maturité<span class="ar">↕</span></th>
        <th data-psort="opportunite" title="Ce que le projet vaut POUR AMARANTE. Ne pas confondre avec la maturité.">Opportunité<span class="ar">↕</span></th>
        <th data-psort="derniere_maj">Dernier signal<span class="ar">↕</span></th>
        <th>Fenêtre</th>
      </tr></thead><tbody id="tbody-proj"></tbody></table></div>
      <div style="padding:12px 4px;font-family:var(--mono);font-size:12px;color:var(--ink-3)" id="proj-count"></div>
    </section>
    <section class="view" id="v-geo">
      <div id="geo-head" class="geo-head"></div>
      <div id="geo-body"></div>
    </section>
  </div>
</div>
<div class="drawer-ov" id="drawer-ov" onclick="closeDrawer()"></div>
<div class="drawer" id="drawer"><div id="drawer-content"></div></div>
<script>
const RAW=__LEADS_JSON__, COORDS=__COORDS_JSON__, RISQUE=__RISQUE_JSON__, GEO=__GEO_JSON__, WATCHLIST=__WATCHLIST_JSON__, CANDIDATS=__CANDIDATS_JSON__, DOSSIERS=__DOSSIERS_JSON__, PROJETS_RAW=__PROJETS_JSON__, CANDPROJ_RAW=__CANDPROJ_JSON__;
// Etat du dernier run par source, derive cote Python (dash.sante_run).
const SANTE=__SANTE_JSON__;
// Posture EFFECTIVE par theatre (socle de risque + aggravation recente),
// derivee cote Python. Cf. radar_cockpit.postures.
const POSTURE=__POSTURE_JSON__;
function candidatsPour(l){
  if(!CANDIDATS||!CANDIDATS.secteur_zone)return [];
  const sect=(l.secteur||"Autre"), zone=(l.zone||"Non classe");
  const c=(CANDIDATS.secteur_zone[sect+"|"+zone]||CANDIDATS.secteur[sect]||CANDIDATS.zone[zone]||[]);
  // ne pas proposer le titulaire lui-meme si le lead EST une attribution
  return c.filter(x=>x.entreprise&&x.entreprise!==l.titulaire).slice(0,6);
}
const SUIVI_URL=__SUIVI_URL__, SUIVI_TOKEN=__SUIVI_TOKEN__, API_STATUT=__API_STATUT__;
const SUIVI_ON=!!SUIVI_URL||API_STATUT;
const SRC_SUIVI={TED:"TED",BM:"Banque Mondiale","PRIVÉ":"Privé BITD",RW:"ReliefWeb",PROPARCO:"Proparco",DFC:"DFC"};
const ONGLET_SRC={TED:"ted_radar",BM:"bm_radar","PRIVÉ":"prive_radar",ATTRIB:"attributions_radar"};
function leadId(l){return l.pub||l.lien||(l.src+"|"+l.pays+"|"+l.acheteur+"|"+l.titre);}
// Statut local (optimiste) : le lead reflete l'action tout de suite, la
// persistance part en arriere-plan (Apps Script + /api/statut si servie par Render).
// ===========================================================================
// ECRITURE D'UN STATUT (P0.2, 26/08/2026) -- UNE seule destination, et elle
// rend des comptes.
// ===========================================================================
// TROIS DEFAUTS CORRIGES ICI, dont un tres couteux :
//
// 1. DEUX ECRITURES CONCURRENTES. La version precedente postait vers l'Apps
//    Script ET vers /api/statut. Deux destinations, aucune transaction : le
//    Sheet et Postgres pouvaient diverger sans que personne le sache.
//
// 2. ECHEC SILENCIEUX. Le POST Apps Script partait en mode no-cors, ce qui
//    rend la reponse ILLISIBLE par construction, et les deux appels finissaient
//    par `.catch(function(){})`, qui avale l'erreur. Le statut etait pose sur
//    l'objet AVANT tout appel et le toast de succes s'affichait quoi qu'il
//    arrive. L'interface mentait a chaque clic rate.
//
// 3. ET SURTOUT : le payload navigateur ne portait PAS de `titre`, alors que
//    le script Apps Script refuse tout envoi sans titre (`missing_fields`).
//    Le bouton n'a donc JAMAIS rien ecrit dans le Sheet depuis le cockpit.
//    Personne ne pouvait le voir : ce mode masquait le refus.
//
// Nouveau contrat : /api/statut (Postgres) est la SEULE ecriture faite par le
// navigateur. Elle est attendue, sa reponse est lue, et l'affichage est
// annule si elle echoue. La replication vers le Sheet se fait cote SERVEUR,
// ou la reponse est lisible et l'echec journalise.
async function envoyerStatut(l,statut,motif,valeur){
  const avant=l.statut;                      // pour pouvoir revenir en arriere
  if(!API_STATUT){
    toast("Page en lecture seule : action non enregistrée.",true);return false;
  }
  l.statut=statut;                           // affichage optimiste
  closeDrawer();go(state.view);
  try{
    const r=await fetch("/api/statut",{method:"POST",credentials:"same-origin",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({onglet:ONGLET_SRC[l.src]||"",publication_number:l.pub||"",
        statut:statut,motif:motif||"",valeur_estimee:(valeur==null?null:+valeur),
        contexte:contexteLead(l)})});
    if(r.status===409||r.status===422){
      // Refus METIER (garde de vocabulaire ou d'enchainement), pas une panne :
      // le message du serveur explique quoi faire, on le montre tel quel.
      let d={};try{d=await r.json();}catch(e){}
      throw new Error(d.detail||("refus "+r.status));
    }
    if(!r.ok)throw new Error("HTTP "+r.status);
    const j=await r.json();
    if(!j||j.ok!==true)throw new Error((j&&j.error)||"réponse inattendue");
    toast(TOAST_STATUT[statut]||("Statut « "+statut+" » enregistré"));
    return true;
  }catch(err){
    // ROLLBACK : ne jamais laisser l'ecran affirmer ce que la base ignore.
    l.statut=avant;go(state.view);
    toast("Échec de l'enregistrement ("+err.message+"). Action NON sauvegardée.",true);
    return false;
  }
}
// Champs d'AFFICHAGE transmis pour la replication vers le Sheet (le script
// Apps Script exige un titre, cf. defaut 3). Ils ne servent JAMAIS a l'ecriture
// en base, qui reste indexee sur (onglet, publication_number).
function contexteLead(l){
  return {titre:l.titre||"",source:SRC_SUIVI[l.src]||l.src||"",pays:l.pays||"",
    zone:l.zone||"",agence:l.acheteur||"",lien:l.lien||"",date_det:l.datedet||"",
    score:l.score,surete:l.surete,comm:l.comm,action:l.prio||"",fenetre:l.win||"",
    contact:(l.nom&&l.nom!=="n.c.")?l.nom:"",email:(l.email&&l.email!=="n.c.")?l.email:"",
    valeur:l.valeur||0,id:leadId(l)};
}
// Un echec doit se voir : fond rouge et duree doublee, pour qu'il ne passe pas
// pour un succes dans le coin de l'ecran.
function toast(msg,erreur){let t=document.getElementById("toast");if(!t){t=document.createElement("div");t.id="toast";t.style.cssText="position:fixed;bottom:26px;left:50%;transform:translateX(-50%);color:#fff;padding:11px 20px;border-radius:9px;font-size:13px;font-weight:600;box-shadow:var(--sh-2);z-index:90;opacity:0;transition:.25s;max-width:min(560px,90vw);text-align:center";document.body.appendChild(t);}t.style.background=erreur?"var(--red)":"var(--ink)";t.textContent=msg;t.style.opacity="1";clearTimeout(t._h);t._h=setTimeout(()=>t.style.opacity="0",erreur?5200:2200);}
// --- SURVEILLANCE : marquer un marche amont pour que le run signale toute
// attribution (surveillance_attributions.py, matching Jaccard). Statut
// 'surveille' -> bascule en 'attribution_publiee' + gagnant au prochain run. ---
let SURV=new Set();try{SURV=new Set(JSON.parse(localStorage.getItem("ck_surveilles")||"[]"));}catch(e){}
function estSurveille(l){return l.statut==="surveille"||l.statut==="attribution_publiee"||SURV.has(leadId(l));}
function attribParue(l){return l.statut==="attribution_publiee";}
// La marque locale suivait l'ecriture reseau sans jamais verifier qu'elle avait
// abouti : un lead pouvait apparaitre surveille ici et nulle part ailleurs. On
// n'ecrit le marqueur local qu'APRES confirmation, et on le retire si l'appel
// a echoue.
async function surveiller(id){
  const l=LEADS.find(x=>x.id===id);if(!l)return;
  const cle=leadId(l),avant=SURV.has(cle);
  SURV.add(cle);majSurv();
  const ok=await envoyerStatut(l,"surveille","");
  if(!ok&&!avant){SURV.delete(cle);majSurv();go(state.view);}
}
function majSurv(){try{localStorage.setItem("ck_surveilles",JSON.stringify([...SURV]));}catch(e){}}
// Posture d'un theatre. Lit le niveau EFFECTIF calcule cote Python (socle de
// risque + aggravation recente). Repli sur la table constante si POSTURE est
// absent : la page d'un ancien cache ne casse pas, elle redevient statique.
function posture(z){
  const p=(POSTURE&&POSTURE[z])||null;
  const r=p?p.niveau:(RISQUE[z]||1.5);
  const cls=r>=4.5?["p-rouge","Posture rouge"]:r>=3?["p-orange","Posture orange"]:["p-jaune","Posture jaune"];
  return [cls[0],cls[1],p&&p.boost>0?p:null];
}
// Mention affichee quand la posture est REHAUSSEE : dire d'ou vient la hausse,
// jamais presenter un niveau rehausse comme s'il etait le socle.
function postureNote(z){
  const p=posture(z)[2];if(!p)return "";
  const t=(p.pays||"")+(p.motif?" — "+p.motif:"")+(p.date?" ("+p.date+")":"");
  return `<span class="post-up" title="${esc(t)} · socle ${p.base} → ${p.niveau}">▲ ${esc(p.pays||"aggravation")}</span>`;
}
const LEADS=RAW.map((l,i)=>({
  id:i,titre:l.titre||"(sans titre)",src:l.src||"?",zone:l.zone||"Non classé",pays:l.pays||"",
  secteur:l.sect||l.grp||"Autre",score:+l.final||0,prio:l.action||"surveiller",valeur:+l.valeur_meur||0,enveloppe:+l.enveloppe_meur||0,
  acheteur:l.agence||"n.c.",statut:l.statut||"nouveau",motif:l.motif_ecart||l.motif||"",titulaire:l.entreprise||"",pays_tit:l.origine||"",entcle:l.ent_cle||"",
  etranger:!!l.etranger_titulaire,renouv:l.statut_renouv||"",nature:l.nature_deploiement||"",besoin:l.besoin_surete||"",
  interlocuteur:l.interlocuteur||"",cible:l.cible||"",justif:l.justif||"",lien:l.lien||"",secu:!!l.secu,
  type_activite:l.grp||"",resume:l.justif||"",
  mois:l.mois_label||l.mois||"",moiscle:l.mois||"",nom:l.nom||"n.c.",email:l.email||"n.c.",tel:l.tel||"n.c.",win:l.win||"",pub:l.pub||"",
  proj:l.projet_id||"",deadline:l.deadline||"",datedet:l.date_det||"",
  // Rehausse geopolitique (dash.appliquer_boost_geo) : score d'origine
  // conserve pour que l'UI puisse montrer AVANT -> APRES, jamais un chiffre
  // rehausse presente comme brut.
  geoboost:+l.geo_boost||0,finalbase:(l.final_base==null?null:+l.final_base),
  geomotif:l.geo_motif||"",geodate:l.geo_date||"",
  type:(l.src==="ATTRIB"?"attrib":(l.src==="PRIVÉ"?"prive":"avis"))
}));
// Date de detection -> timestamp triable (le "run" ou le lead est apparu).
// Gere ISO (2026-08-17) et FR (17/08/2026) ; repli sur la cle mois. 0 si vide.
function parseDate(s){
  if(!s)return 0;s=String(s).trim();
  let m=s.match(/^(\d{4})-(\d{2})-(\d{2})/);if(m)return Date.UTC(+m[1],+m[2]-1,+m[3]);
  m=s.match(/^(\d{2})\/(\d{2})\/(\d{4})/);if(m)return Date.UTC(+m[3],+m[2]-1,+m[1]);
  const t=Date.parse(s);return isNaN(t)?0:t;
}
LEADS.forEach(l=>{l.ts=parseDate(l.datedet)||parseDate(l.moiscle);});
// Dernier run = date de detection la plus recente presente. Sert au badge
// "nouveau" et au filtre "Nouveautes du dernier run".
const LAST_RUN=LEADS.reduce((m,l)=>Math.max(m,l.ts||0),0);
function estNouveau(l){return LAST_RUN>0&&l.ts>0&&(LAST_RUN-l.ts)<36*3600*1000;}
function relDate(l){
  if(!l.ts){return l.mois&&l.mois!=="Sans date"?esc(l.mois):"n.c.";}
  const j=Math.floor((Date.now()-l.ts)/86400000);
  if(j<=0)return "aujourd'hui";if(j===1)return "hier";if(j<7)return "il y a "+j+" j";
  if(j<30)return "il y a "+Math.floor(j/7)+" sem.";
  const d=new Date(l.ts);return d.getUTCDate()+" "+["janv.","févr.","mars","avr.","mai","juin","juil.","août","sept.","oct.","nov.","déc."][d.getUTCMonth()];
}
// --- ECHEANCE DE L'AVIS ---------------------------------------------------
// La date de cloture est ce qui rend un lead actionnable ou pas : un score de
// 9 sur un avis clos hier ne vaut rien. Elle etait collectee, serialisee, et
// affichee nulle part sur le cockpit.
function joursRestants(s){
  const t=parseDate(s);if(!t)return null;
  const auj=new Date();auj.setHours(0,0,0,0);
  return Math.round((t-auj.getTime())/86400000);
}
function badgeDeadline(l){
  const jr=joursRestants(l.deadline);
  if(jr===null)return "";
  if(jr<0)return '<span class="jx clos" title="Avis clôturé">clôturé</span>';
  if(jr===0)return '<span class="jx urgent">clôt. aujourd\'hui</span>';
  return `<span class="jx ${jr<=7?"urgent":jr<=30?"proche":"large"}" title="Clôture le ${esc(l.deadline)}">J-${jr}</span>`;
}
// --- ETAT DU DERNIER RUN PAR SOURCE ---------------------------------------
// Derive cote Python (dash.sante_run) : aucun calcul ici, on rend. Bandeau
// masque si le calcul est absent -- pas de cadre vide sans information.
function renderSante(){
  const box=document.getElementById("santeRun");if(!box)return;
  if(!SANTE||!SANTE.sources||!SANTE.sources.length){box.innerHTML="";box.classList.remove("alerte");return;}
  const ageTxt=x=>{
    if(x.etat==="absent"||!x.n)return "—";
    if(x.age===null||x.age===undefined)return "n.c.";
    if(x.age===0)return "aujourd'hui";
    if(x.age===1)return "hier";
    return "il y a "+x.age+" j";
  };
  const chips=SANTE.sources.map(x=>`<span class="sc ${esc(x.etat)}" title="${esc(x.src)} · ${x.n} lead(s) · plus récent : ${esc(ageTxt(x))}"><span class="dot"></span><span class="src">${esc(x.src)}</span><span>${x.n}</span><span class="ag">${esc(ageTxt(x))}</span></span>`).join("");
  const av=SANTE.a_verifier>0?`<span class="warn">${SANTE.a_verifier} à vérifier</span>`:"toutes actives";
  box.classList.toggle("alerte",SANTE.a_verifier>0);
  box.innerHTML=`<div class="sante-tete"><span class="sante-titre">État du dernier run</span><span class="sante-sub">${esc(SANTE.date||"")} · ${SANTE.actives} source(s) active(s) · ${av}</span></div><div class="sante-grid">${chips}</div>`;
}
const SECT_COLORS={"Génie civil / BTP":"#8E2649","Eau / assainissement":"#33628F","Énergie":"#B07419","Santé":"#237A57","Sécurité / défense":"#C0392B","Logistique / transport":"#6B5B95","Extractif / mines":"#7A5230","Télécom / IT":"#3A8FA8"};
// SOURCE DE VERITE des couleurs de priorite (marqueurs carte + KPI). Les
// classes .pill du CSS en sont le miroir exact : une priorite ne doit pas
// etre verte dans un tableau et rouge sur la carte, comme c'etait le cas.
const PRIO_COLOR={contacter:"#8E2649",surveiller:"#B07419",ignorer:"#8B93A2"};
const PRIO_LBL={contacter:"À contacter",surveiller:"À surveiller",ignorer:"À écarter"};
// Les collecteurs stockent des enums (« court_terme », « fort »). Elles
// fuyaient telles quelles dans le tiroir : on les traduit a l'affichage, sans
// jamais toucher a la donnee stockee.
const WIN_LBL={immediate:"Immédiate",court_terme:"Court terme",moyen_terme:"Moyen terme",indetermine:"Indéterminée"};
const BESOIN_LBL={fort:"Fort",moyen:"Moyen",faible:"Faible",inconnu:"Inconnu"};
// ISSUES COMMERCIALES (P1.1) -- ce qui manquait pour que le radar apprenne.
// L'interface ne pouvait emettre que contacte / surveille / non_pertinent :
// la boucle bayesienne apprenait donc a predire si un humain avait clique,
// pas si Amarante avait gagne.
const TOAST_STATUT={contacte:"Marqué à contacter",surveille:"Ajouté à la surveillance",
  non_pertinent:"Marché écarté",gagne:"🏆 Marché gagné, enregistré",perdu:"Marché perdu, enregistré"};
// Motifs de perte : liste FERMEE, miroir exact de radar_stockage.MOTIFS_PERTE.
// Un champ libre produit vingt formulations de la meme raison et zero
// statistique exploitable. C'est la difference entre une note et une donnee.
const MOTIFS_PERTE={prix:"Prix trop élevé",incumbent:"Titulaire en place reconduit",
  hors_perimetre:"Hors périmètre Amarante",pas_de_reponse:"Aucune réponse du prospect",
  projet_annule:"Projet annulé ou reporté",concurrent:"Perdu face à un concurrent",autre:"Autre"};
// Une issue suppose que le lead a ete TRAVAILLE : on ne perd pas ce qu'on n'a
// jamais approche. Les boutons n'apparaissent donc qu'a partir de ces etats.
const ETATS_TRAVAILLES=["contacte","surveille","attribution_publiee"];
function estTravaille(l){return ETATS_TRAVAILLES.includes(l.statut);}
function estClos(l){return l.statut==="gagne"||l.statut==="perdu";}
const fmtEur=v=>!v?"n.c.":v>=1?v.toFixed(v<10?1:0)+" M€":(v*1000).toFixed(0)+" k€";
function cellMontant(l){
  if(l.valeur>0)return `<span class="t-val">${fmtEur(l.valeur)}</span>`;
  if(l.enveloppe>0)return `<span class="t-env" title="Enveloppe projet (coût total), pas un montant de marché">env. ${fmtEur(l.enveloppe)}</span>`;
  return `<span class="t-nc">n.c.</span>`;
}
const scoreColor=s=>s>=8?"#237A57":s>=6?"#B07419":s>=4?"#33628F":"#8B93A2";
// TROIS ECHELLES DE SCORE, JAMAIS INTERCHANGEABLES (feuille de route, pt 3).
// Elles sortent de moteurs differents et ne se comparent pas entre elles :
//   avis   -> analyse LLM (sûreté x commercial), ted.calculer_scores ;
//   privé  -> intensité du signal de déploiement, scoring signaux privés ;
//   attrib -> formule DETERMINISTE zone + secteur + valeur, sans LLM.
// L'en-tete affichait la formule des attributions pour TOUTES les lignes.
const ECHELLE={
  avis:["avis","Analyse sûreté × potentiel commercial du marché (modèle). Ne se compare pas au score d'une attribution."],
  prive:["signal","Intensité du signal de déploiement détecté (offre d'emploi, presse). Ne se compare pas au score d'un avis de marché."],
  attrib:["titulaire","Calcul déterministe : risque de la zone + secteur + valeur du marché. Indicatif, ce n'est PAS une analyse sûreté."]};
function celluleScore(l){
  const e=ECHELLE[l.type]||ECHELLE.avis;
  const base=l.geoboost&&l.finalbase!=null
    ? `<span class="sfbase" title="Score avant rehausse géopolitique">${l.finalbase.toFixed(1)}</span>`:"";
  return `${base}<span class="t-score" style="color:${scoreColor(l.score)}" title="${esc(e[1])}">${l.score.toFixed(1)}</span><span class="ech" title="${esc(e[1])}">${e[0]}</span>`;
}
const actifs=()=>LEADS.filter(l=>l.statut!=="écarté"&&l.statut!=="non_pertinent"&&l.statut!=="perdu");
// PIPELINE vs MARCHE OBSERVE (25/08/2026) -- distinction de doctrine.
// Une ATTRIBUTION est un marche DEJA GAGNE par un tiers. Elle est precieuse
// (le titulaire est un prospect a demarcher) mais elle n'est PAS du pipeline :
// l'additionner aux avis gonflait la « Valeur du pipeline » avec des contrats
// que personne ici ne peut plus remporter, et ecrasait les proportions des
// graphes. On separe donc les deux populations a la SOURCE, une bonne fois :
//   opps()    = ce qui reste a saisir (avis + signaux prives) ;
//   attribs() = ce qui est deja attribue (registre de prospection).
// Aucune donnee n'est perdue : les attributions gardent leurs propres KPI
// (onglet dedie) et restent comptees a part sur les tuiles de theatre.
const opps=()=>actifs().filter(l=>l.src!=="ATTRIB");
const attribs=()=>actifs().filter(l=>l.src==="ATTRIB");
let state={view:"overview",zone:"",sect:"",src:"",type:"",prio:"traiter",q:"",surv:false,neuf:false,sort:"score",dir:-1};

function renderTheatres(){
  const byZone={};opps().forEach(l=>{(byZone[l.zone]=byZone[l.zone]||[]).push(l);});
  // Attributions comptees SEPAREMENT : elles disent l'activite du theatre sans
  // se faire passer pour des marches encore ouverts.
  const atZone={};attribs().forEach(l=>{atZone[l.zone]=(atZone[l.zone]||0)+1;});
  Object.keys(atZone).forEach(z=>{if(!byZone[z])byZone[z]=[];});
  const zones=Object.keys(byZone).sort((a,b)=>(byZone[b].length+(atZone[b]||0))-(byZone[a].length+(atZone[a]||0))).slice(0,6);
  document.getElementById("theatres").innerHTML=zones.map(z=>{
    const it=byZone[z];const val=it.reduce((s,l)=>s+l.valeur,0);const hot=it.filter(l=>l.prio==="contacter").length;const p=posture(z);
    const nat=atZone[z]||0;
    return `<div class="th" onclick="goZone('${z.replace(/'/g,"\\'")}')"><div class="bar ${p[0]}"></div><div class="zone">${z}</div><div class="post ${p[0]}">${p[1]}${postureNote(z)}</div><div class="big">${it.length}<small> à saisir</small></div><div class="val">${val?fmtEur(val)+" · ":""}${hot} à contacter${nat?' · <span title="Marchés déjà attribués : prospects, pas du pipeline">'+nat+' attrib.</span>':""}</div></div>`;
  }).join("")||'<div class="empty">Aucun marché en zone couverte.</div>';
}
function renderKPIs(){
  const act=opps();const contacter=act.filter(l=>l.prio==="contacter").length;
  // Valeur du PIPELINE : avis + signaux prives UNIQUEMENT. Les attributions
  // ont leur propre ligne (« marché observé ») : deux echelles, deux lectures.
  const valeur=act.reduce((s,l)=>s+l.valeur,0);
  const at=attribs();const valAttrib=at.reduce((s,l)=>s+l.valeur,0);
  const etr=at.filter(l=>l.etranger).length;
  const renouv=LEADS.filter(l=>l.renouv).length;
  const cards=[
    {lbl:"À contacter",val:contacter,sub:"avis et signaux, non traités",ico:'<path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/>',c:"var(--amarante)",cs:"var(--amarante-soft)"},
    {lbl:"Valeur du pipeline",val:fmtEur(valeur),sub:"marchés encore à saisir",ico:'<path d="M12 1v22M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6"/>',c:"var(--green)",cs:"var(--green-soft)",
     note:valAttrib?"hors "+fmtEur(valAttrib)+" déjà attribués":""},
    {lbl:"Titulaires étrangers",val:etr,sub:"déploiements à démarcher",ico:'<circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15 15 0 010 20 15 15 0 010-20"/>',c:"var(--blue)",cs:"var(--blue-soft)"},
    {lbl:"Renouvellements",val:renouv,sub:"contrats à échéance suivie",ico:'<path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.5 9a9 9 0 0114.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0020.5 15"/>',c:"var(--amber)",cs:"var(--amber-soft)"}
  ];
  document.getElementById("kpis").innerHTML=cards.map(k=>`<div class="kpi"><div class="k-top"><span class="k-lbl">${k.lbl}</span><span class="k-ico" style="background:${k.cs}"><svg viewBox="0 0 24 24" fill="none" stroke="${k.c}">${k.ico}</svg></span></div><div class="k-val">${k.val}</div><div class="k-sub">${k.sub}${k.note?'<br><span class="k-note" title="Marchés déjà gagnés par des tiers : registre de prospection, pas du pipeline">'+k.note+'</span>':""}</div></div>`).join("");
}
let charts={};
function renderCharts(){
  Object.values(charts).forEach(c=>c&&c.destroy());
  Chart.defaults.font.family="'Inter',sans-serif";Chart.defaults.font.size=11;Chart.defaults.color="#586173";
  const act=opps();
  const byZone={};act.forEach(l=>{(byZone[l.zone]=byZone[l.zone]||[]).push(l);});
  const zones=Object.keys(byZone).sort((a,b)=>byZone[b].length-byZone[a].length).slice(0,7);
  charts.zone=new Chart(document.getElementById("c-zone"),{type:"bar",data:{labels:zones,datasets:[
    {label:"Marchés à saisir",data:zones.map(z=>byZone[z].length),backgroundColor:"#8E2649",borderRadius:5,barPercentage:.62},
    {label:"Valeur M€",data:zones.map(z=>byZone[z].reduce((s,l)=>s+l.valeur,0)),backgroundColor:"#E5C4CF",borderRadius:5,barPercentage:.62,yAxisID:"y1"}
  ]},options:{maintainAspectRatio:false,plugins:{legend:{position:"bottom",labels:{boxWidth:10,boxHeight:10,padding:14,usePointStyle:true}}},scales:{y:{grid:{color:"#EEF1F5"},title:{display:true,text:"à saisir"}},y1:{position:"right",grid:{display:false},title:{display:true,text:"M€"}},x:{grid:{display:false}}}}});
  const sects={};act.forEach(l=>sects[l.secteur]=(sects[l.secteur]||0)+1);
  const sl=Object.keys(sects).sort((a,b)=>sects[b]-sects[a]).slice(0,8);
  charts.sect=new Chart(document.getElementById("c-sect"),{type:"doughnut",data:{labels:sl,datasets:[{data:sl.map(s=>sects[s]),backgroundColor:sl.map((s,i)=>SECT_COLORS[s]||["#8E2649","#33628F","#B07419","#237A57","#C0392B","#6B5B95","#7A5230","#3A8FA8"][i%8]),borderWidth:2,borderColor:"#fff"}]},options:{maintainAspectRatio:false,cutout:"62%",plugins:{legend:{position:"right",labels:{boxWidth:9,boxHeight:9,padding:8,usePointStyle:true,font:{size:10}}}}}});
  const mm={};LEADS.forEach(l=>{if(l.mois){const k=l.mois;mm[k]=mm[k]||{a:0,at:0};l.src==="ATTRIB"?mm[k].at++:mm[k].a++;}});
  const mk=Object.keys(mm).sort();
  charts.time=new Chart(document.getElementById("c-time"),{type:"line",data:{labels:mk,datasets:[
    {label:"Avis",data:mk.map(k=>mm[k].a),borderColor:"#8E2649",backgroundColor:"rgba(142,38,73,.08)",fill:true,tension:.35,borderWidth:2.5,pointRadius:3,pointBackgroundColor:"#8E2649"},
    {label:"Attributions",data:mk.map(k=>mm[k].at),borderColor:"#33628F",backgroundColor:"rgba(51,98,143,.06)",fill:true,tension:.35,borderWidth:2.5,pointRadius:3,pointBackgroundColor:"#33628F"}
  ]},options:{maintainAspectRatio:false,plugins:{legend:{position:"bottom",labels:{boxWidth:10,boxHeight:10,padding:14,usePointStyle:true}}},scales:{y:{grid:{color:"#EEF1F5"},ticks:{precision:0}},x:{grid:{display:false}}}}});
}
function renderFunnel(){
  // NUANCE ASSUMEE : contrairement aux KPI de valeur, le funnel GARDE les
  // attributions. Un titulaire se demarche, se marque « contacté », se gagne
  // ou se perd : c'est du vrai travail de prospection, l'exclure amputerait
  // les etapes 2 a 4. On affiche donc la COMPOSITION de l'etape 1 plutot que
  // de masquer une population.
  const traite=l=>l.statut&&l.statut!=="nouveau";
  const nOpp=LEADS.filter(l=>l.src!=="ATTRIB").length;
  const nAt=LEADS.length-nOpp;
  const steps=[
    {l:"Signaux détectés",n:LEADS.length,c:"#33628F",
     d:nAt?nOpp+" à saisir + "+nAt+" attribué"+(nAt>1?"s":""):""},
    {l:"À contacter",n:LEADS.filter(l=>l.prio==="contacter").length,c:"#8E2649"},
    {l:"En traitement",n:LEADS.filter(l=>traite(l)&&!estClos(l)&&l.statut!=="écarté"&&l.statut!=="non_pertinent").length,c:"#B07419"},
    // Le vocabulaire de la base est « gagne » / « perdu » SANS accent : ce
    // filtre cherchait « gagné », il ne pouvait donc jamais rien compter --
    // second etage du meme defaut que P1.1.
    {l:"Gagné",n:LEADS.filter(l=>l.statut==="gagne").length,c:"#237A57"}
  ];
  const max=Math.max(steps[0].n,1);
  document.getElementById("funnel").innerHTML=steps.map(s=>`<div class="fn-row"><div class="fn-lbl">${s.l}${s.d?`<div class="fn-d">${s.d}</div>`:""}</div><div class="fn-track"><div class="fn-fill" style="width:${Math.max(6,s.n/max*100)}%;background:${s.c}">${s.n}</div></div><div class="fn-n">${(s.n/max*100).toFixed(0)}%</div></div>`).join("");
}
function renderHot(){
  const hot=LEADS.filter(l=>l.prio==="contacter"&&(l.statut==="nouveau"||!l.statut)).sort((a,b)=>b.score-a.score).slice(0,6);
  document.getElementById("hot").innerHTML=hot.map(l=>`<div class="hot-row" onclick="openDrawer(${l.id})"><div class="score-badge" style="background:${scoreColor(l.score)}">${l.score.toFixed(1)}</div><div class="hot-mid"><div class="hot-title">${esc(l.titre)}</div><div class="hot-meta">${l.zone} · ${l.pays} · ${l.secteur} · ${l.src}</div></div><div class="hot-val">${fmtEur(l.valeur)}</div></div>`).join("")||'<div class="empty">Aucun lead à contacter.</div>';
}
function filtered(){
  return LEADS.filter(l=>{
    if(l.src==="ATTRIB")return false;                    // attributions -> onglet dédié
    if(state.neuf&&!estNouveau(l))return false;          // nouveautés du dernier run
    if(state.surv&&!estSurveille(l))return false;
    if(state.zone&&l.zone!==state.zone)return false;
    if(state.sect&&l.secteur!==state.sect)return false;
    if(state.src&&l.src!==state.src)return false;
    if(state.type&&l.type!==state.type)return false;
    if(state.prio==="traiter"){if(l.prio!=="contacter"&&l.prio!=="surveiller")return false;}
    else if(state.prio&&l.prio!==state.prio)return false;
    // Masque les leads déjà écartés/perdus, sauf en vue "Tout" (prio vide).
    if((l.statut==="écarté"||l.statut==="non_pertinent"||l.statut==="perdu")&&state.prio!=="")return false;
    if(state.q){const q=state.q.toLowerCase();if(!((l.titre+l.pays+l.zone+l.secteur+l.acheteur+l.titulaire).toLowerCase().includes(q)))return false;}
    return true;
  }).sort((a,b)=>{let va=a[state.sort],vb=b[state.sort];if(typeof va==="string"){va=va.toLowerCase();vb=(vb||"").toLowerCase();return va<vb?-state.dir:va>vb?state.dir:0;}return((va||0)-(vb||0))*state.dir;});
}
function esc(s){return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}
const DOSS_MULTI=new Set((DOSSIERS||[]).filter(d=>d.n_phases>=2).map(d=>d.proj_id));
function renderTable(){
  const rows=filtered();
  document.getElementById("tbody").innerHTML=rows.map(l=>{
    const b=[];
    const dl=badgeDeadline(l);if(dl)b.push(dl);
    if(l.geoboost)b.push('<span class="mb geo" title="'+esc(l.geomotif||"Pays en aggravation récente")+' — score rehaussé de +'+(l.geoboost*0.5).toFixed(1)+'">▲ pays en aggravation</span>');
    if(estNouveau(l))b.push('<span class="mb neuf">✦ nouveau</span>');
    if(attribParue(l))b.push('<span class="mb attribp">🎯 attribution parue</span>');else if(estSurveille(l))b.push('<span class="mb surv">👁 surveillé</span>');
    if(l.renouv==="imminent")b.push('<span class="mb renouv">renouv. imminent</span>');else if(l.renouv==="a_venir")b.push('<span class="mb renouv">renouv. à venir</span>');
    if(l.etranger)b.push('<span class="mb etr">titulaire étranger</span>');
    if(l.secu)b.push('<span class="mb secu">sûreté en place</span>');
    if(l.proj&&DOSS_MULTI.has(l.proj))b.push('<span class="mb proj" title="Projet suivi sur plusieurs phases" onclick="event.stopPropagation();ouvrirDossier(\''+esc(l.proj)+'\')">📁 dossier '+esc(l.proj)+'</span>');
    const typeLbl=l.type==="prive"?"signal privé":"avis";
    return `<tr onclick="openDrawer(${l.id})">`
      +`<td><div class="t-title">${esc(l.titre)}</div><div class="t-sub"><span class="tag-src ${l.src}">${l.src}</span> · ${typeLbl} · ${esc(l.acheteur)}</div>${b.length?`<div class="mini-badges">${b.join("")}</div>`:""}</td>`
      +`<td class="t-date">${relDate(l)}</td>`
      +`<td>${l.zone}<div class="t-sub">${l.pays}</div></td>`
      +`<td>${l.secteur}</td>`
      +`<td>${cellMontant(l)}</td>`
      +`<td>${celluleScore(l)}</td>`
      +`<td><span class="pill ${l.prio}">${PRIO_LBL[l.prio]||l.prio}</span></td></tr>`;
  }).join("")||'<tr><td colspan="7" class="empty">Aucun marché ne correspond à ces filtres. Essaie « Tout » ou réinitialise.</td></tr>';
  const val=rows.reduce((s,l)=>s+l.valeur,0);
  const nNeuf=rows.filter(estNouveau).length;
  document.getElementById("tbl-count").textContent=rows.length+" marché"+(rows.length>1?"s":"")
    +(nNeuf?" · "+nNeuf+" nouveau"+(nNeuf>1?"x":"")+" ce run":"")
    +(val?" · "+fmtEur(val)+" de montants chiffrés":"");
}
function ouvrirDossier(pid){
  go("doss");
  setTimeout(()=>{const el=document.getElementById("doss-"+pid);if(el){el.scrollIntoView({behavior:"smooth",block:"center"});el.classList.add("doss-hl");setTimeout(()=>el.classList.remove("doss-hl"),1900);}},90);
}
// --- ATTRIBUTIONS : registre des titulaires, filtrable ---------------------
// L'onglet n'avait AUCUNE facette alors que les Opportunites en ont cinq : sur
// 40 pays et une douzaine de sources, la liste etait a peine exploitable. Les
// KPI se calculent sur la selection COURANTE (avec le total rappele a cote),
// sinon un chiffre filtre et un chiffre global cohabiteraient sans le dire.
let attribState={zone:"",sect:"",orig:"",etr:"",sort:"ts",dir:-1};
function attribFiltres(){
  return LEADS.filter(l=>{
    if(l.src!=="ATTRIB")return false;
    if(attribState.zone&&l.zone!==attribState.zone)return false;
    if(attribState.sect&&l.secteur!==attribState.sect)return false;
    if(attribState.orig&&l.pays_tit!==attribState.orig)return false;
    if(attribState.etr==="1"&&!l.etranger)return false;
    if(attribState.etr==="renouv"&&!l.renouv)return false;
    if(state.q){const q=state.q.toLowerCase();
      if(!((l.titulaire+l.titre+l.acheteur+l.pays+l.pays_tit).toLowerCase().includes(q)))return false;}
    return true;
  }).sort((a,b)=>{
    let va=a[attribState.sort],vb=b[attribState.sort];
    if(typeof va==="string"){va=va.toLowerCase();vb=String(vb||"").toLowerCase();
      return va<vb?-attribState.dir:va>vb?attribState.dir:0;}
    return (((va||0)-(vb||0))*attribState.dir)||(b.score-a.score);
  });
}
function renderAttrib(){
  const tous=LEADS.filter(l=>l.src==="ATTRIB");
  const at=attribFiltres();
  // Recurrence calculee sur TOUT le corpus : « 4 marchés gagnés » doit rester
  // vrai meme quand un filtre n'en montre qu'un seul.
  const cnt={};tous.forEach(l=>{const k=l.entcle||(l.titulaire||"").toLowerCase();if(k)cnt[k]=(cnt[k]||0)+1;});
  const kp=[{lbl:"Attributions",val:at.length,c:"var(--amarante)",sub:at.length!==tous.length?"sur "+tous.length+" au total":"registre complet"},
            {lbl:"Titulaires étrangers",val:at.filter(l=>l.etranger).length,c:"var(--blue)",sub:"déploiements à démarcher"},
            {lbl:"Renouvellements",val:at.filter(l=>l.renouv).length,c:"var(--amber)",sub:"contrats à échéance suivie"},
            {lbl:"Montant cumulé",val:fmtEur(at.reduce((s,l)=>s+l.valeur,0)),c:"var(--green)",sub:"marchés déjà attribués"}];
  document.getElementById("kpis-attrib").innerHTML=kp.map(k=>`<div class="kpi"><div class="k-lbl" style="margin-bottom:8px">${k.lbl}</div><div class="k-val" style="color:${k.c}">${k.val}</div><div class="k-sub">${k.sub}</div></div>`).join("");
  document.getElementById("tbody-attrib").innerHTML=at.map(l=>{
    const n=cnt[l.entcle||(l.titulaire||"").toLowerCase()]||0;
    const inc=n>=2?` <span class="mb inc" title="Titulaire récurrent en zone à risque">⚔ ${n} gagnés</span>`:"";
    const neuf=estNouveau(l)?'<span class="mb neuf">✦ nouveau</span> ':'';
    // « attribué » n'est pas une priorite : pastille NEUTRE. Elle etait rendue
    // avec la classe « contacter », qui porte la couleur d'un lead a traiter.
    const st=l.renouv==="imminent"?'<span class="mb renouv">renouv. imminent</span>':l.renouv==="a_venir"?'<span class="mb renouv">renouv. à venir</span>':'<span class="pill neutre">attribué</span>';
    return `<tr onclick="openDrawer(${l.id})"><td><strong>${esc(l.titulaire||"—")}</strong>${inc}</td><td><div class="t-title" style="max-width:240px">${esc(l.titre)}</div></td><td class="t-date">${relDate(l)}</td><td>${l.zone}<div class="t-sub">${l.pays}</div></td><td>${l.pays_tit?`<span class="tag-orig">${esc(l.pays_tit)}</span>${l.etranger?' <span class="flag">étr.</span>':""}`:'<span class="t-sub">origine n.c.</span>'}</td><td class="t-val">${fmtEur(l.valeur)}</td><td>${neuf}${st}</td></tr>`;
  }).join("")||`<tr><td colspan="7" class="empty">${tous.length?"Aucune attribution ne correspond à ces filtres.":"Aucune attribution collectée pour l'instant."}</td></tr>`;
  const sansOrig=at.filter(l=>!l.pays_tit).length;
  document.getElementById("attrib-count").textContent=at.length+" attribution"+(at.length>1?"s":"")
    +(sansOrig?" · "+sansOrig+" sans origine identifiée":"");
}
function resetAttrib(){
  attribState.zone=attribState.sect=attribState.orig=attribState.etr="";
  ["af-zone","af-sect","af-orig"].forEach(i=>{const el=document.getElementById(i);if(el)el.value="";});
  document.querySelectorAll("#af-etr button").forEach((x,i)=>x.classList.toggle("on",i===0));
  renderAttrib();
}
function initAttribFiltres(){
  const at=LEADS.filter(l=>l.src==="ATTRIB");
  const remplir=(id,vals)=>{const el=document.getElementById(id);if(!el)return;
    [...new Set(vals.filter(Boolean))].sort().forEach(v=>el.innerHTML+=`<option>${esc(v)}</option>`);};
  remplir("af-zone",at.map(l=>l.zone));
  remplir("af-sect",at.map(l=>l.secteur));
  remplir("af-orig",at.map(l=>l.pays_tit));
  const on=(id,champ)=>{const el=document.getElementById(id);
    if(el)el.onchange=e=>{attribState[champ]=e.target.value;renderAttrib();};};
  on("af-zone","zone");on("af-sect","sect");on("af-orig","orig");
  document.querySelectorAll("#af-etr button").forEach(b=>b.onclick=()=>{
    document.querySelectorAll("#af-etr button").forEach(x=>x.classList.remove("on"));
    b.classList.add("on");attribState.etr=b.dataset.e;renderAttrib();});
  document.querySelectorAll("thead th[data-asort]").forEach(th=>th.onclick=()=>{
    const k=th.dataset.asort;attribState.dir=(attribState.sort===k)?-attribState.dir:-1;
    attribState.sort=k;renderAttrib();});
}
let map,markers=[];
function initMap(){
  if(map)return;
  map=L.map("map",{worldCopyJump:true,minZoom:2,attributionControl:true}).setView([18,18],2.3);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",{maxZoom:18,attribution:'&copy; OpenStreetMap &copy; CARTO'}).addTo(map);
  const zones=[...new Set(LEADS.map(l=>l.zone))].sort();
  document.getElementById("map-zones").innerHTML=zones.map(z=>`<label class="chk"><input type="checkbox" checked data-mzone="${esc(z)}" onchange="drawMarkers()">${z}</label>`).join("");
  document.getElementById("map-srcs").innerHTML=[...new Set(LEADS.map(l=>l.src))].sort().map(s=>`<label class="chk"><input type="checkbox" checked data-msrc="${s}" onchange="drawMarkers()">${s}</label>`).join("");
  drawMarkers();
}
function drawMarkers(){
  markers.forEach(m=>map.removeLayer(m));markers=[];
  const zOn=[...document.querySelectorAll("[data-mzone]:checked")].map(c=>c.dataset.mzone);
  const sOn=[...document.querySelectorAll("[data-msrc]:checked")].map(c=>c.dataset.msrc);
  const byPays={};
  LEADS.filter(l=>zOn.includes(l.zone)&&sOn.includes(l.src)&&COORDS[l.pays]).forEach(l=>{(byPays[l.pays]=byPays[l.pays]||[]).push(l);});
  Object.keys(byPays).forEach(pays=>{
    const it=byPays[pays];const top=it.slice().sort((a,b)=>b.score-a.score)[0];
    const r=Math.min(7+it.length*1.5,22);
    const m=L.circleMarker(COORDS[pays],{radius:r,fillColor:PRIO_COLOR[top.prio],color:"#fff",weight:2,fillOpacity:.8});
    m.bindPopup(`<div class="pop-t">${pays} · ${it.length} marché${it.length>1?"s":""}</div><div class="pop-m">${it.slice(0,4).map(l=>`${l.score.toFixed(1)} · ${esc(l.titre).slice(0,42)}`).join("<br>")}${it.length>4?"<br>…":""}</div>`);
    m.addTo(map);markers.push(m);
  });
}
function openDrawer(id){
  const l=LEADS.find(x=>x.id===id);if(!l)return;
  const natL={expatrie_significatif:"Expatrié significatif",mixte:"Encadrement international, main-d'œuvre locale",local_uniquement:"Personnel local",aucun_deploiement:"Aucun déploiement"}[l.nature]||(l.nature||"non analysé");
  const besL=BESOIN_LBL[l.besoin]||l.besoin||"";
  const contact=(l.email!=="n.c."||l.nom!=="n.c.")?`<div class="dr-sec"><h5>Contact enrichi</h5><div class="dr-grid"><div class="dr-field"><div class="l">Nom</div><div class="v">${esc(l.nom)}</div></div><div class="dr-field"><div class="l">Email</div><div class="v" style="font-size:12px">${esc(l.email)}</div></div></div></div>`:"";
  document.getElementById("drawer-content").innerHTML=`
  <div class="dr-head"><button class="dr-close" onclick="closeDrawer()"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg></button>
    <div class="dr-src">${l.src} · ${l.zone}</div><h3>${esc(l.titre)}</h3>
    <div style="display:flex;gap:8px;align-items:center"><span class="score-badge" style="width:34px;height:34px;font-size:14px;background:${scoreColor(l.score)}">${l.score.toFixed(1)}</span><span class="pill ${l.prio}">${PRIO_LBL[l.prio]||l.prio}</span><span class="t-val" style="margin-left:auto;font-size:15px">${fmtEur(l.valeur)}</span></div></div>
  <div class="dr-body">
    <div class="dr-sec"><h5>Marché</h5><div class="dr-grid"><div class="dr-field"><div class="l">Pays</div><div class="v">${l.pays||"—"}</div></div><div class="dr-field"><div class="l">Secteur</div><div class="v">${l.secteur}</div></div><div class="dr-field"><div class="l">Acheteur / bailleur</div><div class="v" style="font-size:13px">${esc(l.acheteur)}</div></div><div class="dr-field"><div class="l">Fenêtre</div><div class="v">${WIN_LBL[l.win]||l.win||"—"}</div></div>${l.deadline?`<div class="dr-field"><div class="l">Clôture</div><div class="v">${esc(l.deadline)} ${badgeDeadline(l)}</div></div>`:""}${l.geoboost?`<div class="dr-field"><div class="l">Contexte géo</div><div class="v" style="font-size:12px;color:var(--red)">▲ aggravation ${esc(l.geodate||"")} · score ${l.finalbase!=null?l.finalbase.toFixed(1):"?"} → ${l.score.toFixed(1)}</div></div>`:""}${l.enveloppe>0?`<div class="dr-field"><div class="l">Enveloppe projet</div><div class="v" style="color:var(--blue)">${fmtEur(l.enveloppe)} <span style="font-size:10px;color:var(--ink-3)">(coût total, pas le marché)</span></div></div>`:""}</div></div>
    ${l.titulaire||l.pays_tit?`<div class="dr-sec"><h5>Titulaire</h5><div class="dr-grid"><div class="dr-field"><div class="l">Entreprise</div><div class="v">${esc(l.titulaire||"—")}</div></div><div class="dr-field"><div class="l">Origine</div><div class="v">${l.pays_tit||"—"} ${l.etranger?'<span class="flag">étranger</span>':""}</div></div></div></div>`:""}
    ${(function(){const c=candidatsPour(l);return c.length?`<div class="dr-sec"><h5>Candidats probables${l.src==="ATTRIB"?" (autres du secteur)":""}</h5><div class="cand-list">${c.map(x=>`<div class="cand" onclick="rechercherEnt('${(x.entreprise||"").replace(/'/g,"\\'")}')"><div class="cand-n">${esc(x.entreprise)}</div><div class="cand-m">${x.nb} marché${x.nb>1?"s":""} similaire${x.nb>1?"s":""}${x.origine?" · "+esc(x.origine):""}${x.etranger?' <span class="cand-etr">étranger</span>':""}</div></div>`).join("")}</div><div class="cand-note">Inféré depuis l'historique des attributions du même secteur / théâtre.</div></div>`:"";})()}
    <div class="dr-sec"><h5>Cible commerciale</h5><div class="dr-analyse">${esc(l.cible)||"—"}${l.interlocuteur?"<br><strong>Interlocuteur :</strong> "+esc(l.interlocuteur):""}${l.besoin?"<br><strong>Besoin de sûreté :</strong> "+esc(besL):""}${l.nature?"<br><strong>Déploiement :</strong> "+natL:""}</div></div>
    ${l.justif?`<div class="dr-sec"><h5>Analyse</h5><div class="dr-analyse">${esc(l.justif)}</div></div>`:""}
    ${contact}
    ${estSurveille(l)?`<div class="dr-sec"><h5>Surveillance</h5><div class="dr-analyse">${attribParue(l)?'<strong style="color:var(--green)">🎯 Attribution parue au dernier run.</strong>'+(l.motif?"<br>Titulaire détecté : <strong>"+esc(l.motif)+"</strong>":""):"👁 Ce marché est surveillé. Chaque run vérifie s\'il a été attribué (et par qui) ou s\'il évolue."}</div></div>`:""}
    <div class="dr-sec"><h5>Action</h5>${SUIVI_ON?blocActions(l):'<div style="font-size:12px;color:var(--ink-3);font-family:var(--mono)">Suivi non configuré sur cette page (lecture seule).</div>'}${l.lien?`<div style="margin-top:10px"><a class="btn" style="width:100%;justify-content:center" href="${l.lien}" target="_blank">Ouvrir l'avis source</a></div>`:""}</div>
  </div>`;
  document.getElementById("drawer").classList.add("on");document.getElementById("drawer-ov").classList.add("on");
}
// Les boutons dependent de l'ETAT du lead : on ne propose « gagné » que sur un
// lead deja travaille, et on ne repropose rien sur un lead clos. Afficher les
// six boutons en permanence inviterait a marquer perdu un lead jamais contacte,
// ce que l'API refuse de toute facon (409).
function blocActions(l){
  if(estClos(l)){
    const lbl=l.statut==="gagne"?"🏆 Marché gagné":"Marché perdu";
    const m=l.motif&&MOTIFS_PERTE[l.motif]?" · "+esc(MOTIFS_PERTE[l.motif]):"";
    return `<div class="issue-close ${l.statut}">${lbl}${m}<div class="issue-sub">Dossier clos. Rouvre-le en le remettant à contacter.</div></div>
      <div class="dr-actions" style="margin-top:10px"><button class="btn" onclick="marquerContacte(${l.id})">Rouvrir (à contacter)</button></div>`;
  }
  const issue=estTravaille(l)?`<div class="dr-actions" style="margin-top:10px">
      <button class="btn issue-g" onclick="marquerGagne(${l.id})">🏆 Gagné</button>
      <button class="btn issue-p" onclick="marquerPerdu(${l.id})">Perdu</button></div>
    <div class="issue-note">Enregistrer l'issue est ce qui permet au radar d'apprendre ce qu'Amarante gagne réellement.</div>`
    :`<div class="issue-note">L'issue (gagné / perdu) se renseigne une fois le marché contacté ou surveillé.</div>`;
  return `<div class="dr-actions">
      <button class="btn pri" onclick="marquerContacte(${l.id})">${l.statut==="contacte"?"Contacté ✓":"À contacter"}</button>
      <button class="btn${estSurveille(l)?' on-watch':''}" onclick="surveiller(${l.id})">${estSurveille(l)?"Surveillé ✓":"Surveiller"}</button>
      <button class="btn" onclick="ecarter(${l.id})">Écarter</button></div>${issue}`;
}
function closeDrawer(){document.getElementById("drawer").classList.remove("on");document.getElementById("drawer-ov").classList.remove("on");}
function ecarter(id){const l=LEADS.find(x=>x.id===id);if(!l)return;const motif=prompt("Motif pour écarter ce marché (facultatif) :","")||"";envoyerStatut(l,"non_pertinent",motif);}
// --- ISSUES COMMERCIALES (P1.1) -------------------------------------------
// Le verrou de tout le reste : sans « gagné » / « perdu » enregistrables,
// aucune boucle d'apprentissage ne peut se calibrer sur la realite d'Amarante.
function marquerContacte(id){
  const l=LEADS.find(x=>x.id===id);if(!l)return;
  // La valeur estimee se saisit ICI, au moment ou on la connait. La demander
  // au moment de gagner serait trop tard : on la reconstruirait de memoire.
  const brut=prompt("Valeur estimée du marché en k€ (facultatif, laisse vide si inconnue) :",
    l.valeur?Math.round(l.valeur*1000):"");
  if(brut===null)return;                       // annulation : on ne fait rien
  const v=parseValeurK(brut);
  if(brut.trim()&&v===null){toast("Montant illisible : saisis un nombre en k€.",true);return;}
  envoyerStatut(l,"contacte","",v);
}
// Accepte « 250 », « 250k », « 1 200 », « 1.2 » -> renvoie des MILLIONS d'euros
// (l'unite du reste de l'application). null si illisible.
function parseValeurK(brut){
  const t=String(brut||"").replace(/[\s\u00A0]/g,"").replace(/[k€kK]/g,"").replace(",",".");
  if(!t)return null;
  const n=parseFloat(t);
  return (isNaN(n)||n<0)?null:Math.round(n)/1000;
}
function marquerGagne(id){
  const l=LEADS.find(x=>x.id===id);if(!l)return;
  if(!confirm("Confirmer : Amarante a GAGNÉ ce marché ?\n\n"+l.titre))return;
  envoyerStatut(l,"gagne","");
}
function marquerPerdu(id){
  const l=LEADS.find(x=>x.id===id);if(!l)return;
  ouvrirMotifPerte(l);
}
// Le motif de perte se choisit dans une liste FERMEE. Un prompt libre
// produirait « trop cher », « prix », « budget » pour la meme raison, et la
// statistique deviendrait inexploitable -- exactement ce qu'on veut eviter.
function ouvrirMotifPerte(l){
  const opts=Object.keys(MOTIFS_PERTE).map(k=>
    `<label class="mp-opt"><input type="radio" name="mp" value="${k}"> ${esc(MOTIFS_PERTE[k])}</label>`).join("");
  const ov=document.createElement("div");
  ov.className="mp-ov";
  ov.innerHTML=`<div class="mp-box">
    <div class="mp-t">Pourquoi ce marché est-il perdu ?</div>
    <div class="mp-s">${esc(l.titre).slice(0,90)}</div>
    <div class="mp-list">${opts}</div>
    <div class="mp-note">Le motif alimente l'apprentissage du radar. Liste fermée : c'est ce qui rend les pertes comparables entre elles.</div>
    <div class="mp-btns"><button class="btn" data-mp="annuler">Annuler</button><button class="btn pri" data-mp="ok">Enregistrer la perte</button></div>
  </div>`;
  document.body.appendChild(ov);
  ov.addEventListener("click",e=>{
    const b=e.target.closest("[data-mp]");
    if(!b&&e.target!==ov)return;
    if(!b||b.dataset.mp==="annuler"){ov.remove();return;}
    const sel=ov.querySelector('input[name="mp"]:checked');
    if(!sel){toast("Choisis un motif.",true);return;}
    ov.remove();envoyerStatut(l,"perdu",sel.value);
  });
}
function rechercherEnt(nom){closeDrawer();state.q=nom;document.getElementById("search").value=nom;go("opps");renderTable();}
// --- ENTREPRISES 360 : dedup transverse par entite (ent_cle), toutes sources ---
const TACT_LBL={delegation_mission:"délégation / mission",recrutement_local:"recrutement",contrat_export:"contrat export",implantation:"implantation",livraison_mise_en_service:"mise en service",formation_mco:"formation / MCO",essais_demonstration:"essais",incident:"incident",autre:"signal"};
let firmoState={tri:"activite",etr:"",suivis:false,q:""};
// Cle canonique : ent_cle precalcule serveur (source de verite). Repli minuscules.
function cleEnt(nom,entcle){return (entcle||"").trim()||String(nom||"").trim().toLowerCase();}
const WL_CLES=new Set(WATCHLIST.map(w=>cleEnt(w.entreprise,w.ent_cle)).filter(Boolean));
const WL_SECT={};WATCHLIST.forEach(w=>{const k=cleEnt(w.entreprise,w.ent_cle);if(k&&!WL_SECT[k])WL_SECT[k]=w.secteur||"Autre";});
function entreprises(){
  const by={};
  // 1) Leads porteurs d'une entreprise : ATTRIB = marche gagne, PRIVÉ/FI = signal.
  LEADS.forEach(l=>{
    const nom=(l.titulaire||"").trim();if(!nom)return;
    const k=cleEnt(nom,l.entcle);if(!k)return;
    if(!by[k])by[k]={cle:k,noms:{},marches:[],signaux:[],zones:new Set(),secteurs:new Set(),valeur:0,origines:{},origine:"",etranger:false,contact:null,suivi:WL_CLES.has(k),secteurSuivi:WL_SECT[k]||"",lastTs:0};
    const e=by[k];
    e.noms[nom]=(e.noms[nom]||0)+1;
    if(l.src==="ATTRIB"){e.marches.push(l);e.valeur+=l.valeur||0;}else{e.signaux.push(l);}
    if(l.zone)e.zones.add(l.zone);if(l.secteur)e.secteurs.add(l.secteur);
    // ORIGINE ARBITREE, pas « la premiere rencontree ». Une entreprise avec
    // deux attributions donnant deux pays affichait silencieusement celui
    // qui sortait en tete du tri. On compte les variantes et on tranche a
    // la majorite, en gardant la trace du desaccord.
    if(l.pays_tit)e.origines[l.pays_tit]=(e.origines[l.pays_tit]||0)+1;
    if(l.etranger)e.etranger=true;
    if((l.ts||0)>e.lastTs)e.lastTs=l.ts||0;
    if(l.email&&l.email!=="n.c."&&!e.contact)e.contact={nom:l.nom,email:l.email};
  });
  // 2) Comptes suivis (watchlist) sans activite detectee -> fiche quand meme.
  WATCHLIST.forEach(w=>{
    const nom=(w.entreprise||"").trim();if(!nom)return;
    const k=cleEnt(nom,w.ent_cle);if(!k||by[k])return;
    by[k]={cle:k,noms:{[nom]:1},marches:[],signaux:[],zones:new Set(),secteurs:new Set(),valeur:0,origines:{},origine:"",etranger:false,contact:null,suivi:true,secteurSuivi:w.secteur||"Autre",lastTs:0};
  });
  // Nom d'affichage : la variante la plus frequente (repli : la plus longue).
  return Object.values(by).map(e=>{
    const noms=Object.keys(e.noms);
    e.nom=noms.sort((a,b)=>(e.noms[b]-e.noms[a])||(b.length-a.length))[0]||"?";
    const og=Object.keys(e.origines).sort((a,b)=>e.origines[b]-e.origines[a]);
    e.origine=og[0]||"";
    e.originesAutres=og.slice(1);          // desaccord de sources, a signaler
    e.activite=e.marches.length+e.signaux.length;
    return e;
  });
}
function toggleSuivis(){firmoState.suivis=!firmoState.suivis;document.getElementById("ff-suivis").classList.toggle("on",firmoState.suivis);renderFirmo();}
function renderFirmo(){
  let ent=entreprises();
  if(firmoState.etr)ent=ent.filter(e=>e.etranger);
  if(firmoState.suivis)ent=ent.filter(e=>e.suivi);
  if(firmoState.q){const q=firmoState.q.toLowerCase();ent=ent.filter(e=>e.nom.toLowerCase().includes(q));}
  const t=firmoState.tri;
  ent.sort((a,b)=>t==="valeur"?b.valeur-a.valeur:t==="theatres"?b.zones.size-a.zones.size:t==="marches"?b.marches.length-a.marches.length:t==="signaux"?b.signaux.length-a.signaux.length:(b.lastTs-a.lastTs)||(b.activite-a.activite));
  const nSuivis=ent.filter(e=>e.suivi).length;
  document.getElementById("ff-count").textContent=ent.length+" entreprise"+(ent.length>1?"s":"")+" · "+nSuivis+" suivie"+(nSuivis>1?"s":"");
  document.getElementById("firmo-grid").innerHTML=ent.map(e=>{
    const ini=e.nom.replace(/[^A-Za-zÀ-ÿ ]/g,"").split(/\s+/).slice(0,2).map(w=>w[0]||"").join("").toUpperCase()||"?";
    const z=[...e.zones];const s=[...e.secteurs];
    const b=(e.suivi?'<span class="fbadge suivi">👁 suivi</span>':"")+(e.etranger?'<span class="fbadge etr">étranger</span>':"")+(e.signaux.length?'<span class="fbadge sig">'+e.signaux.length+' signal'+(e.signaux.length>1?'aux':'')+'</span>':"");
    return `<div class="fcard" onclick="openFiche('${e.cle.replace(/'/g,"\\'")}')">
      <div class="fcard-top"><div class="fmono">${ini}</div><div style="min-width:0"><div class="fname">${esc(e.nom)}</div><div class="fmeta">${e.origine?esc(e.origine)+(e.originesAutres.length?'<span class="fmeta-warn" title="Sources divergentes : '+esc(e.originesAutres.join(", "))+'"> ?</span>':""):"origine n.c."}${e.secteurSuivi?' · <span class="fmeta-sect">'+esc(e.secteurSuivi)+'</span>':""}</div></div></div>
      <div class="fbadges">${b||'<span class="fbadge zero">à qualifier</span>'}</div>
      <div class="fstats"><div class="fstat"><div class="n">${e.marches.length}</div><div class="l">gagnés</div></div><div class="fstat"><div class="n">${e.signaux.length}</div><div class="l">signaux</div></div><div class="fstat"><div class="n">${z.length}</div><div class="l">théâtres</div></div><div class="fstat"><div class="n">${e.valeur?fmtEur(e.valeur):"—"}</div><div class="l">valeur</div></div></div>
      <div class="fchips">${z.slice(0,3).map(x=>`<span class="fchip">${x}</span>`).join("")}${s.slice(0,2).map(x=>`<span class="fchip">${x}</span>`).join("")}${e.contact?'<span class="fchip" style="color:var(--green)">contact ✓</span>':""}</div>
    </div>`;
  }).join("")||'<div class="empty">Aucune entreprise pour ces filtres.</div>';
}
function openFiche(cle){
  const e=entreprises().find(x=>x.cle===cle);if(!e)return;
  const ini=e.nom.replace(/[^A-Za-zÀ-ÿ ]/g,"").split(/\s+/).slice(0,2).map(w=>w[0]||"").join("").toUpperCase()||"?";
  const z=[...e.zones];const s=[...e.secteurs];
  const items=[...e.marches.map(l=>({l,k:"m"})),...e.signaux.map(l=>({l,k:"s"}))].sort((a,b)=>(b.l.ts||0)-(a.l.ts||0));
  const timeline=items.map(({l,k})=>{
    const col=scoreColor(l.score);
    const tag=k==="m"?'<span class="fi-tag gagne">marché gagné</span>':'<span class="fi-tag sig">'+(TACT_LBL[l.type_activite||"autre"]||"signal")+'</span>';
    const meta=[l.zone,l.pays,relDate(l),(k==="m"&&l.valeur?fmtEur(l.valeur):"")].filter(Boolean).join(" · ");
    return `<div class="tl-row" onclick="openDrawer(${l.id})" style="cursor:pointer"><div class="tl-dot" style="background:${col}"></div><div class="tl-body"><div class="tl-t">${tag}${esc(l.titre).slice(0,90)}<span class="tl-go">→</span></div><div class="tl-m">${meta}</div></div></div>`;
  }).join("")||'<div class="fi-empty">Aucun marché ni signal détecté. Compte suivi en veille.</div>';
  const contactNom=e.contact&&e.contact.nom&&e.contact.nom!=="n.c."?e.contact.nom:e.nom;
  const mail=e.contact&&e.contact.email&&e.contact.email!=="n.c."?`<a class="btn" style="width:100%;justify-content:center" href="mailto:${e.contact.email}">Écrire à ${esc(contactNom)}</a>`:'<div style="font-size:12px;color:var(--ink-3);font-family:var(--mono)">Pas de contact enrichi.</div>';
  document.getElementById("drawer-content").innerHTML=`
  <div class="dr-head"><button class="dr-close" onclick="closeDrawer()"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg></button>
    <div class="dr-src">Fiche entreprise 360°</div>
    <div style="display:flex;gap:12px;align-items:center"><div class="fmono" style="width:44px;height:44px;font-size:15px">${ini}</div><div style="min-width:0"><h3 style="margin:0">${esc(e.nom)}</h3><div class="fmeta">${e.origine?esc(e.origine):"origine n.c."}${e.originesAutres.length?' <span class="fmeta-warn" title="Sources divergentes">(aussi '+esc(e.originesAutres.join(", "))+')</span>':""}${e.secteurSuivi?' · <span class="fmeta-sect">'+esc(e.secteurSuivi)+'</span>':""}${e.etranger?' · <span style="color:var(--blue)">étranger</span>':""}${e.suivi?' · <span style="color:var(--amarante)">👁 suivi</span>':""}</div></div></div></div>
  <div class="dr-body">
    <div class="fi-stats"><div class="fi-stat"><div class="n">${e.marches.length}</div><div class="l">marchés gagnés</div></div><div class="fi-stat"><div class="n">${e.signaux.length}</div><div class="l">signaux</div></div><div class="fi-stat"><div class="n">${z.length}</div><div class="l">théâtres</div></div><div class="fi-stat"><div class="n">${e.valeur?fmtEur(e.valeur):"—"}</div><div class="l">valeur cumulée</div></div></div>
    ${(z.length||s.length)?`<div class="dr-sec"><h5>Présence</h5><div class="fchips">${z.map(x=>`<span class="fchip">${x}</span>`).join("")}${s.map(x=>`<span class="fchip alt">${x}</span>`).join("")}</div></div>`:""}
    <div class="dr-sec"><h5>Historique unifié (marchés + signaux)</h5><div class="tl">${timeline}</div></div>
    <div class="dr-sec"><h5>Contact</h5>${mail}</div>
  </div>`;
  document.getElementById("drawer").classList.add("on");document.getElementById("drawer-ov").classList.add("on");
}
// --- CANDIDATS DE DECOUVERTE : pistes non encore promues, a arbitrer ---
// Un candidat n'est PAS un projet suivi. Il est affiche a part, avec ce qui
// permet de trancher : combien de redactions distinctes le rapportent, quelle
// phase, quels acteurs, et les articles d'origine.
const CANDPROJ=(CANDPROJ_RAW||[]).map((c,i)=>({
  i:i, nom:String(c.nom||"?"), id:String(c.project_id_propose||""),
  statut:String(c.statut||"candidat"), iso3:String(c.iso3||""),
  secteur:String(c.secteur||""), phase:String(c.phase||""),
  conf:+c.confiance||0, nsig:+c.nb_signaux||0, nsrc:+c.nb_sources||0,
  montant:+c.montant_musd||0, acteurs:String(c.acteurs||""),
  motifs:String(c.motifs||""), premiere:String(c.premiere_detection||""),
  signaux:Array.isArray(c.signaux)?c.signaux:[],
}));
function renderCandProj(){
  const el=document.getElementById("cand-proj");
  if(!el)return;
  if(!CANDPROJ.length){el.innerHTML="";return;}
  const promus=CANDPROJ.filter(c=>c.statut==="promu");
  const rows=CANDPROJ.slice().sort((a,b)=>b.conf-a.conf).map(c=>{
    const chaud=c.statut==="promu";
    const arts=c.signaux.slice(0,4).map(s=>`<div class="cp-art"><span class="cp-d">${esc(s.date||"")}</span>${esc(s.titre||"").slice(0,84)}${s.lien?` <a href="${esc(s.lien)}" target="_blank" rel="noopener">↗</a>`:""}</div>`).join("");
    return `<div class="cp ${chaud?"promu":""}">
      <div class="cp-h"><span class="cp-n">${esc(c.nom)}</span>
        <span class="cp-badges">${chaud?'<span class="mb attribp">✦ à promouvoir</span>':'<span class="mb surv">piste</span>'}
        <span class="mb">${esc(c.iso3)||"?"}</span><span class="mb">${esc(c.secteur)}</span>
        ${c.phase?`<span class="ph-pill">${esc(c.phase)}</span>`:""}</span>
        <span class="cp-conf" style="color:${jaugeCouleur(c.conf)}">${c.conf}</span></div>
      <div class="cp-m">${c.nsrc} source${c.nsrc>1?"s":""} distincte${c.nsrc>1?"s":""} · ${c.nsig} signal${c.nsig>1?"aux":""}${c.montant?" · "+fmtMusd(c.montant):""}${c.acteurs?" · "+esc(c.acteurs):""}</div>
      <div class="cp-m" style="color:var(--ink-3)">${esc(c.motifs).slice(0,150)}</div>
      ${arts?`<div class="cp-arts">${arts}</div>`:""}
      ${chaud?`<div class="cp-todo">Identifiant proposé <span class="mono-inline">${esc(c.id)}</span> · à ajouter au registre après validation</div>`:""}
    </div>`;
  }).join("");
  el.innerHTML=`<div class="cp-wrap"><div class="cp-titre">Pistes de découverte
    <span class="cp-sub">${CANDPROJ.length} candidat${CANDPROJ.length>1?"s":""}${promus.length?` · ${promus.length} prêt${promus.length>1?"s":""} à promouvoir`:""} · non suivis tant qu'ils ne sont pas validés</span></div>${rows}</div>`;
}
// --- PROJETS (Project Intelligence) : grands projets suivis avant l'AO ---
const PHASES_ORD=["IDEA","POLITICAL_ANNOUNCEMENT","PRE_FEASIBILITY","FEASIBILITY","GOVERNMENT_AGREEMENT","MOU","FUNDING_SEARCH","FUNDING_APPROVED","CONSULTANT_SELECTION","FEED","PRE_FID","FID","EPC_PROCUREMENT","EPC_AWARDED","CONSTRUCTION","COMMISSIONING","OPERATIONS"];
const ALERTE_LBL={haute:"🔴 haute",moyenne:"🟠 moyenne",signal_precoce:"🟡 précoce",aucune:"—"};
// Normalisation : les valeurs viennent du Sheet (donc en texte). String() +
// repli, sinon une cellule inattendue casse tout le rendu (piege connu).
const PROJETS=(PROJETS_RAW||[]).map((p,i)=>({
  i:i,
  id:String(p.project_id||""),
  libelle:String(p.libelle||p.project_id||"Projet"),
  pays:String(p.pays||""),iso3:String(p.iso3||""),secteur:String(p.secteur||""),
  phase:String(p.phase_courante||""),phaseLbl:String(p.libelle_phase||"Phase inconnue"),
  phaseMax:String(p.phase_max_atteinte||""),recul:String(p.recul||"")==="oui",
  maturite:+p.maturite||0,palier:String(p.palier_maturite||""),
  opportunite:+p.opportunite||0,phrase:String(p.opportunite_phrase||""),
  alerte:String(p.alerte||"aucune"),nbSignaux:+p.nb_signaux||0,
  premiere:String(p.premiere_detection||""),derniere:String(p.derniere_maj||""),
  suite:String(p.prochaine_etape||""),
  fDebut:String(p.fenetre_debut||""),fFin:String(p.fenetre_fin||""),fConf:String(p.fenetre_confiance||""),
  valeur_musd:+p.valeur_musd||0,
  acteurs:String(p.acteurs||"").split(",").map(x=>x.trim()).filter(Boolean),
  services:String(p.services||"").split(",").map(x=>x.trim()).filter(Boolean),
  prospects:String(p.prospects||"").split(",").map(x=>x.trim()).filter(Boolean),
  timeline:Array.isArray(p.timeline)?p.timeline:[],
  rangPhase:Math.max(0,PHASES_ORD.indexOf(String(p.phase_courante||""))+1),
  ts:parseDate(String(p.derniere_maj||"")),
}));
let projState={q:"",pays:"",sect:"",phase:"",alerte:"",top:false,sort:"opportunite",dir:-1};
function fmtMusd(v){return !v?"n.c.":v>=1000?(v/1000).toFixed(v<10000?1:0)+" Md$":v+" M$";}
function jaugeCouleur(v){return v>=70?"var(--red)":v>=45?"var(--amber)":"var(--blue)";}
function jauge(v,titre){
  return `<div class="jauge" title="${titre}"><div class="jauge-bar"><span style="width:${Math.max(2,Math.min(v,100))}%;background:${jaugeCouleur(v)}"></span></div><b style="color:${jaugeCouleur(v)}">${v}</b></div>`;
}
function projetsFiltres(){
  let ps=PROJETS.filter(p=>{
    if(projState.pays&&p.pays!==projState.pays)return false;
    if(projState.sect&&p.secteur!==projState.sect)return false;
    if(projState.phase&&p.phase!==projState.phase)return false;
    if(projState.alerte&&p.alerte!==projState.alerte)return false;
    if(projState.q){const q=projState.q.toLowerCase();
      if(!((p.libelle+" "+p.pays+" "+p.acteurs.join(" ")).toLowerCase().includes(q)))return false;}
    return true;
  }).sort((a,b)=>{
    const k=projState.sort;let va=a[k],vb=b[k];
    if(typeof va==="string"){va=va.toLowerCase();vb=String(vb||"").toLowerCase();
      return va<vb?-projState.dir:va>vb?projState.dir:0;}
    return ((va||0)-(vb||0))*projState.dir;
  });
  if(projState.top)ps=ps.slice().sort((a,b)=>b.opportunite-a.opportunite).slice(0,20);
  return ps;
}
function renderProj(){
  try{
    renderCandProj();
    // VUE NON ALIMENTEE : tant que les collecteurs Project Intelligence sont a
    // l'arret, l'onglet affichait quatre KPI a zero, des facettes vides et un
    // tableau vide. Le message existait, mais tout en bas, sous le bruit.
    // On masque l'echafaudage et on ne garde que l'explication.
    const vide=!PROJETS.length&&!CANDPROJ.length;
    ["kpis-proj","p-filtres"].forEach(i=>{const el=document.getElementById(i);
      if(el)el.style.display=vide?"none":"";});
    if(vide){
      document.getElementById("tbody-proj").innerHTML=`<tr><td colspan="8" class="empty" style="text-align:left;line-height:1.7;padding:34px 30px">
        <strong style="color:var(--ink);font-family:var(--display);font-size:15px">Vue en attente d'alimentation</strong><br>
        Les collecteurs Project Intelligence (<span class="mono-inline">collecteur_projets</span> et <span class="mono-inline">decouverte_projets</span>) sont à l'arrêt dans <span class="mono-inline">radar.yml</span>.<br>
        Rien n'est cassé : cette vue se remplira dès leur activation, sans autre changement.</td></tr>`;
      document.getElementById("proj-count").textContent="";
      return;
    }
    const ps=projetsFiltres();
    const hautes=PROJETS.filter(p=>p.alerte==="haute").length;
    const chauds=PROJETS.filter(p=>p.opportunite>=70).length;
    const kp=[
      {lbl:"Projets suivis",val:PROJETS.length,c:"var(--amarante)"},
      {lbl:"Opportunités fortes",val:chauds,c:"var(--red)",sub:"score ≥ 70"},
      {lbl:"Alertes hautes",val:hautes,c:"var(--amber)",sub:"FID, EPC, financement"},
      {lbl:"Valeur suivie",val:fmtMusd(PROJETS.reduce((s,p)=>s+p.valeur_musd,0)),c:"var(--green)"},
    ];
    document.getElementById("kpis-proj").innerHTML=kp.map(k=>`<div class="kpi"><div class="k-lbl" style="margin-bottom:8px">${k.lbl}</div><div class="k-val" style="color:${k.c}">${k.val}</div>${k.sub?`<div class="k-sub">${k.sub}</div>`:""}</div>`).join("");
    document.getElementById("tbody-proj").innerHTML=ps.map(p=>{
      const b=[];
      if(p.recul)b.push('<span class="mb recul">↓ recul de phase</span>');
      if(p.prospects.length)b.push('<span class="mb prosp">'+p.prospects.length+' prospect'+(p.prospects.length>1?'s':'')+'</span>');
      return `<tr onclick="openProjet(${p.i})">`
        +`<td><div class="t-title">${esc(p.libelle)}</div><div class="t-sub">${esc(p.secteur)} · ${p.nbSignaux} signal${p.nbSignaux>1?"aux":""}</div>${b.length?`<div class="mini-badges">${b.join("")}</div>`:""}</td>`
        +`<td>${esc(p.pays)}</td>`
        +`<td class="t-val">${fmtMusd(p.valeur_musd)}</td>`
        +`<td><span class="ph-pill">${esc(p.phaseLbl)}</span></td>`
        +`<td>${jauge(p.maturite,"Maturité du projet : "+esc(p.palier))}</td>`
        +`<td>${jauge(p.opportunite,"Opportunité Amarante")}</td>`
        +`<td class="t-date">${p.derniere?relDate({ts:p.ts,mois:p.derniere}):"n.c."}</td>`
        +`<td class="t-sub">${p.fDebut?esc(p.fDebut+"-"+p.fFin)+'<div class="t-sub">confiance '+esc(p.fConf)+'</div>':"—"}</td></tr>`;
    }).join("")||'<tr><td colspan="8" class="empty">Aucun projet suivi. Active le collecteur Project Intelligence (RADAR_PROJETS=1) pour peupler cette vue.</td></tr>';
    document.getElementById("proj-count").textContent=ps.length+" projet"+(ps.length>1?"s":"")+(projState.top?" (top 20 par opportunité)":"");
  }catch(err){
    document.getElementById("tbody-proj").innerHTML='<tr><td colspan="8" class="empty">Affichage des projets indisponible ('+esc(err&&err.message)+').</td></tr>';
  }
}
function toggleTop(){projState.top=!projState.top;document.getElementById("p-top").classList.toggle("on",projState.top);renderProj();}
function resetProj(){projState.q=projState.pays=projState.sect=projState.phase=projState.alerte="";projState.top=false;
  ["p-q","p-pays","p-sect","p-phase"].forEach(i=>{const el=document.getElementById(i);if(el)el.value="";});
  document.getElementById("p-top").classList.remove("on");
  document.querySelectorAll("#p-alerte button").forEach((x,i)=>x.classList.toggle("on",i===0));renderProj();}
function openProjet(i){
  const p=PROJETS[i];if(!p)return;
  const tl=(p.timeline||[]).map(bloc=>`<div class="tl-an"><div class="tl-an-h">${esc(bloc.annee)}</div>${(bloc.evenements||[]).map(e=>`<div class="tl-row"><div class="tl-dot" style="background:var(--amarante)"></div><div class="tl-body"><div class="tl-t"><span class="tl-ph">${esc(e.libelle_phase||e.phase||"")}</span>${esc(e.titre||"").slice(0,90)}</div><div class="tl-m">${esc(e.date||"")}</div></div></div>`).join("")}</div>`).join("")
    ||'<div class="fi-empty">Aucun événement daté et classé pour l\'instant.</div>';
  const prosp=p.prospects.length?p.prospects.map(x=>`<div class="cand" onclick="rechercherEnt('${x.replace(/'/g,"\\'")}')"><div class="cand-n">${esc(x)}</div><div class="cand-m">acteur international du projet · prospect à ouvrir</div></div>`).join(""):'<div class="fi-empty">Aucun contractor international identifié à ce stade.</div>';
  document.getElementById("drawer-content").innerHTML=`
  <div class="dr-head"><button class="dr-close" onclick="closeDrawer()"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg></button>
    <div class="dr-src">Projet · ${esc(p.pays)} · ${esc(p.secteur)}</div><h3>${esc(p.libelle)}</h3>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><span class="ph-pill">${esc(p.phaseLbl)}</span><span class="mb ${p.alerte==="haute"?"attribp":"surv"}">${ALERTE_LBL[p.alerte]||"—"}</span><span class="t-val" style="margin-left:auto;font-size:15px">${fmtMusd(p.valeur_musd)}</span></div></div>
  <div class="dr-body">
    <div class="fi-stats">
      <div class="fi-stat"><div class="n" style="color:${jaugeCouleur(p.opportunite)}">${p.opportunite}</div><div class="l">opportunité Amarante</div></div>
      <div class="fi-stat"><div class="n">${p.maturite}</div><div class="l">maturité projet</div></div>
      <div class="fi-stat"><div class="n">${p.nbSignaux}</div><div class="l">signaux</div></div>
      <div class="fi-stat"><div class="n">${p.fDebut||"—"}</div><div class="l">besoin probable</div></div>
    </div>
    <div class="dr-sec"><h5>Pourquoi cette opportunité</h5><div class="dr-analyse">${esc(p.phrase)||"—"}</div></div>
    <div class="dr-sec"><h5>Trajectoire</h5><div class="dr-grid">
      <div class="dr-field"><div class="l">Phase courante</div><div class="v">${esc(p.phaseLbl)}</div></div>
      <div class="dr-field"><div class="l">Prochaine étape</div><div class="v">${esc(p.suite)||"—"}</div></div>
      <div class="dr-field"><div class="l">Première détection</div><div class="v">${esc(p.premiere)||"—"}</div></div>
      <div class="dr-field"><div class="l">Dernier signal</div><div class="v">${esc(p.derniere)||"—"}</div></div>
      ${p.recul?`<div class="dr-field"><div class="l">Alerte trajectoire</div><div class="v" style="color:var(--red)">Recul depuis ${esc(p.phaseMax)}</div></div>`:""}
    </div></div>
    <div class="dr-sec"><h5>Services Amarante probables</h5><div class="fchips">${p.services.map(s=>`<span class="fchip">${esc(s)}</span>`).join("")||"—"}</div></div>
    <div class="dr-sec"><h5>Prospects issus du projet</h5><div class="cand-list">${prosp}</div><div class="cand-note">Acteurs internationaux du projet : ce sont eux qui déploieront du personnel, donc les comptes à ouvrir avant l'appel d'offres.</div></div>
    <div class="dr-sec"><h5>Chronologie</h5>${tl}</div>
    ${p.acteurs.length?`<div class="dr-sec"><h5>Tous les acteurs cités</h5><div class="fchips">${p.acteurs.map(a=>`<span class="fchip">${esc(a)}</span>`).join("")}</div></div>`:""}
  </div>`;
  document.getElementById("drawer").classList.add("on");document.getElementById("drawer-ov").classList.add("on");
}
// --- GEOPOLITIQUE : alertes de la semaine, par theatre ---
function posColor(z){const p=posture(z)[0];return p==="p-rouge"?"#C0392B":p==="p-orange"?"#B07419":"#33628F";}
function renderGeo(){
  const head=document.getElementById("geo-head");
  if(!GEO.length){head.innerHTML="";document.getElementById("geo-body").innerHTML='<div class="empty">Aucune alerte cette semaine. Le contexte des théâtres est stable.</div>';return;}
  const agg=GEO.filter(g=>g.sens==="aggravation"||g.sens==="up"||(+g.severite>=7)).length;
  const zonesTouchees=new Set(GEO.map(g=>g.zone)).size;
  head.innerHTML=`<div class="geo-kpi"><div class="n">${GEO.length}</div><div class="l">signaux cette semaine</div></div><div class="geo-kpi"><div class="n" style="color:var(--red)">${agg}</div><div class="l">aggravations</div></div><div class="geo-kpi"><div class="n">${zonesTouchees}</div><div class="l">théâtres concernés</div></div>`;
  const byZone={};GEO.forEach(g=>{(byZone[g.zone||"Non classé"]=byZone[g.zone||"Non classé"]||[]).push(g);});
  const zones=Object.keys(byZone).sort((a,b)=>byZone[b].length-byZone[a].length);
  document.getElementById("geo-body").innerHTML=zones.map(z=>{
    const col=posColor(z);
    return `<div class="geo-zone"><h3><span class="zdot" style="background:${col}"></span>${z} · ${byZone[z].length}</h3>${byZone[z].map(g=>{
      const sev=+g.severite||0;const sc=sev>=8?"#C0392B":sev>=5?"#B07419":"#33628F";
      const up=g.sens==="aggravation"||g.sens==="up";
      return `<div class="geo-row" style="border-left-color:${col}"><div class="geo-sev" style="background:${sc}">${sev||"·"}</div><div class="geo-mid"><div class="geo-pays">${esc(g.pays||g.iso3||"?")}</div><div class="geo-motif">${esc(g.motif||(g.avant&&g.apres?g.avant+" → "+g.apres:""))||"—"}</div></div><span class="geo-sens ${up?"up":"down"}">${up?"▲ aggravation":"▼ amélioration"}</span><span class="geo-date">${g.date||""}</span></div>`;
    }).join("")}</div>`;
  }).join("");
}
// --- DOSSIERS (ecosysteme) : projets BM suivis a travers leurs phases ---
let dossState={mp:"1"};
const PH_LBL={amont:"Amont",avis:"Appel d'offres",attribution:"Attribution"};
const PH_ORD=["amont","avis","attribution"];
function leadDeTimeline(tl){
  if(tl.pub){const l=LEADS.find(x=>x.pub&&x.pub===tl.pub);if(l)return l;}
  return LEADS.find(x=>x.titre===tl.titre&&(!tl.src||x.src===tl.src));
}
function renderDoss(){
  try{
    let ds=DOSSIERS.slice();
    if(dossState.mp)ds=ds.filter(d=>d.n_phases>=2);
    const multi=DOSSIERS.filter(d=>d.n_phases>=2).length;
    document.getElementById("d-count").textContent=ds.length+" dossier"+(ds.length>1?"s":"")+" · "+multi+" multi-phases";
    const vide=dossState.mp
      ? 'Aucun dossier multi-phases pour l\'instant. Un dossier devient « multi-phases » quand au moins deux étapes d\'un même projet BM (amont, avis, attribution) sont détectées. Bascule sur « Tous » pour voir les projets à une seule phase.'
      : 'Aucun dossier constitué pour l\'instant. Les dossiers se remplissent au fil des runs, à mesure que les phases partagent le même identifiant projet BM.';
    document.getElementById("doss-body").innerHTML=ds.map(carteDossier).join("")||'<div class="empty">'+vide+'</div>';
  }catch(err){
    document.getElementById("doss-body").innerHTML='<div class="empty">Affichage des dossiers indisponible ('+esc(err&&err.message)+').</div>';
  }
}
function carteDossier(d){
  const pipe=PH_ORD.map((p,i)=>{
    const on=(d.phases_presentes||[]).includes(p);
    const cur=d.phase_courante===p;
    const seg=i<PH_ORD.length-1?`<div class="ph-link ${on&&(d.phases_presentes||[]).includes(PH_ORD[i+1])?'on':''}"></div>`:"";
    return `<div class="ph ${on?'on':''} ${cur?'cur':''}">${PH_LBL[p]}</div>`+seg;
  }).join("");
  const tl=(d.timeline||[]).map(l=>{
    const col=scoreColor(typeof l.score==="number"?l.score:0);
    const info=l.phase==="attribution"
      ? `${esc(l.entreprise||"Titulaire")}${l.origine?" · "+esc(l.origine):""}${String(l.etranger).toLowerCase()==="oui"?' <span class="cand-etr">étranger</span>':""}${l.valeur?" · "+esc(l.valeur):""}`
      : `${l.pays?esc(l.pays)+" · ":""}${l.date?esc(l.date):""}${typeof l.score==="number"?" · score "+l.score.toFixed(1):""}`;
    const lead=leadDeTimeline(l);
    const clic=lead?`onclick="openDrawer(${lead.id})" style="cursor:pointer"`:"";
    const fleche=lead?'<span class="tl-go">→</span>':"";
    return `<div class="tl-row" ${clic}><div class="tl-dot" style="background:${col}"></div><div class="tl-body"><div class="tl-t"><span class="tl-ph">${PH_LBL[l.phase]||l.phase}</span>${esc(l.titre||"").slice(0,90)}${fleche}</div><div class="tl-m">${info}</div></div></div>`;
  }).join("");
  const cand=(CANDIDATS&&CANDIDATS.secteur?candidatsPour({secteur:d.secteur,zone:d.pays,titulaire:""}):[]);
  const candHtml=cand.length?`<div class="cand-note" style="margin-top:12px">Candidats probables (historique secteur/théâtre) : ${cand.slice(0,4).map(x=>esc(x.entreprise)+(x.etranger?" ⚑":"")).join(" · ")}</div>`:"";
  const nph=(d.phases_presentes||[]).length;
  return `<div class="doss" id="doss-${esc(d.proj_id)}"><div class="doss-top"><div class="doss-tit"><div class="doss-nom">${esc(d.titre||"Projet "+d.proj_id)}</div><div class="doss-meta">${esc(d.pays||"")}${d.secteur?" · "+esc(d.secteur):""} · ${nph} phase${nph>1?"s":""} · ${d.n_leads} signal${d.n_leads>1?"aux":""}</div></div><span class="doss-pid" title="Identifiant projet Banque Mondiale (clé de rattachement)">${esc(d.proj_id)}</span></div><div class="pipe">${pipe}</div><div class="tl">${tl}</div>${candHtml}</div>`;
}
const TITLES={overview:["Vue d'ensemble","Théâtre global"],opps:["Opportunités","Avis de marché et signaux privés"],map:["Carte des théâtres","Répartition géographique"],proj:["Projets","Grands projets suivis avant l'appel d'offres"],attrib:["Attributions","Qui a gagné quoi en zone à risque"],doss:["Dossiers","Projets suivis de l'amont à l'attribution"],firmo:["Entreprises 360°","Marchés gagnés, signaux et comptes suivis, dédupliqués par entité"],geo:["Géopolitique","Alertes de la semaine"]};
function go(v){
  state.view=v;
  document.querySelectorAll(".nav a").forEach(a=>a.classList.toggle("on",a.dataset.view===v));
  document.querySelectorAll(".view").forEach(s=>s.classList.remove("on"));
  document.getElementById("v-"+v).classList.add("on");
  document.getElementById("top-title").textContent=TITLES[v][0];document.getElementById("top-crumb").textContent=TITLES[v][1];
  if(v==="overview"){renderSante();renderTheatres();renderKPIs();renderCharts();renderFunnel();renderHot();}
  if(v==="opps")renderTable();if(v==="attrib")renderAttrib();
  const rc=document.getElementById("search");
  if(rc){const actif=(v==="opps"||v==="attrib");rc.disabled=!actif;
    rc.placeholder=v==="attrib"?"Rechercher un titulaire, marché, acheteur..."
      :actif?"Rechercher un marché, pays, titulaire...":"Recherche : onglets Opportunités et Attributions";}
  if(v==="firmo")renderFirmo();if(v==="geo")renderGeo();if(v==="doss")renderDoss();if(v==="proj")renderProj();
  if(v==="map")setTimeout(()=>{initMap();map.invalidateSize();},60);
}
function goZone(z){state.zone=z;document.getElementById("f-zone").value=z;go("opps");renderTable();}
function initFilters(){
  const uniq=k=>[...new Set(LEADS.map(l=>l[k]).filter(Boolean))].sort();
  uniq("zone").forEach(z=>document.getElementById("f-zone").innerHTML+=`<option>${z}</option>`);
  uniq("secteur").forEach(s=>document.getElementById("f-sect").innerHTML+=`<option>${s}</option>`);
  [...new Set(LEADS.filter(l=>l.src!=="ATTRIB").map(l=>l.src).filter(Boolean))].sort().forEach(s=>document.getElementById("f-src").innerHTML+=`<option>${s}</option>`);
  document.getElementById("f-zone").onchange=e=>{state.zone=e.target.value;renderTable();};
  document.getElementById("f-sect").onchange=e=>{state.sect=e.target.value;renderTable();};
  document.getElementById("f-src").onchange=e=>{state.src=e.target.value;renderTable();};
  const ft=document.getElementById("f-type");if(ft)ft.onchange=e=>{state.type=e.target.value;renderTable();};
  document.querySelectorAll("#f-prio button").forEach(b=>b.onclick=()=>{document.querySelectorAll("#f-prio button").forEach(x=>x.classList.remove("on"));b.classList.add("on");state.prio=b.dataset.p;renderTable();});
  document.querySelectorAll("thead th[data-sort]").forEach(th=>th.onclick=()=>{const k=th.dataset.sort;state.dir=(state.sort===k)?-state.dir:-1;state.sort=k;renderTable();});
  const rech=document.getElementById("search");
  // La barre du haut reste visible sur tous les onglets : elle ne doit pas
  // etre un controle mort. Elle pilote les Opportunites ET le registre des
  // attributions, selon la vue ouverte.
  rech.oninput=e=>{state.q=e.target.value;
    if(state.view==="opps")renderTable();else if(state.view==="attrib")renderAttrib();};
}
function toggleSurv(){state.surv=!state.surv;document.getElementById("f-surv").classList.toggle("on",state.surv);renderTable();}
function toggleNeuf(){state.neuf=!state.neuf;document.getElementById("f-neuf").classList.toggle("on",state.neuf);renderTable();}
function resetFilters(){state.zone=state.sect=state.src=state.type="";state.prio="traiter";state.surv=false;state.neuf=false;document.getElementById("f-surv").classList.remove("on");const fn=document.getElementById("f-neuf");if(fn)fn.classList.remove("on");["f-zone","f-sect","f-type","f-src"].forEach(i=>{const el=document.getElementById(i);if(el)el.value="";});document.querySelectorAll("#f-prio button").forEach((x,i)=>x.classList.toggle("on",i===0));renderTable();}
function exportCSV(){
  const rows=state.view==="opps"?filtered()
    :state.view==="attrib"?attribFiltres():LEADS;
  const head=["titre","zone","pays","secteur","valeur_meur","score","source","priorite","titulaire","origine"];
  const csv=[head.join(";")].concat(rows.map(l=>[l.titre,l.zone,l.pays,l.secteur,l.valeur,l.score,l.src,l.prio,l.titulaire,l.pays_tit].map(v=>`"${(""+v).replace(/"/g,'""')}"`).join(";"))).join("\n");
  const a=document.createElement("a");a.href="data:text/csv;charset=utf-8,"+encodeURIComponent(csv);a.download="radar_cockpit.csv";a.click();
}
document.getElementById("nav").addEventListener("click",e=>{const a=e.target.closest("a");if(a){go(a.dataset.view);}});
document.getElementById("cnt-opps").textContent=LEADS.filter(l=>l.src!=="ATTRIB").length;
document.getElementById("cnt-attrib").textContent=LEADS.filter(l=>l.src==="ATTRIB").length;
// Pied de page : ce que le point vert pretendait dire, mais en le disant. Date
// du run et nombre de sources a verifier, pas seulement un volume.
(function(){
  const el=document.getElementById("run-meta");if(!el)return;
  const d=(SANTE&&SANTE.date)?SANTE.date+" · ":"";
  const av=(SANTE&&SANTE.a_verifier>0)?" · "+SANTE.a_verifier+" source(s) à vérifier":"";
  el.textContent=d+LEADS.length+" leads"+av;
  el.title=av?"Voir le détail dans « État du dernier run » (Vue d'ensemble)":"";
})();
document.getElementById("ff-tri").onchange=e=>{firmoState.tri=e.target.value;renderFirmo();};
document.querySelectorAll("#ff-etr button").forEach(b=>b.onclick=()=>{document.querySelectorAll("#ff-etr button").forEach(x=>x.classList.remove("on"));b.classList.add("on");firmoState.etr=b.dataset.e;renderFirmo();});
document.getElementById("ff-q").oninput=e=>{firmoState.q=e.target.value;renderFirmo();};
(function(){document.getElementById("cnt-firmo").textContent=entreprises().length;})();
// --- Init vue Projets : facettes derivees des donnees, tri par entete ---
(function(){
  const badge=document.getElementById("cnt-proj");
  if(badge)badge.textContent=PROJETS.length||"";
  const bt=document.getElementById("p-top");
  if(bt&&PROJETS.length)bt.style.display="";
  const uniq=(k)=>[...new Set(PROJETS.map(p=>p[k]).filter(Boolean))].sort();
  const remplir=(id,vals,libelle)=>{const el=document.getElementById(id);if(!el)return;
    vals.forEach(v=>el.innerHTML+=`<option value="${v}">${libelle?libelle(v):v}</option>`);};
  remplir("p-pays",uniq("pays"));
  remplir("p-sect",uniq("secteur"));
  const phases=[...new Set(PROJETS.map(p=>p.phase).filter(Boolean))]
    .sort((a,b)=>PHASES_ORD.indexOf(a)-PHASES_ORD.indexOf(b));
  const lbl={};PROJETS.forEach(p=>{if(p.phase)lbl[p.phase]=p.phaseLbl;});
  remplir("p-phase",phases,v=>lbl[v]||v);
  const on=(id,champ)=>{const el=document.getElementById(id);
    if(el)el.onchange=e=>{projState[champ]=e.target.value;renderProj();};};
  on("p-pays","pays");on("p-sect","sect");on("p-phase","phase");
  const q=document.getElementById("p-q");
  if(q)q.oninput=e=>{projState.q=e.target.value;renderProj();};
  document.querySelectorAll("#p-alerte button").forEach(b=>b.onclick=()=>{
    document.querySelectorAll("#p-alerte button").forEach(x=>x.classList.remove("on"));
    b.classList.add("on");projState.alerte=b.dataset.a;renderProj();});
  document.querySelectorAll("thead th[data-psort]").forEach(th=>th.onclick=()=>{
    const k=th.dataset.psort;projState.dir=(projState.sort===k)?-projState.dir:-1;
    projState.sort=k;renderProj();});
})();
(function(){document.getElementById("cnt-doss").textContent=DOSSIERS.filter(d=>d.n_phases>=2).length;document.querySelectorAll("#d-mp button").forEach(b=>b.onclick=()=>{document.querySelectorAll("#d-mp button").forEach(x=>x.classList.remove("on"));b.classList.add("on");dossState.mp=b.dataset.m;renderDoss();});})();
initFilters();initAttribFiltres();go("overview");
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
