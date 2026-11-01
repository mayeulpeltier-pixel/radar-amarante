# -*- coding: utf-8 -*-
"""
Radar Amarante - REGISTRE DES PLATEFORMES D'ACHATS (chantier P2, 01/11/2026).

LE PROBLEME QUE CE MODULE RESOUD
--------------------------------
Le tableau de sante (`sante_sources.py`) dit si une source BRANCHEE ramene
encore quelque chose. Il ne dit rien des plateformes qu'on N'a PAS branchees,
ni POURQUOI : une plateforme accessible mais au rendement nul (Ariba), une
plateforme bloquee (ACPR, AfDB RSS), une plateforme sans API exploitable
(plan-vigilance). Ce savoir se perd et se re-decouvre a chaque fois -- c'est
exactement ce qui a fait perdre des tours sur UNGM et Ariba.

Ce registre est la MEMOIRE INSTITUTIONNELLE des sources : pour chaque
plateforme, son pole, sa nature, son mode d'ACCES, son collecteur eventuel,
son etat, son RENDEMENT MESURE, et la decision. Il capture surtout ce que la
sante ignore : l'acces et le rendement, y compris pour les plateformes sans
collecteur.

CE QU'IL N'EST PAS
------------------
Pas un collecteur (aucune collecte, aucun LLM). Pas un doublon de la sante :
il la COMPLETE. En option, il joint le volume courant en base (lecture seule,
best-effort) comme controle de realite -- mais sa valeur est le catalogue cure,
pas le chiffre live.

Donnees = faits etablis (CLAUDE.md, sondes, runs). Toute colonne non verifiee
reste vide plutot que devinee (lecon : pas de chiffre d'apparence savante).
"""

import csv
import datetime
import html
import os

# Vocabulaires fermes (alignes sur les classes de sante_sources).
POLES = ("Opérations", "IE", "Les deux")
ETATS = ("actif", "lent", "désactivé", "bloqué", "sonde", "écarté")
RENDEMENTS = ("élevé", "moyen", "faible", "nul", "amont", "non mesuré")

# Couleur d'etat pour la vue HTML (le reste herite du theme).
COULEUR_ETAT = {
    "actif": "#2e7d32", "lent": "#f9a825", "désactivé": "#9e9e9e",
    "bloqué": "#c62828", "sonde": "#1565c0", "écarté": "#6d4c41",
}

# ===========================================================================
# LE REGISTRE (donnees curees)
# ===========================================================================
# Champs : cle, nom, pole, categorie, acces, collecteur (fichier ou ""),
#          onglet (table/onglet ou ""), etat, rendement, evalue_le, note.
REGISTRE = [
    # --- Opérations : appels d'offres publics / multilatéraux ---
    {"cle": "ted", "nom": "TED (UE)", "pole": "Opérations",
     "categorie": "Appels d'offres", "acces": "API publique (XML/REST)",
     "collecteur": "ted_complet_v14.py", "onglet": "ted_radar",
     "etat": "actif", "rendement": "élevé", "evalue_le": "",
     "note": "Cœur du système ; contrat d'interface vérifié par radar_run."},
    {"cle": "bm", "nom": "Banque Mondiale (avis)", "pole": "Opérations",
     "categorie": "Appels d'offres", "acces": "Portail rendu serveur",
     "collecteur": "ted_complet_bm.py", "onglet": "bm_radar",
     "etat": "actif", "rendement": "moyen", "evalue_le": "",
     "note": "Budget LLM 150/run. Attributions aussi (PDF fragile)."},
    {"cle": "reliefweb", "nom": "ReliefWeb (humanitaire)", "pole": "Opérations",
     "categorie": "Signaux humanitaires", "acces": "API publique",
     "collecteur": "ted_complet_reliefweb.py", "onglet": "reliefweb_radar",
     "etat": "actif", "rendement": "moyen", "evalue_le": "",
     "note": "Appname requis. Budget 120/run."},
    {"cle": "ebrd", "nom": "EBRD (ECEPP)", "pole": "Opérations",
     "categorie": "Appels d'offres", "acces": "Portail rendu serveur",
     "collecteur": "ebrd_radar.py", "onglet": "ebrd_radar",
     "etat": "actif", "rendement": "moyen", "evalue_le": "",
     "note": "Budget EBRD."},
    {"cle": "ungm", "nom": "UNGM (agences ONU)", "pole": "Opérations",
     "categorie": "Appels d'offres", "acces": "Portail rendu serveur",
     "collecteur": "ungm_radar.py", "onglet": "ungm_radar",
     "etat": "actif", "rendement": "moyen", "evalue_le": "",
     "note": "Avis + attributions. Leçon : sonde avant collecteur."},
    {"cle": "afdb_rss", "nom": "AfDB (avis RSS)", "pole": "Opérations",
     "categorie": "Appels d'offres", "acces": "Flux RSS",
     "collecteur": "afdb_radar.py", "onglet": "afdb_radar",
     "etat": "bloqué", "rendement": "non mesuré", "evalue_le": "2026-10-29",
     "note": "Défi Cloudflare, HTTP 403 depuis le 29/10 ; réessaie chaque run."},
    {"cle": "adb", "nom": "ADB (Asie-Pacifique)", "pole": "Opérations",
     "categorie": "Appels d'offres", "acces": "API publique (JSON SearchStax)",
     "collecteur": "adb_radar.py", "onglet": "adb_radar",
     "etat": "actif", "rendement": "moyen", "evalue_le": "2026-11-01",
     "note": "Réactivé le 01/11 via l'API JSON SearchStax (le portail est en JS). "
             "162 avis en zone suivie à la sonde ; 1er run prod réussi. "
             "RADAR_ADB=0 pour recouper."},
    {"cle": "boamp", "nom": "BOAMP (France)", "pole": "Opérations",
     "categorie": "Appels d'offres", "acces": "Portail rendu serveur",
     "collecteur": "ted_complet_boamp.py", "onglet": "boamp_radar",
     "etat": "écarté", "rendement": "nul", "evalue_le": "",
     "note": "Rendement nul mesuré, code conservé (motif ADB)."},
    {"cle": "prozorro", "nom": "Prozorro (Ukraine)", "pole": "Opérations",
     "categorie": "Appels d'offres", "acces": "API publique",
     "collecteur": "", "onglet": "prozorro_radar",
     "etat": "sonde", "rendement": "non mesuré", "evalue_le": "",
     "note": "Jamais branché en production : sonde seule, aucun collecteur."},
    # --- Opérations : attributions (titulaires -> prospects mobilisation) ---
    {"cle": "isdb", "nom": "IsDB (attributions)", "pole": "Opérations",
     "categorie": "Attributions", "acces": "Portail rendu serveur",
     "collecteur": "isdb_radar.py", "onglet": "attributions_radar",
     "etat": "actif", "rendement": "faible", "evalue_le": "",
     "note": "Pays dérivé du préfixe du code projet (filtre serveur ignoré)."},
    # --- Opérations : financement amont (DFI) ---
    {"cle": "bm_projets", "nom": "Banque Mondiale (projets amont)", "pole": "Opérations",
     "categorie": "Financement amont", "acces": "API publique",
     "collecteur": "bm_projets.py", "onglet": "bm_projets_radar",
     "etat": "lent", "rendement": "amont", "evalue_le": "",
     "note": "search.worldbank.org/api/v3/projects (sonde confirmée)."},
    {"cle": "afdb_projets", "nom": "AfDB (projets amont, IATI)", "pole": "Opérations",
     "categorie": "Financement amont", "acces": "API publique (IATI/d-portal)",
     "collecteur": "afdb_projets.py", "onglet": "afdb_projets_radar",
     "etat": "lent", "rendement": "amont", "evalue_le": "",
     "note": "Repli du RSS bloqué, signal amont, sans LLM."},
    {"cle": "ifc", "nom": "IFC (divulgations)", "pole": "Opérations",
     "categorie": "Financement amont", "acces": "API publique (JSON)",
     "collecteur": "ifc_radar.py", "onglet": "ifc_radar",
     "etat": "lent", "rendement": "amont", "evalue_le": "",
     "note": "API derrière le SPA disclosures.ifc.org (webapi.worldbank.org)."},
    {"cle": "miga", "nom": "MIGA (garanties)", "pole": "Opérations",
     "categorie": "Financement amont", "acces": "Portail/JSON",
     "collecteur": "miga_radar.py", "onglet": "miga_radar",
     "etat": "lent", "rendement": "amont", "evalue_le": "",
     "note": "Acteur toujours étranger au pays hôte : cœur de cible."},
    {"cle": "proparco", "nom": "Proparco (AFD)", "pole": "Opérations",
     "categorie": "Financement amont", "acces": "API publique (Opendatasoft)",
     "collecteur": "proparco_radar.py", "onglet": "proparco_radar",
     "etat": "lent", "rendement": "amont", "evalue_le": "",
     "note": "opendata.afd.fr, sans clé, filtrage serveur. Fenêtre 730 j."},
    # --- Opérations : signaux privés ---
    {"cle": "signaux_prives", "nom": "Signaux privés (watchlist/Adzuna/News)",
     "pole": "Opérations", "categorie": "Signaux privés",
     "acces": "API publique + watchlist", "collecteur": "signaux_prives.py",
     "onglet": "prive_radar", "etat": "actif", "rendement": "moyen",
     "evalue_le": "2026-11-01",
     "note": "Watchlist 408, Adzuna, Google News. Miroir Postgres réparé le 01/11."},
    {"cle": "gdelt", "nom": "GDELT", "pole": "Opérations",
     "categorie": "Signaux", "acces": "API publique",
     "collecteur": "", "onglet": "", "etat": "désactivé",
     "rendement": "non mesuré", "evalue_le": "",
     "note": "RADAR_GDELT=0."},
    # --- IE / ARISE ---
    {"cle": "ie_lentille", "nom": "Lentille IE (BOAMP/TED/UNGM)", "pole": "IE",
     "categorie": "Appels d'offres", "acces": "Hérité des sources amont",
     "collecteur": "collecteur_ie.py", "onglet": "radar_ie_leads",
     "etat": "actif", "rendement": "moyen", "evalue_le": "",
     "note": "Préfiltre lexical FR puis grille LLM. Pile IE isolée."},
    {"cle": "cjip", "nom": "Déclencheurs CJIP (PNF)", "pole": "IE",
     "categorie": "Déclencheurs", "acces": "Portail rendu serveur",
     "collecteur": "declencheurs_ie.py", "onglet": "radar_ie_declencheurs",
     "etat": "actif", "rendement": "faible", "evalue_le": "",
     "note": "Conventions judiciaires d'intérêt public."},
    {"cle": "amf", "nom": "Sanctions AMF", "pole": "IE",
     "categorie": "Déclencheurs", "acces": "Portail rendu serveur (sitemap+PDF)",
     "collecteur": "declencheurs_amf_ie.py", "onglet": "radar_ie_declencheurs",
     "etat": "actif", "rendement": "faible", "evalue_le": "",
     "note": "~6 décisions pertinentes/an. ACPR écartée (voir ci-dessous)."},
    {"cle": "sirene_vigilance", "nom": "SIRENE (cartographie vigilance)",
     "pole": "IE", "categorie": "Cartographie", "acces": "API publique (SIRENE)",
     "collecteur": "cartographie_vigilance_ie.py", "onglet": "radar_ie_vigilance",
     "etat": "lent", "rendement": "amont", "evalue_le": "",
     "note": "Entreprises ≥5000 salariés assujetties au devoir de vigilance."},
    {"cle": "recherche_entreprises", "nom": "recherche-entreprises.api.gouv.fr",
     "pole": "IE", "categorie": "Enrichissement", "acces": "API publique (sans clé)",
     "collecteur": "enrichir_ie.py", "onglet": "radar_ie_enrichissement",
     "etat": "actif", "rendement": "moyen", "evalue_le": "",
     "note": "SIREN, dirigeants, NAF, ville. Flaky réseau par intermittence."},
    {"cle": "sanctions_bm", "nom": "Sanctions Banque Mondiale (débarment)",
     "pole": "IE", "categorie": "Sanctions", "acces": "API publique (clé consultation)",
     "collecteur": "sanctions_ie.py", "onglet": "radar_ie_sanctions",
     "etat": "actif", "rendement": "amont", "evalue_le": "",
     "note": "Registre public officiel, ~1540 entités."},
    # --- Appels d'offres privés : mesurés / écartés (mémoire des impasses) ---
    {"cle": "ariba", "nom": "SAP Ariba Discovery", "pole": "Les deux",
     "categorie": "Appels d'offres privés", "acces": "API publique (Discovery)",
     "collecteur": "collecteur_ariba_ie.py", "onglet": "radar_ie_leads",
     "etat": "écarté", "rendement": "nul", "evalue_le": "2026-10-31",
     "note": "RADAR_IE_ARIBA=0. API accessible mais rendement nul pour Amarante "
             "(sonde : 3/153 pertinents, 0 en zone). Collecteur conservé (motif ADB)."},
    {"cle": "acpr", "nom": "ACPR / Banque de France", "pole": "IE",
     "categorie": "Déclencheurs", "acces": "Bloqué", "collecteur": "",
     "onglet": "", "etat": "écarté", "rendement": "non mesuré", "evalue_le": "",
     "note": "HTTP 403 même depuis GitHub Actions. Écartée au profit de l'AMF."},
    {"cle": "plan_vigilance", "nom": "plan-vigilance.org", "pole": "IE",
     "categorie": "Cartographie", "acces": "Portail JS (pas d'API)",
     "collecteur": "", "onglet": "", "etat": "écarté", "rendement": "non mesuré",
     "evalue_le": "", "note": "Sonde Playwright : aucune API JSON exploitable."},
]


# ===========================================================================
# VALIDATION (pur, testable)
# ===========================================================================
CHAMPS_REQUIS = ("cle", "nom", "pole", "categorie", "acces", "collecteur",
                 "onglet", "etat", "rendement", "evalue_le", "note")


def valider(registre=REGISTRE):
    """Renvoie la liste des anomalies (vide = registre sain). Fonction PURE.

    Verrou de qualité : clés uniques, champs présents, vocabulaires fermés
    respectés. Une entrée mal typée fausserait la vue et les décisions."""
    anomalies = []
    vues = set()
    for i, e in enumerate(registre):
        for c in CHAMPS_REQUIS:
            if c not in e:
                anomalies.append("entrée {} : champ manquant '{}'".format(i, c))
        cle = e.get("cle", "")
        if not cle:
            anomalies.append("entrée {} : clé vide".format(i))
        elif cle in vues:
            anomalies.append("clé dupliquée : '{}'".format(cle))
        vues.add(cle)
        if e.get("pole") not in POLES:
            anomalies.append("{} : pôle invalide '{}'".format(cle, e.get("pole")))
        if e.get("etat") not in ETATS:
            anomalies.append("{} : état invalide '{}'".format(cle, e.get("etat")))
        if e.get("rendement") not in RENDEMENTS:
            anomalies.append("{} : rendement invalide '{}'".format(cle, e.get("rendement")))
    return anomalies


# ===========================================================================
# ENRICHISSEMENT LIVE (pur, injectable) -- contrôle de réalité
# ===========================================================================
def enrichir_live(registre, par_onglet):
    """Joint le volume courant en base a chaque entree, par onglet. PURE.

    `par_onglet` : {onglet: {"lignes": int|None, "dernier": "AAAA-MM-JJ"|None}}.
    Une entree sans collecteur, ou dont l'onglet est absent de la base, recoit
    volume=None (affiche « — ») : c'est normal, pas une anomalie."""
    out = []
    for e in registre:
        info = par_onglet.get(e.get("onglet")) if e.get("onglet") else None
        out.append(dict(e,
                        volume=(info or {}).get("lignes"),
                        dernier=(info or {}).get("dernier")))
    return out


def par_onglet_depuis_sante(stats):
    """Transforme les stats de sante_sources en index {onglet: {lignes, dernier}}.
    La source de sante est « Operations / <onglet> » ou un libellé IE ; on prend
    le dernier segment. Fonction PURE."""
    idx = {}
    for s in (stats or []):
        onglet = str(s.get("source", "")).split(" / ")[-1]
        if onglet:
            idx[onglet] = {"lignes": s.get("lignes"), "dernier": s.get("dernier")}
    return idx


# ===========================================================================
# RAPPORTS
# ===========================================================================
def _groupes(registre):
    """{pole: [entrées]} en préservant l'ordre des pôles."""
    g = {p: [] for p in POLES}
    for e in registre:
        g.setdefault(e.get("pole", "?"), []).append(e)
    return {p: v for p, v in g.items() if v}


def rapport_markdown(registre, aujourd):
    tete = ["# Registre des plateformes d'achats", "",
            "Généré le {}. {} plateformes.".format(aujourd.isoformat(), len(registre)),
            "", "| Plateforme | Pôle | Catégorie | Accès | État | Rendement | Collecteur | Volume base |",
            "|---|---|---|---|---|---|---|---|"]
    for e in registre:
        vol = e.get("volume")
        tete.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
            e["nom"], e["pole"], e["categorie"], e["acces"], e["etat"],
            e["rendement"], e["collecteur"] or "—",
            "—" if vol is None else vol))
    return "\n".join(tete) + "\n"


def rapport_html(registre, aujourd):
    """Vue HTML autonome (préférence Mayeul), groupée par pôle, pastille d'état."""
    def esc(v):
        return html.escape(str(v if v is not None else ""))
    lignes = []
    for pole, entrees in _groupes(registre).items():
        lignes.append('<h2>{}</h2>'.format(esc(pole)))
        lignes.append('<table><thead><tr>'
                      '<th>Plateforme</th><th>Catégorie</th><th>Accès</th>'
                      '<th>État</th><th>Rendement</th><th>Collecteur</th>'
                      '<th>Onglet</th><th>Vol. base</th><th>Note</th>'
                      '</tr></thead><tbody>')
        for e in entrees:
            vol = e.get("volume")
            pastille = ('<span class="etat" style="background:{}">{}</span>'
                        .format(COULEUR_ETAT.get(e["etat"], "#607d8b"), esc(e["etat"])))
            lignes.append(
                "<tr><td><b>{}</b></td><td>{}</td><td>{}</td><td>{}</td>"
                "<td>{}</td><td class='mono'>{}</td><td class='mono'>{}</td>"
                "<td class='num'>{}</td><td class='note'>{}</td></tr>".format(
                    esc(e["nom"]), esc(e["categorie"]), esc(e["acces"]), pastille,
                    esc(e["rendement"]), esc(e["collecteur"] or "—"),
                    esc(e["onglet"] or "—"), "—" if vol is None else esc(vol),
                    esc(e["note"])))
        lignes.append("</tbody></table>")
    corps = "\n".join(lignes)
    return """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Registre des plateformes d'achats</title>
<style>
:root{{--bg:#fff;--fg:#1a1a1a;--bd:#e0e0e0;--head:#f5f5f5;--mut:#666}}
@media(prefers-color-scheme:dark){{:root{{--bg:#16181c;--fg:#e8e8e8;--bd:#2c2f36;--head:#20242b;--mut:#9aa0a6}}}}
*{{box-sizing:border-box}}
body{{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}}
.wrap{{max-width:1180px;margin:0 auto}}
h1{{font-size:1.5rem;margin:0 0 4px}}
.sous{{color:var(--mut);margin:0 0 24px}}
h2{{font-size:1.05rem;margin:28px 0 8px;border-bottom:2px solid var(--bd);padding-bottom:4px}}
table{{width:100%;border-collapse:collapse;margin-bottom:8px;font-size:13px}}
th,td{{text-align:left;padding:7px 9px;border-bottom:1px solid var(--bd);vertical-align:top}}
th{{background:var(--head);font-weight:600;position:sticky;top:0}}
.mono{{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:var(--mut)}}
.num{{text-align:right;font-variant-numeric:tabular-nums}}
.note{{color:var(--mut);max-width:340px}}
.etat{{display:inline-block;padding:2px 8px;border-radius:10px;color:#fff;
font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.03em}}
</style></head><body><div class="wrap">
<h1>Registre des plateformes d'achats</h1>
<p class="sous">Généré le {date}. {n} plateformes. Mémoire institutionnelle des sources : accès, état, rendement mesuré, décision. Le volume en base, quand il existe, est un contrôle de réalité (lecture seule).</p>
{corps}
</div></body></html>""".format(date=aujourd.isoformat(), n=len(registre), corps=corps)


def main():
    aujourd = datetime.date.today()
    anomalies = valider(REGISTRE)
    if anomalies:
        print("REGISTRE INVALIDE :")
        for a in anomalies:
            print("  - " + a)
        raise SystemExit(1)

    # Contrôle de réalité : volume courant en base, best-effort (jamais bloquant).
    registre = REGISTRE
    url = os.environ.get("DATABASE_URL")
    if url:
        try:
            import sante_sources
            idx = par_onglet_depuis_sante(sante_sources.collecter(url))
            registre = enrichir_live(REGISTRE, idx)
        except Exception as e:  # noqa: BLE001
            print("  (info) volume base indisponible ({}).".format(str(e)[:120]))
            registre = enrichir_live(REGISTRE, {})
    else:
        registre = enrichir_live(REGISTRE, {})

    sortie = os.environ.get("REGISTRE_SORTIE") or "registre_plateformes_sortie"
    os.makedirs(sortie, exist_ok=True)
    with open(os.path.join(sortie, "registre.html"), "w", encoding="utf-8") as f:
        f.write(rapport_html(registre, aujourd))
    md = rapport_markdown(registre, aujourd)
    with open(os.path.join(sortie, "registre.md"), "w", encoding="utf-8") as f:
        f.write(md)
    with open(os.path.join(sortie, "registre.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(CHAMPS_REQUIS + ("volume", "dernier"))
        for e in registre:
            w.writerow([e.get(c, "") for c in CHAMPS_REQUIS]
                       + [e.get("volume"), e.get("dernier")])
    print(md)
    resume_env = os.environ.get("GITHUB_STEP_SUMMARY")
    if resume_env:
        with open(resume_env, "a", encoding="utf-8") as f:
            f.write(md)


if __name__ == "__main__":
    main()
