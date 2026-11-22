# ieducar-moodle-publicador

Microsserviço da integração i-Educar → Moodle que **escreve no Moodle sem
duplicar**: consome os comandos do sincronizador e os aplica pelos Web Services REST
do Moodle. É o único serviço que fala com o Moodle.

Faz parte da integração montada em
[ieducar-moodle-integration](https://github.com/vargasnico/ieducar-moodle-integration).

## Contrato

**Consome** a fila `publicador` (routing key `moodle.#`), até 10 em paralelo:

| Routing key | O que faz no Moodle |
|---|---|
| `moodle.usuario.sincronizar.v1` | Procura pelo `idnumber` (ou adota uma conta com o mesmo login e sem `idnumber`); atualiza ou cria; `suspenso` suspende |
| `moodle.curso.sincronizar.v1` | Procura pelo `idnumber`; atualiza nome e visibilidade ou cria na categoria do ano |
| `moodle.inscricao.adicionar.v1` | Inscreve; se o usuário ou o curso ainda não existe, falha e tenta de novo pela fila de retry |
| `moodle.inscricao.remover.v1` | Desinscreve |

**Publica:** nada.

**Dados próprios:** `mapeamento` (idnumber → id do Moodle) e `versao` (ciclo da
última mensagem aplicada a cada usuário, curso e inscrição).

## Resiliência

- **Procurar antes de criar:** a mesma mensagem processada duas vezes não duplica
  nada, e uma criação cuja resposta se perdeu é encontrada na tentativa seguinte.
- Timeout de 3s/10s e 3 tentativas com backoff (tenacity) na operação inteira
  (procurar + criar), não só na chamada de criação.
- Depois disso, a mensagem volta pela fila `publicador.retry` (10s); depois de 5
  falhas vai para `publicador.dlq`.
- **Versão por entidade:** um comando de um ciclo anterior ao último aplicado à mesma
  entidade é descartado (`mensagens_obsoletas_total`). Por isso reprocessar a DLQ não
  desfaz uma alteração mais nova. Comandos da mesma entidade são tratados um por vez.

## API

| Rota | Descrição |
|---|---|
| `POST /dlq/reprocessar` | Devolve à fila os comandos da DLQ (ex.: depois de o Moodle voltar) |
| `POST /simular/resposta-perdida` | Teste: a próxima criação acontece, mas a resposta é descartada |
| `GET /health` · `GET /metrics` | Health check · `mensagens_total`, `sincronizacao_segundos` |

## Configuração

| Variável | Padrão | Descrição |
|---|---|---|
| `RABBITMQ_URL` | `amqp://guest:guest@localhost/` | Broker |
| `DATABASE_PATH` | `data/publicador.db` | Banco do serviço |
| `MOODLE_URL` | `http://localhost:8080` | Moodle |
| `MOODLE_SETUP_FILE` | `/shared/moodle.json` | Token e ids gerados pelo `moodle-setup` do ambiente |
| `MOODLE_TIMEOUT` | `10` | Timeout de resposta (s) |

## Estrutura (arquitetura hexagonal)

O domínio fica no centro e não conhece banco, broker nem HTTP; o caso de uso o usa
por meio das portas (interfaces), e os adaptadores as implementam. Trocar o SQLite
ou o RabbitMQ é trocar um adaptador; os testes usam versões em memória das portas.

```
src/publicador/
├── dominio/                regras puras, sem banco, rede nem framework
│   ├── decisao.py          procurar antes de criar: atualizar, criar ou nada a fazer
│   └── versao.py           mensagem de um ciclo mais antigo é obsoleta
├── aplicacao/
│   └── aplicar_comando.py  caso de uso: versão, decisão e execução no Moodle (com retry)
├── portas.py               interfaces: Moodle, EstadoPublicador
├── adaptadores/            implementações das portas e entradas do serviço
│   ├── moodle.py           porta Moodle sobre os Web Services REST
│   ├── simulacao.py        embrulha o Moodle para simular a resposta perdida (cenário 3)
│   ├── sqlite.py           porta EstadoPublicador (mapeamento de ids, versões)
│   ├── rabbitmq.py         entrada (consumo, retry, DLQ)
│   └── api.py              entrada HTTP (/dlq/reprocessar, /simular/..., /health, /metrics)
├── infra/                  logs JSON, métricas, retry
├── mensagem.py             formato das mensagens (o contrato com os outros serviços)
└── main.py                 composição: cria os adaptadores e os injeta no caso de uso
```

## Desenvolvimento

```bash
uv sync
uv run pytest
```

## Deploy

Cada tag `v*` roda o [CI](.github/workflows/ci.yml): testes e a imagem
`ghcr.io/<dono>/ieducar-moodle-publicador:<tag>`. No ambiente:
`docker compose up -d --build publicador`, sem mexer nos outros serviços.
