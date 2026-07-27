# -*- coding: utf-8 -*-
"""RADAR AMARANTE -- SONDE MIGA/IFC (jetable) : valider le fix + balisage reel.
=========================================================================

Contexte : certaines fiches MIGA redirigent vers le portail IFC
(disclosures.ifc.org), au balisage different ("Company Name" au lieu de
"Guarantee Holder"). Un correctif a ete ajoute a miga_radar (parseur tolerant +
detection de fiche externe + fallback texte). Mais Setrag et Timor sont en
memoire (sautes au run normal), donc le fix n'a pas encore ete verifie sur le
VRAI HTML.

Cette sonde re-parse ces fiches EN IGNORANT LA MEMOIRE, pour :
  1. VALIDER le fix : parser_fiche recupere-t-il l'acteur, le pays (ISO3), la
     categorie, la description riche ?
  2. DUMPER LE BALISAGE BRUT reel d'une fiche IFC -> reconnaissance pour un
     futur collecteur IFC dedie (Vague 2, source n2).

Reutilise miga_radar (session, en-tetes, parser_fiche). Aucune ecriture, aucun
LLM. Sortie code 0. Jetable.
"""

import re
import sys

try:
    import miga_radar as mg
except Exception as e:                               # pragma: no cover
    print("import miga_radar impossible : {}".format(e))
    sys.exit(0)

SLUGS = [
    "/project/setrag-gabon-3",
    "/project/timor-leste-solar-and-bess-0",
]
LABELS = ["Guarantee Holder", "Company Name", "Host Country", "Country",
          "Environmental Category", "Sector", "Project Description"]


def _slice(html, needle, avant=60, apres=240):
    i = html.find(needle)
    if i == -1:
        return "  [absent] '{}'".format(needle)
    return "  '{}' -> {}".format(
        needle, re.sub(r"\s+", " ", html[i:i + apres]))


def main():
    print("SONDE MIGA/IFC -- validation du fix + balisage brut. Aucune ecriture.")
    s = mg._session()

    for slug in SLUGS:
        print("\n" + "=" * 72)
        print("FICHE :", slug)
        print("=" * 72)
        try:
            r = s.get(mg.BASE + slug, timeout=45)
        except Exception as e:
            print("  injoignable : {}".format(e))
            continue
        print("  HTTP {} | URL finale : {}".format(r.status_code, r.url))
        externe = mg._est_fiche_externe(r.text)
        print("  Detectee comme fiche EXTERNE (IFC) :", externe)

        print("\n  --- BALISAGE BRUT autour des libelles (verite terrain) ---")
        for lab in LABELS:
            print(_slice(r.text, lab))

        print("\n  --- RESULTAT DU PARSEUR (le fix marche-t-il ?) ---")
        a = mg.parser_fiche(slug, "", r.text)
        print("  acteur (acheteur) :", a.get("acheteur"))
        print("  pays_execution    :", a.get("pays_execution"), "| ISO3 :", a.get("pays_iso3"))
        print("  categorie_es      :", a.get("categorie_es"))
        print("  type_document     :", a.get("type_document"))
        print("  valeur_estimee    :", a.get("valeur_estimee"))
        desc = a.get("description", "")
        print("  description ({} car.) : {}".format(len(desc), desc[:400]))

        ok = bool(a.get("pays_iso3")) and (
            a.get("acheteur", "").lower() not in ("", "investisseur miga (non precise)"))
        print("\n  => {} : acteur {}, pays {}".format(
            "OK" if ok else "a creuser",
            "recupere" if a.get("acheteur") and "non precise" not in a.get("acheteur", "") else "manquant",
            a.get("pays_iso3") or "manquant"))

    print("\n" + "=" * 72)
    print("LECTURE : si l'acteur et la description riche sortent, le fix marche")
    print("et Setrag/Timor remonteront en FORT au prochain re-parsing. Le balisage")
    print("brut ci-dessus sert de reconnaissance pour un collecteur IFC dedie.")
    print("Sonde jetable : a supprimer ensuite.")
    sys.exit(0)


if __name__ == "__main__":
    main()
