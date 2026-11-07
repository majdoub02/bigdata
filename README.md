# SecLog Monitor - Mini-Projet ELK (Scénario C : Sécurité et Authentification)

Plateforme de monitoring et d'analyse de logs de sécurité : Elasticsearch, Logstash, Kibana,
MongoDB, Redis et une application web Flask, le tout orchestré avec Docker Compose.

## Prérequis
- Docker 24.x et Docker Compose 2.x
- 4 Go de RAM libres minimum (Elasticsearch utilise 1 Go de heap par défaut)
- Python 3.11+ (uniquement pour le script de génération de données)
- Linux : `sudo sysctl -w vm.max_map_count=262144` (requis par Elasticsearch)

## Démarrage
```bash
cp .env.example .env            # puis modifier les mots de passe
docker compose up -d --build    # 1er démarrage : 3 à 6 minutes (téléchargement des images)
docker compose ps               # tous les services doivent être "healthy"
```
Arrêt : `docker compose down` (ajouter `-v` pour supprimer aussi les données).

## Accès
| Service | URL |
|---|---|
| Application web | http://localhost:8000 (santé : /health) |
| Kibana | http://localhost:5601 |
| Elasticsearch | http://localhost:9200 |
| Logstash (API) | http://localhost:9600 |

## Générer et injecter des données de test
```bash
pip install -r scripts/requirements.txt
python scripts/generate_logs.py --rows 10000      # écrit un CSV et un JSON dans data/inbox
curl "localhost:9200/logs-security-*/_count?pretty"   # le compteur augmente en ~10 s
```
Les fichiers déposés dans `data/inbox` sont ingérés automatiquement par Logstash
(pipeline `csv.conf` pour `*.csv`, pipeline `json.conf` pour `*.json` au format NDJSON).
Les lignes en erreur de parsing sont écrites dans `data/errors`.

## Kibana : première visualisation
1. Stack Management > Data Views > Create : nom `logs-security-*`, champ temps `@timestamp`.
2. Discover : choisir la période "Last 7 days" ; vérifier les champs (`source_ip` de type ip, `geoip.location` de type geo_point).

## Structure
```
docker-compose.yml        orchestration de tous les services
.env.example              variables d'environnement (sans secrets)
elasticsearch/templates/  index template logs-security-*
logstash/                 pipelines.yml + pipeline/csv.conf, json.conf
webapp/                   application Flask (Dockerfile, app/)
scripts/generate_logs.py  générateur de logs (Faker)
data/inbox                dossier surveillé par Logstash
kibana/exports/           exports des dashboards (semaine 4)
```

## Dépannage
- **Elasticsearch redémarre en boucle** : vérifier `vm.max_map_count` et la RAM disponible.
- **Re-tester un même fichier** : Logstash mémorise les fichiers lus (sincedb). Utiliser un nouveau nom de fichier ou `docker compose down -v`.
- **Rien dans Elasticsearch** : `docker compose logs logstash` et consulter `data/errors`.
- **Mot de passe avec caractères spéciaux** (`@`, `:`, `/`) : ils cassent les URI MongoDB/Redis, préférer des mots de passe alphanumériques.
- Sécurité Elasticsearch désactivée (`xpack.security.enabled=false`) : réservé au développement.
