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

## Choisir un répertoire précis depuis Safari ou Firefox

Dans la colonne **Bibliothèque**, ouvre **Aller à…**. Le champ **Chemin exact du répertoire**
accepte un chemin macOS complet, par exemple :

```text
/Volumes/HDD01-02-3000/Vidéos/Vacances
```

Cliquer sur **Ouvrir**, vérifier les vidéos présentes (nombre et exemples),
puis lancer **Analyser les vidéos sans couleur**. Le tri se fait toujours
par taille décroissante. La navigation par sous-dossiers reste disponible. Le navigateur mémorise le
dernier dossier ouvert et le rouvre après un rafraîchissement. Si le dossier
n’est plus accessible, l’application affiche la racine de la bibliothèque.

Le chemin choisi doit se trouver **dans le répertoire MEDIA_ROOT monté
dans Colima**. Pour utiliser un tout autre disque ou répertoire racine,
reconfigurer explicitement le montage avec :

```zsh
./deploy.sh "/Volumes/AutreDisque/MonDossier"
```

La commande remonte le nouveau répertoire en **lecture seule** dans Docker
et redémarre le compagnon Finder sur macOS.

### Si l'écran indique « fichiers absents »

Auparavant, la liste de navigation ne présentait que les dossiers et
aucune vidéo : même un dossier plein paraissait vide. Désormais, les vidéos
directement présentes dans le répertoire choisi sont montrées par un
compteur et une liste d'exemples (sans lecture des tags).

Vérifier séparément les vues Mac et Colima :

```zsh
ls -lah "/Volumes/HDD01-02-3000"
docker compose exec -T app ls -lah /library
```

Si les deux affichages diffèrent, revoir le montage Colima. Le script
`deploy.sh` signale maintenant le cas où le Mac voit des fichiers mais
Docker voit un volume vide. Une fois l'analyse lancée, l'application distingue
**0 fichier vidéo** de **vidéos déjà colorées** et de **tags Finder illisibles**.

### Vue MacBook 13 pouces

Pour Safari et Firefox sur un écran de hauteur réduite, une disposition
compacte place les **boutons de couleur** à côté de la vitesse de lecture.
Sur un écran plus étroit, la palette passe à la ligne.
La liste des dossiers est défilante indépendamment du classement. Les
petits écrans mobiles gardent une présentation empilée.

Le filtre **À traiter** de la bibliothèque affiche les sous-dossiers qui
contiennent des vidéos sans pastille Finder et indique leur nombre. Le total
inclut leurs descendants si **Inclure les sous-dossiers** est coché.
Seuls les dossiers avec au moins une vidéo sans pastille confirmée sont affichés.
Un compteur marqué `+` signale un total partiel à cause de pastilles illisibles.

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

Version : `0.1.11+build.0012`.
