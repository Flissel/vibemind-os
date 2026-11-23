"""Datenbank-Schicht des Rowboat-Abholers: nur die vier Huellen aus Migration 101.

`PgEmpfang` ruft ausschliesslich `auftrag.rowboat_empfang_naechster`, `..._abschliessen`,
`..._fehler` und `..._bekannt` auf, immer mit Abfrage-Parametern. Die Anmelde-Rolle
`rowboat_abholer` ist Mitglied von `rowboat_empfang` und darf sonst nichts.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Protocol

from psycopg.types.json import Jsonb

BEKANNT_MAX = 500  # rowboat_empfang_bekannt nimmt hoechstens so viele Kennungen je Aufruf


class Empfang(Protocol):
    def naechster(self, laden: str, lease: timedelta) -> dict | None: ...

    def abschliessen(self, auftrag_id: str, ergebnis: dict, laden: str) -> bool: ...

    def fehler(self, auftrag_id: str, grund: str, laden: str) -> bool: ...

    def bekannt(self, laden: str, punkt_ids: list[str]) -> set[str]: ...


class PgEmpfang:
    """`Empfang` ueber einen `psycopg_pool.ConnectionPool` (jede Abfrage eine eigene Transaktion)."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def _eins(self, sql: str, parameter: tuple) -> object:
        with self._pool.connection() as conn:
            zeile = conn.execute(sql, parameter).fetchone()
        return zeile[0] if zeile else None

    def naechster(self, laden: str, lease: timedelta) -> dict | None:
        wert = self._eins("select auftrag.rowboat_empfang_naechster(%s::text, %s::interval)", (laden, lease))
        return wert if isinstance(wert, dict) else None

    def abschliessen(self, auftrag_id: str, ergebnis: dict, laden: str) -> bool:
        return self._eins("select auftrag.rowboat_empfang_abschliessen(%s::uuid, %s::jsonb, %s::text)",
                          (auftrag_id, Jsonb(ergebnis), laden)) is True

    def fehler(self, auftrag_id: str, grund: str, laden: str) -> bool:
        return self._eins("select auftrag.rowboat_empfang_fehler(%s::uuid, %s::text, %s::text)",
                          (auftrag_id, grund, laden)) is True

    def bekannt(self, laden: str, punkt_ids: list[str]) -> set[str]:
        da: set[str] = set()
        for start in range(0, len(punkt_ids), BEKANNT_MAX):
            wert = self._eins("select auftrag.rowboat_empfang_bekannt(%s::text, %s::text[])",
                              (laden, list(punkt_ids[start:start + BEKANNT_MAX])))
            da |= {k for k in wert or [] if isinstance(k, str)}
        return da
