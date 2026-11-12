# 2N Color — tri vidéo local

Application de classement Finder : web dans **Docker / Colima**, avec **agent macOS natif** pour lire et écrire les pastilles Finder.

## Installer

Prérequis : Colima + Docker Compose, Python 3.11+ sur le Mac.

```zsh
git clone https://github.com/deuzenne/gpt-2n-color.git
cd gpt-2n-color
./deploy.sh "/Volumes/HDD01-02-3000"
```

Ouvrir **http://127.0.0.1:8765**. Le script génère `.env` avec un jeton aléatoire, lance Docker sur Colima et démarre le petit agent Finder natif dans le contexte de l'utilisateur connecté. Le montage des médias est **en lecture seule** dans Docker.

Si Colima n'affiche pas les fichiers d'un disque externe, vérifier `colima start --edit` et ajouter le disque dans les `mounts`, puis `colima restart`. Documentation : https://colima.run/docs/configuration/

Pour arrêter : `./stop.sh`. Logs : `docker compose logs -f` et `tail -f data/mac-agent.log`.

## Parcours

- Choisir un dossier de `MEDIA_ROOT` et l'option sous-dossiers.
- Inventaire par taille décroissante ; **seules les vidéos sans pastille Finder** apparaissent.
- Lecture navigateur avec aperçu FFmpeg H.264/AAC pour MKV, TS, MTS et autres codecs incompatibles, défilement temporel et ×2.
- Une pression sur **Rouge, Violet, Vert, Bleu, Gris, Jaune, Orange** écrit la pastille via Finder, vérifie son état, puis charge la suivante.
- **Aucune** signifie revue sans couleur (reste sans pastille), **Passer** ne modifie rien, **Retour** annule la dernière action si possible.
- Supports : MP4, MOV, M4V, MKV, AVI, WMV, TS, MTS, M2TS.

## Sécurité

**Aucune suppression, aucun renommage, aucun réencodage permanent.** Les labels Finder ne sont pas appliqués depuis Linux : `mac_agent.py` tourne sur macOS, interroge en sortant l'API Colima sur `127.0.0.1` (pas de port natif ouvert) et exécute une seule commande AppleScript par action. En cas d'erreur Finder, aucune tentative automatique : l'interface reste sur la vidéo et permet de réessayer.

La bibliothèque est montée `:ro` ; les chemins sont validés et confinés à `MEDIA_ROOT` ; l'API agent exige un secret (`AGENT_TOKEN`). Le serveur n'écoute que sur le loopback du Mac. Les sessions résident en mémoire, mais les couleurs Finder survivent aux redémarrages.

Configuration : `.env` avec `MEDIA_ROOT`, `PORT` et `AGENT_TOKEN`. Pour changer de bibliothèque : `./deploy.sh "/nouveau/dossier"`.

## Tests

```zsh
python3 -m pip install -r requirements-dev.txt
pytest -q
python3 -m compileall -q app mac_agent.py
docker compose config
```

Version : `0.1.0+build.0001`.
