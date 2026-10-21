"""A suíte nunca lê a produção, qualquer que seja o padrão do `config.toml`.

O padrão do arquivo é a produção (é ela que o dia a dia consulta). Os testes com
banco precisam do ambiente local, e um teste que esquecesse de pedi-lo cairia no
túnel SSH do VPS: aqui o ambiente local é forçado antes de qualquer teste.
Os que testam a escolha de ambiente trocam a variável com `monkeypatch`.
"""

import os

os.environ["FINANCAS_MCP_AMBIENTE"] = "local"
