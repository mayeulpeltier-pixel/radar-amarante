# -*- coding: utf-8 -*-
"""
Radar Amarante -- Generateur du COCKPIT (nouvelle interface).
===============================================================================

ROLE : produire `public/cockpit.html`, la nouvelle interface multi-vues, EN
PARALLELE du dashboard historique (`radar_dashboard.py` -> index.html). Ce
generateur ne fait que LIRE le moteur existant et ECRIRE un HTML : il n'altere
jamais radar_dashboard, si bien que l'ancien tableau reste disponible en
permanence (rollback total, migration progressive lot par lot).

REUTILISATION DU MOTEUR (aucune duplication de logique metier) :
  - dash.charger_leads(sheet_id, fichier)  -> lit les onglets + fusionne
  - dash._valeur_en_millions(txt)          -> conversion montant -> M€ (EUR)
  - dash.RISQUE_ZONE                        -> posture par theatre

Le cockpit consomme le MEME schema de lead que le dashboard (cle `final` pour
le score, `action` pour la priorite, `sect` pour le secteur, etc.). La seule
donnee ajoutee ici est `valeur_meur` (float), pre-calculee cote Python pour que
le front n'ait pas a re-parser les montants.

USAGE (identique au dashboard, variables d'env deja en place) :
    TED_SHEET_ID=... GOOGLE_SERVICE_ACCOUNT_FILE=... \
        COCKPIT_OUTPUT=public/cockpit.html python radar_cockpit.py
"""

import json
import os
import sys

import radar_dashboard as dash


# Coordonnees (nom FR -> [lat, lng]) reprises telles quelles du dashboard :
# le front place les marqueurs via COORDS[lead.pays]. Donnee stable.
COORDS = {
    "Mali": [17.6, -3.5], "Niger": [17.6, 9.4], "Burkina Faso": [12.2, -1.6],
    "Tchad": [15.5, 18.7], "Mauritanie": [20.3, -10.9], "Côte d'Ivoire": [7.5, -5.5],
    "Nigeria": [9.1, 8.7], "Sénégal": [14.5, -14.5], "Ghana": [7.9, -1.0],
    "Togo": [8.6, 0.8], "Bénin": [9.3, 2.3], "Guinée": [9.9, -9.7], "Libéria": [6.4, -9.4],
    "RDC": [-4.0, 21.8], "Congo-Brazzaville": [-0.7, 15.8], "Cameroun": [5.6, 12.4],
    "Centrafrique": [6.6, 20.9], "Gabon": [-0.8, 11.6], "Éthiopie": [9.1, 40.5],
    "Kenya": [0.0, 37.9], "Ouganda": [1.4, 32.3], "Tanzanie": [-6.4, 34.9],
    "Somalie": [5.2, 46.2], "Soudan du Sud": [7.3, 30.3], "Rwanda": [-1.9, 29.9],
    "Djibouti": [11.8, 42.6], "Mozambique": [-18.7, 35.5], "Madagascar": [-18.8, 46.9],
    "Afrique du Sud": [-30.6, 22.9], "Zambie": [-13.1, 27.8], "Zimbabwe": [-19.0, 29.2],
    "Malawi": [-13.3, 34.3], "Angola": [-11.2, 17.9], "Botswana": [-22.3, 24.7],
    "Égypte": [26.8, 30.8], "Maroc": [31.8, -7.1], "Tunisie": [33.9, 9.6],
    "Algérie": [28.0, 1.7], "Libye": [26.3, 17.2], "Cisjordanie et Gaza": [31.9, 35.2],
    "Jordanie": [30.6, 36.2], "Liban": [33.9, 35.9], "Irak": [33.2, 43.7],
    "Yémen": [15.6, 48.0], "Turquie": [39.0, 35.2], "Oman": [21.5, 55.9],
    "Ouzbékistan": [41.4, 64.6], "Tadjikistan": [38.9, 71.3], "Kirghizistan": [41.2, 74.8],
    "Kazakhstan": [48.0, 66.9], "Bangladesh": [23.7, 90.4], "Pakistan": [30.4, 69.3],
    "Inde": [22.4, 78.9], "Népal": [28.4, 84.1], "Indonésie": [-2.5, 118.0],
    "Philippines": [12.9, 121.8], "Ukraine": [48.4, 31.2], "Moldavie": [47.2, 28.5],
    "Albanie": [41.2, 20.0], "Macédoine du Nord": [41.6, 21.7], "Serbie": [44.0, 21.0],
    "Géorgie": [42.3, 43.4], "Arménie": [40.1, 45.0], "Azerbaïdjan": [40.1, 47.6],
    "Haïti": [19.0, -72.3], "Jamaïque": [18.1, -77.3], "Mexique": [23.6, -102.6],
    "Honduras": [14.8, -86.2], "Guatemala": [15.5, -90.2], "El Salvador": [13.8, -88.9],
    "Nicaragua": [12.9, -85.2], "Panama": [8.5, -80.1], "Mongolie": [46.9, 103.8],
    "Équateur": [-1.8, -78.2], "Brésil": [-10.3, -53.2], "Colombie": [4.6, -74.3],
    "France": [46.6, 2.2], "Allemagne": [51.2, 10.4], "Danemark": [56.0, 9.5],
    "Nouvelle-Calédonie": [-21.3, 165.5], "Afghanistan": [33.9, 67.7],
    "Arabie Saoudite": [24.0, 45.1], "Argentine": [-38.4, -63.6], "Bahreïn": [26.1, 50.6],
    "Biélorussie": [53.7, 27.9], "Bolivie": [-16.3, -63.6], "Bosnie-Herzégovine": [43.9, 17.7],
    "Burundi": [-3.4, 29.9], "Cambodge": [12.6, 104.9], "Cap-Vert": [16.0, -24.0],
    "Chili": [-35.7, -71.5], "Comores": [-11.6, 43.3], "Eswatini": [-26.5, 31.5],
    "Fidji": [-17.7, 178.0], "Gambie": [13.4, -15.3], "Guinée équatoriale": [1.6, 10.3],
    "Guinée-Bissau": [12.0, -15.0], "Guyana": [4.9, -58.9], "Guyane": [4.0, -53.0],
    "Iran": [32.4, 53.7], "Israël": [31.4, 35.0], "Kosovo": [42.6, 20.9],
    "Koweït": [29.3, 47.6], "Laos": [19.9, 102.5], "Lesotho": [-29.6, 28.2],
    "Liberia": [6.4, -9.4], "Maurice": [-20.3, 57.6], "Mayotte": [-12.8, 45.2],
    "Monténégro": [42.7, 19.4], "Myanmar": [21.9, 95.9], "Namibie": [-22.6, 17.1],
    "Papouasie-Nouvelle-Guinée": [-6.3, 143.9], "Paraguay": [-23.4, -58.4],
    "Pérou": [-9.2, -75.0], "Qatar": [25.3, 51.2], "Russie": [61.5, 90.0],
    "Sao Tomé-et-Principe": [0.2, 6.6], "Seychelles": [-4.7, 55.5],
    "Sierra Leone": [8.5, -11.8], "Soudan": [15.5, 30.2], "Sri Lanka": [7.9, 80.8],
    "Suriname": [4.0, -56.0], "Syrie": [35.0, 38.5], "Trinité-et-Tobago": [10.5, -61.3],
    "Turkménistan": [38.9, 59.6], "Uruguay": [-32.5, -55.8], "Vanuatu": [-16.3, 167.7],
    "Venezuela": [6.4, -66.6], "Émirats Arabes Unis": [24.0, 54.0],
    "Érythrée": [15.2, 39.8], "Îles Salomon": [-9.6, 160.2],
}


def enrichir(leads):
    """Ajoute `valeur_meur` (float, EUR) a chaque lead, via le convertisseur du
    dashboard. Les avis sans montant recoivent 0.0. Ne modifie pas l'entree."""
    out = []
    for l in leads:
        d = dict(l)
        brut = l.get("valeur", "")
        try:
            d["valeur_meur"] = round(dash._valeur_en_millions(brut), 2) if brut else 0.0
        except Exception:
            d["valeur_meur"] = 0.0
        out.append(d)
    return out


def generer_cockpit(leads, risque=None):
    """leads (schema dashboard) -> HTML autonome. Fonction PURE (testable
    offline). `risque` : table posture par zone (defaut = dash.RISQUE_ZONE)."""
    risque = risque if risque is not None else getattr(dash, "RISQUE_ZONE", {})
    payload = enrichir(leads)
    return (GABARIT
            .replace("__LEADS_JSON__", json.dumps(payload, ensure_ascii=False))
            .replace("__COORDS_JSON__", json.dumps(COORDS, ensure_ascii=False))
            .replace("__RISQUE_JSON__", json.dumps(risque, ensure_ascii=False)))


def main():
    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    sortie = os.environ.get("COCKPIT_OUTPUT", "public/cockpit.html")
    if not sheet_id or not fichier:
        print("ERREUR : TED_SHEET_ID et GOOGLE_SERVICE_ACCOUNT_FILE sont requis.")
        sys.exit(1)
    print("Cockpit : lecture du moteur de donnees (charger_leads)...")
    leads, _ = dash.charger_leads(sheet_id, fichier)
    html = generer_cockpit(leads)
    dossier = os.path.dirname(sortie)
    if dossier:
        os.makedirs(dossier, exist_ok=True)
    with open(sortie, "w", encoding="utf-8") as f:
        f.write(html)
    print("Cockpit ecrit : {} ({} leads, {} octets)".format(
        sortie, len(leads), len(html)))


# ===========================================================================
# GABARIT HTML (cockpit). Placeholders : __LEADS_JSON__ __COORDS_JSON__
# __RISQUE_JSON__. Le front normalise le schema dashboard a l'ingestion.
# ===========================================================================
GABARIT = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Radar Amarante · Cockpit</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{--bg:#F4F6F8;--surface:#FFFFFF;--surface-2:#FAFBFC;--ink:#15181F;--ink-2:#586173;--ink-3:#8B93A2;--line:#E6E9EE;--line-2:#EEF1F5;--amarante:#8E2649;--amarante-2:#A83258;--amarante-soft:#F5E7ec;--red:#C0392B;--red-soft:#FBEAE8;--amber:#B07419;--amber-soft:#FBF1E2;--green:#237A57;--green-soft:#E4F2EB;--blue:#33628F;--blue-soft:#E7EEF6;--display:'Space Grotesk',sans-serif;--body:'Inter',sans-serif;--mono:'IBM Plex Mono',monospace;--sh:0 1px 2px rgba(20,24,31,.04),0 2px 8px rgba(20,24,31,.04);--sh-2:0 4px 20px rgba(20,24,31,.10)}
*{box-sizing:border-box;margin:0;padding:0}html,body{height:100%}
body{background:var(--bg);color:var(--ink);font-family:var(--body);font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased}
button{font-family:inherit;cursor:pointer;border:none;background:none;color:inherit}::selection{background:var(--amarante-soft)}
.app{display:grid;grid-template-columns:236px 1fr;min-height:100vh}
.side{background:var(--surface);border-right:1px solid var(--line);display:flex;flex-direction:column;position:sticky;top:0;height:100vh}
.main{min-width:0;display:flex;flex-direction:column}
.brand{padding:22px 22px 18px;display:flex;align-items:center;gap:12px;border-bottom:1px solid var(--line-2)}
.mark{width:34px;height:34px;border-radius:8px;background:linear-gradient(150deg,var(--amarante),var(--amarante-2));position:relative;flex:none;box-shadow:0 2px 8px rgba(142,38,73,.35)}
.mark::after{content:"";position:absolute;inset:9px;border:2px solid rgba(255,255,255,.9);border-radius:50%}
.mark::before{content:"";position:absolute;left:16px;top:4px;width:2px;height:26px;background:rgba(255,255,255,.9)}
.brand h1{font-family:var(--display);font-size:16px;font-weight:600;letter-spacing:-.01em}
.brand .tag{font-size:11px;color:var(--ink-3);font-family:var(--mono);letter-spacing:.02em}
.nav{padding:12px 12px;display:flex;flex-direction:column;gap:2px;flex:1}
.nav-lbl{font-size:10px;font-weight:600;letter-spacing:.09em;text-transform:uppercase;color:var(--ink-3);padding:14px 12px 6px}
.nav a{display:flex;align-items:center;gap:11px;padding:9px 12px;border-radius:8px;font-size:13.5px;font-weight:500;color:var(--ink-2);transition:.12s;cursor:pointer}
.nav a svg{width:17px;height:17px;flex:none;stroke-width:1.9}
.nav a:hover{background:var(--surface-2);color:var(--ink)}
.nav a.on{background:var(--amarante-soft);color:var(--amarante);font-weight:600}
.nav a .cnt{margin-left:auto;font-family:var(--mono);font-size:11px;background:var(--line-2);color:var(--ink-2);padding:1px 7px;border-radius:20px}
.nav a.on .cnt{background:var(--amarante);color:#fff}
.side-foot{padding:14px 18px;border-top:1px solid var(--line-2);font-size:11px;color:var(--ink-3);font-family:var(--mono);display:flex;align-items:center;gap:7px}
.dot-live{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 0 3px var(--green-soft)}
.top{height:60px;border-bottom:1px solid var(--line);background:var(--surface);display:flex;align-items:center;gap:18px;padding:0 26px;position:sticky;top:0;z-index:20}
.top h2{font-family:var(--display);font-size:18px;font-weight:600;letter-spacing:-.01em}
.top .crumb{font-size:12px;color:var(--ink-3);font-family:var(--mono)}.top .spacer{flex:1}
.search{display:flex;align-items:center;gap:9px;background:var(--surface-2);border:1px solid var(--line);border-radius:9px;padding:8px 13px;width:300px;transition:.15s}
.search:focus-within{border-color:var(--amarante);box-shadow:0 0 0 3px var(--amarante-soft)}
.search svg{width:15px;height:15px;color:var(--ink-3);flex:none}
.search input{border:none;outline:none;background:none;font-family:var(--body);font-size:13px;width:100%;color:var(--ink)}
.btn{display:inline-flex;align-items:center;gap:7px;padding:8px 14px;border-radius:8px;font-size:13px;font-weight:600;border:1px solid var(--line);background:var(--surface);color:var(--ink-2);transition:.12s}
.btn:hover{border-color:var(--ink-3);color:var(--ink)}.btn.pri{background:var(--amarante);border-color:var(--amarante);color:#fff}.btn.pri:hover{background:var(--amarante-2)}
.view{padding:26px;display:none}.view.on{display:block;animation:fade .3s ease}
@keyframes fade{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.theatres{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-bottom:22px}
.th{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:15px 16px;position:relative;overflow:hidden;box-shadow:var(--sh);transition:.15s;cursor:pointer}
.th:hover{box-shadow:var(--sh-2);transform:translateY(-2px)}
.th .bar{position:absolute;left:0;top:0;bottom:0;width:4px}
.th .zone{font-family:var(--display);font-weight:600;font-size:13.5px;margin-bottom:2px}
.th .post{font-size:10px;font-family:var(--mono);letter-spacing:.03em;text-transform:uppercase;font-weight:600}
.th .big{font-family:var(--display);font-size:28px;font-weight:600;letter-spacing:-.02em;margin-top:12px;line-height:1}
.th .big small{font-size:11px;color:var(--ink-3);font-weight:500;font-family:var(--body)}
.th .val{font-family:var(--mono);font-size:11.5px;color:var(--ink-2);margin-top:6px}
.post.p-rouge{color:var(--red)}.bar.p-rouge{background:var(--red)}.post.p-orange{color:var(--amber)}.bar.p-orange{background:var(--amber)}.post.p-jaune{color:var(--blue)}.bar.p-jaune{background:var(--blue)}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:22px}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:17px 18px;box-shadow:var(--sh)}
.kpi .k-top{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
.kpi .k-lbl{font-size:11.5px;font-weight:600;color:var(--ink-2)}
.kpi .k-ico{width:32px;height:32px;border-radius:8px;display:grid;place-items:center}.kpi .k-ico svg{width:16px;height:16px;stroke-width:2}
.kpi .k-val{font-family:var(--display);font-size:29px;font-weight:600;letter-spacing:-.02em;line-height:1}
.kpi .k-sub{font-size:12px;color:var(--ink-3);margin-top:5px;font-family:var(--mono)}
.grid-2{display:grid;grid-template-columns:1.35fr 1fr;gap:16px;margin-bottom:16px}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:12px;box-shadow:var(--sh)}
.p-head{display:flex;align-items:center;justify-content:space-between;padding:15px 18px;border-bottom:1px solid var(--line-2)}
.p-head h3{font-family:var(--display);font-size:14px;font-weight:600}.p-head .hint{font-size:11px;color:var(--ink-3);font-family:var(--mono)}
.p-body{padding:16px 18px}.chart-wrap{position:relative;height:230px}
.funnel{display:flex;flex-direction:column;gap:9px;padding:4px 0}
.fn-row{display:grid;grid-template-columns:130px 1fr 42px;align-items:center;gap:12px}
.fn-lbl{font-size:12.5px;font-weight:500;color:var(--ink-2)}
.fn-track{height:26px;background:var(--line-2);border-radius:6px;overflow:hidden}
.fn-fill{height:100%;border-radius:6px;display:flex;align-items:center;padding-left:10px;color:#fff;font-family:var(--mono);font-size:11px;font-weight:600;transition:width .6s}
.fn-n{font-family:var(--mono);font-size:13px;font-weight:600;text-align:right}
.hot{display:flex;flex-direction:column}
.hot-row{display:flex;align-items:center;gap:14px;padding:12px 18px;border-bottom:1px solid var(--line-2);transition:.12s;cursor:pointer}
.hot-row:last-child{border-bottom:none}.hot-row:hover{background:var(--surface-2)}
.score-badge{width:40px;height:40px;border-radius:9px;display:grid;place-items:center;font-family:var(--display);font-weight:700;font-size:16px;flex:none;color:#fff}
.hot-mid{flex:1;min-width:0}.hot-title{font-weight:600;font-size:13.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hot-meta{font-size:11.5px;color:var(--ink-3);font-family:var(--mono);margin-top:2px}
.hot-val{font-family:var(--display);font-weight:600;font-size:14px;flex:none;color:var(--ink-2)}
.filters{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:18px}
.facet{position:relative}
.facet select{appearance:none;background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:8px 32px 8px 13px;font-family:var(--body);font-size:13px;font-weight:500;color:var(--ink);cursor:pointer}
.facet select:hover{border-color:var(--ink-3)}
.facet::after{content:"";position:absolute;right:12px;top:50%;transform:translateY(-25%) rotate(45deg);width:6px;height:6px;border-right:2px solid var(--ink-3);border-bottom:2px solid var(--ink-3);pointer-events:none}
.seg{display:inline-flex;background:var(--surface-2);border:1px solid var(--line);border-radius:8px;padding:3px}
.seg button{padding:6px 13px;border-radius:6px;font-size:12.5px;font-weight:600;color:var(--ink-2);transition:.12s}
.seg button.on{background:var(--surface);color:var(--amarante);box-shadow:var(--sh)}
.chip-clear{margin-left:auto;font-size:12.5px;color:var(--ink-3);font-weight:600}.chip-clear:hover{color:var(--amarante)}
.tbl-wrap{background:var(--surface);border:1px solid var(--line);border-radius:12px;overflow:hidden;box-shadow:var(--sh)}
table{width:100%;border-collapse:collapse;font-size:13px}
thead th{text-align:left;font-size:11px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--ink-3);padding:12px 16px;border-bottom:1px solid var(--line);background:var(--surface-2);white-space:nowrap;cursor:pointer;user-select:none}
thead th:hover{color:var(--ink)}.ar{opacity:.4;font-size:9px;margin-left:3px}
tbody td{padding:13px 16px;border-bottom:1px solid var(--line-2);vertical-align:middle}
tbody tr{transition:.1s;cursor:pointer}tbody tr:hover{background:var(--surface-2)}tbody tr:last-child td{border-bottom:none}
.t-title{font-weight:600;max-width:340px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.t-sub{font-size:11px;color:var(--ink-3);font-family:var(--mono);margin-top:2px}
.t-val{font-family:var(--mono);font-weight:600;white-space:nowrap}.t-score{font-family:var(--display);font-weight:700;font-size:15px}
.pill{display:inline-flex;align-items:center;gap:5px;padding:3px 9px;border-radius:20px;font-size:11px;font-weight:600;font-family:var(--mono);white-space:nowrap}
.pill.contacter{background:var(--green-soft);color:var(--green)}.pill.surveiller{background:var(--amber-soft);color:var(--amber)}.pill.ignorer{background:var(--line-2);color:var(--ink-3)}
.tag-src{font-family:var(--mono);font-size:10.5px;font-weight:600;padding:2px 7px;border-radius:5px;background:var(--blue-soft);color:var(--blue)}
.tag-src.ATTRIB{background:var(--amarante-soft);color:var(--amarante)}.tag-src.PRIVÉ{background:var(--amber-soft);color:var(--amber)}
.flag{font-size:11px;font-weight:600;font-family:var(--mono);color:var(--red)}
.mini-badges{display:flex;gap:5px;margin-top:4px;flex-wrap:wrap}
.mb{font-size:9.5px;font-family:var(--mono);font-weight:600;padding:1px 6px;border-radius:4px;text-transform:uppercase}
.mb.renouv{background:var(--amber-soft);color:var(--amber)}.mb.etr{background:var(--blue-soft);color:var(--blue)}.mb.secu{background:var(--red-soft);color:var(--red)}
.map-view{display:grid;grid-template-columns:250px 1fr;gap:16px;height:calc(100vh - 60px - 52px)}
#map{border-radius:12px;border:1px solid var(--line);box-shadow:var(--sh);height:100%}
.map-side{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px;box-shadow:var(--sh);overflow:auto}
.map-side h4{font-family:var(--display);font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--ink-3);margin:16px 0 8px}.map-side h4:first-child{margin-top:0}
.leg{display:flex;align-items:center;gap:9px;padding:6px 0;font-size:12.5px;font-weight:500}.leg .sw{width:12px;height:12px;border-radius:50%;flex:none}
.chk{display:flex;align-items:center;gap:9px;padding:5px 0;font-size:12.5px;cursor:pointer}.chk input{accent-color:var(--amarante);width:15px;height:15px}
.leaflet-popup-content-wrapper{border-radius:10px;box-shadow:var(--sh-2)}.leaflet-popup-content{margin:13px 15px;font-family:var(--body)}
.pop-t{font-family:var(--display);font-weight:600;font-size:13px;margin-bottom:5px;color:var(--ink)}.pop-m{font-size:11.5px;color:var(--ink-2);font-family:var(--mono);line-height:1.6}
.soon{display:grid;place-items:center;height:60vh;text-align:center}
.soon .ico{width:64px;height:64px;border-radius:16px;background:var(--amarante-soft);display:grid;place-items:center;margin:0 auto 18px}.soon .ico svg{width:30px;height:30px;color:var(--amarante);stroke-width:1.7}
.soon h3{font-family:var(--display);font-size:20px;font-weight:600;margin-bottom:8px}.soon p{color:var(--ink-2);max-width:440px;font-size:13.5px}
.drawer-ov{position:fixed;inset:0;background:rgba(20,24,31,.35);opacity:0;pointer-events:none;transition:.2s;z-index:50}.drawer-ov.on{opacity:1;pointer-events:auto}
.drawer{position:fixed;top:0;right:0;bottom:0;width:440px;max-width:92vw;background:var(--surface);box-shadow:-8px 0 32px rgba(20,24,31,.18);transform:translateX(100%);transition:.28s cubic-bezier(.4,0,.2,1);z-index:51;overflow:auto}.drawer.on{transform:none}
.dr-head{padding:22px 24px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--surface)}
.dr-close{position:absolute;top:20px;right:20px;width:30px;height:30px;border-radius:7px;display:grid;place-items:center;color:var(--ink-3)}.dr-close:hover{background:var(--surface-2);color:var(--ink)}
.dr-src{font-family:var(--mono);font-size:11px;font-weight:600;color:var(--amarante)}
.dr-head h3{font-family:var(--display);font-size:18px;font-weight:600;margin:6px 0 10px;line-height:1.3;padding-right:34px}
.dr-body{padding:22px 24px}.dr-sec{margin-bottom:22px}
.dr-sec h5{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--ink-3);margin-bottom:10px}
.dr-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.dr-field .l{font-size:11px;color:var(--ink-3);font-family:var(--mono)}.dr-field .v{font-size:14px;font-weight:600;margin-top:2px}
.dr-analyse{background:var(--surface-2);border:1px solid var(--line-2);border-radius:10px;padding:14px 16px;font-size:13px;line-height:1.6;color:var(--ink-2)}
.dr-actions{display:flex;gap:10px;margin-top:8px}.dr-actions .btn{flex:1;justify-content:center}
.empty{padding:60px;text-align:center;color:var(--ink-3);font-family:var(--mono);font-size:13px}
@media(max-width:1100px){.kpis{grid-template-columns:repeat(2,1fr)}.grid-2{grid-template-columns:1fr}.map-view{grid-template-columns:1fr;height:auto}#map{height:60vh}}
@media(max-width:720px){.app{grid-template-columns:1fr}.side{display:none}}
</style>
</head>
<body>
<div class="app">
  <aside class="side">
    <div class="brand"><div class="mark"></div><div><h1>Radar Amarante</h1><div class="tag">SALLE DE SITUATION</div></div></div>
    <nav class="nav" id="nav">
      <div class="nav-lbl">Pilotage</div>
      <a data-view="overview" class="on"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></svg>Vue d'ensemble</a>
      <a data-view="opps"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M3 6h18M3 12h18M3 18h18"/></svg>Opportunités<span class="cnt" id="cnt-opps"></span></a>
      <a data-view="map"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M9 3L3 6v15l6-3 6 3 6-3V3l-6 3-6-3z"/><path d="M9 3v15M15 6v15"/></svg>Carte des théâtres</a>
      <div class="nav-lbl">Renseignement</div>
      <a data-view="attrib"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M6 21V7a2 2 0 012-2h8a2 2 0 012 2v14"/><path d="M6 21h12M10 9h4M10 13h4M10 17h4"/></svg>Attributions<span class="cnt" id="cnt-attrib"></span></a>
      <a data-view="firmo"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/></svg>Entreprises</a>
      <a data-view="geo"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 2l9 5v6c0 5-4 8-9 9-5-1-9-4-9-9V7z"/></svg>Géopolitique</a>
    </nav>
    <div class="side-foot"><span class="dot-live"></span> <span id="run-meta">Cockpit</span></div>
  </aside>
  <div class="main">
    <div class="top">
      <div><h2 id="top-title">Vue d'ensemble</h2></div>
      <div class="crumb" id="top-crumb"></div>
      <div class="spacer"></div>
      <label class="search"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg><input id="search" placeholder="Rechercher un marché, pays, titulaire..."></label>
      <button class="btn pri" onclick="exportCSV()"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12m0 0l-4-4m4 4l4-4M4 21h16"/></svg>Exporter</button>
    </div>
    <section class="view on" id="v-overview">
      <div class="theatres" id="theatres"></div>
      <div class="kpis" id="kpis"></div>
      <div class="grid-2">
        <div class="panel"><div class="p-head"><h3>Marchés par théâtre</h3><span class="hint">volume · valeur M€</span></div><div class="p-body"><div class="chart-wrap"><canvas id="c-zone"></canvas></div></div></div>
        <div class="panel"><div class="p-head"><h3>Secteurs</h3><span class="hint">part des marchés</span></div><div class="p-body"><div class="chart-wrap"><canvas id="c-sect"></canvas></div></div></div>
      </div>
      <div class="grid-2">
        <div class="panel"><div class="p-head"><h3>Détections par mois</h3><span class="hint">avis vs attributions</span></div><div class="p-body"><div class="chart-wrap"><canvas id="c-time"></canvas></div></div></div>
        <div class="panel"><div class="p-head"><h3>Pipeline de prospection</h3><span class="hint">du signal au contrat</span></div><div class="p-body"><div class="funnel" id="funnel"></div></div></div>
      </div>
      <div class="panel"><div class="p-head"><h3>À contacter en priorité</h3><span class="hint">score le plus élevé, non traités</span></div><div class="hot" id="hot"></div></div>
    </section>
    <section class="view" id="v-opps">
      <div class="filters">
        <div class="facet"><select id="f-zone"><option value="">Tous les théâtres</option></select></div>
        <div class="facet"><select id="f-sect"><option value="">Tous les secteurs</option></select></div>
        <div class="facet"><select id="f-src"><option value="">Toutes les sources</option></select></div>
        <div class="seg" id="f-prio"><button data-p="" class="on">Toutes</button><button data-p="contacter">À contacter</button><button data-p="surveiller">À surveiller</button></div>
        <button class="chip-clear" onclick="resetFilters()">Réinitialiser</button>
      </div>
      <div class="tbl-wrap"><table><thead><tr>
        <th data-sort="titre">Marché<span class="ar">↕</span></th><th data-sort="zone">Théâtre<span class="ar">↕</span></th>
        <th data-sort="secteur">Secteur<span class="ar">↕</span></th><th data-sort="valeur">Valeur<span class="ar">↕</span></th>
        <th data-sort="score">Score<span class="ar">↕</span></th><th data-sort="src">Source<span class="ar">↕</span></th><th data-sort="prio">Priorité<span class="ar">↕</span></th>
      </tr></thead><tbody id="tbody"></tbody></table></div>
      <div style="padding:12px 4px;font-family:var(--mono);font-size:12px;color:var(--ink-3)" id="tbl-count"></div>
    </section>
    <section class="view" id="v-map">
      <div class="map-view">
        <div class="map-side">
          <h4>Priorité</h4>
          <div class="leg"><span class="sw" style="background:#C0392B"></span>À contacter</div>
          <div class="leg"><span class="sw" style="background:#B07419"></span>À surveiller</div>
          <div class="leg"><span class="sw" style="background:#8B93A2"></span>À écarter</div>
          <h4>Théâtres</h4><div id="map-zones"></div>
          <h4>Sources</h4><div id="map-srcs"></div>
        </div>
        <div id="map"></div>
      </div>
    </section>
    <section class="view" id="v-attrib">
      <div class="kpis" id="kpis-attrib"></div>
      <div class="tbl-wrap"><table><thead><tr><th>Titulaire</th><th>Marché gagné</th><th>Théâtre</th><th>Origine</th><th>Valeur</th><th>Statut</th></tr></thead><tbody id="tbody-attrib"></tbody></table></div>
    </section>
    <section class="view" id="v-firmo"><div class="soon"><div><div class="ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/></svg></div><h3>Fiche entreprise 360°</h3><p>Vue unifiée par titulaire : marchés gagnés, présence par théâtre, dirigeants et contacts enrichis. Lot 2 de la migration.</p></div></div></section>
    <section class="view" id="v-geo"><div class="soon"><div><div class="ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 2l9 5v6c0 5-4 8-9 9-5-1-9-4-9-9V7z"/></svg></div><h3>Alertes géopolitiques</h3><p>Alertes voyageurs et événements superposés aux opportunités. Lot 2 de la migration.</p></div></div></section>
  </div>
</div>
<div class="drawer-ov" id="drawer-ov" onclick="closeDrawer()"></div>
<div class="drawer" id="drawer"><div id="drawer-content"></div></div>
<script>
const RAW=__LEADS_JSON__, COORDS=__COORDS_JSON__, RISQUE=__RISQUE_JSON__;
function posture(z){const r=RISQUE[z]||1.5;return r>=4.5?["p-rouge","Posture rouge"]:r>=3?["p-orange","Posture orange"]:["p-jaune","Posture jaune"];}
const LEADS=RAW.map((l,i)=>({
  id:i,titre:l.titre||"(sans titre)",src:l.src||"?",zone:l.zone||"Non classé",pays:l.pays||"",
  secteur:l.sect||l.grp||"Autre",score:+l.final||0,prio:l.action||"surveiller",valeur:+l.valeur_meur||0,
  acheteur:l.agence||"n.c.",statut:l.statut||"nouveau",titulaire:l.entreprise||"",pays_tit:l.origine||"",
  etranger:!!l.etranger_titulaire,renouv:l.statut_renouv||"",nature:l.nature_deploiement||"",besoin:l.besoin_surete||"",
  interlocuteur:l.interlocuteur||"",cible:l.cible||"",justif:l.justif||"",lien:l.lien||"",secu:!!l.secu,
  mois:l.mois_label||l.mois||"",nom:l.nom||"n.c.",email:l.email||"n.c.",tel:l.tel||"n.c.",win:l.win||"",pub:l.pub||""
}));
const SECT_COLORS={"Génie civil / BTP":"#8E2649","Eau / assainissement":"#33628F","Énergie":"#B07419","Santé":"#237A57","Sécurité / défense":"#C0392B","Logistique / transport":"#6B5B95","Extractif / mines":"#7A5230","Télécom / IT":"#3A8FA8"};
const PRIO_COLOR={contacter:"#C0392B",surveiller:"#B07419",ignorer:"#8B93A2"};
const PRIO_LBL={contacter:"À contacter",surveiller:"À surveiller",ignorer:"À écarter"};
const fmtEur=v=>!v?"n.c.":v>=1?v.toFixed(v<10?1:0)+" M€":(v*1000).toFixed(0)+" k€";
const scoreColor=s=>s>=8?"#237A57":s>=6?"#B07419":s>=4?"#33628F":"#8B93A2";
const actifs=()=>LEADS.filter(l=>l.statut!=="écarté"&&l.statut!=="perdu");
let state={view:"overview",zone:"",sect:"",src:"",prio:"",q:"",sort:"score",dir:-1};

function renderTheatres(){
  const byZone={};actifs().forEach(l=>{(byZone[l.zone]=byZone[l.zone]||[]).push(l);});
  const zones=Object.keys(byZone).sort((a,b)=>byZone[b].length-byZone[a].length).slice(0,6);
  document.getElementById("theatres").innerHTML=zones.map(z=>{
    const it=byZone[z];const val=it.reduce((s,l)=>s+l.valeur,0);const hot=it.filter(l=>l.prio==="contacter").length;const p=posture(z);
    return `<div class="th" onclick="goZone('${z.replace(/'/g,"\\'")}')"><div class="bar ${p[0]}"></div><div class="zone">${z}</div><div class="post ${p[0]}">${p[1]}</div><div class="big">${it.length}<small> marchés</small></div><div class="val">${val?fmtEur(val)+" · ":""}${hot} à contacter</div></div>`;
  }).join("")||'<div class="empty">Aucun marché en zone couverte.</div>';
}
function renderKPIs(){
  const act=actifs();const contacter=act.filter(l=>l.prio==="contacter").length;
  const valeur=act.reduce((s,l)=>s+l.valeur,0);
  const etr=LEADS.filter(l=>l.src==="ATTRIB"&&l.etranger).length;
  const renouv=LEADS.filter(l=>l.renouv).length;
  const cards=[
    {lbl:"À contacter",val:contacter,sub:"leads chauds actifs",ico:'<path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/>',c:"var(--red)",cs:"var(--red-soft)"},
    {lbl:"Valeur du pipeline",val:fmtEur(valeur),sub:"attributions chiffrées",ico:'<path d="M12 1v22M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6"/>',c:"var(--green)",cs:"var(--green-soft)"},
    {lbl:"Titulaires étrangers",val:etr,sub:"déploiements à démarcher",ico:'<circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15 15 0 010 20 15 15 0 010-20"/>',c:"var(--blue)",cs:"var(--blue-soft)"},
    {lbl:"Renouvellements",val:renouv,sub:"contrats à échéance suivie",ico:'<path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.5 9a9 9 0 0114.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0020.5 15"/>',c:"var(--amber)",cs:"var(--amber-soft)"}
  ];
  document.getElementById("kpis").innerHTML=cards.map(k=>`<div class="kpi"><div class="k-top"><span class="k-lbl">${k.lbl}</span><span class="k-ico" style="background:${k.cs}"><svg viewBox="0 0 24 24" fill="none" stroke="${k.c}">${k.ico}</svg></span></div><div class="k-val">${k.val}</div><div class="k-sub">${k.sub}</div></div>`).join("");
}
let charts={};
function renderCharts(){
  Object.values(charts).forEach(c=>c&&c.destroy());
  Chart.defaults.font.family="'Inter',sans-serif";Chart.defaults.font.size=11;Chart.defaults.color="#586173";
  const act=actifs();
  const byZone={};act.forEach(l=>{(byZone[l.zone]=byZone[l.zone]||[]).push(l);});
  const zones=Object.keys(byZone).sort((a,b)=>byZone[b].length-byZone[a].length).slice(0,7);
  charts.zone=new Chart(document.getElementById("c-zone"),{type:"bar",data:{labels:zones,datasets:[
    {label:"Marchés",data:zones.map(z=>byZone[z].length),backgroundColor:"#8E2649",borderRadius:5,barPercentage:.62},
    {label:"Valeur M€",data:zones.map(z=>byZone[z].reduce((s,l)=>s+l.valeur,0)),backgroundColor:"#E5C4CF",borderRadius:5,barPercentage:.62,yAxisID:"y1"}
  ]},options:{maintainAspectRatio:false,plugins:{legend:{position:"bottom",labels:{boxWidth:10,boxHeight:10,padding:14,usePointStyle:true}}},scales:{y:{grid:{color:"#EEF1F5"},title:{display:true,text:"marchés"}},y1:{position:"right",grid:{display:false},title:{display:true,text:"M€"}},x:{grid:{display:false}}}}});
  const sects={};act.forEach(l=>sects[l.secteur]=(sects[l.secteur]||0)+1);
  const sl=Object.keys(sects).sort((a,b)=>sects[b]-sects[a]).slice(0,8);
  charts.sect=new Chart(document.getElementById("c-sect"),{type:"doughnut",data:{labels:sl,datasets:[{data:sl.map(s=>sects[s]),backgroundColor:sl.map((s,i)=>SECT_COLORS[s]||["#8E2649","#33628F","#B07419","#237A57","#C0392B","#6B5B95","#7A5230","#3A8FA8"][i%8]),borderWidth:2,borderColor:"#fff"}]},options:{maintainAspectRatio:false,cutout:"62%",plugins:{legend:{position:"right",labels:{boxWidth:9,boxHeight:9,padding:8,usePointStyle:true,font:{size:10}}}}}});
  const mm={};LEADS.forEach(l=>{if(l.mois){const k=l.mois;mm[k]=mm[k]||{a:0,at:0};l.src==="ATTRIB"?mm[k].at++:mm[k].a++;}});
  const mk=Object.keys(mm).sort();
  charts.time=new Chart(document.getElementById("c-time"),{type:"line",data:{labels:mk,datasets:[
    {label:"Avis",data:mk.map(k=>mm[k].a),borderColor:"#8E2649",backgroundColor:"rgba(142,38,73,.08)",fill:true,tension:.35,borderWidth:2.5,pointRadius:3,pointBackgroundColor:"#8E2649"},
    {label:"Attributions",data:mk.map(k=>mm[k].at),borderColor:"#33628F",backgroundColor:"rgba(51,98,143,.06)",fill:true,tension:.35,borderWidth:2.5,pointRadius:3,pointBackgroundColor:"#33628F"}
  ]},options:{maintainAspectRatio:false,plugins:{legend:{position:"bottom",labels:{boxWidth:10,boxHeight:10,padding:14,usePointStyle:true}}},scales:{y:{grid:{color:"#EEF1F5"},ticks:{precision:0}},x:{grid:{display:false}}}}});
}
function renderFunnel(){
  const traite=l=>l.statut&&l.statut!=="nouveau";
  const steps=[
    {l:"Signaux détectés",n:LEADS.length,c:"#33628F"},
    {l:"À contacter",n:LEADS.filter(l=>l.prio==="contacter").length,c:"#8E2649"},
    {l:"En traitement",n:LEADS.filter(l=>traite(l)&&l.statut!=="gagné"&&l.statut!=="perdu"&&l.statut!=="écarté").length,c:"#B07419"},
    {l:"Gagné",n:LEADS.filter(l=>l.statut==="gagné").length,c:"#237A57"}
  ];
  const max=Math.max(steps[0].n,1);
  document.getElementById("funnel").innerHTML=steps.map(s=>`<div class="fn-row"><div class="fn-lbl">${s.l}</div><div class="fn-track"><div class="fn-fill" style="width:${Math.max(6,s.n/max*100)}%;background:${s.c}">${s.n}</div></div><div class="fn-n">${(s.n/max*100).toFixed(0)}%</div></div>`).join("");
}
function renderHot(){
  const hot=LEADS.filter(l=>l.prio==="contacter"&&(l.statut==="nouveau"||!l.statut)).sort((a,b)=>b.score-a.score).slice(0,6);
  document.getElementById("hot").innerHTML=hot.map(l=>`<div class="hot-row" onclick="openDrawer(${l.id})"><div class="score-badge" style="background:${scoreColor(l.score)}">${l.score.toFixed(1)}</div><div class="hot-mid"><div class="hot-title">${esc(l.titre)}</div><div class="hot-meta">${l.zone} · ${l.pays} · ${l.secteur} · ${l.src}</div></div><div class="hot-val">${fmtEur(l.valeur)}</div></div>`).join("")||'<div class="empty">Aucun lead à contacter.</div>';
}
function filtered(){
  return LEADS.filter(l=>{
    if(state.zone&&l.zone!==state.zone)return false;
    if(state.sect&&l.secteur!==state.sect)return false;
    if(state.src&&l.src!==state.src)return false;
    if(state.prio&&l.prio!==state.prio)return false;
    if(state.q){const q=state.q.toLowerCase();if(!((l.titre+l.pays+l.zone+l.secteur+l.acheteur+l.titulaire).toLowerCase().includes(q)))return false;}
    return true;
  }).sort((a,b)=>{let va=a[state.sort],vb=b[state.sort];if(typeof va==="string"){va=va.toLowerCase();vb=(vb||"").toLowerCase();return va<vb?-state.dir:va>vb?state.dir:0;}return((va||0)-(vb||0))*state.dir;});
}
function esc(s){return(s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}
function renderTable(){
  const rows=filtered();
  document.getElementById("tbody").innerHTML=rows.map(l=>{
    const b=[];if(l.renouv==="imminent")b.push('<span class="mb renouv">renouv. imminent</span>');else if(l.renouv==="a_venir")b.push('<span class="mb renouv">renouv. à venir</span>');
    if(l.etranger)b.push('<span class="mb etr">titulaire étranger</span>');if(l.secu)b.push('<span class="mb secu">sûreté en place</span>');
    return `<tr onclick="openDrawer(${l.id})"><td><div class="t-title">${esc(l.titre)}</div><div class="t-sub">${esc(l.acheteur)}</div>${b.length?`<div class="mini-badges">${b.join("")}</div>`:""}</td><td>${l.zone}<div class="t-sub">${l.pays}</div></td><td>${l.secteur}</td><td class="t-val">${fmtEur(l.valeur)}</td><td><span class="t-score" style="color:${scoreColor(l.score)}">${l.score.toFixed(1)}</span></td><td><span class="tag-src ${l.src}">${l.src}</span></td><td><span class="pill ${l.prio}">${PRIO_LBL[l.prio]||l.prio}</span></td></tr>`;
  }).join("")||'<tr><td colspan="7" class="empty">Aucun marché ne correspond à ces filtres.</td></tr>';
  const val=rows.reduce((s,l)=>s+l.valeur,0);
  document.getElementById("tbl-count").textContent=rows.length+" marché"+(rows.length>1?"s":"")+(val?" · "+fmtEur(val)+" cumulés":"");
}
function renderAttrib(){
  const at=LEADS.filter(l=>l.src==="ATTRIB");
  const kp=[{lbl:"Attributions suivies",val:at.length,c:"var(--amarante)"},{lbl:"Titulaires étrangers",val:at.filter(l=>l.etranger).length,c:"var(--blue)"},{lbl:"Renouvellements",val:at.filter(l=>l.renouv).length,c:"var(--amber)"},{lbl:"Valeur cumulée",val:fmtEur(at.reduce((s,l)=>s+l.valeur,0)),c:"var(--green)"}];
  document.getElementById("kpis-attrib").innerHTML=kp.map(k=>`<div class="kpi"><div class="k-lbl" style="margin-bottom:8px">${k.lbl}</div><div class="k-val" style="color:${k.c}">${k.val}</div></div>`).join("");
  document.getElementById("tbody-attrib").innerHTML=at.map(l=>{
    const st=l.renouv==="imminent"?'<span class="mb renouv">renouv. imminent</span>':l.renouv==="a_venir"?'<span class="mb renouv">renouv. à venir</span>':'<span class="pill contacter">attribué</span>';
    return `<tr onclick="openDrawer(${l.id})"><td><strong>${esc(l.titulaire||"—")}</strong></td><td><div class="t-title" style="max-width:260px">${esc(l.titre)}</div></td><td>${l.zone}<div class="t-sub">${l.pays}</div></td><td>${l.pays_tit?`<span class="tag-src ${l.etranger?"ATTRIB":""}">${l.pays_tit}</span>${l.etranger?' <span class="flag">étr.</span>':""}`:"—"}</td><td class="t-val">${fmtEur(l.valeur)}</td><td>${st}</td></tr>`;
  }).join("")||'<tr><td colspan="6" class="empty">Aucune attribution.</td></tr>';
}
let map,markers=[];
function initMap(){
  if(map)return;
  map=L.map("map",{worldCopyJump:true,minZoom:2,attributionControl:true}).setView([18,18],2.3);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",{maxZoom:18,attribution:'&copy; OpenStreetMap &copy; CARTO'}).addTo(map);
  const zones=[...new Set(LEADS.map(l=>l.zone))].sort();
  document.getElementById("map-zones").innerHTML=zones.map(z=>`<label class="chk"><input type="checkbox" checked data-mzone="${esc(z)}" onchange="drawMarkers()">${z}</label>`).join("");
  document.getElementById("map-srcs").innerHTML=[...new Set(LEADS.map(l=>l.src))].sort().map(s=>`<label class="chk"><input type="checkbox" checked data-msrc="${s}" onchange="drawMarkers()">${s}</label>`).join("");
  drawMarkers();
}
function drawMarkers(){
  markers.forEach(m=>map.removeLayer(m));markers=[];
  const zOn=[...document.querySelectorAll("[data-mzone]:checked")].map(c=>c.dataset.mzone);
  const sOn=[...document.querySelectorAll("[data-msrc]:checked")].map(c=>c.dataset.msrc);
  const byPays={};
  LEADS.filter(l=>zOn.includes(l.zone)&&sOn.includes(l.src)&&COORDS[l.pays]).forEach(l=>{(byPays[l.pays]=byPays[l.pays]||[]).push(l);});
  Object.keys(byPays).forEach(pays=>{
    const it=byPays[pays];const top=it.slice().sort((a,b)=>b.score-a.score)[0];
    const r=Math.min(7+it.length*1.5,22);
    const m=L.circleMarker(COORDS[pays],{radius:r,fillColor:PRIO_COLOR[top.prio],color:"#fff",weight:2,fillOpacity:.8});
    m.bindPopup(`<div class="pop-t">${pays} · ${it.length} marché${it.length>1?"s":""}</div><div class="pop-m">${it.slice(0,4).map(l=>`${l.score.toFixed(1)} · ${esc(l.titre).slice(0,42)}`).join("<br>")}${it.length>4?"<br>…":""}</div>`);
    m.addTo(map);markers.push(m);
  });
}
function openDrawer(id){
  const l=LEADS.find(x=>x.id===id);if(!l)return;
  const natL={expatrie_significatif:"Expatrié significatif",mixte:"Encadrement international, main-d'œuvre locale",local_uniquement:"Personnel local",aucun_deploiement:"Aucun déploiement"}[l.nature]||(l.nature||"non analysé");
  const contact=(l.email!=="n.c."||l.nom!=="n.c.")?`<div class="dr-sec"><h5>Contact enrichi</h5><div class="dr-grid"><div class="dr-field"><div class="l">Nom</div><div class="v">${esc(l.nom)}</div></div><div class="dr-field"><div class="l">Email</div><div class="v" style="font-size:12px">${esc(l.email)}</div></div></div></div>`:"";
  document.getElementById("drawer-content").innerHTML=`
  <div class="dr-head"><button class="dr-close" onclick="closeDrawer()"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg></button>
    <div class="dr-src">${l.src} · ${l.zone}</div><h3>${esc(l.titre)}</h3>
    <div style="display:flex;gap:8px;align-items:center"><span class="score-badge" style="width:34px;height:34px;font-size:14px;background:${scoreColor(l.score)}">${l.score.toFixed(1)}</span><span class="pill ${l.prio}">${PRIO_LBL[l.prio]||l.prio}</span><span class="t-val" style="margin-left:auto;font-size:15px">${fmtEur(l.valeur)}</span></div></div>
  <div class="dr-body">
    <div class="dr-sec"><h5>Marché</h5><div class="dr-grid"><div class="dr-field"><div class="l">Pays</div><div class="v">${l.pays||"—"}</div></div><div class="dr-field"><div class="l">Secteur</div><div class="v">${l.secteur}</div></div><div class="dr-field"><div class="l">Acheteur / bailleur</div><div class="v" style="font-size:13px">${esc(l.acheteur)}</div></div><div class="dr-field"><div class="l">Fenêtre</div><div class="v">${l.win||"—"}</div></div></div></div>
    ${l.titulaire||l.pays_tit?`<div class="dr-sec"><h5>Titulaire</h5><div class="dr-grid"><div class="dr-field"><div class="l">Entreprise</div><div class="v">${esc(l.titulaire||"—")}</div></div><div class="dr-field"><div class="l">Origine</div><div class="v">${l.pays_tit||"—"} ${l.etranger?'<span class="flag">étranger</span>':""}</div></div></div></div>`:""}
    <div class="dr-sec"><h5>Cible commerciale</h5><div class="dr-analyse">${esc(l.cible)||"—"}${l.interlocuteur?"<br><strong>Interlocuteur :</strong> "+esc(l.interlocuteur):""}${l.besoin?"<br><strong>Besoin de sûreté :</strong> "+l.besoin:""}${l.nature?"<br><strong>Déploiement :</strong> "+natL:""}</div></div>
    ${l.justif?`<div class="dr-sec"><h5>Analyse</h5><div class="dr-analyse">${esc(l.justif)}</div></div>`:""}
    ${contact}
    <div class="dr-sec"><h5>Action</h5><div class="dr-actions"><button class="btn pri">Marquer à contacter</button><button class="btn">Écarter</button></div>${l.lien?`<div style="margin-top:10px"><a class="btn" style="width:100%;justify-content:center" href="${l.lien}" target="_blank">Ouvrir l'avis source</a></div>`:""}</div>
  </div>`;
  document.getElementById("drawer").classList.add("on");document.getElementById("drawer-ov").classList.add("on");
}
function closeDrawer(){document.getElementById("drawer").classList.remove("on");document.getElementById("drawer-ov").classList.remove("on");}
const TITLES={overview:["Vue d'ensemble","Théâtre global"],opps:["Opportunités","Avis de marché et signaux privés"],map:["Carte des théâtres","Répartition géographique"],attrib:["Attributions","Qui a gagné quoi en zone à risque"],firmo:["Entreprises","Fiches titulaires 360°"],geo:["Géopolitique","Alertes et posture"]};
function go(v){
  state.view=v;
  document.querySelectorAll(".nav a").forEach(a=>a.classList.toggle("on",a.dataset.view===v));
  document.querySelectorAll(".view").forEach(s=>s.classList.remove("on"));
  document.getElementById("v-"+v).classList.add("on");
  document.getElementById("top-title").textContent=TITLES[v][0];document.getElementById("top-crumb").textContent=TITLES[v][1];
  if(v==="overview"){renderTheatres();renderKPIs();renderCharts();renderFunnel();renderHot();}
  if(v==="opps")renderTable();if(v==="attrib")renderAttrib();
  if(v==="map")setTimeout(()=>{initMap();map.invalidateSize();},60);
}
function goZone(z){state.zone=z;document.getElementById("f-zone").value=z;go("opps");renderTable();}
function initFilters(){
  const uniq=k=>[...new Set(LEADS.map(l=>l[k]).filter(Boolean))].sort();
  uniq("zone").forEach(z=>document.getElementById("f-zone").innerHTML+=`<option>${z}</option>`);
  uniq("secteur").forEach(s=>document.getElementById("f-sect").innerHTML+=`<option>${s}</option>`);
  uniq("src").forEach(s=>document.getElementById("f-src").innerHTML+=`<option>${s}</option>`);
  document.getElementById("f-zone").onchange=e=>{state.zone=e.target.value;renderTable();};
  document.getElementById("f-sect").onchange=e=>{state.sect=e.target.value;renderTable();};
  document.getElementById("f-src").onchange=e=>{state.src=e.target.value;renderTable();};
  document.querySelectorAll("#f-prio button").forEach(b=>b.onclick=()=>{document.querySelectorAll("#f-prio button").forEach(x=>x.classList.remove("on"));b.classList.add("on");state.prio=b.dataset.p;renderTable();});
  document.querySelectorAll("thead th[data-sort]").forEach(th=>th.onclick=()=>{const k=th.dataset.sort;state.dir=(state.sort===k)?-state.dir:-1;state.sort=k;renderTable();});
  document.getElementById("search").oninput=e=>{state.q=e.target.value;if(state.view==="opps")renderTable();};
}
function resetFilters(){state.zone=state.sect=state.src=state.prio="";["f-zone","f-sect","f-src"].forEach(i=>document.getElementById(i).value="");document.querySelectorAll("#f-prio button").forEach((x,i)=>x.classList.toggle("on",i===0));renderTable();}
function exportCSV(){
  const rows=state.view==="opps"?filtered():LEADS;
  const head=["titre","zone","pays","secteur","valeur_meur","score","source","priorite","titulaire","origine"];
  const csv=[head.join(";")].concat(rows.map(l=>[l.titre,l.zone,l.pays,l.secteur,l.valeur,l.score,l.src,l.prio,l.titulaire,l.pays_tit].map(v=>`"${(""+v).replace(/"/g,'""')}"`).join(";"))).join("\n");
  const a=document.createElement("a");a.href="data:text/csv;charset=utf-8,"+encodeURIComponent(csv);a.download="radar_cockpit.csv";a.click();
}
document.getElementById("nav").addEventListener("click",e=>{const a=e.target.closest("a");if(a){go(a.dataset.view);}});
document.getElementById("cnt-opps").textContent=LEADS.filter(l=>l.src!=="ATTRIB").length;
document.getElementById("cnt-attrib").textContent=LEADS.filter(l=>l.src==="ATTRIB").length;
document.getElementById("run-meta").textContent=LEADS.length+" leads";
initFilters();go("overview");
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
