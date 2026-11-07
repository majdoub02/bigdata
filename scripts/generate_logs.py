#!/usr/bin/env python3
"""Génère des logs de sécurité réalistes (CSV et/ou JSON) avec Faker.

Exemples :
    python scripts/generate_logs.py --rows 10000
    python scripts/generate_logs.py --rows 500 --format csv --name test_small
Les fichiers sont écrits dans data/inbox (dossier surveillé par Logstash).
"""
import argparse
import csv
import json
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from faker import Faker

FIELDS = ["timestamp", "source_ip", "username", "event_type", "status", "level",
          "service", "message", "user_agent", "attack_type", "resource"]

# IP "attaquantes" fixes : visibles en tête du top 10 dans Kibana
ATTACKERS = ["185.220.101.4", "45.155.205.9", "91.240.118.2", "103.77.192.8", "77.91.68.15"]

# Préfixes d'IP par pays (pour que le filtre geoip trouve des pays variés)
GEO = {"TN": ["41.226", "197.2", "41.62"], "FR": ["90.12", "86.200"], "DE": ["85.214"],
       "US": ["8.8", "104.16"], "RU": ["95.31", "77.88"], "CN": ["223.5", "114.114"]}
GEO_WEIGHTS = {"TN": 50, "FR": 25, "DE": 8, "US": 7, "RU": 5, "CN": 5}

USERS = ["m.ben", "s.trabelsi", "a.gharbi", "k.jlassi", "n.mejri", "admin", "root", "support"]
RESOURCES = ["/finance/report.xlsx", "/hr/salaries.csv", "/db/customers", "/vpn/config", "/backup/full.tar"]
PORTS = [21, 22, 23, 25, 80, 443, 445, 1433, 3306, 3389, 5900, 8080]


def country_ip(country):
    return f"{random.choice(GEO[country])}.{random.randint(1, 254)}.{random.randint(1, 254)}"


def random_ip():
    country = random.choices(list(GEO_WEIGHTS), weights=GEO_WEIGHTS.values())[0]
    return country_ip(country)


def row(ts, ip, user, etype, status, level, service, message, ua="", attack="", resource=""):
    return {"timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "source_ip": ip, "username": user,
            "event_type": etype, "status": status, "level": level, "service": service,
            "message": message, "user_agent": ua, "attack_type": attack, "resource": resource}


def normal_event(fake, ts):
    kind = random.choices(["login_ok", "login_ko", "access", "audit", "firewall"],
                          weights=[50, 15, 18, 5, 12])[0]
    ip, user, ua = random_ip(), random.choice(USERS[:6]), fake.user_agent().replace(",", ";")
    if kind == "login_ok":
        return row(ts, ip, user, "auth", "success", "INFO", "auth", f"Login success for {user}", ua)
    if kind == "login_ko":
        return row(ts, ip, user, "auth", "failure", "WARNING", "auth", f"Failed password for {user}", ua)
    if kind == "access":
        res = random.choice(RESOURCES)
        if random.random() < 0.15:
            return row(ts, ip, user, "resource_access", "denied", "WARNING", "file-server",
                       f"Access denied to {res} for {user}", ua, resource=res)
        return row(ts, ip, user, "resource_access", "success", "INFO", "file-server",
                   f"{user} accessed {res}", ua, resource=res)
    if kind == "audit":
        return row(ts, ip, "admin", "audit", "success", "INFO", "audit",
                   random.choice(["Config modified by admin", "User created by admin", "Firewall rule updated"]))
    return row(ts, ip, "", "firewall", random.choice(["allowed", "blocked"]), "DEBUG", "firewall",
               f"Connection to port {random.choice(PORTS)}")


def attack_burst(fake, start):
    """Retourne une rafale d'évènements d'attaque (liste de lignes)."""
    ip = random.choice(ATTACKERS)
    kind = random.choice(["brute_force", "sql_injection", "port_scan", "geo_anomaly"])
    out, ts = [], start
    if kind == "brute_force":
        user = random.choice(["admin", "root"])
        for _ in range(random.randint(8, 25)):
            ts += timedelta(seconds=random.randint(1, 20))
            out.append(row(ts, ip, user, "auth", "failure", "ERROR", "auth",
                           f"Failed password for {user} from {ip}", "python-requests/2.31", "brute_force"))
    elif kind == "sql_injection":
        for _ in range(random.randint(3, 8)):
            ts += timedelta(seconds=random.randint(1, 30))
            out.append(row(ts, ip, "", "intrusion", "blocked", "CRITICAL", "waf",
                           "SQL injection attempt on /login", "sqlmap/1.7", "sql_injection",
                           "/login?user=' OR 1=1--"))
    elif kind == "port_scan":
        for port in random.sample(PORTS, random.randint(6, len(PORTS))):
            ts += timedelta(seconds=random.randint(1, 5))
            out.append(row(ts, ip, "", "firewall", "blocked", "WARNING", "firewall",
                           f"Port scan detected: port {port}", "", "port_scan"))
    else:  # connexion réussie de l'admin depuis un pays inhabituel
        ip = country_ip("RU")
        out.append(row(ts, ip, "admin", "auth", "success", "WARNING", "auth",
                       "Login success for admin from unusual country", fake.user_agent().replace(",", ";"),
                       "geo_anomaly"))
    return out


def generate(rows, days, seed):
    random.seed(seed)
    fake = Faker()
    Faker.seed(seed)
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    span = int((now - start).total_seconds())

    data, target_attacks = [], int(rows * 0.15)
    while len(data) < target_attacks:
        data += attack_burst(fake, start + timedelta(seconds=random.randint(0, span - 3600)))
    while len(data) < rows:
        data.append(normal_event(fake, start + timedelta(seconds=random.randint(0, span))))
    data = data[:rows]
    data.sort(key=lambda r: r["timestamp"])
    return data


def write_atomic(path, writer_fn):
    """Écrit dans un fichier .tmp puis renomme : Logstash ne lit jamais un fichier incomplet."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        writer_fn(fh)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rows", type=int, default=10000, help="nombre de lignes (défaut 10000)")
    ap.add_argument("--format", choices=["csv", "json", "both"], default="both")
    ap.add_argument("--out", default="data/inbox", help="dossier de sortie (défaut data/inbox)")
    ap.add_argument("--name", default=None, help="nom de base des fichiers (sans extension)")
    ap.add_argument("--days", type=int, default=3, help="période couverte en jours (défaut 3)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    base = args.name or f"auth_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    data = generate(args.rows, args.days, args.seed)

    if args.format in ("csv", "both"):
        def w_csv(fh):
            wr = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
            wr.writeheader()
            wr.writerows(data)
        write_atomic(out / f"{base}.csv", w_csv)
        print(f"CSV  : {out / (base + '.csv')} ({len(data)} lignes)")
    if args.format in ("json", "both"):
        def w_json(fh):
            for r in data:
                fh.write(json.dumps({k: v for k, v in r.items() if v != ""}, ensure_ascii=False) + "\n")
        write_atomic(out / f"{base}_json.json", w_json)
        print(f"JSON : {out / (base + '_json.json')} ({len(data)} lignes, format NDJSON)")


if __name__ == "__main__":
    main()
