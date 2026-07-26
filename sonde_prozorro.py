# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE PROZORRO (jetable) : valider AVANT le collecteur.
=========================================================================

Prozorro est le systeme d'e-procurement public ukrainien (marches d'Etat et
municipaux). Zone COEUR du radar (reconstruction, deploiements massifs), mais
source d'un profil nouveau pour nous : gros volume, langue ukrainienne, flux
de changements plutot que recherche plein-texte. On NE code PAS le collecteur
avant d'avoir vu le brut de nos propres yeux (lecon UNGM : ne pas avoir dumpe
le brut a coute deux tours).

CE QUE CETTE SONDE DOIT TRANCHER (aucune supposition, on dumpe le reel) :
  A. Le flux repond-il, et quelle est la forme de l'enveloppe ? Peut-on
     recuperer les avis RECENTS (descending) et estimer le debit (volume) ?
  B. Le DETAIL d'un avis (GET /tenders/{id}) : quels champs exploitables
     (titre, statut, valeur, acheteur, lots) ? Le CPV est-il present et au
     format qu'on sait deja filtrer (divisions a 2 chiffres) ?
  C. Le mode OCDS (opt_schema=ocds) est-il plus propre a parser que le
     schema natif ? (choix d'architecture pour le futur collecteur)
  D. Langue : combien de titres sont en cyrillique (donc le pre-filtre
     mots-cles FR/EN ne suffira pas -- il faudra filtrer sur le CPV et
     laisser le LLM lire l'ukrainien, ou traduire).

FAITS ETABLIS (doc OCP + openprocurement 2.5, verifies) :
  - Base : https://public-api.prozorro.gov.ua/api/2.5
  - Liste /tenders : renvoie surtout {id, dateModified} + next_page.uri.
    Le detail vit sur GET /tenders/{id}. Pagination par next_page.
  - Lots : items[].classification.scheme = "ДК021", id au format CPV
    8 chiffres (ex "55523100-3"). Nos divisions (2 premiers chiffres)
    s'appliquent directement.
  - opt_schema=ocds -> reponse au standard Open Contracting.
  - Certains marches sont caviardes (securite nationale). A prevoir.

Aucune ecriture. Aucun appel paye (zero LLM). Sortie toujours en code 0.
Ne depend que de `requests` (comme sonde_sources.py). Jetable : a supprimer
une fois le collecteur ecrit.
"""

import json
import re
import sys

try:
    import requests
except Exception:                                    # pragma: no cover
    print("requests indisponible")
    sys.exit(0)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE = "https://public-api.prozorro.gov.ua/api/2.5"
ENDPOINT_LISTE = BASE + "/tenders"
ENDPOINT_DETAIL = BASE + "/tenders/{}"
TIMEOUT = 45
NAVIGATEUR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# Combien d'avis recents on echantillonne pour le detail (petit : on VALIDE la
# forme, on ne collecte pas). Chaque detail = 1 requete, on reste modere.
NB_DETAILS = 15

# Divisions CPV qui nous interessent, MIROIR de ted_complet_v14 (on garde la
# sonde autonome : elle n'importe pas le coeur, elle doit tourner seule). Sert
# uniquement a MESURER la filtrabilite, pas a filtrer pour de vrai.
DIVISIONS_CPV_INTERET = {
    "09", "14", "32", "45", "65", "71", "76", "90",   # largement admises
    "75", "79",                                        # conditionnelles
}

RESULTATS = []


def _titre(t):
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


def _verdict(nom, ok, detail):
    RESULTATS.append((nom, ok, detail))
    print("  => {} : {}".format("OK" if ok else "a creuser", detail))


def _plat(t, n=None):
    s = re.sub(r"\s+", " ", str(t or "")).strip()
    return s[:n] if n else s


def _est_cyrillique(t):
    """Vrai si la chaine contient au moins un caractere cyrillique."""
    return bool(re.search(r"[\u0400-\u04FF]", str(t or "")))


def _division_cpv(code):
    """2 premiers chiffres d'un id CPV/DK021 (ex '45250000' -> '45')."""
    m = re.match(r"\s*(\d{2})", str(code or ""))
    return m.group(1) if m else ""


def _session():
    s = requests.Session()
    s.headers.update({"User-Agent": NAVIGATEUR, "Accept": "application/json"})
    return s


# ===========================================================================
# A -- Le flux repond, forme de l'enveloppe, fraicheur, debit
# ===========================================================================

def sonde_a(s):
    _titre("A -- Prozorro : flux /tenders (recent d'abord), enveloppe, debit")
    # descending=1 = les plus recents d'abord (le defaut part de 2015, inutile
    # pour une sonde). On demande une page pour voir la forme et la fraicheur.
    url = ENDPOINT_LISTE + "?descending=1&limit=100"
    try:
        r = s.get(url, timeout=TIMEOUT)
    except Exception as e:
        _verdict("A flux", False, "flux injoignable : {}".format(e))
        return []
    print("HTTP {} sur {}".format(r.status_code, url))
    if r.status_code != 200:
        print("Corps (500 premiers car.) :", _plat(r.text, 500))
        _verdict("A flux", False, "statut != 200")
        return []
    try:
        data = r.json()
    except Exception as e:
        _verdict("A flux", False, "reponse non-JSON : {}".format(e))
        return []

    items = data.get("data") or []
    print("Cles de l'enveloppe :", list(data.keys()))
    print("Nombre d'entrees dans cette page :", len(items))
    print("next_page present :", "next_page" in data)
    if "next_page" in data:
        print("  next_page :", json.dumps(data["next_page"])[:300])

    if items:
        print("\nForme d'une entree de liste (brut) :")
        print(json.dumps(items[0], ensure_ascii=False)[:400])
        # Fraicheur : dates min/max de la page pour estimer le debit.
        dates = sorted(x.get("dateModified", "") for x in items if x.get("dateModified"))
        if dates:
            print("\nFraicheur de la page : de {} a {}".format(dates[0][:19], dates[-1][:19]))
            print("=> {} avis sur cet intervalle : donne l'ordre de grandeur du "
                  "debit (le collecteur devra filtrer CPV + curseur, pas tout "
                  "rapatrier).".format(len(items)))

    ok = len(items) > 0 and ("data" in data)
    _verdict("A flux", ok, "{} entrees, enveloppe {}".format(
        len(items), "conforme" if ok else "inattendue"))
    # On renvoie les ids pour la sonde B.
    return [x.get("id") for x in items if x.get("id")]


# ===========================================================================
# B -- Detail d'un avis : champs exploitables + CPV + langue
# ===========================================================================

def sonde_b(s, ids):
    _titre("B -- Prozorro : detail /tenders/{id}, champs, CPV, langue")
    if not ids:
        _verdict("B detail", False, "aucun id fourni par la sonde A")
        return
    echantillon = ids[:NB_DETAILS]
    print("Echantillon : {} avis (sur {} vus).".format(len(echantillon), len(ids)))

    divisions_vues = {}
    langues = {"cyrillique": 0, "latin_ou_vide": 0}
    statuts = {}
    nb_avec_valeur = 0
    nb_avec_cpv = 0
    premier_dump_fait = False

    for i, tid in enumerate(echantillon, start=1):
        try:
            r = s.get(ENDPOINT_DETAIL.format(tid), timeout=TIMEOUT)
            d = (r.json() or {}).get("data") or {}
        except Exception as e:
            print("  [{}/{}] {} : illisible ({})".format(i, len(echantillon), tid[:12], e))
            continue

        titre = d.get("title") or ""
        statut = d.get("status") or "?"
        statuts[statut] = statuts.get(statut, 0) + 1
        if d.get("value"):
            nb_avec_valeur += 1
        if _est_cyrillique(titre):
            langues["cyrillique"] += 1
        else:
            langues["latin_ou_vide"] += 1

        # CPV : classification principale de chaque lot.
        cpv_avis = []
        for it in (d.get("items") or []):
            clas = it.get("classification") or {}
            code = clas.get("id") or ""
            if code:
                cpv_avis.append(code)
                div = _division_cpv(code)
                if div:
                    divisions_vues[div] = divisions_vues.get(div, 0) + 1
        if cpv_avis:
            nb_avec_cpv += 1

        # On dumpe UN detail complet (le premier qui a un CPV) : c'est lui qui
        # dira le vrai balisage, tout le reste n'est que comptage.
        if not premier_dump_fait and cpv_avis:
            premier_dump_fait = True
            print("\n--- DETAIL BRUT d'un avis (champs cles) ---")
            print("  id            :", tid)
            print("  titre         :", _plat(titre, 120))
            print("  statut        :", statut)
            print("  value         :", json.dumps(d.get("value") or {}, ensure_ascii=False))
            pe = d.get("procuringEntity") or {}
            print("  acheteur      :", _plat(pe.get("name"), 100))
            print("  acheteur.kind :", pe.get("kind"))
            adr = pe.get("address") or {}
            print("  region        :", _plat(adr.get("region"), 60),
                  "| pays :", _plat(adr.get("countryName"), 40))
            print("  method/type   :", d.get("procurementMethod"),
                  "/", d.get("procurementMethodType"))
            print("  dates         : modif", (d.get("dateModified") or "")[:19],
                  "| tenderPeriod.end",
                  (((d.get("tenderPeriod") or {}).get("endDate")) or "")[:19])
            print("  lots (items)  :", len(d.get("items") or []),
                  "| CPV du 1er lot :", cpv_avis[0])
            clas0 = ((d.get("items") or [{}])[0].get("classification") or {})
            print("  classification[0] :", json.dumps(clas0, ensure_ascii=False)[:200])
            print("  champs racine disponibles :", sorted(d.keys()))
            print("--- fin detail brut ---\n")

    # Synthese de l'echantillon.
    print("Statuts rencontres        :", statuts)
    print("Avis avec une valeur      : {}/{}".format(nb_avec_valeur, len(echantillon)))
    print("Avis avec au moins un CPV : {}/{}".format(nb_avec_cpv, len(echantillon)))
    print("Langue des titres         :", langues)
    divisions_interet = {k: v for k, v in divisions_vues.items() if k in DIVISIONS_CPV_INTERET}
    print("Divisions CPV vues        :", dict(sorted(divisions_vues.items())))
    print("  dont divisions D'INTERET Amarante :", dict(sorted(divisions_interet.items())))

    ok = nb_avec_cpv > 0
    _verdict("B detail", ok,
             "detail exploitable, CPV present sur {}/{} avis"
             .format(nb_avec_cpv, len(echantillon)) if ok
             else "CPV absent du detail : filtrage a repenser")
    _verdict("B langue", True,
             "{} titres cyrilliques sur {} : le pre-filtre FR/EN ne suffira PAS, "
             "filtrer sur le CPV et laisser le LLM lire l'ukrainien (ou traduire)"
             .format(langues["cyrillique"], len(echantillon)))
    _verdict("B filtrabilite CPV", bool(divisions_interet),
             "au moins une division d'interet presente dans l'echantillon"
             if divisions_interet else
             "aucune division d'interet dans ce petit echantillon (agrandir NB_DETAILS)")


# ===========================================================================
# C -- Mode OCDS : plus propre a parser ?
# ===========================================================================

def sonde_c(s, ids):
    _titre("C -- Prozorro : mode OCDS (opt_schema=ocds) sur un avis")
    if not ids:
        _verdict("C ocds", False, "aucun id disponible")
        return
    tid = ids[0]
    url = ENDPOINT_DETAIL.format(tid) + "?opt_schema=ocds"
    try:
        r = s.get(url, timeout=TIMEOUT)
        d = r.json() or {}
    except Exception as e:
        _verdict("C ocds", False, "OCDS illisible : {}".format(e))
        return
    print("HTTP {} sur {}".format(r.status_code, url))
    print("Cles racine OCDS :", sorted(d.keys())[:20])
    # En OCDS, la charge utile vit typiquement sous data.releases[] ou similaire.
    apercu = json.dumps(d, ensure_ascii=False)
    print("Apercu (500 premiers car.) :", apercu[:500])
    ok = r.status_code == 200 and bool(d)
    _verdict("C ocds", ok,
             "OCDS repond : comparer la simplicite de parsing vs schema natif "
             "avant de choisir pour le collecteur" if ok
             else "OCDS n'a pas repondu comme attendu, rester sur le schema natif")


# ===========================================================================
# Point d'entree
# ===========================================================================

def main():
    print("SONDE PROZORRO -- diagnostic, aucune ecriture, aucun LLM.")
    s = _session()
    ids = sonde_a(s)
    sonde_b(s, ids)
    sonde_c(s, ids)

    _titre("SYNTHESE")
    for nom, ok, detail in RESULTATS:
        print("  [{}] {:20} {}".format("OK " if ok else "?? ", nom, detail))
    print("\nRappel : sonde jetable. Une fois le collecteur ecrit, la supprimer.")
    # Toujours 0 : une sonde ne doit jamais faire echouer un run.
    sys.exit(0)


if __name__ == "__main__":
    main()
