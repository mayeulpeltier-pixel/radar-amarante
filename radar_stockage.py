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
non vide. L'insertion se fait en ON CONFLICT DO NOTHING : une ligne existante
n'est JAMAIS reecrite -- c'est la transposition exacte de la garde du Sheet
qui protege `statut_prospection` (zone de saisie humaine). Les requetes
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
    maj                TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (onglet, publication_number)
);
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


def ajouter_lignes(conn, onglet, lignes):
    """Insere les lignes d'un onglet. (ajoutees, ignorees).

    ON CONFLICT DO NOTHING : une ligne deja presente (meme onglet, meme
    publication_number) est IGNOREE, jamais mise a jour. Meme promesse que
    l'ecriture Sheet : ce qui est en base -- y compris de futures saisies
    humaines -- survit a tous les runs."""
    ajoutees = ignorees = 0
    with conn.cursor() as cur:
        for ligne in lignes or []:
            pub, donnees = preparer_ligne(ligne)
            cur.execute(
                "INSERT INTO radar_lignes (onglet, publication_number, donnees)"
                " VALUES (%s, %s, %s::jsonb)"
                " ON CONFLICT (onglet, publication_number)"
                " WHERE publication_number <> '' DO NOTHING",
                (onglet, pub, json.dumps(donnees, ensure_ascii=False)))
            if cur.rowcount:
                ajoutees += 1
            else:
                ignorees += 1
    return ajoutees, ignorees


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
# STATUTS (zone de saisie HUMAINE) -- la seule table ou la reecriture est
# permise, car c'est sa raison d'etre : "contacte", "perdu", "gagne"...
# evoluent au fil de la prospection. radar_lignes reste, elle, en ajout seul.
# ---------------------------------------------------------------------------

def definir_statut(conn, onglet, publication_number, statut):
    """Upsert ASSUME du statut d'un lead. Cle : (onglet, publication_number)."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO radar_statuts (onglet, publication_number, statut)"
            " VALUES (%s, %s, %s)"
            " ON CONFLICT (onglet, publication_number)"
            " DO UPDATE SET statut = EXCLUDED.statut, maj = now()",
            (onglet, str(publication_number or ""), str(statut or "")))


def lire_statuts(conn):
    """{(onglet, publication_number): statut}, pour superposer la zone humaine
    aux lignes de collecte au moment de la lecture."""
    with conn.cursor() as cur:
        cur.execute("SELECT onglet, publication_number, statut FROM radar_statuts")
        return {(o, p): s for o, p, s in cur.fetchall()}


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
            ajoutees, ignorees = ajouter_lignes(conn, onglet, lignes)
        return "miroir '{}' : {} ajoutee(s), {} deja connue(s)".format(
            onglet, ajoutees, ignorees)
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
