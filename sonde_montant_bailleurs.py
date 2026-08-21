# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE MONTANT BAILLEURS (jetable, diagnostic)
===============================================================================

QUESTION
--------
Pour chaque bailleur multilateral (BM avis, BM projets amont, AfDB, ADB, EBRD,
IDB, UNGM), un MONTANT est-il disponible et exploitable au niveau de la notice ?
Si oui, sous quelle forme (champ structure vs texte libre), avec quel taux de
remplissage, quelle devise, quels exemples ?

POURQUOI UNE SONDE D'ABORD
--------------------------
Discipline projet : on ne modifie aucun collecteur tant qu'on n'a pas CONFIRME,
source par source, que le montant existe et est fiable. Cette sonde ne fait que
LIRE : aucune ecriture Sheet/Postgres, aucun LLM, aucun secret, sortie code 0.

DISTINCTION DE DOCTRINE (a trancher APRES la sonde)
---------------------------------------------------
Deux montants tres differents peuvent apparaitre :
  - MONTANT PROJET / ENVELOPPE : cout total du projet (ex. BM totalamt =
    plusieurs centaines de M USD). Proxy de la TAILLE du deploiement, pas du
    marche a gagner. Deja utilise par bm_projets pour le score commercial.
  - MONTANT MARCHE / LOT : la valeur du contrat precis (ce qui a une vraie
    valeur commerciale de prospection).
La sonde rapporte les deux si presents ; le choix d'affichage (et l'etiquette
"enveloppe projet" vs "montant marche") se fait au vu du resultat.

CE QUE LA SONDE SAIT DEJA (lecture de code, sans reseau)
--------------------------------------------------------
  - BM projets amont (bm_projets) : le montant ENVELOPPE (totalamt /
    lendprojectcost) est deja recupere et parse par _montant(), mais n'est
    ECRIT dans AUCUNE colonne (COLONNES_BMP ne l'expose pas). Feasibilite
    d'exposition = confirmee ; la sonde ne fait que quantifier le remplissage.
  - IDB : les ATTRIBUTIONS portent total_amount / idb_amount (deja lus). Les
    AVIS (normaliser) n'exposent aucun montant a ce jour.

USAGE
-----
    python sonde_montant_bailleurs.py                 # toutes les sources
    SONDE_SOURCE=bm_avis python sonde_montant_bailleurs.py   # une seule
    SONDE_ECH=60 python sonde_montant_bailleurs.py    # taille d'echantillon

Les sondes tournent via GitHub Actions (le runner atteint les APIs externes) ;
en local hors reseau, les probers "live" echouent proprement (rapportes ERREUR)
et n'empechent pas les autres. Le COEUR (scanner de champs) est teste offline.
"""

import collections
import os
import re
import sys


# ===========================================================================
# COEUR TESTABLE : detection de montant dans un enregistrement / un texte.
# ===========================================================================

# Cles de champ qui trahissent un montant. Large volontairement (une sonde
# doit ratisser). Inclut les cles connues (totalamt, lendprojectcost, amt).
CLE_MONTANT = re.compile(
    r"(?i)(montant|amount|\bamt\b|amt$|value|valeur|\bcost\b|cou[tû]|budget|"
    r"price|prix|estimat|contract[_\s-]?value|financ|envelop|ceiling|"
    r"award|totalamt|lendprojectcost|idb_amount|total_amount)")

# Valeurs vides / sentinelles a ne pas compter comme un montant renseigne.
_VIDES = {"", "null", "none", "n/a", "na", "0", "0.0", "0,0", "-", "inconnu",
          "nc", "n.c."}

# Mention de montant dans du texte libre : un nombre accole a une devise (ou
# l'inverse), ou un ordre de grandeur (million / milliard).
_MENTION = re.compile(
    r"(?:(?:USD|EUR|GBP|XOF|XAF|CFA|\$|€|£)\s?\d[\d\s.,]{2,})"
    r"|(?:\d[\d\s.,]{2,}\s?(?:USD|EUR|GBP|XOF|XAF|CFA|million|milliard|billion|M\b))",
    re.I)


def _est_vide(v):
    return str(v).strip().lower() in _VIDES


def scanner_record(rec):
    """Champs d'un dict dont la CLE trahit un montant et dont la valeur est
    renseignee. Retour : [(cle, valeur_tronquee)]. Fonction PURE."""
    out = []
    if not isinstance(rec, dict):
        return out
    for cle, val in rec.items():
        if CLE_MONTANT.search(str(cle)) and not _est_vide(val):
            out.append((str(cle), str(val)[:50]))
    return out


def scanner_texte(txt, maxi=6):
    """Mentions de montant reperees dans du texte libre. Retour : liste de
    fragments courts, dedupliquee, bornee. Fonction PURE."""
    vu, out = set(), []
    for m in _MENTION.finditer(txt or ""):
        frag = re.sub(r"\s+", " ", m.group(0)).strip()[:50]
        if frag.lower() not in vu:
            vu.add(frag.lower())
            out.append(frag)
        if len(out) >= maxi:
            break
    return out


def analyser_echantillon(records, texte_sup=""):
    """Agrege un echantillon : champs montant structures (avec taux de
    remplissage et exemples) + mentions en texte libre. Fonction PURE."""
    champs = collections.OrderedDict()
    blob = [texte_sup or ""]
    n = 0
    for rec in records or []:
        if isinstance(rec, dict):
            n += 1
            for cle, val in rec.items():
                if val is not None and str(val):
                    blob.append(str(val))
            for cle, val in scanner_record(rec):
                d = champs.setdefault(cle, {"rempli": 0, "exemples": []})
                d["rempli"] += 1
                if len(d["exemples"]) < 3 and val not in d["exemples"]:
                    d["exemples"].append(val)
        else:
            blob.append(str(rec))
    return {"n": n, "champs": champs, "mentions": scanner_texte("\n".join(blob))}


def verdict(rap):
    """Traduit un rapport d'echantillon en verdict lisible."""
    n = max(rap.get("n", 0), 1)
    forts = [c for c, d in rap["champs"].items() if d["rempli"] / n >= 0.30]
    if forts:
        return "EXPLOITABLE", "champ(s) structure(s) rempli(s) : " + ", ".join(forts)
    if rap["champs"]:
        rares = ", ".join(rap["champs"].keys())
        return "PARTIEL", "champ(s) present(s) mais peu rempli(s) : " + rares
    if rap["mentions"]:
        return "TEXTE_LIBRE", "montant dans la description, a parser (regex)"
    return "ABSENT", "aucun montant detecte dans l'echantillon"


# ===========================================================================
# PROBERS PAR SOURCE : obtiennent un echantillon de records BRUTS.
# Chacun renvoie (label_transport, records, texte_libre_supplementaire).
# Les probers "inject-friendly" acceptent un fetch (pour les tests offline) ;
# les "live_only" ne tournent qu'avec reseau (Actions).
# ===========================================================================

ECH = int(os.environ.get("SONDE_ECH", "40"))


def prober_bm_amont(fetch=None):
    import bm_projets as m
    recs = m.collecter_flux(fetch=fetch)
    return ("BM projets amont -- JSON search.worldbank.org (enveloppe projet)",
            recs[:ECH], "")


def prober_afdb(fetch=None):
    import afdb_radar as m
    xml = m.collecter_flux(fetch=fetch)
    items = m.parser_items(xml)
    return ("AfDB -- RSS 2.0 (montant eventuel dans <description>)",
            items[:ECH], xml[:300000])


def prober_adb(fetch=None):
    import adb_radar as m
    txt = m.collecter_pages(fetch=fetch, pages=1)
    notices = m.parser_notices(txt)
    return ("ADB -- page tenders", notices[:ECH], txt[:300000])


def prober_ebrd(fetch=None):
    import ebrd_radar as m
    html = m.collecter_html(fetch=fetch)
    notices = m.parser_notices(html)
    return ("EBRD -- scrape ECEPP", notices[:ECH], html[:300000])


def prober_bm_avis(fetch=None):
    # Live only : collecte_bm() gere sa propre session (pas d'injection).
    import ted_complet_bm as m
    bruts, _, _ = m.collecte_bm()
    return ("BM avis -- procnotices JSON", bruts[:ECH], "")


def prober_idb(fetch=None):
    # Best-effort, live only. Les AVIS IDB (CSV) n'exposent aucun montant a la
    # normalisation ; on echantillonne les premieres lignes du CSV via les
    # helpers du collecteur (url_du_fichier + lignes_csv en flux, arret tot)
    # pour voir si une colonne montant existe cote datastore.
    import idb_radar as m
    url = m.url_du_fichier()
    recs = []
    for i, ligne in enumerate(m.lignes_csv(url)):
        recs.append(ligne)
        if i + 1 >= ECH:
            break
    return ("IDB avis -- CSV datastore (colonnes brutes)", recs, "")


def prober_ungm(fetch=None):
    # Best-effort. Le montant UNGM (s'il existe) est sur la fiche detail, pas
    # sur le listing. On scanne la page publique en texte libre : signal
    # grossier "y a-t-il des montants quelque part ?".
    import ungm_radar as m
    import requests
    url = getattr(m, "PAGE_PUBLIQUE", None)
    if not url:
        raise RuntimeError("PAGE_PUBLIQUE absente d'ungm_radar.")
    rep = requests.get(url, headers=getattr(m, "ENTETES", {}), timeout=45)
    rep.raise_for_status()
    return ("UNGM -- listing public (scan texte)", [], rep.text[:400000])


# Registre : nom -> (fonction, live_only). Ordre d'affichage.
SOURCES = collections.OrderedDict([
    ("bm_amont", (prober_bm_amont, False)),
    ("bm_avis", (prober_bm_avis, True)),
    ("afdb", (prober_afdb, False)),
    ("adb", (prober_adb, False)),
    ("ebrd", (prober_ebrd, False)),
    ("idb", (prober_idb, True)),
    ("ungm", (prober_ungm, True)),
])


def rapport_source(nom, fetch=None):
    """Execute un prober, isole des erreurs. Retour : dict de rapport."""
    fn, live_only = SOURCES[nom]
    try:
        label, records, texte = fn(fetch=fetch)
    except Exception as e:
        return {"nom": nom, "erreur": "{}: {}".format(type(e).__name__, e)}
    rap = analyser_echantillon(records, texte)
    verd, detail = verdict(rap)
    return {"nom": nom, "label": label, "n": rap["n"], "champs": rap["champs"],
            "mentions": rap["mentions"], "verdict": verd, "detail": detail}


def imprimer(rap):
    print("\n" + "=" * 74)
    print("SOURCE : {}".format(rap["nom"]))
    if "erreur" in rap:
        print("  ERREUR (ignoree, les autres continuent) : {}".format(rap["erreur"]))
        return
    print("  Transport : {}".format(rap["label"]))
    print("  Echantillon : {} enregistrement(s)".format(rap["n"]))
    print("  VERDICT : {}  ({})".format(rap["verdict"], rap["detail"]))
    if rap["champs"]:
        print("  Champs montant structures :")
        for cle, d in rap["champs"].items():
            taux = (100.0 * d["rempli"] / rap["n"]) if rap["n"] else 0.0
            print("    - {:<22} rempli {:>3}/{:<3} ({:>3.0f}%)  ex: {}".format(
                cle[:22], d["rempli"], rap["n"], taux,
                " | ".join(d["exemples"]) or "-"))
    if rap["mentions"]:
        print("  Mentions en texte libre (a parser) :")
        for frag in rap["mentions"]:
            print("    - {}".format(frag))


def main():
    cible = os.environ.get("SONDE_SOURCE", "").strip().lower()
    noms = [cible] if cible in SOURCES else list(SOURCES.keys())
    if cible and cible not in SOURCES:
        print("Source inconnue : {!r}. Sources : {}".format(
            cible, ", ".join(SOURCES)))
        sys.exit(0)
    print("#" * 74)
    print("SONDE MONTANT BAILLEURS -- echantillon cible {} / source".format(ECH))
    print("Rappel doctrine : montant PROJET (enveloppe) != montant MARCHE (lot).")
    print("#" * 74)
    synth = []
    for nom in noms:
        rap = rapport_source(nom)
        imprimer(rap)
        synth.append(rap)
    print("\n" + "#" * 74)
    print("SYNTHESE")
    print("#" * 74)
    for r in synth:
        if "erreur" in r:
            print("  {:<10} : ERREUR ({})".format(r["nom"], r["erreur"][:48]))
        else:
            print("  {:<10} : {:<12} n={:<3} {}".format(
                r["nom"], r["verdict"], r["n"], r["detail"][:60]))
    print("\nProchaine etape : pour toute source EXPLOITABLE, on branche le "
          "champ en colonne (additif, en derniere position) + tests apparies.")
    sys.exit(0)


if __name__ == "__main__":
    main()
