# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- REGISTRE DES PROJETS SUIVIS (Project Intelligence, couche 3).
===============================================================================

CE QUE C'EST
------------
La liste CUREE A LA MAIN des grands projets que le radar suit nominativement.
Chaque entree porte un PROJECT_ID stable et persistant : tous les signaux
detectes plus tard se rattachent a cet identifiant, quel que soit le nom
employe par la presse.

POURQUOI UNE LISTE CUREE, ET PAS DE LA DECOUVERTE AUTOMATIQUE
--------------------------------------------------------------
La sonde (sonde_projets.py) a prouve qu'on sait SUIVRE un projet dont on
connait les alias : 143 signaux rattaches pour Inga 3 (75 % du corpus), 44 pour
Tanzania LNG (66 %), avec une profondeur de 10 a 14 ans. Elle n'a PAS prouve
qu'on sait DECOUVRIR un projet inconnu : c'est une question distincte, traitee
dans un chantier separe. On demarre donc sur une liste curee, ou chaque alias
est valide par un humain -- ce qui donne un taux de rattachement fiable.

ALIAS FORTS vs ALIAS FAIBLES (lecon directe de la sonde)
---------------------------------------------------------
  - alias        : sans ambiguite ("inga 3", "grand inga"). Rattachent seuls.
  - alias_faibles: ambigus isoles ("inga" est un prenom scandinave ; "lng" est
    un terme generique). Ne rattachent QUE s'ils sont accompagnes d'un mot de
    contexte projet. Sans cette regle, l'article pivot du cahier des charges
    ("AECOM selected for Inga studies") n'etait rattache a AUCUN projet.

VALEURS ET MONTANTS : A VERIFIER
--------------------------------
`valeur_musd` est un ORDRE DE GRANDEUR public, saisi pour amorcer le score
d'opportunite. Il doit etre valide par l'analyste avant tout usage commercial ;
laisser 0 quand on n'est pas sur vaut mieux qu'un chiffre invente. Les montants
detectes dans les signaux viendront l'affiner.

EXTENSION
---------
Ajouter un projet = ajouter une entree ici (aucun code a modifier). Le fichier
est volontairement plat et lisible : c'est un referentiel, pas de la logique.
"""

# Secteurs : cadrent les services Amarante probables (voir projets.py).
# energie | mines | transport | industrie | infrastructure

REGISTRE = [
    # ---------------------------------------------------------------- AFRIQUE
    {
        "project_id": "INGA3_COD",
        "libelle": "Inga 3 / Grand Inga",
        "pays": "RDC", "iso3": "COD", "secteur": "energie",
        "valeur_musd": 14000,          # ordre de grandeur public, a valider
        "alias": ["inga 3", "inga iii", "grand inga", "inga hydropower",
                  "barrage inga", "inga dam", "site d'inga", "inga hydroelectric"],
        "alias_faibles": ["inga"],
        "acteurs": ["aecom", "world bank", "banque mondiale", "afd", "adpi",
                    "eskom", "sinohydro", "actis", "fortescue"],
    },
    {
        "project_id": "TANZLNG_TZA",
        "libelle": "Tanzania LNG / Lindi LNG",
        "pays": "Tanzanie", "iso3": "TZA", "secteur": "energie",
        "valeur_musd": 42000,
        "alias": ["tanzania lng", "lindi lng", "tanzania liquefied",
                  "lng terminal tanzania", "lng project tanzania", "likong'o"],
        "alias_faibles": ["lng"],
        "acteurs": ["shell", "equinor", "exxonmobil", "exxon", "tpdc",
                    "ophir", "pavilion energy"],
    },
    {
        "project_id": "MOZLNG_MOZ",
        "libelle": "Mozambique LNG (Afungi, Cabo Delgado)",
        "pays": "Mozambique", "iso3": "MOZ", "secteur": "energie",
        "valeur_musd": 20000,
        "alias": ["mozambique lng", "afungi", "cabo delgado lng",
                  "area 1 mozambique", "rovuma lng"],
        "alias_faibles": [],
        "acteurs": ["totalenergies", "total", "mitsui", "enh", "exxonmobil",
                    "saipem", "cciv", "bechtel"],
    },
    {
        "project_id": "CORALSUL_MOZ",
        "libelle": "Coral Sul / Coral Norte FLNG",
        "pays": "Mozambique", "iso3": "MOZ", "secteur": "energie",
        "valuer_musd": 0, "valeur_musd": 7000,
        "alias": ["coral sul", "coral norte", "coral flng"],
        "alias_faibles": [],
        "acteurs": ["eni", "exxonmobil", "cnpc", "enh", "saipem"],
    },
    {
        "project_id": "SIMANDOU_GIN",
        "libelle": "Simandou (minerai de fer + corridor ferroviaire)",
        "pays": "Guinee", "iso3": "GIN", "secteur": "mines",
        "valeur_musd": 20000,
        "alias": ["simandou", "simfer", "winning consortium simandou", "wcs"],
        "alias_faibles": [],
        "acteurs": ["rio tinto", "chinalco", "baowu", "winning", "simfer"],
    },
    {
        "project_id": "LOBITO_AGO",
        "libelle": "Corridor de Lobito (rail Angola / RDC / Zambie)",
        "pays": "Angola", "iso3": "AGO", "secteur": "transport",
        "valeur_musd": 5000,
        "alias": ["lobito corridor", "corridor de lobito", "lobito atlantic",
                  "benguela railway", "chemin de fer de benguela"],
        "alias_faibles": [],
        "acteurs": ["trafigura", "mota-engil", "vecturis", "dfc", "afdb"],
    },
    {
        "project_id": "EACOP_UGA",
        "libelle": "EACOP (oleoduc Ouganda-Tanzanie) et Tilenga",
        "pays": "Ouganda", "iso3": "UGA", "secteur": "energie",
        "valeur_musd": 5000,
        "alias": ["eacop", "east african crude oil pipeline", "tilenga",
                  "kingfisher project", "oleoduc ouganda"],
        "alias_faibles": [],
        "acteurs": ["totalenergies", "total", "cnooc", "unoc", "tpdc"],
    },
    {
        "project_id": "GRANDTORTUE_SEN",
        "libelle": "Grand Tortue Ahmeyim (GTA)",
        "pays": "Senegal / Mauritanie", "iso3": "SEN", "secteur": "energie",
        "valeur_musd": 5000,
        "alias": ["grand tortue", "gta project", "ahmeyim", "greater tortue"],
        "alias_faibles": [],
        "acteurs": ["bp", "kosmos", "petrosen", "smh", "golar"],
    },
    {
        "project_id": "NIGERMOROC_NGA",
        "libelle": "Gazoduc Nigeria-Maroc",
        "pays": "Nigeria / Maroc", "iso3": "NGA", "secteur": "energie",
        "valeur_musd": 25000,
        "alias": ["nigeria-morocco gas pipeline", "gazoduc nigeria-maroc",
                  "nigeria morocco pipeline", "african atlantic gas pipeline"],
        "alias_faibles": [],
        "acteurs": ["nnpc", "onhym", "ecowas", "cedeao"],
    },
    # ------------------------------------------------------------------ SAHEL
    {
        "project_id": "GUELBS_MRT",
        "libelle": "Extension miniere Guelb / SNIM",
        "pays": "Mauritanie", "iso3": "MRT", "secteur": "mines",
        "valeur_musd": 1500,
        "alias": ["guelb el rhein", "guelbs ii", "snim expansion", "projet guelbs"],
        "alias_faibles": [],
        "acteurs": ["snim", "afdb", "bad"],
    },
    {
        "project_id": "DESERTTOPOWER_SAHEL",
        "libelle": "Desert to Power (solaire Sahel)",
        "pays": "Sahel (multi-pays)", "iso3": "NER", "secteur": "energie",
        "valeur_musd": 20000,
        "alias": ["desert to power", "desert-to-power"],
        "alias_faibles": [],
        "acteurs": ["afdb", "bad", "green climate fund", "world bank"],
    },
    # -------------------------------------------------------------- MENA / EST
    {
        "project_id": "RECONSTRUCTION_UKR",
        "libelle": "Reconstruction Ukraine (energie et infrastructures)",
        "pays": "Ukraine", "iso3": "UKR", "secteur": "infrastructure",
        "valeur_musd": 0,              # trop heterogene pour un chiffre unique
        "alias": ["ukraine reconstruction", "reconstruction de l'ukraine",
                  "ukraine recovery conference", "ukraine facility"],
        "alias_faibles": [],
        "acteurs": ["ebrd", "berd", "world bank", "eib", "dfc", "ifc"],
    },
    {
        "project_id": "BASRAGAS_IRQ",
        "libelle": "Gas Growth Integrated Project (Bassorah)",
        "pays": "Irak", "iso3": "IRQ", "secteur": "energie",
        "valeur_musd": 27000,
        "alias": ["gas growth integrated project", "ggip", "ratawi",
                  "basrah gas", "artawi"],
        "alias_faibles": [],
        "acteurs": ["totalenergies", "total", "qatarenergy", "basrah oil company"],
    },
    {
        "project_id": "DEVCORRIDOR_IRQ",
        "libelle": "Route du developpement (Grand Faw / corridor irakien)",
        "pays": "Irak", "iso3": "IRQ", "secteur": "transport",
        "valeur_musd": 17000,
        "alias": ["development road", "route du developpement", "grand faw",
                  "al faw port", "port de faw"],
        "alias_faibles": [],
        "acteurs": ["daewoo", "iraq ministry of transport", "turkiye"],
    },
    # ----------------------------------------------------- AMERIQUE / ASIE CENTR.
    {
        "project_id": "CANALSECO_PAN",
        "libelle": "Projets hydriques et logistiques du Canal de Panama",
        "pays": "Panama", "iso3": "PAN", "secteur": "transport",
        "valeur_musd": 1600,
        "alias": ["rio indio", "panama canal water", "canal de panama reservoir"],
        "alias_faibles": [],
        "acteurs": ["panama canal authority", "acp"],
    },
    {
        "project_id": "TENGIZ_KAZ",
        "libelle": "Tengiz / Future Growth Project",
        "pays": "Kazakhstan", "iso3": "KAZ", "secteur": "energie",
        "valeur_musd": 48000,
        "alias": ["tengizchevroil", "future growth project tengiz", "tengiz expansion"],
        "alias_faibles": ["tengiz"],
        "acteurs": ["chevron", "exxonmobil", "kazmunaygas", "lukoil"],
    },
    {
        "project_id": "CASA1000_TJK",
        "libelle": "CASA-1000 (interconnexion electrique Asie centrale-Sud)",
        "pays": "Tadjikistan", "iso3": "TJK", "secteur": "energie",
        "valeur_musd": 1200,
        "alias": ["casa-1000", "casa 1000"],
        "alias_faibles": [],
        "acteurs": ["world bank", "banque mondiale", "islamic development bank"],
    },
    {
        "project_id": "ROGUN_TJK",
        "libelle": "Barrage de Rogun",
        "pays": "Tadjikistan", "iso3": "TJK", "secteur": "energie",
        "valeur_musd": 6000,
        "alias": ["rogun dam", "barrage de rogun", "rogun hpp"],
        "alias_faibles": ["rogun"],
        "acteurs": ["world bank", "webuild", "salini"],
    },
    {
        "project_id": "TAPI_TKM",
        "libelle": "Gazoduc TAPI (Turkmenistan-Afghanistan-Pakistan-Inde)",
        "pays": "Turkmenistan", "iso3": "TKM", "secteur": "energie",
        "valeur_musd": 10000,
        "alias": ["tapi pipeline", "gazoduc tapi", "turkmenistan afghanistan pakistan india"],
        "alias_faibles": ["tapi"],
        "acteurs": ["turkmengaz", "adb", "isdb"],
    },
]


def charger_registre(registre=None):
    """Liste des projets suivis. Parametre injectable pour les tests.
    Normalise les champs optionnels pour que les appelants n'aient jamais a
    tester leur presence."""
    out = []
    for p in (registre if registre is not None else REGISTRE):
        d = dict(p)
        d.setdefault("alias", [])
        d.setdefault("alias_faibles", [])
        d.setdefault("acteurs", [])
        d.setdefault("secteur", "infrastructure")
        d.setdefault("valeur_musd", 0)
        d.setdefault("iso3", "")
        d.pop("valuer_musd", None)      # garde-fou : faute de frappe eventuelle
        out.append(d)
    return out


def projet_par_id(project_id, registre=None):
    """Un projet du registre par son PROJECT_ID, ou None."""
    for p in charger_registre(registre):
        if p["project_id"] == project_id:
            return p
    return None
