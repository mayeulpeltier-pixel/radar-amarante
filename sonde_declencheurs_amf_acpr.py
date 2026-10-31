# -*- coding: utf-8 -*-
"""
Radar Amarante - SONDE declencheurs AMF / ACPR (jetable, lecture seule).
=========================================================================

POURQUOI
--------
Meme logique que les CJIP (declencheurs_ie.py) etendue au secteur financier :
une decision de sanction qui vise le DISPOSITIF de conformite / LCB-FT d'une
societe NOMMEE cree un besoin de remediation (evaluation des tiers, KYC,
cartographie des risques), donc un prospect qualifie et date pour ARISE.

Une sanction pour pur ABUS DE MARCHE (information privilegiee, manipulation de
cours, manquement declaratif) est une amende : aucun besoin de remediation de
dispositif. La sonde doit donc separer les deux.

CE QU'ELLE MESURE (par canal)
-----------------------------
  - volume de decisions sur la fenetre,
  - part nommant une societe (vs personnes physiques seules),
  - part dont le motif touche conformite / LCB-FT / dispositif,
  - part portant un signal de remediation (injonction, mise en conformite),
  - verdict EXPLOITABLE / MARGINAL.

ACCES (verifie le 31/10/2026)
-----------------------------
  - AMF : sitemap.xml public liste 454 decisions ; chaque page expose en HTML
    brut le titre (societe nommee + date) et le lien PDF STATIQUE (texte
    integral). Tout joignable sans compte ni JS. Le back si-bo est inutile.
  - ACPR : banque-france.fr renvoie 403 au proxy du conteneur (comme AfDB/ADB).
    La sonde tente quand meme ; si injoignable, elle le dit et renvoie a une
    mesure en GitHub Actions (egress ouvert).

Deterministe : AUCUN appel LLM. Reseau INJECTABLE pour les tests (aucun test
ne touche le reseau ni pypdf). A SUPPRIMER une fois la decision prise.
"""

import csv
import datetime
import io
import os
import re
import time
import unicodedata
import urllib.request

import config_env

# --- Reglages -------------------------------------------------------------
MOIS_FENETRE = config_env.entier("SONDE_MOIS", 24)
PAUSE = float(os.environ.get("SONDE_PAUSE", "0.3"))
SORTIE = os.environ.get("SONDE_SORTIE") or "sonde_declencheurs_amf_acpr_sortie"
UA = {"User-Agent": "Mozilla/5.0 (compatible; RadarAmarante/1.0)"}

SITEMAP_AMF = "https://www.amf-france.org/sitemap.xml"
MARQUE_DECISION_AMF = "/decisions-de-la-commission-des-sanctions/"
ACPR_LISTE = "https://acpr.banque-france.fr/liste-des-decisions-de-la-commission-des-sanctions"

# Seuils de verdict (par an)
SEUIL_PERTINENT_AN = float(os.environ.get("SONDE_SEUIL_AN", "3"))

# --- Lexique (accent-insensible) -----------------------------------------
_CONF = ("lcb-ft", "lcb ft", "blanchiment", "financement du terrorisme",
         "dispositif de conformite", "controle interne", "obligations professionnelles",
         "evaluation des tiers", "due diligence", "diligences", "kyc",
         "connaissance client", "gel des avoirs", "cartographie des risques",
         "programme de conformite", "deontolog")
_ABUS = ("information privilegiee", "manipulation de cours", "manquement declaratif",
         "abus de marche", "fausse information", "franchissement de seuil",
         "operation d'initie", "diffusion de fausse")
_REMEDIATION = ("injonction", "mise en conformite", "se mettre en conformite",
                "remediation", "plan d'action", "renforcer son dispositif",
                "sous astreinte")

MOIS_FR = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
           "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10,
           "novembre": 11, "decembre": 12}


# ===========================================================================
# FONCTIONS PURES (testables sans reseau)
# ===========================================================================
def sans_accents(texte):
    """Minuscule sans accents, pour une recherche de mots robuste."""
    decompose = unicodedata.normalize("NFKD", texte or "")
    return "".join(c for c in decompose if not unicodedata.combining(c)).lower()


def decisions_du_sitemap(xml):
    """Liste des URLs de decisions de la commission des sanctions (ordre du sitemap)."""
    return [u for u in re.findall(r"<loc>([^<]+)</loc>", xml or "")
            if MARQUE_DECISION_AMF in u]


def titre_og(html):
    """Titre de la decision depuis la balise og:title (HTML brut, sans JS)."""
    m = re.search(r'<meta property="og:title" content="([^"]+)"', html or "")
    if not m:
        m = re.search(r"<title>([^<]+)</title>", html or "")
    brut = m.group(1) if m else ""
    brut = brut.replace("&#039;", "'").replace("&amp;", "&").replace("&eacute;", "e")
    return brut.split(" | ")[0].strip()


def lien_pdf(html):
    """Lien PDF statique de la decision (texte integral)."""
    m = re.search(r'href="(https://www\.amf-france\.org/sites/[^"]+\.pdf[^"]*)"',
                  html or "")
    return m.group(1).replace("&amp;", "&") if m else ""


def date_decision(titre):
    """Date 'du JJ[er] mois AAAA' extraite du titre, sinon None."""
    t = sans_accents(titre)
    m = re.search(r"du\s+(\d{1,2})(?:er)?\s+([a-z]+)\s+(\d{4})", t)
    if not m:
        return None
    jour, mois, annee = int(m.group(1)), MOIS_FR.get(m.group(2)), int(m.group(3))
    if not mois:
        return None
    try:
        return datetime.date(annee, mois, jour)
    except ValueError:
        return None


def societe_nommee(titre):
    """Vrai si la decision vise une societe nommee (pas des personnes seules)."""
    t = sans_accents(titre)
    if "societe" in t or "banque" in t or "assurance" in t or "etablissement" in t:
        return True
    return bool(re.search(r"\b(sa|sas|sasu|sgp|sarl|snc)\b", t))


def classer_motif(texte_pdf):
    """Classe le motif d'une decision a partir du texte integral.

    Renvoie un dict de drapeaux. `pertinent` = touche la conformite/LCB-FT et
    n'est pas une pure affaire d'abus de marche."""
    t = sans_accents(texte_pdf)
    conf = any(k in t for k in _CONF)
    abus = any(k in t for k in _ABUS)
    remed = any(k in t for k in _REMEDIATION)
    # Pertinent ARISE : dispositif de conformite en jeu ; si pur abus de marche
    # sans aucun terme de conformite, non pertinent.
    pertinent = conf and not (abus and not remed and _abus_domine(t))
    return {"conformite": conf, "abus": abus, "remediation": remed,
            "pertinent": bool(pertinent)}


def _abus_domine(texte_sans_accents):
    """Heuristique : l'abus de marche est le sujet central (plus d'occurrences
    d'abus que de conformite)."""
    na = sum(texte_sans_accents.count(k) for k in _ABUS)
    nc = sum(texte_sans_accents.count(k) for k in _CONF)
    return na > nc


def dans_fenetre(date, aujourd, mois):
    """Vrai si `date` tombe dans les `mois` derniers mois avant `aujourd`."""
    if not date:
        return False
    limite = aujourd - datetime.timedelta(days=int(mois * 30.44))
    return date >= limite


def verdict(journal):
    """Verdict d'un canal a partir de ses compteurs."""
    if journal.get("injoignable"):
        return "INJOIGNABLE", journal.get("note", "canal injoignable depuis cet environnement")
    mois = max(1, journal.get("mois", MOIS_FENETRE))
    pertinents = journal.get("pertinents", 0)
    par_an = pertinents * 12.0 / mois
    if par_an >= SEUIL_PERTINENT_AN:
        return "EXPLOITABLE", ("{:.1f} decisions pertinentes/an nommant une societe "
                               "(conformite/LCB-FT).".format(par_an))
    return "MARGINAL", ("{:.1f} decision(s) pertinente(s)/an : sous le seuil de {:.0f}."
                        .format(par_an, SEUIL_PERTINENT_AN))


# ===========================================================================
# COLLECTE (reseau injectable)
# ===========================================================================
def _get_texte(url):
    rep = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=35)
    return rep.read().decode("utf-8", "replace")


def _get_pdf_texte(url):
    import pypdf
    rep = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=45)
    lecteur = pypdf.PdfReader(io.BytesIO(rep.read()))
    return " ".join((p.extract_text() or "") for p in lecteur.pages[:8])


def sonder_amf(get_texte=None, get_pdf=None, aujourd=None, mois=None, limite=None):
    """Mesure le canal AMF. `get_texte(url)->str`, `get_pdf(url)->str` injectables."""
    get_texte = get_texte or _get_texte
    get_pdf = get_pdf or _get_pdf_texte
    aujourd = aujourd or datetime.date.today()
    mois = mois or MOIS_FENETRE
    journal = {"canal": "AMF", "mois": mois, "lues": 0, "nommees": 0,
               "conformite": 0, "abus": 0, "remediation": 0, "pertinents": 0,
               "exemples": [], "erreurs": [], "hors_fenetre": 0}
    try:
        xml = get_texte(SITEMAP_AMF)
    except Exception as e:
        journal["injoignable"] = True
        journal["note"] = "sitemap AMF injoignable : {}".format(str(e)[:120])
        return journal
    urls = decisions_du_sitemap(xml)
    vues = set()
    examinees = 0
    for url in reversed(urls):          # du plus recent au plus ancien
        if url in vues:
            continue
        vues.add(url)
        if limite and examinees >= limite:
            break
        try:
            html = get_texte(url)
        except Exception as e:
            journal["erreurs"].append("page : {}".format(str(e)[:100]))
            continue
        titre = titre_og(html)
        date = date_decision(titre)
        if not dans_fenetre(date, aujourd, mois):
            journal["hors_fenetre"] += 1
            # Les decisions sont a peu pres chronologiques ; apres plusieurs
            # hors fenetre d'affilee, on s'arrete.
            if journal["hors_fenetre"] > 8:
                break
            continue
        journal["hors_fenetre"] = 0
        examinees += 1
        pdf = lien_pdf(html)
        if not pdf:
            journal["erreurs"].append("sans PDF : {}".format(titre[:60]))
            continue
        try:
            texte = get_pdf(pdf)
        except Exception as e:
            journal["erreurs"].append("PDF : {}".format(str(e)[:100]))
            continue
        journal["lues"] += 1
        nommee = societe_nommee(titre)
        cl = classer_motif(texte)
        journal["nommees"] += nommee
        journal["conformite"] += cl["conformite"]
        journal["abus"] += cl["abus"]
        journal["remediation"] += cl["remediation"]
        if nommee and cl["pertinent"]:
            journal["pertinents"] += 1
            if len(journal["exemples"]) < 12:
                journal["exemples"].append(titre[:120])
        time.sleep(PAUSE)
    return journal


def sonder_acpr(get_texte=None):
    """Tente le canal ACPR. Injoignable depuis le conteneur -> note explicite."""
    get_texte = get_texte or _get_texte
    journal = {"canal": "ACPR", "mois": MOIS_FENETRE, "pertinents": 0}
    try:
        get_texte(ACPR_LISTE)
    except Exception as e:
        journal["injoignable"] = True
        journal["note"] = ("banque-france.fr injoignable depuis cet environnement "
                           "({}). A mesurer en GitHub Actions.".format(str(e)[:80]))
    return journal


# ===========================================================================
# RAPPORT
# ===========================================================================
def rapport(amf, acpr, aujourd=None):
    aujourd = aujourd or datetime.date.today()
    va = verdict(amf)
    vp = verdict(acpr)
    md = ["# Sonde declencheurs AMF / ACPR",
          "",
          "Fenetre {} mois. Lecture seule, aucun LLM. Genere le {}.".format(
              amf.get("mois", MOIS_FENETRE), aujourd.isoformat()),
          "",
          "| Canal | Verdict |", "|---|---|",
          "| AMF (commission des sanctions) | **{}** |".format(va[0]),
          "| ACPR (commission des sanctions) | **{}** |".format(vp[0]),
          "",
          "## 1. AMF - {}".format(va[0]), "", va[1], ""]
    if not amf.get("injoignable"):
        md += ["| Mesure | Valeur |", "|---|---|",
               "| Decisions lues (fenetre) | {} |".format(amf["lues"]),
               "| Nommant une societe | {} |".format(amf["nommees"]),
               "| Motif conformite / LCB-FT | {} |".format(amf["conformite"]),
               "| Motif abus de marche | {} |".format(amf["abus"]),
               "| Signal de remediation | {} |".format(amf["remediation"]),
               "| **Pertinents ARISE (societe + conformite)** | **{}** |".format(amf["pertinents"]),
               ""]
        if amf["exemples"]:
            md += ["**Exemples pertinents :**"] + ["- " + x for x in amf["exemples"]] + [""]
    for e in amf.get("erreurs", [])[:5]:
        md.append("- Erreur : {}".format(e))
    md += ["", "## 2. ACPR - {}".format(vp[0]), "", vp[1], ""]
    md += ["", "## Lecture", "",
           "Une sanction qui vise le DISPOSITIF de conformite / LCB-FT d'une "
           "societe nommee cree un besoin de remediation (evaluation des tiers, "
           "KYC, cartographie des risques) = prospect ARISE date. Les pures "
           "affaires d'abus de marche sont ecartees. Branchement = collecteur "
           "declencheurs, meme patron que les CJIP (sans LLM)."]
    return "\n".join(md) + "\n", (va, vp)


def main():
    amf = sonder_amf()
    acpr = sonder_acpr()
    md, _ = rapport(amf, acpr)
    print(md)
    os.makedirs(SORTIE, exist_ok=True)
    with open(os.path.join(SORTIE, "rapport.md"), "w", encoding="utf-8") as f:
        f.write(md)
    with open(os.path.join(SORTIE, "amf_pertinents.csv"), "w", encoding="utf-8",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["titre"])
        for x in amf.get("exemples", []):
            w.writerow([x])
    resume = os.environ.get("GITHUB_STEP_SUMMARY")
    if resume:
        with open(resume, "a", encoding="utf-8") as f:
            f.write(md)


if __name__ == "__main__":
    main()
