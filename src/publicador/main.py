"""Serviço publicador: aplica no Moodle os comandos do sincronizador.

Único serviço que fala com o Moodle. Dono do banco publicador.db (mapeamento
idnumber → id do Moodle).
"""

import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from publicador.api import rotas_comuns
from publicador.broker import Broker
from publicador.logs import configurar_logs
from publicador.moodle import MoodleClient, carregar_setup
from publicador.publicacao import Publicador


@asynccontextmanager
async def lifespan(app: FastAPI):
    configurar_logs("publicador")
    db = sqlite3.connect(os.environ.get("DATABASE_PATH", "data/publicador.db"))
    broker = Broker(os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost/"), "publicador")
    await broker.conectar()
    setup = carregar_setup(Path(os.environ.get("MOODLE_SETUP_FILE", "/shared/moodle.json")))
    # Timeout: 3s para conectar, 10s para responder.
    http = httpx.AsyncClient(
        base_url=os.environ.get("MOODLE_URL", "http://localhost:8080"),
        timeout=httpx.Timeout(float(os.environ.get("MOODLE_TIMEOUT", 10)), connect=3),
    )
    publicador = Publicador(db, MoodleClient(http, setup["token"]), setup)
    app.state.broker, app.state.publicador = broker, publicador
    # Até 10 comandos em paralelo: o Moodle é o gargalo.
    await broker.consumir("publicador", "moodle.#", publicador.tratar, prefetch=10)
    yield
    await http.aclose()
    await broker.fechar()


app = FastAPI(title="Publicador no Moodle", lifespan=lifespan)
rotas_comuns(app)


@app.post("/dlq/reprocessar", tags=["filas"])
async def reprocessar_dlq() -> dict:
    """Devolve à fila as mensagens que foram para a DLQ (ex.: depois que o Moodle voltou)."""
    return {"reprocessadas": await app.state.broker.reprocessar_dlq("publicador")}


@app.post("/simular/resposta-perdida", tags=["falhas"])
def simular_resposta_perdida() -> dict:
    """Cenário 3: a próxima criação acontece no Moodle, mas a resposta é descartada."""
    app.state.publicador.perder_proxima_resposta = True
    return {"perder_proxima_resposta": True}
