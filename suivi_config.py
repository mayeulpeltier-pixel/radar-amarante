# -*- coding: utf-8 -*-
# =============================================================================
# CONFIG DU BOUTON « Je contacte » du dashboard.
#
# SECURITE : plus aucun secret n'est stocke en clair dans ce fichier. Les deux
# valeurs sont lues depuis les VARIABLES D'ENVIRONNEMENT (secrets GitHub
# Actions en production). Le dashboard lit d'abord ces memes variables ; ce
# fichier ne sert que de repli local optionnel.
#
# EN PRODUCTION (GitHub Actions) : definis deux secrets de depot
#   - SUIVI_WEBAPP_URL  : l'URL de l'app web Apps Script (finit par /exec)
#   - SUIVI_TOKEN       : le MEME mot de passe que la variable TOKEN du script
# puis passe-les en env a l'etape "Generer le tableau de bord" du workflow
# (deja cable dans radar.yml).
#
# EN LOCAL (test a la main) : exporte les deux variables avant de lancer, ex.
#   export SUIVI_WEBAPP_URL="https://script.google.com/macros/s/XXXX/exec"
#   export SUIVI_TOKEN="ton-nouveau-token"
#
# Tant que l'une des deux est vide, le bouton ne s'affiche pas (dashboard
# intact). Si les deux sont vides, tout fonctionne comme avant, sans suivi.
# =============================================================================

import os

SUIVI_WEBAPP_URL = os.environ.get("SUIVI_WEBAPP_URL", "")
SUIVI_TOKEN = os.environ.get("SUIVI_TOKEN", "")
