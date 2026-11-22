"""Adaptador da porta Moodle sobre os Web Services REST (o timeout fica no httpx.AsyncClient)."""

import json
from pathlib import Path
from typing import Any

import httpx


class MoodleError(Exception):
    """O Moodle recusou a chamada (dado inválido, conflito...)."""


def carregar_setup(arquivo: Path) -> dict:
    """Token, categoria raiz e ids dos papéis, gerados pelo serviço moodle-setup."""
    return json.loads(arquivo.read_text())


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """{"users": [{"username": "x"}]} → {"users[0][username]": "x"} (formato do Moodle)."""
    if isinstance(value, dict):
        items = value.items()
    elif isinstance(value, list):
        items = enumerate(value)
    else:
        return {prefix: int(value) if isinstance(value, bool) else value}
    flat: dict[str, Any] = {}
    for key, item in items:
        flat.update(_flatten(item, f"{prefix}[{key}]" if prefix else str(key)))
    return flat


class MoodleClient:
    def __init__(self, http: httpx.AsyncClient, token: str) -> None:
        self._http = http
        self._token = token

    async def call(self, function: str, **params: Any) -> Any:
        data = {"wstoken": self._token, "wsfunction": function, "moodlewsrestformat": "json"}
        response = await self._http.post(
            "/webservice/rest/server.php", data={**data, **_flatten(params)}
        )
        response.raise_for_status()
        result = response.json()
        if isinstance(result, dict) and "exception" in result:
            raise MoodleError(f"{function}: {result.get('message')} ({result.get('errorcode')})")
        if isinstance(result, dict) and result.get("warnings"):
            raise MoodleError(f"{function}: {result['warnings'][0].get('message')}")
        return result

    async def usuario_por(self, campo: str, valor: str) -> dict | None:
        users = await self.call("core_user_get_users_by_field", field=campo, values=[valor])
        return users[0] if users else None

    async def criar_usuario(self, user: dict) -> int:
        return int((await self.call("core_user_create_users", users=[user]))[0]["id"])

    async def atualizar_usuario(self, user: dict) -> None:
        await self.call("core_user_update_users", users=[user])

    async def categoria_por_idnumber(self, idnumber: str) -> dict | None:
        categorias = await self.call(
            "core_course_get_categories", criteria=[{"key": "idnumber", "value": idnumber}]
        )
        return categorias[0] if categorias else None

    async def criar_categoria(self, nome: str, parent: int, idnumber: str) -> int:
        criadas = await self.call(
            "core_course_create_categories",
            categories=[{"name": nome, "parent": parent, "idnumber": idnumber}],
        )
        return int(criadas[0]["id"])

    async def curso_por(self, campo: str, valor: str) -> dict | None:
        cursos = await self.call("core_course_get_courses_by_field", field=campo, value=valor)
        return (cursos.get("courses") or [None])[0]

    async def criar_curso(self, course: dict) -> int:
        return int((await self.call("core_course_create_courses", courses=[course]))[0]["id"])

    async def atualizar_curso(self, course: dict) -> None:
        await self.call("core_course_update_courses", courses=[course])

    async def inscrever(self, role_id: int, course_id: int, user_id: int) -> None:
        """Inscrever quem já está inscrito não muda nada (idempotente)."""
        await self.call(
            "enrol_manual_enrol_users",
            enrolments=[{"roleid": role_id, "userid": user_id, "courseid": course_id}],
        )

    async def desinscrever(self, course_id: int, user_id: int) -> None:
        """Desinscrever quem não está inscrito não muda nada (idempotente)."""
        await self.call(
            "enrol_manual_unenrol_users", enrolments=[{"userid": user_id, "courseid": course_id}]
        )
