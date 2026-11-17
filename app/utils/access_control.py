"""
Controle de acesso por módulo (MEL-3).

Cada usuário tem uma lista de módulos liberados em `users.modulos_permitidos`
(CSV, ex.: "monitoramento,adensamento"). Vazio = acesso a todos os módulos —
mesma convenção usada em `unidades_permitidas`, então usuários existentes não
perdem acesso a nada quando essa coluna é criada.

`admin` e `master` sempre têm acesso a todos os módulos, independente do que
estiver salvo — o campo serve para o admin restringir OUTROS usuários
(operador/visualizador), não para se autorrestringir por engano.
"""
from __future__ import annotations
from functools import wraps
from flask import session, jsonify, redirect, url_for, request

MODULOS = {
    "monitoramento": "Monitoramento",
    "adensamento": "Adensamento",
    "armazem": "Painel do Armazém",
    "xsense": "Câmaras frias",
    "fifo": "Devolução & Vencimento",
    "mapa_estoque": "Mapa de Estoque",
    "giro": "Giro de Estoque",
    "produtividade": "Produtividade",
    "expedito": "Expedito",
    "slas": "SLAs",
    "entregas": "Monitoramento de Entregas",
}


def modulos_da_sessao() -> list[str]:
    """Módulos liberados para o usuário logado (lista vazia = todos)."""
    raw = session.get("modulos_permitidos", "") or ""
    return [m.strip() for m in raw.split(",") if m.strip()]


def tem_acesso_modulo(nome_modulo: str) -> bool:
    if session.get("perfil") in ("admin", "master"):
        return True
    permitidos = modulos_da_sessao()
    return not permitidos or nome_modulo in permitidos


# Caminho da página inicial de cada módulo (strings literais, conferidas nas
# rotas dos blueprints — mais robusto que url_for por nome de endpoint).
MODULO_ROTA = {
    "monitoramento": "/",
    "entregas": "/entregas",
    "adensamento": "/adensamento",
    "armazem": "/armazem",
    "xsense": "/xsense",
    "fifo": "/fifo",
    "mapa_estoque": "/mapa-estoque",
    "giro": "/giro",
    "produtividade": "/produtividade",
    "expedito": "/expedito",
    "slas": "/slas",
}


def rota_inicial() -> str:
    """Caminho de entrada do usuário logado. Operador de call center entra
    direto em Monitoramento de Entregas; quem tem Monitoramento (ou acesso a
    tudo) entra na raiz; senão, o primeiro módulo liberado dele. Serve tanto
    para o pouso no login quanto para não jogar quem não tem Monitoramento
    de volta na raiz (que exige Monitoramento) — ver modulo_required."""
    if session.get("operador_call_center"):
        return "/entregas"
    if session.get("perfil") in ("admin", "master"):
        return "/"
    permitidos = modulos_da_sessao()
    if not permitidos or "monitoramento" in permitidos:
        return "/"
    for m in MODULOS:  # ordem estável do dicionário MODULOS
        if m in permitidos and m in MODULO_ROTA:
            return MODULO_ROTA[m]
    return "/entregas"


# ── Papéis com escopo de 1 CD (redesenho de hierarquia, 24/08/2026) ────
# Gerente/Desenvolvedor = perfil admin/master de sempre, acesso irrestrito.
# Os 4 abaixo ficam presos ao "CD cadastrado" deles (1ª unidade de
# unidades_permitidas) e trabalham em cima da CONTA CANÔNICA daquele CD —
# não têm dado próprio, leem/gravam sempre no mesmo lugar (ver
# usuario_efetivo). Coordenador e Analista podem gravar; Qualidade é
# sempre só-leitura; Operador é só-leitura com 2 exceções tratadas pela
# própria rota (estoque em trânsito e upload no Adensamento).
PAPEIS_ESCOPO_CD = ("coordenador", "analista", "qualidade", "operador")
PAPEIS_ESCRITA_GERAL = ("coordenador", "analista")


def cd_do_usuario() -> str:
    """"CD cadastrado" do usuário logado — 1ª sigla de unidades_vinculadas
    da sessão (redesenho de controle de acesso, 29/08/2026: antes usava
    unidades_permitidas, mas essa lista agora pode incluir CDs que a
    pessoa só tem permissão de VER — cobertura temporária de outra
    unidade — sem ser dona dos dados; usar ela aqui deixaria a pessoa
    GRAVAR num CD que devia ser só leitura). Cai pra unidades_permitidas
    se vinculadas ainda estiver vazio — conta antiga que ainda não foi
    resalva no cadastro depois dessa migração continua funcionando igual
    a antes, sem quebrar ninguém. Vazio pra quem não tem 1 CD fixo
    (admin/master)."""
    raw = session.get("unidades_vinculadas", "") or session.get("unidades_permitidas", "") or ""
    partes = [u.strip() for u in raw.split(",") if u.strip()]
    return partes[0] if partes else ""


def unidades_extras_permitidas_sessao() -> list[str]:
    """CDs que a sessão atual pode VER mas não é dona (unidades_permitidas
    menos unidades_vinculadas) — cobertura temporária de outra unidade,
    liberada explicitamente pelo admin no cadastro. Usado pra decidir o
    que aparece no seletor de Espelhamento pra quem não é admin/master
    (ver /api/espelho/usuarios e /api/espelho/iniciar em web/server.py)."""
    permitidas = {u.strip().upper() for u in (session.get("unidades_permitidas", "") or "").split(",") if u.strip()}
    vinculadas = {u.strip().upper() for u in (session.get("unidades_vinculadas", "") or "").split(",") if u.strip()}
    return sorted(permitidas - vinculadas)


def conta_canonica_do_cd(cd_sigla: str) -> str:
    """Usuário dono dos dados de verdade daquele CD (cadastrado por um
    admin/master — ver /admin). Vazio se esse CD ainda não tiver conta
    canônica configurada."""
    if not cd_sigla:
        return ""
    from app.database import get_db
    with get_db() as db:
        row = db.execute(
            "SELECT usuario FROM cd_conta_canonica WHERE cd_sigla=?", (cd_sigla.strip().upper(),)
        ).fetchone()
    return row["usuario"] if row else ""


def cds_da_conta_canonica(usuario: str) -> list[str]:
    """Inverso de conta_canonica_do_cd: todas as siglas de CD (empresas, no
    sentido do redesenho Local×Empresa) que apontam pra essa conta canônica
    — ex.: CDA e CDB ambas apontando pro mesmo `usuario` = 2 empresas do
    mesmo Local. Lista vazia = essa conta ainda não tem mapeamento explícito
    (caso normal de todo CD fora do CDA/CDB hoje) — quem chama deve tratar
    isso como "1 empresa só, sem nome de empresa pra mostrar", não como erro."""
    if not usuario:
        return []
    from app.database import get_db
    with get_db() as db:
        rows = db.execute(
            "SELECT cd_sigla FROM cd_conta_canonica WHERE usuario=? ORDER BY cd_sigla", (usuario,)
        ).fetchall()
    return [r["cd_sigla"] for r in rows]


def usuario_efetivo() -> str:
    """Resolve de QUEM são os dados que a sessão atual está vendo/editando
    agora. Ordem: (1) Espelho manual (admin/master observando alguém,
    comportamento já existente); (2) papel com escopo de CD resolve
    automaticamente pra conta canônica do CD cadastrado dele, sem precisar
    de Espelho manual; (3) senão, a própria conta logada. Substitui as
    versões duplicadas em web/armazem.py, web/fifo.py, web/adensamento.py,
    web/server.py (pedido do usuário 24/08/2026, redesenho de papéis) —
    ficavam duplicadas por causa de import circular com server.py; este
    módulo (app/utils) não tem esse problema."""
    espelhando = str(session.get("espelhando", "")).strip().lower()
    if espelhando:
        return espelhando
    if session.get("perfil") in PAPEIS_ESCOPO_CD:
        canonico = conta_canonica_do_cd(cd_do_usuario())
        if canonico:
            return canonico
    return str(session.get("user", "")).strip().lower()


PERFIS_IRRESTRITOS = ("admin", "master")


def espelhando() -> str:
    """Conta que a sessão está espelhando agora, ou "" quando não há espelho."""
    return str(session.get("espelhando") or "")


def pode_gravar_espelhando() -> bool:
    """Gerente/Desenvolvedor pode ATUAR no CD espelhado, não apenas observar.

    Pedido do usuário 01/10/2026: ele é admin, alimenta à mão os dados de um
    CD que não é do Local dele (CD CDD) e precisava deslogar e entrar na
    conta de CDD para carregar arquivo e editar capacidade/contrato. Os
    demais perfis seguem em leitura durante o espelho.

    O bloqueio que existia aqui era proteção redundante: o estrago que ele
    evitava (BUG-9c, gravar o recálculo no estado do OBSERVADOR em vez do
    ALVO) foi corrigido na origem — as rotas resolvem o alvo por
    usuario_efetivo(), ver web/server.py:1373 e :1445.
    """
    return session.get("perfil") in PERFIS_IRRESTRITOS


def somente_leitura() -> bool:
    """True = a sessão atual não pode gravar dados agora. Durante o espelho,
    Gerente/Desenvolvedor grava no CD espelhado (ver pode_gravar_espelhando);
    os outros perfis ficam em leitura. Qualidade é sempre só-leitura.
    Operador é só-leitura de regra geral — as 2 exceções dele (estoque em
    trânsito é só leitura mesmo, e upload no Adensamento) são tratadas pela
    própria rota com somente_leitura_exceto_operador()."""
    if session.get("espelhando") and not pode_gravar_espelhando():
        return True
    return session.get("perfil") in ("qualidade", "operador")


def somente_leitura_exceto_operador() -> bool:
    """Mesma regra de somente_leitura(), mas com passe livre pro Operador —
    usada só na rota de upload do Adensamento (pedido do usuário
    24/08/2026: "o operador pode... subir TXT no módulo adensamento")."""
    if session.get("espelhando") and not pode_gravar_espelhando():
        return True
    return session.get("perfil") == "qualidade"


def bloquear_escrita(f):
    """Decorator pras rotas de escrita normais — 403 se somente_leitura()."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        if somente_leitura():
            return jsonify({"error": "Seu perfil tem acesso somente leitura."}), 403
        return f(*args, **kwargs)
    return wrapped


def bloquear_escrita_exceto_operador(f):
    """Mesma coisa que bloquear_escrita, mas com passe livre pro Operador —
    usada nas rotas de cadastro/config do Adensamento (26/08/2026): esses
    dados são pessoais de cada login (não passam por usuario_efetivo/conta
    canônica — ver decisão registrada no BACKLOG), então o operador editar
    o PRÓPRIO cadastro não expõe nem altera dado de mais ninguém, diferente
    de escrever nos dados compartilhados do CD."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        if somente_leitura_exceto_operador():
            return jsonify({"error": "Seu perfil tem acesso somente leitura."}), 403
        return f(*args, **kwargs)
    return wrapped


def pode_ver_localizacao_exata() -> bool:
    """Operador não pode ver lat/long exata de carretas — achado do usuário
    24/08/2026 ao desenhar a hierarquia de papéis: o botão "🗺 Rota" já
    ficava escondido pra esse perfil na tela, mas os dados de latitude/
    longitude vinham completos mesmo assim em /api/dados e /api/redespacho
    (bastava olhar a resposta da API, sem precisar do botão). Os demais
    perfis (inclusive coordenador/analista/qualidade, quando existirem)
    continuam vendo normalmente.

    Operador de call center (marcação no cadastro) vê a localização em
    qualquer perfil — pedido do usuário 15/11/2026: ele é funcionário da
    própria empresa e acompanha o veículo com o motorista; a restrição vale
    para o Operador de armazém, que é terceirizado."""
    if session.get("operador_call_center"):
        return True
    return session.get("perfil") != "operador"


def modulo_required(nome_modulo: str):
    """Bloqueia a rota se o usuário logado não tiver o módulo liberado.
    Assume que a rota já checa autenticação (própria ou via login_required)."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            # Chamada de API responde JSON mesmo num GET sem corpo: o
            # redirect para "/" devolvia a página inicial em HTML a um fetch,
            # e a tela do Expedito só conseguia dizer "não foi possível ler a
            # operação" (09/10/2026).
            api = request.is_json or request.path.startswith("/api/")
            if not session.get("user"):
                return (jsonify({"error": "Não autenticado"}), 401) \
                       if api else redirect(url_for("login_page"))
            if not tem_acesso_modulo(nome_modulo):
                nome_legivel = MODULOS.get(nome_modulo, nome_modulo)
                if api:
                    return jsonify({"error": f"Sem acesso ao módulo {nome_legivel}."}), 403
                # Antes mandava para "index", mas a raiz também exige
                # Monitoramento — quem não tinha esse módulo entrava em laço
                # de redirecionamento (ERR_TOO_MANY_REDIRECTS). Agora vai para
                # a página inicial permitida dele; a trava evita repetir o
                # mesmo caminho negado.
                destino = rota_inicial()
                if destino == request.path:
                    return redirect(url_for("login_page"))
                return redirect(destino)
            return f(*args, **kwargs)
        return wrapped
    return decorator
