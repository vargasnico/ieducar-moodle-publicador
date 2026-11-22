"""Métricas do publicador (Prometheus), expostas em /metrics."""

from prometheus_client import Counter, Histogram

MENSAGENS = Counter(
    "mensagens_total",
    "Comandos consumidos, por resultado (sucesso, retry ou dlq)",
    ["tipo", "resultado"],
)
TEMPO_SINCRONIZACAO = Histogram(
    "sincronizacao_segundos",
    "Do início do ciclo no detector até a alteração ser aplicada no Moodle",
    ["tipo"],
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600),
)
OBSOLETAS = Counter(
    "mensagens_obsoletas_total",
    "Comandos descartados porque uma versão mais nova da entidade já foi aplicada",
    ["tipo"],
)
