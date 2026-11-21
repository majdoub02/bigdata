import json

import pytest

from app.validation import CSV_COLUMNS, ValidationError, validate_and_normalize

from .conftest import GOOD_CSV, GOOD_NDJSON


def code_of(filename, content):
    with pytest.raises(ValidationError) as exc:
        validate_and_normalize(filename, content.encode() if isinstance(content, str) else content)
    return exc.value.code


def test_valid_csv():
    v = validate_and_normalize("auth.csv", GOOD_CSV.encode())
    assert v.fmt == "csv" and v.rows == 2
    assert v.content.splitlines()[0] == ",".join(CSV_COLUMNS)
    assert v.preview[0]["source_ip"] == "185.220.101.4"


def test_csv_columns_are_reordered_and_completed():
    raw = "level,source_ip,timestamp,extra\nERROR,1.2.3.4,2026-10-30T09:00:00Z,x\n"
    v = validate_and_normalize("a.csv", raw.encode())
    lines = v.content.splitlines()
    assert lines[0] == ",".join(CSV_COLUMNS)
    assert lines[1].startswith("2026-10-30T09:00:00Z,1.2.3.4,")
    assert any("extra" in w for w in v.warnings)


def test_csv_missing_required_column():
    assert code_of("a.csv", "timestamp,username\n2026-10-30T09:00:00Z,x\n") == "missing_columns"


def test_csv_invalid_timestamp():
    assert code_of("a.csv", "timestamp,source_ip\nhier,1.2.3.4\n") == "invalid_timestamp"


def test_csv_invalid_ip():
    assert code_of("a.csv", "timestamp,source_ip\n2026-10-30T09:00:00Z,999.1.1.1\n") == "invalid_ip"


def test_csv_header_only():
    assert code_of("a.csv", "timestamp,source_ip\n") == "no_rows"


def test_empty_file():
    assert code_of("a.csv", b"") == "empty_file"


def test_unsupported_extension():
    assert code_of("virus.exe", "abc") == "unsupported_extension"


def test_invalid_encoding():
    assert code_of("a.csv", b"\xff\xfe\x00bad") == "invalid_encoding"


def test_valid_ndjson():
    v = validate_and_normalize("a.json", GOOD_NDJSON.encode())
    assert v.fmt == "json" and v.rows == 2 and len(v.content.splitlines()) == 2


def test_json_array_is_converted_to_ndjson():
    arr = json.dumps([json.loads(line) for line in GOOD_NDJSON.splitlines()])
    v = validate_and_normalize("a.json", arr.encode())
    assert v.rows == 2
    assert [json.loads(line)["level"] for line in v.content.splitlines()] == ["ERROR", "INFO"]


def test_invalid_json_reports_line():
    raw = GOOD_NDJSON + "{pas du json}\n"
    with pytest.raises(ValidationError) as exc:
        validate_and_normalize("a.json", raw.encode())
    assert exc.value.code == "invalid_json" and "Ligne 3" in exc.value.message


def test_json_without_timestamp():
    assert code_of("a.json", '{"source_ip": "1.2.3.4"}\n') == "invalid_timestamp"


def test_txt_with_json_content_is_detected():
    assert validate_and_normalize("logs.txt", GOOD_NDJSON.encode()).fmt == "json"


def test_txt_with_csv_content_is_detected():
    assert validate_and_normalize("logs.txt", GOOD_CSV.encode()).fmt == "csv"


def test_txt_unknown_content():
    assert code_of("notes.txt", "bonjour, ceci n'est pas un log") == "unsupported_content"
