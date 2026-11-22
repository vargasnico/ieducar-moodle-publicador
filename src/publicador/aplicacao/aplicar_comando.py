"""Caso de uso: aplicar no Moodle um comando do sincronizador.

1. Comandos da mesma entidade, um por vez (os de entidades diferentes em paralelo).
2. Descarta o comando se já foi aplicada uma versão mais nova da entidade.
3. Consulta o Moodle, pede a decisão ao domínio (atualizar, criar ou nada) e executa.

O retry com backoff envolve a operação inteira (procurar + criar), e não só a chamada
de criação: se o Moodle criou e a resposta se perdeu, a nova tentativa procura,
encontra e só atualiza.
"""

import asyncio
import logging
from collections import defaultdict

from publicador.dominio import versao
from publicador.dominio.decisao import (
    Atualizar,
    Criar,
    decidir_curso,
    decidir_usuario,
    entidade,
)
from publicador.infra.metricas import OBSOLETAS, TEMPO_SINCRONIZACAO
from publicador.infra.retry import com_retry
from publicador.mensagem import Mensagem, agora
from publicador.portas import EstadoPublicador, Moodle

log = logging.getLogger(__name__)


class AindaNaoExisteError(Exception):
    """A inscrição chegou antes do usuário ou do curso: tenta de novo mais tarde."""


class AplicarComando:
    def __init__(self, moodle: Moodle, estado: EstadoPublicador, setup: dict) -> None:
        self.moodle = moodle
        self.estado = estado
        self.role_ids: dict[str, int] = setup["role_ids"]
        self.categoria_raiz: int = setup["category_id"]  # categoria "iEducar"
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._lock_categoria = asyncio.Lock()

    @com_retry  # timeout ou erro 5xx: repete a operação inteira, com backoff
    async def __call__(self, comando: Mensagem) -> None:
        chave = entidade(comando.tipo, comando.dados)
        async with self._locks[chave]:
            if versao.obsoleta(self.estado.ultimo_ciclo(chave), comando.ciclo_iniciado_em):
                log.warning(
                    "%s de %s descartado: já foi aplicada uma versão mais nova",
                    comando.routing_key,
                    chave,
                )
                OBSOLETAS.labels(comando.routing_key).inc()
                return
            await self._aplicar(comando)
            self.estado.registrar_ciclo(chave, comando.ciclo_iniciado_em)
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
                user_id, course_id = await self._ids(d["usuario"], d["curso"])
                if not (user_id and course_id):
                    raise AindaNaoExisteError(f"{d['usuario']} ou {d['curso']} ainda não existe")
                await self.moodle.inscrever(self.role_ids[d["papel"]], course_id, user_id)
            case "moodle.inscricao.remover":
                user_id, course_id = await self._ids(d["usuario"], d["curso"])
                if user_id and course_id:
                    await self.moodle.desinscrever(course_id, user_id)

    async def _usuario(self, d: dict) -> None:
        por_idnumber = await self.moodle.usuario_por("idnumber", d["idnumber"])
        por_login = None
        if por_idnumber is None and d.get("username"):
            por_login = await self.moodle.usuario_por("username", d["username"])
        decisao = decidir_usuario(por_idnumber, por_login, d["suspenso"])
        campos = {k: d[k] for k in ("firstname", "lastname", "email", "idnumber") if k in d}
        if isinstance(decisao, Atualizar):
            await self.moodle.atualizar_usuario(
                {"id": decisao.moodle_id, **campos, "suspended": d["suspenso"]}
            )
            self.estado.mapear(d["idnumber"], decisao.moodle_id)
        elif isinstance(decisao, Criar):
            log.info("Criando usuário %s", d["idnumber"])
            novo = {"username": d["username"], "auth": "manual", "createpassword": True, **campos}
            self.estado.mapear(d["idnumber"], await self.moodle.criar_usuario(novo))

    async def _curso(self, d: dict) -> None:
        decisao = decidir_curso(
            await self.moodle.curso_por("idnumber", d["idnumber"]), d["visivel"]
        )
        if isinstance(decisao, Atualizar):
            await self.moodle.atualizar_curso(
                {"id": decisao.moodle_id, "fullname": d["fullname"], "visible": d["visivel"]}
            )
            self.estado.mapear(d["idnumber"], decisao.moodle_id)
        elif isinstance(decisao, Criar):
            log.info("Criando curso %s", d["idnumber"])
            novo = {
                "fullname": d["fullname"],
                "shortname": d["shortname"],
                "idnumber": d["idnumber"],
                "categoryid": await self._categoria(d["ano"]),
            }
            self.estado.mapear(d["idnumber"], await self.moodle.criar_curso(novo))

    async def _categoria(self, ano: int) -> int:
        """Categoria do ano letivo (iEducar › 2026). Uma criação por vez."""
        async with self._lock_categoria:
            idnumber = f"ieducar-{ano}"
            existente = await self.moodle.categoria_por_idnumber(idnumber)
            if existente:
                return int(existente["id"])
            return await self.moodle.criar_categoria(str(ano), self.categoria_raiz, idnumber)

    async def _ids(self, usuario: str, curso: str) -> tuple[int | None, int | None]:
        return (
            await self._id(usuario, self.moodle.usuario_por),
            await self._id(curso, self.moodle.curso_por),
        )

    async def _id(self, idnumber: str, buscar) -> int | None:
        """Id do Moodle pelo mapeamento local ou, se não houver, procurando no Moodle."""
        if (moodle_id := self.estado.moodle_id(idnumber)) is not None:
            return moodle_id
        encontrado = await buscar("idnumber", idnumber)
        if encontrado:
            self.estado.mapear(idnumber, int(encontrado["id"]))
            return int(encontrado["id"])
        return None
