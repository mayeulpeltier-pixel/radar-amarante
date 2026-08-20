# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SONDE RATTACHEMENT (jetable) : peut-on relier les phases d'un
meme projet (amont -> avis -> attribution) pour construire un "dossier vivant" ?
===============================================================================

POURQUOI
--------
Le Lot 2 (dossier vivant) suppose de regrouper, sous une meme entite, les signaux
d'un meme projet a travers le temps. Or il n'existe AUCUN identifiant commun
entre un projet amont (bm_projets), un avis d'appel d'offres et une attribution :
le rattachement se fait par SIMILARITE (meme pays + titres proches + montant
proche). Avant de batir l'infrastructure, on mesure ici, sur les vraies donnees,
le taux de rattachement plausible et sa qualite (vrais liens vs faux).

CE QUE LA SONDE FAIT (aucune ecriture)
--------------------------------------
  1. Repartition des leads par source (pour voir ce qui est disponible : amont,
     avis, attributions).
  2. Lien AMONT -> AVIS (le maillon NEUF a valider) : part des projets amont qui
     trouvent au moins un avis plausible (meme pays, Jaccard titre >= seuil).
  3. Lien AVIS -> ATTRIBUTION (deja en prod via surveillance_attributions, on
     confirme) : part des attributions rattachables a un avis anterieur.
  Exemples affichés pour juger la PERTINENCE a l'oeil.

Reutilise le moteur (charger_leads) : une seule lecture du Sheet. Lancer en
Actions (acces au Sheet/PG). Sortie toujours en code 0.

USAGE
-----
    SONDE_RATT_SEUIL=0.30 python sonde_rattachement.py
"""

import os
import re
import sys
from collections import defaultdict

import radar_dashboard as dash


SEUIL = float(os.environ.get("SONDE_RATT_SEUIL", "0.30"))
SOURCES_AMONT = {"AMONT", "BM-PROJETS", "BM_PROJETS", "PROJETS", "BMP"}
MOTS_VIDES = {
    "pour", "avec", "dans", "des", "les", "une", "aux", "sur", "par", "the",
    "and", "for", "with", "project", "projet", "works", "services", "supply",
    "travaux", "fourniture", "marche", "contract", "contrat", "appel", "offres",
    "region", "national", "programme", "program", "phase", "lot",
}


def tokens(titre):
    return {m for m in re.findall(r"[a-zA-ZÀ-ÿ]{4,}", (titre or "").lower())
            if m not in MOTS_VIDES}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def classer(leads):
    amont, avis, attrib = [], [], []
    for l in leads:
        src = (l.get("src") or "").upper()
        if src == "ATTRIB":
            attrib.append(l)
        elif src in SOURCES_AMONT:
            amont.append(l)
        else:
            avis.append(l)
    return amont, avis, attrib


def _pays(l):
    return (l.get("pays") or "").strip().lower()


def meilleur_match(source, cibles, index_pays):
    """Meilleure cible (meme pays, Jaccard titre max) pour un lead source."""
    tk = tokens(source.get("titre"))
    if not tk:
        return None, 0.0
    best, score = None, 0.0
    for c in index_pays.get(_pays(source), ()):
        s = jaccard(tk, tokens(c.get("titre")))
        if s > score:
            best, score = c, s
    return best, score


def indexer_par_pays(leads):
    idx = defaultdict(list)
    for l in leads:
        idx[_pays(l)].append(l)
    return idx


def mesurer(sources, cibles, libelle):
    """Part des sources qui trouvent une cible au-dessus du seuil. Affiche
    quelques liens pour juger la qualite."""
    if not sources or not cibles:
        print("  ({}): donnees insuffisantes ({} sources, {} cibles).".format(
            libelle, len(sources), len(cibles)))
        return
    idx = indexer_par_pays(cibles)
    relies, exemples = 0, []
    for s in sources:
        best, score = meilleur_match(s, cibles, idx)
        if score >= SEUIL:
            relies += 1
            if len(exemples) < 5:
                exemples.append((score, s, best))
    pct = 100.0 * relies / len(sources)
    print("  {} : {}/{} relies (>= {:.2f}) = {:.0f}%".format(
        libelle, relies, len(sources), SEUIL, pct))
    for score, s, b in exemples:
        print("     [{:.2f}] {}".format(score, (s.get("titre") or "")[:60]))
        print("            -> {}".format((b.get("titre") or "")[:60]))


def main():
    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not sheet_id or not fichier:
        print("ERREUR : TED_SHEET_ID et GOOGLE_SERVICE_ACCOUNT_FILE requis.")
        sys.exit(0)
    print("SONDE RATTACHEMENT -- seuil Jaccard :", SEUIL)
    print("Lecture du moteur (charger_leads)...")
    leads, _ = dash.charger_leads(sheet_id, fichier)
    amont, avis, attrib = classer(leads)

    print("\n" + "=" * 70)
    print("1. REPARTITION PAR SOURCE ({} leads)".format(len(leads)))
    print("=" * 70)
    rep = defaultdict(int)
    for l in leads:
        rep[(l.get("src") or "?")] += 1
    for s, n in sorted(rep.items(), key=lambda x: -x[1]):
        print("  {:<12} {}".format(s, n))
    print("\n  Classes : {} amont | {} avis | {} attributions".format(
        len(amont), len(avis), len(attrib)))
    if not amont:
        print("  (!) Aucun lead 'amont' detecte : bm_projets n'est peut-etre pas")
        print("      dans les leads du dashboard. Le lien amont->avis ne pourra")
        print("      etre teste qu'une fois l'amont injecte dans les leads.")

    print("\n" + "=" * 70)
    print("2. LIEN AMONT -> AVIS (maillon NEUF a valider)")
    print("=" * 70)
    mesurer(amont, avis, "amont -> avis")

    print("\n" + "=" * 70)
    print("3. LIEN AVIS -> ATTRIBUTION (confirmation)")
    print("=" * 70)
    mesurer(attrib, avis, "attribution -> avis")

    print("\n" + "=" * 70)
    print("LECTURE")
    print("=" * 70)
    print("  Taux eleve + exemples pertinents (memes marches) -> le dossier")
    print("  vivant est faisable : on construit le resolveur d'entites.")
    print("  Taux faible ou exemples incoherents -> affiner (secteur+montant en")
    print("  plus du titre) ou renoncer au rattachement automatique complet.")
    sys.exit(0)


if __name__ == "__main__":
    main()
