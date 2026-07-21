# CLAUDE.md — Radar Amarante

> **Lis ce fichier en entier avant toute modification du dépôt.**
> Il fait autorité sur les conventions du projet. En cas de doute, poser la
> question plutôt que de supposer.

---

## 1. Mission du projet

**Radar Amarante** est un outil d'intelligence commerciale pour une société
française de sécurité privée opérant en zones à risque (Sahel, MENA, Ukraine,
Asie centrale). Il détecte des opportunités de contrats de sécurité (protection
rapprochée, escorte, sûreté de sites) **avant les concurrents**, en surveillant :

- les appels d'offres publics et multilatéraux (TED, Banque Mondiale, AfDB, EBRD, UNGM, IsDB),
- les signaux humanitaires (ReliefWeb),
- les mouvements du secteur privé (offres d'emploi Adzuna, Google News, watchlist de 201 entreprises),
- les attributions de marchés (les titulaires nommés deviennent des prospects "mobilisation").

**Langue de travail : français**, partout (code, commentaires, commits, PR, docstrings).

---

## 2. Architecture d'ensemble

```
Collecteurs Python ──► Google Sheet (stockage) ──► radar_dashboard.py ──► public/index.html ──► Cloudflare Pages
        │
        └──► radar_etat.json (état inter-runs, committé dans le dépôt, [skip ci])
```

- **Orchestration** : GitHub Actions (`radar.yml`), lundi et jeudi 06h00 UTC + déclenchement manuel.
- **Le Sheet est le stockage des données** ; l'état inter-runs (articles vus, curseurs)
  est découplé dans `radar_etat.json` (fin du SPOF Sheet pour l'état).
- **Dashboard à trois lentilles** :
  1. **Opportunités · avis** (TED, BM, AfDB, EBRD, UNGM, ReliefWeb)
  2. **Cibles privées · prospects** (watchlist, colonnes sectorielles dynamiques)
  3. **Titulaires · attributions** (TED, BM, UNGM, IsDB — mini-score déterministe)

---

## 3. Inventaire des fichiers

### Cœur et orchestration
| Fichier | Rôle |
|---|---|
| `radar_run.py` | Master d'orchestration. Vérifie le contrat d'interface TED (14 symboles), lance TED puis BM, chacun isolé. Code de sortie ≠ 0 si échec. |
| `ted_complet_v14.py` | **Cœur du système.** Collecteur TED + fonctions partagées (scoring, LLM, session robuste, écriture Sheet). Les autres collecteurs lui empruntent des symboles : **ne jamais renommer une fonction publique sans vérifier `SYMBOLES_REQUIS_TED` dans `radar_run.py`.** |
| `radar_dashboard.py` | Génère `public/index.html` depuis le Sheet. Gros fichier (~120 Ko), HTML/JS inline. |
| `radar_etat.py` / `radar_etat.json` | État inter-runs versionné, committé avec `[skip ci]`. |
| `suivi_config.py` | Config du suivi "Je contacte" (webapp Apps Script). `SUIVI_TOKEN` vient d'un secret, jamais du dépôt. |

### Collecteurs d'avis
| Fichier | Source | État |
|---|---|---|
| `ted_complet_bm.py` | Banque Mondiale | Actif |
| `afdb_radar.py` | AfDB (RSS) | Actif |
| `ebrd_radar.py` | EBRD (portail ECEPP) | Actif |
| `ted_complet_reliefweb.py` | ReliefWeb (appname dédié) | Actif |
| `ungm_radar.py` | UNGM avis (agences ONU) | Actif |
| `adb_radar.py` | ADB | **Désactivé** (`RADAR_ADB=0`) : portail rendu en JS, 0 résultat depuis l'infra CI. Code conservé. |
| `ted_complet_boamp.py` | BOAMP | **Mort** (rendement nul). Code conservé pour le schéma. |

### Attributions (titulaires)
| Fichier | Source |
|---|---|
| `ted_complet_attributions.py` | TED |
| `bm_attributions.py` | Banque Mondiale (parsing PDF des gagnants, fragile) |
| `ungm_attributions.py` | UNGM (endpoint `/PublicSearch` relevé dans le JS du portail, 20/07/2026 ; pas de montant ni pays fournisseur sur la fiche) |
| `isdb_radar.py` | IsDB (fiche avec nom ET pays du titulaire → filtre local/étranger fonctionne) |

Tous écrivent dans le **même onglet attributions** : aucun câblage dashboard
nécessaire pour une nouvelle source d'attributions.

### Signaux privés et enrichissement
| Fichier | Rôle |
|---|---|
| `signaux_prives.py` | Watchlist 201 entreprises, Adzuna (7 portails), Google News. Debug : `RADAR_PRIVES_DEBUG=1`. |
| `bitd_signaux.py` | Signaux BITD complémentaires. |
| `enrichir_entreprises.py` | GLEIF (gratuit, international) + Hunter Domain Search (budget global). |
| `radar_retroaction.py` | Multiplicateurs bayésiens bornés [0.85, 1.15], N_min=8. **OFF par défaut** (`RADAR_RETROACTION=1` pour activer). |
| `radar_digest.py` | Digest push e-mail des nouveaux leads "à contacter", via le webapp Apps Script (pas de SMTP dans le dépôt). Best-effort, isolé. |

### Outils et sauvegarde
| Fichier | Rôle |
|---|---|
| `backup_sheet.py` + `backup.yml` | Un CSV par onglet. **Refuse de committer si le dépôt est public** (protection de l'intelligence commerciale). Ne jamais retirer cette garde. |
| `sonde_sources.py` + `sonde.yml` | Sonde jetable, déclenchement manuel uniquement, aucune écriture. Sert à tester l'accessibilité d'une source depuis l'infra CI **avant** d'écrire un collecteur. |

### Tests
`test_radar.py` (suite principale, ~124 Ko), `test_adb.py`, `test_afdb.py`, `test_ebrd.py`.
Découverte : `python -m unittest discover -p "test_*.py"`. **59+ tests, tous verts, toujours.**

---

## 4. Règles non négociables

1. **Fichiers complets, jamais de snippets.** Toute livraison est un fichier
   entier, prêt à coller. Le workflow de déploiement humain est : crayon
   GitHub → Ctrl+A → coller → Commit. Pas d'accès à un poste de dev local.
2. **Discipline des paires.** Un collecteur modifié = son fichier de test
   modifié et livré **en même temps**. Jamais l'un sans l'autre (sinon
   décalage de version en CI).
3. **Tests d'abord en CI.** `radar.yml` lance les tests avant tout. Si un test
   casse, rien ne tourne. Ne jamais affaiblir ou supprimer un test pour faire
   passer un build : corriger le code ou discuter.
4. **Préservation du schéma.** Les colonnes du Sheet sont conservées même quand
   la logique sous-jacente disparaît (ex. `source_mode_b` toujours False).
   Ne jamais supprimer ni réordonner une colonne sans validation explicite.
5. **Motif ADB.** Une source inaccessible depuis l'infra CI (403, rendu JS)
   se **désactive par variable d'environnement**, le code reste. On ne supprime pas.
6. **Logique commerciale, pas de kill-switch booléen.**
   `securite_existante` est un enum à 4 valeurs :
   `aucune / interne_client / prestataire_tiers / inconnu`.
   Seul `interne_client` supprime un lead. `prestataire_tiers` fait remonter
   une opportunité préfixée `[DÉPLACEMENT CONCURRENT]`.
7. **Normalisation conservatrice.** Le rapprochement de noms d'entreprises
   retire les suffixes juridiques (SA, SARL, Ltd…) mais reste prudent.
   Priorité d'une fiche = meilleur score signal.
8. **Aucun secret dans le dépôt.** Tokens, clés API, `SUIVI_TOKEN` : secrets
   GitHub Actions uniquement.
9. **`radar_etat.json`** se committe avec le message
   `chore(radar): etat inter-runs [skip ci]`, jamais autrement.
10. **Deux échelles de score distinctes à l'affichage** : score signal (privé)
    vs score avis (marché). Ne pas les mélanger.

---

## 5. Variables d'environnement

| Variable | Rôle | Défaut |
|---|---|---|
| `ANTHROPIC_API_KEY` | LLM (scoring/raffinement) | requis |
| `TED_SHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_FILE` | Écriture Sheet | console si absents |
| `RADAR_LOG_FILE` | Journal du run | `radar.log` |
| `RELIEFWEB_APPNAME` | Appname ReliefWeb | `amarante-radar-veille-x7q234hyu` |
| `RADAR_ADB` | ADB on/off | `0` (off) |
| `RADAR_GDELT` | GDELT on/off | `0` (off, définitif) |
| `RADAR_RETROACTION` | Rétroaction bayésienne | off |
| `RADAR_PRIVES_DEBUG` | Trace des rejets Adzuna | `0` |
| `RADAR_BM_ATTRIB_DEBUG`, `RADAR_UNGM_DEBUG`, `RADAR_UNGM_ATTRIB_DEBUG`, `RADAR_ISDB_DEBUG` | Mode vérification sans écriture | variable |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | Adzuna | secrets |
| `SUIVI_WEBAPP_URL`, `SUIVI_TOKEN` | Bouton "Je contacte" + digest | secrets |
| `DASHBOARD_OUTPUT` | Sortie dashboard | `public/index.html` |

**État connu au 21/07/2026** : `RADAR_ISDB_DEBUG=1` dans `radar.yml`
(IsDB en phase de validation, n'écrit rien). À passer à `0` après validation
des titulaires extraits.

---

## 6. Méthode de travail attendue

- **Lire avant de diagnostiquer.** Toujours ouvrir les fichiers réels avant de
  proposer un changement. Aucune supposition sur l'état du code.
- **Compromis avant code.** Pour tout chantier significatif : diagnostic,
  2-3 options avec leurs compromis, validation humaine, puis implémentation.
- **Un chantier = une branche = une PR.** Petits périmètres, mergés vite.
  Pas de PR fourre-tout.
- **Chaque PR contient** : résumé, pourquoi, risques, fichiers modifiés,
  tests ajoutés/modifiés, comportement avant/après.
- **Sonde avant collecteur.** Nouvelle source envisagée → d'abord
  `sonde_sources.py` (accessibilité depuis l'infra CI), ensuite seulement le
  collecteur. Dumper le HTML brut (leçon UNGM : deux tours perdus faute de
  l'avoir fait).
- **Isolation des étapes.** Chaque collecteur du workflow est isolé
  (`if: always()`) : un échec n'empêche ni les autres, ni le dashboard, ni la
  publication. Préserver ce principe pour tout ajout.

---

## 7. Feuille de route (cap produit)

Objectif : passer d'un prototype interne à un **logiciel professionnel
vendable** (multi-client, faible coût d'exploitation).

1. **Stockage** : Google Sheets → SQLite (le Sheet devient un export, plus la
   source de vérité), puis Postgres managé.
2. **Dashboard statique → application web** avec authentification
   (l'URL Cloudflare actuelle expose l'intelligence commerciale à quiconque la connaît).
3. **Configuration paramétrable** : watchlist, pays, pondérations, sources
   éditables par client, hors code.
4. **Instance par client** (Docker à terme).
5. Reliquat audit : étendre les tests aux collecteurs privés/attributions
   (parsing PDF BM fragile), multiplicateurs de risque dynamiques
   (`risque_pays` avec fallback codé en dur, déjà en place).

Toute proposition d'amélioration se juge à l'aune de ce cap : **valeur
commerciale d'abord, élégance technique ensuite.**
