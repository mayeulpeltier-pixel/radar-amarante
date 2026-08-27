# -*- coding: utf-8 -*-
"""
RADAR AMARANTE -- COUCHE DE STOCKAGE POSTGRES.
===============================================

POURQUOI CE MODULE (option B validee le 21/07/2026)
---------------------------------------------------
Google Sheets est aujourd'hui l'unique base de donnees du radar : quotas
d'API, aucune requete possible, un seul ecrivain, lie a un compte personnel,
invendable en l'etat. Ce module pose la fondation definitive : un Postgres
manage gratuit (Neon), sur lequel l'application web puis le multi-client se
brancheront tels quels.

STRATEGIE DE MIGRATION (sans big bang, reversible a chaque etape)
-----------------------------------------------------------------
  Etape 1 (CE MODULE)  : la couche existe, testee, inerte tant que
                         DATABASE_URL est absent.
  Etape 2 (PR suivante): DOUBLE ECRITURE. Chaque collecteur ecrit dans le
                         Sheet (reference actuelle) ET appelle
                         `ecrire_miroir` (best-effort : un echec Postgres
                         n'affecte JAMAIS le run).
  Etape 3              : le dashboard lit Postgres ; le Sheet devient un
                         export de confort.
Interrupteur : RADAR_PG=0 coupe tout (motif ADB : on desactive par variable
d'environnement, on ne supprime pas).

MODELE DE DONNEES : UNE TABLE GENERIQUE, PAS UNE TABLE PAR ONGLET
-----------------------------------------------------------------
Les onglets du radar ont des colonnes DYNAMIQUES (les cibles privees ajoutent
des colonnes sectorielles) et le principe du projet est la PRESERVATION DU
SCHEMA. Une table typee par onglet casserait a chaque colonne ajoutee. On
stocke donc chaque ligne en JSONB, fidele au Sheet :

    radar_lignes(onglet, publication_number, donnees JSONB, date_detection, maj)

avec un index UNIQUE sur (onglet, publication_number) quand l'identifiant est
non vide. L'insertion se fait en ON CONFLICT DO UPDATE : une ligne existante
est RAFRAICHIE (voir `ajouter_lignes`).

  Correction du 23/07/2026. C'etait `DO NOTHING`, au motif de "proteger la
  zone de saisie humaine". Motif errone : cette zone n'est pas dans cette
  table, elle vit dans `radar_statuts`. La garde ne protegeait donc rien,
  mais elle empechait les scores RAFFINES (escalade Sonnet) d'arriver en
  base. L'application Render affichait 5.0 / "surveiller" quand le dashboard
  Cloudflare affichait 8.5 / "contacter", pour le meme avis. Un miroir qui
  ne reflete pas n'est pas un miroir.

`date_detection` (premiere detection) n'est jamais reecrite. Les requetes
analytiques viendront plus tard par des vues SQL, sans toucher aux donnees.

SECURITE
--------
  - `DATABASE_URL` vient d'un secret GitHub Actions, jamais du depot.
  - Aucun identifiant dynamique dans le SQL : les noms d'onglets sont des
    VALEURS (parametres lies), jamais concatenes dans les requetes.
  - `ecrire_miroir` avale toutes les exceptions et renvoie un compte-rendu
    texte pour le journal : la base ne peut pas faire echouer un collecteur.

VERIFICATION :  python radar_stockage.py   (workflow "Stockage Postgres")
  Sans DATABASE_URL -> l'explique et sort en code 0.
  Avec              -> cree le schema (idempotent) et imprime l'inventaire.
"""

import json
import os
from datetime import date, datetime


ACTIVER = os.environ.get("RADAR_PG", "1") != "0"
URL_ENV = "DATABASE_URL"

# Restitution de la date de PREMIERE detection a la relecture (voir
# `injecter_date_detection`). Par defaut ACTIF : c'est une correction de
# donnee fausse, pas une fonctionnalite -- laisser OFF reviendrait a garder
# « detecte aujourd'hui » sur des lignes vieilles de plusieurs mois. Poser
# RADAR_DATE_DET_PG=0 dans l'environnement Render pour revenir a l'ancien
# comportement sans redeploiement.
DATE_DET_PG = os.environ.get("RADAR_DATE_DET_PG", "1") != "0"

# Schema idempotent : rejouable a chaque demarrage sans jamais rien detruire.
# L'index unique est PARTIEL : les lignes sans identifiant (rares, assumees
# cote BM : "on prefere un doublon potentiel a une perte silencieuse de
# lead") s'inserent toujours.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS radar_lignes (
    id                 BIGSERIAL PRIMARY KEY,
    onglet             TEXT        NOT NULL,
    publication_number TEXT        NOT NULL DEFAULT '',
    donnees            JSONB       NOT NULL,
    date_detection     DATE        NOT NULL DEFAULT CURRENT_DATE,
    maj                TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS radar_lignes_onglet_pub
    ON radar_lignes (onglet, publication_number)
    WHERE publication_number <> '';
CREATE INDEX IF NOT EXISTS radar_lignes_onglet
    ON radar_lignes (onglet);
CREATE TABLE IF NOT EXISTS radar_statuts (
    onglet             TEXT        NOT NULL,
    publication_number TEXT        NOT NULL,
    statut             TEXT        NOT NULL,
    motif              TEXT        NOT NULL DEFAULT '',
    maj                TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (onglet, publication_number)
);
-- Migration idempotente : ajoute 'motif' aux bases anterieures a cette colonne
-- (bouton « Pas pertinent » : on stocke la RAISON pour apprendre, 12/08/2026).
ALTER TABLE radar_statuts ADD COLUMN IF NOT EXISTS motif TEXT NOT NULL DEFAULT '';
-- Valeur estimee, saisie a la main au passage en « contacte » (P1.1). Sans
-- elle, aucun apprentissage sur le MONTANT n'est possible : le radar ne peut
-- comparer que des comptages. NULL = non renseignee, ce qui n'est PAS zero.
ALTER TABLE radar_statuts ADD COLUMN IF NOT EXISTS valeur_estimee NUMERIC;

-- =========================================================================
-- JOURNAL DES TRANSITIONS (P1.1, 26/08/2026) -- le socle de l'apprentissage
-- =========================================================================
-- `radar_statuts` porte l'ETAT COURANT : un lead gagne ecrase son passage en
-- « contacte », et l'information « combien de temps entre le contact et la
-- signature » est perdue a jamais.
--
-- Cette table-ci est un JOURNAL APPEND-ONLY : une ligne par transition, avec
-- l'etat precedent et l'horodatage. C'est ce qui permet de repondre a des
-- questions qu'un etat courant ne peut pas porter :
--   - quel delai moyen entre « contacte » et « gagne » par secteur ?
--   - quels motifs de perte reviennent sur quels theatres ?
--   - un lead repasse-t-il plusieurs fois par le meme statut ?
--
-- On journalise TOUTES les transitions, pas seulement les issues. Le surcout
-- est nul (quelques lignes par semaine) et le manque serait irrattrapable :
-- une transition non enregistree ne se reconstitue pas apres coup.
CREATE TABLE IF NOT EXISTS radar_outcomes (
    id                 BIGSERIAL   PRIMARY KEY,
    onglet             TEXT        NOT NULL,
    publication_number TEXT        NOT NULL,
    statut             TEXT        NOT NULL,
    statut_precedent   TEXT        NOT NULL DEFAULT '',
    motif              TEXT        NOT NULL DEFAULT '',
    valeur_estimee     NUMERIC,
    cree_le            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS radar_outcomes_lead
    ON radar_outcomes (onglet, publication_number, cree_le);
-- Les issues seules, c'est ce que lira la boucle de retroaction.
CREATE INDEX IF NOT EXISTS radar_outcomes_issues
    ON radar_outcomes (statut, cree_le) WHERE statut IN ('gagne', 'perdu');
"""


# ===========================================================================
# VOCABULAIRE DES STATUTS (P1.1)
# ===========================================================================
# AVANT : l'interface ne pouvait emettre que contacte / surveille /
# non_pertinent. Les mots « gagne » et « perdu » existaient dans les
# commentaires, dans les filtres du dashboard, et `radar_retroaction`
# pretendait s'en nourrir -- mais RIEN ne pouvait en produire un. La boucle
# bayesienne apprenait donc a predire si un humain avait clique, pas si
# Amarante avait gagne.
#
# Les motifs de perte sont une liste FERMEE, volontairement. Un champ libre
# produit vingt formulations de la meme raison et zero statistique
# exploitable : c'est la difference entre une note et une donnee.

STATUTS_VALIDES = ("nouveau", "contacte", "surveille", "non_pertinent",
                   "gagne", "perdu", "attribution_publiee")

# Issues COMMERCIALES : les seules qui alimentent l'apprentissage.
STATUTS_ISSUE = ("gagne", "perdu")

# Un lead ne se perd pas s'il n'a jamais ete travaille. Exiger un contact
# prealable evite un journal pollue de « perdu » qui ne sont que des
# desinteressements -- lesquels ont deja leur statut : non_pertinent.
STATUTS_AVANT_ISSUE = ("contacte", "surveille", "attribution_publiee")

MOTIFS_PERTE = {
    "prix": "Prix trop élevé",
    "incumbent": "Titulaire en place reconduit",
    "hors_perimetre": "Hors périmètre Amarante",
    "pas_de_reponse": "Aucune réponse du prospect",
    "projet_annule": "Projet annulé ou reporté",
    "concurrent": "Perdu face à un concurrent",
    "autre": "Autre",
}


def statut_valide(statut):
    """Vrai si `statut` fait partie du vocabulaire. Fonction PURE."""
    return str(statut or "").strip().lower() in STATUTS_VALIDES


def motif_perte_valide(motif):
    """Vrai si `motif` est un code de perte connu. Fonction PURE.

    Vide accepte : un « perdu » sans motif reste enregistrable. Refuser
    l'enregistrement faute de motif ferait perdre l'issue elle-meme, ce qui
    coute bien plus cher qu'un motif manquant."""
    m = str(motif or "").strip().lower()
    return (not m) or (m in MOTIFS_PERTE)


# Vues analytiques posees SUR le JSONB (sans toucher aux donnees). Recreees a
# chaque init (idempotent). Elles ouvrent l'interrogation SQL ad hoc que le
# JSONB seul rendait penible : attributions a plat, incumbents (titulaires
# recurrents), pipeline de renouvellement. Le montant reste en devise locale
# dans le JSONB -> on agrege par NOMBRE de marches, pas par valeur (la
# conversion EUR se fait a l'affichage, pas en SQL).
VUES_SQL = """
DROP VIEW IF EXISTS v_attributions;
CREATE VIEW v_attributions AS
SELECT
    publication_number,
    donnees->>'gagnant'          AS gagnant,
    donnees->>'acheteur'         AS acheteur,
    donnees->>'pays_execution'   AS pays_execution,
    donnees->>'secteur'          AS secteur,
    donnees->>'valeur_attribuee' AS valeur_attribuee,
    donnees->>'cpv'              AS cpv,
    donnees->>'statut_renouv'    AS statut_renouv,
    donnees->>'fin_contrat'      AS fin_contrat,
    NULLIF(donnees->>'mois_avant_fin', '')::numeric AS mois_avant_fin,
    donnees->>'date_publication' AS date_publication,
    date_detection, maj
FROM radar_lignes
WHERE onglet = 'attributions_radar';

DROP VIEW IF EXISTS v_incumbents;
CREATE VIEW v_incumbents AS
SELECT
    donnees->>'gagnant'                              AS gagnant,
    COUNT(*)                                         AS nb_marches,
    COUNT(DISTINCT donnees->>'pays_execution')       AS nb_pays,
    MAX(donnees->>'fin_contrat')                     AS derniere_fin
FROM radar_lignes
WHERE onglet = 'attributions_radar'
  AND COALESCE(donnees->>'gagnant', '') NOT IN ('', '(gagnant non publie)', 'Titulaire')
GROUP BY donnees->>'gagnant'
HAVING COUNT(*) >= 2;

DROP VIEW IF EXISTS v_renouvellements;
CREATE VIEW v_renouvellements AS
SELECT
    publication_number,
    donnees->>'gagnant'        AS gagnant,
    donnees->>'acheteur'       AS acheteur,
    donnees->>'pays_execution' AS pays_execution,
    donnees->>'fin_contrat'    AS fin_contrat,
    NULLIF(donnees->>'mois_avant_fin', '')::numeric AS mois_avant_fin,
    donnees->>'statut_renouv'  AS statut_renouv
FROM radar_lignes
WHERE onglet = 'attributions_radar'
  AND COALESCE(donnees->>'statut_renouv', '') IN ('imminent', 'a_venir');
"""


# ===========================================================================
# OUTILS PURS (testables sans base)
# ===========================================================================

def actif():
    """Vrai si le miroir Postgres doit tourner : interrupteur ouvert,
    DATABASE_URL present et pilote importable. Sinon le radar vit sa vie
    actuelle (Sheet seul), sans bruit ni erreur."""
    if not ACTIVER or not os.environ.get(URL_ENV):
        return False
    try:
        import psycopg  # noqa: F401  (present en CI, peut manquer en local)
        return True
    except Exception:
        return False


def preparer_ligne(ligne):
    """Dict d'un collecteur -> (publication_number, JSON serialisable).

    Deux formes de lignes coexistent dans le radar :
      - attributions : dict PLAT, publication_number a la racine ;
      - avis (TED, BM, AfDB, EBRD, ReliefWeb, UNGM) : resultat IMBRIQUE, le
        publication_number vit sous r["avis"]. On le cherche aux deux
        endroits ; le JSONB conserve la structure complete (avis + scores),
        plus riche que la ligne aplatie du Sheet.

    Deux nettoyages, tous deux justifies par les donnees reelles :
      - les cles techniques prefixees '_' (_etranger, _origine...) sont des
        champs de travail des collecteurs, pas des donnees : on ne les
        persiste pas ;
      - dates et datetimes deviennent des chaines ISO, JSON ne les connait
        pas."""
    propre = {}
    for cle, val in (ligne or {}).items():
        if str(cle).startswith("_"):
            continue
        if isinstance(val, (datetime, date)):
            val = val.isoformat()
        elif val is not None and not isinstance(val, (str, int, float, bool,
                                                      list, dict)):
            val = str(val)
        propre[str(cle)] = val
    pub = str(propre.get("publication_number", "") or "")
    if not pub:
        avis = propre.get("avis")
        if isinstance(avis, dict):
            pub = str(avis.get("publication_number", "") or "")
    return pub, propre


# ===========================================================================
# ACCES BASE
# ===========================================================================

def connexion(url=None):
    """Connexion psycopg. L'appelant est responsable du close (ou utilise
    `with connexion() as conn:` -- psycopg commit/rollback a la sortie)."""
    import psycopg
    return psycopg.connect(url or os.environ[URL_ENV], connect_timeout=15)


def initialiser(conn):
    """Cree le schema. Idempotent : rejouable a chaque run sans danger."""
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
        cur.execute(VUES_SQL)


def ajouter_lignes(conn, onglet, lignes):
    """Insere ou RAFRAICHIT les lignes d'un onglet. (ajoutees, mises_a_jour).

    POURQUOI CE N'EST PLUS `DO NOTHING` (23/07/2026)
    ------------------------------------------------
    L'ancienne version ignorait toute ligne deja presente, au motif de
    "proteger la zone de saisie humaine". Ce motif etait FAUX : la zone
    humaine ne vit pas ici. `radar_lignes` ne contient QUE de la donnee de
    collecte ; les statuts de prospection vivent dans `radar_statuts`, table
    separee que rien ici ne touche. La garde protegeait donc quelque chose
    qui n'etait pas la, au prix d'un vrai defaut :

      Le Sheet, lui, MET A JOUR les scores d'un avis reanalyse (escalade
      Sonnet). Postgres, non. Resultat constate : l'application Render
      affichait 5.0 / "surveiller" pendant que le dashboard Cloudflare
      affichait 8.5 / "contacter" POUR LE MEME AVIS. Deux surfaces, deux
      verites, sur un produit dont l'application est le livrable.

    Desormais le miroir merite son nom : il reflete le Sheet.

    CE QUI EST RAFRAICHI, CE QUI NE L'EST PAS
    -----------------------------------------
      - `donnees`        : rafraichi (scores, action recommandee, raffine...) ;
      - `maj`            : rafraichi, ce qui fait aussi expirer le cache de
                           l'application (voir radar_app.version_donnees, qui
                           s'appuie sur max(maj)) ;
      - `date_detection` : JAMAIS touche. C'est la date de PREMIERE detection,
                           elle n'a de sens que si elle ne bouge pas ;
      - `radar_statuts`  : autre table, hors de portee de cette requete.

    Les lignes sans identifiant restent en insertion pure : l'index unique
    est partiel (`WHERE publication_number <> ''`), donc aucun conflit a
    resoudre pour elles.

    NOTE SUR `xmax = 0` : c'est l'idiome Postgres pour distinguer une
    insertion d'une mise a jour dans un ON CONFLICT. Il suppose un ecrivain
    unique, ce qui est le cas ici (le workflow a un groupe de concurrence)."""
    ajoutees = mises_a_jour = 0
    with conn.cursor() as cur:
        for ligne in lignes or []:
            pub, donnees = preparer_ligne(ligne)
            cur.execute(
                "INSERT INTO radar_lignes (onglet, publication_number, donnees)"
                " VALUES (%s, %s, %s::jsonb)"
                " ON CONFLICT (onglet, publication_number)"
                " WHERE publication_number <> ''"
                " DO UPDATE SET donnees = EXCLUDED.donnees, maj = now()"
                " RETURNING (xmax = 0) AS insertion",
                (onglet, pub, json.dumps(donnees, ensure_ascii=False)))
            resultat = cur.fetchone()
            if resultat is None or resultat[0]:
                ajoutees += 1
            else:
                mises_a_jour += 1
    return ajoutees, mises_a_jour


def injecter_date_detection(donnees, date_detection):
    """Rend la date de PREMIERE detection a une ligne relue en base. Fonction
    PURE (testable sans base).

    Regles, dans cet ordre :
      1. `donnees` n'est pas un dict (cas theorique) -> renvoye tel quel ;
      2. `donnees["date_detection"]` deja renseigne -> INTACT. Les lignes du
         rattrapage portent la date reelle du Sheet : elle fait autorite sur
         la colonne, qui vaut la date de premiere ECRITURE EN BASE ;
      3. sinon, la colonne est copiee en ISO (AAAA-MM-JJ), format attendu par
         `radar_dashboard._age_jours` et `_mois_depuis_date` ;
      4. colonne vide -> on ne fabrique rien (le lecteur retombera sur son
         repli habituel, pas sur une date inventee).

    Ne modifie JAMAIS le dict d'entree (copie a l'ecriture) : le miroir peut
    relire la meme ligne plusieurs fois sans effet de bord.

    RADAR_DATE_DET_PG=0 restitue exactement le comportement d'avant
    (interrupteur de repli, sans redeploiement)."""
    if not DATE_DET_PG or not isinstance(donnees, dict):
        return donnees
    if str(donnees.get("date_detection") or "").strip():
        return donnees
    if not date_detection:
        return donnees
    iso = (date_detection.isoformat()
           if hasattr(date_detection, "isoformat") else str(date_detection))
    copie = dict(donnees)
    copie["date_detection"] = iso[:10]
    return copie


def publications_existantes(conn, onglet):
    """Identifiants deja en base pour un onglet : la meme memoire que
    `ted.numeros_publication_existants` cote Sheet."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT publication_number FROM radar_lignes"
            " WHERE onglet = %s AND publication_number <> ''", (onglet,))
        return {r[0] for r in cur.fetchall()}


def lire_onglet(conn, onglet):
    """Lignes d'un onglet, plus recentes d'abord, sous la forme exacte
    qu'ecrivent les collecteurs (ce que lira le dashboard a l'etape 3).

    DATE DE PREMIERE DETECTION (correctif 25/08/2026)
    -------------------------------------------------
    Le JSONB `donnees` est la ligne telle que l'ecrit le collecteur. Or les
    collecteurs n'y mettent PAS `date_detection` : cette colonne est ajoutee
    a part, cote Sheet (append) et cote base (colonne dediee, jamais
    reecrite). Resultat, l'application relisait des lignes SANS
    `date_detection` et retombait sur `date_maj` -- qui, elle, vaut la date
    du RUN et est rafraichie a chaque miroir. Tout ce qui rentrait dans la
    fenetre de collecte s'affichait donc « detecte aujourd'hui », faussait le
    badge « nouveau », le graphe des detections par mois, le tri par
    fraicheur et le coup d'oeil sante des sources.

    On rend donc ici la colonne `date_detection` a la ligne, SANS jamais
    ecraser une valeur deja presente dans le JSONB (lignes du rattrapage, qui
    portent la vraie date du Sheet et font autorite)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT donnees, date_detection FROM radar_lignes WHERE onglet = %s"
            " ORDER BY id DESC", (onglet,))
        return [injecter_date_detection(r[0], r[1]) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# STATUTS (zone de saisie HUMAINE) -- table SEPAREE, et c'est tout l'interet :
# "contacte", "perdu", "gagne"... evoluent au fil de la prospection, sans
# jamais croiser les donnees de collecte. C'est cette separation qui autorise
# `radar_lignes` a rafraichir ses lignes sans risque pour le travail humain.
# ---------------------------------------------------------------------------

def definir_statut(conn, onglet, publication_number, statut, motif="",
                   valeur_estimee=None):
    """Upsert ASSUME du statut d'un lead. Cle : (onglet, publication_number).
    `motif` trace la RAISON d'un ecartement ou d'une perte.
    `valeur_estimee` (P1.1) est saisie a la main au passage en « contacte ».

    JOURNALISE la transition dans `radar_outcomes` avant d'ecraser l'etat
    courant. C'est l'ordre qui compte : lire l'ancien statut D'ABORD, sinon la
    transition est perdue. Toute la boucle d'apprentissage repose la-dessus.

    La valeur estimee n'est JAMAIS effacee par une transition ulterieure qui
    ne la porte pas : marquer « gagne » sans ressaisir le montant ne doit pas
    faire disparaitre le montant saisi au moment du contact."""
    pub = str(publication_number or "")
    statut = str(statut or "").strip().lower()
    motif = str(motif or "").strip()
    with conn.cursor() as cur:
        # 1. Etat AVANT, pour pouvoir journaliser la transition.
        cur.execute(
            "SELECT statut FROM radar_statuts"
            " WHERE onglet = %s AND publication_number = %s", (onglet, pub))
        ligne = cur.fetchone()
        precedent = (ligne[0] if ligne else "") or ""

        # 2. Journal append-only. Une transition non enregistree ne se
        #    reconstitue pas apres coup : on ecrit meme si l'etat ne change
        #    pas (un re-clic est une information sur l'hesitation).
        cur.execute(
            "INSERT INTO radar_outcomes (onglet, publication_number, statut,"
            " statut_precedent, motif, valeur_estimee)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (onglet, pub, statut, precedent, motif, valeur_estimee))

        # 3. Etat courant.
        cur.execute(
            "INSERT INTO radar_statuts (onglet, publication_number, statut,"
            " motif, valeur_estimee)"
            " VALUES (%s, %s, %s, %s, %s)"
            " ON CONFLICT (onglet, publication_number)"
            " DO UPDATE SET statut = EXCLUDED.statut, motif = EXCLUDED.motif,"
            " valeur_estimee = COALESCE(EXCLUDED.valeur_estimee,"
            "                           radar_statuts.valeur_estimee),"
            " maj = now()",
            (onglet, pub, statut, motif, valeur_estimee))


def lire_outcomes(conn, depuis=None):
    """Issues COMMERCIALES (gagne / perdu) du journal, plus recentes d'abord.

    C'est la source que `radar_retroaction` doit lire, a la place de la
    colonne `statut` de l'onglet prive -- laquelle ne contiendra jamais
    d'issue puisque l'interface n'en emettait aucune (le defaut corrige par
    P1.1). Renvoie une liste de dicts, vide si la table n'existe pas encore
    (base anterieure a la migration)."""
    sql = ("SELECT onglet, publication_number, statut, statut_precedent,"
           " motif, valeur_estimee, cree_le FROM radar_outcomes"
           " WHERE statut IN ('gagne', 'perdu')")
    params = []
    if depuis:
        sql += " AND cree_le >= %s"
        params.append(depuis)
    sql += " ORDER BY cree_le DESC"
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [{"onglet": o, "publication_number": p, "statut": s,
                     "statut_precedent": sp, "motif": m,
                     "valeur_estimee": (float(v) if v is not None else None),
                     "cree_le": c}
                    for o, p, s, sp, m, v, c in cur.fetchall()]
    except Exception as e:
        print("(stockage) lire_outcomes indisponible : {}".format(str(e)[:90]))
        return []


def compter_issues(conn):
    """{'gagne': n, 'perdu': n} -- de quoi savoir si le seuil d'apprentissage
    est atteint AVANT de sortir la retroaction du mode ombre."""
    out = {"gagne": 0, "perdu": 0}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT statut, COUNT(*) FROM radar_outcomes"
                        " WHERE statut IN ('gagne', 'perdu') GROUP BY statut")
            for statut, n in cur.fetchall():
                out[statut] = int(n)
    except Exception:
        pass
    return out


def lire_statuts(conn):
    """{(onglet, publication_number): statut}, pour superposer la zone humaine
    aux lignes de collecte au moment de la lecture."""
    with conn.cursor() as cur:
        cur.execute("SELECT onglet, publication_number, statut FROM radar_statuts")
        return {(o, p): s for o, p, s in cur.fetchall()}


def lire_motifs(conn):
    """{(onglet, publication_number): motif} pour les leads ecartes. Sert a
    afficher POURQUOI un lead a ete ecarte (section « Ecartes ») et a agreger
    les raisons pour l'apprentissage. Vide si la colonne n'existe pas encore."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT onglet, publication_number, motif FROM radar_statuts"
                        " WHERE motif <> ''")
            return {(o, p): m for o, p, m in cur.fetchall()}
    except Exception:
        return {}


def inventaire(conn):
    """{onglet: nombre de lignes}, pour le compte-rendu du run."""
    with conn.cursor() as cur:
        cur.execute("SELECT onglet, count(*) FROM radar_lignes"
                    " GROUP BY onglet ORDER BY onglet")
        return dict(cur.fetchall())


# ===========================================================================
# POINT D'ENTREE DES COLLECTEURS (etape 2 : double ecriture)
# ===========================================================================

def ecrire_miroir(onglet, lignes):
    """Miroir best-effort : a appeler par les collecteurs APRES leur ecriture
    Sheet. Renvoie une phrase pour le journal, ne leve JAMAIS : un Postgres
    en panne, mal configure ou absent ne doit pas coter un seul lead.

    Exemple d'usage (PR suivante) :
        print("(pg) " + radar_stockage.ecrire_miroir(NOM_ONGLET, attributions))
    """
    if not actif():
        return "miroir inactif (RADAR_PG=0 ou DATABASE_URL absent)"
    try:
        with connexion() as conn:
            initialiser(conn)
            ajoutees, mises_a_jour = ajouter_lignes(conn, onglet, lignes)
        return "miroir '{}' : {} ajoutee(s), {} mise(s) a jour".format(
            onglet, ajoutees, mises_a_jour)
    except Exception as e:
        return "miroir '{}' indisponible ({}) -- run non affecte".format(
            onglet, str(e)[:120])


def lire_miroir(onglet, limite=500):
    """Lignes d'un onglet depuis le miroir, ou []. Ne leve JAMAIS.

    SOURCE DE SECOURS. Constatee necessaire au premier run de production du
    24/08/2026 : l'ecriture Sheet avait echoue (portee OAuth insuffisante)
    alors que le miroir Postgres, lui, etait correctement alimente. Sans
    lecture possible, des donnees disponibles restaient invisibles.

    C'est aussi un premier pas vers le decouplage du Google Sheet, qui reste
    aujourd'hui un point de defaillance unique (quota, panne, droits)."""
    if not actif():
        return []
    try:
        with connexion() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT donnees FROM radar_lignes WHERE onglet = %s"
                    " ORDER BY maj DESC LIMIT %s", (onglet, int(limite)))
                lignes = cur.fetchall()
        out = []
        for (donnees,) in lignes:
            if isinstance(donnees, dict):
                out.append(donnees)
            elif donnees:
                try:
                    out.append(json.loads(donnees))
                except (TypeError, ValueError):
                    continue
        return out
    except Exception:
        return []


# ===========================================================================
# MAIN : verification autonome (workflow "Stockage Postgres")
# ===========================================================================

def main():
    if not ACTIVER:
        print("(info) Miroir Postgres desactive (RADAR_PG=0).")
        return
    if not os.environ.get(URL_ENV):
        print("(info) DATABASE_URL absent : rien a verifier. Creer le secret"
              " GitHub puis relancer ce workflow.")
        return
    if not actif():
        print("(erreur) psycopg introuvable : ajouter 'psycopg[binary]' a"
              " l'etape d'installation du workflow.")
        return
    print("Verification du stockage Postgres...")
    try:
        with connexion() as conn:
            initialiser(conn)
            etat = inventaire(conn)
    except Exception as e:
        print("(erreur) connexion ou schema impossible : {}".format(e))
        raise SystemExit(1)
    print("  schema OK (idempotent).")
    if not etat:
        print("  base vide : normal avant l'activation de la double ecriture.")
    for onglet, nb in etat.items():
        print("  {:24} {} ligne(s)".format(onglet, nb))


if __name__ == "__main__":
    main()
