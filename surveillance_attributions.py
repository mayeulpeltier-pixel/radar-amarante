# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- SURVEILLANCE : l'attribution a-t-elle paru ? (12/08/2026)
==========================================================================

Un projet AMONT (BM Projects, ou tout avis) qui interesse l'utilisateur mais qui
n'a pas encore d'attribution peut etre mis SOUS SURVEILLANCE (bouton « Surveiller »
-> statut 'surveille' dans radar_statuts). A chaque run, ce module verifie si une
ATTRIBUTION correspondante a ete publiee (onglet attributions_radar). Si oui, il
bascule le statut a 'attribution_publiee' et enregistre le GAGNANT dans le motif
-> l'utilisateur voit « attribution publiee : <gagnant> » dans la section
Surveillance. C'est le pont entre l'amont (projet) et l'aval (titulaire a demarcher).

MATCHING : HEURISTIQUE, CONFIRME PAR L'HUMAIN
--------------------------------------------
On matche par SIMILARITE de titre + agence (recouvrement de tokens). Le pays est
un signal peu fiable ici (BM Projects stocke l'ISO3, les attributions un nom), on
ne l'exige donc pas. Faux positifs possibles -> l'humain confirme dans l'UI. Seuil
regable (RADAR_SURVEILLANCE_SEUIL). Fonctions de matching PURES (testables) ;
l'acces base est best-effort et ne leve jamais.
"""

import os
import re
import unicodedata


ACTIVER = os.environ.get("RADAR_SURVEILLANCE", "1") != "0"
SEUIL = float(os.environ.get("RADAR_SURVEILLANCE_SEUIL", "0.30"))
STATUT_SURVEILLE = "surveille"
STATUT_TROUVE = "attribution_publiee"
ONGLET_ATTRIB = "attributions_radar"

# Mots vides ecartes du calcul de similarite (bruit generique des intitules).
_STOP = {"de", "des", "du", "la", "le", "les", "et", "en", "pour", "the", "of",
         "and", "for", "to", "a", "au", "aux", "un", "une", "project", "projet",
         "programme", "program", "phase", "works", "travaux", "supply", "of"}


def _norm(s):
    return unicodedata.normalize("NFD", str(s or "").lower()).encode(
        "ascii", "ignore").decode("ascii")


def _tokens(texte):
    """Ensemble de tokens significatifs (>= 3 lettres, hors mots vides)."""
    mots = re.findall(r"[a-z0-9]+", _norm(texte))
    return {m for m in mots if len(m) >= 3 and m not in _STOP}


def similarite(a, b):
    """Jaccard des tokens de a et b (0..1). 0 si l'un est vide."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(len(ta | tb))


def _texte_item(row):
    return "{} {}".format(row.get("titre", ""), row.get("acheteur", "")
                          or row.get("agence", ""))


def _texte_attrib(att):
    return "{} {}".format(att.get("titre", ""), att.get("acheteur", ""))


def chercher_attribution(item, attributions, seuil=None):
    """item (avis surveille) x liste d'attributions -> (meilleure_attrib, score)
    si score >= seuil, sinon (None, 0.0). PUR."""
    seuil = SEUIL if seuil is None else seuil
    cible = _texte_item(item)
    best, meilleur = None, 0.0
    for att in (attributions or []):
        s = similarite(cible, _texte_attrib(att))
        if s > meilleur:
            meilleur, best = s, att
    if best is not None and meilleur >= seuil:
        return best, round(meilleur, 2)
    return None, 0.0


def gagnant_de(attribution):
    return str(attribution.get("gagnant") or attribution.get("entreprise") or "").strip()


def evaluer_surveillances(surveilles, index_lignes, attributions, seuil=None):
    """Coeur PUR de la verification. Entrees :
      - surveilles : [(onglet, pub)] sous surveillance ;
      - index_lignes : {(onglet, pub): row} pour retrouver l'avis surveille ;
      - attributions : [rows] de attributions_radar.
    Sortie : [(onglet, pub, gagnant, score)] pour les matches trouves."""
    trouves = []
    for onglet, pub in surveilles:
        item = index_lignes.get((onglet, pub))
        if not item:
            continue
        att, score = chercher_attribution(item, attributions, seuil)
        if att is not None:
            g = gagnant_de(att)
            if g:
                trouves.append((onglet, pub, g, score))
    return trouves


# ===========================================================================
# POINT D'ENTREE (colle Postgres, best-effort)
# ===========================================================================
def main():
    if not ACTIVER:
        print("(info) Surveillance desactivee (RADAR_SURVEILLANCE=0).")
        return
    try:
        import radar_stockage as st
    except Exception as e:
        print("(info) surveillance : stockage indisponible ({}).".format(e))
        return
    if not st.actif():
        print("(info) surveillance : DATABASE_URL absent, run saute.")
        return
    with st.connexion() as conn:
        statuts = st.lire_statuts(conn)
        surveilles = [cle for cle, s in statuts.items() if s == STATUT_SURVEILLE]
        if not surveilles:
            print("Surveillance : aucun projet sous surveillance.")
            return
        # Index des lignes surveillees (par onglet, pour ne lire que l'utile).
        onglets = {o for o, _ in surveilles}
        index = {}
        for onglet in onglets:
            for row in st.lire_onglet(conn, onglet):
                pub = str(row.get("publication_number", "") or "")
                if pub:
                    index[(onglet, pub)] = row
        attributions = st.lire_onglet(conn, ONGLET_ATTRIB)
        trouves = evaluer_surveillances(surveilles, index, attributions)
        for onglet, pub, gagnant, score in trouves:
            st.definir_statut(conn, onglet, pub, STATUT_TROUVE, motif=gagnant)
            print("  [attribution publiee] {} / {} -> {} (score {})".format(
                onglet, pub, gagnant, score))
        print("Surveillance : {} sous surveillance, {} attribution(s) detectee(s)."
              .format(len(surveilles), len(trouves)))


if __name__ == "__main__":
    main()
