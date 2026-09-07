# -*- coding: utf-8 -*-
"""Lecture des reglages d'environnement (26/08/2026).

L'INCIDENT
----------
    File "collecteur_projets.py", line 57, in <module>
        PROJETS_PAR_RUN = int(os.environ.get("RADAR_PROJETS_PAR_RUN", "6"))
    ValueError: invalid literal for int() with base 10: ''

Cause : `os.environ.get(nom, defaut)` ne rend le defaut que si la cle est
ABSENTE. Or GitHub Actions definit toujours la variable et y met une chaine
VIDE quand la valeur n'est pas fournie :

    RADAR_PROJETS_PAR_RUN: ${{ github.event.inputs.projets_par_run }}

Champ laisse vide au lancement -> variable presente, valeur "" -> `int("")`
leve, et le collecteur meurt A L'IMPORT, avant d'avoir rien fait. Une valeur
non renseignee ne devrait jamais etre plus dangereuse qu'une valeur absente.

POURQUOI UN MODULE A PART
-------------------------
Le depot comptait 141 occurrences de `int(os.environ.get(...))`, toutes
exposees au meme piege. `radar_retroaction` avait deja son propre helper `_f`,
utilise par lui seul. Recopier la parade dans chaque fichier aurait produit
autant de variantes a maintenir.

Ces fonctions ne dependent QUE de la bibliotheque standard : elles sont
importables par n'importe quel module du depot, y compris ceux qui tournent
sans aucune dependance installee.
"""

import os

__all__ = ["entier", "reel", "texte", "booleen"]


def _brut(nom, defaut=None):
    """Valeur nettoyee, ou None si elle est absente OU vide.

    C'est ici que se joue la correction : une chaine vide, ou faite
    d'espaces, est traitee comme une ABSENCE et non comme une valeur."""
    valeur = os.environ.get(nom)
    if valeur is None:
        return None
    valeur = valeur.strip()
    return valeur or None


def entier(nom, defaut):
    """Reglage entier. Ne leve JAMAIS.

    Une valeur illisible ne doit pas tuer un collecteur au chargement : elle
    est signalee et le defaut s'applique. Un run degrade vaut mieux qu'un run
    mort avant d'avoir commence."""
    valeur = _brut(nom)
    if valeur is None:
        return int(defaut)
    try:
        return int(float(valeur))     # accepte "6" comme "6.0"
    except (TypeError, ValueError):
        print("(config) {} = {!r} illisible, valeur par defaut {} utilisee."
              .format(nom, valeur, defaut))
        return int(defaut)


def reel(nom, defaut):
    """Reglage decimal. Ne leve JAMAIS. Accepte la virgule decimale."""
    valeur = _brut(nom)
    if valeur is None:
        return float(defaut)
    try:
        return float(valeur.replace(",", "."))
    except (TypeError, ValueError):
        print("(config) {} = {!r} illisible, valeur par defaut {} utilisee."
              .format(nom, valeur, defaut))
        return float(defaut)


def texte(nom, defaut=""):
    """Reglage texte. Une chaine vide vaut le defaut, pas la chaine vide."""
    valeur = _brut(nom)
    return defaut if valeur is None else valeur


def booleen(nom, defaut=False):
    """Reglage oui/non, tolerant sur la forme.

    Accepte 1/0, true/false, oui/non, on/off, actif/inactif. Une valeur
    inconnue applique le defaut plutot que de la traiter comme fausse : « peut
    etre », ce n'est pas « non »."""
    valeur = _brut(nom)
    if valeur is None:
        return bool(defaut)
    v = valeur.lower()
    if v in ("1", "true", "vrai", "oui", "yes", "on", "actif"):
        return True
    if v in ("0", "false", "faux", "non", "no", "off", "inactif"):
        return False
    print("(config) {} = {!r} non reconnu, valeur par defaut {} utilisee."
          .format(nom, valeur, defaut))
    return bool(defaut)
