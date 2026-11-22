"""Retry com backoff exponencial para as chamadas ao i-Educar e ao Moodle.

Só repete falhas que tendem a passar sozinhas (rede, timeout, erro 5xx). As esperas
entre as tentativas são 0,5s, 1s, 2s... com um pouco de aleatoriedade (jitter), para
que muitas mensagens que falharam juntas não voltem todas ao mesmo tempo.

Se as tentativas acabarem, a exceção sobe: o consumidor manda a mensagem para a fila
de retry do RabbitMQ (e, depois de várias falhas, para a DLQ).
"""

import logging

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

log = logging.getLogger(__name__)


def transitorio(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):  # conexão recusada, timeout...
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


com_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(multiplier=0.5, max=4),
    retry=retry_if_exception(transitorio),
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,
)
