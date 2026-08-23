# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- ADAPTATEUR DFI VERS PROJECT DISCOVERY (P1).
===============================================================================

L'EXIGENCE
----------
"Le resultat de ces collecteurs doit alimenter exactement le meme pipeline que
Google News : source -> signal -> LLM -> candidate -> clustering -> promotion
-> PROJECT. Ne cree pas un pipeline parallele."

COMMENT, SANS RIEN DUPLIQUER
-----------------------------
Les collecteurs BM, IFC, MIGA, AfDB, EBRD, ADB, IsDB, IDB, Proparco/AFD, DFC
ecrivent DEJA leurs avis a chaque run, et `radar_dashboard.charger_leads` les
relit tous dans un format unifie (le "lead", avec `src`, `titre`, `pays`,
`lien`, `date_det`). Cet adaptateur ne recollecte RIEN : il convertit ces leads
en SIGNAUX au format attendu par `decouverte_projets`, et les injecte dans le
pipeline existant, exactement au meme endroit que les articles de presse.

    collecteurs DFI (deja en place)
            v
    leads unifies (radar_dashboard)
            v
    [ CET ADAPTATEUR ]  --> signaux { source: "BM"|"AFDB"|... }
            v
    decouverte_projets.extraire_par_lots -> regrouper -> promouvoir
            v
    projets.construire_projets  (socle inchange)

POURQUOI CA CHANGE TOUT POUR LA QUALITE
----------------------------------------
`sources_reference` attribue a ces signaux la fiabilite "dfi" (0.95) sur la
foi de leur COLLECTEUR d'origine, sans dependre d'une URL. Combine a la voie
officielle de promotion (P5), cela signifie qu'un seul avis de la Banque
Mondiale suffit desormais a creer un candidat de projet solide, la ou il
fallait auparavant trois articles de presse.

FILTRAGE AMONT
--------------
Tous les avis ne sont pas des signaux de PROJET : un appel d'offres de
fournitures de bureau n'annonce aucun grand projet. On applique donc un
pre-filtre deterministe (montant, vocabulaire projet, type de notice) AVANT
tout appel LLM, pour ne pas gaspiller le budget.
"""

import os
import re
import unicodedata

import sources_reference as sref


# Collecteurs consideres comme porteurs de signaux de PROJET. Les sources
# purement "achat courant" (TED, UNGM) sont exclues par defaut : leur volume
# est enorme et leur rapport signal/bruit faible pour la decouverte de projets.
SOURCES_DFI = ("BM", "BMP", "IFC", "MIGA", "AFDB", "EBRD", "ADB", "ISDB",
               "IDB", "PROPARCO", "DFC")

ACTIVER_TED = os.environ.get("RADAR_DECOUVERTE_TED", "0") == "1"

# Vocabulaire qui trahit un GRAND PROJET dans un intitule d'avis DFI. Un avis
# de supervision, d'audit ou de fournitures n'annonce pas un projet nouveau.
MOTS_PROJET = (
    "project", "projet", "programme", "program", "development", "developpement",
    "construction", "rehabilitation", "expansion", "corridor", "master plan",
    "feasibility", "faisabilite", "design", "engineering", "epc",
    "power plant", "centrale", "hydro", "hydropower", "barrage", "dam",
    "transmission", "pipeline", "gazoduc", "oleoduc", "lng", "gnl",
    "refinery", "raffinerie", "mine", "mining", "miniere", "smelter",
    "port", "railway", "rail", "chemin de fer", "airport", "aeroport",
    "road", "route", "highway", "industrial", "industriel", "plant", "usine",
    "solar", "solaire", "wind", "eolien", "terminal", "special economic zone",
)

# Intitules qui, au contraire, designent de l'achat courant ou du service
# support : ils ne doivent pas consommer de budget LLM.
MOTS_EXCLUS = (
    "office supplies", "fournitures de bureau", "stationery", "cleaning",
    "nettoyage", "catering", "restauration", "insurance", "assurance",
    "audit", "training", "formation", "workshop", "atelier", "seminar",
    "vehicle rental", "location de vehicules", "translation", "traduction",
    "recruitment", "recrutement", "consultancy services for the preparation of "
    "the annual report", "printing", "impression", "furniture", "mobilier",
)


def _norm(txt):
    t = unicodedata.normalize("NFD", str(txt or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def est_signal_de_projet(lead):
    """Cet avis DFI annonce-t-il plausiblement un GRAND PROJET ?
    Pre-filtre DETERMINISTE, applique AVANT tout appel LLM. Fonction PURE."""
    texte = _norm("{} {} {}".format(lead.get("titre", ""), lead.get("agence", ""),
                                    lead.get("justif", "")))
    if not texte.strip():
        return False
    if any(_norm(m) in texte for m in MOTS_EXCLUS):
        return False
    if any(_norm(m) in texte for m in MOTS_PROJET):
        return True
    # Repli : un montant significatif est en soi un indice de projet, meme si
    # l'intitule est laconique.
    return _montant_significatif(lead)


_MONTANT = re.compile(r"(\d[\d\s.,]*)\s*(m|md|bn|million|milliard|billion)?", re.I)


def _montant_significatif(lead, seuil_meur=20.0):
    """Le lead porte-t-il un montant (marche ou enveloppe) >= seuil ?
    Best-effort : en cas de doute, False. Fonction PURE."""
    for champ in ("valeur", "enveloppe"):
        brut = str(lead.get(champ) or "")
        if not brut.strip():
            continue
        try:
            import radar_dashboard as dash
            if dash._valeur_en_millions(brut) >= seuil_meur:
                return True
        except Exception:
            continue
    return False


def signal_depuis_lead(lead):
    """Lead unifie -> SIGNAL au format `decouverte_projets`. Fonction PURE.

    Le champ `source` porte le code du collecteur : c'est lui qui donne au
    signal sa fiabilite DFI dans `sources_reference`, sans dependre de l'URL
    (un avis peut n'avoir aucun lien exploitable)."""
    src = str(lead.get("src") or "").upper()
    lien = str(lead.get("lien") or "")
    # Identifiant stable meme sans lien : sinon deux avis distincts sans URL
    # seraient confondus a la deduplication.
    if lien:
        import bitd_signaux as bitd
        ident = bitd.id_article(lien)
    else:
        ident = "{}:{}".format(src, lead.get("pub") or _norm(lead.get("titre", ""))[:60])
    resume = " ".join(x for x in (lead.get("agence", ""), lead.get("justif", ""),
                                  lead.get("valeur", ""), lead.get("enveloppe", ""))
                      if x and x != "n.c.")
    return {
        "id": ident,
        "titre": lead.get("titre", ""),
        "resume": resume[:400],
        "date": str(lead.get("date_det") or lead.get("mois") or "")[:10],
        "lien": lien,
        "source": src,                      # -> fiabilite dfi
        "iso3_requete": "",                 # le LLM tranchera le pays
        "projet_id_amont": lead.get("projet_id", ""),
    }


def signaux_depuis_leads(leads, vus=None, sources=None, activer_ted=None):
    """Leads unifies -> signaux de decouverte, filtres et dedupliques.
    Fonction PURE (aucun acces reseau : les leads sont deja en memoire)."""
    activer_ted = ACTIVER_TED if activer_ted is None else activer_ted
    autorisees = set(sources or SOURCES_DFI)
    if activer_ted:
        autorisees |= {"TED", "UNGM"}
    vus, locaux, out = set(vus or ()), set(), []
    for lead in leads or []:
        src = str(lead.get("src") or "").upper()
        if src not in autorisees:
            continue
        if not est_signal_de_projet(lead):
            continue
        signal = signal_depuis_lead(lead)
        if signal["id"] in vus or signal["id"] in locaux:
            continue
        locaux.add(signal["id"])
        out.append(signal)
    return out


def repartition(signaux):
    """Comptage par source et par fiabilite (journalisation). Fonction PURE."""
    import collections
    par_src = collections.Counter(s.get("source", "?") for s in signaux)
    poids = sum(sref.fiabilite_du_signal(s) for s in signaux)
    return {"total": len(signaux), "par_source": dict(par_src),
            "poids_cumule": round(poids, 2)}
