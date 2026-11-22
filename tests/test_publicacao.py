"""Idempotência do publicador, sem rede (Moodle em memória)."""

import sqlite3
from datetime import UTC, datetime
from itertools import count

import httpx
import pytest

from publicador.mensagem import Mensagem
from publicador.publicacao import AindaNaoExisteError, Publicador


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def msg(tipo: str, dados: dict) -> Mensagem:
    return Mensagem(
        tipo=tipo, correlation_id="c1", ciclo_iniciado_em=datetime.now(UTC), dados=dados
    )


# --- Publicador ----------------------------------------------------------------


class MoodleFalso:
    def __init__(self) -> None:
        self.ids = count(100)
        self.users: dict[int, dict] = {}
        self.courses: dict[int, dict] = {}
        self.enrolments: set[tuple] = set()

    async def usuario_por(self, campo, valor):
        return next((u for u in self.users.values() if u.get(campo) == valor), None)

    async def criar_usuario(self, user):
        if any(u["username"] == user["username"] for u in self.users.values()):
            raise ValueError("Username already exists")
        i = next(self.ids)
        self.users[i] = {**user, "id": i}
        return i

    async def atualizar_usuario(self, user):
        self.users[user["id"]].update(user)

    async def curso_por(self, campo, valor):
        return next((c for c in self.courses.values() if c.get(campo) == valor), None)

    async def criar_curso(self, course):
        i = next(self.ids)
        self.courses[i] = {**course, "id": i}
        return i

    async def atualizar_curso(self, course):
        self.courses[course["id"]].update(course)

    async def categoria_por_idnumber(self, idnumber):
        return {"id": 1}

    async def inscrever(self, role_id, course_id, user_id):
        self.enrolments.add((course_id, user_id, role_id))

    async def desinscrever(self, course_id, user_id):
        self.enrolments = {e for e in self.enrolments if e[:2] != (course_id, user_id)}


USUARIO = {
    "idnumber": "ieducar-aluno-20",
    "username": "aluno20",
    "firstname": "Carla",
    "lastname": "Dias",
    "email": "x@y",
    "suspenso": False,
}


@pytest.fixture
def pub():
    moodle = MoodleFalso()
    setup = {"category_id": 1, "role_ids": {"student": 5}}
    return Publicador(sqlite3.connect(":memory:"), moodle, setup), moodle  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_mesmo_comando_duas_vezes_cria_um_usuario(pub):
    p, moodle = pub
    comando = msg("moodle.usuario.sincronizar", USUARIO)
    await p.tratar(comando)
    await p.tratar(comando)
    assert len(moodle.users) == 1


@pytest.mark.anyio
async def test_resposta_perdida_depois_de_criar_nao_duplica(pub):
    """Cenário 3: o Moodle cria, a resposta se perde (timeout), o retry procura pelo
    idnumber, encontra e só atualiza."""
    p, moodle = pub
    p.perder_proxima_resposta = True
    await p.tratar(msg("moodle.usuario.sincronizar", USUARIO))
    assert len(moodle.users) == 1


@pytest.mark.anyio
async def test_inscricao_antes_do_curso_espera(pub):
    p, moodle = pub
    await p.tratar(msg("moodle.usuario.sincronizar", USUARIO))
    inscricao = msg(
        "moodle.inscricao.adicionar",
        {
            "usuario": "ieducar-aluno-20",
            "curso": "ieducar-turma-10-componente-1",
            "papel": "student",
        },
    )
    with pytest.raises(AindaNaoExisteError):
        await p.tratar(inscricao)
    await p.tratar(
        msg(
            "moodle.curso.sincronizar",
            {
                "idnumber": "ieducar-turma-10-componente-1",
                "shortname": "2026-T10-C1",
                "fullname": "1º ano A – Português (2026)",
                "ano": 2026,
                "visivel": True,
            },
        )
    )
    await p.tratar(inscricao)
    assert len(moodle.enrolments) == 1


@pytest.mark.anyio
async def test_moodle_fora_do_ar_propaga_o_erro_para_o_retry_da_fila(pub):
    p, moodle = pub

    async def fora(*_):
        raise httpx.ConnectError("Moodle fora do ar")

    moodle.usuario_por = fora
    with pytest.raises(httpx.ConnectError):
        await p.tratar(msg("moodle.usuario.sincronizar", USUARIO))


def no_ciclo(tipo: str, dados: dict, hora: int) -> Mensagem:
    return Mensagem(
        tipo=tipo,
        correlation_id=f"ciclo-{hora}",
        ciclo_iniciado_em=datetime(2026, 11, 22, hora, tzinfo=UTC),
        dados=dados,
    )


@pytest.mark.anyio
async def test_comando_antigo_reprocessado_da_dlq_nao_desfaz_o_novo(pub):
    """O ciclo das 10h (aluno ativo) ficou na DLQ; o das 11h (aluno suspenso) passou.
    Reprocessar o das 10h não pode reativar o aluno."""
    p, moodle = pub
    await p.tratar(no_ciclo("moodle.usuario.sincronizar", USUARIO, 9))
    await p.tratar(no_ciclo("moodle.usuario.sincronizar", {**USUARIO, "suspenso": True}, 11))
    await p.tratar(no_ciclo("moodle.usuario.sincronizar", USUARIO, 10))
    assert next(iter(moodle.users.values()))["suspended"] is True


@pytest.mark.anyio
async def test_inscricao_antiga_nao_volta_depois_de_removida(pub):
    p, moodle = pub
    await p.tratar(no_ciclo("moodle.usuario.sincronizar", USUARIO, 9))
    await p.tratar(
        no_ciclo(
            "moodle.curso.sincronizar",
            {
                "idnumber": "ieducar-turma-10-componente-1",
                "shortname": "2026-T10-C1",
                "fullname": "1º ano A – Português (2026)",
                "ano": 2026,
                "visivel": True,
            },
            9,
        )
    )
    inscricao = {
        "usuario": "ieducar-aluno-20",
        "curso": "ieducar-turma-10-componente-1",
        "papel": "student",
    }
    await p.tratar(no_ciclo("moodle.inscricao.remover", inscricao, 11))
    await p.tratar(no_ciclo("moodle.inscricao.adicionar", inscricao, 10))
    assert moodle.enrolments == set()
    # Um comando de um ciclo posterior continua valendo.
    await p.tratar(no_ciclo("moodle.inscricao.adicionar", inscricao, 12))
    assert len(moodle.enrolments) == 1
