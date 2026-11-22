"""Adaptador da porta EstadoPublicador sobre SQLite (o banco só deste serviço)."""

import sqlite3
from datetime import datetime


class EstadoSqlite:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        db.executescript("""
            CREATE TABLE IF NOT EXISTS mapeamento (idnumber TEXT PRIMARY KEY, moodle_id INTEGER);
            CREATE TABLE IF NOT EXISTS versao (chave TEXT PRIMARY KEY, ciclo TEXT);
        """)

    def moodle_id(self, idnumber: str) -> int | None:
        linha = self.db.execute(
            "SELECT moodle_id FROM mapeamento WHERE idnumber = ?", (idnumber,)
        ).fetchone()
        return linha[0] if linha else None

    def mapear(self, idnumber: str, moodle_id: int) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO mapeamento VALUES (?, ?)", (idnumber, moodle_id)
            )

    def ultimo_ciclo(self, entidade: str) -> datetime | None:
        linha = self.db.execute("SELECT ciclo FROM versao WHERE chave = ?", (entidade,)).fetchone()
        return datetime.fromisoformat(linha[0]) if linha else None

    def registrar_ciclo(self, entidade: str, ciclo: datetime) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO versao VALUES (?, ?) ON CONFLICT (chave) DO UPDATE"
                " SET ciclo = MAX(ciclo, excluded.ciclo)",
                (entidade, ciclo.isoformat()),
            )
