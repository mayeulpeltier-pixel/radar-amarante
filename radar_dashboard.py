# -*- coding: utf-8 -*-
"""
Radar Amarante -- Generateur de tableau de bord
================================================

Lit les deux onglets du Google Sheet (ted_radar, bm_radar) et produit une page
web autonome (public/index.html), le "tableau de situation", destinee a etre
publiee sur Cloudflare Pages apres chaque run du radar.

PRINCIPE : ce script ne fait que LIRE le Sheet et ECRIRE un fichier HTML. Il ne
touche ni aux collecteurs, ni au Sheet. Il lit par NOM de colonne (get_all_records),
donc il reste correct meme si l'ordre des colonnes change.

ENV attendues (fournies par GitHub Actions ou une cellule Colab) :
  - TED_SHEET_ID                 (obligatoire)
  - GOOGLE_SERVICE_ACCOUNT_FILE  (obligatoire, meme compte de service que les collecteurs)
  - DASHBOARD_OUTPUT             (optionnel, defaut "public/index.html")

LANCEMENT :  python radar_dashboard.py
"""

import json
import os
import sys
from datetime import date

NOM_ONGLET_TED = "ted_radar"
NOM_ONGLET_BM = "bm_radar"

# ===========================================================================
# RESOLUTION PAYS -> (nom affiche, zone)
# BM stocke le NOM du pays, TED stocke un CODE ISO. On gere les deux.
# Les zones sont volontairement larges (lecture operationnelle, pas geographie fine).
# ===========================================================================
ZONE_PAR_NOM = {
    # Sahel
    "mali": ("Mali", "Sahel"), "niger": ("Niger", "Sahel"),
    "burkina faso": ("Burkina Faso", "Sahel"), "chad": ("Tchad", "Sahel"),
    "mauritania": ("Mauritanie", "Sahel"),
    # Afrique de l'Ouest (hors Sahel)
    "cote d'ivoire": ("Côte d'Ivoire", "Afrique de l'Ouest"),
    "côte d'ivoire": ("Côte d'Ivoire", "Afrique de l'Ouest"),
    "nigeria": ("Nigeria", "Afrique de l'Ouest"),
    "senegal": ("Sénégal", "Afrique de l'Ouest"), "ghana": ("Ghana", "Afrique de l'Ouest"),
    "benin": ("Bénin", "Afrique de l'Ouest"), "togo": ("Togo", "Afrique de l'Ouest"),
    "guinea": ("Guinée", "Afrique de l'Ouest"), "liberia": ("Libéria", "Afrique de l'Ouest"),
    # Afrique centrale
    "congo, democratic republic of": ("RDC", "Afrique centrale"),
    "democratic republic of congo": ("RDC", "Afrique centrale"),
    "congo, republic of": ("Congo-Brazzaville", "Afrique centrale"),
    "cameroon": ("Cameroun", "Afrique centrale"),
    "central african republic": ("Centrafrique", "Afrique centrale"),
    "chad ": ("Tchad", "Afrique centrale"), "gabon": ("Gabon", "Afrique centrale"),
    # Afrique de l'Est / Corne
    "ethiopia": ("Éthiopie", "Afrique de l'Est"), "kenya": ("Kenya", "Afrique de l'Est"),
    "uganda": ("Ouganda", "Afrique de l'Est"), "tanzania": ("Tanzanie", "Afrique de l'Est"),
    "somalia": ("Somalie", "Afrique de l'Est"),
    "somalia, federal republic of": ("Somalie", "Afrique de l'Est"),
    "south sudan": ("Soudan du Sud", "Afrique de l'Est"),
    "rwanda": ("Rwanda", "Afrique de l'Est"), "djibouti": ("Djibouti", "Afrique de l'Est"),
    # Afrique australe / Ocean Indien
    "mozambique": ("Mozambique", "Afrique australe"),
    "madagascar": ("Madagascar", "Afrique australe"),
    "south africa": ("Afrique du Sud", "Afrique australe"),
    "zambia": ("Zambie", "Afrique australe"), "zimbabwe": ("Zimbabwe", "Afrique australe"),
    "malawi": ("Malawi", "Afrique australe"), "angola": ("Angola", "Afrique australe"),
    "botswana": ("Botswana", "Afrique australe"),
    # Afrique du Nord
    "egypt, arab republic of": ("Égypte", "Afrique du Nord"),
    "egypt": ("Égypte", "Afrique du Nord"), "morocco": ("Maroc", "Afrique du Nord"),
    "tunisia": ("Tunisie", "Afrique du Nord"), "algeria": ("Algérie", "Afrique du Nord"),
    "libya": ("Libye", "Afrique du Nord"),
    # Proche / Moyen-Orient
    "west bank and gaza": ("Cisjordanie et Gaza", "Proche-Orient"),
    "jordan": ("Jordanie", "Proche-Orient"), "lebanon": ("Liban", "Proche-Orient"),
    "iraq": ("Irak", "Proche-Orient"), "yemen, republic of": ("Yémen", "Proche-Orient"),
    "yemen": ("Yémen", "Proche-Orient"),
    "turkiye": ("Turquie", "Proche-Orient"), "türkiye": ("Turquie", "Proche-Orient"),
    "turkey": ("Turquie", "Proche-Orient"),
    # Asie centrale
    "uzbekistan": ("Ouzbékistan", "Asie centrale"),
    "tajikistan": ("Tadjikistan", "Asie centrale"),
    "kyrgyz republic": ("Kirghizistan", "Asie centrale"),
    "kazakhstan": ("Kazakhstan", "Asie centrale"),
    # Asie du Sud / Sud-Est
    "bangladesh": ("Bangladesh", "Asie du Sud"), "pakistan": ("Pakistan", "Asie du Sud"),
    "india": ("Inde", "Asie du Sud"), "nepal": ("Népal", "Asie du Sud"),
    "indonesia": ("Indonésie", "Asie du Sud-Est"),
    "philippines": ("Philippines", "Asie du Sud-Est"),
    # Europe de l'Est / Balkans / Caucase
    "ukraine": ("Ukraine", "Europe de l'Est"), "moldova": ("Moldavie", "Europe de l'Est"),
    "albania": ("Albanie", "Balkans"), "north macedonia": ("Macédoine du Nord", "Balkans"),
    "serbia": ("Serbie", "Balkans"), "georgia": ("Géorgie", "Caucase"),
    "armenia": ("Arménie", "Caucase"), "azerbaijan": ("Azerbaïdjan", "Caucase"),
    # Amerique latine / Caraibes
    "haiti": ("Haïti", "Caraïbes"), "jamaica": ("Jamaïque", "Caraïbes"),
    "mexico": ("Mexique", "Amérique latine"), "ecuador": ("Équateur", "Amérique latine"),
    "brazil": ("Brésil", "Amérique latine"), "colombia": ("Colombie", "Amérique latine"),
    # Europe de l'Ouest (TED, faible interet operationnel)
    "france": ("France", "Europe de l'Ouest"), "germany": ("Allemagne", "Europe de l'Ouest"),
    "denmark": ("Danemark", "Europe de l'Ouest"), "new caledonia": ("Nouvelle-Calédonie", "Outre-mer"),
}

# TED stocke des codes ISO. Table code -> (nom FR, zone).
ZONE_PAR_ISO3 = {
    "MLI": ("Mali", "Sahel"), "NER": ("Niger", "Sahel"), "BFA": ("Burkina Faso", "Sahel"),
    "TCD": ("Tchad", "Sahel"), "MRT": ("Mauritanie", "Sahel"),
    "CIV": ("Côte d'Ivoire", "Afrique de l'Ouest"), "NGA": ("Nigeria", "Afrique de l'Ouest"),
    "SEN": ("Sénégal", "Afrique de l'Ouest"), "GHA": ("Ghana", "Afrique de l'Ouest"),
    "TGO": ("Togo", "Afrique de l'Ouest"), "BEN": ("Bénin", "Afrique de l'Ouest"),
    "COD": ("RDC", "Afrique centrale"), "COG": ("Congo-Brazzaville", "Afrique centrale"),
    "CMR": ("Cameroun", "Afrique centrale"), "CAF": ("Centrafrique", "Afrique centrale"),
    "GAB": ("Gabon", "Afrique centrale"),
    "ETH": ("Éthiopie", "Afrique de l'Est"), "KEN": ("Kenya", "Afrique de l'Est"),
    "UGA": ("Ouganda", "Afrique de l'Est"), "TZA": ("Tanzanie", "Afrique de l'Est"),
    "SOM": ("Somalie", "Afrique de l'Est"), "SSD": ("Soudan du Sud", "Afrique de l'Est"),
    "MOZ": ("Mozambique", "Afrique australe"), "MDG": ("Madagascar", "Afrique australe"),
    "ZAF": ("Afrique du Sud", "Afrique australe"), "AGO": ("Angola", "Afrique australe"),
    "ZMB": ("Zambie", "Afrique australe"), "BWA": ("Botswana", "Afrique australe"),
    "EGY": ("Égypte", "Afrique du Nord"), "MAR": ("Maroc", "Afrique du Nord"),
    "TUN": ("Tunisie", "Afrique du Nord"), "DZA": ("Algérie", "Afrique du Nord"),
    "LBY": ("Libye", "Afrique du Nord"),
    "PSE": ("Cisjordanie et Gaza", "Proche-Orient"), "JOR": ("Jordanie", "Proche-Orient"),
    "LBN": ("Liban", "Proche-Orient"), "IRQ": ("Irak", "Proche-Orient"),
    "YEM": ("Yémen", "Proche-Orient"), "TUR": ("Turquie", "Proche-Orient"),
    "UZB": ("Ouzbékistan", "Asie centrale"), "TJK": ("Tadjikistan", "Asie centrale"),
    "KGZ": ("Kirghizistan", "Asie centrale"), "KAZ": ("Kazakhstan", "Asie centrale"),
    "BGD": ("Bangladesh", "Asie du Sud"), "PAK": ("Pakistan", "Asie du Sud"),
    "IND": ("Inde", "Asie du Sud"), "IDN": ("Indonésie", "Asie du Sud-Est"),
    "UKR": ("Ukraine", "Europe de l'Est"), "MDA": ("Moldavie", "Europe de l'Est"),
    "ALB": ("Albanie", "Balkans"), "MKD": ("Macédoine du Nord", "Balkans"),
    "SRB": ("Serbie", "Balkans"), "GEO": ("Géorgie", "Caucase"),
    "HTI": ("Haïti", "Caraïbes"), "JAM": ("Jamaïque", "Caraïbes"),
    "MEX": ("Mexique", "Amérique latine"), "ECU": ("Équateur", "Amérique latine"),
    "BRA": ("Brésil", "Amérique latine"), "OMN": ("Oman", "Péninsule arabique"),
    "FRA": ("France", "Europe de l'Ouest"), "DEU": ("Allemagne", "Europe de l'Ouest"),
    "DNK": ("Danemark", "Europe de l'Ouest"), "NCL": ("Nouvelle-Calédonie", "Outre-mer"),
}

# Ordre d'affichage des zones (les autres suivent, "Non classé" en dernier)
ORDRE_ZONES = [
    "Afrique de l'Ouest", "Sahel", "Afrique centrale", "Afrique de l'Est",
    "Afrique australe", "Afrique du Nord", "Proche-Orient", "Péninsule arabique",
    "Asie centrale", "Asie du Sud", "Asie du Sud-Est", "Caucase", "Balkans",
    "Europe de l'Est", "Caraïbes", "Amérique latine", "Europe de l'Ouest",
    "Outre-mer", "Non classé",
]


def resoudre_pays(brut, source):
    """Renvoie (nom_affiche, zone) a partir du champ pays_execution."""
    brut = _txt(brut)
    if not brut:
        return ("Pays non précisé", "Non classé")
    if source == "TED":
        code = brut.split(",")[0].strip().upper()
        if code in ZONE_PAR_ISO3:
            return ZONE_PAR_ISO3[code]
        return (code or "Pays non précisé", "Non classé")
    # BM : nom lisible
    nom = brut.split(",")[0].strip() if brut.lower() not in ZONE_PAR_NOM else brut
    cle = brut.lower().strip()
    if cle in ZONE_PAR_NOM:
        return ZONE_PAR_NOM[cle]
    cle2 = nom.lower().strip()
    if cle2 in ZONE_PAR_NOM:
        return ZONE_PAR_NOM[cle2]
    return (nom or brut, "Non classé")


def _txt(v):
    """Cellule -> texte propre. gspread peut renvoyer des int/float (ex. un
    numero de telephone ou un score), donc on force en str avant tout .strip()."""
    if v is None:
        return ""
    return str(v).strip()


def _vrai(v):
    """Interprete une valeur de cellule comme booleen (divergence, securite)."""
    return _txt(v).lower() in ("true", "vrai", "1", "oui", "yes")


def _num(v, defaut=0.0):
    try:
        return float(str(v).replace(",", ".").strip())
    except (ValueError, AttributeError):
        return defaut


_MOIS_FR = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet",
            "août", "septembre", "octobre", "novembre", "décembre"]


def _mois_depuis_date(s):
    """'2026-06-28' -> ('2026-06', 'juin 2026'). Gere aussi JJ/MM/AAAA.
    Renvoie ('', 'Sans date') si illisible (l'avis ira dans un groupe a part)."""
    import re
    s = _txt(s)
    m = re.match(r"(\d{4})-(\d{2})", s)
    if m:
        annee, mois = m.group(1), int(m.group(2))
    else:
        m2 = re.match(r"\d{2}/(\d{2})/(\d{4})", s)
        if m2:
            annee, mois = m2.group(2), int(m2.group(1))
        else:
            return ("", "Sans date")
    if 1 <= mois <= 12:
        return ("{}-{:02d}".format(annee, mois), "{} {}".format(_MOIS_FR[mois], annee))
    return ("", "Sans date")


def ligne_vers_lead(row, source):
    """Transforme une ligne de Sheet (dict par nom de colonne) en lead unifie.
    Toutes les cellules passent par _txt() : gspread peut renvoyer des nombres
    (telephone, score, identifiant) la ou on attend du texte."""
    pays_brut = _txt(row.get("pays_execution"))
    nom_pays, zone = resoudre_pays(pays_brut, source)
    if source == "PRIVÉ":
        zone = _txt(row.get("zone")) or zone
    action = _txt(row.get("action_recommandee")).lower()

    if source == "BM":
        cible = _txt(row.get("cible_commerciale_reelle")) or \
            "Titulaire du marché qui déploie les équipes, pas l'agence acheteuse."
        groupe = _txt(row.get("procurement_group")) or "n.c."
    elif source == "BOAMP":
        cible = _txt(row.get("cible_commerciale_reelle")) or \
            "Acheteur public ou titulaire du marché ; vérifier qui expose du personnel."
        groupe = _txt(row.get("procurement_method")) or "MP"
    elif source == "PRIVÉ":
        cible = _txt(row.get("cible_commerciale_reelle")) or \
            "Contact via réseau : direction sûreté / export / MCO."
        groupe = _txt(row.get("type_activite")) or "signal"
    else:
        cible = "Bureau ou consortium titulaire du marché, pas le bailleur."
        groupe = "AT"

    # Periode : mois de PREMIERE detection (date_detection ne change jamais ;
    # a defaut, date de derniere maj). Permet de classer les leads par mois.
    date_det = _txt(row.get("date_detection")) or _txt(row.get("date_maj"))
    mois_cle, mois_label = _mois_depuis_date(date_det)
    # Statut de suivi (CRM) : ce que tu ecris a la main dans le Sheet.
    statut = _txt(row.get("statut_suivi")) or "nouveau"

    return {
        "src": source,
        "pays": nom_pays,
        "zone": zone,
        "titre": _txt(row.get("titre")),
        "agence": _txt(row.get("acheteur")) or "n.c.",
        "final": round(_num(row.get("score_final")), 1),
        "surete": round(_num(row.get("score_surete")), 1),
        "comm": round(_num(row.get("score_commercial")), 1),
        "action": action or "n.c.",
        "win": _txt(row.get("fenetre_action")) or "indetermine",
        "nom": _txt(row.get("contact_name")) or "n.c.",
        "email": _txt(row.get("contact_email")) or "n.c.",
        "tel": _txt(row.get("contact_phone")) or "n.c.",
        "cible": cible,
        "justif": _txt(row.get("justification")),
        "grp": groupe,
        "lien": _txt(row.get("lien_avis")),
        "ecart": _vrai(row.get("divergence")),
        "secu": _vrai(row.get("securite_existante_detectee")),
        "mois": mois_cle,
        "mois_label": mois_label,
        "date_det": date_det,
        "statut": statut,
        "deadline": _txt(row.get("deadline")),
        "conf": _txt(row.get("confiance")),
        "modele": _txt(row.get("modele")),
    }


def construire_leads(lignes_ted, lignes_bm, lignes_boamp=None, lignes_prive=None,
                     enrichissement=None):
    """Fusionne les onglets (TED, Banque Mondiale, BOAMP, PRIVÉ/BITD), deduplique,
    trie. Pour les leads PRIVÉ, remonte le dirigeant (enrichissement) comme contact."""
    leads = [ligne_vers_lead(r, "TED") for r in lignes_ted]
    leads += [ligne_vers_lead(r, "BM") for r in lignes_bm]
    leads += [ligne_vers_lead(r, "BOAMP") for r in (lignes_boamp or [])]
    leads_prive = [ligne_vers_lead(r, "PRIVÉ") for r in (lignes_prive or [])]

    enrichissement = enrichissement or {}
    for l in leads_prive:
        info = enrichissement.get(l["agence"].lower())
        if info:
            dirigeant = _txt(info.get("dirigeant_principal"))
            if dirigeant and l["nom"] in ("", "n.c."):
                l["nom"] = dirigeant
            email = _txt(info.get("email_pro"))
            if email:
                l["email"] = email          # contact Hunter -> pre-remplit le mailto
            l["siren"] = _txt(info.get("siren"))
            l["ca"] = _txt(info.get("chiffre_affaires"))
    leads += leads_prive

    leads = [l for l in leads if l["titre"]]  # avis exploitables seulement

    # Deduplication : un meme avis (meme lien, ou meme pays+titre) ne doit
    # apparaitre qu'une fois, meme si la feuille contient des lignes en double.
    # On garde l'occurrence au meilleur score.
    uniques = {}
    for l in leads:
        cle = l["lien"] or (l["src"] + "|" + l["pays"] + "|" + l["agence"] + "|" + l["titre"])
        if cle not in uniques or l["final"] > uniques[cle]["final"]:
            uniques[cle] = l
    leads = list(uniques.values())

    leads.sort(key=lambda l: l["final"], reverse=True)
    return leads


def _lignes_vers_dicts(valeurs, colonnes):
    """Transforme une grille brute (get_all_values) en liste de dicts, en
    mappant PAR POSITION sur l'ordre officiel `colonnes` (source de verite =
    les collecteurs). On ignore la ligne d'en-tete de la feuille, qui peut etre
    restee sur un ancien schema et provoquer un decalage. Robuste."""
    if not valeurs:
        return []
    debut = 0
    premiere = [str(c).strip() for c in valeurs[0]]
    # Detecte une ligne d'en-tete : mots-cles connus OU 1re cellule = 1re colonne.
    if ("score_final" in premiere or "titre" in premiere
            or premiere[:1] == ["date_maj"] or (colonnes and premiere[:1] == [colonnes[0]])):
        debut = 1
    dicts = []
    for row in valeurs[debut:]:
        if not any(str(c).strip() for c in row):
            continue  # ligne vide
        d = {nom: (row[i] if i < len(row) else "") for i, nom in enumerate(colonnes)}
        dicts.append(d)
    return dicts


def lire_onglets(sheet_id, fichier_cs):
    """Lit les deux onglets via gspread, en lecture POSITIONNELLE (get_all_values
    + ordre officiel des colonnes), pas get_all_records (sensible aux en-tetes).
    Import paresseux : pas requis pour les tests de generation HTML."""
    import gspread
    from google.oauth2.service_account import Credentials
    import ted_complet_v14 as ted
    import ted_complet_bm as bm

    portee = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = Credentials.from_service_account_file(fichier_cs, scopes=portee)
    client = gspread.authorize(creds)
    classeur = client.open_by_key(sheet_id)

    def valeurs(nom):
        try:
            return classeur.worksheet(nom).get_all_values()
        except gspread.WorksheetNotFound:
            print("  (info) onglet '{}' introuvable, ignore.".format(nom))
            return []

    # On lit TOUTES les colonnes (y compris statut_suivi et date_detection,
    # situees apres la zone preservee) pour le CRM et le tri par mois.
    lignes_ted = _lignes_vers_dicts(valeurs(NOM_ONGLET_TED), ted.TOUTES_COLONNES_SHEET)
    lignes_bm = _lignes_vers_dicts(valeurs(NOM_ONGLET_BM), bm.TOUTES_COLONNES_BM)

    # BOAMP (marches publics FR) : meme schema que la Banque Mondiale.
    try:
        import ted_complet_boamp as boamp
        onglet_boamp = boamp.NOM_ONGLET_BOAMP
    except Exception:
        onglet_boamp = "boamp_radar"
    lignes_boamp = _lignes_vers_dicts(valeurs(onglet_boamp), bm.TOUTES_COLONNES_BM)

    # Onglet des signaux prives (BITD), s'il existe. Schema fourni par le moteur.
    try:
        import bitd_signaux as bitd
        colonnes_prive = bitd.TOUTES_COLONNES_PRIVE
        onglet_prive = bitd.NOM_ONGLET_PRIVE
    except Exception:
        onglet_prive = "prive_radar"
        colonnes_prive = [
            "date_maj", "score_final", "score_surete", "score_commercial",
            "action_recommandee", "fenetre_action", "titre", "acheteur",
            "pays_execution", "zone", "type_activite", "confiance", "modele",
            "cible_commerciale_reelle", "justification", "entreprise",
            "priorite_compte", "publication_number", "lien_avis",
            "date_publication", "statut_suivi", "date_detection"]
    lignes_prive = _lignes_vers_dicts(valeurs(onglet_prive), colonnes_prive)

    # Enrichissement firmographique (dirigeants), pour remonter le contact.
    try:
        import enrichir_entreprises as ee
        colonnes_enr = ee.COLONNES_ENRICHIES
        onglet_enr = ee.NOM_ONGLET_ENRICHIES
    except Exception:
        onglet_enr = "entreprises_enrichies"
        colonnes_enr = ["entreprise", "siren", "nom_officiel", "dirigeant_principal",
                        "autres_dirigeants", "activite_naf", "effectif", "ville",
                        "chiffre_affaires", "source", "date_enrichissement"]
    enrichissement = {}
    for d in _lignes_vers_dicts(valeurs(onglet_enr), colonnes_enr):
        nom = _txt(d.get("entreprise")).lower()
        if nom:
            enrichissement[nom] = d

    # Contacts (Hunter) : email pro par entreprise, injecte dans l'enrichissement.
    try:
        import enrichir_entreprises as ee
        onglet_contacts = ee.NOM_ONGLET_CONTACTS
        colonnes_contacts = ee.COLONNES_CONTACTS
    except Exception:
        onglet_contacts = "contacts_bitd"
        colonnes_contacts = ["entreprise", "email_pro", "confiance", "source", "date_contact"]
    for d in _lignes_vers_dicts(valeurs(onglet_contacts), colonnes_contacts):
        nom = _txt(d.get("entreprise")).lower()
        email = _txt(d.get("email_pro"))
        if nom and email:
            enrichissement.setdefault(nom, {})["email_pro"] = email

    return lignes_ted, lignes_bm, lignes_boamp, lignes_prive, enrichissement


def generer_html(leads):
    """Produit la page HTML autonome (situation board) a partir des leads."""
    meta = {
        "date": date.today().strftime("%d/%m/%Y"),
        "total": len(leads),
        "contacter": sum(1 for l in leads if l["action"] == "contacter"),
        "surveiller": sum(1 for l in leads if l["action"] == "surveiller"),
        "ignorer": sum(1 for l in leads if l["action"] == "ignorer"),
    }
    # Mois presents, du plus recent au plus ancien (pour les onglets periode).
    labels = {}
    for l in leads:
        if l["mois"]:
            labels[l["mois"]] = l["mois_label"]
    meta["mois"] = [{"cle": c, "label": labels[c]} for c in sorted(labels, reverse=True)]
    leads_json = json.dumps(leads, ensure_ascii=False)
    meta_json = json.dumps(meta, ensure_ascii=False)
    return (GABARIT_HTML
            .replace("__LEADS_JSON__", leads_json)
            .replace("__META_JSON__", meta_json))


def main():
    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier_cs = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    sortie = os.environ.get("DASHBOARD_OUTPUT", "public/index.html")

    if not sheet_id or not fichier_cs:
        print("ERREUR : TED_SHEET_ID et GOOGLE_SERVICE_ACCOUNT_FILE sont requis.")
        sys.exit(1)

    print("Lecture des onglets du Sheet...")
    lignes_ted, lignes_bm, lignes_boamp, lignes_prive, enrichissement = lire_onglets(sheet_id, fichier_cs)
    leads = construire_leads(lignes_ted, lignes_bm, lignes_boamp, lignes_prive, enrichissement)
    print("  TED : {} avis | BM : {} avis | total exploitable : {}".format(
        len(lignes_ted), len(lignes_bm), len(leads)))

    html = generer_html(leads)
    dossier = os.path.dirname(sortie)
    if dossier:
        os.makedirs(dossier, exist_ok=True)
    with open(sortie, "w", encoding="utf-8") as f:
        f.write(html)
    print("Tableau de bord ecrit dans : {} ({} octets)".format(sortie, len(html)))


# ===========================================================================
# GABARIT HTML (situation board). __LEADS_JSON__ et __META_JSON__ injectes.
# ===========================================================================
GABARIT_HTML = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Radar Amarante, tableau de situation</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Jost:wght@300;400;500;600;700&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
  :root{
    --ink:#16100F; --ink-2:#1F1715; --ink-3:#281D1B;
    --bone:#ECE4DA; --bone-dim:#A99E92; --bone-faint:#6E645C;
    --oxblood:#6F0E27; --oxblood-2:#8C1D2C;
    --fort:#C0273A; --fort-soft:rgba(192,39,58,0.14);
    --watch:#C8893B; --watch-soft:rgba(200,137,59,0.13);
    --low:#5C6670; --low-soft:rgba(92,102,112,0.12);
    --line:rgba(236,228,218,0.12); --line-2:rgba(236,228,218,0.06);
    --display:'Jost',-apple-system,system-ui,sans-serif;
    --body:'Inter',-apple-system,system-ui,sans-serif;
    --mono:'IBM Plex Mono',ui-monospace,monospace;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  html{-webkit-text-size-adjust:100%}
  body{
    font-family:var(--body); color:var(--bone); background:var(--ink);
    line-height:1.5; font-size:15px; padding-bottom:4rem;
    background-image:
      radial-gradient(1200px 600px at 78% -8%, rgba(140,29,44,0.18), transparent 60%),
      repeating-radial-gradient(circle at 82% 4%, transparent 0 34px, rgba(236,228,218,0.018) 34px 35px);
    min-height:100vh;
  }
  .wrap{max-width:1180px;margin:0 auto;padding:0 18px}
  header.mast{border-bottom:1px solid var(--line);padding:26px 0 20px;margin-bottom:22px}
  .mast-top{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
  .brand{display:flex;align-items:center;gap:13px}
  .brand .mark{width:11px;height:34px;background:linear-gradient(var(--oxblood),var(--oxblood-2));border-radius:1px;box-shadow:0 0 18px rgba(140,29,44,0.5)}
  .brand h1{font-family:var(--display);font-weight:600;font-size:1.18rem;letter-spacing:0.14em;text-transform:uppercase}
  .brand .sub{font-family:var(--mono);font-size:0.66rem;letter-spacing:0.26em;color:var(--bone-dim);text-transform:uppercase;margin-top:2px}
  .runmeta{font-family:var(--mono);font-size:0.7rem;color:var(--bone-dim);text-align:right;letter-spacing:0.04em;line-height:1.7}
  .runmeta b{color:var(--bone);font-weight:600}
  .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:22px}
  .tile{background:var(--ink-2);border:1px solid var(--line);border-radius:7px;padding:13px 14px;position:relative;overflow:hidden;cursor:pointer;text-align:left;color:inherit;font-family:inherit}
  .tile .n{font-family:var(--display);font-weight:600;font-size:1.85rem;line-height:1}
  .tile .l{font-family:var(--mono);font-size:0.6rem;letter-spacing:0.18em;text-transform:uppercase;color:var(--bone-dim);margin-top:7px}
  .tile.act{border-color:rgba(192,39,58,0.45)} .tile.act .n{color:var(--fort)}
  .tile.act::after{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--fort)}
  .tile.wat .n{color:var(--watch)} .tile.low .n{color:var(--bone-dim)}
  .tile[aria-pressed="true"]{outline:2px solid var(--bone-dim);outline-offset:-1px}
  section.zones{margin-top:26px}
  .eyebrow{font-family:var(--mono);font-size:0.64rem;letter-spacing:0.24em;text-transform:uppercase;color:var(--bone-dim);margin-bottom:12px;display:flex;align-items:center;gap:10px}
  .eyebrow::after{content:"";flex:1;height:1px;background:var(--line-2)}
  .zonegrid{display:grid;grid-template-columns:repeat(6,1fr);gap:9px}
  .zone{background:var(--ink-2);border:1px solid var(--line);border-radius:7px;padding:12px 11px;cursor:pointer;transition:border-color .15s,background .15s;text-align:left;color:inherit;font-family:inherit}
  .zone:hover{border-color:rgba(236,228,218,0.3)}
  .zone[aria-pressed="true"]{border-color:var(--fort);background:var(--fort-soft)}
  .zone .zn{font-family:var(--display);font-weight:500;font-size:0.82rem;line-height:1.2;min-height:2.1em}
  .zone .zc{font-family:var(--mono);font-size:1.5rem;font-weight:600;margin-top:8px;line-height:1}
  .zone .zl{font-family:var(--mono);font-size:0.56rem;letter-spacing:0.14em;text-transform:uppercase;color:var(--bone-dim);margin-top:3px}
  .zbar{height:3px;border-radius:2px;margin-top:9px;background:var(--low)}
  .zone[data-int="3"] .zbar{background:var(--fort)}
  .zone[data-int="2"] .zbar{background:var(--oxblood-2)}
  .zone[data-int="1"] .zbar{background:var(--watch)}
  .controls{display:flex;gap:10px;align-items:center;margin:26px 0 16px;flex-wrap:wrap}
  .search{flex:1;min-width:200px;display:flex;align-items:center;gap:9px;background:var(--ink-2);border:1px solid var(--line);border-radius:7px;padding:9px 13px}
  .search input{flex:1;background:none;border:none;color:var(--bone);font-family:var(--body);font-size:0.9rem;outline:none}
  .search input::placeholder{color:var(--bone-faint)}
  .search svg{flex-shrink:0;stroke:var(--bone-dim)}
  .seg{display:flex;border:1px solid var(--line);border-radius:7px;overflow:hidden}
  .seg button{background:var(--ink-2);border:none;color:var(--bone-dim);font-family:var(--mono);font-size:0.66rem;letter-spacing:0.1em;text-transform:uppercase;padding:9px 13px;cursor:pointer;transition:.15s}
  .seg button+button{border-left:1px solid var(--line)}
  .seg button[aria-pressed="true"]{background:var(--oxblood);color:var(--bone)}
  .clearz{font-family:var(--mono);font-size:0.66rem;letter-spacing:0.08em;color:var(--bone-dim);background:none;border:none;cursor:pointer;text-decoration:underline;text-underline-offset:3px;display:none}
  .clearz.on{display:inline}
  .count{font-family:var(--mono);font-size:0.7rem;color:var(--bone-dim);letter-spacing:0.06em;margin-bottom:14px}
  /* Onglets periode (mois) */
  .period{display:flex;gap:7px;flex-wrap:wrap;margin:22px 0 4px;align-items:center}
  .period .chip{background:var(--ink-2);border:1px solid var(--line);border-radius:20px;padding:7px 14px;cursor:pointer;color:var(--bone-dim);font-family:var(--mono);font-size:0.66rem;letter-spacing:0.06em;transition:.15s;white-space:nowrap}
  .period .chip:hover{border-color:rgba(236,228,218,0.3);color:var(--bone)}
  .period .chip[aria-pressed="true"]{background:var(--oxblood);border-color:var(--oxblood);color:var(--bone)}
  /* Badge statut de suivi (CRM) */
  .statut{font-family:var(--mono);font-size:0.56rem;letter-spacing:0.1em;text-transform:uppercase;padding:3px 7px;border-radius:4px;border:1px solid var(--line);color:var(--bone-dim)}
  .statut.contacte{border-color:rgba(200,137,59,0.5);color:#dcb079}
  .statut.gagne{border-color:rgba(95,160,110,0.6);color:#86c596}
  .statut.perdu{border-color:rgba(150,150,150,0.4);color:var(--bone-faint)}
  .statut.relance{border-color:rgba(192,39,58,0.5);color:#e08e98}
  .datedet{font-family:var(--mono);font-size:0.58rem;color:var(--bone-faint);letter-spacing:0.04em}
  .jx{font-family:var(--mono);font-size:0.58rem;letter-spacing:0.08em;font-weight:600;padding:3px 8px;border-radius:4px;border:1px solid var(--line)}
  .jx.urgent{background:rgba(192,39,58,0.18);border-color:rgba(192,39,58,0.6);color:#e88b96}
  .jx.proche{background:var(--watch-soft);border-color:rgba(200,137,59,0.5);color:#dcb079}
  .jx.large{border-color:var(--line-2);color:var(--bone-dim)}
  .jx.clos{border-color:rgba(150,150,150,0.35);color:var(--bone-faint)}
  .leads{display:grid;grid-template-columns:repeat(2,1fr);gap:13px}
  .lead{background:var(--ink-2);border:1px solid var(--line);border-radius:9px;overflow:hidden;position:relative;display:flex;flex-direction:column}
  .lead .spine{position:absolute;left:0;top:0;bottom:0;width:4px}
  .lead[data-tier="contacter"] .spine{background:var(--fort)}
  .lead[data-tier="surveiller"] .spine{background:var(--watch)}
  .lead[data-tier="ignorer"] .spine{background:var(--low)}
  .lead .body{padding:15px 16px 14px 19px}
  .lhead{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}
  .lmeta{font-family:var(--mono);font-size:0.62rem;letter-spacing:0.12em;text-transform:uppercase;color:var(--bone-dim);display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  .src{padding:2px 6px;border-radius:3px;border:1px solid var(--line);color:var(--bone-dim)}
  .src.bm{border-color:rgba(192,39,58,0.4);color:#d98a96}
  .src.ted{border-color:rgba(200,137,59,0.4);color:#dcb079}
  .pays{color:var(--bone);font-weight:600}
  .scorebox{text-align:right;flex-shrink:0}
  .scorebox .sf{font-family:var(--display);font-weight:700;font-size:1.5rem;line-height:1}
  .lead[data-tier="contacter"] .sf{color:var(--fort)}
  .lead[data-tier="surveiller"] .sf{color:var(--watch)}
  .lead[data-tier="ignorer"] .sf{color:var(--bone-dim)}
  .scorebox .sd{font-family:var(--mono);font-size:0.58rem;color:var(--bone-dim);letter-spacing:0.06em;margin-top:3px;white-space:nowrap}
  .ltitle{font-family:var(--display);font-weight:500;font-size:1.02rem;line-height:1.3;margin:11px 0 12px;color:var(--bone)}
  .badges{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:13px}
  .badge{font-family:var(--mono);font-size:0.58rem;letter-spacing:0.1em;text-transform:uppercase;padding:4px 8px;border-radius:4px;display:inline-flex;align-items:center;gap:5px}
  .badge.win-immediate{background:var(--fort-soft);color:#e08e98}
  .badge.win-court_terme{background:var(--watch-soft);color:#dcb079}
  .badge.win-indetermine{background:var(--low-soft);color:var(--bone-dim)}
  .badge.ecart{background:transparent;border:1px solid rgba(200,137,59,0.5);color:#dcb079}
  .contact{border-top:1px solid var(--line-2);padding-top:12px;margin-top:2px}
  .contact .row{display:flex;gap:8px;font-size:0.8rem;margin-bottom:5px;align-items:baseline}
  .contact .k{font-family:var(--mono);font-size:0.58rem;letter-spacing:0.1em;text-transform:uppercase;color:var(--bone-faint);min-width:62px;flex-shrink:0;padding-top:2px}
  .contact .v{color:var(--bone);word-break:break-word}
  .contact .v a{color:#d98a96;text-decoration:none}
  .contact .v a:hover{text-decoration:underline}
  .cible{background:rgba(111,14,39,0.16);border-left:2px solid var(--oxblood-2);padding:9px 11px;border-radius:0 5px 5px 0;font-size:0.78rem;color:var(--bone-dim);margin-top:11px;line-height:1.45}
  .cible b{color:var(--bone);font-weight:600}
  details.just{margin-top:11px;border-top:1px solid var(--line-2);padding-top:10px}
  details.just summary{font-family:var(--mono);font-size:0.62rem;letter-spacing:0.12em;text-transform:uppercase;color:var(--bone-dim);cursor:pointer;list-style:none;display:flex;align-items:center;gap:7px;user-select:none}
  details.just summary::-webkit-details-marker{display:none}
  details.just summary .chev{transition:transform .2s;display:inline-block}
  details.just[open] summary .chev{transform:rotate(90deg)}
  details.just p{font-size:0.83rem;color:var(--bone-dim);line-height:1.55;margin-top:10px}
  .lead .foot{margin-top:auto;border-top:1px solid var(--line-2);padding:10px 16px 11px 19px;display:flex;justify-content:space-between;align-items:center;gap:10px}
  .lead .foot a.av{font-family:var(--mono);font-size:0.62rem;letter-spacing:0.08em;color:var(--bone-dim);text-decoration:none}
  .lead .foot a.av:hover{color:var(--bone)}
  .lead .foot .grp{font-family:var(--mono);font-size:0.58rem;letter-spacing:0.12em;color:var(--bone-faint)}
  .empty{grid-column:1/-1;text-align:center;padding:50px 20px;color:var(--bone-dim);font-family:var(--mono);font-size:0.8rem;letter-spacing:0.06em}
  footer{margin-top:34px;padding-top:18px;border-top:1px solid var(--line);font-family:var(--mono);font-size:0.64rem;color:var(--bone-faint);letter-spacing:0.06em;line-height:1.8}
  @media(max-width:860px){.stats{grid-template-columns:repeat(2,1fr)}.zonegrid{grid-template-columns:repeat(2,1fr)}.leads{grid-template-columns:1fr}.runmeta{text-align:left}}
  @media(prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
  :focus-visible{outline:2px solid var(--watch);outline-offset:2px}

  /* Synthese executive */
  .exec{margin-top:18px;padding:15px 18px;background:linear-gradient(100deg,rgba(111,14,39,0.22),rgba(31,23,21,0.4));border:1px solid rgba(140,29,44,0.35);border-radius:9px;font-size:0.96rem;line-height:1.55;color:var(--bone)}
  .exec b{color:#e8a0ab;font-weight:600}
  .exec .lead-strong{font-family:var(--display);font-weight:500}
  /* Bloc carte + graphique cote a cote */
  .geo{display:grid;grid-template-columns:1.7fr 1fr;gap:13px;margin-top:24px}
  .panel{background:var(--ink-2);border:1px solid var(--line);border-radius:10px;overflow:hidden}
  .panel .phead{font-family:var(--mono);font-size:0.62rem;letter-spacing:0.22em;text-transform:uppercase;color:var(--bone-dim);padding:13px 16px 0}
  #map{height:340px;width:100%;margin-top:10px;background:#11100f;border-bottom-left-radius:10px;border-bottom-right-radius:10px}
  .leaflet-container{background:#13110f!important;font-family:var(--mono)!important}
  .leaflet-popup-content-wrapper{background:var(--ink-3);color:var(--bone);border-radius:7px;box-shadow:0 6px 24px rgba(0,0,0,0.5)}
  .leaflet-popup-tip{background:var(--ink-3)}
  .leaflet-popup-content{margin:11px 13px;font-family:var(--body)}
  .leaflet-popup-content b{font-family:var(--display)}
  .leaflet-control-attribution{background:rgba(20,16,15,0.7)!important;color:var(--bone-faint)!important}
  .leaflet-control-attribution a{color:var(--bone-dim)!important}
  .leaflet-bar a{background:var(--ink-3)!important;color:var(--bone)!important;border-color:var(--line)!important}
  /* Graphique repartition par zone */
  .zonechart{padding:14px 16px 16px;display:flex;flex-direction:column;gap:9px}
  .zrow{cursor:pointer}
  .zrow .zlab{display:flex;justify-content:space-between;font-family:var(--mono);font-size:0.66rem;letter-spacing:0.04em;color:var(--bone-dim);margin-bottom:4px}
  .zrow:hover .zlab{color:var(--bone)}
  .zrow[aria-pressed="true"] .zlab{color:var(--fort)}
  .ztrack{height:8px;background:var(--ink-3);border-radius:5px;overflow:hidden}
  .zfill{height:100%;border-radius:5px;background:linear-gradient(90deg,var(--oxblood),var(--fort));transition:width .4s ease}
  .zrow[aria-pressed="true"] .zfill{background:var(--fort)}
  /* Entree progressive */
  @keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
  .lead{animation:rise .35s ease both}
  .panel,.exec,.stats .tile{animation:rise .4s ease both}
  @media(max-width:860px){.geo{grid-template-columns:1fr}#map{height:280px}}
  /* --- Fiche lead (modale) + actions --- */
  .foot .footacts{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  .foot .act{cursor:pointer;border:1px solid var(--line);background:transparent;color:var(--bone-dim);
    font:inherit;font-size:12px;padding:4px 10px;border-radius:6px;text-decoration:none;transition:.15s}
  .foot .act:hover{border-color:var(--oxblood);color:var(--bone)}
  .foot .act.mail{border-color:var(--oxblood);color:var(--fort)}
  .foot .act.mail:hover{background:var(--oxblood);color:#fff}
  .modal-ov{position:fixed;inset:0;background:rgba(10,6,8,.72);backdrop-filter:blur(3px);
    display:none;align-items:flex-start;justify-content:center;z-index:1000;padding:40px 16px;overflow:auto}
  .modal-ov.open{display:flex}
  .modal{background:var(--panel);border:1px solid var(--line);border-radius:12px;max-width:640px;width:100%;
    box-shadow:0 24px 80px rgba(0,0,0,.6);animation:pop .18s ease}
  @keyframes pop{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
  .modal .mhead{padding:20px 22px 14px;border-bottom:1px solid var(--line);position:relative}
  .modal .mclose{position:absolute;top:14px;right:16px;background:none;border:none;color:var(--bone-dim);
    font-size:22px;cursor:pointer;line-height:1}
  .modal .mclose:hover{color:var(--bone)}
  .modal .msrc{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--fort)}
  .modal h2{font-size:17px;margin:6px 0 0;color:var(--bone);padding-right:24px}
  .modal .mscore{display:flex;gap:18px;margin-top:12px;align-items:baseline}
  .modal .mscore .big{font-size:30px;font-weight:700;color:var(--bone)}
  .modal .mscore .sub{font-size:12px;color:var(--bone-dim)}
  .modal .mbody{padding:16px 22px}
  .modal .frow{display:flex;gap:12px;padding:7px 0;border-bottom:1px solid rgba(255,255,255,.05);font-size:13px}
  .modal .fk{flex:0 0 130px;color:var(--bone-dim)}
  .modal .fv{flex:1;color:var(--bone)}
  .modal .fv a{color:var(--fort)}
  .modal .mactions{display:flex;gap:10px;padding:16px 22px 22px;flex-wrap:wrap}
  .modal .mbtn{flex:1;min-width:160px;text-align:center;padding:11px 14px;border-radius:8px;font:inherit;
    font-size:13px;font-weight:600;cursor:pointer;text-decoration:none;border:1px solid var(--line);
    background:transparent;color:var(--bone)}
  .modal .mbtn.primary{background:var(--oxblood);border-color:var(--oxblood);color:#fff}
  .modal .mbtn.primary:hover{background:var(--fort)}
  .modal .mbtn.ghost:hover{border-color:var(--oxblood);color:var(--fort)}
  /* --- Comptes chauds (BITD) + timeline --- */
  #comptes{margin:0 0 18px}
  .chead{display:flex;align-items:baseline;gap:10px;margin-bottom:10px;flex-wrap:wrap}
  .chead b{color:var(--bone);font-size:14px}
  .csub{color:var(--bone-dim);font-size:11px}
  .cgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px}
  .ccard{text-align:left;cursor:pointer;background:var(--panel);border:1px solid var(--line);
    border-radius:9px;padding:11px 12px;color:var(--bone);font:inherit;transition:.15s}
  .ccard:hover{border-color:var(--oxblood);transform:translateY(-1px)}
  .ccard.t-chaud{border-left:3px solid var(--fort)}
  .ccard.t-tiede{border-left:3px solid var(--watch)}
  .ccard.t-froid{border-left:3px solid var(--line)}
  .ccard .cn{font-weight:600;font-size:13px;display:flex;gap:6px;align-items:center}
  .ccard .cmeta{color:var(--bone-dim);font-size:11px;margin-top:4px}
  .ccard .cbar{height:4px;background:rgba(255,255,255,.06);border-radius:3px;margin-top:8px;overflow:hidden}
  .ccard .cbar span{display:block;height:100%;background:var(--oxblood)}
  .modal .tlhead{color:var(--bone-dim);font-size:12px;text-transform:uppercase;letter-spacing:.06em;margin:14px 0 8px}
  .modal .timeline{display:flex;flex-direction:column;gap:2px}
  .modal .tlrow{display:grid;grid-template-columns:96px 1fr auto 42px;gap:8px;padding:6px 0;
    border-bottom:1px solid rgba(255,255,255,.05);font-size:12px}
  .modal .tlrow .tld{color:var(--bone-dim)}
  .modal .tlrow .tls{color:var(--fort);text-align:right;font-weight:600}
</style>
</head>
<body>
<div class="wrap">
  <header class="mast">
    <div class="mast-top">
      <div class="brand">
        <div class="mark"></div>
        <div><h1>Radar Amarante</h1><div class="sub">Tableau de situation, BU Escorte</div></div>
      </div>
      <div class="runmeta" id="runmeta"></div>
    </div>
    <div class="stats" id="stats"></div>
    <div class="exec" id="exec"></div>
  </header>
  <section class="geo">
    <div class="panel">
      <div class="phead">Carte des opportunités</div>
      <div id="map"></div>
    </div>
    <div class="panel">
      <div class="phead">Répartition par zone</div>
      <div class="zonechart" id="zonechart"></div>
    </div>
  </section>
  <div class="period" id="period"></div>
  <div id="comptes"></div>
  <div class="controls">
    <label class="search">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
      <input type="text" id="search" placeholder="Filtrer par pays, agence, mot-clé..." autocomplete="off">
    </label>
    <div class="seg" id="srcseg" role="group" aria-label="Source">
      <button data-src="all" aria-pressed="true">Toutes</button>
      <button data-src="BM" aria-pressed="false">Banque Mondiale</button>
      <button data-src="TED" aria-pressed="false">TED</button>
      <button data-src="BOAMP" aria-pressed="false">BOAMP</button>
      <button data-src="PRIVÉ" aria-pressed="false">Privé (BITD)</button>
    </div>
    <div class="seg" id="triseg" role="group" aria-label="Tri">
      <button data-tri="score" aria-pressed="true">Importance</button>
      <button data-tri="urgence" aria-pressed="false">Urgence</button>
      <button data-tri="date" aria-pressed="false">Récents</button>
    </div>
    <button class="clearz" id="clearz">Réinitialiser</button>
  </div>
  <div class="count" id="count"></div>
  <div class="leads" id="leads"></div>
  <footer id="foot"></footer>
</div>
<div class="modal-ov" id="modal" role="dialog" aria-modal="true">
  <div class="modal" id="modalcard"></div>
</div>
<script>
const LEADS = __LEADS_JSON__;
const META = __META_JSON__;
const ORDRE_ZONES = ["Afrique de l'Ouest","Sahel","Afrique centrale","Afrique de l'Est","Afrique australe","Afrique du Nord","Proche-Orient","Péninsule arabique","Asie centrale","Asie du Sud","Asie du Sud-Est","Caucase","Balkans","Europe de l'Est","Caraïbes","Amérique latine","Europe de l'Ouest","Outre-mer","Non classé"];
const winLabel={immediate:'Fenêtre immédiate',court_terme:'Court terme',indetermine:'Fenêtre indéterminée'};
const SRC_LABEL={BM:'Banque Mondiale',TED:'TED',BOAMP:'BOAMP','PRIVÉ':'Privé · BITD'};
let AFFICHES=[];

// Pays francophones (choix de la langue du mail). Les entreprises BITD et les
// acheteurs BOAMP sont francophones par nature.
const FRANCO=['france','mali','niger','tchad','senegal','ivoire','burkina','benin','togo',
'guinee','cameroun','gabon','congo','rdc','centrafrique','djibouti','madagascar','maroc','algerie',
'tunisie','mauritanie','liban','haiti','belgique','suisse','luxembourg','monaco','comores','burundi',
'rwanda','seychelles','vanuatu'];
function sansAccent(s){return String(s||'').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'');}
function langue(l){
  if(l.src==='PRIVÉ'||l.src==='BOAMP')return 'fr';
  // Match par MOT entier (évite "niger" ⊂ "nigeria").
  const p=' '+sansAccent(l.pays).replace(/[^a-z ]/g,' ').replace(/\s+/g,' ').trim()+' ';
  return FRANCO.some(f=>p.includes(' '+f+' '))?'fr':'en';
}

// Brouillon d'email contextualise (pre-rempli, a ajuster avant envoi).
function buildEmail(l){
  const fr=langue(l)==='fr';
  const pays=l.pays||(fr?'la zone concernée':'the area');
  const zone=l.zone&&l.zone!=='Non classé'?l.zone:(fr?'les zones sensibles':'high-risk areas');
  if(l.src==='PRIVÉ'){
    const act=l.grp&&l.grp!=='signal'?l.grp.replace(/_/g,' '):(fr?'déploiement':'deployment');
    if(fr)return{subject:`Sûreté de vos équipes déployées — ${pays}`,
      body:`Madame, Monsieur,\n\nNous suivons le développement de ${l.agence} à l'international. Un déploiement de vos équipes en ${pays} (${act}) peut impliquer des enjeux de sûreté pour les personnels sur place.\n\nAmarante International sécurise les collaborateurs de la BITD en environnement sensible : protection rapprochée, escorte, chauffeurs de sécurité, évaluation de sites et conseil sûreté.\n\nSi le sujet est d'actualité pour vos prochaines missions, je serais ravi d'en échanger quelques minutes.\n\nBien cordialement,\n[Votre nom]\nAmarante International`};
    return{subject:`Security for your deployed teams — ${pays}`,
      body:`Dear Sir or Madam,\n\nWe are following ${l.agence}'s international development. Deploying your teams to ${pays} (${act}) may raise security considerations for your personnel on the ground.\n\nAmarante International protects defence-industry staff in complex environments: close protection, secure transport, site assessment and security advisory.\n\nIf this is relevant to your upcoming missions, I would welcome a brief call.\n\nKind regards,\n[Your name]\nAmarante International`};
  }
  // Marches publics (TED / BM / BOAMP)
  const titre=l.titre||(fr?'votre projet':'your project');
  if(fr)return{subject:`Sûreté des équipes — ${pays}`,
    body:`Madame, Monsieur,\n\nJe me permets de vous contacter au sujet de « ${titre} », en ${pays}.\n\nAmarante International accompagne les organisations déployant du personnel en environnement complexe : protection rapprochée, escorte sécurisée, chauffeurs de sécurité et conseil sûreté. Nous intervenons régulièrement en ${zone}.\n\nSi la sûreté des équipes mobilisées sur ce projet est un sujet, je serais ravi d'échanger quelques minutes.\n\nBien cordialement,\n[Votre nom]\nAmarante International`};
  return{subject:`Security for deployed teams — ${pays}`,
    body:`Dear Sir or Madam,\n\nI am reaching out regarding "${titre}", in ${pays}.\n\nAmarante International supports organisations deploying staff in complex environments: close protection, secure transport and security advisory. We operate regularly across ${zone}.\n\nIf the safety of the teams involved in this project is a consideration, I would welcome a brief call.\n\nKind regards,\n[Your name]\nAmarante International`};
}
function mailtoHref(l){
  const e=buildEmail(l);
  const to=(l.email&&l.email!=='n.c.')?encodeURIComponent(l.email):'';
  return `mailto:${to}?subject=${encodeURIComponent(e.subject)}&body=${encodeURIComponent(e.body)}`;
}
let state={zone:null,src:'all',q:'',action:'contacter',mois:null,tri:'score'};

// Filtre commun. `ignore` permet de compter en ignorant un critere donne
// (ex: compter les zones sans s'auto-filtrer sur la zone selectionnee).
function match(l, ignore){
  ignore = ignore || {};
  if(!ignore.action && state.action!=='all' && l.action!==state.action) return false;
  if(!ignore.mois && state.mois && l.mois!==state.mois) return false;
  if(!ignore.zone && state.zone && l.zone!==state.zone) return false;
  if(!ignore.src && state.src!=='all' && l.src!==state.src) return false;
  if(!ignore.q && state.q){const hay=(l.pays+' '+l.agence+' '+l.titre+' '+l.zone+' '+l.nom).toLowerCase(); if(!hay.includes(state.q)) return false;}
  return true;
}

// Onglets periode (mois). Construits depuis META.mois (du plus recent au plus ancien).
function buildPeriod(){
  const box=document.getElementById('period');
  const chips=[{cle:null,label:'Toute la période'}].concat(META.mois);
  box.innerHTML=chips.map(m=>{
    const c=m.cle===null ? LEADS.length : LEADS.filter(l=>l.mois===m.cle).length;
    const pressed=(state.mois===m.cle)?'true':'false';
    return `<button class="chip" data-mois="${m.cle===null?'':m.cle}" aria-pressed="${pressed}">${m.label} · ${c}</button>`;
  }).join('');
  box.querySelectorAll('.chip').forEach(ch=>ch.addEventListener('click',()=>{
    const v=ch.dataset.mois;
    state.mois = v===''? null : v;
    box.querySelectorAll('.chip').forEach(x=>x.setAttribute('aria-pressed',(x===ch)?'true':'false'));
    render();
  }));
}

document.getElementById('runmeta').innerHTML =
  'Run du <b>'+META.date+'</b><br>'+META.total+' avis analysés<br>Sources, TED + Banque Mondiale';

// Stat tiles (cliquables = filtre par action)
const statsDef=[
  {k:'contacter',cls:'act',n:META.contacter,l:'À contacter'},
  {k:'surveiller',cls:'wat',n:META.surveiller,l:'À surveiller'},
  {k:'ignorer',cls:'low',n:META.ignorer,l:'Faibles'},
  {k:'all',cls:'',n:META.total,l:'Tous les avis'}
];
const statsBox=document.getElementById('stats');
statsBox.innerHTML=statsDef.map(s=>
  `<button class="tile ${s.cls}" data-action="${s.k}" aria-pressed="${s.k===state.action}"><div class="n">${s.n}</div><div class="l">${s.l}</div></button>`).join('');
statsBox.querySelectorAll('.tile').forEach(t=>t.addEventListener('click',()=>{
  state.action=t.dataset.action;
  statsBox.querySelectorAll('.tile').forEach(x=>x.setAttribute('aria-pressed',x===t?'true':'false'));
  render();
}));

// Coordonnees (lat,lng) par pays affiche, pour la carte mondiale.
const COORDS={
 "Mali":[17.6,-3.5],"Niger":[17.6,9.4],"Burkina Faso":[12.2,-1.6],"Tchad":[15.5,18.7],"Mauritanie":[20.3,-10.9],
 "Côte d'Ivoire":[7.5,-5.5],"Nigeria":[9.1,8.7],"Sénégal":[14.5,-14.5],"Ghana":[7.9,-1.0],"Togo":[8.6,0.8],"Bénin":[9.3,2.3],"Guinée":[9.9,-9.7],"Libéria":[6.4,-9.4],
 "RDC":[-4.0,21.8],"Congo-Brazzaville":[-0.7,15.8],"Cameroun":[5.6,12.4],"Centrafrique":[6.6,20.9],"Gabon":[-0.8,11.6],
 "Éthiopie":[9.1,40.5],"Kenya":[0.0,37.9],"Ouganda":[1.4,32.3],"Tanzanie":[-6.4,34.9],"Somalie":[5.2,46.2],"Soudan du Sud":[7.3,30.3],"Rwanda":[-1.9,29.9],"Djibouti":[11.8,42.6],
 "Mozambique":[-18.7,35.5],"Madagascar":[-18.8,46.9],"Afrique du Sud":[-30.6,22.9],"Zambie":[-13.1,27.8],"Zimbabwe":[-19.0,29.2],"Malawi":[-13.3,34.3],"Angola":[-11.2,17.9],"Botswana":[-22.3,24.7],
 "Égypte":[26.8,30.8],"Maroc":[31.8,-7.1],"Tunisie":[33.9,9.6],"Algérie":[28.0,1.7],"Libye":[26.3,17.2],
 "Cisjordanie et Gaza":[31.9,35.2],"Jordanie":[30.6,36.2],"Liban":[33.9,35.9],"Irak":[33.2,43.7],"Yémen":[15.6,48.0],"Turquie":[39.0,35.2],"Oman":[21.5,55.9],
 "Ouzbékistan":[41.4,64.6],"Tadjikistan":[38.9,71.3],"Kirghizistan":[41.2,74.8],"Kazakhstan":[48.0,66.9],
 "Bangladesh":[23.7,90.4],"Pakistan":[30.4,69.3],"Inde":[22.4,78.9],"Népal":[28.4,84.1],"Indonésie":[-2.5,118.0],"Philippines":[12.9,121.8],
 "Ukraine":[48.4,31.2],"Moldavie":[47.2,28.5],"Albanie":[41.2,20.0],"Macédoine du Nord":[41.6,21.7],"Serbie":[44.0,21.0],"Géorgie":[42.3,43.4],"Arménie":[40.1,45.0],"Azerbaïdjan":[40.1,47.6],
 "Haïti":[19.0,-72.3],"Jamaïque":[18.1,-77.3],"Mexique":[23.6,-102.6],"Équateur":[-1.8,-78.2],"Brésil":[-10.3,-53.2],"Colombie":[4.6,-74.3],
 "France":[46.6,2.2],"Allemagne":[51.2,10.4],"Danemark":[56.0,9.5],"Nouvelle-Calédonie":[-21.3,165.5]
};
const TIER_COULEUR={contacter:'#C0273A',surveiller:'#C8893B',ignorer:'#5C6670'};
const TIER_RANG={contacter:3,surveiller:2,ignorer:1};

// Synthese executive
function buildExec(){
  const aContacter=LEADS.filter(l=>l.action==='contacter');
  const zonesRisque=new Set(aContacter.map(l=>l.zone)).size;
  const immediat=aContacter.filter(l=>l.win==='immediate').length;
  const avecContact=aContacter.filter(l=>l.email!=='n.c.').length;
  document.getElementById('exec').innerHTML=
    `<span class="lead-strong">${aContacter.length} opportunités prioritaires</span> détectées dans `+
    `<b>${zonesRisque} zones</b> à enjeu sûreté, dont <b>${immediat}</b> en fenêtre immédiate. `+
    `<b>${avecContact}</b> disposent d'un contact direct identifié, en amont de la concurrence.`;
}

// Graphique repartition par zone (respecte les filtres sauf la zone)
function buildZoneChart(){
  const counts={};
  LEADS.forEach(l=>{ if(match(l,{zone:true})){counts[l.zone]=(counts[l.zone]||0)+1;} });
  const zones=ORDRE_ZONES.filter(z=>counts[z]);
  const maxZ=Math.max(1,...zones.map(z=>counts[z]));
  const box=document.getElementById('zonechart');
  if(!zones.length){box.innerHTML='<div class="count">Aucune zone pour ce filtre.</div>';return;}
  box.innerHTML=zones.map(z=>{
    const c=counts[z], pct=Math.round(c/maxZ*100);
    const pressed=z===state.zone?'true':'false';
    return `<div class="zrow" data-zone="${z}" role="button" tabindex="0" aria-pressed="${pressed}"><div class="zlab"><span>${z}</span><span>${c}</span></div><div class="ztrack"><div class="zfill" style="width:${pct}%"></div></div></div>`;
  }).join('');
  box.querySelectorAll('.zrow').forEach(el=>{
    const choisir=()=>{ state.zone=state.zone===el.dataset.zone?null:el.dataset.zone;
      document.getElementById('clearz').classList.toggle('on',!!(state.zone||state.mois)); render(); };
    el.addEventListener('click',choisir);
    el.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();choisir();}});
  });
}

// Carte mondiale (Leaflet). Un marqueur agrege par pays.
let _map=null, _layer=null;
function initMap(){
  if(typeof L==='undefined'){document.getElementById('map').innerHTML='<div class="count" style="padding:20px">Carte indisponible (connexion requise).</div>';return;}
  _map=L.map('map',{worldCopyJump:true,minZoom:1,attributionControl:true}).setView([18,18],2);
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',{
    attribution:'&copy; OpenStreetMap &copy; CARTO',subdomains:'abcd',maxZoom:8
  }).addTo(_map);
  _layer=L.layerGroup().addTo(_map);
}
function updateMap(filtered){
  if(!_map||!_layer)return;
  _layer.clearLayers();
  const parPays={};
  filtered.forEach(l=>{ if(!COORDS[l.pays])return; (parPays[l.pays]=parPays[l.pays]||[]).push(l); });
  Object.entries(parPays).forEach(([pays,arr])=>{
    const meilleur=arr.reduce((a,b)=>TIER_RANG[b.action]>TIER_RANG[a.action]?b:a);
    const couleur=TIER_COULEUR[meilleur.action]||'#5C6670';
    const r=6+Math.min(14,arr.length*2);
    const top=arr.slice().sort((a,b)=>b.final-a.final)[0];
    const m=L.circleMarker(COORDS[pays],{radius:r,color:couleur,weight:1.5,fillColor:couleur,fillOpacity:0.55});
    m.bindPopup(`<b>${pays}</b><br>${arr.length} avis · meilleur score ${top.final.toFixed(1)}<br><span style="color:#A99E92;font-size:0.85em">${top.titre.slice(0,70)}</span>`);
    m.on('click',()=>{ const inp=document.getElementById('search'); inp.value=pays; state.q=pays.toLowerCase(); document.getElementById('clearz').classList.add('on'); render(); });
    m.addTo(_layer);
  });
}
document.getElementById('clearz').addEventListener('click',()=>{
  state.zone=null; state.mois=null;
  document.getElementById('clearz').classList.remove('on');
  buildPeriod(); buildZoneChart(); render();
});
document.querySelectorAll('#srcseg button').forEach(b=>b.addEventListener('click',()=>{
  state.src=b.dataset.src;
  document.querySelectorAll('#srcseg button').forEach(x=>x.setAttribute('aria-pressed',x===b?'true':'false'));
  render();
}));
document.querySelectorAll('#triseg button').forEach(b=>b.addEventListener('click',()=>{
  state.tri=b.dataset.tri;
  document.querySelectorAll('#triseg button').forEach(x=>x.setAttribute('aria-pressed',x===b?'true':'false'));
  render();
}));
document.getElementById('search').addEventListener('input',e=>{state.q=e.target.value.toLowerCase().trim();render();});

const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

// Jours restants avant la date limite. Renvoie un entier (peut etre negatif si
// cloture) ou null si pas de date exploitable.
function joursRestants(s){
  s=(s||'').trim(); if(!s)return null;
  let d=null;
  let m=s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if(m){ d=new Date(+m[1],+m[2]-1,+m[3]); }
  else{ let m2=s.match(/^(\d{2})\/(\d{2})\/(\d{4})/); if(m2){ d=new Date(+m2[3],+m2[2]-1,+m2[1]); } }
  if(!d||isNaN(d))return null;
  const auj=new Date(); auj.setHours(0,0,0,0);
  return Math.round((d-auj)/86400000);
}
function badgeDeadline(l){
  const jr=joursRestants(l.deadline);
  if(jr===null)return '';
  if(jr<0)return '<span class="jx clos">clôturé</span>';
  if(jr===0)return '<span class="jx urgent">clôt. aujourd\'hui</span>';
  const cls=jr<=7?'urgent':(jr<=30?'proche':'large');
  return `<span class="jx ${cls}">J-${jr}</span>`;
}

function render(){
  buildZoneChart();
  const box=document.getElementById('leads');
  let filtered=LEADS.filter(l=>match(l));
  if(state.tri==='date'){
    filtered.sort((a,b)=> (b.date_det||'').localeCompare(a.date_det||'') || b.final-a.final);
  }else if(state.tri==='urgence'){
    filtered.sort((a,b)=>{
      const ja=joursRestants(a.deadline), jb=joursRestants(b.deadline);
      const oa=(ja!==null&&ja>=0), ob=(jb!==null&&jb>=0);
      if(oa&&ob)return ja-jb;            // deux ouverts : le plus proche d'abord
      if(oa!==ob)return oa?-1:1;         // un ouvert passe avant un fermé/sans date
      return b.final-a.final;            // sinon, par score
    });
  }else{
    filtered.sort((a,b)=> b.final-a.final);
  }
  updateMap(filtered);
  const moisLabel = state.mois ? (META.mois.find(m=>m.cle===state.mois)||{}).label : null;
  document.getElementById('count').textContent=
    `${filtered.length} avis affiché${filtered.length>1?'s':''}`+
    (moisLabel?`, ${moisLabel}`:'')+
    (state.zone?`, zone ${state.zone}`:'')+(state.src!=='all'?`, source ${state.src}`:'');
  if(!filtered.length){box.innerHTML='<div class="empty">Aucun avis ne correspond à ce filtre.</div>';return;}
  AFFICHES=filtered;
  box.innerHTML=filtered.map((l,i)=>{
    const tier=l.action;
    const win=(['immediate','court_terme','indetermine'].includes(l.win))?l.win:'indetermine';
    const mail=l.email!=='n.c.'?`<a href="mailto:${esc(l.email)}">${esc(l.email)}</a>`:'n.c.';
    const tel=l.tel!=='n.c.'?`<a href="tel:${esc(l.tel.replace(/\s/g,''))}">${esc(l.tel)}</a>`:'n.c.';
    const ecart=l.ecart?`<span class="badge ecart" title="Écart d'évaluation entre les deux passes, lire la justification">⚠ écart</span>`:'';
    const stKey=(l.statut||'nouveau').toLowerCase();
    const stCls=stKey.includes('gagn')?'gagne':stKey.includes('perd')?'perdu':stKey.includes('contact')?'contacte':stKey.includes('relanc')?'relance':'';
    const statut=(stKey!=='nouveau')?`<span class="statut ${stCls}">${esc(l.statut)}</span>`:'';
    const dateChip=l.mois_label&&l.mois_label!=='Sans date'?`<span class="datedet">détecté ${esc(l.mois_label)}</span>`:'';
    const hasContact=(l.nom&&l.nom!=='n.c.')||(l.email&&l.email!=='n.c.')||(l.tel&&l.tel!=='n.c.');
    const contactRows = hasContact ? `
          <div class="row"><span class="k">Contact</span><span class="v">${esc(l.nom)}</span></div>
          <div class="row"><span class="k">Email</span><span class="v">${mail}</span></div>
          <div class="row"><span class="k">Tél</span><span class="v">${tel}</span></div>` : '';
    return `<article class="lead" data-tier="${tier}" data-idx="${i}"><span class="spine"></span><div class="body">
      <div class="lhead"><div class="lmeta"><span class="src ${l.src.toLowerCase()}">${SRC_LABEL[l.src]||l.src}</span><span class="pays">${esc(l.pays)}</span><span>· ${esc(l.zone)}</span></div>
      <div class="scorebox"><div class="sf">${l.final.toFixed(1)}</div><div class="sd">sûreté ${l.surete.toFixed(1)} · com ${l.comm.toFixed(1)}</div></div></div>
      <h3 class="ltitle">${esc(l.titre)}</h3>
      <div class="badges"><span class="badge win-${win}">${winLabel[win]}</span>${badgeDeadline(l)}${ecart}${statut}${dateChip}</div>
      <div class="contact"><div class="row"><span class="k">Agence</span><span class="v">${esc(l.agence)}</span></div>${contactRows}</div>
      <div class="cible"><b>Qui démarcher.</b> ${esc(l.cible)}</div>
      ${l.justif?`<details class="just"><summary><span class="chev">▸</span> Justification sûreté</summary><p>${esc(l.justif)}</p></details>`:''}
      </div><div class="foot"><span class="grp">Groupe ${esc(l.grp)}</span><span class="footacts"><button class="act" type="button" data-fiche="${i}">Fiche ↗</button><a class="act mail" href="${mailtoHref(l)}">✉ Rédiger email</a>${l.lien?`<a class="act" href="${esc(l.lien)}" target="_blank" rel="noopener">Voir l'avis ↗</a>`:''}</span></div></article>`;
  }).join('');
}
document.getElementById('foot').innerHTML=
  'Généré automatiquement après le run du radar. Les contacts proviennent des avis Banque Mondiale.<br>Le destinataire commercial réel est le titulaire du marché, pas l\'agence acheteuse.';

// --- Fiche lead detaillee (modale) ---
function ficheHtml(l){
  const row=(k,v)=>v?`<div class="frow"><span class="fk">${k}</span><span class="fv">${v}</span></div>`:'';
  const mail=(l.email&&l.email!=='n.c.')?`<a href="mailto:${esc(l.email)}">${esc(l.email)}</a>`:'';
  const tel=(l.tel&&l.tel!=='n.c.')?esc(l.tel):'';
  const jr=joursRestants(l.deadline);
  const dl=(jr!==null)?(jr>=0?`${jr} j restants (${esc(l.deadline)})`:`clôturé (${esc(l.deadline)})`):'';
  return `
   <div class="mhead"><button class="mclose" type="button" onclick="closeFiche()" aria-label="Fermer">×</button>
     <div class="msrc">${SRC_LABEL[l.src]||l.src}</div>
     <h2>${esc(l.titre)}</h2>
     <div class="mscore"><span class="big">${l.final.toFixed(1)}</span><span class="sub">sûreté ${l.surete.toFixed(1)} · commercial ${l.comm.toFixed(1)}</span></div>
   </div>
   <div class="mbody">
     ${row('Pays',esc(l.pays))}
     ${row('Zone',esc(l.zone))}
     ${row('Action',esc(l.action))}
     ${row('Fenêtre',winLabel[l.win]||esc(l.win))}
     ${row('Échéance',dl)}
     ${row(l.src==='PRIVÉ'?'Activité':'Groupe',esc(l.grp))}
     ${row(l.src==='PRIVÉ'?'Entreprise':'Agence',esc(l.agence))}
     ${row('Contact',(l.nom&&l.nom!=='n.c.')?esc(l.nom):'')}
     ${row('Email',mail)}
     ${row('Tél',tel)}
     ${row('SIREN',esc(l.siren||''))}
     ${row("Chiffre d'affaires",l.ca?esc(l.ca)+' €':'')}
     ${row('Qui démarcher',esc(l.cible))}
     ${row('Justification',esc(l.justif))}
   </div>
   <div class="mactions">
     <a class="mbtn primary" href="${mailtoHref(l)}">✉ Rédiger l'email</a>
     ${l.src==='PRIVÉ'?`<button class="mbtn ghost" type="button" data-compte-link="${esc(l.agence)}">Historique du compte ↗</button>`:''}
     ${l.lien?`<a class="mbtn ghost" href="${esc(l.lien)}" target="_blank" rel="noopener">Voir l'avis ↗</a>`:''}
     <button class="mbtn ghost" type="button" onclick="closeFiche()">Fermer</button>
   </div>`;
}
function openFiche(l){if(!l)return;document.getElementById('modalcard').innerHTML=ficheHtml(l);document.getElementById('modal').classList.add('open');}
function closeFiche(){document.getElementById('modal').classList.remove('open');}
document.getElementById('leads').addEventListener('click',e=>{
  const b=e.target.closest('[data-fiche]');
  if(b){e.preventDefault();openFiche(AFFICHES[+b.getAttribute('data-fiche')]);}
});
document.getElementById('modal').addEventListener('click',e=>{if(e.target.id==='modal')closeFiche();});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeFiche();});

// --- Historique par entreprise + score d'accélération (signaux BITD) ---
function joursDepuis(d){if(!d)return null;const t=Date.parse(d);if(isNaN(t))return null;return Math.floor((Date.now()-t)/86400000);}
function poidsRecence(j){if(j==null)return 0.3;if(j<=30)return 1;if(j<=90)return 0.6;if(j<=180)return 0.3;return 0.1;}
function agregerComptes(){
  const parNom={};
  LEADS.filter(l=>l.src==='PRIVÉ').forEach(l=>{
    const k=l.agence||'?';
    (parNom[k]=parNom[k]||{nom:k,signaux:[],pays:new Set(),act:new Set()});
    parNom[k].signaux.push(l);
    if(l.pays)parNom[k].pays.add(l.pays);
    if(l.grp&&l.grp!=='signal')parNom[k].act.add(l.grp.replace(/_/g,' '));
  });
  const comptes=Object.values(parNom).map(c=>{
    let accel=0,recents=0,dernier=null;
    c.signaux.forEach(s=>{
      const j=joursDepuis(s.date_det);
      accel+=(s.final/10)*poidsRecence(j);
      if(j!=null&&j<=90)recents++;
      if(s.date_det&&(!dernier||s.date_det>dernier))dernier=s.date_det;
    });
    if(recents>=3)accel*=1.2;                 // momentum : rafale de signaux
    accel=Math.round(accel*100)/100;
    const tier=accel>=1.5?'chaud':accel>=0.7?'tiede':'froid';
    return Object.assign(c,{n:c.signaux.length,recents,dernier,accel,tier,
      pays:[...c.pays],act:[...c.act]});
  });
  comptes.sort((a,b)=>b.accel-a.accel);
  return comptes;
}
function buildComptes(){
  const box=document.getElementById('comptes');
  const comptes=agregerComptes().filter(c=>c.accel>0);
  if(!comptes.length){box.style.display='none';return;}
  box.style.display='';
  const icon={chaud:'🔥',tiede:'🟠',froid:'·'};
  box.innerHTML=`<div class="chead"><b>Comptes chauds · BITD</b><span class="csub">accélération = signaux récents pondérés par le score. Cliquer pour l'historique.</span></div><div class="cgrid">`+
    comptes.slice(0,8).map(c=>`<button class="ccard t-${c.tier}" type="button" data-compte="${esc(c.nom)}">
      <div class="cn"><span>${icon[c.tier]}</span>${esc(c.nom)}</div>
      <div class="cmeta">${c.n} signal${c.n>1?'s':''}${c.recents?` · ${c.recents} récent${c.recents>1?'s':''}`:''} · ${c.pays.length} pays</div>
      <div class="cbar"><span style="width:${Math.min(100,Math.round(c.accel/2*100))}%"></span></div></button>`).join('')+`</div>`;
}
function openCompte(nom){
  const c=agregerComptes().find(x=>x.nom===nom);if(!c)return;
  const etiq={chaud:'🔥 Compte chaud',tiede:'🟠 Tiède',froid:'· Froid'};
  const sigs=c.signaux.slice().sort((a,b)=>(b.date_det||'').localeCompare(a.date_det||''));
  const tl=sigs.map(s=>`<div class="tlrow"><span class="tld">${esc(s.date_det||'—')}</span><span>${esc(s.pays)}</span><span>${esc((s.grp||'').replace(/_/g,' '))}</span><span class="tls">${s.final.toFixed(1)}</span></div>`).join('');
  document.getElementById('modalcard').innerHTML=`
   <div class="mhead"><button class="mclose" type="button" onclick="closeFiche()" aria-label="Fermer">×</button>
     <div class="msrc">${etiq[c.tier]} · accélération ${c.accel}</div>
     <h2>${esc(c.nom)}</h2>
     <div class="mscore"><span class="big">${c.n}</span><span class="sub">signaux · ${c.recents} récent(s) · ${c.pays.length} pays</span></div>
   </div>
   <div class="mbody">
     ${c.pays.length?`<div class="frow"><span class="fk">Pays</span><span class="fv">${c.pays.map(esc).join(', ')}</span></div>`:''}
     ${c.act.length?`<div class="frow"><span class="fk">Activités</span><span class="fv">${c.act.map(esc).join(', ')}</span></div>`:''}
     <div class="frow"><span class="fk">Dernier signal</span><span class="fv">${esc(c.dernier||'—')}</span></div>
     <div class="tlhead">Historique des signaux</div>
     <div class="timeline">${tl}</div>
   </div>
   <div class="mactions"><button class="mbtn ghost" type="button" onclick="closeFiche()">Fermer</button></div>`;
  document.getElementById('modal').classList.add('open');
}
document.getElementById('comptes').addEventListener('click',e=>{
  const b=e.target.closest('[data-compte]');
  if(b)openCompte(b.getAttribute('data-compte'));
});
document.getElementById('modalcard').addEventListener('click',e=>{
  const b=e.target.closest('[data-compte-link]');
  if(b){closeFiche();openCompte(b.getAttribute('data-compte-link'));}
});

buildExec();
initMap();
buildPeriod();
buildComptes();
render();
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
