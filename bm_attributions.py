# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- COLLECTEUR D'ATTRIBUTIONS BANQUE MONDIALE.
=============================================================

CE QU'IL FAIT
-------------
Recupere les avis d'ATTRIBUTION ("Contract Award") de la Banque Mondiale et en
extrait le NOM DU TITULAIRE, puis ecrit dans l'onglet `attributions_radar`,
CELUI-LA MEME que les attributions TED.

Consequence voulue : aucune modification du dashboard. Les lignes remontent
automatiquement dans la lentille "Titulaires - attributions" et dans la fiche
entreprise 360 (via `attribution_vers_lead`, qui lit `gagnant`).

POURQUOI CETTE SOURCE
---------------------
Un marche attribue il y a quelques semaines = une entreprise EN MOBILISATION.
C'est la fenetre ou le besoin de surete se decide, avant le deploiement. Le
titulaire est nomme, donc c'est un prospect direct, pas une intention de marche.

CHOIX TECHNIQUES (valides par sonde depuis GitHub Actions, juillet 2026)
------------------------------------------------------------------------
  - On reutilise l'API que le collecteur BM interroge DEJA (procnotices), en
    ajoutant simplement le type "Contract Award". Pas de nouvelle dependance,
    pas de nouveau domaine, fraicheur du jour meme (verifie : as_of du jour).
  - Le nom du gagnant n'est PAS un champ structure : il vit dans le HTML de
    `notice_text`, sous la section "Awarded Bidder(s):". D'ou le parseur
    tolerant ci-dessous, teste et faillible en douceur (une ligne sans gagnant
    identifiable est ignoree, jamais ecrite a moitie).
  - On ne garde que les groupes CS (conseil) et CW (travaux) : ce sont ceux ou
    du personnel se deploie. GO (fournitures) est ecarte, un marche de cables
    HDMI n'interesse pas Amarante.
  - On ne garde que les pays de l'univers de risque (MULTIPLICATEUR_ZONE).

MODE VERIFICATION (a utiliser au premier run)
---------------------------------------------
    RADAR_BM_ATTRIB_DEBUG=1  -> imprime ce qui a ete extrait, N'ECRIT RIEN.
C'est le moyen de confirmer que le parseur lit bien les vrais noms avant de
laisser le collecteur alimenter le Sheet.

Interrupteur : RADAR_BM_ATTRIB=0 desactive la collecte.
Fenetre       : RADAR_BM_ATTRIB_JOURS (defaut 120 jours).

LANCEMENT :  python bm_attributions.py
"""

import html as _html
import os
import re
from datetime import date, datetime, timedelta

import ted_complet_v14 as ted
import ted_complet_bm as bm


# ===========================================================================
# CONFIGURATION
# ===========================================================================

ACTIVER = os.environ.get("RADAR_BM_ATTRIB", "1") != "0"
DEBUG = os.environ.get("RADAR_BM_ATTRIB_DEBUG", "0") == "1"

# Fenetre de mobilisation : au-dela, l'entreprise est deja installee et le
# besoin de surete a ete arbitre. 120 jours couvre large sans noyer le Sheet.
JOURS_FENETRE = int(os.environ.get("RADAR_BM_ATTRIB_JOURS", "120"))

# Onglet PARTAGE avec les attributions TED (integration dashboard gratuite).
NOM_ONGLET = "attributions_radar"
COLONNES = [
    "date_maj", "gagnant", "secteur", "pays_execution", "valeur_attribuee",
    "acheteur", "titre", "cpv", "sous_traitance",
    "date_publication", "publication_number", "lien", "a_demarcher",
]
COL_STATUT = "statut_prospection"
COL_DETECTION = "date_detection"
TOUTES_COLONNES = COLONNES + [COL_STATUT, COL_DETECTION]

TYPE_ATTRIBUTION = "Contract Award"
PAGES_MAX = int(os.environ.get("RADAR_BM_ATTRIB_PAGES", "12"))

# Groupes d'achat ou du personnel se deploie reellement.
LIBELLE_GROUPE = {"CS": "Conseil / AT", "CW": "Travaux / BTP"}

# Etiquettes rencontrees dans le bloc "Awarded Bidder(s)" : ce sont des
# EN-TETES, jamais des noms d'entreprise. Sert a ne pas confondre les deux.
ETIQUETTES = {
    "name", "supplier", "bidder", "bidder name", "supplier name",
    "address", "country", "city", "state", "province", "zip", "postal code",
    "contract amount", "evaluated cost", "bid price", "amount",
    "beneficial ownership", "awarded bidder", "awarded bidder(s)",
    "e-mail", "email", "phone", "fax", "web", "website", "contact",
}
# Etiquettes qui ANNONCENT un nom d'entreprise a la ligne suivante.
ETIQUETTES_NOM = {"name", "supplier", "bidder", "bidder name", "supplier name"}


# ===========================================================================
# PARSEUR (fonctions PURES : testables sans reseau)
# ===========================================================================

def _norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def texte_en_lignes(html_brut):
    """HTML de notice_text -> liste de lignes texte propres.

    Les separateurs de bloc (<br>, </div>, </p>, </td>) deviennent des sauts
    de ligne AVANT le retrait des balises, sinon tout le contenu se colle en
    une seule ligne illisible."""
    t = str(html_brut or "")
    t = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", t)
    t = re.sub(r"(?i)</\s*(div|p|tr|td|li|h\d)\s*>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", "", t)          # retire toutes les balises
    t = _html.unescape(t)
    lignes = [_norm(l) for l in t.split("\n")]
    return [l for l in lignes if l]


def valeur_label(lignes, label):
    """Valeur associee a une etiquette, dans les deux mises en forme vues :
      - meme ligne :  "Project:P180076-Lowlands..."
      - lignes suivantes : "Duration of Contract" / "" / "2 Week(s)"
    Les indications de format entre parentheses, comme "(YYYY/MM/DD)", sont
    sautees : ce sont des aides de lecture, pas des valeurs."""
    cible = _norm(label).lower().rstrip(":")
    for i, ligne in enumerate(lignes):
        bas = ligne.lower()
        if not bas.startswith(cible):
            continue
        reste = ligne[len(cible):].lstrip(" :")
        if reste and not reste.startswith("("):
            return _norm(reste)
        for suivante in lignes[i + 1:i + 4]:
            if suivante.startswith("(") and suivante.endswith(")"):
                continue               # "(YYYY/MM/DD)"
            if _norm(suivante).lower().rstrip(":") in ETIQUETTES:
                break                  # on est tombe sur l'etiquette suivante
            return _norm(suivante)
    return ""


def extraire_gagnants(notice_text, maxi=4):
    """Noms d'entreprises de la section "Awarded Bidder(s)".

    Tolerant par construction : la sous-structure exacte du bloc varie d'un
    avis a l'autre. On procede en deux temps :
      1. si une etiquette de nom ("Name", "Supplier"...) est presente, on prend
         la ligne qui la suit (cas le plus fiable) ;
      2. sinon, on prend les premieres lignes qui ne sont pas des etiquettes,
         ni des montants, ni des codes.
    Renvoie [] si rien de credible : le lead est alors ignore plutot
    qu'ecrit avec un gagnant faux."""
    brut = str(notice_text or "")
    m = re.search(r"(?i)awarded\s+bidder", brut)
    if not m:
        return []
    lignes = texte_en_lignes(brut[m.start():])
    if lignes and re.match(r"(?i)^awarded\s+bidder", lignes[0]):
        lignes = lignes[1:]

    noms, vus = [], set()

    def _ajouter(candidat):
        c = _norm(candidat).strip(" .,;:")
        if not _nom_plausible(c):
            return
        cle = c.lower()
        if cle not in vus:
            vus.add(cle)
            noms.append(c)

    # 1. Etiquette de nom -> valeur a la ligne suivante.
    for i, ligne in enumerate(lignes):
        if _norm(ligne).lower().rstrip(":") in ETIQUETTES_NOM and i + 1 < len(lignes):
            _ajouter(lignes[i + 1])
            if len(noms) >= maxi:
                return noms

    # 2. Repli : premieres lignes non etiquettes.
    if not noms:
        for ligne in lignes:
            if _norm(ligne).lower().rstrip(":") in ETIQUETTES:
                continue
            _ajouter(ligne)
            if len(noms) >= maxi:
                break
    return noms[:maxi]


def _nom_plausible(c):
    """Ecarte ce qui ne peut pas etre une raison sociale : trop court, purement
    numerique, montant, date, ou mention d'absence de publication."""
    if len(c) < 3 or len(c) > 160:
        return False
    bas = c.lower()
    if bas in ETIQUETTES:
        return False
    if re.match(r"^[\d\s.,/%-]+$", c):                 # nombres, dates, montants
        return False
    if re.match(r"(?i)^(n/?a|none|not applicable|non publie|not disclosed)$", bas):
        return False
    if not re.search(r"[A-Za-zÀ-ÿ]{3}", c):            # doit contenir des lettres
        return False
    return True


def date_attribution(notice_text, record):
    """Date de notification d'attribution (AAAA/MM/JJ dans le notice_text),
    avec repli sur la date de publication de l'avis."""
    lignes = texte_en_lignes(notice_text)
    brut = valeur_label(lignes, "Date Notification of Award Issued")
    m = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", brut or "")
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            pass
    brut2 = _norm(record.get("noticedate"))
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(brut2, fmt).date().isoformat()
        except (ValueError, TypeError):
            continue
    return ""


def montant_attribue(notice_text):
    """Montant du contrat s'il figure dans l'avis, sinon chaine vide."""
    lignes = texte_en_lignes(notice_text)
    for label in ("Contract Amount", "Evaluated Cost", "Bid Price", "Amount"):
        v = valeur_label(lignes, label)
        if v and re.search(r"\d", v):
            return _norm(v)[:60]
    return ""


def duree_contrat(notice_text):
    """Duree du contrat ("60 Day(s)", "2 Week(s)"...). Signal de deploiement :
    un chantier long implique une presence residente, donc un besoin durable."""
    return _norm(valeur_label(texte_en_lignes(notice_text), "Duration of Contract"))[:40]


# ===========================================================================
# FILTRAGE ET NORMALISATION
# ===========================================================================

def dans_la_fenetre(iso_date, aujourdhui=None, jours=None):
    """Attribution assez recente pour que la mobilisation soit en cours."""
    if not iso_date:
        return False
    jours = JOURS_FENETRE if jours is None else jours
    aujourdhui = aujourdhui or date.today()
    try:
        d = date.fromisoformat(iso_date)
    except ValueError:
        return False
    return timedelta(0) <= (aujourdhui - d) <= timedelta(days=jours)


def record_retenu(record):
    """Filtre de pertinence : type attribution, groupe CS/CW, pays a risque."""
    if _norm(record.get("notice_type")) != TYPE_ATTRIBUTION:
        return False, "type"
    if _norm(record.get("procurement_group")).upper() not in bm.BM_GROUPES_RETENUS:
        return False, "groupe"
    iso3 = bm.code_iso3_pays(record.get("project_ctry_name") or "")
    if not iso3 or iso3 not in ted.MULTIPLICATEUR_ZONE:
        return False, "pays"
    return True, ""


def normaliser(record):
    """Enregistrement BM -> ligne de l'onglet `attributions_radar`.
    Renvoie None si l'avis est hors perimetre ou sans gagnant identifiable."""
    ok, _motif = record_retenu(record)
    if not ok:
        return None
    texte = record.get("notice_text") or ""
    gagnants = extraire_gagnants(texte)
    if not gagnants:
        return None                     # mieux vaut rien qu'un faux titulaire
    d_attrib = date_attribution(texte, record)
    if not dans_la_fenetre(d_attrib):
        return None

    groupe = _norm(record.get("procurement_group")).upper()
    pays_nom = _norm(record.get("project_ctry_name"))
    duree = duree_contrat(texte)
    titre = _norm(record.get("bid_description")) or _norm(record.get("project_name"))
    secteur = LIBELLE_GROUPE.get(groupe, groupe or "Attribution")
    if duree:
        secteur_affiche = secteur
        titre = "{} (duree {})".format(titre, duree) if titre else titre
    else:
        secteur_affiche = secteur

    return {
        "date_maj": date.today().isoformat(),
        "gagnant": " ; ".join(gagnants),
        "secteur": secteur_affiche,
        "pays_execution": pays_nom,
        "valeur_attribuee": montant_attribue(texte),
        "acheteur": _norm(record.get("project_name")) or "Banque Mondiale",
        "titre": titre[:300],
        "cpv": _norm(record.get("procurement_method_code")),
        "sous_traitance": "",
        "date_publication": d_attrib,
        "publication_number": _norm(record.get("id")),
        "lien": bm.LIEN_BM.format(_norm(record.get("id"))),
        "a_demarcher": "oui",
        "_nb_gagnants": len(gagnants),
        "_duree": duree,
    }


# ===========================================================================
# COLLECTE
# ===========================================================================

def collecte(session=None):
    """Pagine les attributions BM. Best-effort : une page en echec interrompt
    la pagination sans faire echouer le run."""
    session = session or ted.session_robuste()
    records, stats = [], {"pages": 0, "recus": 0}
    for page in range(PAGES_MAX):
        params = {"format": "json", "rows": bm.ROWS_BM,
                  "os": page * bm.ROWS_BM,
                  "notice_type_exact": TYPE_ATTRIBUTION}
        try:
            rep = session.get(bm.BM_ENDPOINT, params=params, timeout=45)
            if rep.status_code >= 400:
                print("(bm-attrib) page {} : HTTP {}, arret de la pagination.".format(
                    page, rep.status_code))
                break
            data = rep.json()
        except Exception as e:
            print("(bm-attrib) page {} illisible ({}), arret.".format(page, e))
            break
        lot = data.get("procnotices") or []
        if not lot:
            break
        records.extend(lot)
        stats["pages"] += 1
        stats["recus"] += len(lot)
        if len(lot) < bm.ROWS_BM:
            break
    return records, stats


def construire(records):
    """Records bruts -> attributions normalisees, dedupliquees par identifiant."""
    sorties, vus = [], set()
    motifs = {"type": 0, "groupe": 0, "pays": 0, "sans_gagnant": 0, "hors_fenetre": 0}
    for rec in records:
        ok, motif = record_retenu(rec)
        if not ok:
            motifs[motif] = motifs.get(motif, 0) + 1
            continue
        ligne = normaliser(rec)
        if ligne is None:
            texte = rec.get("notice_text") or ""
            if not extraire_gagnants(texte):
                motifs["sans_gagnant"] += 1
            else:
                motifs["hors_fenetre"] += 1
            continue
        pub = ligne["publication_number"]
        if pub and pub in vus:
            continue
        vus.add(pub)
        sorties.append(ligne)
    return sorties, motifs


# ===========================================================================
# ECRITURE (onglet partage avec les attributions TED)
# ===========================================================================

def ouvrir_feuille(sheet_id, fichier):
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_file(
        fichier, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    classeur = gspread.authorize(creds).open_by_key(sheet_id)
    try:
        f = classeur.worksheet(NOM_ONGLET)
    except gspread.WorksheetNotFound:
        f = classeur.add_worksheet(title=NOM_ONGLET, rows=3000,
                                   cols=len(TOUTES_COLONNES))
        f.append_row(TOUTES_COLONNES)
        return f
    if COL_DETECTION not in f.row_values(1):
        f.update(values=[TOUTES_COLONNES], range_name="A1")
    return f


def ligne_pour_sheet(a):
    return [str(a.get(c, "")) for c in COLONNES]


def ecrire(feuille, attributions):
    """N'ajoute que les nouvelles lignes. Ne REECRIT jamais une ligne existante :
    la colonne `statut_prospection` est une zone de saisie humaine."""
    index = ted.charger_index_publication(feuille)
    nouvelles, ignorees = [], 0
    for a in attributions:
        pub = a.get("publication_number", "")
        if pub and pub in index:
            ignorees += 1
            continue
        nouvelles.append(ligne_pour_sheet(a) + ["", date.today().isoformat()])
    if nouvelles:
        feuille.append_rows(nouvelles, value_input_option="RAW")
    return len(nouvelles), ignorees


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    if not ACTIVER:
        print("(info) Collecteur attributions BM desactive (RADAR_BM_ATTRIB=0).")
        return

    print("Collecte des attributions Banque Mondiale "
          "(groupes {}, fenetre {} jours)...".format(
              "/".join(sorted(bm.BM_GROUPES_RETENUS)), JOURS_FENETRE))
    records, stats = collecte()
    print("  {} enregistrement(s) recu(s) sur {} page(s).".format(
        stats["recus"], stats["pages"]))

    attributions, motifs = construire(records)
    print("  ecartes -> groupe hors CS/CW : {groupe} | pays hors perimetre : {pays} | "
          "sans gagnant lisible : {sans_gagnant} | hors fenetre : {hors_fenetre}".format(**motifs))
    print("  {} attribution(s) exploitable(s).".format(len(attributions)))

    if DEBUG:
        print("\n--- MODE VERIFICATION (RADAR_BM_ATTRIB_DEBUG=1) : AUCUNE ECRITURE ---")
        for a in attributions[:25]:
            print("  [{}] {} | {} | {} | {}{}".format(
                a["date_publication"], a["gagnant"], a["pays_execution"],
                a["secteur"], (a["valeur_attribuee"] or "montant n.c."),
                (" | duree " + a["_duree"]) if a.get("_duree") else ""))
        print("--- Verifie que les noms ci-dessus sont bien des entreprises. ---")
        return

    sheet_id = os.environ.get("TED_SHEET_ID")
    fichier = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not (sheet_id and fichier):
        print("(info) TED_SHEET_ID / GOOGLE_SERVICE_ACCOUNT_FILE absents : pas d'ecriture.")
        return
    try:
        feuille = ouvrir_feuille(sheet_id, fichier)
        ajoutees, deja = ecrire(feuille, attributions)
        print("  {} nouvelle(s) ligne(s) ecrite(s) dans '{}' "
              "({} deja connue(s)).".format(ajoutees, NOM_ONGLET, deja))
    except Exception as e:
        print("(bm-attrib) ecriture impossible ({}). Le run continue.".format(e))


if __name__ == "__main__":
    main()
