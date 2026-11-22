"""Idempotência: o que fazer com um usuário ou curso, dado o que já existe no Moodle.

A regra é **procurar antes de criar**. Todo usuário e curso criado pela integração
leva um `idnumber` (ex.: ieducar-aluno-20). Se já existe um registro com ele, só
atualizamos. Isso resolve dois cenários de uma vez:
- a mesma mensagem processada duas vezes (reentrega depois de um reinício) não cria
  nada de novo (cenário 4);
- o Moodle criou o registro mas a resposta se perdeu: a nova tentativa procura,
  encontra e só atualiza (cenário 3).

Funções puras: quem consulta o Moodle e executa a decisão é a camada de aplicação.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Atualizar:
    moodle_id: int


@dataclass(frozen=True)
class Criar:
    pass


@dataclass(frozen=True)
class NadaAFazer:
    pass


Decisao = Atualizar | Criar | NadaAFazer


class ConflitoError(Exception):
    """O login já pertence a outro usuário do Moodle (com outro idnumber)."""


def decidir_usuario(por_idnumber: dict | None, por_login: dict | None, suspenso: bool) -> Decisao:
    """`por_login` é a conta com o mesmo login, procurada só se não houver nenhuma com
    o idnumber. Uma conta criada à mão (sem idnumber) é adotada pela integração."""
    if por_idnumber:
        return Atualizar(int(por_idnumber["id"]))
    if por_login:
        if por_login.get("idnumber"):
            raise ConflitoError(f"login {por_login['username']} já pertence a outro usuário")
        return Atualizar(int(por_login["id"]))
    # Não existe: só cria quem deve estar ativo (suspender quem não existe é nada).
    return NadaAFazer() if suspenso else Criar()


def decidir_curso(existente: dict | None, visivel: bool) -> Decisao:
    if existente:
        return Atualizar(int(existente["id"]))
    # Não existe: só cria o que deve estar visível (ocultar o que não existe é nada).
    return Criar() if visivel else NadaAFazer()


def entidade(tipo_comando: str, dados: dict) -> str:
    """Chave da entidade do comando. Adicionar e remover a mesma inscrição têm a mesma
    chave: a mais nova das duas é a que vale."""
    if tipo_comando.startswith("moodle.inscricao."):
        return f"inscricao:{dados['usuario']}|{dados['curso']}|{dados['papel']}"
    return f"{tipo_comando.split('.')[1]}:{dados['idnumber']}"  # usuario:… ou curso:…
