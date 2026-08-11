"""Etat inter-runs du radar : curseur de rotation + articles deja vus.

Pourquoi ce module existe
-------------------------
Avant, cet etat vivait dans le Google Sheet (onglets `radar_etat` et
`prive_vus`). C'etait un point de defaillance unique (SPOF) : si le Sheet etait
indisponible ou corrompu, le radar perdait sa memoire et retraitait tout (des
doublons et une facture LLM inutile).

L'etat vit desormais dans un fichier JSON (`radar_etat.json`) versionne dans le
depot. Le workflow GitHub Actions le commite en fin de run, ce qui le rend
persistant entre les executions, independant du Sheet, et auditable dans
l'historique git. La migration initiale (premier run) est geree par l'appelant :
si le fichier n'existe pas encore, il relit une derniere fois l'ancien etat du
Sheet (voir signaux_prives.main).
"""

import json
import os
import datetime

# Fichier d'etat (surchargable pour les tests via RADAR_ETAT_FICHIER).
CHEMIN_ETAT = os.environ.get("RADAR_ETAT_FICHIER", "radar_etat.json")
# Fenetre glissante des articles memorises (aligne sur l'ancien plafond BITD).
MAX_VUS_MEMOIRE = 6000


def charger(chemin=None):
    """Lit l'etat depuis le fichier JSON.

    Renvoie (curseur:int, vus:list) si le fichier existe. Renvoie (None, None)
    si le fichier est absent ou illisible : c'est le signal d'un premier run,
    l'appelant fait alors une migration douce depuis l'ancien etat du Sheet.
    Les vus sont une liste ORDONNEE (du plus ancien au plus recent)."""
    chemin = chemin or CHEMIN_ETAT
    if not os.path.exists(chemin):
        return None, None
    try:
        with open(chemin, encoding="utf-8") as f:
            data = json.load(f)
        curseur = int(data.get("curseur", 0))
        vus = [str(v) for v in data.get("vus", [])]
        return curseur, vus
    except (ValueError, OSError, TypeError) as e:
        print("radar_etat : fichier illisible ({}), repli sur migration Sheet.".format(e))
        return None, None


def charger_prio(chemin=None):
    """Curseur de la TETE prioritaire (rotation a deux vitesses, item cadence).

    Second curseur, independant de `curseur` (qui balaie la queue). Renvoie 0 si
    absent : un etat ecrit avant ce chantier n'a pas la cle, on repart de 0 sans
    casser (la tete est petite, se recale en quelques runs). Volontairement
    separe de `charger()` pour ne pas changer la signature de retour existante."""
    chemin = chemin or CHEMIN_ETAT
    if not os.path.exists(chemin):
        return 0
    try:
        with open(chemin, encoding="utf-8") as f:
            return int(json.load(f).get("curseur_prio", 0))
    except (ValueError, OSError, TypeError):
        return 0


def sauver(curseur, vus_anciens, vus_nouveaux=None, chemin=None, curseur_prio=0):
    """Ecrit l'etat dans le fichier JSON (ecriture atomique).

    vus_anciens : liste ORDONNEE (plus ancien -> plus recent) deja connue.
    vus_nouveaux : iterable des vus de ce run, ajoutes a la fin.
    curseur_prio : curseur de la tete prioritaire (0 par defaut ; les appelants
      qui n'utilisent pas la rotation a deux vitesses n'ont rien a changer).

    Deduplique en preservant l'ordre, puis plafonne aux MAX_VUS_MEMOIRE plus
    recents (les plus anciens sont oublies en premier ; la fraicheur reelle est
    de toute facon deja geree a la collecte). Renvoie le nombre de vus conserves.
    """
    chemin = chemin or CHEMIN_ETAT
    ordonnes = [str(v) for v in (vus_anciens or [])]
    connus = set(ordonnes)
    for v in (vus_nouveaux or []):
        v = str(v)
        if v not in connus:
            ordonnes.append(v)
            connus.add(v)
    if len(ordonnes) > MAX_VUS_MEMOIRE:
        ordonnes = ordonnes[-MAX_VUS_MEMOIRE:]
    data = {
        "curseur": int(curseur),
        "curseur_prio": int(curseur_prio),
        "vus": ordonnes,
        "maj": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }
    tmp = chemin + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, chemin)          # remplacement atomique (pas de fichier a moitie ecrit)
    return len(ordonnes)
