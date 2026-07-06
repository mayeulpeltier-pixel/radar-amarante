# Restauration du Sheet depuis les sauvegardes

Ce dossier contient une copie CSV de chaque onglet du Google Sheet du Radar,
rafraichie chaque jour a 22h00 UTC par le workflow `backup.yml`.

**L'historique est dans git.** Chaque commit `chore(backup): ...` est un point de
restauration date. Pour voir l'etat d'un jour precis :

```
git log --oneline backups/            # liste des sauvegardes datees
git show <commit>:backups/ted_radar.csv   # contenu d'un onglet a une date donnee
```

## Restaurer un onglet (cas le plus courant)

Un onglet a ete desaligne, vide ou corrompu par une manipulation.

1. Recupere le bon CSV : soit la version actuelle dans `backups/`, soit une
   version anterieure via `git show <commit>:backups/<fichier>.csv > /tmp/<fichier>.csv`.
2. Dans le Google Sheet, ouvre l'onglet concerne.
3. **Fichier > Importer > Importer > Envoyer** le CSV.
4. Choisis **"Remplacer la feuille de calcul"** (ou "Remplacer les donnees a la
   cellule selectionnee" A1), separateur **virgule**, encodage **UTF-8**.

## Restaurer le Sheet entier

1. Cree un nouveau Google Sheet vierge.
2. Pour chaque onglet liste dans `_manifest.json`, cree une feuille du meme nom
   (colonne `onglet`) et importe le CSV correspondant (colonne `fichier`).
3. Verifie le nombre de lignes attendu (colonne `lignes` du manifeste).
4. Repointe `TED_SHEET_ID` (secret GitHub) vers le nouveau Sheet.

## Le manifeste

`_manifest.json` liste, a la derniere sauvegarde : le nom reel de chaque onglet,
son fichier CSV, le nombre de lignes et de colonnes, et une eventuelle erreur de
lecture. Il sert a retrouver le vrai nom d'un onglet dont le fichier a ete
normalise (espaces, accents, `/` remplaces par `_`).

## Ce que le backup NE couvre PAS

- **L'Apps Script** lie au Sheet (bouton "Je contacte", webhook de suivi).
- **La mise en forme conditionnelle** et les formules eventuelles.

Ce sont du code et de la presentation, pas des donnees. A sauvegarder a part si
besoin (copie manuelle de l'Apps Script, ou `clasp`).
