# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- EVENEMENTS GEOPOLITIQUES (veille pays via Google News).
=========================================================================

DEUXIEME BRIQUE DE LA VEILLE "SIGNAUX FAIBLES" -- LE "NIVEAU 10"
----------------------------------------------------------------
Coups d'Etat, etats d'urgence, evacuations, attentats majeurs, fermetures
d'aeroport : quand un de ces evenements survient dans un pays du perimetre,
les entreprises DEJA presentes deviennent immediatement des prospects (revue
de dispositif, evacuation, escorte, intelligence pays). Le document strategique
classait ce niveau cinq etoiles.

POURQUOI PAS ACLED
------------------
Verifie le 23/07/2026 : les conditions d'ACLED restreignent explicitement
l'usage de leurs donnees avec des systemes LLM/IA (que ce soit commercial ou
non), interdisent la revente, et exigent un contrat negocie pour un usage
entreprise. Or ce projet est a visee commerciale ET fait tourner un LLM sur
les signaux. Le risque juridique est reel, et il porte precisement sur le coeur
de metier d'Amarante (due diligence, conformite). On obtient donc le meme
BESOIN -- les evenements geopolitiques -- par une voie propre : Google News,
deja utilise, deja legal, deja teste.

VEILLE PAR PAYS, PAS PAR ENTREPRISE
-----------------------------------
`signaux_prives` repond a "quelle entreprise se deploie ?". Ce collecteur
repond a "quel pays vient de basculer ?". C'est la MEME nature que les alertes
voyageurs FCDO : un signal pays, sans titulaire, qui rend les autres leads du
pays plus chauds. Il s'affiche donc dans le MEME bandeau "contexte pays" du
dashboard (alertes_radar), pas dans les leads.

REUTILISATION MAXIMALE DE L'EXISTANT
------------------------------------
  - Google News RSS multi-locale via bitd.url_google_news / parser_rss ;
  - fraicheur, dedup, pre-filtre bruit via bitd (article_frais, id_article) ;
  - tri LLM (Haiku) via bitd._appel_llm / _parser_json, avec le disjoncteur
    de ted (un solde epuise coupe proprement) ;
  - perimetre et zones via ted.CODES_PAYS_SUIVIS / dashboard.ZONE_PAR_ISO3.

Aucune source nouvelle, aucun risque juridique nouveau.

USAGE
-----
    python evenements_geo.py
    RADAR_GEO_DEBUG=1 python evenements_geo.py     # n'ecrit rien
    RADAR_GEO_BUDGET=40 python evenements_geo.py   # plafond d'appels LLM

VARIABLES
---------
    RADAR_GEO_DEBUG     1 = diagnostic, aucune ecriture
    RADAR_GEO_BUDGET    max d'articles analyses par le LLM (defaut 60)
    RADAR_GEO_PAUSE     secondes entre requetes pays (defaut 0.4)
"""

import os
import sys
import time
import json
from datetime import date

import ted_complet_v14 as ted
import bitd_signaux as bitd


# ===========================================================================
# CONFIGURATION
# ===========================================================================

NOM_ONGLET = "alertes_radar"          # PARTAGE avec les alertes voyageurs :
                                       # meme bandeau "contexte pays".
BUDGET = int(os.environ.get("RADAR_GEO_BUDGET", "60"))
PAUSE = float(os.environ.get("RADAR_GEO_PAUSE", "0.4"))
DEBUG = os.environ.get("RADAR_GEO_DEBUG", "0") == "1"

# Requete d'evenements (FR + EN) : cible l'actu de RUPTURE, pas l'actu de fond.
# Les termes sont choisis pour attraper les basculements soudains qui creent un
# besoin de surete immediat.
DECLENCHEURS_GEO = (
    '"coup d\'etat" OR "etat d\'urgence" OR "loi martiale" OR evacuation OR '
    '"prise d\'otages" OR enlevement OR putsch OR insurrection OR '
    '"couvre-feu" OR "fermeture de l\'aeroport" OR '
    'coup OR "state of emergency" OR "martial law" OR evacuation OR '
    'kidnapping OR hostage OR curfew OR "airport closure" OR '
    'insurgency OR uprising OR "travel disruption"')

# Types d'evenement retournes par le LLM (vocabulaire ferme).
TYPES_EVENEMENT = (
    "coup_etat", "violence_politique", "enlevement", "evacuation",
    "catastrophe", "tension_diplomatique", "autre", "aucun")

# Severite indicative par type (le LLM affine, ceci borne).
SEVERITE_TYPE = {
    "coup_etat": 5, "enlevement": 5, "evacuation": 4, "violence_politique": 4,
    "catastrophe": 3, "tension_diplomatique": 2, "autre": 1, "aucun": 0}


PROMPT_GEO = """Tu analyses un article de presse pour Amarante International, societe francaise de securite privee operant en zones a risque.

CONTEXTE : on cherche des EVENEMENTS GEOPOLITIQUES DE RUPTURE dans un pays donne. Un tel evenement rend immediatement les entreprises deja presentes dans ce pays demandeuses de surete (revue de dispositif, evacuation, escorte, intelligence pays). On ne veut PAS l'actualite de fond, la politique ordinaire, le sport, l'economie : uniquement les ruptures qui declenchent un besoin de securite.

PAYS SURVEILLE : {pays}
ARTICLE
Titre : {titre}
Extrait : {resume}

Reponds UNIQUEMENT par un objet JSON, sans texte autour, sans balises Markdown :
{{
  "concerne_le_pays": true | false,
  "type_evenement": "coup_etat | violence_politique | enlevement | evacuation | catastrophe | tension_diplomatique | autre | aucun",
  "gravite": 1 a 5,
  "resume_court": "une phrase factuelle, ou vide si aucun evenement",
  "confiance": 0.0 a 1.0
}}

REGLES
- "concerne_le_pays": false si l'article ne parle pas reellement du pays surveille (homonymie, mention incidente).
- "type_evenement": "aucun" si l'article n'est pas une rupture securitaire (politique ordinaire, economie, sport, culture).
- Sois STRICT : mieux vaut "aucun" qu'un faux positif. Une liste d'evenements diluee ne sert a rien."""


# ===========================================================================
# ANALYSE  (fonctions PURES autant que possible)
# ===========================================================================

def construire_requete(nom_pays):
    """Requete Google News pour un pays. Le nom entre guillemets ancre la
    recherche, les declencheurs ciblent la rupture."""
    return '"{}" ({})'.format(nom_pays, DECLENCHEURS_GEO)


def normaliser_evenement(extraction):
    """Ramene l'extraction LLM dans le vocabulaire ferme. Ne leve jamais."""
    if not isinstance(extraction, dict):
        return None
    typ = str(extraction.get("type_evenement") or "").strip().lower()
    if typ not in TYPES_EVENEMENT:
        typ = "autre"
    try:
        grav = int(float(extraction.get("gravite", 0)))
    except (TypeError, ValueError):
        grav = 0
    grav = max(0, min(5, grav))
    try:
        conf = float(extraction.get("confiance", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    return {
        "concerne_le_pays": bool(extraction.get("concerne_le_pays")),
        "type_evenement": typ,
        "gravite": grav,
        "resume_court": str(extraction.get("resume_court") or "").strip(),
        "confiance": max(0.0, min(1.0, conf)),
    }


def est_pertinent(evenement):
    """Un evenement merite d'etre remonte s'il concerne le pays, n'est pas
    'aucun', et depasse un seuil minimal de gravite et de confiance. Le
    STRICT est volontaire : un bandeau dilue est inutile."""
    if not evenement:
        return False
    return (evenement["concerne_le_pays"]
            and evenement["type_evenement"] != "aucun"
            and evenement["gravite"] >= 2
            and evenement["confiance"] >= 0.5)


def lead_evenement(iso3, nom_fr, zone, article, evenement):
    """Ligne ecrite dans alertes_radar. MEME schema que les alertes voyageurs,
    pour partager le bandeau. Le 'sens' vaut toujours 'aggravation' (un
    evenement de rupture aggrave le contexte). Fonction pure."""
    grav = evenement["gravite"]
    return {
        "date_maj": date.today().isoformat(),
        "pays_execution": iso3,
        "pays_nom": nom_fr,
        "zone": zone,
        "niveau_avant": "",                       # pas de "avant" pour un evenement
        "niveau_apres": "Evenement : {}".format(
            evenement["type_evenement"].replace("_", " ")),
        "sens": "aggravation",
        "severite": grav,
        "motif": evenement["resume_court"] or article.get("titre", ""),
        "publication_number": "GEO-{}-{}".format(
            iso3, bitd.id_article(article.get("lien", "")) or "x"),
        "lien": article.get("lien", ""),
    }


# ===========================================================================
# COLLECTE
# ===========================================================================

COLONNES = [
    "date_maj", "pays_execution", "pays_nom", "zone",
    "niveau_avant", "niveau_apres", "sens", "severite", "motif",
    "publication_number", "lien",
]


def collecter_articles_pays(nom_pays, session=None):
    """Google News pour un pays, multi-locale, dedup, filtre fraicheur/bruit.
    Reutilise integralement les briques bitd."""
    session = session or ted.session_robuste()
    requete = construire_requete(nom_pays)
    vus, articles = set(), []
    for hl, gl, ceid in (("fr", "FR", "FR:fr"), ("en", "US", "US:en")):
        url = bitd.url_google_news("", requete, hl=hl, gl=gl, ceid=ceid)
        try:
            rep = session.get(url, timeout=25)
            lot = bitd.parser_rss(rep.text)
        except Exception:
            continue
        for a in lot:
            if not bitd.article_frais(a) or bitd.bruit_evident(a):
                continue
            k = bitd.id_article(a.get("lien", ""))
            if k and k not in vus:
                vus.add(k)
                articles.append(a)
        time.sleep(PAUSE)
    return articles


def analyser_article(nom_pays, article, appel=None):
    """Article -> evenement normalise, ou None. Passe par le disjoncteur de
    ted (via bitd._appel_llm) : un solde epuise coupe proprement."""
    prompt = PROMPT_GEO.format(
        pays=nom_pays, titre=article.get("titre", ""),
        resume=(article.get("resume") or article.get("titre", ""))[:600])
    try:
        appel_reel = appel or (lambda p: bitd._appel_llm(p, ted.MODELE))
        return normaliser_evenement(bitd._parser_json(appel_reel(prompt)))
    except Exception as e:
        print("  (info) Analyse geo echouee ({}).".format(e))
        return None


# ===========================================================================
# ECRITURE (Sheet + miroir PG), best-effort
# ===========================================================================

def ecrire(leads):
    if not leads:
        return
    sheet_id = os.environ.get("TED_SHEET_ID", "")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
    if sheet_id:
        try:
            import gspread
            from google.oauth2.service_account import Credentials
            creds = Credentials.from_service_account_file(
                fichier, scopes=["https://www.googleapis.com/auth/spreadsheets"])
            classeur = gspread.authorize(creds).open_by_key(sheet_id)
            try:
                f = classeur.worksheet(NOM_ONGLET)
            except Exception:
                f = classeur.add_worksheet(title=NOM_ONGLET, rows=2000,
                                           cols=len(COLONNES))
                f.append_row(COLONNES)
            f.append_rows([[str(l.get(c, "")) for c in COLONNES] for l in leads],
                          value_input_option="RAW")
            print("  {} evenement(s) ecrit(s) dans '{}'.".format(len(leads), NOM_ONGLET))
        except Exception as e:
            print("  (geo) ecriture Sheet impossible ({}). Le run continue.".format(e))
    try:
        import radar_stockage as st
        print("  (pg) " + st.ecrire_miroir(NOM_ONGLET, leads))
    except Exception as e:
        print("  (pg) miroir indisponible ({})".format(e))


# ===========================================================================
# MEMOIRE : ne pas re-remonter deux fois le meme article
# ===========================================================================

def deja_vus():
    """Ensemble des publication_number GEO deja presents (Postgres). Best-effort :
    en cas d'echec, on repart a vide (au pire, un doublon, jamais une perte)."""
    try:
        import radar_stockage as st
        if not st.actif():
            return set()
        with st.connexion() as conn:
            lignes = st.lire_onglet(conn, NOM_ONGLET)
        return {l.get("publication_number") for l in lignes
                if str(l.get("publication_number", "")).startswith("GEO-")}
    except Exception:
        return set()


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    print("=" * 62)
    print("EVENEMENTS GEOPOLITIQUES - Radar Amarante")
    print("  budget LLM : {}{}".format(
        BUDGET, " | MODE DEBUG (aucune ecriture)" if DEBUG else ""))
    print("=" * 62)

    try:
        import radar_dashboard as dash
        zone_par_iso3 = dict(dash.ZONE_PAR_ISO3)
    except Exception:
        zone_par_iso3 = {}

    perimetre = [c for c in ted.CODES_PAYS_SUIVIS if c in zone_par_iso3]
    # On priorise les zones les plus a risque : c'est la que la rupture coute
    # le plus cher, et le budget LLM doit y aller en premier.
    perimetre.sort(key=lambda c: -ted.MULTIPLICATEUR_ZONE.get(c, 0.3))
    print("  {} pays du perimetre a surveiller.".format(len(perimetre)))

    connus = deja_vus()
    session = ted.session_robuste()
    leads, analyses, lus = [], 0, 0

    for iso3 in perimetre:
        if analyses >= BUDGET:
            print("  budget LLM atteint ({}), arret propre.".format(BUDGET))
            break
        nom_fr, zone = zone_par_iso3.get(iso3, (iso3, "Non classe"))
        articles = collecter_articles_pays(nom_fr, session=session)
        for a in articles:
            if analyses >= BUDGET:
                break
            pub = "GEO-{}-{}".format(iso3, bitd.id_article(a.get("lien", "")) or "x")
            if pub in connus:
                continue                          # deja remonte un run precedent
            lus += 1
            evenement = analyser_article(nom_fr, a)
            analyses += 1
            if est_pertinent(evenement):
                leads.append(lead_evenement(iso3, nom_fr, zone, a, evenement))

    print("  {} article(s) analyse(s), {} evenement(s) retenu(s).".format(
        lus, len(leads)))
    for l in sorted(leads, key=lambda x: -x["severite"])[:12]:
        print("   [{}] {} : {}".format(l["severite"], l["pays_nom"],
                                        l["motif"][:100]))

    if DEBUG:
        print("\n  MODE DEBUG : {} evenement(s) NON ecrit(s).".format(len(leads)))
        return

    ecrire(leads)
    ted.sortie_selon_sante_llm("evenements-geo")


if __name__ == "__main__":
    main()
