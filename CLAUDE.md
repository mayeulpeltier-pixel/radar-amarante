# CLAUDE.md — Radar Amarante

> **Lis ce fichier en entier avant toute modification du dépôt.**
> Il fait autorité sur les conventions du projet. En cas de doute, poser la
> question plutôt que de supposer.
>
> Dernière mise à jour : **22 juillet 2026** (bascule Postgres + application web).

---

## 1. Mission du projet

**Radar Amarante** est un outil d'intelligence commerciale pour une société
française de sécurité privée opérant en zones à risque (Sahel, MENA, Ukraine,
Asie centrale). Il détecte des opportunités de contrats de sécurité (protection
rapprochée, escorte, sûreté de sites) **avant les concurrents**, en surveillant :

- les appels d'offres publics et multilatéraux (TED, Banque Mondiale, AfDB, EBRD, UNGM, IsDB),
- les signaux humanitaires (ReliefWeb),
- les mouvements du secteur privé (offres d'emploi Adzuna, Google News, watchlist),
- les attributions de marchés (les titulaires nommés deviennent des prospects "mobilisation").

**Langue de travail : français**, partout (code, commentaires, commits, PR, docstrings).

**Cap produit** : passer d'un prototype interne à un **logiciel professionnel
vendable** (multi-client, faible coût d'exploitation). Toute proposition se juge
à cette aune : **valeur commerciale d'abord, élégance technique ensuite.**

---

## 2. Architecture (état au 22/07/2026)

```
                    GitHub Actions (lundi + jeudi 06h00 UTC)
                                 │
                    ┌────────────┴────────────┐
                    ▼                         ▼
            Collecteurs Python        radar_etat.json
                    │                 (curseurs, vus)
        ┌───────────┴───────────┐
        ▼                       ▼
  Google Sheet            Postgres / Neon          ◄── SOURCE DE VÉRITÉ
  (écriture seule,        radar_lignes  (collecte, ajout seul)
   export + secours)      radar_statuts (zone humaine, upsert)
        │                       │
        ▼                       ▼
  radar_dashboard.py      radar_app.py (FastAPI)
        │                       │
        ▼                       ▼
  Cloudflare Pages        Render  ◄── L'APPLICATION
  (statique, protégé      (auth, gzip, cache,
   par Cloudflare Access)  API de statuts)
```

**Ce qui a changé le 22/07/2026** (une seule journée, tout est documenté ci-dessous) :
la mémoire inter-runs ne lit plus le Sheet, l'application web existe, les statuts
s'écrivent en base. **Le Sheet ne sert plus qu'à écrire** : export lisible et
filet de secours, plus source de vérité.

### Infrastructure

| Brique | Service | Coût | Notes |
|---|---|---|---|
| Base de données | **Neon** (Postgres managé) | gratuit | secret `DATABASE_URL` |
| Application web | **Render** (blueprint `render.yaml`) | gratuit | s'endort après 15 min, réveil ~50 s |
| Page statique | Cloudflare Pages | gratuit | derrière Cloudflare Access |
| Orchestration | GitHub Actions | quota privé | timeout job **150 min** |
| Stockage export | Google Sheets | gratuit | écriture seule désormais |

⚠️ **`render.yaml`, `requirements.txt` et `.python-version` vont à la RACINE du
dépôt**, pas dans `.github/workflows/`. C'est la seule exception : tous les
autres `.yml` sont des workflows GitHub. Python est épinglé à **3.12** via
`.python-version` (Render prenait 3.14 par défaut).

---

## 3. Inventaire des fichiers

### Stockage et application (nouveaux, 22/07/2026)
| Fichier | Rôle |
|---|---|
| `radar_stockage.py` | Couche Postgres. `radar_lignes` (JSONB, **ajout seul**, `ON CONFLICT DO NOTHING`) et `radar_statuts` (**seule table où l'upsert est permis**). `ecrire_miroir()` est le point d'entrée des collecteurs : best-effort absolu, ne lève jamais. |
| `radar_app.py` | Application FastAPI. Lit Postgres, réutilise `construire_leads` + `generer_html` **tels quels** (zéro duplication de rendu). Auth HTTP Basic, gzip, cache 10 min, `POST /api/statut`, `/sante`. |
| `radar_rattrapage.py` | Verse tout le Sheet dans le miroir. **Lecture positionnelle** pour les 11 onglets à schéma connu. Rejouable, jamais destructif. `RADAR_RATTRAPAGE_PURGE=1` pour réimporter proprement. |
| `requirements.txt`, `render.yaml`, `.python-version` | Déploiement Render (racine du dépôt). |

### Cœur et orchestration
| Fichier | Rôle |
|---|---|
| `radar_run.py` | Master d'orchestration. Vérifie le contrat d'interface TED (14 symboles), lance TED puis BM, chacun isolé. |
| `ted_complet_v14.py` | **Cœur du système.** Collecteur TED + fonctions partagées (scoring, LLM, session robuste, écriture Sheet, **mémoire inter-runs**). Les autres collecteurs lui empruntent des symboles : ne jamais renommer une fonction publique sans vérifier `SYMBOLES_REQUIS_TED` dans `radar_run.py`. |
| `radar_dashboard.py` | Génère le HTML. `generer_html(leads, watchlist, api_statut=False)` : `False` = page statique Cloudflare, `True` = servie par l'application (le bouton écrit aussi en base). |
| `radar_etat.py` / `radar_etat.json` | Curseurs et vus, committés avec `[skip ci]`. |
| `suivi_config.py` | Config Apps Script. `SUIVI_TOKEN` vient d'un secret. |

### Collecteurs d'avis
| Fichier | Source | État |
|---|---|---|
| `ted_complet_bm.py` | Banque Mondiale | Actif · budget `BM_BUDGET` (150) |
| `ted_complet_reliefweb.py` | ReliefWeb | Actif · budget `RELIEFWEB_BUDGET` (120) |
| `afdb_radar.py` | AfDB (RSS) | Actif · `AFDB_BUDGET` |
| `ebrd_radar.py` | EBRD (portail ECEPP) | Actif · `EBRD_BUDGET` |
| `ungm_radar.py` | UNGM (agences ONU) | Actif |
| `adb_radar.py` | ADB | **Désactivé** (`RADAR_ADB=0`), portail rendu en JS |
| `ted_complet_boamp.py` | BOAMP | **Mort** (rendement nul), code conservé |

### Attributions (titulaires) — onglet partagé `attributions_radar`
`ted_complet_attributions.py` (TED) · `bm_attributions.py` (BM, parsing PDF fragile) ·
`ungm_attributions.py` (UNGM) · `isdb_radar.py` (IsDB).
Une nouvelle source d'attributions ne demande **aucun câblage dashboard**.

### Signaux privés, enrichissement, outils
`signaux_prives.py` (watchlist 408, Adzuna, Google News) · `bitd_signaux.py` ·
`enrichir_entreprises.py` (GLEIF + Hunter) · `radar_retroaction.py` (OFF par
défaut) · `radar_digest.py` · `backup_sheet.py` (**refuse de committer si le
dépôt est public**) · `sonde_sources.py` (sonde jetable, manuelle).

### Tests — **400 tests, tous verts**
`test_radar.py` (principal) · `test_adb/afdb/ebrd.py` · `test_stockage.py`
(couche + rattrapage) · `test_app.py` (application + bouton) ·
`test_bm_ecriture.py` (garde d'écriture + miroir attributions) ·
`test_miroir_avis.py` (câblage des 6 écrivains d'avis) · `test_memoire.py`
(mémoire inter-runs) · `test_budget.py` (ordre mémoire/plafond, priorité).

Découverte : `python -m unittest discover -p "test_*.py"`.
Les tests d'intégration Postgres se sautent seuls sans `RADAR_TEST_DATABASE_URL`.

---

## 4. Règles non négociables

1. **Fichiers complets, jamais de snippets.** Workflow humain : crayon GitHub →
   Ctrl+A → coller → Commit. Pas de poste de dev local.
2. **Discipline des paires.** Collecteur modifié = son test livré en même temps.
3. **Tests d'abord en CI.** Ne jamais affaiblir un test pour faire passer un build.
4. **LECTURE POSITIONNELLE, JAMAIS PAR EN-TÊTE.** Un en-tête de Sheet peut être
   désaligné. C'est arrivé (`bm_radar`, décalage d'une colonne) : le rattrapage
   avait rangé les **numéros de téléphone** sous `publication_number`.
5. **`radar_lignes` en ajout seul.** Seule `radar_statuts` accepte l'upsert :
   c'est la zone de saisie humaine, elle doit évoluer. Une ligne de collecte
   n'est jamais réécrite (transposition de la garde `statut_suivi` du Sheet).
6. **Préservation du schéma.** Colonnes conservées même quand la logique
   disparaît (`source_mode_b` toujours False).
7. **Motif ADB.** Une source inaccessible se **désactive par variable
   d'environnement**, le code reste.
8. **Logique commerciale, pas de kill-switch.** `securite_existante` est un enum
   à 4 valeurs ; seul `interne_client` supprime un lead, `prestataire_tiers`
   fait remonter `[DÉPLACEMENT CONCURRENT]`.
9. **Mémoire AVANT plafond.** Le budget d'analyse sert à **découvrir**, pas à
   redécouvrir. Verrouillé par `test_budget.py` pour les 4 collecteurs à budget.
10. **Aucun secret dans le dépôt.** Secrets GitHub / variables Render uniquement.
11. **Le miroir ne peut jamais faire échouer un run.** `ecrire_miroir` avale
    toutes les exceptions et renvoie une phrase de journal.

---

## 5. Variables d'environnement

### Collecteurs (secrets et variables GitHub Actions)
| Variable | Rôle | Défaut |
|---|---|---|
| `ANTHROPIC_API_KEY` | LLM | requis |
| `TED_SHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_FILE` | Sheet (écriture) | requis |
| `DATABASE_URL` | Postgres Neon | secret |
| `RADAR_MEMOIRE` | `pg` = Postgres fait foi ; `0`/absent = Sheet | **`pg`** |
| `RADAR_PG` | `0` coupe le miroir | actif |
| `BM_BUDGET`, `RELIEFWEB_BUDGET`, `AFDB_BUDGET`, `EBRD_BUDGET` | plafonds LLM par run | 150 / 120 / … |
| `RADAR_ADB`, `RADAR_GDELT` | sources coupées | `0` |
| `RADAR_RETROACTION` | rétroaction bayésienne | off |
| `RADAR_PRIVES_DEBUG`, `RADAR_*_DEBUG` | traces, mode vérification sans écriture | `0` |
| `ADZUNA_APP_ID/KEY`, `SUIVI_WEBAPP_URL`, `SUIVI_TOKEN`, `RELIEFWEB_APPNAME` | sources et suivi | secrets |

### Application (variables Render)
| Variable | Rôle | Défaut |
|---|---|---|
| `DATABASE_URL` | même base Neon | requis |
| `RADAR_APP_MOT_DE_PASSE` | **sans lui, 503 partout** | requis |
| `RADAR_APP_UTILISATEUR` | identifiant | `radar` |
| `RADAR_APP_CACHE_S` | durée du cache page | `600` |

### Rattrapage (workflow manuel)
`RADAR_RATTRAPAGE_PURGE=1` (vide et réimporte les onglets à schéma connu) ·
`RADAR_RATTRAPAGE_EXCLUS` (onglets à sauter).

---

## 6. Méthode de travail attendue

- **Lire avant de diagnostiquer.** Toujours ouvrir les fichiers réels. Aucune
  supposition sur l'état du code.
- **Mesurer avant d'optimiser.** La page pesait 2,6 Mo : c'est la mesure, pas
  l'intuition, qui a désigné le bon chantier (gzip, 32x).
- **Instrumenter plutôt que deviner.** Deux hypothèses fausses sur `bm_radar`
  avant d'ajouter des exemples au journal, qui ont donné la réponse en un run.
- **Phase d'ombre avant toute bascule.** Lire les deux sources, journaliser
  l'écart, ne basculer qu'après constat. C'est ainsi qu'on a détecté une
  corruption de données que ni le dashboard ni l'app ne montraient.
- **Vérifier qu'un test peut échouer.** Un test neuf se soumet à l'ancien code
  pour prouver qu'il attrape bien la régression.
- **Compromis avant code** pour tout chantier significatif : diagnostic,
  2-3 options chiffrées, validation, puis implémentation.
- **Sonde avant collecteur** pour toute nouvelle source (leçon UNGM).
- **Isolation des étapes** (`if: always()`) : un échec n'empêche ni les autres,
  ni le dashboard, ni la publication.

---

## 7. Leçons de terrain (bugs réels, ne pas les refaire)

- **IsDB** : le filtre pays du portail est accepté mais **ignoré**. Les 6
  attributions sortaient toutes en `AFG`. Le pays vient désormais du **préfixe
  du code projet** de la fiche. Ne jamais faire confiance à un filtre serveur
  sans vérifier la sortie.
- **`bm_radar`** : en-tête désaligné d'une colonne → téléphones rangés en
  identifiants. D'où la règle 4.
- **Plafond avant mémoire** (BM et ReliefWeb) : 150 places dont 146 déjà
  connues, soit 4 analyses neuves par run, pendant que **278 offres attendaient
  sans jamais avoir leur tour**. Aucune erreur levée, aucun test rouge, des
  leads perdus pendant des mois.
- **Tri par risque seul** : sur une file de plusieurs runs, une offre de 28
  jours passait avant une offre d'hier. Le tri combine désormais **risque
  (dominant) et fraîcheur (départage)**.
- **`SUIVI_ON`** ne dépendait que d'Apps Script : sur Render, sans ce secret, le
  bouton aurait purement disparu.

---

## 8. Feuille de route

**Fait (22/07/2026)** : fondation Postgres · double écriture des 10 collecteurs ·
rattrapage historique · forme canonique unique · application web authentifiée,
compressée, en cache · statuts en base · lecture Sheet retirée du chemin critique ·
ordre mémoire/plafond corrigé · tri risque + fraîcheur.

**Reste à faire**
1. **Remettre `RELIEFWEB_BUDGET` à 120** une fois la file d'attente absorbée
   (et retirer `RADAR_RATTRAPAGE_PURGE` du workflow de rattrapage).
2. **Écriture Sheet optionnelle** (`RADAR_SHEET=0`) : dernière dépendance.
   Suppose d'abord de basculer le dashboard statique et `backup_sheet.py` sur
   Postgres, sinon ils gèlent.
3. **Unifier le CRM** : Apps Script écrit encore les statuts côté Sheet ; l'app
   les écrit en base. Deux sources de vérité tant que les deux pages coexistent.
4. **Configuration paramétrable** (watchlist, pays, pondérations en base,
   éditables) : le vrai déclencheur du multi-client.
5. **Multi-client** : une instance par client. ⚠️ Le cache de `radar_app` est
   **global au processus** : sa clé devra inclure l'identité du client, sinon
   fuite de données entre comptes (noté dans le code).
6. Étendre les tests au parsing PDF des gagnants BM (fragile).
