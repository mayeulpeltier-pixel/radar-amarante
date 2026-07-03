# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- Moteur SIGNAUX PRIVES (refonte du module BITD).
=================================================================

Objectif : detecter le plus tot possible qu'une entreprise privee VA DEPLOYER
du personnel en zone a risque (donc un besoin de surete a venir), bien avant
tout appel d'offres. Remplace le module BITD (defense seule + GDELT).

CE QUI CHANGE PAR RAPPORT A BITD :
  1. WATCHLIST MULTI-SECTEURS : lit l'onglet 'watchlist_prives' (BTP, oil & gas,
     mines, ingenierie, agro, telecom, transport, luxe...) EN PLUS de la
     whitelist defense existante ('comptes_cibles_bitd').
  2. AUTO-ALIMENTATION MONDIALE : les titulaires gagnants du registre
     'attributions_radar' entrent automatiquement dans la veille (ils
     deploient deja). Couvre Ukraine, -stan, Sahel, etc. sans liste manuelle.
  3. NOUVELLE SOURCE ADZUNA (offres d'emploi) : une entreprise qui recrute
     pour un pays a risque = signal de deploiement fort. S'active des que
     ADZUNA_APP_ID / ADZUNA_APP_KEY sont presents.
  4. GDELT SUPPRIME (rate-limite, sans valeur, couteux en temps).

REUTILISATION : tout le moteur eprouve de bitd_signaux est reutilise tel quel
(Google News, fraicheur, anti-bruit, memoire, rotation, scoring, ecriture
Sheet dans 'prive_radar'). Seuls la watchlist, Adzuna et le prompt (neutre,
plus specifique defense) sont propres a ce module.

Ecrit dans le MEME onglet 'prive_radar' et le MEME schema que BITD : zero
impact sur le dashboard.

Interrupteurs :
  RADAR_SIGNAUX_PRIVES=0  -> desactive le moteur.
  ADZUNA_APP_ID / ADZUNA_APP_KEY -> activent la source offres d'emploi.
  RADAR_BITD_BUDGET       -> plafond d'analyses LLM par run (herite de BITD).
"""

import datetime
import email.utils
import os
import re
import time

import ted_complet_v14 as ted
import bitd_signaux as bitd


# ===========================================================================
# CONFIGURATION
# ===========================================================================
ACTIVER = os.environ.get("RADAR_SIGNAUX_PRIVES", "1") != "0"

# Anti rate-limit Google News (503) : pause entre entreprises + re-tentative.
PAUSE_ENTREPRISE = float(os.environ.get("RADAR_PRIVES_PAUSE", "0.7"))
PAUSE_REPLI = 2.5   # attente avant la 2e (et derniere) tentative sur 503

NOM_ONGLET_WATCHLIST = "watchlist_prives"     # nouvel onglet multi-secteurs
NOM_ONGLET_ATTRIBUTIONS = "attributions_radar"  # source d'auto-alimentation

ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY", "")
ADZUNA_ENDPOINT = "https://api.adzuna.com/v1/api/jobs/{}/search/1"
# Adzuna couvre surtout des pays developpes : on interroge les portails ou se
# trouvent les SIEGES qui recrutent pour l'etranger (France d'abord).
ADZUNA_PAYS = [p.strip() for p in os.environ.get("ADZUNA_PAYS", "fr,gb").split(",") if p.strip()]
ADZUNA_RESULTATS = 20

# Diagnostic : combien d'appels/offres/erreurs Adzuna sur le run (affiche a la
# fin). Permet de trancher : 0 appel = cles non lues ; appels mais 0 offre =
# actif mais rien trouve ; erreurs = probleme de cle/quota.
_ADZUNA_STATS = {"appels": 0, "offres": 0, "erreurs": 0}

# Noms de pays a risque (FR + quelques alias EN), pour reperer un signal de
# deploiement dans une offre d'emploi ou un article. Construit depuis la carte
# de risque du coeur (PAYS_ROUGE : nom_fr -> iso3).
def _noms_pays_risque():
    noms = set()
    for nom in getattr(ted, "PAYS_ROUGE", {}):
        n = str(nom).strip().lower()
        if len(n) >= 4:
            noms.add(n)
    # quelques alias anglais frequents dans les annonces
    noms.update(["ukraine", "mali", "niger", "nigeria", "chad", "sudan",
                 "south sudan", "somalia", "iraq", "libya", "yemen", "haiti",
                 "afghanistan", "burkina", "congo", "mozambique", "kazakhstan",
                 "uzbekistan", "tajikistan", "turkmenistan", "kyrgyzstan",
                 "moldova", "georgia", "armenia", "azerbaijan"])
    return noms

NOMS_PAYS_RISQUE = _noms_pays_risque()


def _mentionne_pays_risque(texte):
    """True si un pays a risque est cite dans le texte (signal de deploiement)."""
    t = " " + (texte or "").lower() + " "
    for nom in NOMS_PAYS_RISQUE:
        if nom in t:
            return True
    return False


# Formes juridiques quasi toujours associees a une PME purement locale (pas un
# prospect Amarante : pas de deploiement d'expatries). On NE bloque PAS les
# formes courantes de moyennes/grandes entreprises (GmbH, SAS, SA, Ltd, SpA,
# AG, BV, A/S, Inc) qui incluent de vrais prospects.
_FORMES_LOCALES = re.compile(
    r"\b(LLC|LLP|OOO|\u041e\u041e\u041e|TOV|\u0422\u041e\u0412|FOP|"
    r"S\.?\s?A\.?\s?de\s?C\.?\s?V\.?|Sp\.?\s*z\s*o\.?\s*o|"
    r"Additional Liability|Limited Liability Company|Private Enterprise)\b",
    re.I)


def _est_pme_locale(nom):
    """True si le nom porte une forme juridique de PME purement locale."""
    return bool(_FORMES_LOCALES.search(nom or ""))


# ===========================================================================
# PARTIE 1 -- WATCHLIST MULTI-SOURCES (le neuf)
# ===========================================================================

def _ouvrir_classeur(sheet_id, fichier_cs):
    import gspread
    from google.oauth2.service_account import Credentials
    portee = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = Credentials.from_service_account_file(fichier_cs, scopes=portee)
    return gspread.authorize(creds).open_by_key(sheet_id)


def lire_watchlist_multisecteurs(valeurs):
    """Transforme les lignes brutes de l'onglet 'watchlist_prives' en comptes.
    Colonnes attendues : entreprise, secteur, actif, requete_optionnelle.
    Ignore les lignes ou actif == 'non'."""
    if not valeurs or len(valeurs) < 2:
        return []
    entetes = [str(c).strip().lower() for c in valeurs[0]]
    def idx(nom):
        return entetes.index(nom) if nom in entetes else -1
    i_ent, i_sec = idx("entreprise"), idx("secteur")
    i_act, i_req = idx("actif"), idx("requete_optionnelle")
    comptes = []
    for row in valeurs[1:]:
        get = lambda i: (row[i].strip() if 0 <= i < len(row) else "")
        ent = get(i_ent)
        if not ent:
            continue
        if get(i_act).lower() == "non":
            continue
        comptes.append({
            "entreprise": ent,
            "secteur": get(i_sec) or "Autre",
            "requete_personnalisee": get(i_req),
            "priorite_socle": "moyenne",
            "angle_contact": "Entreprise deployant du personnel en zone a risque.",
        })
    return comptes


def seed_depuis_attributions(valeurs, max_comptes=150):
    """Auto-alimentation : chaque titulaire gagnant du registre d'attributions
    devient un compte a surveiller (il deploie deja). Couvre TOUTE zone a
    risque sans liste manuelle. Ignore les '(gagnant non publie)'."""
    if not valeurs or len(valeurs) < 2:
        return []
    entetes = [str(c).strip().lower() for c in valeurs[0]]
    if "gagnant" not in entetes:
        return []
    ig = entetes.index("gagnant")
    ipays = entetes.index("pays_execution") if "pays_execution" in entetes else -1
    vus, comptes = set(), []
    for row in valeurs[1:]:
        brut = row[ig].strip() if ig < len(row) else ""
        if not brut or "non publie" in brut.lower():
            continue
        pays = (row[ipays].strip() if 0 <= ipays < len(row) else "")
        # un marche peut lister plusieurs gagnants separes par ';'
        for nom in brut.split(";"):
            nom = nom.strip()
            cle = nom.lower()
            if len(nom) < 3 or cle in vus:
                continue
            if _est_pme_locale(nom):     # PME purement locale -> pas un prospect
                continue
            vus.add(cle)
            comptes.append({
                "entreprise": nom,
                "secteur": "Attributaire (marche gagne)",
                "requete_personnalisee": "",
                "priorite_socle": "haute",   # deploiement en cours = prioritaire
                "angle_contact": "Titulaire d'un marche en zone a risque{} : deploiement en cours.".format(
                    " (" + pays + ")" if pays else ""),
            })
            if len(comptes) >= max_comptes:
                return comptes
    return comptes


def construire_watchlist(sheet_id, fichier_cs):
    """Fusionne : watchlist multi-secteurs + whitelist defense existante +
    titulaires d'attributions. Dedup par nom (insensible a la casse).
    La 1re occurrence gagne (watchlist curee prioritaire sur l'auto-seed)."""
    comptes = []
    if sheet_id and fichier_cs:
        try:
            classeur = _ouvrir_classeur(sheet_id, fichier_cs)
        except Exception as e:
            print("  (info) Sheet illisible ({}). Moteur prive inactif.".format(e))
            return []
        # 1. Watchlist multi-secteurs (curee)
        try:
            v = classeur.worksheet(NOM_ONGLET_WATCHLIST).get_all_values()
            comptes += lire_watchlist_multisecteurs(v)
        except Exception:
            print("  (info) Onglet '{}' absent : ajoute-le pour la veille multi-secteurs.".format(
                NOM_ONGLET_WATCHLIST))
        # 2. Whitelist defense existante (conventions d'origine conservees)
        try:
            v = classeur.worksheet(bitd.NOM_ONGLET_WHITELIST).get_all_values()
            for d in bitd._whitelist_depuis_valeurs(v):
                d.setdefault("secteur", "Defense")
                comptes.append(d)
        except Exception:
            pass
        # 3. Auto-alimentation par les attributions (couverture mondiale)
        try:
            v = classeur.worksheet(NOM_ONGLET_ATTRIBUTIONS).get_all_values()
            comptes += seed_depuis_attributions(v)
        except Exception:
            pass

    # Dedup par nom, 1re occurrence prioritaire.
    vus, uniques = set(), []
    for c in comptes:
        cle = c.get("entreprise", "").strip().lower()
        if cle and cle not in vus:
            vus.add(cle)
            uniques.append(c)
    return uniques


# ===========================================================================
# PARTIE 2 -- SOURCE ADZUNA (offres d'emploi = signal de deploiement)
# ===========================================================================

def _iso_vers_rfc822(iso):
    """'2026-06-15T12:00:00Z' -> date RFC822 (lisible par bitd.article_frais)."""
    s = (iso or "").strip().replace("Z", "+00:00")
    try:
        return email.utils.format_datetime(datetime.datetime.fromisoformat(s))
    except Exception:
        return ""


def collecter_adzuna(entreprise, fetch=None, session=None):
    """Offres d'emploi mentionnant un pays a risque pour cette entreprise.
    Interroge les portails Adzuna des pays sieges (France, UK...). Renvoie des
    'articles' au meme format que Google News (titre/lien/date/resume), pour
    passer dans le meme pipeline d'analyse.

    `fetch` injectable pour tests : callable(pays, params) -> dict JSON.
    Sans cle et sans fetch : renvoie [] (source simplement inactive)."""
    if fetch is None and not (ADZUNA_APP_ID and ADZUNA_APP_KEY):
        return []
    session = session or ted.session_robuste()
    articles = []
    for pays in ADZUNA_PAYS:
        params = {
            "app_id": ADZUNA_APP_ID, "app_key": ADZUNA_APP_KEY,
            "what_phrase": entreprise, "results_per_page": ADZUNA_RESULTATS,
            "content-type": "application/json",
        }
        _ADZUNA_STATS["appels"] += 1
        try:
            if fetch is not None:
                data = fetch(pays, params)
            else:
                rep = session.get(ADZUNA_ENDPOINT.format(pays), params=params, timeout=20)
                if rep.status_code >= 400:
                    _ADZUNA_STATS["erreurs"] += 1
                    detail = ""
                    try:
                        detail = rep.json().get("exception") or rep.text[:200]
                    except Exception:
                        detail = rep.text[:200]
                    print("  (info) Adzuna {} : HTTP {} -- {}".format(pays, rep.status_code, detail))
                    continue
                data = rep.json()
        except Exception as e:
            _ADZUNA_STATS["erreurs"] += 1
            print("  (info) Adzuna {} indisponible ({}).".format(pays, e))
            continue

        for job in (data or {}).get("results", []):
            titre = (job.get("title") or "").strip()
            desc = (job.get("description") or "").strip()
            lieu = ((job.get("location") or {}).get("display_name") or "")
            zones = " ".join((job.get("location") or {}).get("area", []) or [])
            societe = ((job.get("company") or {}).get("display_name") or "")
            texte = " ".join([titre, desc, lieu, zones])
            # On ne garde que les offres evoquant un pays a risque (= deploiement).
            if not _mentionne_pays_risque(texte):
                continue
            _ADZUNA_STATS["offres"] += 1
            articles.append({
                "titre": "[Offre d'emploi] {} - {}".format(titre, lieu),
                "lien": job.get("redirect_url") or job.get("id", ""),
                "date": _iso_vers_rfc822(job.get("created", "")),
                "resume": "{}. Recruteur : {}. Lieu : {} {}. {}".format(
                    titre, societe or entreprise, lieu, zones, desc)[:800],
            })
    return articles


# ===========================================================================
# PARTIE 3 -- PROMPT NEUTRE (tous secteurs, meme schema que BITD)
# ===========================================================================
# Meme schema de sortie que bitd.PROMPT_SIGNAL pour reutiliser bitd.scorer_signal.
PROMPT_NEUTRE = """Tu analyses une actualité ou une offre d'emploi concernant une entreprise privée, pour une société française de protection de personnes en zones à risque. Objectif : repérer si l'entreprise VA DÉPLOYER ou DÉPLOIE DÉJÀ des personnels (cadres, experts, techniciens, chefs de projet, équipes) DANS UN PAYS À RISQUE, ce qui crée un besoin probable de sûreté (escorte, protection rapprochée, chauffeur sécurité, sécurisation de déplacements).

Secteur de l'entreprise : {secteur}
Entreprise : {entreprise}
Titre : {titre}
Contenu (peut être tronqué) : {resume}

Sont des signaux : ouverture de site/filiale/chantier, contrat ou marché à l'étranger, recrutement d'un poste basé ou déployé dans un pays à risque, mission d'assistance technique, livraison/mise en service sur place, exploration minière/pétrolière, salon ou implantation à l'étranger.
NE sont PAS des signaux : résultats financiers, nominations internes, produits sans déploiement, actualité 100% domestique, poste télétravail/siège sans terrain, rumeur vague.

IMPORTANT sur la certitude : une simple INTENTION, ANNONCE, lettre d'intention ou PROJET (mots comme "intention", "envisage", "pourrait", "projette", "en discussion", "vraisemblablement") SANS présence physique confirmée ou datée => confiance FAIBLE (0.3 à 0.55) ET imminence "indetermine". Ne réserve "immediate" ou "court_terme" qu'aux déploiements confirmés ou déjà en cours sur le terrain.

Sois STRICT : dans le doute, signal=false. La confiance reflète ta certitude d'un vrai déploiement de personnel dans un pays à risque.

Réponds UNIQUEMENT en JSON valide, sans texte autour :
{{"signal": true/false, "iso3": "code ISO3 du pays de déploiement ou vide", "pays": "nom du pays ou vide", "type_activite": "formation_mco|implantation|essais_demonstration|livraison_mise_en_service|recrutement_local|incident|contrat_export|autre", "imminence": "immediate|court_terme|indetermine", "confiance": 0.0, "resume": "une phrase factuelle"}}"""


def analyser(entreprise, secteur, article, appel=None):
    """Tri LLM (Haiku) avec le prompt neutre. Meme schema de sortie que BITD,
    donc bitd.scorer_signal / normaliser_iso3 s'appliquent tels quels."""
    prompt = PROMPT_NEUTRE.format(
        secteur=secteur or "non precise", entreprise=entreprise,
        titre=article.get("titre", ""),
        resume=(article.get("resume") or article.get("titre", ""))[:800])
    try:
        appel_reel = appel or (lambda p: bitd._appel_llm(p, ted.MODELE))
        return bitd._parser_json(appel_reel(prompt))
    except Exception as e:
        print("  (info) Analyse LLM echouee ({}).".format(e))
        return None


# ===========================================================================
# SCORING CORRIGE (commercial vraiment variable + garde-fou confiance)
# ===========================================================================
# Bug constate : le score commercial etait fige a 8/10 (poids priorite mal
# calibres). On le rend variable et on ajoute un garde-fou : un signal peu sur
# ne peut pas etre promu en "contacter". Reutilise les autres poids de BITD.
POIDS_PRIORITE_V2 = {"haute": 0.8, "moyenne": 0.5, "basse": 0.3}
CONF_MIN_CONTACTER = 0.6   # sous ce niveau, action plafonnee a "surveiller"


def scorer_signal(extraction, priorite_compte, iso3=None):
    iso3 = (iso3 or extraction.get("iso3") or "").strip().upper()
    if not iso3 or iso3 not in ted.CODES_PAYS_SUIVIS:
        return None
    poids_zone = ted.MULTIPLICATEUR_ZONE.get(iso3, 0.3)
    poids_act = bitd.POIDS_ACTIVITE.get(extraction.get("type_activite"), 0.25)
    poids_prio = POIDS_PRIORITE_V2.get((priorite_compte or "").strip().lower(), 0.5)
    poids_imm = bitd.POIDS_IMMINENCE.get(extraction.get("imminence"), 0.7)
    surete = round(10 * poids_zone * poids_act, 1)
    commercial = round(10 * poids_prio, 1)
    final = round((0.6 * surete + 0.4 * commercial) * poids_imm, 1)
    action = ("contacter" if final >= bitd.SEUIL_CONTACTER
              else "surveiller" if final >= bitd.SEUIL_SURVEILLER else "ignorer")
    # Garde-fou confiance : un signal peu sur ne monte jamais en "contacter".
    try:
        conf = float(extraction.get("confiance") or 0)
    except (TypeError, ValueError):
        conf = 0
    if action == "contacter" and conf < CONF_MIN_CONTACTER:
        action = "surveiller"
    paire = bitd.ZONE_PAR_ISO3.get(iso3)
    if isinstance(paire, (tuple, list)) and len(paire) >= 2:
        nom_fr, zone = paire[0], paire[1]
    else:
        nom_fr, zone = (extraction.get("pays") or ""), "Non classe"
    return {"final": final, "surete": surete, "commercial": commercial,
            "zone": zone, "action": action, "nom": nom_fr}


# ===========================================================================
# PARTIE 4 -- TRAITEMENT D'UNE ENTREPRISE (Google News + Adzuna)
# ===========================================================================

def collecter_news(entreprise, requete="", session=None):
    """Google News RSS avec repli : sur 503 (rate-limit), une seule nouvelle
    tentative apres pause, sinon on passe sans insister. Reutilise l'URL et le
    parseur de BITD."""
    session = session or ted.session_robuste()
    url = bitd.url_google_news(entreprise, requete)
    for tentative in range(2):
        try:
            rep = session.get(url, timeout=30)
            if rep.status_code == 503:
                if tentative == 0:
                    time.sleep(PAUSE_REPLI)
                    continue
                print("  (info) Google News sature (503) pour {} : ignore ce run.".format(entreprise))
                return []
            rep.raise_for_status()
            return bitd.parser_rss(rep.text)[:bitd.MAX_ARTICLES_PAR_ENTREPRISE]
        except Exception as e:
            if tentative == 0:
                time.sleep(PAUSE_REPLI)
                continue
            print("  (info) Flux indisponible pour {} ({}).".format(entreprise, str(e)[:70]))
            return []
    return []


def collecter_signaux(entreprise, requete, session=None, fetch_adzuna=None):
    """Fusionne Google News (resilient) + Adzuna, dedup par URL."""
    articles = collecter_news(entreprise, requete, session=session)
    articles += collecter_adzuna(entreprise, fetch=fetch_adzuna, session=session)
    vus, uniques = set(), []
    for a in articles:
        k = bitd.id_article(a.get("lien", ""))
        if k and k not in vus:
            vus.add(k)
            uniques.append(a)
    return uniques


def traiter_entreprise(compte, deja_vus, cles_existantes, appel=None,
                       appel_verif=None, session=None, budget=None,
                       vus_ce_run=None, fetch_adzuna=None):
    """Renvoie les signaux retenus pour une entreprise (dedup par evenement).
    Reutilise le scoring, la verification Sonnet et la memoire de BITD."""
    entreprise = compte.get("entreprise", "").strip()
    if not entreprise:
        return []
    secteur = compte.get("secteur", "")
    requete = compte.get("requete_personnalisee", "")
    retenus = {}

    for article in collecter_signaux(entreprise, requete, session=session,
                                     fetch_adzuna=fetch_adzuna):
        if not bitd.article_frais(article):
            continue
        pub = bitd.id_article(article.get("lien", ""))
        if pub in deja_vus:
            continue
        if bitd.bruit_evident(article):
            deja_vus.add(pub)
            if vus_ce_run is not None:
                vus_ce_run.add(pub)
            continue
        if budget is not None:
            if budget.get("reste", 0) <= 0:
                break
            budget["reste"] -= 1
        if vus_ce_run is not None:
            vus_ce_run.add(pub)
        deja_vus.add(pub)

        extraction = analyser(entreprise, secteur, article, appel=appel)
        if not extraction or not extraction.get("signal"):
            continue
        iso3 = bitd.normaliser_iso3(extraction)
        extraction["iso3"] = iso3
        sc = scorer_signal(extraction, compte.get("priorite_socle"), iso3=iso3)
        if not sc or sc["action"] == "ignorer":
            continue

        # Escalade Sonnet sur signaux a fort enjeu (reutilise BITD).
        if sc["final"] >= bitd.SEUIL_CONTACTER or float(extraction.get("confiance") or 0) < 0.7:
            verif = bitd.verifier_signal_sonnet(entreprise, article, appel=appel_verif)
            if verif is not None:
                if not verif.get("signal"):
                    continue
                iso3 = bitd.normaliser_iso3(verif)
                verif["iso3"] = iso3
                sc2 = scorer_signal(verif, compte.get("priorite_socle"), iso3=iso3)
                if not sc2 or sc2["action"] == "ignorer":
                    continue
                extraction, sc = verif, sc2

        cle = bitd.clef_evenement(entreprise, sc["nom"], extraction.get("type_activite"))
        if cle in cles_existantes:
            continue
        modele = ted.MODELE_RAFFINEMENT if sc["final"] >= bitd.SEUIL_CONTACTER else ted.MODELE
        ligne = bitd.ligne_prive(compte, article, extraction, sc, modele)
        # on garde le meilleur signal par evenement
        if cle not in retenus or sc["final"] > retenus[cle]["final"]:
            retenus[cle] = {"final": sc["final"], "ligne": ligne, "sc": sc,
                            "entreprise": entreprise}
    return list(retenus.values())


# ===========================================================================
# PARTIE 5 -- POINT D'ENTREE
# ===========================================================================

def main():
    if not ACTIVER:
        print("(info) Moteur signaux prives desactive (RADAR_SIGNAUX_PRIVES=0).")
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERREUR : ANTHROPIC_API_KEY absente.")
        return

    print("=" * 60)
    print("MOTEUR SIGNAUX PRIVES (multi-secteurs) - Radar Amarante")
    print("=" * 60)
    if not (ADZUNA_APP_ID and ADZUNA_APP_KEY):
        print("(info) Adzuna inactif (ADZUNA_APP_ID / ADZUNA_APP_KEY absents) : "
              "Google News + attributions seulement.")

    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    comptes = construire_watchlist(sheet_id, fichier)
    if not comptes:
        print("Watchlist vide. Rien a faire (verifie l'onglet '{}').".format(NOM_ONGLET_WATCHLIST))
        return
    print("Watchlist : {} entreprises (multi-secteurs + defense + attributaires).".format(len(comptes)))

    # Memoire + rotation (reutilise BITD).
    deja_vus = bitd.charger_vus(sheet_id, fichier)
    cles_existantes = bitd.cles_evenements_existantes(sheet_id, fichier)
    curseur = bitd.lire_curseur(sheet_id, fichier)

    # Fenetre de rotation : on borne le nombre d'ENTREPRISES par run (le reseau
    # est le facteur limitant). Priorite : watchlist curee (majors deliberement
    # choisis, forts deployeurs) d'abord, attributaires ensuite (couverts par la
    # rotation sur les runs suivants).
    comptes.sort(key=lambda c: 1 if str(c.get("secteur", "")).startswith("Attributaire") else 0)
    taille_fenetre = int(os.environ.get("RADAR_PRIVES_ENTREPRISES", "20"))
    n = len(comptes)
    debut = curseur % n if n else 0
    fenetre = [comptes[(debut + i) % n] for i in range(min(taille_fenetre, n))]
    prochain = (debut + len(fenetre)) % n

    budget = {"reste": bitd.MAX_ANALYSES_PAR_RUN}
    vus_ce_run = set()
    t0 = time.time()
    resultats = []
    for i, compte in enumerate(fenetre, start=1):
        signaux = traiter_entreprise(
            compte, deja_vus, cles_existantes, session=None,
            budget=budget, vus_ce_run=vus_ce_run)
        for s in signaux:
            cles_existantes.add(bitd.clef_evenement(
                s["entreprise"], s["sc"]["nom"], ""))  # anti-doublon intra-run
        resultats += signaux
        etat = "{} signal(aux)".format(len(signaux)) if signaux else "0 signal"
        print("  [{:>3}/{}] {:34} : {}".format(i, len(fenetre), compte["entreprise"][:34], etat))
        if budget["reste"] <= 0:
            print("  (budget d'analyses epuise, on s'arrete proprement)")
            break
        time.sleep(PAUSE_ENTREPRISE)   # respiration anti rate-limit Google News

    print("Temps moteur : {:.0f}s. Signaux retenus : {}. Prochain curseur : {}.".format(
        time.time() - t0, len(resultats), prochain))
    if ADZUNA_APP_ID and ADZUNA_APP_KEY:
        print("Adzuna : {appels} appel(s), {offres} offre(s) zone risque, "
              "{erreurs} erreur(s).".format(**_ADZUNA_STATS))
    else:
        print("Adzuna : inactif (ADZUNA_APP_ID / ADZUNA_APP_KEY absents).")

    # Ecriture + persistance memoire/curseur (reutilise BITD).
    if sheet_id and fichier:
        try:
            import gspread
            from google.oauth2.service_account import Credentials
            creds = Credentials.from_service_account_file(
                fichier, scopes=["https://www.googleapis.com/auth/spreadsheets"])
            classeur_rw = gspread.authorize(creds).open_by_key(sheet_id)
            feuille = bitd.ouvrir_ou_creer_onglet(classeur_rw)
            n_ecrits = bitd.ecrire_resultats(feuille, resultats)
            print("-> {} signal(aux) ecrit(s) dans '{}'.".format(n_ecrits, bitd.NOM_ONGLET_PRIVE))
            bitd.persister_vus(classeur_rw, vus_ce_run)
            bitd.ecrire_curseur(classeur_rw, prochain)
        except Exception as e:
            print("ERREUR ecriture Sheet : {}".format(e))
    else:
        print("(dry-run : pas de Sheet configure)")


if __name__ == "__main__":
    main()
