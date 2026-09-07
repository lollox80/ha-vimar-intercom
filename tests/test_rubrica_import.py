"""Test per rubrica_import.py: importer di rubrica.db per l'options flow.

Costruisce un rubrica.db sintetico (stesso schema minimo usato da
tools/parse_rubrica.py, vedi docs/RUBRICA.md §2) e verifica estrazione
attuatori/SYSTEM, fallback quando mancano ACTUATOR_RULES/ICON_LIST, e gli
errori su file mancante o non-SQLite.
"""
from __future__ import annotations

import sqlite3

import pytest

rubrica_import = pytest.importorskip("custom_components.vimar_intercom.rubrica_import")


def _make_db(path, *, with_rules=True, with_icons=True, with_system=True):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE ACTUATOR_LIST (ID INTEGER, NAME TEXT, GID_PE TEXT, ATT_ID TEXT, MSG TEXT, ICON INTEGER)")
    con.execute(
        "INSERT INTO ACTUATOR_LIST VALUES (1,'Serratura','55001','A1','OPEN',1),"
        "(2,'LUCE SCALA','55001','A2','ATTUATORE_01',3)"
    )
    if with_rules:
        con.execute("CREATE TABLE ACTUATOR_RULES (ID INTEGER, GA_GID TEXT, ACTUATOR_ID INTEGER)")
        con.execute("INSERT INTO ACTUATOR_RULES VALUES (1,'101',1),(2,'101',2),(3,'999',1)")
    if with_icons:
        con.execute("CREATE TABLE ICON_LIST (ID INTEGER, NAME TEXT)")
        con.execute("INSERT INTO ICON_LIST VALUES (1,'DOOR'),(2,'LIGHT'),(3,'SWITCH')")
    if with_system:
        con.execute("CREATE TABLE SYSTEM (PARAM TEXT, VALUE TEXT)")
        con.execute("INSERT INTO SYSTEM VALUES ('MAGIC_APT_INTERCOM','55001'),('VM_PREFIX','8000')")
    con.commit()
    con.close()


def test_parse_rubrica_file_full_schema(tmp_path):
    db = tmp_path / "rubrica.db"
    _make_db(db)

    result = rubrica_import.parse_rubrica_file(str(db), gid="101")

    assert result["sga"] == "55001"
    assert result["system"]["VM_PREFIX"] == "8000"
    names = {a["name"] for a in result["actuators"]}
    assert names == {"Serratura", "LUCE SCALA"}
    serratura = next(a for a in result["actuators"] if a["name"] == "Serratura")
    assert serratura == {"name": "Serratura", "msg": "OPEN", "target": "55001", "icon": "door"}
    luce = next(a for a in result["actuators"] if a["name"] == "LUCE SCALA")
    assert luce["icon"] == "switch"


def test_parse_rubrica_file_filters_by_gid(tmp_path):
    db = tmp_path / "rubrica.db"
    _make_db(db)

    result = rubrica_import.parse_rubrica_file(str(db), gid="999")

    assert [a["name"] for a in result["actuators"]] == ["Serratura"]


def test_parse_rubrica_file_fallback_without_rules_table(tmp_path):
    """Senza ACTUATOR_RULES/ICON_LIST il fallback ritorna comunque tutte le
    righe di ACTUATOR_LIST (icona default 'switch')."""
    db = tmp_path / "rubrica.db"
    _make_db(db, with_rules=False, with_icons=False)

    result = rubrica_import.parse_rubrica_file(str(db), gid="101")

    assert len(result["actuators"]) == 2
    assert all(a["icon"] == "switch" for a in result["actuators"])


def test_parse_rubrica_file_no_system_table(tmp_path):
    db = tmp_path / "rubrica.db"
    _make_db(db, with_system=False)

    result = rubrica_import.parse_rubrica_file(str(db), gid="101")

    assert result["system"] == {}
    assert result["sga"] is None


def test_missing_file_raises_rubrica_import_error(tmp_path):
    with pytest.raises(rubrica_import.RubricaImportError):
        rubrica_import.parse_rubrica_file(str(tmp_path / "non_esiste.db"))


def test_non_sqlite_file_raises_rubrica_import_error(tmp_path):
    bogus = tmp_path / "non_e_un_db.db"
    bogus.write_text("questo non è un database SQLite")
    with pytest.raises(rubrica_import.RubricaImportError):
        rubrica_import.parse_rubrica_file(str(bogus))


def test_never_writes_to_the_database(tmp_path):
    """L'apertura è read-only: un tentativo di scrittura sulla stessa
    connessione usata dal modulo deve fallire."""
    db = tmp_path / "rubrica.db"
    _make_db(db)

    con = rubrica_import._connect_ro(str(db))
    try:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("DELETE FROM ACTUATOR_LIST")
    finally:
        con.close()
