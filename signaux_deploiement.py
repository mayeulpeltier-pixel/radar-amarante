# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- DECOUVERTE : deploiements prives en zone a risque.
===================================================================

LE GAP QUE CE MODULE COMBLE (12/08/2026)
----------------------------------------
bitd_signaux et signaux_prives interrogent Google News PAR ENTREPRISE d'une
watchlist : ils SUIVENT des societes deja connues. Une entreprise INCONNUE qui
ouvre une usine au Mali, prend une FID en RDC ou signe un contrat au Tchad est
aujourd'hui INVISIBLE. Ce module fait de la DECOUVERTE OUVERTE : il interroge
Google News par PAYS A RISQUE x MOTS-CLES DE DEPLOIEMENT, extrait l'entreprise
qui deploie, et alimente le radar en NOUVELLES cibles. C'est l'amont prive, le
pendant de bm_projets (amont public).

REUTILISATION MAXIMALE (rien de duplique)
-----------------------------------------
RSS + fraicheur + anti-bruit + id + nom->ISO3 + LLM : bitd_signaux.
Scoring + ecriture prive_radar : bitd.scorer via signaux_prives.scorer_signal +
bitd.ligne_prive. Le SEUL ajout : les requetes pays x mots-cles et un prompt
d'EXTRACTION calque sur bitd.PROMPT_SIGNAL (mais sans entreprise connue -- on la
fait ressortir). Ecrit dans le MEME onglet 'prive_radar' -> apparait directement
en Cibles privees, avec priorite_compte='decouverte'.

PRUDENCE
--------
Source neuve et potentiellement bruyante : desactivee par defaut
(RADAR_DECOUVERTE=1 pour l'activer). Pre-filtre deterministe AGRESSIF avant tout
LLM (fraicheur serree, pays a risque cite, anti-bruit, dedup) pour proteger le
budget. Budget LLM plafonne + disjoncteur herite de bitd/ted. A VALIDER EN REEL
au premier run (le prompt se regle sur donnees reelles).
"""

import os
import time
import datetime

import bitd_signaux as bitd
import signaux_prives as sp
import ted_complet_v14 as ted


ACTIVER = os.environ.get("RADAR_DECOUVERTE", "0") == "1"     # opt-in prudent
NOM_ONGLET = "prive_radar"                                    # meme onglet que BITD
BUDGET_LLM = int(os.environ.get("RADAR_DECOUVERTE_BUDGET", "40"))
NB_PAYS_PAR_RUN = int(os.environ.get("RADAR_DECOUVERTE_PAYS", "8"))
MAX_ARTICLES_PAR_REQUETE = int(os.environ.get("RADAR_DECOUVERTE_MAX_ART", "5"))
JOURS_FRAICHEUR = int(os.environ.get("RADAR_DECOUVERTE_JOURS", "10"))  # amont = frais
CONF_MIN = float(os.environ.get("RADAR_DECOUVERTE_CONF_MIN", "0.55"))
PAUSE = float(os.environ.get("RADAR_DECOUVERTE_PAUSE", "1.0"))

# Grappe de mots-cles de DEPLOIEMENT (FR + EN), en OR pour une requete large.
DECLENCHEURS_DEPLOIEMENT = (
    '("final investment decision" OR FID OR "nouvelle usine" OR "new plant" '
    'OR "opens plant" OR filiale OR subsidiary OR implantation OR "sets up" '
    'OR "invests in" OR investit OR concession OR "remporte le contrat" '
    'OR "signs contract" OR "mise en service" OR chantier)')

# Pays a risque a interroger : (nom pour la requete, ISO3, locale). On alterne
# FR et EN pour ne pas rater la presse locale/anglophone. Liste bornee par run
# via NB_PAYS_PAR_RUN. Etendre/reordonner librement.
PAYS_DECOUVERTE = [
    ("Mali", "MLI", "fr"), ("Niger", "NER", "fr"), ("Chad", "TCD", "en"),
    ("Burkina Faso", "BFA", "fr"), ("Nigeria", "NGA", "en"),
    ("Democratic Republic of Congo", "COD", "en"), ("Mozambique", "MOZ", "en"),
    ("Somalia", "SOM", "en"), ("South Sudan", "SSD", "en"),
    ("Libya", "LBY", "en"), ("Iraq", "IRQ", "en"), ("Yemen", "YEM", "en"),
    ("Central African Republic", "CAF", "en"), ("Cameroon", "CMR", "en"),
]
_LOCALES = {"fr": ("fr", "FR", "FR:fr"), "en": ("en", "US", "US:en")}


# ===========================================================================
# 1. REQUETES (pur)
# ===========================================================================
def url_pays(nom_pays, langue="fr"):
    """URL Google News RSS pour un pays x la grappe de deploiement."""
    hl, gl, ceid = _LOCALES.get(langue, _LOCALES["fr"])
    requete = '{} "{}"'.format(DECLENCHEURS_DEPLOIEMENT, nom_pays)
    return bitd.url_google_news("", requete_perso=requete, hl=hl, gl=gl, ceid=ceid)


def pays_du_run(curseur=0):
    """Fenetre de pays a interroger ce run (rotation simple sur NB_PAYS_PAR_RUN)."""
    n = len(PAYS_DECOUVERTE)
    if n == 0:
        return []
    debut = curseur % n
    ordonne = PAYS_DECOUVERTE[debut:] + PAYS_DECOUVERTE[:debut]
    return ordonne[:NB_PAYS_PAR_RUN]


# ===========================================================================
# 2. PRE-FILTRE DETERMINISTE (pur) -- protege le budget LLM
# ===========================================================================
def article_retenu(article, aujourd=None):
    """True si l'article merite un appel LLM : frais (fenetre serree) et pas un
    bruit evident. La mention du pays est garantie par la requete."""
    if not bitd.article_frais(article, aujourd=aujourd, jours=JOURS_FRAICHEUR):
        return False
    if bitd.bruit_evident(article):
        return False
    return True


def collecter_candidats(fetch=None, curseur=0, aujourd=None):
    """Pays x requetes -> articles candidats dedupliques (par lien). I/O tolerant.
    Chaque candidat porte son ISO3 de requete (le pays est connu par construction)."""
    vus, candidats = set(), []
    for nom_pays, iso3, langue in pays_du_run(curseur):
        url = url_pays(nom_pays, langue)
        try:
            if fetch is not None:
                xml = fetch(url)
            else:
                import requests
                xml = requests.get(url, timeout=30,
                                   headers={"User-Agent": "RadarAmarante/1.0"}).text
        except Exception:
            continue
        articles = bitd.parser_rss(xml or "")[:MAX_ARTICLES_PAR_REQUETE]
        for a in articles:
            if not article_retenu(a, aujourd=aujourd):
                continue
            cle = bitd.id_article(a.get("lien", ""))
            if not cle or cle in vus:
                continue
            vus.add(cle)
            a = dict(a)
            a["_iso3_requete"] = iso3
            a["_pays_requete"] = nom_pays
            candidats.append(a)
    return candidats


# ===========================================================================
# 3. EXTRACTION LLM (calquee sur bitd.PROMPT_SIGNAL, mais fait ressortir l'entreprise)
# ===========================================================================
PROMPT_DECOUVERTE = """Tu analyses une actualité pour une société de protection de personnes en zones à risque. Objectif : repérer une ENTREPRISE (quelconque, connue ou non) qui VA DÉPLOYER ou DÉPLOIE des personnels (cadres, techniciens, équipes projet) dans un PAYS À RISQUE, créant un besoin de sûreté (escorte, protection rapprochée, chauffeur sécurité).

Titre : {titre}
Extrait : {extrait}
Pays visé par la recherche : {pays}

Signal PERTINENT : décision d'investissement (FID), ouverture d'usine/filiale/bureau, contrat majeur, mise en service, chantier ou mission sur site dans ce pays à risque, avec une entreprise identifiable qui déploie des équipes.

Réponds UNIQUEMENT en JSON, sans texte autour :
{{"signal": true/false, "entreprise": "nom exact de l'entreprise qui déploie, ou vide", "iso3": "code ISO3 du pays ou vide", "pays": "nom du pays ou vide", "type_activite": "implantation|contrat_export|livraison_mise_en_service|essais_demonstration|formation_mco|chantier|autre", "imminence": "immediate|court_terme|indetermine", "confiance": 0.0, "resume": "une phrase factuelle"}}"""


def extraire_deploiement(article, appel=None):
    """Article -> extraction {signal, entreprise, iso3, ...}. `appel` injectable
    pour les tests. Repli ISO3 sur le pays de la requete si le LLM ne le donne pas."""
    prompt = PROMPT_DECOUVERTE.format(
        titre=article.get("titre", ""), extrait=article.get("resume", "")[:600],
        pays=article.get("_pays_requete", ""))
    lanceur = appel or (lambda p: bitd._appel_llm(p, ted.MODELE))
    extraction = bitd._parser_json(lanceur(prompt)) or {}
    if not extraction.get("iso3"):
        extraction["iso3"] = article.get("_iso3_requete", "")
    extraction["iso3"] = bitd.normaliser_iso3(extraction)
    return extraction


def _valide(extraction):
    """Filtre qualite : signal reel, entreprise nommee, confiance suffisante."""
    if not extraction.get("signal"):
        return False
    if len((extraction.get("entreprise") or "").strip()) < 3:
        return False
    try:
        return float(extraction.get("confiance") or 0) >= CONF_MIN
    except (TypeError, ValueError):
        return False


# ===========================================================================
# 4. MAPPING VERS UNE LIGNE prive_radar (reutilise scorer_signal + ligne_prive)
# ===========================================================================
def ligne_decouverte(article, extraction, modele=None):
    """Extraction validee -> ligne prive_radar, ou None (ISO3 hors suivi).
    Reutilise le scoring et le format d'ecriture des signaux prives."""
    sc = sp.scorer_signal(extraction, priorite_compte="decouverte",
                          iso3=extraction.get("iso3"))
    if sc is None:
        return None
    entreprise_row = {
        "entreprise": extraction.get("entreprise", "").strip(),
        "priorite_socle": "decouverte",
        "angle_contact": "Cible DECOUVERTE (hors watchlist) : direction sûreté / "
                         "export à qualifier.",
    }
    return bitd.ligne_prive(entreprise_row, article, extraction, sc, modele or ted.MODELE)


def analyser_candidats(candidats, appel=None, connues=None, budget=None):
    """Candidats -> lignes prive_radar. Saute les entreprises deja connues
    (watchlist) et respecte le budget LLM. Renvoie (lignes, nb_llm). Pur (appel
    et le set `connues` injectables)."""
    connues = {(_c or "").strip().lower() for _c in (connues or [])}
    budget = BUDGET_LLM if budget is None else budget
    lignes, evenements, nb_llm = [], set(), 0
    for art in candidats:
        if nb_llm >= budget:
            break
        nb_llm += 1
        extraction = extraire_deploiement(art, appel=appel)
        if not _valide(extraction):
            continue
        nom = extraction.get("entreprise", "").strip()
        if nom.lower() in connues:                # deja suivie -> pas une decouverte
            continue
        cle = bitd.clef_evenement(nom, extraction.get("pays", ""),
                                  extraction.get("type_activite", ""))
        if cle in evenements:
            continue
        evenements.add(cle)
        ligne = ligne_decouverte(art, extraction)
        if ligne:
            lignes.append(ligne)
    return lignes, nb_llm


# ===========================================================================
# 5. POINT D'ENTREE
# ===========================================================================
def _noms_watchlist(sheet_id, fichier_cs):
    """Noms deja suivis (watchlist) pour ne pas 'redecouvrir'. Best-effort."""
    try:
        wl = sp.lire_watchlist_multisecteurs(
            sp._ouvrir_classeur(sheet_id, fichier_cs).worksheet(
                sp.NOM_ONGLET_WATCHLIST).get_all_values())
        return [w.get("entreprise", "") for w in wl]
    except Exception:
        return []


def main():
    if not ACTIVER:
        print("(info) Decouverte desactivee (RADAR_DECOUVERTE=1 pour activer).")
        return
    if not ted.sortie_selon_sante_llm():
        print("(info) Decouverte : disjoncteur LLM ouvert, run saute.")
        return
    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier_cs = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    print("=== DECOUVERTE -- deploiements prives en zone a risque ===")
    curseur = int(os.environ.get("RADAR_DECOUVERTE_CURSEUR", "0"))
    candidats = collecter_candidats(curseur=curseur)
    print("  {} article(s) candidat(s) apres pre-filtre.".format(len(candidats)))
    connues = _noms_watchlist(sheet_id, fichier_cs) if (sheet_id and fichier_cs) else []
    lignes, nb_llm = analyser_candidats(candidats, connues=connues)
    print("  {} appel(s) LLM, {} nouvelle(s) cible(s) decouverte(s).".format(nb_llm, len(lignes)))
    if not (sheet_id and fichier_cs):
        print("(info) TED_SHEET_ID / GOOGLE_SERVICE_ACCOUNT_FILE absents : pas d'ecriture.")
        return
    if not lignes:
        print("  rien a ecrire ce run.")
        return
    _ecrire(sheet_id, fichier_cs, lignes)


def _ecrire(sheet_id, fichier_cs, lignes):
    """Append dans prive_radar (schema BITD) + miroir Postgres. Best-effort."""
    try:
        import radar_resilience
        classeur = radar_resilience.ouvrir_classeur(sheet_id, fichier_cs)
        feuille = classeur.worksheet(NOM_ONGLET)
        aug = [l + ["nouveau", datetime.date.today().isoformat()] for l in lignes]
        radar_resilience.avec_retry(
            lambda: feuille.append_rows(aug, value_input_option="RAW"), "decouverte append")
        print("  ecrit : {} ligne(s) dans {}.".format(len(lignes), NOM_ONGLET))
    except Exception as e:
        print("  (erreur) ecriture decouverte impossible : {}".format(e))
    try:
        import radar_stockage
        plates = [dict(zip(bitd.COLONNES_PRIVE, l)) for l in lignes]
        print("  (pg) " + radar_stockage.ecrire_miroir(NOM_ONGLET, plates))
    except Exception as e:
        print("  (pg) miroir indisponible ({})".format(e))


if __name__ == "__main__":
    main()
