"""Validation et normalisation des fichiers de logs uploadés (CSV, JSON, TXT).

Le fichier est converti dans le format exact attendu par les pipelines Logstash :
  - CSV  : colonnes dans l'ordre de CSV_COLUMNS (le filtre csv de Logstash lit par position)
  - JSON : un objet JSON par ligne (NDJSON)
"""
import csv
import io
import ipaddress
import json
import os
from dataclasses import dataclass, field
from datetime import datetime

CSV_COLUMNS = ["timestamp", "source_ip", "username", "event_type", "status", "level",
               "service", "message", "user_agent", "attack_type", "resource"]
REQUIRED_CSV = ("timestamp", "source_ip")
ALLOWED_EXTENSIONS = {".csv", ".json", ".jsonl", ".txt"}
SAMPLE_ROWS = 100     # nombre de lignes dont le contenu est vérifié en détail
PREVIEW_ROWS = 10     # lignes renvoyées pour la prévisualisation


class ValidationError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Validated:
    fmt: str                      # "csv" ou "json"
    rows: int
    content: str                  # contenu normalisé, prêt pour Logstash
    preview: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def parse_timestamp(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("vide")
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    return datetime.fromisoformat(v)


def check_record(rec, where):
    """Vérifie un enregistrement : horodatage ISO 8601 et adresse IP valides."""
    ts = rec.get("timestamp")
    try:
        parse_timestamp(ts)
    except (ValueError, TypeError):
        raise ValidationError(
            "invalid_timestamp",
            f"{where} : horodatage invalide ({ts!r}). Format attendu : ISO 8601, "
            "par exemple 2026-10-30T09:01:12Z.") from None
    ip = rec.get("source_ip")
    if ip not in (None, ""):
        try:
            ipaddress.ip_address(str(ip).strip())
        except ValueError:
            raise ValidationError("invalid_ip", f"{where} : adresse IP invalide ({ip!r}).") from None


def detect_format(ext, text):
    if ext == ".csv":
        return "csv"
    if ext in (".json", ".jsonl"):
        return "json"
    # .txt : on devine le format d'après le contenu
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        return "json"
    first_line = stripped.splitlines()[0].lower() if stripped else ""
    if "," in first_line and "timestamp" in first_line:
        return "csv"
    raise ValidationError(
        "unsupported_content",
        "Fichier TXT non reconnu : il doit contenir soit du JSON (un objet par ligne), "
        "soit du CSV avec une ligne d'en-tête (timestamp,source_ip,...).")


def normalize_csv(text):
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise ValidationError("empty_file", "Le fichier est vide.") from None
    header = [h.strip().lower() for h in header]
    missing = [c for c in REQUIRED_CSV if c not in header]
    if missing:
        raise ValidationError(
            "missing_columns",
            f"Colonnes obligatoires manquantes : {', '.join(missing)}. "
            f"Colonnes attendues : {', '.join(CSV_COLUMNS)}.")
    idx = {name: header.index(name) for name in CSV_COLUMNS if name in header}
    ignored = [h for h in header if h and h not in CSV_COLUMNS]

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    rows, preview = 0, []
    for line_no, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        rec = {name: (row[i].strip() if i < len(row) else "") for name, i in idx.items()}
        if rows < SAMPLE_ROWS:
            check_record(rec, f"Ligne {line_no}")
        writer.writerow([rec.get(c, "") for c in CSV_COLUMNS])
        if len(preview) < PREVIEW_ROWS:
            preview.append({c: rec.get(c, "") for c in CSV_COLUMNS})
        rows += 1
    if rows == 0:
        raise ValidationError("no_rows", "Le fichier ne contient aucune ligne de données.")
    warnings = [f"Colonnes ignorées : {', '.join(ignored)}."] if ignored else []
    return Validated("csv", rows, out.getvalue(), preview, warnings)


def normalize_json(text):
    records = []
    if text.lstrip().startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValidationError(
                "invalid_json", f"JSON invalide : {exc.msg} (ligne {exc.lineno}, colonne {exc.colno}).") from None
        for i, rec in enumerate(data, start=1):
            records.append((f"Élément {i}", rec))
    else:
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                records.append((f"Ligne {line_no}", json.loads(line)))
            except json.JSONDecodeError as exc:
                raise ValidationError(
                    "invalid_json",
                    f"Ligne {line_no} : JSON invalide ({exc.msg}). Le fichier doit être un tableau JSON "
                    "ou contenir un objet JSON par ligne.") from None
    if not records:
        raise ValidationError("no_rows", "Le fichier ne contient aucun enregistrement.")
    for n, (where, rec) in enumerate(records):
        if not isinstance(rec, dict):
            raise ValidationError("invalid_json", f"{where} : un objet JSON est attendu.")
        if n < SAMPLE_ROWS:
            check_record(rec, where)
    content = "".join(json.dumps(rec, ensure_ascii=False) + "\n" for _, rec in records)
    return Validated("json", len(records), content, [rec for _, rec in records[:PREVIEW_ROWS]])


def validate_and_normalize(filename, raw):
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            "unsupported_extension",
            f"Extension {ext or '(aucune)'} non supportée. Formats acceptés : CSV, JSON, TXT.")
    if not raw:
        raise ValidationError("empty_file", "Le fichier est vide.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValidationError("invalid_encoding", "Le fichier doit être encodé en UTF-8.") from None
    fmt = detect_format(ext, text)
    return normalize_csv(text) if fmt == "csv" else normalize_json(text)
