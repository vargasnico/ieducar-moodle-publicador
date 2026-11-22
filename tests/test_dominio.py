"""Domínio puro: decisão de idempotência ("procurar antes de criar") e versão."""

import pytest

from publicador.dominio.decisao import (
    Atualizar,
    ConflitoError,
    Criar,
    NadaAFazer,
    decidir_curso,
    decidir_usuario,
    entidade,
)


def test_usuario_existente_pelo_idnumber_e_atualizado():
    assert decidir_usuario({"id": 5}, None, suspenso=False) == Atualizar(5)


def test_conta_manual_sem_idnumber_e_adotada():
    assert decidir_usuario(None, {"id": 7, "username": "x", "idnumber": ""}, False) == Atualizar(7)


def test_login_de_outro_usuario_e_conflito():
    with pytest.raises(ConflitoError):
        decidir_usuario(None, {"id": 7, "username": "x", "idnumber": "outro"}, False)


def test_so_cria_quem_deve_estar_ativo_e_visivel():
    assert decidir_usuario(None, None, suspenso=False) == Criar()
    assert decidir_usuario(None, None, suspenso=True) == NadaAFazer()
    assert decidir_curso(None, visivel=True) == Criar()
    assert decidir_curso(None, visivel=False) == NadaAFazer()


def test_adicionar_e_remover_a_mesma_inscricao_tem_a_mesma_chave():
    d = {"usuario": "u", "curso": "c", "papel": "student"}
    assert entidade("moodle.inscricao.adicionar", d) == entidade("moodle.inscricao.remover", d)
    assert entidade("moodle.usuario.sincronizar", {"idnumber": "u"}) == "usuario:u"
