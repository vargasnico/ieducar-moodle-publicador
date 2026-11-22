"""Adaptador para demonstrar o cenário 3: embrulha o adaptador do Moodle e, quando
ligado (POST /simular/resposta-perdida), deixa a próxima criação acontecer no Moodle
mas descarta a resposta, como se ela não tivesse chegado.

Por implementar a mesma porta Moodle, o caso de uso não sabe que está sendo testado.
"""

import logging

import httpx

from publicador.portas import Moodle

log = logging.getLogger(__name__)


class MoodleComFalhaSimulada:
    def __init__(self, moodle: Moodle) -> None:
        self._moodle = moodle
        self.perder_proxima_resposta = False

    def __getattr__(self, nome: str):
        return getattr(self._moodle, nome)  # tudo o que não é criação passa direto

    async def criar_usuario(self, user: dict) -> int:
        return self._resposta(await self._moodle.criar_usuario(user), user["idnumber"])

    async def criar_curso(self, course: dict) -> int:
        return self._resposta(await self._moodle.criar_curso(course), course["idnumber"])

    def _resposta(self, moodle_id: int, idnumber: str) -> int:
        if self.perder_proxima_resposta:
            self.perder_proxima_resposta = False
            log.warning("SIMULAÇÃO: o Moodle criou %s, mas a resposta foi descartada", idnumber)
            raise httpx.ReadTimeout("resposta do Moodle não chegou (simulado)")
        return moodle_id
