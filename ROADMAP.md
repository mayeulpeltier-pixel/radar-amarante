# Roadmap Radar Amarante

**Mise à jour : 16 août 2026.** Cette version remplace la feuille de route de juillet 2026, dont la majorité des points étaient en réalité déjà traités (vérifié dans le code). Elle intègre le cycle d'intégration TED Open Data / SPARQL et le tri de l'audit externe.

Principe directeur retenu : **outil interne BD piloté en solo**. On privilégie la valeur commerciale directe et le faible risque de maintenance, on écarte la sur-ingénierie de plateforme tant que la trajectoire produit n'est pas décidée.

---

## Fait et vérifié (ce cycle)

**Dette technique (ancien palier 1, entièrement bouclé)**
- ReliefWeb branché au dashboard et à l'app (n'était plus orphelin).
- `SUIVI_TOKEN` lu depuis l'environnement, pas en dur.
- Attributions sorties des KPI contacter/surveiller (barème propre).
- Échelles de score étiquetées (`echelleLabel` : avis / signal privé / attribution).
- Bandeau méta dérivé dynamiquement (plus hardcodé).
- Strings de modèles valides (`claude-sonnet-4-6`, `claude-haiku-4-5` actifs).
- `ted_complet_boamp.py` supprimé (code mort, jamais câblé ni lu).

**Référentiels officiels (eForms-SDK)**
- `pays_reference.py` : résolution nom↔ISO3 officielle, câblée dans signaux privés, dashboard (branche BM/RW), Proparco. Corrige les variantes qui tuaient des leads (Irak/Iraq, accents, RDC).
- `cpv_reference.py` : 45 divisions CPV officielles en fallback de `secteur_lisible`.

**Scoring**
- Normalisation des montants en EUR au point de scoring (`_facteur_eur`), corrige le biais des devises faibles (franc CFA du Sahel sur-pondéré). Table `TAUX_USD` complétée de 18 devises.

**Titulaires et renouvellement (via SPARQL TED Open Data)**
- `sparql_titulaires.py` : titulaires structurés (nom, montant, devise), SPARQL prioritaire, PDF en secours, disjoncteur. Flag `RADAR_SPARQL_TITULAIRES`.
- Détection de renouvellement : date de fin de contrat calculée (conclusion + durée), colonnes `fin_contrat` / `mois_avant_fin` / `statut_renouv`, badge « expire dans X mois » sur la ligne.
- Intelligence concurrents : badge « incumbent » (≥ 2 marchés) sur les fiches titulaires et entreprises.

**Infrastructure**
- Vues SQL analytiques (`v_attributions`, `v_incumbents`, `v_renouvellements`) posées sur le JSONB, sans toucher aux données.
- Pépites TED (filtre date serveur, backfill par tranches, failover endpoint, enrichissement PDF) livrées derrière flags.

**Déjà présent avant ce cycle (vérifié encore valide)**
- Vue fiche entreprise unifiée (`agregerEntreprises` : watchlist + presse + titulaire fusionnés).
- Boucle de rétroaction bayésienne (`radar_retroaction.py`), en mode ombre.
- Mémoire inter-runs découplée du Sheet (miroir Postgres).
- Multiplicateur dynamique via aggravation géopolitique (`geo_boost`).
- Enrichissement contact international (GLEIF mondial en repli de l'API gouv.fr).

---

## À valider (en attente d'un run réel)

1. **Flags date TED** : `RADAR_TED_FILTRE_DATE`, `RADAR_TED_BACKFILL_*`, `RADAR_TED_ENRICHIR`. Ils partagent la même syntaxe de bornes date non sondable depuis le conteneur. Un run debug valide les trois. Repli documenté si TED renvoie 400 : `PD>=` au lieu de `publication-date>=`.
2. **SPARQL titulaires + renouvellement** : mesurer sur un run avec de **nouvelles** attributions le taux réel de publication durée/date en zones à risque. Détermine si le renouvellement est un filon riche ou marginal.
3. **Nettoyage** : supprimer `sonde_sparql.py` et `sonde_sparql.yml` (jetables) une fois SPARQL validé en production.

---

## Reste à faire

**Priorité haute (valeur BD, faisable en additif)**
1. **Activer la rétroaction** (ombre → actif) une fois assez d'issues gagné/perdu accumulées. C'est de la calibration, pas du code neuf : le module existe déjà.
2. **Formaliser un sous-score accessibilité / winability**. La donnée `accessibilite_commerciale` existe déjà dans l'extraction LLM ; il reste à l'exposer et la pondérer explicitement (un marché juteux mais verrouillé par un incumbent ne doit pas passer « fort »). Partiellement là.

**Priorité moyenne**
3. **Hunter sur reliquat de budget** pour les titulaires étrangers (posture RGPD générique-email-first).
4. **Auditer le code mort résiduel** (GDELT, `bitd.main`, Mode B) et unifier le scoring privé en une seule fonction `scorer_signal`. Item partiellement traité (BOAMP fait).
5. **Étendre les tests** aux collecteurs privés et aux attributions. Moins critique depuis que SPARQL remplace le parsing PDF fragile des gagnants.

**Priorité basse**
6. **Brancher les vues SQL** à un consommateur (reporting/export) si un besoin analytique concret émerge. Sinon elles restent une réserve.
7. **Migration escalade vers `claude-sonnet-5`** (optionnel) : meilleure qualité, mais impose de retirer `temperature:0` et de re-valider le scoring. Amélioration, pas correction.

---

## Écarté (sur-ingénierie pour le contexte actuel)

Recommandations de l'audit externe évaluées puis écartées pour un outil interne solo. À rouvrir seulement si une trajectoire produit/SaaS est décidée.

- **Modèle relationnel complet (12 tables)** : la voie JSONB + vues SQL suffit et coûte bien moins cher à maintenir.
- **Architecture de connecteurs abstraits** : ROI faible, et l'audit lui-même recommande de ne pas ajouter de sources (contradiction).
- **Objet Procédure central / graphe de procurement complet** : vision intéressante, mais lourde pour un gain marginal sur un outil interne.
- **Win probability par ML, MCP/agents, UI sophistiquée** : prématuré.

Raison commune : en solo et via l'éditeur web, le coût de construction et de maintenance dépasse le bénéfice pour le besoin réel (fournir des leads actionnables au BD).

---

## Préalable stratégique (hors technique, mais prioritaire)

- **Propriété intellectuelle** : clarifier par écrit, avec Amarante International, la propriété de Radar. C'est le vrai bloquant à toute idée de valorisation, licence ou produit externe. À traiter avant tout investissement d'industrialisation. L'audit externe l'a totalement ignoré.
- **Si (et seulement si) une trajectoire produit se confirme** : rapport de due diligence complet (Tome 2+) et documentation d'architecture (~15 pages).

---

## Discipline projet (inchangée, rappel)

- Sonde jetable avant tout nouveau collecteur ; ne jamais deviner un endpoint.
- Tout changement structurel derrière un flag `RADAR_X` (défaut off si non sondable), rollback sans déploiement.
- Baseline de tests avant édition, tests appariés offline (fetch/session injecté), suite complète après.
- Imports résilients : module absent → fallback neutre, jamais d'exception propagée.
- Livraison par fichiers complets (éditeur web GitHub).
