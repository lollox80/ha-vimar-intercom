"""rubrica_import.py — estrazione in sola lettura da rubrica.db per l'options flow.

Stessa logica di ``tools/parse_rubrica.py`` (schema descritto in docs/RUBRICA.md §2),
riproposta qui come modulo puro — nessuna dipendenza da Home Assistant — così può
essere importato sia dal tool a riga di comando (indirettamente, restano comunque
due implementazioni volutamente separate per non rischiare regressioni nel tool già
in uso) sia da ``config_flow.OptionsFlowHandler.async_step_import_rubrica``, ed è
testabile da solo in ``tests/test_rubrica_import.py`` senza bisogno di HA installato
(vedi ``tests/conftest.py`` e la lista PURE in ``tests/test_smoke.py``).

Il database non viene MAI scritto: l'apertura è sempre read-only (``mode=ro``).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

ICON_MAP = {"DOOR": "door", "LIGHT": "light", "SWITCH": "switch"}


class RubricaImportError(Exception):
    """File non trovato, non è un database SQLite valido, o errore di lettura."""


def _connect_ro(path: str) -> sqlite3.Connection:
    p = Path(path)
    if not p.exists():
        raise RubricaImportError(f"File non trovato: {path}")
    try:
        con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
        con.execute("SELECT 1")  # verifica che sia davvero un database SQLite leggibile
    except sqlite3.Error as exc:
        raise RubricaImportError(f"Non è un database SQLite valido: {exc}") from exc
    con.row_factory = sqlite3.Row
    return con


def _tables(con: sqlite3.Connection) -> set[str]:
    rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r["name"].lower() for r in rows}


def _get(row: sqlite3.Row, *names: str):
    """Ritorna il primo campo presente (case-insensitive) tra i nomi dati."""
    keys = {k.lower(): k for k in row.keys()}
    for n in names:
        k = keys.get(n.lower())
        if k is not None:
            return row[k]
    return None


def _norm_icon(val) -> str:
    if val is None:
        return "switch"
    return ICON_MAP.get(str(val).strip().upper(), "switch")


def _row_to_actuator(r: sqlite3.Row) -> dict:
    name = _get(r, "NAME") or "Attuatore"
    msg = _get(r, "MSG")
    gid_pe = _get(r, "GID_PE")
    icon = _norm_icon(_get(r, "ICON"))
    # Il comando va a GID_PE (destinatario dell'attuatore); solo se manca del
    # tutto si ricade su "AUTO" (targa di default dell'apri-porta).
    target = str(gid_pe) if gid_pe not in (None, "", 0, "0") else "AUTO"
    return {
        "name": str(name),
        "msg": None if msg is None else str(msg),
        "target": target,
        "icon": icon,
    }


def extract_actuators(con: sqlite3.Connection, gid: str) -> list[dict]:
    """Attuatori visibili al GID appartamento indicato.

    Percorso principale: join ACTUATOR_LIST/ACTUATOR_RULES(/ICON_LIST) come fa
    l'app VIEW. Fallback: tutte le righe di ACTUATOR_LIST se la join non produce
    risultati o le tabelle di regole non esistono su questo schema.
    """
    tables = _tables(con)
    actuators: list[dict] = []

    if {"actuator_list", "actuator_rules"} <= tables:
        icon_join = ", ICON_LIST" if "icon_list" in tables else ""
        icon_sel = "ICON_LIST.NAME" if "icon_list" in tables else "NULL"
        icon_where = " AND ACTUATOR_LIST.ICON = ICON_LIST.ID" if "icon_list" in tables else ""
        q = (
            f"SELECT ACTUATOR_LIST.ID AS ID, ACTUATOR_LIST.NAME AS NAME, "
            f"ACTUATOR_LIST.GID_PE AS GID_PE, ACTUATOR_LIST.ATT_ID AS ATT_ID, "
            f"ACTUATOR_LIST.MSG AS MSG, {icon_sel} AS ICON "
            f"FROM ACTUATOR_LIST, ACTUATOR_RULES{icon_join} "
            f"WHERE ACTUATOR_RULES.ACTUATOR_ID = ACTUATOR_LIST.ID "
            f"AND ACTUATOR_RULES.GA_GID = ?{icon_where}"
        )
        rows = con.execute(q, (gid,)).fetchall()
        actuators = [_row_to_actuator(r) for r in rows]
        if actuators:
            return actuators

    if "actuator_list" in tables:
        actuators = [_row_to_actuator(r) for r in con.execute("SELECT * FROM ACTUATOR_LIST").fetchall()]
    return actuators


def extract_system(con: sqlite3.Connection) -> dict:
    """Parametri della tabella SYSTEM (PARAM/VALUE), incluso MAGIC_APT_INTERCOM
    (l'SGA reale). Dizionario vuoto se la tabella non esiste su questo schema."""
    if "system" not in _tables(con):
        return {}
    out: dict[str, str | None] = {}
    for r in con.execute("SELECT * FROM SYSTEM").fetchall():
        param = _get(r, "PARAM", "KEY")
        value = _get(r, "VALUE", "VAL")
        if param is not None:
            out[str(param)] = None if value is None else str(value)
    return out


def parse_rubrica_file(path: str, gid: str = "101") -> dict:
    """Apre rubrica.db in sola lettura ed estrae attuatori + parametri SYSTEM.

    Ritorna ``{"actuators": [...], "system": {...}, "sga": str | None}``.
    Solleva ``RubricaImportError`` se il file non esiste, non è un database
    SQLite valido, o una query fallisce inaspettatamente. Tabelle mancanti
    (schema diverso da questo impianto) non sono un errore: producono
    semplicemente liste/dizionari vuoti, così l'importer resta utilizzabile
    anche su impianti con uno schema rubrica.db leggermente diverso.
    """
    con = _connect_ro(path)
    try:
        try:
            actuators = extract_actuators(con, gid)
            system = extract_system(con)
        except sqlite3.Error as exc:
            raise RubricaImportError(f"Errore di lettura del database: {exc}") from exc
        return {
            "actuators": actuators,
            "system": system,
            "sga": system.get("MAGIC_APT_INTERCOM"),
        }
    finally:
        con.close()
