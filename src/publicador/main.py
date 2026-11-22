"""Serviço publicador: aplica no Moodle os comandos do sincronizador. Único serviço
que fala com o Moodle.

Este arquivo só compõe as peças (arquitetura hexagonal):

    adaptadores/rabbitmq.py ──comando──▶ aplicacao/aplicar_comando.py ──▶ dominio/
                                               │ portas.py
                          adaptadores/sqlite.py ◀┘└▶ adaptadores/moodle.py
"""

import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from publicador.adaptadores.api import criar_app
from publicador.adaptadores.moodle import MoodleClient, carregar_setup
from publicador.adaptadores.rabbitmq import Broker
from publicador.adaptadores.simulacao import MoodleComFalhaSimulada
from publicador.adaptadores.sqlite import EstadoSqlite
from publicador.aplicacao.aplicar_comando import AplicarComando
from publicador.infra.logs import configurar_logs


@asynccontextmanager
async def lifespan(app: FastAPI):
    configurar_logs("publicador")
    estado = EstadoSqlite(sqlite3.connect(os.environ.get("DATABASE_PATH", "data/publicador.db")))
    broker = Broker(os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost/"), "publicador")
    await broker.conectar()
    setup = carregar_setup(Path(os.environ.get("MOODLE_SETUP_FILE", "/shared/moodle.json")))
    # Timeout: 3s para conectar, 10s para responder.
    http = httpx.AsyncClient(
        base_url=os.environ.get("MOODLE_URL", "http://localhost:8080"),
        timeout=httpx.Timeout(float(os.environ.get("MOODLE_TIMEOUT", 10)), connect=3),
    )
    moodle = MoodleComFalhaSimulada(MoodleClient(http, setup["token"]))
    aplicar_comando = AplicarComando(moodle, estado, setup)
    app.state.broker, app.state.moodle = broker, moodle
    # Até 10 comandos em paralelo: o Moodle é o gargalo.
    await broker.consumir("publicador", "moodle.#", aplicar_comando, prefetch=10)
    yield
    await http.aclose()
    await broker.fechar()


app = criar_app(lifespan)
