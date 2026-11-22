"""Aplica no Moodle os comandos do sincronizador, de forma idempotente.

A regra que torna tudo idempotente: **procurar antes de criar**. Todo usuário e curso
criado pela integração leva um `idnumber` (ex.: ieducar-aluno-20). Antes de criar,
o publicador procura no Moodle por esse idnumber; se já existe, só atualiza.

Isso resolve dois cenários de uma vez:
- a mesma mensagem processada duas vezes (reentrega depois de um reinício) não cria
  nada de novo (cenário 4);
- o Moodle criou o registro mas a resposta se perdeu: o retry procura, encontra e só
  atualiza (cenário 3). Por isso o retry envolve a operação inteira (procurar +
  criar), e não só a chamada de criação.

O publicador também guarda o mapeamento idnumber → id do Moodle, usado nas
inscrições (que precisam dos ids numéricos de usuário e curso), e a versão da última
mensagem aplicada a cada entidade: uma mensagem mais antiga (ex.: reprocessada da DLQ
depois que uma mais nova passou) é descartada (versoes.py).
"""

import asyncio
import logging
import sqlite3
from collections import defaultdict

import httpx

from publicador.mensagem import Mensagem, agora
from publicador.metricas import OBSOLETAS, TEMPO_SINCRONIZACAO
from publicador.moodle import MoodleClient
from publicador.retry import com_retry
from publicador.versoes import Versoes

log = logging.getLogger(__name__)


class AindaNaoExisteError(Exception):
    """A inscrição chegou antes do usuário ou do curso: tenta de novo mais tarde."""


class Publicador:
    def __init__(self, db: sqlite3.Connection, moodle: MoodleClient, setup: dict) -> None:
        self.db = db
        self.moodle = moodle
        self.setup = setup  # token, category_id (categoria "iEducar") e role_ids
        self._lock_categoria = asyncio.Lock()
        self.versoes = Versoes(db)
        # Comandos da mesma entidade, um por vez (os de entidades diferentes em paralelo).
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        # Para demonstrar o cenário 3: a próxima criação acontece no Moodle, mas a
        # resposta é descartada, como se não tivesse chegado (POST /simular/...).
        self.perder_proxima_resposta = False
        db.execute(
            "CREATE TABLE IF NOT EXISTS mapeamento (idnumber TEXT PRIMARY KEY, moodle_id INTEGER)"
        )

    @com_retry  # timeout ou erro 5xx: repete a operação inteira, com backoff
    async def tratar(self, comando: Mensagem) -> None:
        chave = _chave(comando)
        async with self._locks[chave]:
            if self.versoes.obsoleta(chave, comando.ciclo_iniciado_em):
                log.warning(
                    "%s de %s descartado: já foi aplicada uma versão mais nova",
                    comando.routing_key,
                    chave,
                )
                OBSOLETAS.labels(comando.routing_key).inc()
                return
            await self._aplicar(comando)
            self.versoes.registrar(chave, comando.ciclo_iniciado_em)
        log.info("%s aplicado: %s", comando.routing_key, chave)
        TEMPO_SINCRONIZACAO.labels(comando.tipo).observe(
            (agora() - comando.ciclo_iniciado_em).total_seconds()
        )

    async def _aplicar(self, comando: Mensagem) -> None:
        d = comando.dados
        match comando.tipo:
            case "moodle.usuario.sincronizar":
                await self._usuario(d)
            case "moodle.curso.sincronizar":
                await self._curso(d)
            case "moodle.inscricao.adicionar":
                role_id = self.setup["role_ids"][d["papel"]]
                user_id, course_id = await self._ids(d["usuario"], d["curso"])
                if not (user_id and course_id):
                    raise AindaNaoExisteError(f"{d['usuario']} ou {d['curso']} ainda não existe")
                await self.moodle.inscrever(role_id, course_id, user_id)
            case "moodle.inscricao.remover":
                user_id, course_id = await self._ids(d["usuario"], d["curso"])
                if user_id and course_id:
                    await self.moodle.desinscrever(course_id, user_id)

    async def _usuario(self, d: dict) -> None:
        existente = await self.moodle.usuario_por("idnumber", d["idnumber"])
        if existente is None and d.get("username"):
            # Conta criada à mão com o mesmo login e sem idnumber: a integração adota.
            existente = await self.moodle.usuario_por("username", d["username"])
            if existente and existente.get("idnumber"):
                raise ValueError(f"login {d['username']} já pertence a outro usuário do Moodle")
        campos = {k: d[k] for k in ("firstname", "lastname", "email", "idnumber") if k in d}
        if existente:
            await self.moodle.atualizar_usuario(
                {"id": existente["id"], **campos, "suspended": d["suspenso"]}
            )
            self._mapear(d["idnumber"], existente["id"])
        elif not d["suspenso"]:  # não existe e deveria estar suspenso: nada a fazer
            log.info("Criando usuário %s", d["idnumber"])
            novo = {"username": d["username"], "auth": "manual", "createpassword": True, **campos}
            self._mapear(d["idnumber"], await self._criar(self.moodle.criar_usuario, novo))

    async def _curso(self, d: dict) -> None:
        existente = await self.moodle.curso_por("idnumber", d["idnumber"])
        if existente:
            await self.moodle.atualizar_curso(
                {"id": existente["id"], "fullname": d["fullname"], "visible": d["visivel"]}
            )
            self._mapear(d["idnumber"], existente["id"])
        elif d["visivel"]:
            log.info("Criando curso %s", d["idnumber"])
            novo = {
                "fullname": d["fullname"],
                "shortname": d["shortname"],
                "idnumber": d["idnumber"],
                "categoryid": await self._categoria(d["ano"]),
            }
            self._mapear(d["idnumber"], await self._criar(self.moodle.criar_curso, novo))

    async def _criar(self, criar, dados: dict) -> int:
        moodle_id = await criar(dados)
        if self.perder_proxima_resposta:
            self.perder_proxima_resposta = False
            log.warning(
                "SIMULAÇÃO: o Moodle criou %s, mas a resposta foi descartada", dados["idnumber"]
            )
            raise httpx.ReadTimeout("resposta do Moodle não chegou (simulado)")
        return moodle_id

    async def _categoria(self, ano: int) -> int:
        """Categoria do ano letivo (iEducar › 2026). Uma criação por vez."""
        async with self._lock_categoria:
            idnumber = f"ieducar-{ano}"
            existente = await self.moodle.categoria_por_idnumber(idnumber)
            if existente:
                return int(existente["id"])
            return await self.moodle.criar_categoria(str(ano), self.setup["category_id"], idnumber)

    async def _ids(self, usuario: str, curso: str) -> tuple[int | None, int | None]:
        return await self._id(usuario, self.moodle.usuario_por, "idnumber"), await self._id(
            curso, self.moodle.curso_por, "idnumber"
        )

    async def _id(self, idnumber: str, buscar, campo: str) -> int | None:
        linha = self.db.execute(
            "SELECT moodle_id FROM mapeamento WHERE idnumber = ?", (idnumber,)
        ).fetchone()
        if linha:
            return linha[0]
        encontrado = await buscar(campo, idnumber)
        if encontrado:
            self._mapear(idnumber, encontrado["id"])
            return int(encontrado["id"])
        return None

    def _mapear(self, idnumber: str, moodle_id: int) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO mapeamento VALUES (?, ?)", (idnumber, int(moodle_id))
            )


def _chave(comando: Mensagem) -> str:
    """Entidade a que o comando se refere. Adicionar e remover a mesma inscrição têm a
    mesma chave: a mais nova das duas é a que vale."""
    d = comando.dados
    if comando.tipo.startswith("moodle.inscricao."):
        return f"inscricao:{d['usuario']}|{d['curso']}|{d['papel']}"
    return f"{comando.tipo.split('.')[1]}:{d['idnumber']}"  # usuario:… ou curso:…
