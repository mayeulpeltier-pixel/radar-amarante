# -*- coding: utf-8 -*-
"""
Radar Amarante - Sonde jetable IE-7 : les DECLENCHEURS de demande privee en
conformite et intelligence economique sont-ils captables, et a quel bruit ?

Diagnostic ponctuel, declenchement MANUEL (workflow sonde_declencheurs_ie.yml).
Ne touche a RIEN : ni Sheet, ni Postgres, ni etat. AUCUN appel LLM.

Deux familles testees :

1. CJIP (conventions judiciaires d'interet public), source OFFICIELLE.
   Page justice.gouv.fr : une entree par CJIP, etiquetee FINANCIER ou
   ENVIRONNEMENT, avec PDF de la convention. Une CJIP financiere impose
   souvent a la societe un programme de mise en conformite de 2 a 3 ans
   sous controle de l'AFA : la societe nommee doit alors acheter cartographie
   des risques, evaluation des tiers, audits. La sonde mesure :
     - combien de CJIP financieres par an ;
     - combien imposent un programme sous controle AFA (lecture du PDF) ;
     - le delai entre validation et mise en ligne (fraicheur du signal).

2. PRESSE (flux RSS Google News, deja utilise par le radar Operations).
   Volume et echantillon de titres pour six requetes, sans filtre : c'est
   la mesure du bruit brut qu'un collecteur devrait trier.

Verdict par famille : EXPLOITABLE, MARGINAL, NON CONCLU (rien lu).
"""

import datetime
import html
import io
import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

URL_CJIP = ("https://www.justice.gouv.fr/documentation/ressources/"
            "conventions-judiciaires-dinteret-public")
URL_RSS = "https://news.google.com/rss/search?q={}&hl=fr&gl=FR&ceid=FR:fr"
UA = {"User-Agent": "Mozilla/5.0 (compatible; RadarAmarante-sonde/1.0)"}
SORTIE = os.environ.get("SONDE_SORTIE") or "sonde_declencheurs_ie_sortie"
ANNEES = int(os.environ.get("SONDE_ANNEES") or 2)
MAX_PDF = int(os.environ.get("SONDE_MAX_PDF") or 40)
PAUSE = 0.5

REQUETES_PRESSE = [
    '"convention judiciaire d\'intérêt public"',
    '"Agence française anticorruption"',
    '"programme de conformité" corruption',
    '"ingérence économique" entreprise',
    '"intelligence économique" région',
    '"devoir de vigilance" entreprise',
]
JOURS_PRESSE = 90

MOIS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
        "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
        "decembre": 12}


def normaliser(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.replace("’", "'").lower()).strip()


def _texte(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


# ===========================================================================
# 1. CJIP
# ===========================================================================
_RE_TITRE = re.compile(r"<h([2-4])[^>]*>(.*?)</h\1>", re.S | re.I)
_RE_LIEN = re.compile(r'<a[^>]+href="([^"]+?\.pdf)"[^>]*>(.*?)</a>', re.S | re.I)
_RE_DATE_LONGUE = re.compile(r"valid[ée]e? le (\d{1,2})(?:er)? ([a-zéû]+) (\d{4})", re.I)


def date_longue(texte):
    m = _RE_DATE_LONGUE.search(texte or "")
    if not m:
        return None
    mois = MOIS.get(normaliser(m.group(2)))
    try:
        return datetime.date(int(m.group(3)), mois, int(m.group(1))) if mois else None
    except ValueError:
        return None


def parser_page_cjip(page):
    """HTML -> liste d'entrees {domaine, titre, validation, societe, pdfs}.
    Une entree = un titre de section contenant 'convention judiciaire', suivi
    de ses liens PDF jusqu'au titre suivant. Tolerant aux variations de
    balisage (h2 a h4) : seule la structure titre puis liens est supposee."""
    titres = [(m.start(), m.end(), _texte(m.group(2))) for m in _RE_TITRE.finditer(page)]
    entrees = []
    for i, (debut, fin, titre) in enumerate(titres):
        if "convention judiciaire" not in normaliser(titre):
            continue
        suite = titres[i + 1][0] if i + 1 < len(titres) else len(page)
        bloc = page[fin:suite]
        pdfs = []
        for href, lib in _RE_LIEN.findall(bloc):
            lib = _texte(lib)
            if href.startswith("/"):
                href = "https://www.justice.gouv.fr" + href
            pdfs.append({"url": href, "libelle": lib, "type": type_document(href, lib)})
        t = normaliser(titre)
        domaine = ("ENVIRONNEMENT" if "environnement" in t
                   else "FINANCIER" if "financier" in t else "AUTRE")
        entrees.append({"domaine": domaine, "titre": titre,
                        "validation": date_longue(titre),
                        "societe": societe(titre, pdfs), "pdfs": pdfs,
                        "mise_en_ligne": mise_en_ligne(pdfs)})
    return entrees


def type_document(href, libelle):
    h = href.upper()
    if "_OV_" in h or "ordonnance" in normaliser(libelle):
        return "ordonnance"
    if "_CP_" in h or "communiqu" in normaliser(libelle):
        return "communique"
    if "_AIP_" in h:
        return "amende"
    return "convention"


def societe(titre, pdfs):
    # Le nom suit le premier " et " APRES "conclue entre" (le titre contient
    # d'autres "et" avant). " et " en minuscules : "PERIPHERIQUES ET
    # MATERIELS" ne coupe pas le nom.
    partie = re.split(r"conclue entre", titre, maxsplit=1, flags=re.I)[-1]
    m = re.search(r"\bet (?:la |le |les |l')?(?:soci[ée]t[ée]s? )?(.+)$", partie)
    if m and len(m.group(1)) < 120:
        return m.group(1).strip(" .")
    for p in pdfs:
        parties = [x.strip() for x in p["libelle"].split(" - ")]
        if len(parties) >= 3:
            return re.sub(r"\s*PDF.*$", "", parties[2]).strip()
    return ""


def mise_en_ligne(pdfs):
    """Mois de depot du PDF (dossier /files/AAAA-MM/) : borne basse de la
    date a laquelle un collecteur aurait pu voir la CJIP."""
    for p in pdfs:
        m = re.search(r"/files/(\d{4})-(\d{2})/", p["url"])
        if m:
            return datetime.date(int(m.group(1)), int(m.group(2)), 1)
    return None


_RE_AFA = re.compile(r"agence fran[cç]aise anti-?corruption|\bafa\b", re.I)
_RE_PROGRAMME = re.compile(r"programme de mise en conformit", re.I)
_RE_DUREE = re.compile(r"dur[ée]e (?:maximale )?de (\d+|deux|trois|quatre|dix-huit|vingt-quatre|trente-six) (ans|mois)", re.I)
_NOMBRES = {"deux": 2, "trois": 3, "quatre": 4, "dix-huit": 18, "vingt-quatre": 24,
            "trente-six": 36}


def analyser_convention(texte):
    """Texte de la convention -> {afa, programme, duree_mois}."""
    t = texte or ""
    duree = None
    m = _RE_DUREE.search(t) if _RE_PROGRAMME.search(t) else None
    if m:
        brut = m.group(1).lower()
        n = int(brut) if brut.isdigit() else _NOMBRES.get(brut)
        if n:
            duree = n * 12 if m.group(2).lower() == "ans" else n
    return {"afa": bool(_RE_AFA.search(t)), "programme": bool(_RE_PROGRAMME.search(t)),
            "duree_mois": duree}


def _get(url, binaire=False, session=None):
    import requests
    s = session or requests
    rep = s.get(url, headers=UA, timeout=60)
    rep.raise_for_status()
    return rep.content if binaire else rep.text


def texte_pdf(contenu):
    from pypdf import PdfReader
    lecteur = PdfReader(io.BytesIO(contenu))
    return " ".join((p.extract_text() or "") for p in lecteur.pages[:15])


def sonder_cjip(aujourd, get=None, lire_pdf=None):
    get = get or _get
    lire_pdf = lire_pdf or texte_pdf
    erreurs = []
    try:
        entrees = parser_page_cjip(get(URL_CJIP))
    except Exception as e:  # noqa: BLE001
        return [], ["page CJIP : {}".format(e)]
    limite = datetime.date(aujourd.year - ANNEES + 1, 1, 1)
    recentes = [e for e in entrees if e["validation"] and e["validation"] >= limite]
    lus = 0
    for e in recentes:
        e["analyse"] = None
        if e["domaine"] != "FINANCIER" or lus >= MAX_PDF:
            continue
        conv = [p for p in e["pdfs"] if p["type"] == "convention"]
        if not conv:
            continue
        try:
            e["analyse"] = analyser_convention(lire_pdf(get(conv[0]["url"], binaire=True)))
            lus += 1
        except Exception as ex:  # noqa: BLE001
            erreurs.append("{} : {}".format(e["societe"][:40], str(ex)[:100]))
        time.sleep(PAUSE)
    return recentes, erreurs


# ===========================================================================
# 2. PRESSE
# ===========================================================================
def parser_rss(xml_texte):
    racine = ET.fromstring(xml_texte)
    items = []
    for it in racine.iter("item"):
        items.append({"titre": (it.findtext("title") or "").strip(),
                      "lien": (it.findtext("link") or "").strip(),
                      "date": (it.findtext("pubDate") or "").strip(),
                      "source": (it.findtext("source") or "").strip()})
    return items


def sonder_presse(get=None, requetes=REQUETES_PRESSE):
    get = get or _get
    res, erreurs = {}, []
    for q in requetes:
        url = URL_RSS.format(quote_plus("{} when:{}d".format(q, JOURS_PRESSE)))
        try:
            items = parser_rss(get(url))
        except Exception as e:  # noqa: BLE001
            erreurs.append("{} : {}".format(q, str(e)[:100]))
            items = None
        if items is not None:
            vus, uniques = set(), []
            for it in items:
                cle = normaliser(re.sub(r"\s+-\s+[^-]+$", "", it["titre"]))
                if cle not in vus:
                    vus.add(cle)
                    uniques.append(it)
            res[q] = uniques
        time.sleep(PAUSE)
    return res, erreurs


# ===========================================================================
# VERDICTS ET RAPPORT
# ===========================================================================
def stats_cjip(entrees):
    fin = [e for e in entrees if e["domaine"] == "FINANCIER"]
    analysees = [e for e in fin if e.get("analyse")]
    avec_prog = [e for e in analysees if e["analyse"]["programme"] and e["analyse"]["afa"]]
    delais = [(e["mise_en_ligne"] - e["validation"].replace(day=1)).days // 30
              for e in entrees if e["mise_en_ligne"] and e["validation"]]
    par_an = {}
    for e in fin:
        par_an[e["validation"].year] = par_an.get(e["validation"].year, 0) + 1
    return {"total": len(entrees), "financieres": fin, "analysees": analysees,
            "avec_programme": avec_prog, "par_an": par_an,
            "delai_median_mois": sorted(delais)[len(delais) // 2] if delais else None}


def verdict_cjip(st):
    if st["total"] == 0:
        return ("NON CONCLU", "page non lue ou structure non reconnue")
    n = len(st["avec_programme"])
    if n >= 3:
        return ("EXPLOITABLE", "{} CJIP financiere(s) imposent un programme sous "
                "controle AFA".format(n))
    if st["financieres"]:
        return ("MARGINAL", "{} CJIP financieres, {} avec programme AFA identifie".format(
            len(st["financieres"]), n))
    return ("MARGINAL", "aucune CJIP financiere sur la periode")


def verdict_presse(res):
    if not res:
        return ("NON CONCLU", "aucun flux lu")
    total = sum(len(v) for v in res.values())
    return ("A TRIER", "{} articles uniques sur {} jours, {} requetes : le tri "
            "necessite le LLM ou une liste d'exclusion".format(total, JOURS_PRESSE, len(res)))


def rapport(cj, cj_err, presse, presse_err, aujourd):
    st = stats_cjip(cj)
    v1, v2 = verdict_cjip(st), verdict_presse(presse)
    L = ["# Sonde declencheurs IE (IE-7)", "", "Date : {}.".format(aujourd), "",
         "## 1. CJIP : {}".format(v1[0]), "", v1[1], "",
         "- Entrees lues sur {} an(s) : {} (financieres : {}, PDF analyses : {})".format(
             ANNEES, st["total"], len(st["financieres"]), len(st["analysees"])),
         "- CJIP financieres par an : {}".format(
             ", ".join("{} : {}".format(a, n) for a, n in sorted(st["par_an"].items())) or "-"),
         "- Delai median validation -> mise en ligne : {} mois".format(
             st["delai_median_mois"] if st["delai_median_mois"] is not None else "n.c."), "",
         "| Validation | Societe | Programme AFA | Duree | Convention |",
         "|---|---|---|---|---|"]
    for e in sorted(st["financieres"], key=lambda x: x["validation"], reverse=True):
        a = e.get("analyse") or {}
        conv = next((p["url"] for p in e["pdfs"] if p["type"] == "convention"), "")
        L.append("| {} | {} | {} | {} | {} |".format(
            e["validation"], e["societe"][:60],
            "oui" if a.get("programme") and a.get("afa") else ("non" if a else "non lu"),
            "{} mois".format(a["duree_mois"]) if a.get("duree_mois") else "-", conv))
    L += ["", "## 2. Presse : {}".format(v2[0]), "", v2[1], ""]
    for q, items in presse.items():
        L += ["### {} : {} article(s)".format(q, len(items)), ""]
        L += ["- {} | {} | {}".format(it["date"][:16], it["source"][:30], it["titre"][:110])
              for it in items[:8]] or ["- (aucun)"]
        L.append("")
    erreurs = cj_err + presse_err
    if erreurs:
        L += ["## Erreurs", ""] + ["- " + e for e in erreurs[:30]]
    return "\n".join(L) + "\n"


def main():
    aujourd = datetime.date.today()
    cj, cj_err = sonder_cjip(aujourd)
    presse, presse_err = sonder_presse()
    md = rapport(cj, cj_err, presse, presse_err, aujourd)
    print(md)
    os.makedirs(SORTIE, exist_ok=True)
    with open(os.path.join(SORTIE, "rapport.md"), "w", encoding="utf-8") as f:
        f.write(md)
    resume = os.environ.get("GITHUB_STEP_SUMMARY")
    if resume:
        with open(resume, "a", encoding="utf-8") as f:
            f.write(md)


if __name__ == "__main__":
    main()
