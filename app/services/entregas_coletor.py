"""
Módulo fora da versão pública.

Na operação este arquivo integra o sistema com serviços externos (rastreamento
de frota, sensores de temperatura, alertas, contratos) ou com dados que não
podem sair da empresa. Aqui fica só o "contorno" para o resto do código
continuar importando normalmente: qualquer nome pedido devolve um objeto
neutro, e as rotas desses módulos não existem nesta versão.
"""
from flask import Blueprint as _Blueprint

_cache = {}


def __getattr__(nome):
    if nome.startswith("__"):
        raise AttributeError(nome)
    if nome not in _cache:
        if nome.endswith("_bp"):
            _cache[nome] = _Blueprint(__name__.rsplit(".", 1)[-1] + "_" + nome + "_publico", __name__)
        elif nome[:1].isupper():
            _cache[nome] = type(nome, (Exception,), {})
        else:
            def _neutro(*_a, **_k):
                return None
            _neutro.__name__ = nome
            _cache[nome] = _neutro
    return _cache[nome]
