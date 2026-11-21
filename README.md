# SecLog Monitor - Mini-Projet ELK (Scénario C : Sécurité et Authentification)

Plateforme de monitoring et d'analyse de logs de sécurité : Elasticsearch, Logstash, Kibana,
MongoDB, Redis et une application web Flask, le tout orchestré avec Docker Compose.

## Prérequis
- Docker 24.x et Docker Compose 2.x
- 4 Go de RAM libres minimum (Elasticsearch utilise 1 Go de heap par défaut)
- Python 3.11+ (script de génération de données et tests)
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

## Générer des données de test
```bash
pip install -r scripts/requirements.txt
python scripts/generate_logs.py --rows 10000          # écrit directement dans data/inbox (ingéré par Logstash)
python scripts/generate_logs.py --rows 500 --out samples --name test_upload   # fichier à uploader via l'API
```

## API REST (semaine 3)
Toutes les erreurs sont renvoyées en JSON : `{"error": {"code": "...", "message": "..."}}`.

| Méthode | Route | Description |
|---|---|---|
| POST | `/upload` | Upload d'un fichier (champ multipart `file`) : CSV, JSON ou TXT, 100 Mo max. Valide, normalise, enregistre les métadonnées dans MongoDB et dépose le fichier dans `data/inbox`. Réponse `202` avec `file_id`. |
| POST | `/upload/preview` | Valide le fichier et renvoie les 10 premières lignes, sans rien enregistrer. |
| GET | `/api/v1/files` | Liste des fichiers uploadés (`status`, `page`, `size`). |
| GET | `/api/v1/files/<file_id>` | Métadonnées d'un fichier. |
| GET | `/api/v1/files/<file_id>/status` | Compare les logs indexés au nombre de lignes et met à jour le statut (`pending`, `indexing`, `indexed`, `partial`, `error`). |
| GET | `/search` ou `/api/v1/logs` | Recherche paginée (50 par page). Paramètres : `q`, `level` (ex : `ERROR,CRITICAL`), `service`, `source_ip`, `from`, `to` (AAAA-MM-JJ ou ISO 8601), `sort` (`@timestamp:desc`, `level:asc`, `service:asc`), `page`, `size` (max 100). Résultats en cache Redis 60 s. |
| GET | `/api/v1/logs/<id>` | Détail d'un log. |
| GET | `/api/v1/search/history` | Dernières recherches enregistrées dans MongoDB (`limit`). |
| GET | `/api/v1/stats` | Total de logs, logs du jour, logs ERROR/CRITICAL, fichiers uploadés, répartition par niveau, top IP en échec, évolution sur 7 jours (cache Redis 30 s). |
| GET | `/health` | État de l'application et de ses dépendances (Elasticsearch, MongoDB, Redis). |

Formats acceptés à l'upload :
- **CSV** : colonnes `timestamp` et `source_ip` obligatoires ; les autres colonnes (`username, event_type, status, level, service, message, user_agent, attack_type, resource`) sont réordonnées automatiquement, les colonnes inconnues sont ignorées.
- **JSON** : tableau JSON ou un objet par ligne (NDJSON) ; `timestamp` obligatoire.
- **TXT** : détecté automatiquement comme JSON ou CSV d'après son contenu.
- `timestamp` au format ISO 8601 (ex : `2026-10-30T09:01:12Z`), `source_ip` : adresse IPv4/IPv6 valide.

### Exemples (PowerShell : utiliser `curl.exe`)
```bash
curl.exe -F "file=@samples/test_upload.csv" http://localhost:8000/upload
curl.exe "http://localhost:8000/api/v1/files/f_20261114_001/status"
curl.exe "http://localhost:8000/search?q=failed&level=ERROR&from=2026-11-01&to=2026-11-14"
curl.exe "http://localhost:8000/api/v1/stats"
```

## Tests
```bash
cd webapp
pip install -r requirements-dev.txt
pytest --cov=app --cov-report=html          # tests unitaires (sans Docker), rapport dans htmlcov/
RUN_INTEGRATION=1 pytest -m integration     # test de bout en bout (docker compose up requis)
```
PowerShell : `$env:RUN_INTEGRATION=1; pytest -m integration`

## Kibana : data view
1. Stack Management > Data Views > Create : nom et index pattern `logs-security-*`, champ temps `@timestamp`.
2. Discover : période « Last 7 days » ou plus. Champs de géolocalisation : `geoip.geo.country_name`, `geoip.geo.location` (geo_point).

## Structure
```
docker-compose.yml        orchestration de tous les services
.env.example              variables d'environnement (sans secrets)
elasticsearch/templates/  index template logs-security-*
logstash/                 pipelines.yml + pipeline/csv.conf, json.conf
webapp/app/               application Flask (validation, repository, search, stats, api)
webapp/tests/             tests pytest
scripts/generate_logs.py  générateur de logs (Faker)
data/inbox                dossier surveillé par Logstash
kibana/exports/           exports des dashboards (semaine 4)
```

## Dépannage
- **Elasticsearch redémarre en boucle** : vérifier `vm.max_map_count` et la RAM disponible.
- **Re-tester un même fichier** : Logstash mémorise les fichiers lus (sincedb). Utiliser un nouveau nom de fichier ou `docker compose down -v`.
- **Rien dans Elasticsearch** : `docker compose logs logstash` et consulter `data/errors`.
- **Template modifié** : il n'est appliqué qu'aux nouveaux index. Faire `docker compose down -v`, vider `data/inbox`, puis `docker compose up -d`.
- **Mot de passe avec caractères spéciaux** (`@`, `:`, `/`) : ils cassent les URI MongoDB/Redis.
- Sécurité Elasticsearch désactivée (`xpack.security.enabled=false`) : réservé au développement.
