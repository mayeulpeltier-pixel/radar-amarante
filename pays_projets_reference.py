# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- REFERENTIEL GEOGRAPHIQUE DE LA DECOUVERTE (P3, P7, P8).
===============================================================================

CE QUE CE MODULE APPORTE
------------------------
Il remplace la liste plate `PAYS_DECOUVERTE` par trois notions qui manquaient :

  1. NIVEAU (P7)    : tous les pays ne meritent pas la meme attention.
       suivi        -> coeur de metier Amarante, presence ou clients actuels
       strategique  -> grands projets probables, forte valeur commerciale
       global_watch -> veille large, on ne veut pas rater une naissance
  2. LANGUES (P3)   : la presse locale d'un pays lusophone n'est PAS en anglais.
       Defaut de l'ancien systeme : Mozambique et Angola etaient interroges en
       anglais, ce qui rendait invisible l'essentiel de leur presse.
  3. CADENCE (P8)   : a quelle frequence on interroge un pays.
       suivi = tous les jours, strategique = 2-3 fois par semaine,
       global_watch = une fois par semaine.

AJOUTER UN PAYS = AJOUTER UNE LIGNE. Aucun code moteur a modifier : c'est le
sens de la demande "l'architecture doit permettre d'en ajouter facilement".

CE MODULE NE DECIDE PAS DU PERIMETRE COMMERCIAL
------------------------------------------------
`ted_complet_v14.dans_le_perimetre` continue de gouverner la COLLECTE
d'appels d'offres. Ici on parle de DECOUVERTE de projets : un projet peut
naitre dans un pays hors perimetre (cas Tanzanie), et il faut le voir venir.
Les deux notions sont volontairement independantes.
"""

import collections


# Cadence en jours entre deux interrogations d'un pays du niveau.
CADENCE = {"suivi": 1, "strategique": 3, "global_watch": 7}

LIBELLE_NIVEAU = {
    "suivi": "Pays suivi (coeur de metier)",
    "strategique": "Pays strategique (grands projets)",
    "global_watch": "Veille globale",
}

# Langues supportees par le generateur de requetes (voir DECLENCHEURS_LANGUE
# dans le moteur de decouverte). Une langue non supportee retombe sur "en".
LANGUES_SUPPORTEES = ("fr", "en", "pt", "ar", "es", "sw", "ru", "uk")

# Parametres Google News par langue : (hl, gl par defaut, ceid par defaut).
# Le `gl`/`ceid` est surcharge par pays quand une edition locale existe.
PARAMS_LANGUE = {
    "fr": ("fr", "FR", "FR:fr"),
    "en": ("en", "US", "US:en"),
    "pt": ("pt-BR", "BR", "BR:pt-419"),
    "ar": ("ar", "EG", "EG:ar"),
    "es": ("es", "ES", "ES:es"),
    "sw": ("sw", "TZ", "TZ:sw"),
    "ru": ("ru", "RU", "RU:ru"),
    "uk": ("uk", "UA", "UA:uk"),
}


def _p(iso3, nom_en, niveau, langues, noms_locaux=None, edition=None):
    """Fabrique une entree pays.
    noms_locaux : {langue: nom du pays dans cette langue} pour la requete.
    edition     : (gl, ceid) pour forcer l'edition Google News locale."""
    return {"iso3": iso3, "nom": nom_en, "niveau": niveau,
            "langues": list(langues), "noms_locaux": noms_locaux or {},
            "edition": edition}


# ===========================================================================
# REGISTRE PAYS
# ===========================================================================
PAYS = [
    # ------------------------------------------------------------ SUIVI
    # Coeur de metier Amarante : Sahel, MENA, Ukraine, Asie centrale.
    _p("MLI", "Mali", "suivi", ["fr", "en"], {"fr": "Mali"}, ("ML", "ML:fr")),
    _p("NER", "Niger", "suivi", ["fr", "en"], {"fr": "Niger"}, ("NE", "NE:fr")),
    _p("BFA", "Burkina Faso", "suivi", ["fr", "en"], {"fr": "Burkina Faso"}),
    _p("TCD", "Chad", "suivi", ["fr", "en"], {"fr": "Tchad"}),
    _p("MRT", "Mauritania", "suivi", ["fr", "ar", "en"],
       {"fr": "Mauritanie", "ar": "موريتانيا"}),
    _p("CIV", "Ivory Coast", "suivi", ["fr", "en"], {"fr": "Côte d'Ivoire"},
       ("CI", "CI:fr")),
    _p("SEN", "Senegal", "suivi", ["fr", "en"], {"fr": "Sénégal"}, ("SN", "SN:fr")),
    _p("COD", "Democratic Republic of Congo", "suivi", ["fr", "en"],
       {"fr": "RD Congo"}),
    _p("UKR", "Ukraine", "suivi", ["uk", "en", "ru"],
       {"uk": "Україна", "ru": "Украина"}, ("UA", "UA:uk")),
    _p("IRQ", "Iraq", "suivi", ["ar", "en"], {"ar": "العراق"}, ("IQ", "IQ:ar")),
    _p("LBY", "Libya", "suivi", ["ar", "en"], {"ar": "ليبيا"}),
    _p("NGA", "Nigeria", "suivi", ["en"], None, ("NG", "NG:en")),

    # ------------------------------------------------------ STRATEGIQUE
    # Grands projets probables, forte valeur commerciale.
    _p("TZA", "Tanzania", "strategique", ["en", "sw"],
       {"sw": "Tanzania"}, ("TZ", "TZ:en")),
    _p("MOZ", "Mozambique", "strategique", ["pt", "en"],
       {"pt": "Moçambique"}, ("PT", "PT:pt-150")),      # LUSOPHONE (corrige)
    _p("AGO", "Angola", "strategique", ["pt", "en"],
       {"pt": "Angola"}, ("PT", "PT:pt-150")),          # LUSOPHONE (corrige)
    _p("GIN", "Guinea", "strategique", ["fr", "en"], {"fr": "Guinée"}),
    _p("UGA", "Uganda", "strategique", ["en", "sw"], None, ("UG", "UG:en")),
    _p("KAZ", "Kazakhstan", "strategique", ["ru", "en"],
       {"ru": "Казахстан"}, ("KZ", "KZ:ru")),
    _p("UZB", "Uzbekistan", "strategique", ["ru", "en"], {"ru": "Узбекистан"}),
    _p("ZMB", "Zambia", "strategique", ["en"]),
    _p("TJK", "Tajikistan", "strategique", ["ru", "en"], {"ru": "Таджикистан"}),
    _p("TKM", "Turkmenistan", "strategique", ["ru", "en"], {"ru": "Туркменистан"}),
    _p("SAU", "Saudi Arabia", "strategique", ["ar", "en"],
       {"ar": "السعودية"}, ("SA", "SA:ar")),
    _p("ARE", "United Arab Emirates", "strategique", ["ar", "en"],
       {"ar": "الإمارات"}, ("AE", "AE:ar")),

    # ----------------------------------------------------- GLOBAL WATCH
    # Veille large : on ne veut pas rater la naissance d'un projet.
    _p("GHA", "Ghana", "global_watch", ["en"]),
    _p("CMR", "Cameroon", "global_watch", ["fr", "en"], {"fr": "Cameroun"}),
    _p("ETH", "Ethiopia", "global_watch", ["en"]),
    _p("EGY", "Egypt", "global_watch", ["ar", "en"], {"ar": "مصر"}, ("EG", "EG:ar")),
    _p("QAT", "Qatar", "global_watch", ["ar", "en"], {"ar": "قطر"}),
    _p("OMN", "Oman", "global_watch", ["ar", "en"], {"ar": "عمان"}),
    _p("PAK", "Pakistan", "global_watch", ["en"], None, ("PK", "PK:en")),
    _p("IDN", "Indonesia", "global_watch", ["en"]),
    _p("PAN", "Panama", "global_watch", ["es", "en"], {"es": "Panama"}),
    _p("BRA", "Brazil", "global_watch", ["pt", "en"], {"pt": "Brasil"},
       ("BR", "BR:pt-419")),
    _p("COL", "Colombia", "global_watch", ["es", "en"], {"es": "Colombia"},
       ("CO", "CO:es-419")),
    _p("GUY", "Guyana", "global_watch", ["en"]),
    _p("SUR", "Suriname", "global_watch", ["en", "pt"]),
    _p("MAR", "Morocco", "global_watch", ["fr", "ar", "en"],
       {"fr": "Maroc", "ar": "المغرب"}),
    _p("SOM", "Somalia", "global_watch", ["en", "ar"]),
    _p("COG", "Republic of Congo", "global_watch", ["fr", "en"], {"fr": "Congo"}),
]


# ===========================================================================
# ACCES
# ===========================================================================
def charger_pays(registre=None):
    """Tous les pays de la decouverte. Injectable pour les tests."""
    out = []
    for p in (registre if registre is not None else PAYS):
        d = dict(p)
        d.setdefault("langues", ["en"])
        d.setdefault("noms_locaux", {})
        d.setdefault("edition", None)
        d.setdefault("niveau", "global_watch")
        out.append(d)
    return out


def pays_par_iso3(iso3, registre=None):
    for p in charger_pays(registre):
        if p["iso3"] == str(iso3 or "").upper():
            return p
    return None


def pays_du_niveau(niveau, registre=None):
    return [p for p in charger_pays(registre) if p["niveau"] == niveau]


def cadence_jours(pays):
    """Nombre de jours entre deux interrogations de ce pays."""
    return CADENCE.get(pays.get("niveau"), 7)


def nom_pour_requete(pays, langue):
    """Nom du pays a utiliser dans la requete, dans la langue visee."""
    return pays.get("noms_locaux", {}).get(langue) or pays["nom"]


def params_google_news(pays, langue):
    """(hl, gl, ceid) pour ce pays dans cette langue. L'edition locale du pays
    prime quand elle existe : la presse locale y est mieux representee."""
    hl, gl, ceid = PARAMS_LANGUE.get(langue, PARAMS_LANGUE["en"])
    edition = pays.get("edition")
    if edition and langue in (pays.get("langues") or []):
        # On ne force l'edition locale que si sa langue correspond a la
        # premiere langue du pays (sinon on melangerait hl et ceid).
        if (pays.get("langues") or [""])[0] == langue:
            gl, ceid = edition
    return hl, gl, ceid


def a_interroger(pays, dernier_passage_jours):
    """Ce pays est-il du au vu de sa cadence ? Fonction PURE.
    `dernier_passage_jours` = None si jamais interroge."""
    if dernier_passage_jours is None:
        return True
    return dernier_passage_jours >= cadence_jours(pays)


def selection_du_run(derniers_passages=None, plafond=None, registre=None):
    """Pays a interroger ce run, les plus prioritaires d'abord. Fonction PURE.

    `derniers_passages` : {iso3: jours depuis le dernier passage}.
    Tri : niveau (suivi > strategique > global_watch), puis retard accumule.
    Un plafond borne le cout d'un run."""
    derniers = derniers_passages or {}
    ordre = {"suivi": 0, "strategique": 1, "global_watch": 2}
    dus = []
    for p in charger_pays(registre):
        jours = derniers.get(p["iso3"])
        if a_interroger(p, jours):
            retard = 999 if jours is None else jours - cadence_jours(p)
            dus.append((ordre.get(p["niveau"], 9), -retard, p))
    dus.sort(key=lambda t: (t[0], t[1]))
    sel = [p for _, _, p in dus]
    return sel[:plafond] if plafond else sel


def statistiques(registre=None):
    """Repartition par niveau et par langue (diagnostic)."""
    pays = charger_pays(registre)
    niveaux = collections.Counter(p["niveau"] for p in pays)
    langues = collections.Counter(l for p in pays for l in p["langues"])
    return {"total": len(pays), "par_niveau": dict(niveaux),
            "par_langue": dict(langues)}
