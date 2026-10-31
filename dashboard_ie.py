# -*- coding: utf-8 -*-
"""
Radar Amarante - TABLEAU DE BORD ARISE (chantier IE-2, 31/10/2026).

Page web AUTONOME pour le pole ARISE (intelligence economique & conformite),
ISOLEE de l'application Operations : elle ne lit que les tables du volet IE
(`radar_ie_leads`, `radar_ie_echeances`, `radar_ie_declencheurs`), jamais
`radar_lignes`. Aucune donnee Operations ne peut fuir ici, et un probleme ici
n'affecte pas l'app Operations.

Meme philosophie que radar_dashboard cote Operations : LIRE -> CONSTRUIRE ->
RENDRE. `generer_html` est une fonction PURE (aucun reseau), donc testable ;
`lire_tables` fait la seule I/O. La page est AUTO-SUFFISANTE (CSS et JS en
ligne, aucune dependance externe) : un seul fichier a publier.

La saisie d'ARISE (statut, commentaire) reste dans le classeur Google : ce
tableau de bord est la vue LISIBLE et filtrable des donnees collectees. Un
bouton de statut en base (comme le bouton Operations) serait un chantier
ulterieur.

LANCEMENT : python dashboard_ie.py  (ecrit public_ie/index.html)
  DATABASE_URL requis en production ; sans lui, page vide avec un bandeau.
"""

import datetime
import html
import os


COLONNES = {
    "radar_ie_leads": ("cle", "source", "id_avis", "date_detection",
                       "date_publication", "date_limite", "acheteur", "titre",
                       "lien", "classe", "termes", "famille", "perimetre",
                       "action", "score", "adequation", "commercial", "escalade",
                       "motifs", "resume"),
    "radar_ie_echeances": ("cle", "fin_estimee", "notification", "duree_mois",
                           "acheteur", "acheteur_id", "region", "objet",
                           "titulaires", "sirens", "position", "expertise",
                           "outil", "plafond", "montant_douteux", "nb_lots",
                           "offres", "lien", "renouvele", "successeur"),
    "radar_ie_declencheurs": ("cle", "type", "societe", "validation",
                              "juridiction", "statut", "programme", "afa",
                              "duree_mois", "fin_suivi", "lecture",
                              "lien_communique", "lien_convention"),
}


# ===========================================================================
# LECTURE (seule I/O ; best-effort, ne leve jamais)
# ===========================================================================
def lire_table(conn, table, colonnes):
    with conn.cursor() as cur:
        try:
            cur.execute("SELECT {} FROM {}".format(", ".join(colonnes), table))
        except Exception:                      # table absente : rien a montrer
            return []
        return [dict(zip(colonnes, row)) for row in cur.fetchall()]


def lire_tables(url):
    """(leads, echeances, declencheurs). Chaque table est best-effort :
    absente ou illisible -> liste vide, la page reste complete."""
    if not url:
        return [], [], []
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=15) as conn:
            return (lire_table(conn, "radar_ie_leads", COLONNES["radar_ie_leads"]),
                    lire_table(conn, "radar_ie_echeances", COLONNES["radar_ie_echeances"]),
                    lire_table(conn, "radar_ie_declencheurs", COLONNES["radar_ie_declencheurs"]))
    except Exception as e:  # noqa: BLE001
        print("  (info) base IE indisponible ({}).".format(str(e)[:120]))
        return [], [], []


# ===========================================================================
# PREPARATION (pur)
# ===========================================================================
def _f(v):
    return float(v) if v not in (None, "") else 0.0


def leads_affiches(leads):
    """Perimetre affiche + action actionnable, trie action puis score. C'est
    la meme regle que visible_arise du collecteur (contacter / surveiller)."""
    rang = {"contacter": 0, "surveiller": 1}
    vus = [l for l in leads
           if l.get("perimetre") == "affiche"
           and l.get("action") in ("contacter", "surveiller")]
    return sorted(vus, key=lambda l: (rang.get(l.get("action"), 9), -_f(l.get("score"))))


def echeances_affichees(ech, aujourd):
    """Echeances a venir (ou recemment passees), les plus proches d'abord.
    Les marches deja renouveles passent en fin (signal plus faible)."""
    def cle(e):
        fin = _date(e.get("fin_estimee"))
        return (1 if _vrai(e.get("renouvele")) else 0,
                fin or datetime.date.max)
    return sorted(ech, key=cle)


def declencheurs_affiches(decl):
    """Programme AFA confirme d'abord, puis a verifier, puis scans illisibles,
    chacun du plus recemment valide au plus ancien."""
    rang = {"programme_afa": 0, "a_verifier": 1, "illisible": 2}
    vus = [d for d in decl if d.get("statut") in rang]
    return sorted(vus, key=lambda d: (rang[d["statut"]], _tri_date(d.get("validation"))),
                  reverse=False)


def _tri_date(v):
    d = _date(v)
    return (datetime.date.max - d).days if d else 10 ** 9


def _date(v):
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _vrai(v):
    return str(v).strip().lower() in ("true", "1", "oui", "t")


# ===========================================================================
# RENDU (pur)
# ===========================================================================
def e(v):
    return html.escape(str(v if v is not None else "")).strip()


def _lien(url, libelle):
    url = e(url)
    if not url or not url.startswith(("http://", "https://")):
        return e(libelle)
    return '<a href="{}" target="_blank" rel="noopener">{}</a>'.format(url, e(libelle))


def _badge(txt, classe):
    return '<span class="badge {}">{}</span>'.format(classe, e(txt))


def rendre_leads(leads):
    if not leads:
        return '<p class="vide">Aucun lead a traiter pour le moment.</p>'
    lignes = []
    for l in leads:
        action = l.get("action", "")
        src = e(l.get("source"))
        lignes.append(
            '<tr data-source="{src}" data-action="{act}">'
            '<td>{badge}</td><td class="num">{score}</td><td>{fam}</td>'
            '<td>{src}</td><td>{ach}</td><td class="titre">{titre}<div class="resume">{resume}</div></td>'
            '<td>{lim}</td><td class="motifs">{motifs}</td></tr>'.format(
                src=src, act=e(action),
                badge=_badge(action, "a-" + e(action)),
                score=e(round(_f(l.get("score")), 1)), fam=e(l.get("famille")),
                ach=e(l.get("acheteur")),
                titre=_lien(l.get("lien"), l.get("titre") or "(sans titre)"),
                resume=e(l.get("resume")), lim=e(l.get("date_limite")),
                motifs=e(l.get("motifs"))))
    return (
        '<div class="filtres">'
        '<label>Source <select id="f-source" onchange="filtrer()">'
        '<option value="">toutes</option><option>BOAMP</option>'
        '<option>TED</option><option>UNGM</option></select></label>'
        '<label>Action <select id="f-action" onchange="filtrer()">'
        '<option value="">toutes</option><option value="contacter">a contacter</option>'
        '<option value="surveiller">a surveiller</option></select></label>'
        '<input id="f-texte" type="search" placeholder="filtrer par mot..." oninput="filtrer()">'
        '</div>'
        '<table id="t-leads"><thead><tr><th>Action</th><th>Score</th><th>Famille</th>'
        '<th>Source</th><th>Acheteur</th><th>Objet</th><th>Date limite</th>'
        '<th>Motifs</th></tr></thead><tbody>' + "".join(lignes) + '</tbody></table>')


def rendre_echeances(ech):
    if not ech:
        return '<p class="vide">Aucune echeance identifiee.</p>'
    POS = {"amarante_sortant": ("Amarante sortant", "pos-amarante"),
           "concurrent_suivi": ("Concurrent suivi", "pos-concurrent")}
    lignes = []
    for x in ech:
        pos_txt, pos_cls = POS.get(x.get("position"), ("Autre", "pos-autre"))
        renouv = ' <span class="tag">deja renouvele</span>' if _vrai(x.get("renouvele")) else ""
        plafond = x.get("plafond")
        montant = "" if (not plafond or _vrai(x.get("montant_douteux"))) else "{} EUR".format(e(plafond))
        lignes.append(
            '<tr><td>{fin}{renouv}</td><td>{badge}</td><td>{ach}</td>'
            '<td class="titre">{objet}</td><td>{tit}</td><td>{reg}</td>'
            '<td class="num">{montant}</td></tr>'.format(
                fin=e(x.get("fin_estimee")), renouv=renouv,
                badge=_badge(pos_txt, pos_cls), ach=e(x.get("acheteur")),
                objet=_lien(x.get("lien"), x.get("objet") or "(sans objet)"),
                tit=e(x.get("titulaires")), reg=e(x.get("region")), montant=montant))
    return ('<table><thead><tr><th>Fin estimee</th><th>Position</th><th>Acheteur</th>'
            '<th>Objet</th><th>Titulaire(s)</th><th>Region</th><th>Plafond</th>'
            '</tr></thead><tbody>' + "".join(lignes) + '</tbody></table>')


def rendre_declencheurs(decl):
    if not decl:
        return '<p class="vide">Aucun declencheur CJIP recent.</p>'
    STAT = {"programme_afa": ("Programme AFA", "st-afa"),
            "a_verifier": ("A verifier", "st-verif"),
            "illisible": ("PDF scanne, a lire", "st-scan")}
    lignes = []
    for d in decl:
        txt, cls = STAT.get(d.get("statut"), (d.get("statut"), "st-verif"))
        liens = " ".join(filter(None, [
            _lien(d.get("lien_communique"), "communique") if d.get("lien_communique") else "",
            _lien(d.get("lien_convention"), "convention") if d.get("lien_convention") else ""]))
        lignes.append(
            '<tr><td>{val}</td><td>{badge}</td><td>{soc}</td><td>{fin}</td>'
            '<td>{jur}</td><td class="liens">{liens}</td></tr>'.format(
                val=e(d.get("validation")), badge=_badge(txt, cls),
                soc=e(d.get("societe")), fin=e(d.get("fin_suivi")),
                jur=e(d.get("juridiction")), liens=liens or "-"))
    return ('<table><thead><tr><th>Validee le</th><th>Statut</th><th>Societe</th>'
            '<th>Fin suivi AFA</th><th>Juridiction</th><th>Documents</th>'
            '</tr></thead><tbody>' + "".join(lignes) + '</tbody></table>')


STYLE = """
:root{--bg:#f4f5f7;--carte:#fff;--txt:#1c2431;--doux:#5b6676;--trait:#e3e7ee;
--accent:#1f6feb;--contacter:#b42318;--surveiller:#b54708;--ok:#067647}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);
font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
header{background:#0f1b2d;color:#fff;padding:22px 28px}
header h1{margin:0;font-size:20px;letter-spacing:.3px}
header p{margin:6px 0 0;color:#9fb0c7;font-size:13px}
main{padding:20px 28px 60px;max-width:1280px;margin:0 auto}
section{background:var(--carte);border:1px solid var(--trait);border-radius:12px;
margin:18px 0;padding:18px 20px;box-shadow:0 1px 2px rgba(16,24,40,.04)}
section h2{margin:0 0 4px;font-size:16px}
section .sous{margin:0 0 14px;color:var(--doux);font-size:13px}
.compteur{display:inline-block;margin-left:8px;background:#eef2f8;color:var(--accent);
border-radius:20px;padding:1px 10px;font-size:12px;font-weight:600}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--trait);vertical-align:top}
th{position:sticky;top:0;background:var(--carte);color:var(--doux);font-weight:600;
font-size:12px;text-transform:uppercase;letter-spacing:.3px}
tr:hover td{background:#fafbfd}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.titre{max-width:420px}.resume{color:var(--doux);font-size:12.5px;margin-top:3px}
td.motifs{color:var(--doux);font-size:12px;max-width:220px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.badge{display:inline-block;padding:2px 9px;border-radius:20px;font-size:11.5px;
font-weight:600;white-space:nowrap}
.a-contacter{background:#fef3f2;color:var(--contacter)}
.a-surveiller{background:#fffaeb;color:var(--surveiller)}
.pos-amarante{background:#ecfdf3;color:var(--ok)}
.pos-concurrent{background:#fef3f2;color:var(--contacter)}
.pos-autre{background:#eef2f8;color:var(--doux)}
.st-afa{background:#fef3f2;color:var(--contacter)}
.st-verif{background:#fffaeb;color:var(--surveiller)}
.st-scan{background:#eef2f8;color:var(--doux)}
.tag{background:#eef2f8;color:var(--doux);border-radius:6px;padding:0 6px;font-size:11px}
.filtres{display:flex;gap:14px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
.filtres select,.filtres input{font:inherit;padding:6px 9px;border:1px solid var(--trait);
border-radius:8px;background:#fff}.filtres input{min-width:200px}
.vide{color:var(--doux);font-style:italic;padding:8px 0}
footer{color:var(--doux);font-size:12px;text-align:center;padding:24px}
@media(max-width:720px){td.titre{max-width:none}.resume{display:none}header,main{padding-left:16px;padding-right:16px}}
"""

SCRIPT = """
function filtrer(){
 var s=document.getElementById('f-source').value.toLowerCase();
 var a=document.getElementById('f-action').value;
 var q=document.getElementById('f-texte').value.toLowerCase();
 document.querySelectorAll('#t-leads tbody tr').forEach(function(tr){
  var okS=!s||tr.dataset.source.toLowerCase()===s;
  var okA=!a||tr.dataset.action===a;
  var okQ=!q||tr.textContent.toLowerCase().indexOf(q)>-1;
  tr.style.display=(okS&&okA&&okQ)?'':'none';});
}
"""


def generer_html(leads, echeances, declencheurs, genere_le=None):
    """Page complete, auto-suffisante. Fonction PURE."""
    genere_le = genere_le or datetime.date.today()
    la = leads_affiches(leads)
    le = echeances_affichees(echeances, genere_le if isinstance(genere_le, datetime.date)
                             else datetime.date.today())
    ld = declencheurs_affiches(declencheurs)
    stamp = genere_le.isoformat() if isinstance(genere_le, datetime.date) else str(genere_le)

    def compteur(n):
        return '<span class="compteur">{}</span>'.format(n)

    return """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Radar ARISE — Intelligence economique & conformite</title>
<style>{style}</style></head><body>
<header><h1>Radar ARISE — Intelligence economique &amp; conformite</h1>
<p>Marches et declencheurs captes pour le pole ARISE. Genere le {stamp}.
La saisie (statut, commentaire) reste dans le classeur ARISE.</p></header>
<main>
<section><h2>Leads a traiter{c_leads}</h2>
<p class="sous">Appels d'offres ouverts (BOAMP, TED hors France, UNGM), a contacter ou a surveiller.</p>
{leads}</section>
<section><h2>Echeances de marches{c_ech}</h2>
<p class="sous">Marches IE deja attribues dont la fin approche : fenetre de reconquete, ou signal concurrent.</p>
{ech}</section>
<section><h2>Declencheurs CJIP{c_decl}</h2>
<p class="sous">Conventions judiciaires d'interet public imposant un programme de conformite sous controle de l'AFA : demande privee a venir.</p>
{decl}</section>
</main>
<footer>Radar Amarante — volet IE. Donnees internes, usage ARISE uniquement.</footer>
<script>{script}</script></body></html>""".format(
        style=STYLE, script=SCRIPT, stamp=e(stamp),
        c_leads=compteur(len(la)), c_ech=compteur(len(le)), c_decl=compteur(len(ld)),
        leads=rendre_leads(la), ech=rendre_echeances(le), decl=rendre_declencheurs(ld))


def main():
    url = os.environ.get("DATABASE_URL")
    sortie = os.environ.get("IE_DASHBOARD_SORTIE", "public_ie")
    leads, ech, decl = lire_tables(url)
    html_page = generer_html(leads, ech, decl)
    os.makedirs(sortie, exist_ok=True)
    chemin = os.path.join(sortie, "index.html")
    with open(chemin, "w", encoding="utf-8") as f:
        f.write(html_page)
    print("Tableau de bord ARISE ecrit : {} ({} leads, {} echeances, {} declencheurs).".format(
        chemin, len(leads_affiches(leads)), len(ech), len(declencheurs_affiches(decl))))
    resume = os.environ.get("GITHUB_STEP_SUMMARY")
    if resume:
        with open(resume, "a", encoding="utf-8") as f:
            f.write("Tableau de bord ARISE genere ({} leads affiches).\n".format(
                len(leads_affiches(leads))))


if __name__ == "__main__":
    main()
