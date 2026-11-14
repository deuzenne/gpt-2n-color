# FAQ de dépannage 2N Color

Lance les commandes depuis le dossier `gpt-2n-color`, dans le terminal de ta session macOS. Les exemples utilisent le profil Colima `default` et le port `8765`. Si tu as changé le port dans `.env`, utilise cette valeur dans les URL.

## Docker cherche /var/run/docker.sock

`Already up to date.` indique que Git a terminé. Si Docker affiche ensuite une erreur sur `/var/run/docker.sock`, vérifie le profil et cible explicitement le contexte Colima.

```zsh
colima status
docker context ls
docker --context colima info
docker --context colima compose ps
```

Si Colima est arrêté, lance `colima start`. Pour reconstruire l’application et relancer aussi l’agent Finder, lance `./deploy.sh`. Ce script reprend le répertoire média configuré dans `.env`.

## Le montage échoue ou le disque paraît vide

Pour `mkdir …: file exists`, `Bad file descriptor` ou une bibliothèque vide, compare la vue du Mac, de la VM et du conteneur.

```zsh
/bin/ls -la "/Volumes/HDD01-02-3000"
colima ssh -- ls -la "/Volumes/HDD01-02-3000"
docker --context colima compose exec -T app ls -la /library
```

La troisième commande nécessite un conteneur démarré.

- Si la première échoue, reconnecte le disque et vérifie son nom dans `/Volumes`.
- Si le Mac lit le disque mais que la VM échoue, vérifie le partage dans `~/.colima/default/colima.yaml`. Ajoute l’entrée ci-dessous à la liste `mounts` existante.
- Si la VM voit les fichiers mais que le conteneur ne les voit pas, vérifie le répertoire configuré avec `sed -n 's/^MEDIA_ROOT=//p' .env`. C’est lui qui est monté sur `/library`.

```yaml
mounts:
  - location: /Volumes/HDD01-02-3000
    writable: false
```

Avec le disque monté côté Mac, redémarre Colima pour rétablir le partage. Cette opération interrompt brièvement les conteneurs du profil.

```zsh
colima stop && colima start &&
  colima ssh -- ls -la "/Volumes/HDD01-02-3000" &&
  ./deploy.sh
```

## Finder non connecté ou le bouton Calculer échoue

Le calcul lit les pastilles avec `mac_agent.py`, un processus macOS. Le conteneur sert l’interface et reçoit les résultats. Vérifie les deux côtés.

```zsh
curl -fsS http://127.0.0.1:8765/api/health
pgrep -fl mac_agent.py
tail -n 40 data/mac-agent.log
```

`agent_online: true` signifie que l’agent a contacté l’application dans les 45 dernières secondes. Si la valeur est `false`, lance `./deploy.sh`, puis recharge la page. En cas de refus d’automatisation dans le journal, vérifie l’autorisation de contrôler Finder dans **Réglages Système → Confidentialité et sécurité → Automatisation** pour l’application qui lance l’agent.

Si Finder est connecté mais que le calcul échoue encore, clique sur **Calculer** et consulte les deux journaux au moment de l’erreur.

```zsh
tail -n 40 data/mac-agent.log
docker --context colima compose logs --tail=80 app
```

## « Codec non lisible directement : aperçu FFmpeg »

Ce message indique que la lecture directe dans le navigateur a échoué et que 2N Color essaie l’aperçu FFmpeg. Si l’aperçu se lit, le classement peut continuer. Si lui aussi échoue, relève le codec affiché et les lignes correspondantes dans les logs du conteneur.

## Vérifier une mise à jour

```zsh
git pull --ff-only && ./deploy.sh
docker --context colima compose ps
curl -fsS http://127.0.0.1:8765/api/health
```

Vérifie la version dans la réponse et `agent_online`, puis recharge la page avec **⌘R**.
