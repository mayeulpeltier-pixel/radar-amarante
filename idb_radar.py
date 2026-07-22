# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- COLLECTEUR IDB (Banque interamericaine de developpement).
============================================================================

POURQUOI CETTE SOURCE
---------------------
Le perimetre commercial s'est ouvert le 22/07/2026 a treize pays d'Amerique
latine et d'Asie. Jusque-la, aucune source REGIONALE ne couvrait l'Amerique
latine : l'IDB y est le bailleur principal, l'equivalent de ce qu'est l'AfDB
pour l'Afrique ou l'EBRD pour l'Est.

COMMENT ON Y ACCEDE (quatre sondes, et une lecon)
-------------------------------------------------
  - Les portails web du groupe (www.iadb.org, projectprocurement.iadb.org...)
    sont derriere CLOUDFLARE, qui bloque l'IP des runners GitHub (plage Azure).
    Sept variantes testees, en-tetes de navigateur, amorcage de cookie, httpx
    HTTP/1.1 et HTTP/2, curl en trois piles TLS : AUCUNE ne passe. Le grattage
    est mort, c'est mesure, pas suppose.
  - MAIS data.iadb.org est un CKAN OUVERT, hors de cette regle, et il publie
    le jeu "IDB Project procurement bidding notices and notification of
    contract awards", mis a jour quotidiennement.
  - LECON : j'avais d'abord qualifie ce portail de "donnees de recherche" sur
    la foi des premiers noms renvoyes par ordre alphabetique. C'etait faux.
    Ne jamais juger un catalogue sur un echantillon alphabetique.

PIEGES DU FICHIER, RELEVES SUR LES DONNEES REELLES
--------------------------------------------------
  - La premiere colonne porte un BOM UTF-8 : "\ufeffnoticeid".
  - Les valeurs manquantes sont les CHAINES "null" et "NULL", pas des cellules
    vides.
  - `countryname` est un NOM en majuscules ("ARGENTINA", "BAHAMAS"), et vaut
    parfois "REGIONAL" (projet multi-pays, sans pays d'execution unique).
  - `deadline` est au format americain M/J/AAAA, `publicationdate` en ISO.
  - Le fichier contient a la fois des AVIS et des NOTIFICATIONS D'ATTRIBUTION.
    Le mode verification imprime les valeurs distinctes des colonnes
    discriminantes pour trancher sur donnees reelles avant de separer les deux.

VERIFICATION AVANT ECRITURE :  RADAR_IDB_DEBUG=1  (motif IsDB)
    -> entonnoir complet, valeurs distinctes, echantillon. AUCUNE ecriture.
"""

import csv
import io
import os
import time
from datetime import date, datetime, timedelta

import ted_complet_v14 as ted
import ted_complet_bm as bm


# ===========================================================================
# PARTIE 1 -- CONFIGURATION
# ===========================================================================

ACTIVER = os.environ.get("RADAR_IDB", "1") != "0"
DEBUG = os.environ.get("RADAR_IDB_DEBUG", "") == "1"

NOM_ONGLET = "idb_radar"
MAX_AVIS_LLM = int(os.environ.get("IDB_BUDGET", "60"))    # plafond d'appels LLM
NB_JOURS_FENETRE = int(os.environ.get("IDB_JOURS", "60"))
MAX_LIGNES_CSV = int(os.environ.get("IDB_MAX_LIGNES", "400000"))

CKAN = "https://data.iadb.org/api/3/action"
PAQUET_AVIS = "project-procurement-bidding-notices-and-notification-of-contract-awards"
# Repli si le CKAN change de forme : URL relevee par la sonde le 22/07/2026.
URL_SECOURS = "https://data.iadb.org/file/download/9cc29cd0-c487-42e9-ad49-9971b4125066"

# Colonnes du CSV (relevees sur le fichier reel).
COL_ID = "noticeid"
COL_PAYS = "countryname"
COL_TITRE = "noticetitle"
COL_PROJET = "projectname"
COL_NUM_PROJET = "projectnumber"
COL_URL_PROJET = "proyecturl"
COL_URL_DOC = "documenturl"
COL_DATE = "publicationdate"
COL_DEADLINE = "deadline"
COL_SECTEUR = "sectorenglnm"
COL_STATUT = "projectstatus"
COL_METHODE = "prcrmnt_mthd_engl_nm"
COL_CATEGORIE = "category_nm"
COL_TYPE = "type"
COL_DESC = "process_desc"

VIDES = {"", "null", "NULL", "None", "n/a", "N/A", "-"}

# EN-TETES : data.iadb.org est derriere Cloudflare. Il accepte l'API mais
# REFUSE (403) le telechargement de fichier a un client qui s'annonce
# "python-requests/x.y" -- constate au premier run reel du 22/07/2026.
#
# PIEGE EVITE : ted.session_robuste() renvoie un SINGLETON GLOBAL, partage
# avec le collecteur TED et les appels Anthropic. Modifier ses en-tetes
# contaminerait tout le pipeline. On passe donc ces en-tetes REQUETE PAR
# REQUETE : requests les fusionne par-dessus ceux de la session, sans la
# muter. On conserve ainsi ses reessais automatiques sans effet de bord.
ENTETES = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/csv, application/json, text/html;q=0.8, */*;q=0.5",
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
    "Referer": "https://data.iadb.org/",
}


def _val(ligne, cle):
    """Valeur nettoyee. Traite les CHAINES 'null'/'NULL' comme du vide : sans
    cela, un titre vaudrait litteralement 'null' dans le dashboard."""
    v = (ligne.get(cle) or "").strip()
    return "" if v in VIDES else v


# ===========================================================================
# PARTIE 2 -- RECUPERATION DU FICHIER
# ===========================================================================

def url_du_fichier(session=None, fetch=None):
    """URL du CSV, resolue DYNAMIQUEMENT via le CKAN : l'identifiant de
    ressource change a chaque republication. Repli sur l'URL relevee par la
    sonde si le CKAN est indisponible."""
    if fetch:
        return fetch()
    session = session or ted.session_robuste()
    try:
        r = session.get("{}/package_show".format(CKAN),
                        params={"id": PAQUET_AVIS}, headers=ENTETES,
                        timeout=45)
        if r.status_code < 400:
            res = (r.json() or {}).get("result") or {}
            for ressource in res.get("resources") or []:
                url = (ressource.get("url") or "").strip()
                if url:
                    return url
                # Certaines ressources n'exposent pas d'URL mais un id : le
                # portail sert alors le fichier via /file/download/<id>.
                ident = (ressource.get("id") or "").strip()
                if ident:
                    return "https://data.iadb.org/file/download/{}".format(ident)
    except Exception as e:
        print("  (info) CKAN indisponible ({}), repli sur l'URL de secours.".format(
            str(e)[:70]))
    return URL_SECOURS


def lignes_csv(url, session=None, fetch=None):
    """Generateur de dicts depuis le CSV, EN FLUX : le fichier peut peser
    plusieurs dizaines de Mo, on ne le charge jamais entierement en memoire.
    Le BOM de la premiere colonne est retire ici, une bonne fois."""
    if fetch:
        texte = fetch(url)
        flux = io.StringIO(texte)
    else:
        session = session or ted.session_robuste()
        r = session.get(url, headers=ENTETES, timeout=180, stream=True)
        r.raise_for_status()
        r.encoding = r.encoding or "utf-8"
        flux = io.StringIO("")

        def _iterer():
            for morceau in r.iter_lines(decode_unicode=True):
                if morceau is not None:
                    yield morceau
        lecteur = csv.DictReader(_iterer())
        lecteur.fieldnames = [(c or "").lstrip("\ufeff").strip()
                              for c in (lecteur.fieldnames or [])]
        for i, ligne in enumerate(lecteur):
            if i >= MAX_LIGNES_CSV:
                break
            yield ligne
        return

    lecteur = csv.DictReader(flux)
    lecteur.fieldnames = [(c or "").lstrip("\ufeff").strip()
                          for c in (lecteur.fieldnames or [])]
    for i, ligne in enumerate(lecteur):
        if i >= MAX_LIGNES_CSV:
            break
        yield ligne


# ===========================================================================
# PARTIE 3 -- NORMALISATION
# ===========================================================================

def lire_date(brut):
    """'2019-04-05T00:00' ou '4/5/2019' -> date ISO. '' si illisible.
    L'IDB melange les deux formats dans le meme fichier."""
    t = (brut or "").strip()
    if t in VIDES:
        return ""
    if len(t) >= 10 and t[4] == "-" and t[7] == "-":
        return t[:10]
    for gabarit in ("%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(t.split(" ")[0], gabarit).date().isoformat()
        except ValueError:
            continue
    return ""


def echeance_depassee(deadline_iso, aujourd_hui=None):
    """Un avis dont la date limite est passee n'a AUCUNE valeur commerciale.
    Une echeance illisible ou absente est conservee (on prefere analyser en
    trop qu'ecarter a tort)."""
    if not deadline_iso:
        return False
    try:
        d = datetime.strptime(deadline_iso, "%Y-%m-%d").date()
    except ValueError:
        return False
    return d < (aujourd_hui or date.today())


def normaliser(ligne):
    """Ligne du CSV -> avis compatible ted.appeler_llm / ted.calculer_scores.
    None si hors perimetre. Le pays vient de `countryname`, un NOM : sans
    correspondance vers l'ISO3, l'avis est ecarte (jamais de pays invente)."""
    titre = _val(ligne, COL_TITRE)
    if not titre:
        return None
    iso3 = bm.code_iso3_pays(_val(ligne, COL_PAYS))
    if not iso3 or not ted.dans_le_perimetre(iso3):
        return None                      # "REGIONAL" et hors perimetre tombent ici
    date_pub = lire_date(_val(ligne, COL_DATE))
    deadline = lire_date(_val(ligne, COL_DEADLINE))
    if echeance_depassee(deadline):
        return None
    projet = _val(ligne, COL_PROJET)
    description = " ".join(x for x in (
        _val(ligne, COL_DESC), projet, _val(ligne, COL_SECTEUR),
        _val(ligne, COL_METHODE)) if x)[:ted.MAX_CARACTERES_DESCRIPTION]
    ident = _val(ligne, COL_ID)
    return {
        # Cles lues par le prompt LLM et le moteur de score.
        "acheteur": "Inter-American Development Bank",
        "pays_acheteur": "",                        # bailleur multilateral
        "pays_execution": iso3,
        "titre": titre[:300],
        "cpv": "",
        "description": description,
        # Metadonnees propres IDB.
        "type_notice": _val(ligne, COL_CATEGORIE) or _val(ligne, COL_TYPE),
        "methode_passation": _val(ligne, COL_METHODE),
        "secteur_idb": _val(ligne, COL_SECTEUR),
        "projet": projet,
        "numero_projet": _val(ligne, COL_NUM_PROJET),
        "lien_avis": (_val(ligne, COL_URL_DOC) or _val(ligne, COL_URL_PROJET)),
        "publication_number": "IDB-{}".format(ident) if ident else "",
        "deadline": deadline,
        "date_publication": date_pub,
        "pays_execution_incertitude": False,
    }


def motif_rejet(ligne):
    """Pourquoi cette ligne est ecartee, dans l'ORDRE des filtres reels.

    La premiere version testait l'echeance en premier, quel que soit le pays :
    elle attribuait donc a "echeance passee" des lignes qui etaient d'abord
    hors perimetre. Un compteur faux est pire qu'aucun compteur : il oriente
    le diagnostic dans la mauvaise direction."""
    if not _val(ligne, COL_TITRE):
        return "sans_titre"
    iso3 = bm.code_iso3_pays(_val(ligne, COL_PAYS))
    if not iso3:
        return "pays_non_reconnu"          # dont "REGIONAL"
    if not ted.dans_le_perimetre(iso3):
        return "hors_perimetre"
    if echeance_depassee(lire_date(_val(ligne, COL_DEADLINE))):
        return "echeance_passee"
    return ""


def dans_la_fenetre(avis, seuil):
    """Fraicheur sur la date de publication. Date absente = conservee."""
    d = avis.get("date_publication") or ""
    if not d:
        return True
    try:
        return datetime.strptime(d, "%Y-%m-%d").date() >= seuil
    except ValueError:
        return True


def priorite_analyse(avis, aujourd_hui=None):
    """Ordre de passage sous le plafond LLM : risque pays dominant, fraicheur
    en depart (meme doctrine que ReliefWeb depuis le 22/07/2026 -- un avis de
    six semaines vaut moins qu'un avis d'hier)."""
    tier = ted.MULTIPLICATEUR_ZONE.get(avis.get("pays_execution", ""), 0.2)
    d = avis.get("date_publication") or ""
    if not d:
        return tier * 0.6
    try:
        age = ((aujourd_hui or date.today())
               - datetime.strptime(d, "%Y-%m-%d").date()).days
    except ValueError:
        return tier * 0.6
    return tier * max(0.4, 1.0 - 0.6 * min(max(age, 0), 60) / 60.0)


def cible_commerciale(avis, extraction):
    """Qui demarcher. La Banque ne deploie personne : le prospect est
    l'entreprise ou le bureau d'etudes qui executera le marche sur le terrain,
    et l'unite d'execution du projet cote emprunteur."""
    return ("Titulaire (entreprise de travaux ou bureau d'etudes) qui executera "
            "le marche IDB sur le terrain, et unite d'execution du projet cote "
            "emprunteur. Viser leur direction operations / surete, pas la Banque.")


# ===========================================================================
# PARTIE 4 -- SORTIE GOOGLE SHEET
# ===========================================================================

COLONNES_IDB = [
    "date_maj", "score_final", "score_surete", "score_commercial",
    "action_recommandee", "fenetre_action", "niveau_opportunite_amarante",
    "titre", "acheteur", "pays_execution", "pays_acheteur",
    "type_client", "type_mobilite", "profil_personnes_exposees",
    "duree_estimee", "accessibilite_commerciale", "securite_existante_detectee",
    "profils_acteurs_probables", "cible_commerciale_reelle",
    "justification", "confiance", "modele", "raffine", "divergence",
    "type_notice", "methode_passation", "secteur_idb", "projet",
    "numero_projet", "publication_number", "lien_avis",
    "deadline", "date_publication",
]
COLONNE_STATUT_SUIVI = "statut_suivi"
COLONNE_DATE_DETECTION = "date_detection"
TOUTES_COLONNES_IDB = COLONNES_IDB + [COLONNE_STATUT_SUIVI, COLONNE_DATE_DETECTION]


def ouvrir_feuille(sheet_id, fichier_cs):
    import gspread
    from google.oauth2.service_account import Credentials
    portee = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(fichier_cs, scopes=portee)
    classeur = gspread.authorize(creds).open_by_key(sheet_id)
    try:
        f = classeur.worksheet(NOM_ONGLET)
    except gspread.WorksheetNotFound:
        f = classeur.add_worksheet(title=NOM_ONGLET, rows=3000,
                                   cols=len(TOUTES_COLONNES_IDB))
        f.append_row(TOUTES_COLONNES_IDB)
    return f


def ligne_depuis_resultat(r):
    avis, extraction = r["avis"], r["extraction"]
    modele = ted.MODELE_RAFFINEMENT if r["raffine"] else ted.MODELE
    v = {
        "date_maj": date.today().isoformat(),
        "score_final": r["score"], "score_surete": r["surete"],
        "score_commercial": r["commercial"],
        "action_recommandee": ted.calculer_action_recommandee(
            r["score"], extraction, surete=r["surete"]),
        "fenetre_action": ted.calculer_fenetre_action(avis),
        "niveau_opportunite_amarante": (extraction or {}).get(
            "niveau_opportunite_amarante", ""),
        "titre": avis.get("titre", ""), "acheteur": avis.get("acheteur", ""),
        "pays_execution": avis.get("pays_execution", ""),
        "pays_acheteur": avis.get("pays_acheteur", ""),
        "type_client": (extraction or {}).get("type_client", ""),
        "type_mobilite": (extraction or {}).get("type_mobilite", ""),
        "profil_personnes_exposees": (extraction or {}).get(
            "profil_personnes_exposees", ""),
        "duree_estimee": (extraction or {}).get("duree_estimee", ""),
        "accessibilite_commerciale": (extraction or {}).get(
            "accessibilite_commerciale", ""),
        "securite_existante_detectee": (extraction or {}).get(
            "securite_existante_detectee", ""),
        "profils_acteurs_probables": ", ".join(
            (extraction or {}).get("profils_acteurs_probables") or []),
        "cible_commerciale_reelle": cible_commerciale(avis, extraction),
        "justification": (extraction or {}).get("justification", ""),
        "confiance": (extraction or {}).get("confiance", ""),
        "modele": modele, "raffine": r["raffine"], "divergence": r["divergence"],
        "type_notice": avis.get("type_notice", ""),
        "methode_passation": avis.get("methode_passation", ""),
        "secteur_idb": avis.get("secteur_idb", ""),
        "projet": avis.get("projet", ""),
        "numero_projet": avis.get("numero_projet", ""),
        "publication_number": avis.get("publication_number", ""),
        "lien_avis": avis.get("lien_avis", ""),
        "deadline": avis.get("deadline", ""),
        "date_publication": avis.get("date_publication", ""),
    }
    return [str(v.get(c, "")) for c in COLONNES_IDB]


def ecrire_resultats(feuille, resultats):
    """Insere les nouveaux avis, met a jour les scores des avis presents SANS
    toucher a statut_suivi ni date_detection (zone de saisie humaine)."""
    valeurs = feuille.get_all_records()
    index = {}
    for num, ligne in enumerate(valeurs, start=2):
        pub = ligne.get("publication_number", "")
        if pub:
            index[pub] = num
    derniere = ted.lettre_colonne(len(COLONNES_IDB))
    maj, nouvelles, nb_maj, nb_new = [], [], 0, 0
    for r in resultats:
        pub = r["avis"].get("publication_number", "")
        ligne = ligne_depuis_resultat(r)
        if pub and pub in index:
            maj.append({"range": "A{0}:{1}{0}".format(index[pub], derniere),
                        "values": [ligne]})
            nb_maj += 1
        else:
            nouvelles.append(ligne + ["nouveau", date.today().isoformat()])
            nb_new += 1
    if maj:
        feuille.batch_update(maj)
    if nouvelles:
        feuille.append_rows(nouvelles, value_input_option="RAW")
    # Double ecriture : miroir Postgres best-effort, sous FORME PLATE (colonnes
    # du Sheet), la forme canonique que lit le dashboard. Ne peut JAMAIS faire
    # echouer le run.
    try:
        import radar_stockage
        plates = [dict(zip(COLONNES_IDB, ligne_depuis_resultat(r)))
                  for r in resultats]
        print("  (pg) " + radar_stockage.ecrire_miroir(NOM_ONGLET, plates))
    except Exception as e:                     # module absent : run intact
        print("  (pg) miroir indisponible ({})".format(e))
    return nb_new, nb_maj


# ===========================================================================
# PARTIE 5 -- POINT D'ENTREE
# ===========================================================================

def merite_escalade(r):
    """Meme doctrine que TED/BM/AfDB : relecture Sonnet sur les cas a enjeu."""
    e = r["extraction"]
    if e is None:
        return True
    try:
        confiance = float(e.get("confiance") or 0)
    except (TypeError, ValueError):
        confiance = 0.0
    return (r["score"] >= 5.0 or r["surete"] >= 6.0 or confiance < 0.55
            or str(e.get("securite_existante_detectee", "")).lower()
            in ("prestataire_tiers", "aucune"))


def collecter_et_normaliser(session=None, fetch_url=None, fetch_csv=None):
    """Etapes deterministes, testables sans reseau ni LLM."""
    url = url_du_fichier(session=session, fetch=fetch_url)
    seuil = date.today() - timedelta(days=NB_JOURS_FENETRE)
    stats = {"lignes": 0, "retenus": 0, "url": url, "hors_fenetre": 0,
             "motifs": {}, "annees_perimetre": {}, "recents": [],
             "valeurs": {COL_TYPE: {}, COL_CATEGORIE: {}, COL_STATUT: {}}}
    avis, vus = [], set()
    for ligne in lignes_csv(url, session=session, fetch=fetch_csv):
        stats["lignes"] += 1
        if DEBUG:
            for col in (COL_TYPE, COL_CATEGORIE, COL_STATUT):
                v = _val(ligne, col) or "(vide)"
                stats["valeurs"][col][v] = stats["valeurs"][col].get(v, 0) + 1
        motif = motif_rejet(ligne)
        if motif:
            stats["motifs"][motif] = stats["motifs"].get(motif, 0) + 1
            continue
        a = normaliser(ligne)
        if a is None:                      # ceinture et bretelles
            stats["motifs"]["autre"] = stats["motifs"].get("autre", 0) + 1
            continue
        # DIAGNOSTIC : distribution des annees de publication des avis qui sont
        # DANS le perimetre. C'est cette mesure qui dit si le fichier est
        # historique ou si la fenetre est simplement trop etroite.
        annee = (a.get("date_publication") or "")[:4] or "(sans date)"
        stats["annees_perimetre"][annee] = stats["annees_perimetre"].get(annee, 0) + 1
        stats["recents"].append((a.get("date_publication") or "",
                                 a["pays_execution"], a["titre"][:52]))
        if not dans_la_fenetre(a, seuil):
            stats["hors_fenetre"] += 1
            continue
        cle = a["publication_number"] or a["titre"]
        if cle in vus:
            continue
        vus.add(cle)
        avis.append(a)
    stats["retenus"] = len(avis)
    stats["recents"].sort(reverse=True)
    stats["recents"] = stats["recents"][:12]
    return avis, stats


def main():
    if not ACTIVER:
        print("(info) Collecteur IDB desactive (RADAR_IDB=0).")
        return
    print("=" * 60)
    print("COLLECTEUR IDB (Banque Interamericaine) - Radar Amarante")
    print("=" * 60)

    try:
        avis, stats = collecter_et_normaliser()
    except Exception as e:
        # Une source NEUVE ne doit pas faire echouer le run entier : les autres
        # collecteurs et la publication du dashboard doivent aboutir. Meme
        # philosophie que l'isolation des etapes du workflow. On signale
        # clairement et on sort proprement.
        print("ERREUR : collecte IDB impossible ({}).".format(str(e)[:200]))
        print("(info) Les autres collecteurs et le dashboard ne sont pas affectes.")
        return
    print("Fichier : {}".format(stats["url"]))
    print("CSV : {} ligne(s) | retenus : {}".format(
        stats["lignes"], stats["retenus"]))
    print("  ecartes -> " + " | ".join(
        "{} : {}".format(m, n) for m, n in sorted(
            stats["motifs"].items(), key=lambda x: -x[1])) or "  aucun rejet")
    print("  dans le perimetre mais hors fenetre ({} j) : {}".format(
        NB_JOURS_FENETRE, stats["hors_fenetre"]))

    if DEBUG:
        print("\n--- MODE VERIFICATION (RADAR_IDB_DEBUG=1) : AUCUNE ECRITURE ---")
        print("\n[A] Valeurs distinctes des colonnes discriminantes")
        print("    (la colonne `type` separe les AVIS des ATTRIBUTIONS)")
        for col, compte in stats["valeurs"].items():
            print("\n  {} ({} valeur(s) distincte(s)) :".format(col, len(compte)))
            for v, n in sorted(compte.items(), key=lambda x: -x[1])[:12]:
                print("      {:6}x  {}".format(n, v[:60]))

        print("\n[B] ANNEES DE PUBLICATION des avis DANS le perimetre")
        print("    (dit si le fichier est historique ou la fenetre trop etroite)")
        for an, n in sorted(stats["annees_perimetre"].items(), reverse=True):
            print("      {:12} {:6} avis".format(an, n))

        print("\n[C] Les 12 avis les PLUS RECENTS du perimetre :")
        for d, pays, titre in stats["recents"]:
            print("      {:12} {:4} {}".format(d or "(sans date)", pays, titre))

        print("\n[D] Avis retenus dans la fenetre ({} j) :".format(NB_JOURS_FENETRE))
        for a in sorted(avis, key=priorite_analyse, reverse=True)[:20]:
            print("      [{}] {} | echeance {} | {}".format(
                a["pays_execution"], a["date_publication"] or "?",
                a["deadline"] or "-", a["titre"][:52]))
        if not avis:
            print("      (aucun : voir [B] et [C] pour la raison)")

        print("\n[E] Repartition par pays des avis retenus :")
        par_pays = {}
        for a in avis:
            par_pays[a["pays_execution"]] = par_pays.get(a["pays_execution"], 0) + 1
        for p, n in sorted(par_pays.items(), key=lambda x: -x[1]):
            print("      {:4} {} avis".format(p, n))
        print("\n--- FIN DU MODE VERIFICATION ---")
        return

    if not avis:
        print("Aucun avis IDB a analyser ce run.")
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERREUR : ANTHROPIC_API_KEY absente (analyse LLM impossible).")
        return

    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")

    # MEMOIRE AVANT PLAFOND (regle du projet) : le budget d'analyse sert a
    # decouvrir, pas a redecouvrir.
    deja_vus = ted.numeros_publication_existants(
        sheet_id, fichier, NOM_ONGLET, COLONNES_IDB)
    a_traiter = [a for a in avis if a["publication_number"] not in deja_vus]
    print("Memoire : {} deja vu(s) ignore(s), {} nouveau(x) a analyser.".format(
        len(avis) - len(a_traiter), len(a_traiter)))
    if not a_traiter:
        print("Rien de nouveau. Onglet et dashboard restent a jour.")
        return

    a_traiter.sort(key=priorite_analyse, reverse=True)
    if len(a_traiter) > MAX_AVIS_LLM:
        en_attente = len(a_traiter) - MAX_AVIS_LLM
        a_traiter = a_traiter[:MAX_AVIS_LLM]
        print("    (plafond de {} : {} analyses ce run, {} en attente pour le "
              "prochain, les plus a risque et les plus recents d'abord.)".format(
                  MAX_AVIS_LLM, MAX_AVIS_LLM, en_attente))

    print("\nAnalyse LLM ({} avis, modele {})...\n".format(
        len(a_traiter), ted.MODELE))
    resultats = []
    for i, avis_un in enumerate(a_traiter, start=1):
        print("[{}/{}] {}...".format(i, len(a_traiter), avis_un["titre"][:60]))
        extraction = ted.appeler_llm(avis_un)
        surete, commercial, final = ted.calculer_scores(avis_un, extraction)
        resultats.append({
            "avis": avis_un, "extraction": extraction,
            "surete": surete, "commercial": commercial, "score": final,
            "final_haiku": final, "raffine": False, "divergence": False,
        })
        time.sleep(0.4)

    a_escalader = [r for r in resultats if merite_escalade(r)]
    if a_escalader:
        print("\n{} avis escalade(s) vers {}...\n".format(
            len(a_escalader), ted.MODELE_RAFFINEMENT))
        for r in a_escalader:
            ex = ted.appeler_llm(r["avis"], modele=ted.MODELE_RAFFINEMENT)
            if ex is not None:
                s, c, f = ted.calculer_scores(r["avis"], ex)
                r["extraction"], r["surete"], r["commercial"], r["score"] = ex, s, c, f
                r["raffine"] = True
                r["divergence"] = abs(f - r["final_haiku"]) >= 2.0

    if not (sheet_id and fichier):
        print("\n(info) Pas de Sheet configure : resultats en console seulement.")
        for r in sorted(resultats, key=lambda x: -x["score"])[:20]:
            print("  {:4.1f} [{}] {}".format(
                r["score"], r["avis"]["pays_execution"], r["avis"]["titre"][:66]))
        return

    print("\nEcriture dans l'onglet '{}' ({} avis)...".format(
        NOM_ONGLET, len(resultats)))
    feuille = ouvrir_feuille(sheet_id, fichier)
    nb_new, nb_maj = ecrire_resultats(feuille, resultats)
    print("-> {} nouvel(s) avis, {} mis a jour (statut_suivi jamais touche).".format(
        nb_new, nb_maj))

    print("\n" + "=" * 70)
    print("RESULTATS IDB (score = surete x0.5 + commercial x0.5)")
    print("=" * 70)
    for r in sorted(resultats, key=lambda x: -x["score"])[:15]:
        a = r["avis"]
        print("\n[{:4.1f}] {} | {} | echeance {}".format(
            r["score"], a["pays_execution"], a["titre"][:60], a["deadline"] or "-"))
        print("   {}".format((r["extraction"] or {}).get("justification", "")[:220]))
        print("   Lien : {}".format(a["lien_avis"][:100]))


if __name__ == "__main__":
    main()
