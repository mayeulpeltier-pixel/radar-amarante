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
    brut = (brut or "").strip()
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


def _vrai(v):
    """Interprete une valeur de cellule comme booleen (divergence, securite)."""
    return str(v).strip().lower() in ("true", "vrai", "1", "oui", "yes")


def _num(v, defaut=0.0):
    try:
        return float(str(v).replace(",", ".").strip())
    except (ValueError, AttributeError):
        return defaut


def ligne_vers_lead(row, source):
    """Transforme une ligne de Sheet (dict par nom de colonne) en lead unifie."""
    pays_brut = row.get("pays_execution", "")
    nom_pays, zone = resoudre_pays(pays_brut, source)
    action = (row.get("action_recommandee", "") or "").strip().lower()

    if source == "BM":
        cible = row.get("cible_commerciale_reelle", "") or \
            "Titulaire du marché qui déploie les équipes, pas l'agence acheteuse."
        groupe = (row.get("procurement_group", "") or "").strip() or "n.c."
    else:
        cible = "Bureau ou consortium titulaire du marché, pas le bailleur."
        groupe = "AT"

    return {
        "src": source,
        "pays": nom_pays,
        "zone": zone,
        "titre": (row.get("titre", "") or "").strip(),
        "agence": (row.get("acheteur", "") or "").strip() or "n.c.",
        "final": round(_num(row.get("score_final")), 1),
        "surete": round(_num(row.get("score_surete")), 1),
        "comm": round(_num(row.get("score_commercial")), 1),
        "action": action or "n.c.",
        "win": (row.get("fenetre_action", "") or "indetermine").strip() or "indetermine",
        "nom": (row.get("contact_name", "") or "").strip() or "n.c.",
        "email": (row.get("contact_email", "") or "").strip() or "n.c.",
        "tel": (row.get("contact_phone", "") or "").strip() or "n.c.",
        "cible": cible.strip(),
        "justif": (row.get("justification", "") or "").strip(),
        "grp": groupe,
        "lien": (row.get("lien_avis", "") or "").strip(),
        "ecart": _vrai(row.get("divergence", "")),
        "secu": _vrai(row.get("securite_existante_detectee", "")),
    }


def construire_leads(lignes_ted, lignes_bm):
    """Fusionne les deux onglets en une liste de leads, trie par score."""
    leads = [ligne_vers_lead(r, "TED") for r in lignes_ted]
    leads += [ligne_vers_lead(r, "BM") for r in lignes_bm]
    # On ne garde que les avis exploitables (titre + score)
    leads = [l for l in leads if l["titre"]]
    leads.sort(key=lambda l: l["final"], reverse=True)
    return leads


def lire_onglets(sheet_id, fichier_cs):
    """Lit les deux onglets via gspread. Import paresseux (pas requis pour les tests)."""
    import gspread
    from google.oauth2.service_account import Credentials
    portee = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = Credentials.from_service_account_file(fichier_cs, scopes=portee)
    client = gspread.authorize(creds)
    classeur = client.open_by_key(sheet_id)

    def lire(nom):
        try:
            return classeur.worksheet(nom).get_all_records()
        except gspread.WorksheetNotFound:
            print("  (info) onglet '{}' introuvable, ignore.".format(nom))
            return []

    return lire(NOM_ONGLET_TED), lire(NOM_ONGLET_BM)


def generer_html(leads):
    """Produit la page HTML autonome (situation board) a partir des leads."""
    meta = {
        "date": date.today().strftime("%d/%m/%Y"),
        "total": len(leads),
        "contacter": sum(1 for l in leads if l["action"] == "contacter"),
        "surveiller": sum(1 for l in leads if l["action"] == "surveiller"),
        "ignorer": sum(1 for l in leads if l["action"] == "ignorer"),
    }
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
    lignes_ted, lignes_bm = lire_onglets(sheet_id, fichier_cs)
    leads = construire_leads(lignes_ted, lignes_bm)
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
  @media(prefers-reduced-motion:reduce){*{transition:none!important}}
  :focus-visible{outline:2px solid var(--watch);outline-offset:2px}
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
  </header>
  <section class="zones">
    <div class="eyebrow">Carte des zones</div>
    <div class="zonegrid" id="zonegrid"></div>
  </section>
  <div class="controls">
    <label class="search">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
      <input type="text" id="search" placeholder="Filtrer par pays, agence, mot-clé..." autocomplete="off">
    </label>
    <div class="seg" id="srcseg" role="group" aria-label="Source">
      <button data-src="all" aria-pressed="true">Toutes</button>
      <button data-src="BM" aria-pressed="false">Banque Mondiale</button>
      <button data-src="TED" aria-pressed="false">TED</button>
    </div>
    <button class="clearz" id="clearz">Réinitialiser</button>
  </div>
  <div class="count" id="count"></div>
  <div class="leads" id="leads"></div>
  <footer id="foot"></footer>
</div>
<script>
const LEADS = __LEADS_JSON__;
const META = __META_JSON__;
const ORDRE_ZONES = ["Afrique de l'Ouest","Sahel","Afrique centrale","Afrique de l'Est","Afrique australe","Afrique du Nord","Proche-Orient","Péninsule arabique","Asie centrale","Asie du Sud","Asie du Sud-Est","Caucase","Balkans","Europe de l'Est","Caraïbes","Amérique latine","Europe de l'Ouest","Outre-mer","Non classé"];
const winLabel={immediate:'Fenêtre immédiate',court_terme:'Court terme',indetermine:'Fenêtre indéterminée'};
let state={zone:null,src:'all',q:'',action:'contacter'};

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

// Zone map (compte selon l'action filtree)
function buildZones(){
  const counts={};
  LEADS.forEach(l=>{ if(state.action==='all'||l.action===state.action){counts[l.zone]=(counts[l.zone]||0)+1;} });
  const zones=ORDRE_ZONES.filter(z=>counts[z]); 
  const maxZ=Math.max(1,...zones.map(z=>counts[z]));
  const zg=document.getElementById('zonegrid');
  if(!zones.length){zg.innerHTML='<div class="count" style="grid-column:1/-1">Aucune zone pour ce filtre.</div>';return;}
  zg.innerHTML=zones.map(z=>{
    const c=counts[z], intensity=c>=Math.ceil(maxZ*0.75)?3:(c>=2?2:1);
    const pressed=z===state.zone?'true':'false';
    return `<button class="zone" data-zone="${z}" data-int="${intensity}" aria-pressed="${pressed}"><div class="zn">${z}</div><div class="zc">${c}</div><div class="zl">avis</div><div class="zbar"></div></button>`;
  }).join('');
  zg.querySelectorAll('.zone').forEach(el=>el.addEventListener('click',()=>{
    state.zone=state.zone===el.dataset.zone?null:el.dataset.zone;
    document.getElementById('clearz').classList.toggle('on',!!state.zone);
    zg.querySelectorAll('.zone').forEach(x=>x.setAttribute('aria-pressed',x.dataset.zone===state.zone?'true':'false'));
    render();
  }));
}
document.getElementById('clearz').addEventListener('click',()=>{state.zone=null;document.getElementById('clearz').classList.remove('on');buildZones();render();});
document.querySelectorAll('#srcseg button').forEach(b=>b.addEventListener('click',()=>{
  state.src=b.dataset.src;
  document.querySelectorAll('#srcseg button').forEach(x=>x.setAttribute('aria-pressed',x===b?'true':'false'));
  render();
}));
document.getElementById('search').addEventListener('input',e=>{state.q=e.target.value.toLowerCase().trim();render();});

const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function render(){
  buildZones();
  const box=document.getElementById('leads');
  const filtered=LEADS.filter(l=>{
    if(state.action!=='all'&&l.action!==state.action)return false;
    if(state.zone&&l.zone!==state.zone)return false;
    if(state.src!=='all'&&l.src!==state.src)return false;
    if(state.q){const hay=(l.pays+' '+l.agence+' '+l.titre+' '+l.zone+' '+l.nom).toLowerCase();if(!hay.includes(state.q))return false;}
    return true;
  });
  document.getElementById('count').textContent=
    `${filtered.length} avis affiché${filtered.length>1?'s':''}`+
    (state.zone?`, zone ${state.zone}`:'')+(state.src!=='all'?`, source ${state.src}`:'');
  if(!filtered.length){box.innerHTML='<div class="empty">Aucun avis ne correspond à ce filtre.</div>';return;}
  box.innerHTML=filtered.map(l=>{
    const tier=l.action;
    const win=(['immediate','court_terme','indetermine'].includes(l.win))?l.win:'indetermine';
    const mail=l.email!=='n.c.'?`<a href="mailto:${esc(l.email)}">${esc(l.email)}</a>`:'n.c.';
    const tel=l.tel!=='n.c.'?`<a href="tel:${esc(l.tel.replace(/\s/g,''))}">${esc(l.tel)}</a>`:'n.c.';
    const ecart=l.ecart?`<span class="badge ecart" title="Écart d'évaluation entre les deux passes, lire la justification">⚠ écart</span>`:'';
    const contactRows = l.src==='BM' ? `
          <div class="row"><span class="k">Contact</span><span class="v">${esc(l.nom)}</span></div>
          <div class="row"><span class="k">Email</span><span class="v">${mail}</span></div>
          <div class="row"><span class="k">Tél</span><span class="v">${tel}</span></div>` : '';
    return `<article class="lead" data-tier="${tier}"><span class="spine"></span><div class="body">
      <div class="lhead"><div class="lmeta"><span class="src ${l.src.toLowerCase()}">${l.src==='BM'?'Banque Mondiale':'TED'}</span><span class="pays">${esc(l.pays)}</span><span>· ${esc(l.zone)}</span></div>
      <div class="scorebox"><div class="sf">${l.final.toFixed(1)}</div><div class="sd">sûreté ${l.surete.toFixed(1)} · com ${l.comm.toFixed(1)}</div></div></div>
      <h3 class="ltitle">${esc(l.titre)}</h3>
      <div class="badges"><span class="badge win-${win}">${winLabel[win]}</span>${ecart}</div>
      <div class="contact"><div class="row"><span class="k">Agence</span><span class="v">${esc(l.agence)}</span></div>${contactRows}</div>
      <div class="cible"><b>Qui démarcher.</b> ${esc(l.cible)}</div>
      ${l.justif?`<details class="just"><summary><span class="chev">▸</span> Justification sûreté</summary><p>${esc(l.justif)}</p></details>`:''}
      </div><div class="foot"><span class="grp">Groupe ${esc(l.grp)}</span>${l.lien?`<a class="av" href="${esc(l.lien)}" target="_blank" rel="noopener">Voir l'avis ↗</a>`:''}</div></article>`;
  }).join('');
}
document.getElementById('foot').innerHTML=
  'Généré automatiquement après le run du radar. Les contacts proviennent des avis Banque Mondiale.<br>Le destinataire commercial réel est le titulaire du marché, pas l\'agence acheteuse.';
render();
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
