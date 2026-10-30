# -*- coding: utf-8 -*-
"""
Radar Amarante - Sonde jetable IE-1 : que disent les DECP (donnees
essentielles de la commande publique) du marche de l'intelligence economique ?

Diagnostic ponctuel, declenchement MANUEL (workflow sonde_decp_ie.yml).
Ne touche a RIEN : ni Google Sheet, ni Postgres, ni etat, ni dashboard.
N'appelle AUCUN LLM (cout zero).

Source : DECP consolidees de decp.info (Colin Maudry), publiees chaque jour
ouvre sur data.gouv.fr en Parquet, licence ouverte. Une ligne par marche,
par version (modification) et par titulaire. Schema verifie dans
reference/schema_base.json du depot ColinMaudry/decp-processing.

Six questions :
  1. VOLUME       : combien de marches IE attribues, par an, pour quels montants ?
  2. RENOUVELLEMENT : lesquels arrivent a echeance dans les 24 mois (ou viennent
                    d'expirer) ? C'est la sortie commerciale principale.
  3. AMARANTE     : quel portefeuille, sous quels SIRET, avec quelle part de
                    titres que le lexique ne voit pas (titres opaques) ?
  4. CONCURRENTS  : portefeuilles d'ADIT, Decidento, Explore ; leurs acheteurs
                    sont des prospects IE prouves.
  5. RAPPEL       : retrouve-t-on AURA, Nice, Hauts-de-France, Nouvelle-Aquitaine ?
  6. COUVERTURE   : part des marches IE avec une duree et un titulaire connus.

Lexique = IE-0 corrige (probite, integrite des tiers, veille sectorielle,
exclusion due diligence immobiliere, revue de presse en terme ambigu).
Perimetre D1 : IE + Conformite, etudes geopolitiques hors perimetre.
"""

import csv
import datetime
import os
import re
import unicodedata
from collections import Counter, defaultdict

# ===========================================================================
# CONFIGURATION
# ===========================================================================
URL_DECP_PARQUET = ("https://www.data.gouv.fr/api/1/datasets/r/"
                    "11cea8e8-df3e-4ed1-932b-781e2635e432")
FICHIER = os.environ.get("SONDE_DECP_FICHIER") or "decp.parquet"
SORTIE = os.environ.get("SONDE_SORTIE") or "sonde_decp_ie_sortie"
HORIZON_MOIS = 24        # echeances a venir prises en compte
RETRO_MOIS = 6           # echeances recentes : remise en concurrence en cours

# ===========================================================================
# LEXIQUE (IE-0 corrige)
# ===========================================================================
TERMES = [
    ("intelligence economique", "fort"),
    ("intelligence strategique", "fort"),
    ("securite economique", "fort"),
    ("renseignement economique", "fort"),
    ("veille strategique", "fort"),
    ("veille concurrentielle", "fort"),
    ("veille economique", "fort"),
    ("veille sectorielle", "fort"),
    ("due diligence", "fort"),
    ("diligence raisonnable", "fort"),
    ("evaluation des tiers", "fort"),
    ("integrite des tiers", "fort"),
    ("cartographie des acteurs", "fort"),
    ("anticorruption", "fort"),
    ("anti-corruption", "fort"),
    ("probite", "fort"),
    ("sapin ii", "fort"),
    ("sapin 2", "fort"),
    ("osint", "fort"),
    ("veille technologique", "a_mesurer"),
    ("veille reglementaire", "a_mesurer"),
    ("etudes sectorielles", "a_mesurer"),
    ("intelligence territoriale", "a_mesurer"),
    ("revue de presse", "a_mesurer"),
    ("veille media", "a_mesurer"),
]
BRUIT = ("sondage", "satisfaction", "touris", "statistique", "barometre",
         "frequentation", "panel", "enquete telephonique", "recensement",
         "comptage", "evaluation de politique", "evaluation des politiques",
         "evaluation du programme", "evaluation d'impact", "hackathon")
# Exclusions par combinaison : (terme present, ET sous-chaine presente).
BRUIT_COMBINE = (("due diligence", "immobili"),)
ANALYSTE = ("observatoire", "geopolitique", "geostrategique", "prospective")
NOYAU_IE = ("intelligence economique", "intelligence strategique",
            "securite economique", "due diligence", "anticorruption",
            "anti-corruption")

# Titulaires surveilles (mot entier dans le nom). KPMG et EY sont exclus :
# trop d'activite hors IE, ils apparaissent quand meme dans le classement
# des titulaires de marches IE.
AMARANTE = "amarante"
CONCURRENTS = ("adit", "decidento", "explore")

# Verite terrain (IE-0) : (libelle, mots requis dans l'acheteur, motif objet).
REFERENCES = [
    ("AURA Entreprises, cellule d'intelligence strategique (2024)",
     ("auvergne",), "intelligence strategique"),
    ("Metropole Nice Cote d'Azur, prestations d'IE (2025)",
     ("nice",), "intelligence economique"),
    ("Region Hauts-de-France, dispositif d'IE (2025)",
     ("hauts",), "intelligence economique"),
    ("Region Nouvelle-Aquitaine, feuille de route IE (2026)",
     ("aquitaine",), "intelligence economique"),
]

# ===========================================================================
# TEXTE ET CLASSEMENT
# ===========================================================================
def normaliser_texte(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("’", "'").replace("‘", "'").lower()
    return re.sub(r"\s+", " ", s).strip()


def _contient_mot(texte_n, motif):
    return re.search(r"(?<![a-z0-9])" + re.escape(motif) + r"(?![a-z0-9])",
                     texte_n) is not None


def termes_trouves(texte_n):
    return [(m, n) for m, n in TERMES if _contient_mot(texte_n, m)]


def classer(objet):
    """hors_analyste > bruit > ie > a_mesurer > sans_terme."""
    o = normaliser_texte(objet)
    t = termes_trouves(o)
    if any(x in o for x in ANALYSTE) and not any(_contient_mot(o, m) for m in NOYAU_IE):
        return "hors_analyste"
    if any(x in o for x in BRUIT) or any(
            _contient_mot(o, a) and b in o for a, b in BRUIT_COMBINE):
        return "bruit"
    if any(n == "fort" for _, n in t):
        return "ie"
    if t:
        return "a_mesurer"
    return "sans_terme"


def regex_sql(motifs):
    """Regex RE2 (DuckDB) pour le pre-filtre serveur. \\b est une frontiere
    ASCII : correcte ici car le texte est passe par strip_accents."""
    return r"\b(" + "|".join(re.escape(m) for m in motifs) + r")\b"


# ===========================================================================
# CHARGEMENT (DuckDB, import paresseux : les tests n'en dependent pas)
# ===========================================================================
COLONNES_UTILES = ("uid", "id", "objet", "acheteur_id", "acheteur_nom",
                   "acheteur_region_nom", "titulaire_id", "titulaire_nom",
                   "montant", "montant_rationalise", "codeCPV", "procedure",
                   "dureeMois", "dureeRestanteMois", "offresRecues",
                   "dateNotification", "donneesActuelles", "modification_id")


def charger(chemin=FICHIER):
    """Renvoie (lignes, colonnes_presentes, nb_lignes_total). Pre-filtre :
    objet qui matche le lexique OU titulaire surveille. Les colonnes absentes
    du fichier sont simplement ignorees (le schema publie peut evoluer)."""
    import duckdb
    con = duckdb.connect()
    desc = con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [chemin]).fetchall()
    presentes = [d[0] for d in desc]
    cols = [c for c in COLONNES_UTILES if c in presentes]
    total = con.execute("SELECT count(*) FROM read_parquet(?)", [chemin]).fetchone()[0]
    rx_objet = regex_sql([m for m, _ in TERMES])
    rx_tit = regex_sql((AMARANTE,) + CONCURRENTS)
    conds = ["(regexp_matches(lower(strip_accents(coalesce(CAST(objet AS VARCHAR), ''))), ?)"
             " OR regexp_matches(lower(strip_accents(coalesce(CAST(titulaire_nom AS VARCHAR), ''))), ?))"]
    if "donneesActuelles" in presentes:
        conds.append("coalesce(donneesActuelles, true)")
    q = "SELECT {} FROM read_parquet(?) WHERE {}".format(
        ", ".join('"{}"'.format(c) for c in cols), " AND ".join(conds))
    cur = con.execute(q, [chemin, rx_objet, rx_tit])
    noms = [d[0] for d in cur.description]
    lignes = [dict(zip(noms, r)) for r in cur.fetchall()]
    return lignes, presentes, total


# ===========================================================================
# AGREGATION PAR MARCHE
# ===========================================================================
def _date(v):
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _nombre(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # ecarte NaN


def ajouter_mois(d, mois):
    m = d.month - 1 + int(mois)
    annee, mois_ = d.year + m // 12, m % 12 + 1
    jours = [31, 29 if annee % 4 == 0 and (annee % 100 != 0 or annee % 400 == 0) else 28,
             31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mois_ - 1]
    return datetime.date(annee, mois_, min(d.day, jours))


def _montant(l):
    """Montant rationalise (aberrations corrigees par decp.info), sinon declare."""
    m = _nombre(l.get("montant_rationalise"))
    return m if m is not None else _nombre(l.get("montant"))


def _cle(l):
    return l.get("uid") or "{}|{}|{}".format(l.get("acheteur_id"), l.get("id"),
                                             normaliser_texte(l.get("objet"))[:80])


def _titulaire_surveille(nom_n):
    if _contient_mot(nom_n, AMARANTE):
        return "amarante"
    for c in CONCURRENTS:
        if _contient_mot(nom_n, c):
            return c
    return ""


def agreger(lignes, aujourd=None):
    """Lignes (marche x version x titulaire) -> marches. Montant = maximum
    (repete a l'identique sur chaque co-titulaire, ne jamais le sommer)."""
    aujourd = aujourd or datetime.date.today()
    par_cle = defaultdict(list)
    for l in lignes:
        par_cle[_cle(l)].append(l)
    marches = []
    for cle, ls in par_cle.items():
        # Version la plus recente si plusieurs modifications survivent au filtre.
        ls.sort(key=lambda x: _nombre(x.get("modification_id")) or 0)
        der = ls[-1]
        titulaires, ids, paires, surveilles = [], [], [], set()
        for l in ls:
            nom = str(l.get("titulaire_nom") or "").strip()
            tid = str(l.get("titulaire_id") or "").strip()
            if nom and nom not in titulaires:
                titulaires.append(nom)
            if tid and tid not in ids:
                ids.append(tid)
            if (nom or tid) and (nom, tid) not in paires:
                paires.append((nom, tid))
            s = _titulaire_surveille(normaliser_texte(nom))
            if s:
                surveilles.add(s)
        montants = [_montant(l) for l in ls]
        montants = [m for m in montants if m is not None]
        montant = max(montants) if montants else None
        notif = _date(der.get("dateNotification"))
        duree = _nombre(der.get("dureeMois"))
        fin = ajouter_mois(notif, duree) if notif and duree and duree > 0 else None
        restant = None
        if fin:
            restant = (fin.year - aujourd.year) * 12 + (fin.month - aujourd.month)
        marches.append({
            "cle": cle,
            "objet": str(der.get("objet") or "")[:300],
            "classe": classer(der.get("objet")),
            "termes": [m for m, _ in termes_trouves(normaliser_texte(der.get("objet")))],
            "acheteur": str(der.get("acheteur_nom") or "").strip(),
            "acheteur_id": str(der.get("acheteur_id") or "").strip(),
            "region": str(der.get("acheteur_region_nom") or "").strip(),
            "titulaires": titulaires,
            "titulaire_ids": ids,
            "paires": paires,
            "surveilles": sorted(surveilles),
            "montant": montant,
            "cpv": str(der.get("codeCPV") or ""),
            "procedure": str(der.get("procedure") or ""),
            "offres": _nombre(der.get("offresRecues")),
            "notification": notif,
            "duree_mois": duree,
            "fin": fin,
            "mois_restants": restant,
        })
    return marches


# ===========================================================================
# ANALYSE ET VERDICT
# ===========================================================================
def _mediane(valeurs):
    v = sorted(x for x in valeurs if x is not None)
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def evaluer_rappel(marches, references=REFERENCES):
    res = []
    for libelle, mots, motif in references:
        trouves = [m for m in marches
                   if all(x in normaliser_texte(m["acheteur"]) for x in mots)
                   and _contient_mot(normaliser_texte(m["objet"]), motif)]
        res.append({"libelle": libelle, "trouve": bool(trouves),
                    "marches": trouves[:3]})
    return res


def analyser(marches, aujourd=None):
    aujourd = aujourd or datetime.date.today()
    ie = [m for m in marches if m["classe"] == "ie"]
    debut = ajouter_mois(aujourd, -RETRO_MOIS)
    horizon = ajouter_mois(aujourd, HORIZON_MOIS)
    renouv = sorted([m for m in ie if m["fin"] and debut <= m["fin"] <= horizon],
                    key=lambda m: m["fin"])
    portefeuilles = {}
    for nom in (AMARANTE,) + CONCURRENTS:
        lot = [m for m in marches if nom in m["surveilles"]]
        portefeuilles[nom] = {
            "marches": sorted(lot, key=lambda m: m["notification"] or datetime.date.min,
                              reverse=True),
            "ie": sum(1 for m in lot if m["classe"] == "ie"),
            "opaques": sum(1 for m in lot if m["classe"] == "sans_terme"),
            "ids": sorted({i for m in lot for n, i in m["paires"]
                           if i and _titulaire_surveille(normaliser_texte(n)) == nom}),
            "acheteurs": Counter(m["acheteur"] for m in lot if m["acheteur"]),
        }
    return {
        "total": len(marches),
        "classes": Counter(m["classe"] for m in marches),
        "ie": ie,
        "par_annee": Counter(m["notification"].year for m in ie if m["notification"]),
        "montant_median": _mediane(m["montant"] for m in ie),
        "offres_mediane": _mediane(m["offres"] for m in ie),
        "duree_connue": sum(1 for m in ie if m["duree_mois"]),
        "titulaire_connu": sum(1 for m in ie if m["titulaires"]),
        "renouvellements": renouv,
        "titulaires_ie": Counter(t for m in ie for t in m["titulaires"]),
        "acheteurs_ie": Counter(m["acheteur"] for m in ie if m["acheteur"]),
        "portefeuilles": portefeuilles,
        "termes": Counter(t for m in ie for t in m["termes"]),
    }


def verdict(rap, rappel, charge_ok=True):
    if not charge_ok or rap["total"] == 0:
        return ("NON CONCLU", "fichier DECP non charge ou aucune ligne retenue")
    if not rap["ie"]:
        return ("INFAISABLE", "aucun marche IE dans les DECP")
    if not any(r["trouve"] for r in rappel):
        return ("COUVERTURE A VERIFIER",
                "{} marches IE mais aucune reference retrouvee".format(len(rap["ie"])))
    part = rap["duree_connue"] / float(len(rap["ie"]))
    if part >= 0.5 and rap["renouvellements"]:
        return ("EXPLOITABLE", "{} marches IE, duree connue pour {:.0f} %, "
                "{} echeance(s) dans la fenetre".format(
                    len(rap["ie"]), 100 * part, len(rap["renouvellements"])))
    return ("LIMITE", "{} marches IE, duree connue pour {:.0f} %, {} echeance(s) "
            "dans la fenetre".format(len(rap["ie"]), 100 * part,
                                     len(rap["renouvellements"])))


# ===========================================================================
# RAPPORT
# ===========================================================================
def _eur(v):
    if v is None:
        return "n.c."
    return "{:,.0f} EUR".format(v).replace(",", " ")


def _ligne_marche(m):
    return "| {} | {} | {} | {} | {} | {} |".format(
        m["fin"].isoformat() if m["fin"] else "?", m["acheteur"][:45],
        m["objet"][:80].replace("|", "/"), " ; ".join(m["titulaires"])[:60],
        _eur(m["montant"]), int(m["duree_mois"]) if m["duree_mois"] else "?")


def rapport_markdown(rap, rappel, v, total_lignes, colonnes, aujourd):
    L = ["# Sonde DECP IE (IE-1)", "",
         "Date : {}. Lignes DECP lues : {}. Marches retenus au pre-filtre : {}.".format(
             aujourd.isoformat(), total_lignes, rap["total"]), "",
         "## Verdict : {}".format(v[0]), "", v[1], "",
         "## Rappel sur la verite terrain", ""]
    for r in rappel:
        if r["trouve"]:
            m = r["marches"][0]
            L.append("- OUI : {} -> {} | {} | {}".format(
                r["libelle"], m["acheteur"], _eur(m["montant"]),
                " ; ".join(m["titulaires"])[:80]))
        else:
            L.append("- NON : {}".format(r["libelle"]))
    L += ["", "## Volume des marches IE attribues", ""]
    L.append("- Marches IE : {} (duree connue : {}, titulaire connu : {})".format(
        len(rap["ie"]), rap["duree_connue"], rap["titulaire_connu"]))
    L.append("- Montant median declare : {}".format(_eur(rap["montant_median"])))
    L.append("- Offres recues (mediane) : {}".format(
        rap["offres_mediane"] if rap["offres_mediane"] is not None else "n.c."))
    for an in sorted(rap["par_annee"]):
        L.append("- {} : {} marche(s)".format(an, rap["par_annee"][an]))
    L += ["", "## Classes au pre-filtre", ""]
    for k, n in rap["classes"].most_common():
        L.append("- {} : {}".format(k, n))
    L += ["", "## Echeances : de {} mois passes a {} mois a venir".format(
        RETRO_MOIS, HORIZON_MOIS), "",
        "| Fin estimee | Acheteur | Objet | Titulaire(s) | Montant | Duree (mois) |",
        "|---|---|---|---|---|---|"]
    for m in rap["renouvellements"][:60]:
        L.append(_ligne_marche(m))
    if not rap["renouvellements"]:
        L.append("| (aucune) | | | | | |")
    for nom in (AMARANTE,) + CONCURRENTS:
        p = rap["portefeuilles"][nom]
        L += ["", "## Portefeuille {}".format(nom.upper()), "",
              "- Marches : {} (dont IE : {}, titres opaques : {})".format(
                  len(p["marches"]), p["ie"], p["opaques"]),
              "- Identifiants titulaire : {}".format(", ".join(p["ids"]) or "(aucun)")]
        top = p["acheteurs"].most_common(10)
        if top:
            L.append("- Acheteurs : " + " ; ".join("{} ({})".format(a, n) for a, n in top))
        if p["marches"]:
            L += ["", "| Fin estimee | Acheteur | Objet | Titulaire(s) | Montant | Duree (mois) |",
                  "|---|---|---|---|---|---|"]
            for m in p["marches"][:25]:
                L.append(_ligne_marche(m))
    for titre, cle in (("Titulaires des marches IE", "titulaires_ie"),
                       ("Acheteurs IE recurrents", "acheteurs_ie"),
                       ("Termes declencheurs", "termes")):
        L += ["", "## " + titre, ""]
        top = rap[cle].most_common(20)
        L += ["- {} : {}".format(k, n) for k, n in top] or ["- (aucun)"]
    L += ["", "## Diagnostic technique", "",
          "- Colonnes du fichier : {}".format(", ".join(colonnes) or "(inconnues)")]
    return "\n".join(L) + "\n"


COLONNES_CSV = ("classe", "fin", "mois_restants", "notification", "duree_mois",
                "acheteur", "acheteur_id", "region", "objet", "titulaires",
                "titulaire_ids", "surveilles", "montant", "offres", "cpv",
                "procedure", "termes")


def ecrire_csv(marches, chemin):
    with open(chemin, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(COLONNES_CSV)
        for m in sorted(marches, key=lambda x: (x["classe"], str(x["fin"] or ""))):
            w.writerow([" | ".join(m[c]) if isinstance(m[c], list)
                        else ("" if m[c] is None else m[c]) for c in COLONNES_CSV])


# ===========================================================================
# POINT D'ENTREE
# ===========================================================================
def main():
    aujourd = datetime.date.today()
    try:
        lignes, colonnes, total = charger(FICHIER)
        ok = True
    except Exception as e:  # noqa: BLE001 - sonde : on journalise
        print("(erreur) Chargement DECP impossible : {}".format(e))
        lignes, colonnes, total, ok = [], [], 0, False
    marches = agreger(lignes, aujourd=aujourd)
    rap = analyser(marches, aujourd=aujourd)
    rappel = evaluer_rappel(marches)
    v = verdict(rap, rappel, charge_ok=ok)
    md = rapport_markdown(rap, rappel, v, total, colonnes, aujourd)
    print(md)
    os.makedirs(SORTIE, exist_ok=True)
    with open(os.path.join(SORTIE, "rapport.md"), "w", encoding="utf-8") as f:
        f.write(md)
    ecrire_csv(marches, os.path.join(SORTIE, "marches.csv"))
    resume = os.environ.get("GITHUB_STEP_SUMMARY")
    if resume:
        with open(resume, "a", encoding="utf-8") as f:
            f.write(md)
    print("Sorties ecrites dans {}/ (rapport.md, marches.csv).".format(SORTIE))


if __name__ == "__main__":
    main()
