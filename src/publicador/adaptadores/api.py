"""Adaptador de entrada HTTP: health check, métricas, DLQ e a simulação do cenário 3."""

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest


def criar_app(lifespan) -> FastAPI:
    app = FastAPI(title="Publicador no Moodle", lifespan=lifespan)

    @app.get("/health", tags=["status"])
    def health(request: Request) -> JSONResponse:
        """O serviço está no ar e conectado ao RabbitMQ."""
        ok = request.app.state.broker.conectado
        return JSONResponse(
            {"status": "ok" if ok else "sem broker", "broker": ok}, status_code=200 if ok else 503
        )

    @app.get("/metrics", tags=["status"], include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/dlq/reprocessar", tags=["filas"])
    async def reprocessar_dlq(request: Request) -> dict:
        """Devolve à fila os comandos da DLQ (ex.: depois que o Moodle voltou)."""
        return {"reprocessadas": await request.app.state.broker.reprocessar_dlq("publicador")}

    @app.post("/simular/resposta-perdida", tags=["falhas"])
    def simular_resposta_perdida(request: Request) -> dict:
        """Cenário 3: a próxima criação acontece no Moodle, mas a resposta é descartada."""
        request.app.state.moodle.perder_proxima_resposta = True
        return {"perder_proxima_resposta": True}

    return app
