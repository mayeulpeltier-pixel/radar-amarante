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
"""


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
    qu'ecrivent les collecteurs (ce que lira le dashboard a l'etape 3)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT donnees FROM radar_lignes WHERE onglet = %s"
            " ORDER BY id DESC", (onglet,))
        return [r[0] for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# STATUTS (zone de saisie HUMAINE) -- table SEPAREE, et c'est tout l'interet :
# "contacte", "perdu", "gagne"... evoluent au fil de la prospection, sans
# jamais croiser les donnees de collecte. C'est cette separation qui autorise
# `radar_lignes` a rafraichir ses lignes sans risque pour le travail humain.
# ---------------------------------------------------------------------------

def definir_statut(conn, onglet, publication_number, statut, motif=""):
    """Upsert ASSUME du statut d'un lead. Cle : (onglet, publication_number).
    `motif` (optionnel) trace la RAISON d'un ecartement (« Pas pertinent »)."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO radar_statuts (onglet, publication_number, statut, motif)"
            " VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (onglet, publication_number)"
            " DO UPDATE SET statut = EXCLUDED.statut, motif = EXCLUDED.motif, maj = now()",
            (onglet, str(publication_number or ""), str(statut or ""), str(motif or "")))


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
