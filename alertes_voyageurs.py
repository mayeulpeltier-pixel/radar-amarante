# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- ALERTES VOYAGEURS (FCDO / UK Foreign Travel Advice).
======================================================================

PREMIERE BRIQUE DE LA VEILLE "SIGNAUX FAIBLES"
----------------------------------------------
Passage d'une logique reactive (appels d'offres) a une logique proactive.
Le signal ici n'est PAS le niveau d'alerte d'un pays -- c'est son CHANGEMENT.
Quand le FCDO fait basculer un pays (ex. "avoid all but essential travel" ->
"avoid all travel"), toute entreprise qui y a du personnel doit revoir son
dispositif de surete. C'est le moment ou Amarante a une carte a jouer, et il
precede l'expression publique du besoin. Le document strategique classait ce
signal parmi ceux "qui valent de l'or".

POURQUOI LE FCDO
----------------
Verifie le 23/07/2026 (sonde_alertes.py) :
  - API GOV.UK Content, JSON public, SANS cle, SANS scraping ;
  - contenu sous Open Government Licence v3.0 -> reutilisation LEGALE explicite,
    ce qui la rend sure pour un projet a visee commerciale (a la difference de
    LinkedIn/Bloomberg/scraping) ;
  - 226 pays, structure stable.

CE QUE LA SONDE A REVELE, ET QUI A DICTE CE CODE
------------------------------------------------
  - `details.alert_status` est une LISTE de codes stables, ex.
    ['avoid_all_travel_to_whole_country']. Pas de JSON a re-parser.
  - `details.change_description` : le libelle en clair du dernier changement.
  - `details.change_history` : liste horodatee (note + public_timestamp).
  - PIEGE EVITE : `updated_at` valait 2026-07-23 pour TOUS les pays (rebuild
    technique du site). Se fier a `updated_at` ferait croire que tout change a
    chaque run. C'est `public_updated_at` qu'il faut suivre : dates distinctes,
    reellement liees au contenu. Ce collecteur n'utilise QUE public_updated_at.
  - Le croisement nom FR -> slug a echoue (0/123) : le radar nomme en francais,
    le FCDO en anglais. D'ou l'OPTION 2 retenue : on resout les slugs au
    runtime depuis l'index, via une table ISO3 -> NOM ANGLAIS (plus stable
    qu'un slug, qui peut etre renomme).

MEMOIRE ET DETECTION DE CHANGEMENT
----------------------------------
Etat precedent lu dans Postgres (onglet `alertes_etat`), comme les autres
collecteurs. Pour chaque pays : on compare (alert_status, public_updated_at) a
l'etat memorise. Trois cas :
  - INCONNU (premier passage)      : on memorise, on n'emet PAS de signal
    (sinon 123 faux signaux au premier run) ;
  - INCHANGE                        : rien ;
  - CHANGE                          : on emet un lead dans `alertes_radar`, et
    on met l'etat a jour.

USAGE
-----
    python alertes_voyageurs.py
    RADAR_ALERTES_DEBUG=1 python alertes_voyageurs.py   # n'ecrit rien

VARIABLES
---------
    RADAR_ALERTES_DEBUG   1 = diagnostic, aucune ecriture
    RADAR_ALERTES_PAUSE   secondes entre requetes pays (defaut 0.4)
"""

import os
import sys
import time
import json
import urllib.request
import urllib.error
from datetime import date, datetime, timezone

import ted_complet_v14 as ted


# ===========================================================================
# CONFIGURATION
# ===========================================================================

BASE = "https://www.gov.uk/api/content/foreign-travel-advice"
UA = "radar-amarante/1.0 (business development; +https://amarante.com)"
PAUSE = float(os.environ.get("RADAR_ALERTES_PAUSE", "0.4"))
DEBUG = os.environ.get("RADAR_ALERTES_DEBUG", "0") == "1"

NOM_ONGLET = "alertes_radar"          # les signaux (changements detectes)
ONGLET_ETAT = "alertes_etat"          # la memoire (dernier niveau connu/pays)

# Severite des codes FCDO, du plus grave au plus benin. Sert a qualifier le
# SENS d'un changement (aggravation vs allegement) : une aggravation est un
# signal commercial plus chaud.
SEVERITE = {
    "avoid_all_travel_to_whole_country": 5,
    "avoid_all_travel_to_parts": 4,
    "avoid_all_but_essential_travel_to_whole_country": 3,
    "avoid_all_but_essential_travel_to_parts": 2,
    "see_our_travel_advice_before_travelling": 1,
    "": 0,
}

# Libelles lisibles (pour le lead et le dashboard).
LIBELLE = {
    "avoid_all_travel_to_whole_country": "Tout voyage deconseille (pays entier)",
    "avoid_all_travel_to_parts": "Tout voyage deconseille (certaines zones)",
    "avoid_all_but_essential_travel_to_whole_country":
        "Voyage essentiel uniquement (pays entier)",
    "avoid_all_but_essential_travel_to_parts":
        "Voyage essentiel uniquement (certaines zones)",
    "see_our_travel_advice_before_travelling":
        "Vigilance : consulter l'avis avant de voyager",
    "": "Aucune alerte particuliere",
}


# ===========================================================================
# TABLE ISO3 -> NOM ANGLAIS (perimetre radar)
# ===========================================================================
# On ne fige PAS les slugs (renommables) mais les NOMS anglais, croises au
# runtime avec l'index FCDO. Table derivee du perimetre ; les formes suivent
# les titres FCDO ("Democratic Republic of the Congo", "The Gambia"...).
# Un pays absent de cette table est simplement ignore (journalise), jamais une
# cause d'echec.
NOM_EN = {
    "AFG": "Afghanistan", "AGO": "Angola", "ALB": "Albania",
    "ARE": "United Arab Emirates", "ARG": "Argentina", "ARM": "Armenia",
    "AZE": "Azerbaijan", "BDI": "Burundi", "BEN": "Benin",
    "BFA": "Burkina Faso", "BGD": "Bangladesh", "BHR": "Bahrain",
    "BIH": "Bosnia and Herzegovina", "BLR": "Belarus", "BOL": "Bolivia",
    "BRA": "Brazil", "BWA": "Botswana", "CAF": "Central African Republic",
    "CHL": "Chile", "CIV": "Ivory Coast", "CMR": "Cameroon",
    "COD": "Democratic Republic of the Congo", "COG": "Congo",
    "COL": "Colombia", "COM": "Comoros", "CPV": "Cape Verde",
    "DJI": "Djibouti", "DZA": "Algeria", "ECU": "Ecuador", "EGY": "Egypt",
    "ERI": "Eritrea", "ETH": "Ethiopia", "FJI": "Fiji", "GAB": "Gabon",
    "GEO": "Georgia", "GHA": "Ghana", "GIN": "Guinea", "GMB": "The Gambia",
    "GNB": "Guinea-Bissau", "GNQ": "Equatorial Guinea", "GTM": "Guatemala",
    "GUF": "French Guiana", "GUY": "Guyana", "HND": "Honduras", "HTI": "Haiti",
    "IDN": "Indonesia", "IRN": "Iran", "IRQ": "Iraq", "ISR": "Israel",
    "JAM": "Jamaica", "JOR": "Jordan", "KAZ": "Kazakhstan", "KEN": "Kenya",
    "KGZ": "Kyrgyzstan", "KHM": "Cambodia", "KWT": "Kuwait", "LAO": "Laos",
    "LBN": "Lebanon", "LBR": "Liberia", "LBY": "Libya", "LKA": "Sri Lanka",
    "LSO": "Lesotho", "MAR": "Morocco", "MDA": "Moldova", "MDG": "Madagascar",
    "MEX": "Mexico", "MKD": "North Macedonia", "MLI": "Mali", "MMR": "Myanmar",
    "MNE": "Montenegro", "MNG": "Mongolia", "MOZ": "Mozambique",
    "MRT": "Mauritania", "MUS": "Mauritius", "MWI": "Malawi", "MYT": "Mayotte",
    "NAM": "Namibia", "NCL": "New Caledonia", "NER": "Niger", "NGA": "Nigeria",
    "NPL": "Nepal", "OMN": "Oman", "PAK": "Pakistan", "PER": "Peru",
    "PHL": "Philippines", "PNG": "Papua New Guinea", "PRY": "Paraguay",
    "PSE": "The Occupied Palestinian Territories", "QAT": "Qatar",
    "RUS": "Russia", "RWA": "Rwanda", "SAU": "Saudi Arabia", "SDN": "Sudan",
    "SEN": "Senegal", "SLB": "Solomon Islands", "SLE": "Sierra Leone",
    "SOM": "Somalia", "SRB": "Serbia", "SSD": "South Sudan",
    "STP": "Sao Tome and Principe", "SUR": "Suriname", "SWZ": "Eswatini",
    "SYC": "Seychelles", "SYR": "Syria", "TCD": "Chad", "TGO": "Togo",
    "TJK": "Tajikistan", "TKM": "Turkmenistan", "TTO": "Trinidad and Tobago",
    "TUN": "Tunisia", "TUR": "Turkey", "TZA": "Tanzania", "UGA": "Uganda",
    "UKR": "Ukraine", "URY": "Uruguay", "UZB": "Uzbekistan",
    "VEN": "Venezuela", "VUT": "Vanuatu", "XKX": "Kosovo", "YEM": "Yemen",
    "ZAF": "South Africa", "ZMB": "Zambia", "ZWE": "Zimbabwe",
}


def _norm(s):
    """Normalise un nom pour rapprocher titre FCDO et nom de la table."""
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


# ===========================================================================
# RESEAU
# ===========================================================================

def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))


def resoudre_slugs(session_get=_get):
    """OPTION 2 : construit ISO3 -> slug en croisant l'index FCDO (au runtime)
    avec la table ISO3 -> nom anglais. Robuste aux renommages de slug.

    Renvoie (mapping, manquants). Ne leve pas sur un index vide : renvoie un
    mapping vide, le collecteur le signalera."""
    try:
        index = session_get(BASE)
    except Exception as e:
        print("  (alertes) index FCDO inaccessible : {}".format(e))
        return {}, list(NOM_EN)
    titre_vers_slug = {}
    for enfant in (index.get("links") or {}).get("children", []):
        bp = enfant.get("base_path", "") or ""
        slug = bp.rsplit("/", 1)[-1] if bp else ""
        titre = (enfant.get("title") or "").replace(" travel advice", "")
        if slug and titre:
            titre_vers_slug[_norm(titre)] = slug
    mapping, manquants = {}, []
    for iso3, nom_en in NOM_EN.items():
        slug = titre_vers_slug.get(_norm(nom_en))
        if slug:
            mapping[iso3] = slug
        else:
            manquants.append(iso3)
    return mapping, manquants


# ===========================================================================
# LECTURE D'UNE FICHE PAYS  (fonctions PURES autant que possible)
# ===========================================================================

def niveau_max(alert_status):
    """La liste alert_status peut porter plusieurs codes : on retient le PLUS
    SEVERE, c'est lui qui qualifie le pays. Fonction pure."""
    if not isinstance(alert_status, list):
        return ""
    pire, code_pire = -1, ""
    for code in alert_status:
        s = SEVERITE.get(str(code), 0)
        if s > pire:
            pire, code_pire = s, str(code)
    return code_pire


def extraire_etat(fiche):
    """Fiche FCDO -> etat minimal a memoriser. Fonction pure.

    N'utilise QUE public_updated_at (le piege updated_at est documente en tete).
    Renvoie None si la fiche est inexploitable (jamais d'exception)."""
    if not isinstance(fiche, dict):
        return None
    details = fiche.get("details") or {}
    alert = details.get("alert_status")
    code = niveau_max(alert)
    return {
        "code": code,
        "severite": SEVERITE.get(code, 0),
        "public_updated_at": str(fiche.get("public_updated_at") or ""),
        "change_description": str(details.get("change_description") or "").strip(),
    }


# ===========================================================================
# DETECTION DE CHANGEMENT
# ===========================================================================

def sens_du_changement(avant, apres):
    """'aggravation', 'allegement' ou 'lateral' (meme severite, contenu revu).
    Fonction pure. Une aggravation est le signal commercial le plus chaud."""
    sa, sb = avant.get("severite", 0), apres.get("severite", 0)
    if sb > sa:
        return "aggravation"
    if sb < sa:
        return "allegement"
    return "lateral"


def lead_de_changement(iso3, nom_fr, avant, apres, zone):
    """Construit la ligne ecrite dans alertes_radar. Fonction pure."""
    sens = sens_du_changement(avant, apres)
    pub = apres.get("public_updated_at", "")
    return {
        "date_maj": date.today().isoformat(),
        "pays_execution": iso3,
        "pays_nom": nom_fr,
        "zone": zone,
        "niveau_avant": LIBELLE.get(avant.get("code", ""), avant.get("code", "")),
        "niveau_apres": LIBELLE.get(apres.get("code", ""), apres.get("code", "")),
        "sens": sens,
        "severite": apres.get("severite", 0),
        "motif": apres.get("change_description", ""),
        "publication_number": "FCDO-{}-{}".format(iso3, pub[:10]),
        "lien": "https://www.gov.uk/foreign-travel-advice/{}".format(
            (nom_fr or iso3)),
    }


# ===========================================================================
# MEMOIRE (Postgres, best-effort)
# ===========================================================================

def charger_etat_precedent():
    """{iso3: etat} depuis Postgres. Renvoie {} si indisponible : au premier
    run reel, tout est INCONNU -> on memorise sans emettre (pas de faux flot)."""
    try:
        import radar_stockage as st
        if not st.actif():
            return {}
        with st.connexion() as conn:
            lignes = st.lire_onglet(conn, ONGLET_ETAT)
    except Exception as e:
        print("  (pg) etat precedent illisible ({}) : premier passage suppose."
              .format(e))
        return {}
    etat = {}
    for l in lignes:
        iso3 = l.get("pays_execution")
        if iso3 and iso3 not in etat:      # lire_onglet renvoie recent d'abord
            etat[iso3] = {
                "code": l.get("code", ""),
                "severite": int(l.get("severite", 0) or 0),
                "public_updated_at": l.get("public_updated_at", ""),
                "change_description": l.get("change_description", ""),
            }
    return etat


def memoriser_etat(etats_courants):
    """Ecrit l'etat courant de CHAQUE pays lu (pas seulement les changements) :
    c'est la reference du prochain run. Best-effort."""
    lignes = []
    for iso3, e in etats_courants.items():
        lignes.append({
            "pays_execution": iso3,
            "code": e.get("code", ""),
            "severite": e.get("severite", 0),
            "public_updated_at": e.get("public_updated_at", ""),
            "change_description": e.get("change_description", ""),
            "publication_number": "ETAT-{}".format(iso3),
        })
    try:
        import radar_stockage as st
        return st.ecrire_miroir(ONGLET_ETAT, lignes)
    except Exception as e:
        return "etat non memorise ({})".format(e)


# ===========================================================================
# GOOGLE SHEETS  (ecriture des signaux)
# ===========================================================================

COLONNES = [
    "date_maj", "pays_execution", "pays_nom", "zone",
    "niveau_avant", "niveau_apres", "sens", "severite", "motif",
    "publication_number", "lien",
]


def ecrire_signaux(leads):
    """Ecrit les changements dans alertes_radar (Sheet + miroir PG). Best-effort
    cote Sheet : un echec n'empeche pas la memorisation de l'etat."""
    if not leads:
        return
    sheet_id = os.environ.get("TED_SHEET_ID", "")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
    if sheet_id:
        try:
            import gspread
            from google.oauth2.service_account import Credentials
            creds = Credentials.from_service_account_file(
                fichier, scopes=["https://www.googleapis.com/auth/spreadsheets"])
            classeur = gspread.authorize(creds).open_by_key(sheet_id)
            try:
                f = classeur.worksheet(NOM_ONGLET)
            except Exception:
                f = classeur.add_worksheet(title=NOM_ONGLET, rows=2000,
                                           cols=len(COLONNES))
                f.append_row(COLONNES)
            f.append_rows([[str(l.get(c, "")) for c in COLONNES] for l in leads],
                          value_input_option="RAW")
            print("  {} signal(aux) ecrit(s) dans '{}'.".format(len(leads), NOM_ONGLET))
        except Exception as e:
            print("  (alertes) ecriture Sheet impossible ({}). Le run continue."
                  .format(e))
    try:
        import radar_stockage as st
        print("  (pg) " + st.ecrire_miroir(NOM_ONGLET, leads))
    except Exception as e:
        print("  (pg) miroir signaux indisponible ({})".format(e))


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    print("=" * 62)
    print("ALERTES VOYAGEURS FCDO - Radar Amarante")
    print("  perimetre : {} pays{}".format(
        len(NOM_EN), " | MODE DEBUG (aucune ecriture)" if DEBUG else ""))
    print("=" * 62)

    try:
        import radar_dashboard as dash
        zone_par_iso3 = {i: (n, z) for i, (n, z) in dash.ZONE_PAR_ISO3.items()}
    except Exception:
        zone_par_iso3 = {}

    mapping, manquants = resoudre_slugs()
    print("  slugs FCDO resolus : {}/{}".format(len(mapping), len(NOM_EN)))
    if manquants:
        print("  sans slug (ignores) : {}".format(", ".join(sorted(manquants))))
    if not mapping:
        print("  Aucun slug resolu : index FCDO injoignable. Arret propre.")
        return

    precedent = charger_etat_precedent()
    premier_run = not precedent
    if premier_run:
        print("  premier passage (aucun etat memorise) : on memorise sans "
              "emettre de signal.")

    etats_courants, changements, lus, echecs = {}, [], 0, 0
    for iso3 in sorted(mapping):
        slug = mapping[iso3]
        try:
            fiche = _get("{}/{}".format(BASE, slug))
        except Exception:
            echecs += 1
            continue
        etat = extraire_etat(fiche)
        if not etat:
            echecs += 1
            continue
        lus += 1
        etats_courants[iso3] = etat

        avant = precedent.get(iso3)
        if avant is None:
            pass                                    # inconnu : memoriser, pas emettre
        elif (avant.get("public_updated_at") != etat["public_updated_at"]
              or avant.get("code") != etat["code"]):
            nom_fr, zone = zone_par_iso3.get(iso3, (iso3, "Hors zone"))
            changements.append(
                lead_de_changement(iso3, nom_fr, avant, etat, zone))
        time.sleep(PAUSE)

    print("  {} fiche(s) lue(s), {} echec(s) reseau.".format(lus, echecs))
    print("  {} changement(s) detecte(s).".format(len(changements)))

    for l in sorted(changements, key=lambda x: -x["severite"])[:15]:
        print("   [{}] {} {} : {} -> {}".format(
            l["severite"], l["pays_nom"], l["sens"],
            l["niveau_avant"], l["niveau_apres"]))
        if l["motif"]:
            print("        motif : {}".format(l["motif"][:110]))

    if DEBUG:
        print("\n  MODE DEBUG : {} signal(aux) NON ecrit(s), etat NON memorise."
              .format(len(changements)))
        return

    if not premier_run:
        ecrire_signaux(changements)
    print("  (pg) etat : " + memoriser_etat(etats_courants))


if __name__ == "__main__":
    main()
