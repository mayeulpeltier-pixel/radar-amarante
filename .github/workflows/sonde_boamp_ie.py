# -*- coding: utf-8 -*-
"""
Radar Amarante - Sonde jetable IE-0 : le BOAMP porte-t-il assez de marches
d'intelligence economique pour justifier un collecteur dedie ?

Diagnostic ponctuel, declenchement MANUEL (workflow sonde_boamp_ie.yml).
Ne touche a RIEN : ni Google Sheet, ni Postgres, ni etat, ni dashboard.
N'appelle AUCUN LLM (cout zero).

Cinq questions, une reponse chiffree chacune :
  1. VOLUME     : combien d'avis IE sur la fenetre (avis de marche / resultats) ?
  2. BRUIT      : quelle part releve du sondage, de la statistique, du tourisme ?
  3. RAPPEL     : retrouve-t-on les marches de reference (AURA 24-80762,
                  Nice 24-99592) ? S'ils manquent, le lexique est faux.
  4. CONCURRENCE: qui sont les titulaires des marches IE attribues ?
  5. CPV        : sous quels codes ces marches sont-ils publies ?

Perimetre = decision D1 du 30/10/2026 : IE + Conformite. Les etudes
geopolitiques et observatoires sont classes HORS PERIMETRE (reserves a la
future lentille Analyste), sans exclusion par mot-cle du titre : un
"observatoire de la securite economique" reste de l'IE.

Methode : recherche plein texte cote serveur (API Opendatasoft de la DILA,
sans cle, licence ouverte 2.0), puis CONFIRMATION LOCALE de l'expression
exacte. La recherche serveur ratisse large (mots dans n'importe quel ordre) ;
seule la confirmation locale compte dans les chiffres. L'ecart entre les deux
mesure le bruit serveur, utile pour dimensionner le futur collecteur.
"""

import csv
import datetime
import json
import os
import re
import time
import unicodedata
from collections import Counter

try:  # import resilient : module absent -> pas de validation de division CPV
    import cpv_reference
    DIVISIONS_CPV = set(cpv_reference.DIVISIONS)
except Exception:  # pragma: no cover - depend de l'environnement
    DIVISIONS_CPV = set()

# ===========================================================================
# CONFIGURATION
# ===========================================================================
URL_RECORDS = ("https://boamp-datadila.opendatasoft.com/api/explore/v2.1/"
               "catalog/datasets/boamp/records")
LIEN_AVIS = "https://www.boamp.fr/pages/avis/?q=idweb:{}"
PAGE = 100            # plafond Opendatasoft par appel
FENETRE_ODS = 10000   # offset + limit ne peut pas depasser cette valeur
PAUSE = 0.3           # politesse entre deux pages


def _entier_env(nom, defaut):
    try:
        return int(os.environ.get(nom) or defaut)
    except ValueError:
        return defaut


# 32 mois par defaut : la fenetre doit couvrir les references de 2024
# (AURA avis initial avril 2024, attribution juillet 2024, Nice septembre 2024).
MOIS = _entier_env("SONDE_MOIS", 32)
MAX_PAR_TERME = _entier_env("SONDE_MAX_PAR_TERME", 1000)
SORTIE = os.environ.get("SONDE_SORTIE") or "sonde_boamp_ie_sortie"

# Seuil de faisabilite (hypothese a discuter) : au moins 2 avis de marche par
# mois dont le TITRE porte un terme fort, soit environ 24 AO exploitables / an.
SEUIL_RYTHME_FAISABLE = 2.0

# ===========================================================================
# LEXIQUE (perimetre D1 : IE + Conformite)
# ===========================================================================
# (requete serveur, motif local normalise, niveau)
#   fort      : un terme suffit a retenir l'avis
#   a_mesurer : terme ambigu, compte a part pour en mesurer le bruit
TERMES = [
    ("intelligence économique", "intelligence economique", "fort"),
    ("intelligence stratégique", "intelligence strategique", "fort"),
    ("sécurité économique", "securite economique", "fort"),
    ("renseignement économique", "renseignement economique", "fort"),
    ("veille stratégique", "veille strategique", "fort"),
    ("veille concurrentielle", "veille concurrentielle", "fort"),
    ("veille économique", "veille economique", "fort"),
    ("due diligence", "due diligence", "fort"),
    ("diligence raisonnable", "diligence raisonnable", "fort"),
    ("évaluation des tiers", "evaluation des tiers", "fort"),
    ("cartographie des acteurs", "cartographie des acteurs", "fort"),
    ("anticorruption", "anticorruption", "fort"),
    ("anti-corruption", "anti-corruption", "fort"),
    ("Sapin II", "sapin ii", "fort"),
    ("Sapin 2", "sapin 2", "fort"),
    ("OSINT", "osint", "fort"),
    ("veille technologique", "veille technologique", "a_mesurer"),
    ("veille réglementaire", "veille reglementaire", "a_mesurer"),
    ("études sectorielles", "etudes sectorielles", "a_mesurer"),
    ("intelligence territoriale", "intelligence territoriale", "a_mesurer"),
]

# Bruit connu des CPV 79311000 / 79300000 (sous-chaines, sur le TITRE).
BRUIT = ("sondage", "satisfaction", "touris", "statistique", "barometre",
         "frequentation", "panel", "enquete telephonique", "recensement",
         "comptage", "evaluation de politique", "evaluation des politiques",
         "evaluation du programme", "evaluation d'impact")

# Perimetre Analyste (decision D1) : hors IE SAUF si le titre porte aussi un
# terme du noyau IE.
ANALYSTE = ("observatoire", "geopolitique", "geostrategique", "prospective")
NOYAU_IE = ("intelligence economique", "intelligence strategique",
            "securite economique", "due diligence", "anticorruption",
            "anti-corruption")

# Verite terrain : marches IE reels, publies au BOAMP.
REFERENCES = {
    "24-80762": "AURA Entreprises, cellule d'intelligence strategique "
                "(attribution, gagne par Amarante)",
    "24-39524": "AURA Entreprises, cellule d'intelligence strategique "
                "(avis de marche initial)",
    "24-99592": "Metropole Nice Cote d'Azur, prestations d'IE (avis de marche)",
}

# ===========================================================================
# TEXTE
# ===========================================================================
def normaliser_texte(s):
    """Minuscules, sans accents, apostrophes unifiees, espaces compactes."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("’", "'").replace("‘", "'").lower()
    return re.sub(r"\s+", " ", s).strip()


def _contient_mot(texte_n, motif):
    """Motif present en mot entier (pas 'osint' dans 'cosinus')."""
    return re.search(r"(?<![a-z0-9])" + re.escape(motif) + r"(?![a-z0-9])",
                     texte_n) is not None


def _contient_un(texte_n, sous_chaines):
    return any(x in texte_n for x in sous_chaines)


def termes_trouves(texte_n, termes=TERMES):
    """Liste (motif, niveau) des termes du lexique presents dans le texte."""
    return [(m, n) for _, m, n in termes if _contient_mot(texte_n, m)]


def classer(objet_n, corps_n):
    """Classe d'un avis. Ordre de priorite :
    hors_analyste > bruit > fort_objet > fort_corps > a_mesurer > sans_terme."""
    t_objet = termes_trouves(objet_n)
    t_corps = termes_trouves(corps_n)
    if _contient_un(objet_n, ANALYSTE) and not any(
            _contient_mot(objet_n, m) for m in NOYAU_IE):
        return "hors_analyste"
    if _contient_un(objet_n, BRUIT):
        return "bruit"
    if any(n == "fort" for _, n in t_objet):
        return "fort_objet"
    if any(n == "fort" for _, n in t_corps):
        return "fort_corps"
    if t_objet or t_corps:
        return "a_mesurer"
    return "sans_terme"


CLASSES_RETENUES = ("fort_objet", "fort_corps")

# ===========================================================================
# EXTRACTION DEFENSIVE (les champs BOAMP varient selon les schemas d'avis)
# ===========================================================================
def _premier(rec, *cles):
    """Premiere valeur non vide parmi plusieurs noms de champs possibles."""
    for c in cles:
        v = rec.get(c)
        if isinstance(v, dict):
            v = v.get("libelle") or v.get("valeur") or ""
        if isinstance(v, (list, tuple)):
            v = " ; ".join(str(x) for x in v if x)
        if v not in (None, "", []):
            return str(v).strip()
    return ""


def texte_donnees(d):
    """Le champ `donnees` est un blob JSON (eForms ou ancien schema), parfois
    serialise en chaine. On le rend en texte lisible, accents compris."""
    if not d:
        return ""
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except (ValueError, TypeError):
            return d
    try:
        return json.dumps(d, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(d)


_RE_CPV = (
    re.compile(r"cpv[^0-9]{0,60}?(?<!\d)(\d{8})(?!\d)", re.I),
    re.compile(r"(?<!\d)(\d{8})(?!\d)[^0-9]{0,60}?cpv", re.I),
)


def extraire_cpv(texte):
    """Codes CPV (8 chiffres) proches du mot 'cpv', dedoublonnes, dans
    l'ordre d'apparition. Un SIRET (14 chiffres) n'est jamais capture."""
    vus = []
    for rx in _RE_CPV:
        for m in rx.finditer(texte or ""):
            code = m.group(1)
            if DIVISIONS_CPV and code[:2] not in DIVISIONS_CPV:
                continue
            if code not in vus:
                vus.append(code)
    return vus


def normaliser(rec):
    """Avis brut -> dict compact. Le corps du texte n'est PAS conserve
    (memoire) : classification et detections sont faites ici."""
    idweb = _premier(rec, "idweb", "id")
    objet = _premier(rec, "objet", "objet_complet", "titre_marche")
    brut = texte_donnees(rec.get("donnees"))
    corps = " ".join([objet, _premier(rec, "descripteur_libelle"), brut])
    objet_n, corps_n = normaliser_texte(objet), normaliser_texte(corps)
    nature = _premier(rec, "nature_libelle", "nature", "type_avis")
    titulaire = _premier(rec, "titulaire", "titulaires", "nom_titulaire")
    champ_cpv = _premier(rec, "code_cpv", "cpv")
    cpv = extraire_cpv("cpv " + champ_cpv) if champ_cpv else []
    cpv += [c for c in extraire_cpv(brut) if c not in cpv]
    return {
        "idweb": idweb,
        "date": _premier(rec, "dateparution", "date_parution")[:10],
        "nature": nature,
        "resultat": _contient_un(normaliser_texte(nature),
                                 ("resultat", "attribution")),
        "procedure": _premier(rec, "procedure_libelle", "type_procedure",
                              "procedure_categorise"),
        "acheteur": _premier(rec, "nomacheteur", "acheteur", "nom_acheteur"),
        "objet": objet[:300],
        "titulaire": titulaire[:300],
        "cpv": cpv,
        "termes_objet": [m for m, _ in termes_trouves(objet_n)],
        "termes_corps": [m for m, _ in termes_trouves(corps_n)],
        "classe": classer(objet_n, corps_n),
        "amarante": "amarante" in normaliser_texte(titulaire) or (
            "amarante risk intelligence" in corps_n
            or "amarante international" in corps_n),
        "lien": _premier(rec, "url_avis") or (LIEN_AVIS.format(idweb) if idweb else ""),
    }


# ===========================================================================
# COLLECTE (API Opendatasoft v2.1)
# ===========================================================================
def construire_where(requete, depuis):
    """Recherche plein texte + borne de date (ODSQL)."""
    return "\"{}\" AND dateparution >= date'{}'".format(
        requete.replace('"', ""), depuis.isoformat())


def _fetch_http(url, params):
    import requests
    derniere = None
    for tentative in range(2):
        try:
            rep = requests.get(url, params=params, timeout=60)
            if rep.status_code in (429, 500, 502, 503, 504) and tentative == 0:
                time.sleep(3)
                continue
            if rep.status_code >= 400:
                raise RuntimeError("HTTP {} : {}".format(
                    rep.status_code, rep.text[:300]))
            return rep.json()
        except (requests.ConnectionError, requests.Timeout) as e:
            derniere = e
            time.sleep(3)
    raise RuntimeError("API BOAMP injoignable ({})".format(derniere))


def collecter(depuis, fetch=None, termes=TERMES, max_par_terme=None):
    """Interroge chaque terme, pagine, dedoublonne par idweb.
    Les erreurs sont COLLECTEES, jamais levees : un terme en echec
    n'interrompt pas la sonde."""
    fetch = fetch or _fetch_http
    plafond = MAX_PAR_TERME if max_par_terme is None else max_par_terme
    avis, par_terme, erreurs, champs = {}, {}, [], []
    for requete, _, niveau in termes:
        lus, total, offset = 0, None, 0
        try:
            while lus < plafond and offset + PAGE <= FENETRE_ODS:
                params = {"where": construire_where(requete, depuis),
                          "order_by": "dateparution desc",
                          "limit": min(PAGE, plafond - lus), "offset": offset}
                rep = fetch(URL_RECORDS, params) or {}
                if total is None:
                    total = rep.get("total_count")
                resultats = rep.get("results") or []
                if resultats and not champs:
                    champs = sorted(resultats[0].keys())
                for r in resultats:
                    a = normaliser(r)
                    if a["idweb"] and a["idweb"] not in avis:
                        avis[a["idweb"]] = a
                lus += len(resultats)
                offset += PAGE
                if len(resultats) < params["limit"]:
                    break
                time.sleep(PAUSE)
        except Exception as e:  # noqa: BLE001 - sonde : on journalise tout
            erreurs.append("{} : {}".format(requete, e))
        par_terme[requete] = {"total": total, "lus": lus, "niveau": niveau}
    return {"avis": list(avis.values()), "par_terme": par_terme,
            "erreurs": erreurs, "champs": champs}


def evaluer_rappel(avis, fetch=None, references=REFERENCES):
    """Pour chaque reference : captee par le lexique ? Sinon, on la
    recherche directement pour savoir si elle existe et POURQUOI elle a
    echappe (objet, classe)."""
    fetch = fetch or _fetch_http
    index = {a["idweb"]: a for a in avis}
    rappel = {}
    for idweb, libelle in references.items():
        if idweb in index:
            a = index[idweb]
            rappel[idweb] = {"libelle": libelle, "existe": True,
                             "captee": a["classe"] in CLASSES_RETENUES,
                             "classe": a["classe"], "objet": a["objet"]}
            continue
        entree = {"libelle": libelle, "existe": None, "captee": False,
                  "classe": None, "objet": ""}
        try:
            rep = fetch(URL_RECORDS, {"where": "idweb=\"{}\"".format(idweb),
                                      "limit": 1}) or {}
            res = rep.get("results") or []
            entree["existe"] = bool(res)
            if res:
                a = normaliser(res[0])
                entree.update(classe=a["classe"], objet=a["objet"])
        except Exception as e:  # noqa: BLE001
            entree["erreur"] = str(e)
        rappel[idweb] = entree
    return rappel


# ===========================================================================
# ANALYSE ET VERDICT
# ===========================================================================
def _titulaires(champ):
    return [t.strip() for t in re.split(r"[;\n]", champ or "") if t.strip()]


def analyser(avis, mois=MOIS):
    classes = Counter(a["classe"] for a in avis)
    retenus = [a for a in avis if a["classe"] in CLASSES_RETENUES]
    marches = [a for a in retenus if not a["resultat"]]
    resultats = [a for a in retenus if a["resultat"]]
    marches_titre = [a for a in marches if a["classe"] == "fort_objet"]
    return {
        "total": len(avis),
        "classes": classes,
        "retenus": retenus,
        "nb_marches": len(marches),
        "nb_resultats": len(resultats),
        "nb_marches_titre": len(marches_titre),
        "rythme_mensuel": round(len(marches_titre) / float(max(mois, 1)), 2),
        "termes_titre": Counter(m for a in retenus for m in a["termes_objet"]),
        "cpv": Counter(c for a in retenus for c in a["cpv"][:1]),
        "acheteurs": Counter(a["acheteur"] for a in retenus if a["acheteur"]),
        "titulaires": Counter(t for a in resultats
                              for t in _titulaires(a["titulaire"])),
        "sans_titulaire": sum(1 for a in resultats if not a["titulaire"]),
        "amarante": [a for a in avis if a["amarante"]],
    }


def verdict(rap, rappel, erreurs=None):
    """(code, explication). Lecon ADB : un echantillon vide n'est pas un
    verdict, c'est une sonde qui n'a pas pu conclure."""
    if rap["total"] == 0:
        motif = "aucun avis collecte"
        if erreurs:
            motif += " ({} erreur(s) d'acces)".format(len(erreurs))
        return ("NON CONCLU", motif)
    existantes = [r for r in rappel.values() if r.get("existe")]
    captees = [r for r in rappel.values() if r.get("captee")]
    if existantes and not captees:
        return ("LEXIQUE A REVOIR",
                "{} reference(s) presente(s) au BOAMP, aucune captee".format(
                    len(existantes)))
    if rap["rythme_mensuel"] >= SEUIL_RYTHME_FAISABLE:
        return ("FAISABLE", "{} avis de marche IE par mois (titre)".format(
            rap["rythme_mensuel"]))
    if rap["nb_marches"] > 0:
        return ("MARGINAL", "{} avis de marche IE par mois (titre), sous le "
                "seuil de {}".format(rap["rythme_mensuel"], SEUIL_RYTHME_FAISABLE))
    return ("INFAISABLE", "avis collectes mais aucun avis de marche IE confirme")


# ===========================================================================
# RAPPORT
# ===========================================================================
LIBELLES_CLASSES = {
    "fort_objet": "Terme fort dans le titre (retenu)",
    "fort_corps": "Terme fort dans le corps seulement (retenu, a surveiller)",
    "a_mesurer": "Terme ambigu seulement (non retenu)",
    "bruit": "Bruit connu : sondage, statistique, tourisme (non retenu)",
    "hors_analyste": "Hors perimetre, futur pole Analyste (non retenu)",
    "sans_terme": "Bruit serveur : mots disperses, expression absente",
}


def _top(compteur, n=15):
    return compteur.most_common(n)


def rapport_markdown(collecte, rap, rappel, v, depuis, aujourd):
    L = []
    L.append("# Sonde BOAMP IE (IE-0)")
    L.append("")
    L.append("Fenetre : {} au {} ({} mois). Plafond : {} avis par terme.".format(
        depuis.isoformat(), aujourd.isoformat(), MOIS, MAX_PAR_TERME))
    L.append("")
    L.append("## Verdict : {}".format(v[0]))
    L.append("")
    L.append(v[1])
    L.append("")
    L.append("## Rappel sur la verite terrain")
    L.append("")
    L.append("| idweb | Reference | Existe | Captee | Classe | Objet |")
    L.append("|---|---|---|---|---|---|")
    for idweb, r in rappel.items():
        existe = {True: "oui", False: "non", None: "?"}[r.get("existe")]
        L.append("| {} | {} | {} | {} | {} | {} |".format(
            idweb, r["libelle"], existe, "oui" if r["captee"] else "NON",
            r.get("classe") or "-", (r.get("objet") or "-")[:90]))
    L.append("")
    L.append("## Volume")
    L.append("")
    L.append("- Avis uniques collectes (avant confirmation locale) : {}".format(rap["total"]))
    L.append("- Retenus IE : {} (avis de marche {}, resultats {})".format(
        len(rap["retenus"]), rap["nb_marches"], rap["nb_resultats"]))
    L.append("- Avis de marche avec terme fort dans le titre : {} soit {} par mois".format(
        rap["nb_marches_titre"], rap["rythme_mensuel"]))
    L.append("")
    L.append("## Repartition par classe")
    L.append("")
    for cle, lib in LIBELLES_CLASSES.items():
        n = rap["classes"].get(cle, 0)
        part = 100.0 * n / rap["total"] if rap["total"] else 0
        L.append("- {} : {} ({:.0f} %)".format(lib, n, part))
    L.append("")
    L.append("## Par terme (serveur puis confirmation dans les titres retenus)")
    L.append("")
    L.append("| Terme | Niveau | Total serveur | Lus | Titres retenus |")
    L.append("|---|---|---|---|---|")
    for requete, motif, niveau in TERMES:
        pt = collecte["par_terme"].get(requete, {})
        L.append("| {} | {} | {} | {} | {} |".format(
            requete, niveau, pt.get("total"), pt.get("lus", 0),
            rap["termes_titre"].get(motif, 0)))
    for titre, cle in (("CPV principaux des avis retenus", "cpv"),
                       ("Acheteurs recurrents", "acheteurs"),
                       ("Titulaires des marches IE attribues", "titulaires")):
        L.append("")
        L.append("## " + titre)
        L.append("")
        top = _top(rap[cle])
        if not top:
            L.append("- (aucun)")
        for nom, n in top:
            libelle = nom
            if cle == "cpv" and DIVISIONS_CPV:
                libelle = "{} ({})".format(nom, cpv_reference.division_lisible(nom))
            L.append("- {} : {}".format(libelle, n))
    if rap["nb_resultats"]:
        L.append("")
        L.append("Resultats sans champ titulaire exploitable : {} / {}.".format(
            rap["sans_titulaire"], rap["nb_resultats"]))
    L.append("")
    L.append("## Mentions d'Amarante")
    L.append("")
    if not rap["amarante"]:
        L.append("- (aucune)")
    for a in rap["amarante"]:
        L.append("- {} {} | {} | {}".format(a["idweb"], a["date"], a["acheteur"],
                                            a["objet"][:90]))
    L.append("")
    L.append("## Diagnostic technique")
    L.append("")
    L.append("- Champs disponibles : {}".format(", ".join(collecte["champs"]) or "(inconnus)"))
    L.append("- Erreurs : {}".format(len(collecte["erreurs"])))
    for e in collecte["erreurs"][:20]:
        L.append("  - " + e)
    return "\n".join(L) + "\n"


COLONNES_CSV = ("idweb", "date", "nature", "classe", "acheteur", "objet",
                "termes_objet", "termes_corps", "cpv", "titulaire", "procedure",
                "amarante", "lien")


def ecrire_csv(avis, chemin):
    with open(chemin, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(COLONNES_CSV)
        for a in sorted(avis, key=lambda x: (x["classe"], x["date"]), reverse=False):
            w.writerow([" | ".join(a[c]) if isinstance(a[c], list) else a[c]
                        for c in COLONNES_CSV])


# ===========================================================================
# POINT D'ENTREE
# ===========================================================================
def main():
    aujourd = datetime.date.today()
    depuis = aujourd - datetime.timedelta(days=int(round(MOIS * 30.44)))
    print("Sonde BOAMP IE : {} termes, fenetre depuis {}".format(
        len(TERMES), depuis.isoformat()))
    collecte = collecter(depuis)
    rappel = evaluer_rappel(collecte["avis"])
    rap = analyser(collecte["avis"])
    v = verdict(rap, rappel, collecte["erreurs"])
    md = rapport_markdown(collecte, rap, rappel, v, depuis, aujourd)
    print(md)

    os.makedirs(SORTIE, exist_ok=True)
    with open(os.path.join(SORTIE, "rapport.md"), "w", encoding="utf-8") as f:
        f.write(md)
    ecrire_csv(collecte["avis"], os.path.join(SORTIE, "avis.csv"))
    resume = os.environ.get("GITHUB_STEP_SUMMARY")
    if resume:
        with open(resume, "a", encoding="utf-8") as f:
            f.write(md)
    print("Sorties ecrites dans {}/ (rapport.md, avis.csv).".format(SORTIE))


if __name__ == "__main__":
    main()
