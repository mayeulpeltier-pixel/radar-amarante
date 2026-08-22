# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE PROJECT INTELLIGENCE (jetable, diagnostic)
===============================================================================

LA QUESTION, ET RIEN D'AUTRE
----------------------------
Avant d'ecrire la moindre ligne de `projets.py`, on mesure si la matiere
premiere EXISTE reellement. Trois questions, une reponse chiffree :

  Q1. DETECTABILITE : avec une grille de mots-cles CYCLE DE VIE (et non plus
      "deploiement"), Google News RSS remonte-t-il des signaux sur INGA 3 (RDC)
      et TANZANIA LNG (TZA) ?
  Q2. RATTACHEMENT  : ces signaux se regroupent-ils sous UN projet unique via
      une resolution d'alias ("Inga 3" = "Grand Inga" = "Inga III") ?
  Q3. COUT          : quel volume de bruit faudrait-il faire passer au LLM ?
      (c'est ce qui decidera du pre-filtre deterministe)

Elle mesure AUSSI la profondeur temporelle atteignable : le plus ancien signal
retrouve. C'est le coeur du sujet ("detecter des mois ou des annees avant l'AO").

CE QU'ELLE NE FAIT PAS
----------------------
Aucune ecriture (Sheet, Postgres, etat), AUCUN appel LLM (donc cout zero),
aucune modification de collecteur. Lecture seule, sortie code 0. Jetable : a
supprimer une fois le verdict rendu.

RAPPEL DE L'AUDIT (ce que la sonde va confronter au reel)
---------------------------------------------------------
  - TZA est HORS PERIMETRE de collecte (dans_le_perimetre('TZA') = False) et
    absent de PAYS_DECOUVERTE : aujourd'hui, zero signal tanzanien entre.
  - COD est dans le perimetre, mais la grille DECLENCHEURS_DEPLOIEMENT ignore
    le vocabulaire projet (MoU, feasibility, consultant selected, financial
    close, EPC...), et le prompt LLM exige une entreprise qui deploie -- ce qui
    elimine par construction les signaux gouvernementaux et financiers.
La sonde teste donc la grille CIBLE, pas la grille actuelle : elle repond
"est-ce que ca marcherait si on changeait", pas "est-ce que ca marche".

USAGE
-----
    python sonde_projets.py                 # les deux cas tests
    SONDE_CAS=inga python sonde_projets.py  # un seul (inga | tanzanie)
    SONDE_JOURS=540 python sonde_projets.py # profondeur demandee (defaut 365)
"""

import collections
import os
import re
import sys
import time
import unicodedata

import bitd_signaux as bitd


JOURS = int(os.environ.get("SONDE_JOURS", "365"))
MAX_ART = int(os.environ.get("SONDE_MAX_ART", "40"))
PAUSE = float(os.environ.get("SONDE_PAUSE", "1.0"))


# ===========================================================================
# 1. GRILLE CYCLE DE VIE (le coeur de la proposition, teste ici avant d'etre
#    cablee dans un collecteur). Chaque phase porte ses expressions FR + EN.
# ===========================================================================
PHASES = collections.OrderedDict([
    ("POLITICAL_ANNOUNCEMENT", [
        "government approves", "cabinet approves", "conseil des ministres",
        "presidential", "annonce du gouvernement", "state visit", "approuve le projet"]),
    ("MOU", [
        "memorandum of understanding", "signs mou", "signe un protocole",
        "protocole d'accord", "signs agreement", "accord de principe"]),
    ("FEASIBILITY", [
        "feasibility study", "pre-feasibility", "etude de faisabilite",
        "technical study", "etudes techniques", "master plan"]),
    ("GOVERNMENT_AGREEMENT", [
        "host government agreement", "concession agreement",
        "investment agreement", "convention de concession", "hga"]),
    ("FUNDING", [
        "financing agreement", "funding approved", "approves loan", "credit approved",
        "financement approuve", "accord de financement", "financial close",
        "bouclage financier"]),
    ("CONSULTANT_SELECTION", [
        "consultant appointed", "consultant selected", "selected as consultant",
        "consultant retenu", "cabinet retenu", "awarded the study",
        "preferred bidder", "adjudicataire pressenti"]),
    ("FEED_PREFID", [
        "front-end engineering", "feed contract", "pre-fid", "feed"]),
    ("FID", [
        "final investment decision", "fid", "decision finale d'investissement"]),
    ("EPC", [
        "epc contract", "epc contractor", "engineering procurement construction",
        "contrat epc", "tender for construction", "appel d'offres travaux"]),
    ("CONSTRUCTION", [
        "construction expected to begin", "construction scheduled", "groundbreaking",
        "pose de la premiere pierre", "debut des travaux", "construction begins"]),
])

# Index inverse : expression -> phase (pour classer un article).
_EXPR_PHASE = []
for _ph, _exprs in PHASES.items():
    for _e in _exprs:
        _EXPR_PHASE.append((_e, _ph))
# Les expressions longues d'abord (evite que "feed" masque "feed contract").
_EXPR_PHASE.sort(key=lambda x: -len(x[0]))

# Motifs REGEX, evalues APRES les expressions litterales. Ils captent ce
# qu'une liste de mots ne peut pas : les annonces de financement chiffrees.
# Trouve par les tests : "World Bank approves $250m for Inga 3" n'etait capte
# par AUCUNE expression ("approves loan" ne matche pas "approves $250m").
MOTIFS_PHASE = [
    # "AECOM selected for Inga studies" : la tournure reelle de la presse, que
    # "consultant selected" ne captait pas. On exige un mot de metier proche
    # pour ne pas avaler "selected for construction" (qui releve de l'EPC).
    (re.compile(r"(?:selected|appointed|chosen)\s+(?:as|for)\s+"
                r"[^.]{0,40}(?:stud|design|engineering|consult|feasibilit)"),
     "CONSULTANT_SELECTION"),
    (re.compile(r"retenu[e]?\s+pour\s+[^.]{0,40}(?:etude|ingenierie|conseil)"),
     "CONSULTANT_SELECTION"),
    (re.compile(r"approves?\s+(?:us)?\$?\s?\d"), "FUNDING"),
    (re.compile(r"\$\s?\d[\d.,]*\s?(?:m|bn|million|billion)?\s+(?:for|to)\b"), "FUNDING"),
    (re.compile(r"(?:pret|prêt|financement)\s+de\s+\d"), "FUNDING"),
    (re.compile(r"\b(?:loan|grant|credit)\s+of\s+(?:us)?\$?\s?\d"), "FUNDING"),
]


# ===========================================================================
# 2. CAS TESTS : alias du projet (resolution d'entite PROJET, la brique qui
#    manque aujourd'hui) + acteurs attendus, pour mesurer le rattachement.
# ===========================================================================
CAS = collections.OrderedDict([
    ("inga", {
        "project_id": "INGA3_COD",
        "libelle": "Inga 3 / Grand Inga (RDC)",
        "pays": "Democratic Republic of Congo",
        "iso3": "COD",
        "alias": ["inga 3", "inga iii", "grand inga", "inga hydropower",
                  "barrage inga", "inga dam", "site d'inga"],
        # Alias FAIBLE : "Inga" seul est ambigu (prenom scandinave). On ne le
        # retient QUE s'il est accompagne d'un mot de contexte projet. Sans
        # cette regle, "AECOM selected for Inga studies" -- l'article pivot du
        # cahier des charges -- n'etait rattache a AUCUN projet.
        "alias_faibles": ["inga"],
        "acteurs": ["aecom", "world bank", "banque mondiale", "afd", "adpi",
                    "eskom", "sinohydro", "actis", "fortescue"],
        "locales": ["fr", "en"],
    }),
    ("tanzanie", {
        "project_id": "TANZLNG_TZA",
        "libelle": "Tanzania LNG / Lindi LNG (Tanzanie)",
        "pays": "Tanzania",
        "iso3": "TZA",
        "alias": ["tanzania lng", "lindi lng", "tanzania liquefied",
                  "lng terminal tanzania", "lng project tanzania", "likong'o"],
        "alias_faibles": ["lng"],   # ambigu seul -> exige un mot de contexte
        "acteurs": ["shell", "equinor", "exxonmobil", "exxon", "tpdc",
                    "ophir", "pavilion energy"],
        "locales": ["en", "sw"],
    }),
])

_LOCALES = {"fr": ("fr", "FR", "FR:fr"), "en": ("en", "US", "US:en"),
            "sw": ("en", "TZ", "TZ:en")}


def _norm(txt):
    """Minuscules, sans accents : base de tout rapprochement textuel."""
    t = unicodedata.normalize("NFD", str(txt or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


# ===========================================================================
# 3. COEUR TESTABLE (fonctions PURES, validees offline)
# ===========================================================================
# Mots qui prouvent qu'un alias faible parle bien d'un PROJET (et non d'un
# homonyme). Volontairement larges : le role du garde-fou est d'ecarter le
# hors-sujet evident, pas de filtrer finement.
CONTEXTE_PROJET = [
    "project", "projet", "dam", "barrage", "hydro", "hydropower", "hydroelectric",
    "study", "studies", "etude", "power", "electricity", "electrification",
    "plant", "usine", "terminal", "pipeline", "construction", "epc",
    "investment", "financing", "financement", "consortium", "gas", "gaz",
]


def phase_de_article(article):
    """Phase du cycle de vie suggeree par un article, ou '' si rien ne matche.
    Litteraux d'abord (les plus longs), puis motifs regex. Fonction PURE."""
    texte = _norm(article.get("titre", "") + " " + article.get("resume", ""))
    for expr, phase in _EXPR_PHASE:
        motif = (r"(?<![a-z0-9])" + re.escape(_norm(expr)) + r"(?![a-z0-9])")
        if re.search(motif, texte):
            return phase
    for motif, phase in MOTIFS_PHASE:
        if motif.search(texte):
            return phase
    return ""


def rattache_au_projet(article, cas):
    """True si l'article parle bien du projet : soit un alias FORT, soit un
    alias FAIBLE accompagne d'un mot de contexte projet. C'est la brique de
    resolution d'entite PROJET qui manque aujourd'hui. Fonction PURE."""
    texte = _norm(article.get("titre", "") + " " + article.get("resume", ""))
    if any(_norm(a) in texte for a in cas["alias"]):
        return True
    faibles = cas.get("alias_faibles") or []
    if not faibles:
        return False
    presence = any(
        re.search(r"(?<![a-z0-9])" + re.escape(_norm(a)) + r"(?![a-z0-9])", texte)
        for a in faibles)
    return presence and any(c in texte for c in CONTEXTE_PROJET)


def acteurs_cites(article, cas):
    """Acteurs connus du projet cites dans l'article (lien PROJET -> ENTREPRISE
    de la couche 5). Fonction PURE."""
    texte = _norm(article.get("titre", "") + " " + article.get("resume", ""))
    return sorted({a for a in cas["acteurs"] if _norm(a) in texte})


def age_jours(article, aujourd=None):
    """Age de l'article en jours, ou None si date illisible. Fonction PURE."""
    import datetime
    import email.utils
    brut = article.get("date", "")
    if not brut:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(brut)
        if dt is None:
            return None
        ref = aujourd or datetime.datetime.now(dt.tzinfo)
        return (ref - dt).days
    except Exception:
        return None


def analyser(articles, cas, aujourd=None):
    """Agrege un lot d'articles pour un cas test. Fonction PURE.
    Retour : compteurs, phases vues, acteurs vus, profondeur temporelle."""
    vus, retenus, bruit = set(), [], 0
    phases = collections.Counter()
    acteurs = collections.Counter()
    plus_ancien, plus_recent = None, None
    for a in articles:
        lien = a.get("lien", "")
        if lien in vus:
            continue
        vus.add(lien)
        if not rattache_au_projet(a, cas):
            bruit += 1
            continue
        ph = phase_de_article(a)
        if ph:
            phases[ph] += 1
        for ac in acteurs_cites(a, cas):
            acteurs[ac] += 1
        j = age_jours(a, aujourd=aujourd)
        if j is not None:
            plus_ancien = j if plus_ancien is None else max(plus_ancien, j)
            plus_recent = j if plus_recent is None else min(plus_recent, j)
        retenus.append({"titre": a.get("titre", ""), "phase": ph, "age": j})
    return {"total": len(vus), "retenus": retenus, "bruit": bruit,
            "phases": phases, "acteurs": acteurs,
            "plus_ancien": plus_ancien, "plus_recent": plus_recent}


def verdict(rap, erreurs=None):
    """Traduit les mesures en verdict de faisabilite. Fonction PURE.

    GARDE-FOU (lecon de la sonde ADB, qui avait conclu ABSENT sur n=0) : un
    echantillon VIDE ne prouve rien s'il vient d'un echec de collecte. On
    distingue donc "rien trouve" (verdict) de "rien collecte" (non conclu)."""
    n = len(rap["retenus"])
    if rap["total"] == 0:
        if erreurs:
            return "NON CONCLU", ("aucun article collecte ({} requete(s) en "
                                  "erreur) : ce n'est PAS un verdict".format(len(erreurs)))
        return "NON CONCLU", "aucun article collecte : ce n'est PAS un verdict"
    if n == 0:
        return "INFAISABLE", "aucun signal rattachable : Google News RSS ne suffit pas"
    nph = len(rap["phases"])
    if n >= 5 and nph >= 3:
        return "FAISABLE", "{} signaux sur {} phases distinctes".format(n, nph)
    if n >= 3:
        return "PARTIEL", "{} signaux, {} phase(s) : matiere mince".format(n, nph)
    return "FAIBLE", "{} signal(aux) seulement".format(n)


# ===========================================================================
# 4. COLLECTE (I/O, live only -- injectable pour les tests)
# ===========================================================================
def requetes_du_cas(cas):
    """Requetes Google News a lancer : les alias du projet, croises avec la
    grille cycle de vie condensee. On interroge le PROJET, pas l'entreprise :
    c'est tout le changement de doctrine."""
    cycle = ('("feasibility study" OR "memorandum of understanding" OR MoU OR '
             '"financing agreement" OR "funding approved" OR "consultant selected" OR '
             '"final investment decision" OR FID OR "EPC contract" OR '
             '"financial close" OR "government approves" OR construction)')
    out = []
    for alias in cas["alias"][:4]:
        out.append('"{}"'.format(alias))                 # large : tout sur le projet
    out.append('"{}" {}'.format(cas["alias"][0], cycle))  # cible : projet x cycle
    return out


def collecter(cas, fetch=None, session=None):
    """Articles bruts pour un cas test, toutes locales. I/O tolerant : une
    requete qui echoue n'interrompt pas les autres."""
    if fetch is None:
        import requests
        sess = session or requests.Session()

        def fetch(url):
            rep = sess.get(url, timeout=30, headers={
                "User-Agent": "Mozilla/5.0 (compatible; RadarAmaranteSonde/1.0)"})
            rep.raise_for_status()
            return rep.text

    articles, erreurs = [], []
    for requete in requetes_du_cas(cas):
        for loc in cas["locales"]:
            hl, gl, ceid = _LOCALES.get(loc, _LOCALES["en"])
            url = bitd.url_google_news("", requete_perso=requete,
                                       hl=hl, gl=gl, ceid=ceid)
            try:
                articles.extend(bitd.parser_rss(fetch(url))[:MAX_ART])
            except Exception as e:
                erreurs.append("{}: {}".format(type(e).__name__, str(e)[:40]))
            time.sleep(PAUSE)
    return articles, erreurs


# ===========================================================================
# 5. RAPPORT
# ===========================================================================
def imprimer(nom, cas, rap, erreurs):
    print("\n" + "=" * 74)
    print("CAS TEST : {}  [{}]".format(cas["libelle"], cas["project_id"]))
    print("  Pays : {} ({}) · alias testes : {}".format(
        cas["pays"], cas["iso3"], ", ".join(cas["alias"][:4])))
    if erreurs:
        print("  Requetes en erreur : {}".format(" | ".join(erreurs[:3])))
    verd, detail = verdict(rap, erreurs)
    print("  VERDICT : {}  ({})".format(verd, detail))
    print("  Articles vus : {} · rattaches au projet : {} · bruit ecarte : {}".format(
        rap["total"], len(rap["retenus"]), rap["bruit"]))
    if rap["plus_ancien"] is not None:
        print("  Profondeur temporelle : du plus ancien {} j au plus recent {} j".format(
            rap["plus_ancien"], rap["plus_recent"]))
    if rap["phases"]:
        print("  Phases du cycle de vie detectees :")
        for ph, n in rap["phases"].most_common():
            print("    - {:<24} {} signal(aux)".format(ph, n))
    else:
        print("  Phases detectees : AUCUNE (la grille cycle de vie ne matche pas)")
    if rap["acteurs"]:
        print("  Acteurs cites (lien PROJET -> ENTREPRISE) :")
        print("    " + ", ".join("{} ({})".format(a, n)
                                 for a, n in rap["acteurs"].most_common(8)))
    if rap["retenus"]:
        print("  Echantillon de signaux rattaches :")
        for s in sorted(rap["retenus"], key=lambda x: -(x["age"] or 0))[:6]:
            print("    [{:>4} j] {:<22} {}".format(
                s["age"] if s["age"] is not None else "?",
                s["phase"] or "(phase n.c.)", s["titre"][:64]))


def main():
    cible = os.environ.get("SONDE_CAS", "").strip().lower()
    noms = [cible] if cible in CAS else list(CAS.keys())
    if cible and cible not in CAS:
        print("Cas inconnu : {!r}. Cas : {}".format(cible, ", ".join(CAS)))
        sys.exit(0)
    print("#" * 74)
    print("SONDE PROJECT INTELLIGENCE -- Inga 3 & Tanzania LNG")
    print("Teste la grille CIBLE (cycle de vie), pas la grille actuelle.")
    print("Lecture seule : aucune ecriture, aucun appel LLM.")
    print("#" * 74)
    synth = []
    for nom in noms:
        cas = CAS[nom]
        try:
            articles, erreurs = collecter(cas)
        except Exception as e:
            print("\nCAS {} : ERREUR de collecte ({}: {})".format(
                nom, type(e).__name__, str(e)[:60]))
            synth.append((nom, "ERREUR", ""))
            continue
        rap = analyser(articles, cas)
        imprimer(nom, cas, rap, erreurs)
        verd, detail = verdict(rap, erreurs)
        synth.append((nom, verd, detail))
    print("\n" + "#" * 74)
    print("SYNTHESE")
    print("#" * 74)
    for nom, verd, detail in synth:
        print("  {:<10} : {:<12} {}".format(nom, verd, detail[:56]))
    print("\nLecture : FAISABLE sur les deux cas => on construit projets.py")
    print("(entite PROJECT_ID + cycle de vie + clustering par alias).")
    print("INFAISABLE ou FAIBLE => Google News seul ne suffit pas, il faudra")
    print("des sources gouvernementales/sectorielles avant tout developpement.")
    sys.exit(0)


if __name__ == "__main__":
    main()
