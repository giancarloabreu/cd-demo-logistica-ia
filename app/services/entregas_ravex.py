"""
Monitoramento de Entregas (13/11/2026) — dados da Ravex.

Fonte: dois relatórios salvos na Central de relatórios da Ravex, gerados
pela API em CSV (testado ao vivo em 13/11/2026):
- "THIAGO NF" (IdRelatorio 4609): uma linha por nota fiscal, com viagem,
  placa, cliente, sequência, status da nota, "Homologada distância"
  (Sim = baixa dentro do raio do cliente) e horários. Cada nota vem
  repetida, uma vez por regional da unidade → uma linha por (viagem,
  chave NF-e); a mesma nota em outra viagem (reentrega) é outra linha.
- "itens da nota" (IdRelatorio 4362): produtos de cada nota (a coluna
  "Descrição" traz o Id da viagem).
Os relatórios pertencem à conta da Ravex cadastrada em Configurações >
Ravex; se forem renomeados ou apagados lá, a coleta para com aviso.

Regras do usuário (13/11/2026):
- 1 entrega = 1 cliente na viagem (cliente com 3 notas = 1 bolinha);
- cores: cinza pendente, verde entregue, rosa reentrega, vermelho
  devolução; devolução parcial = duas cores na mesma bolinha. Cada nota
  contribui com a sua cor, então cliente com notas em situações diferentes
  também aparece com mais de uma cor;
- anel branco = baixa fora do raio do cliente; baixa sem GPS conta como
  fora do raio (a Ravex marca "Homologada distância" = Não nos dois casos);
- coleta a cada 5 min, apenas das unidades marcadas nos filtros salvos dos
  usuários; produto devolvido fica fora por enquanto.

Período de cada coleta: hoje, mais os dias das viagens que seguem em
aberto (viagem importada em 11/11 ainda tinha entrega em 13/11), até 6
dias para trás. Unidade nova na coleta busca também os 2 dias anteriores.
"""
from __future__ import annotations

import csv
import io
import re
import time
import unicodedata
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.database import get_db, get_preferencia, set_preferencia

_TZ = ZoneInfo("America/Sao_Paulo")

RELATORIO_NF = {"NomeRelatorio": "THIAGO NF", "IdTipoRelatorio": "1",
                "IdentificadorRelatorio": "NotasFiscais", "IdRelatorio": "4609"}
RELATORIO_ITENS = {"NomeRelatorio": "itens da nota", "IdTipoRelatorio": "11",
                   "IdentificadorRelatorio": "Itens", "IdRelatorio": "4362"}

DIAS_MAX_ATRAS = 6            # viagem em aberto acompanhada por até 6 dias
SEM_FIM_MAX_H = 24            # viagem encerrada sem fim nem baixa: histórico até 24 h depois do início
DIAS_UNIDADE_NOVA = 2         # unidade recém-incluída busca D-2..D
FILTRO_VALIDO_DIAS = 30       # filtro de quem não abre o painel há 30 dias sai da coleta
RETENCAO_DIAS = 180
TENTATIVAS_ITENS = 3
LIMITE_BYTES = 60 * 1024 * 1024
TIMEOUT_RELATORIO = 120
CICLO_CRON_MIN = 5            # o cron da Ravex roda a cada 5 min (scripts/atualizar_ravex.py)
FOLGA_CICLO_SEG = 60          # tolerância do cron: intervalo de 10 min não pode virar 15

PREF_COLETA = "entregas_coleta"
PREF_PARAMETROS = "entregas_parametros"
PREF_PEDIDO_CATALOGO = "entregas_catalogo_pedido"

# Parâmetros da coleta (pedido do usuário 14/11/2026: "não vamos chumbar
# isso no código... não faz sentido atualizar de madrugada, não tem
# entregas... das 06:00 às 20:00"). Editados na tela, pelo gerente.
ESPERA_CATALOGO_APOS_FALHA = 3600      # lista de unidades que falhou: nova tentativa em 1 h
PARAMETROS_PADRAO = {"inicio": "06:00", "fim": "20:00", "intervalo_min": 5, "intervalo_itens_min": 10,
                     "pnp_min": 15, "pnp_raio_m": 500}
# Parada não prevista (pedido do usuário 15/11/2026): minutos parado no mesmo
# lugar, fora do raio dos clientes da viagem, que geram a PNP — 15 para todos
# no início; raio do cliente em metros. Faixas aceitas na tela:
PNP_MIN_FAIXA = (5, 240)
PNP_RAIO_FAIXA = (100, 5000)
# 1, 2 e 3 min (pedido do usuário 15/11/2026): a extensão confere a cada 30 s
# e o relatório de notas leva poucos segundos. Com a coleta pela VM, o cron
# roda a cada 5 min (CICLO_CRON_MIN) e o mínimo efetivo fica 5 min.
INTERVALOS_PERMITIDOS = (1, 2, 3, 5, 10, 15, 20, 30, 60)
INTERVALOS_ITENS_PERMITIDOS = (5, 10, 15, 20, 30, 60, 120)

COLUNAS_NF = {
    "id_viagem": "id da viagem",
    "identificador": "identificador",
    "placa": "placa",
    "eficiencia": "percentual de eficiencia",
    "cidade": "cidade",
    "cliente": "cliente",
    "fim_real": "fim real da viagem",
    "fim_entrega": "fim entrega",
    "importacao": "data de importacao da viagem",
    "inicio_real": "inicio real da viagem",
    "inicio_entrega": "inicio entrega",
    "motorista": "motorista",
    "homologada": "homologada distancia",
    "numero_nf": "numero nf",
    "qtd_entregas": "quantidade de entregas",
    "qtd_reentregas": "quantidade reentregas",
    "notas_previstas": "notas previstas",
    "qtd_entregas_realizadas": "quantidade de entregas realizadas",
    "seq_programada": "sequencia programada",
    "seq_real": "sequencia real",
    "cod_cliente": "codigo do cliente",
    "lat_ref": "latitude referencia",
    "lon_ref": "longitude referencia",
    "metros": "metros da entrega",
    "status_nf": "status nf",
    "peso_bruto": "peso bruto (nf)",
    "valor_nf": "valor nf",
    "endereco": "endereco da referencia",
    "bairro": "bairro",
    "unidade": "unidade",
    "qtd_notas_entregues": "quantidade de notas entregues",
    "transportadora": "transportadora",
    "status_viagem": "status viagem",
    "chave": "chave da nfe",
    "primeira_pos_raio": "data hora primeira posicao dentro do raio",
    "ultima_pos_raio": "data hora ultima posicao dentro do raio",
    "lat_ent": "latitude entrega",
    "lon_ent": "longitude entrega",
    "data_estimada": "data estimada de entrega",
}
OBRIGATORIAS_NF = ("id_viagem", "numero_nf", "status_nf", "cod_cliente", "unidade", "status_viagem")

COLUNAS_ITENS = {
    "id_viagem": "descricao",
    "numero_nf": "numero nf",
    "codigo": "codigo item",
    "descricao": "descricao item",
    "quantidade": "quantidade",
    "peso_liquido": "peso total liquido",
    "unidade": "unidade",
}
OBRIGATORIAS_ITENS = ("id_viagem", "numero_nf", "codigo", "descricao")

# categoria de cada nota → cor da bolinha (ordem = ordem das fatias)
ORDEM_CORES = ("ok", "reent", "dev", "pend")


# ── utilitários ──────────────────────────────────────────────────────
def _agora() -> datetime:
    return datetime.now(_TZ).replace(tzinfo=None)


def _agora_txt() -> str:
    return _agora().strftime("%Y-%m-%d %H:%M:%S")


def _hoje() -> date:
    return _agora().date()


def _norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", s.replace('"', "").strip().lower())


def _data_hora(s: str) -> str:
    """'13/11/2026 05:21' → '2026-11-13 05:21'; vazio/inválido → ''."""
    s = (s or "").strip()
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})(?:\s+(\d{2}):(\d{2})(?::(\d{2}))?)?", s)
    if not m:
        return ""
    d, mo, a, h, mi, se = m.groups()
    if h is None:
        return f"{a}-{mo}-{d}"
    return f"{a}-{mo}-{d} {h}:{mi}" + (f":{se}" if se else "")


def _num(s) -> float | None:
    s = str(s or "").strip().replace(" ", "")
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _int(s) -> int | None:
    v = _num(s)
    return int(v) if v is not None else None


def _categoria_base(status_nf: str) -> str:
    n = _norm(status_nf)
    if n.startswith("entregue"):
        return "ok"
    if n.startswith("reentrega"):
        return "reent"
    if "devolucao parcial" in n:
        return "parc"
    if "devolucao" in n:
        return "dev"
    return "pend"            # A entregar, Entregando, Anomalias, vazio


def categoria_status(status_nf: str, qtd_reentregas=None) -> str:
    """Categoria da nota para a cor. Nota de volta a "A entregar" depois de
    uma reentrega (achado nos dados de 13/11: "A entregar" com Quantidade
    reentregas = 1) fica rosa: já houve tentativa sem sucesso."""
    base = _categoria_base(status_nf)
    if base == "pend" and (qtd_reentregas or 0) > 0:
        return "reent"
    return base


def teve_baixa(status_nf: str) -> bool:
    """A nota recebeu baixa (entregue, reentrega ou devolução). O anel de
    fora do raio vale apenas para nota com baixa."""
    return _categoria_base(status_nf) != "pend"


def viagem_aberta(status_viagem: str) -> bool:
    n = _norm(status_viagem)
    return not ("finaliz" in n or "cancel" in n)


def sigla_unidade(nome: str) -> str:
    n = (nome or "").strip()
    return n[len("Marca "):].strip() if n.lower().startswith("marca ") else n


# ── banco ────────────────────────────────────────────────────────────
_COLS_NFS = ("chave, id_viagem, numero_nf, cod_cliente, cliente, cidade, bairro, endereco, seq_programada, "
             "seq_real, status_nf, homologada, metros, qtd_reentregas, inicio_entrega, fim_entrega, data_estimada, "
             "primeira_pos_raio, ultima_pos_raio, peso_bruto, valor_nf, lat_ref, lon_ref, lat_ent, lon_ent, "
             "itens_em, itens_tentativas, itens_qtd, atualizado_em")


def garantir_tabelas(db) -> None:
    # A nota é identificada por (viagem, chave NF-e): a mesma NF volta em
    # outra viagem depois de uma reentrega, e as duas viagens precisam dela
    # (revisão de 13/11/2026). Banco criado antes disso, com a chave sozinha
    # como identificador, é convertido aqui.
    cols = db.execute("PRAGMA table_info(entregas_nfs)").fetchall()
    pk_antiga = [c["name"] for c in sorted((c for c in cols if c["pk"]), key=lambda c: c["pk"])] == ["chave"]
    if pk_antiga:
        db.executescript("""
            ALTER TABLE entregas_nfs RENAME TO entregas_nfs_antiga;
            DROP INDEX IF EXISTS ix_entregas_nfs_viagem;
            DROP INDEX IF EXISTS ix_entregas_nfs_numero;
        """)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS entregas_unidades (
            id            INTEGER PRIMARY KEY,
            nome          TEXT NOT NULL,
            sigla         TEXT NOT NULL DEFAULT '',
            eh_cd         INTEGER NOT NULL DEFAULT 1,
            atualizado_em TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS entregas_filtro_usuario (
            usuario       TEXT PRIMARY KEY,
            unidades      TEXT NOT NULL DEFAULT '',
            atualizado_em TEXT NOT NULL DEFAULT '',
            ultimo_acesso TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS entregas_unidade_coleta (
            unidade_id INTEGER PRIMARY KEY,
            desde      TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS entregas_viagens (
            id_viagem       TEXT PRIMARY KEY,
            identificador   TEXT NOT NULL DEFAULT '',
            unidade_id      INTEGER,
            unidade         TEXT NOT NULL DEFAULT '',
            placa           TEXT NOT NULL DEFAULT '',
            motorista       TEXT NOT NULL DEFAULT '',
            transportadora  TEXT NOT NULL DEFAULT '',
            status_viagem   TEXT NOT NULL DEFAULT '',
            aberta          INTEGER NOT NULL DEFAULT 1,
            importacao      TEXT NOT NULL DEFAULT '',
            inicio_real     TEXT NOT NULL DEFAULT '',
            fim_real        TEXT NOT NULL DEFAULT '',
            dia             TEXT NOT NULL DEFAULT '',
            fim_dia         TEXT NOT NULL DEFAULT '',
            dia_coleta      TEXT NOT NULL DEFAULT '',
            qtd_entregas    INTEGER,
            qtd_entregas_realizadas INTEGER,
            notas_previstas INTEGER,
            qtd_notas_entregues INTEGER,
            eficiencia      REAL,
            ausente_desde   TEXT NOT NULL DEFAULT '',
            atualizado_em   TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS ix_entregas_viagens_dia ON entregas_viagens(unidade_id, dia);
        CREATE INDEX IF NOT EXISTS ix_entregas_viagens_coleta ON entregas_viagens(unidade_id, dia_coleta);
        CREATE TABLE IF NOT EXISTS entregas_nfs (
            chave            TEXT NOT NULL,
            id_viagem        TEXT NOT NULL,
            numero_nf        TEXT NOT NULL DEFAULT '',
            cod_cliente      TEXT NOT NULL DEFAULT '',
            cliente          TEXT NOT NULL DEFAULT '',
            cidade           TEXT NOT NULL DEFAULT '',
            bairro           TEXT NOT NULL DEFAULT '',
            endereco         TEXT NOT NULL DEFAULT '',
            seq_programada   INTEGER,
            seq_real         INTEGER,
            status_nf        TEXT NOT NULL DEFAULT '',
            homologada       TEXT NOT NULL DEFAULT '',
            metros           REAL,
            qtd_reentregas   INTEGER,
            inicio_entrega   TEXT NOT NULL DEFAULT '',
            fim_entrega      TEXT NOT NULL DEFAULT '',
            data_estimada    TEXT NOT NULL DEFAULT '',
            primeira_pos_raio TEXT NOT NULL DEFAULT '',
            ultima_pos_raio  TEXT NOT NULL DEFAULT '',
            peso_bruto       REAL,
            valor_nf         REAL,
            lat_ref REAL, lon_ref REAL, lat_ent REAL, lon_ent REAL,
            itens_em         TEXT NOT NULL DEFAULT '',
            itens_tentativas INTEGER NOT NULL DEFAULT 0,
            itens_qtd        INTEGER NOT NULL DEFAULT 0,
            atualizado_em    TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (id_viagem, chave)
        );
        CREATE INDEX IF NOT EXISTS ix_entregas_nfs_chave ON entregas_nfs(chave);
        CREATE INDEX IF NOT EXISTS ix_entregas_nfs_numero ON entregas_nfs(numero_nf);
        CREATE TABLE IF NOT EXISTS entregas_itens (
            id_viagem    TEXT NOT NULL,
            numero_nf    TEXT NOT NULL,
            codigo       TEXT NOT NULL DEFAULT '',
            descricao    TEXT NOT NULL DEFAULT '',
            quantidade   REAL,
            peso_liquido REAL
        );
        CREATE INDEX IF NOT EXISTS ix_entregas_itens_nf ON entregas_itens(id_viagem, numero_nf);
    """)
    if pk_antiga:
        db.executescript(f"""
            INSERT OR IGNORE INTO entregas_nfs({_COLS_NFS}) SELECT {_COLS_NFS} FROM entregas_nfs_antiga;
            DROP TABLE entregas_nfs_antiga;
        """)
    # filtro de veículos de cada login (pedido do usuário 15/11/2026)
    cols_f = {r["name"] for r in db.execute("PRAGMA table_info(entregas_filtro_usuario)")}
    if "placas" not in cols_f:
        db.execute("ALTER TABLE entregas_filtro_usuario ADD COLUMN placas TEXT NOT NULL DEFAULT ''")
    # efetividade de temperatura da viagem, replicada da Ravex (Viagens ›
    # Grid Operacional, coluna "Efet. Temp" — pedido do usuário 16/11/2026)
    cols_v = {r["name"] for r in db.execute("PRAGMA table_info(entregas_viagens)")}
    if "efet_temp" not in cols_v:
        db.execute("ALTER TABLE entregas_viagens ADD COLUMN efet_temp REAL")
    if "efet_temp_em" not in cols_v:
        db.execute("ALTER TABLE entregas_viagens ADD COLUMN efet_temp_em TEXT NOT NULL DEFAULT ''")
    # situação da Ravex (dentro/fora da margem, viagem não rastreada), a
    # margem (70%) e a faixa de temperatura da viagem (TempMin/TempMax)
    for col, tipo in (("efet_situacao", "TEXT NOT NULL DEFAULT ''"), ("efet_margem", "REAL"),
                      ("temp_min", "REAL"), ("temp_max", "REAL")):
        if col not in cols_v:
            db.execute(f"ALTER TABLE entregas_viagens ADD COLUMN {col} {tipo}")


# ── catálogo de unidades e filtro de cada usuário ────────────────────
def listar_unidades(db, somente_cd: bool = True) -> list[dict]:
    sql = "SELECT id, nome, sigla FROM entregas_unidades"
    if somente_cd:
        sql += " WHERE eh_cd=1"
    rows = db.execute(sql + " ORDER BY nome").fetchall()
    return [{"id": r["id"], "nome": r["nome"], "sigla": r["sigla"]} for r in rows]


def gravar_catalogo(db, itens: list[dict]) -> int:
    agora = _agora_txt()
    n = 0
    for u in itens:
        try:
            uid = int(u.get("Id"))
        except (TypeError, ValueError):
            continue
        nome = str(u.get("Nome") or "").strip()
        if not nome:
            continue
        eh_cd = 0 if (u.get("EhTransportadora") or u.get("EhCooperativa")) else 1
        db.execute("""INSERT INTO entregas_unidades(id, nome, sigla, eh_cd, atualizado_em) VALUES(?,?,?,?,?)
                      ON CONFLICT(id) DO UPDATE SET nome=excluded.nome, sigla=excluded.sigla,
                      eh_cd=excluded.eh_cd, atualizado_em=excluded.atualizado_em""",
                   (uid, nome, sigla_unidade(nome), eh_cd, agora))
        n += 1
    return n


def _ids_csv(texto: str) -> list[int]:
    out = []
    for p in (texto or "").split(","):
        p = p.strip()
        if p.isdigit() and int(p) not in out:
            out.append(int(p))
    return out


def filtro_do_usuario(db, usuario: str) -> list[int] | None:
    """Unidades salvas do usuário; None = nunca salvou."""
    row = db.execute("SELECT unidades FROM entregas_filtro_usuario WHERE usuario=?", (usuario,)).fetchone()
    return _ids_csv(row["unidades"]) if row else None


def salvar_filtro(db, usuario: str, ids: list[int]) -> list[int]:
    validos = {u["id"] for u in listar_unidades(db)}
    ids = [i for i in dict.fromkeys(ids) if i in validos]
    agora = _agora_txt()
    db.execute("""INSERT INTO entregas_filtro_usuario(usuario, unidades, atualizado_em, ultimo_acesso)
                  VALUES(?,?,?,?) ON CONFLICT(usuario) DO UPDATE SET unidades=excluded.unidades,
                  atualizado_em=excluded.atualizado_em, ultimo_acesso=excluded.ultimo_acesso""",
               (usuario, ",".join(str(i) for i in ids), agora, agora))
    return ids


MAX_PLACAS_FILTRO = 300


def _placa(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())[:10]


def placas_do_usuario(db, usuario: str) -> list[str]:
    """Veículos marcados no filtro do login (vazio = todos)."""
    row = db.execute("SELECT placas FROM entregas_filtro_usuario WHERE usuario=?", (usuario,)).fetchone()
    if not row:
        return []
    return [p for p in dict.fromkeys(_placa(x) for x in (row["placas"] or "").split(",")) if p]


def salvar_placas(db, usuario: str, placas: list) -> list[str]:
    """Filtro de veículos do login (pedido do usuário 15/11/2026: a pessoa
    marca os veículos que acompanha e o sistema lembra a escolha). Muda a
    tela, nunca a coleta: a Ravex segue consultada pela unidade inteira."""
    lista = [p for p in dict.fromkeys(_placa(x) for x in placas) if p][:MAX_PLACAS_FILTRO]
    # só atualiza a linha que já existe: criar a linha aqui (unidades vazias)
    # apagaria a "primeira abertura" do filtro de unidades (revisão 15/11).
    # A linha nasce na primeira abertura do painel, antes deste filtro.
    cur = db.execute("UPDATE entregas_filtro_usuario SET placas=? WHERE usuario=?", (",".join(lista), usuario))
    return lista if cur.rowcount else []


def registrar_acesso(db, usuario: str, minimo_seg: int = 0) -> None:
    """Renova o "último acesso" (o filtro só entra na coleta se o painel foi
    usado nos últimos 30 dias). minimo_seg > 0: grava só se o registro
    anterior for mais antigo que isso — usado pela atualização automática
    da tela, para um painel deixado aberto continuar na coleta."""
    if minimo_seg:
        limite = (_agora() - timedelta(seconds=minimo_seg)).strftime("%Y-%m-%d %H:%M:%S")
        db.execute("UPDATE entregas_filtro_usuario SET ultimo_acesso=? WHERE usuario=? AND ultimo_acesso<?",
                   (_agora_txt(), usuario, limite))
    else:
        db.execute("UPDATE entregas_filtro_usuario SET ultimo_acesso=? WHERE usuario=?", (_agora_txt(), usuario))


def unidades_padrao(db, siglas_cd: list[str]) -> list[int]:
    """Primeira abertura do painel: as unidades da Ravex com a mesma sigla
    dos CDs do usuário (CDA → Marca CDA)."""
    alvo = {s.strip().upper() for s in siglas_cd if s and s.strip()}
    return [u["id"] for u in listar_unidades(db) if u["sigla"].upper() in alvo]


def unidades_para_coletar(db) -> list[int]:
    limite = (_agora() - timedelta(days=FILTRO_VALIDO_DIAS)).strftime("%Y-%m-%d %H:%M:%S")
    ids: list[int] = []
    for r in db.execute("SELECT unidades FROM entregas_filtro_usuario WHERE ultimo_acesso>=?", (limite,)):
        ids.extend(_ids_csv(r["unidades"]))
    validos = {u["id"] for u in listar_unidades(db)}
    return sorted({i for i in ids if i in validos})


def unidades_coletadas(db) -> dict[int, str]:
    return {r["unidade_id"]: r["desde"] for r in db.execute("SELECT unidade_id, desde FROM entregas_unidade_coleta")}


# ── leitura do CSV ───────────────────────────────────────────────────
class FormatoInesperado(Exception):
    pass


def ler_csv(texto: str, colunas: dict, obrigatorias: tuple) -> list[dict]:
    """Lê o CSV pelo cabeçalho (nunca por posição): devolve uma lista de
    dicionários com as chaves de `colunas`."""
    texto = texto.lstrip("﻿")
    leitor = csv.reader(io.StringIO(texto), delimiter=";")
    try:
        cab = next(leitor)
    except StopIteration:
        raise FormatoInesperado("Relatório veio vazio, sem cabeçalho.")
    pos = {_norm(c): i for i, c in enumerate(cab)}
    idx = {k: pos.get(v) for k, v in colunas.items()}
    faltando = [colunas[k] for k in obrigatorias if idx.get(k) is None]
    if faltando:
        raise FormatoInesperado("Colunas ausentes no relatório: " + ", ".join(faltando))
    out = []
    for linha in leitor:
        if not linha or not any(c.strip() for c in linha):
            continue
        out.append({k: (linha[i].strip() if i is not None and i < len(linha) else "") for k, i in idx.items()})
    return out


def _chave_nf(r: dict) -> str:
    ch = re.sub(r"\D", "", r.get("chave") or "")
    return ch if len(ch) >= 20 else f"{r['id_viagem']}|{r['numero_nf']}"


def _dia_de(*valores: str) -> str:
    dias = [v[:10] for v in valores if v]
    return min(dias) if dias else ""


# ── gravação ─────────────────────────────────────────────────────────
def gravar_notas(db, linhas: list[dict], unidades_pedidas: list[int], inicio: str, fim: str) -> dict:
    """Grava viagens e notas do relatório. Viagens que sumiram do relatório
    (canceladas/excluídas na Ravex) ficam marcadas como ausentes, nunca
    apagadas; notas que saíram de uma viagem presente são removidas."""
    agora = _agora_txt()
    mapa_unid = {_norm(r["nome"]): r["id"] for r in db.execute("SELECT id, nome FROM entregas_unidades")}
    notas: dict[tuple[str, str], dict] = {}
    for r in linhas:
        if not r["id_viagem"] or not r["numero_nf"]:
            continue
        # dedup: uma linha por (viagem, chave NF-e) — a nota vem repetida
        # por regional; a mesma nota em outra viagem (reentrega) é outra linha
        notas[(r["id_viagem"], _chave_nf(r))] = r
    viagens: dict[str, dict] = {}
    for r in notas.values():
        viagens[r["id_viagem"]] = r
    linhas_por_unidade: dict[int, int] = {}

    for vid, r in viagens.items():
        imp, ini, fim_r = _data_hora(r["importacao"]), _data_hora(r["inicio_real"]), _data_hora(r["fim_real"])
        uid = mapa_unid.get(_norm(r["unidade"]))
        if uid is not None:
            linhas_por_unidade[uid] = linhas_por_unidade.get(uid, 0) + 1
        db.execute("""INSERT INTO entregas_viagens(id_viagem, identificador, unidade_id, unidade, placa, motorista,
                        transportadora, status_viagem, aberta, importacao, inicio_real, fim_real, dia, fim_dia,
                        dia_coleta, qtd_entregas, qtd_entregas_realizadas, notas_previstas, qtd_notas_entregues,
                        eficiencia, ausente_desde, atualizado_em)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'',?)
                      ON CONFLICT(id_viagem) DO UPDATE SET identificador=excluded.identificador,
                        unidade_id=excluded.unidade_id, unidade=excluded.unidade, placa=excluded.placa,
                        motorista=excluded.motorista, transportadora=excluded.transportadora,
                        status_viagem=excluded.status_viagem, aberta=excluded.aberta,
                        importacao=excluded.importacao, inicio_real=excluded.inicio_real,
                        fim_real=excluded.fim_real, dia=excluded.dia, fim_dia=excluded.fim_dia,
                        dia_coleta=excluded.dia_coleta, qtd_entregas=excluded.qtd_entregas,
                        qtd_entregas_realizadas=excluded.qtd_entregas_realizadas,
                        notas_previstas=excluded.notas_previstas,
                        qtd_notas_entregues=excluded.qtd_notas_entregues, eficiencia=excluded.eficiencia,
                        ausente_desde='', atualizado_em=excluded.atualizado_em""",
                   (vid, r["identificador"], uid, r["unidade"], r["placa"].upper(), r["motorista"],
                    r["transportadora"], r["status_viagem"], 1 if viagem_aberta(r["status_viagem"]) else 0,
                    imp, ini, fim_r, (ini or imp)[:10], fim_r[:10], _dia_de(imp, ini),
                    _int(r["qtd_entregas"]), _int(r["qtd_entregas_realizadas"]), _int(r["notas_previstas"]),
                    _int(r["qtd_notas_entregues"]), _num(r["eficiencia"]), agora))

    for (_vid, ch), r in notas.items():
        db.execute("""INSERT INTO entregas_nfs(chave, id_viagem, numero_nf, cod_cliente, cliente, cidade, bairro,
                        endereco, seq_programada, seq_real, status_nf, homologada, metros, qtd_reentregas,
                        inicio_entrega, fim_entrega, data_estimada, primeira_pos_raio, ultima_pos_raio,
                        peso_bruto, valor_nf, lat_ref, lon_ref, lat_ent, lon_ent, atualizado_em)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(id_viagem, chave) DO UPDATE SET numero_nf=excluded.numero_nf,
                        cod_cliente=excluded.cod_cliente, cliente=excluded.cliente, cidade=excluded.cidade,
                        bairro=excluded.bairro, endereco=excluded.endereco, seq_programada=excluded.seq_programada,
                        seq_real=excluded.seq_real, status_nf=excluded.status_nf, homologada=excluded.homologada,
                        metros=excluded.metros, qtd_reentregas=excluded.qtd_reentregas,
                        inicio_entrega=excluded.inicio_entrega, fim_entrega=excluded.fim_entrega,
                        data_estimada=excluded.data_estimada, primeira_pos_raio=excluded.primeira_pos_raio,
                        ultima_pos_raio=excluded.ultima_pos_raio, peso_bruto=excluded.peso_bruto,
                        valor_nf=excluded.valor_nf, lat_ref=excluded.lat_ref, lon_ref=excluded.lon_ref,
                        lat_ent=excluded.lat_ent, lon_ent=excluded.lon_ent, atualizado_em=excluded.atualizado_em""",
                   (ch, r["id_viagem"], r["numero_nf"], r["cod_cliente"], r["cliente"], r["cidade"], r["bairro"],
                    r["endereco"], _int(r["seq_programada"]), _int(r["seq_real"]), r["status_nf"], r["homologada"],
                    _num(r["metros"]), _int(r["qtd_reentregas"]), _data_hora(r["inicio_entrega"]),
                    _data_hora(r["fim_entrega"]), _data_hora(r["data_estimada"]),
                    _data_hora(r["primeira_pos_raio"]), _data_hora(r["ultima_pos_raio"]),
                    _num(r["peso_bruto"]), _num(r["valor_nf"]), _num(r["lat_ref"]), _num(r["lon_ref"]),
                    _num(r["lat_ent"]), _num(r["lon_ent"]), agora))

    # notas que saíram de uma viagem presente no relatório
    removidas = 0
    for vid in viagens:
        chaves = [ch for (v, ch) in notas if v == vid]
        marc = ",".join("?" * len(chaves))
        cur = db.execute(f"DELETE FROM entregas_nfs WHERE id_viagem=? AND chave NOT IN ({marc})", (vid, *chaves))
        removidas += cur.rowcount or 0
    if removidas:
        db.execute("""DELETE FROM entregas_itens WHERE NOT EXISTS (SELECT 1 FROM entregas_nfs n
                      WHERE n.id_viagem=entregas_itens.id_viagem AND n.numero_nf=entregas_itens.numero_nf)""")

    # viagens que sumiram do relatório no período pedido. Proteção: unidade
    # sem nenhuma linha na resposta não marca nada (resposta vazia por
    # falha da Ravex não pode esconder o dia inteiro).
    ausentes = 0
    for uid in unidades_pedidas:
        if not linhas_por_unidade.get(uid):
            continue
        rows = db.execute("""SELECT id_viagem FROM entregas_viagens WHERE unidade_id=? AND dia_coleta BETWEEN ? AND ?
                             AND ausente_desde=''""", (uid, inicio, fim)).fetchall()
        for row in rows:
            if row["id_viagem"] not in viagens:
                db.execute("UPDATE entregas_viagens SET ausente_desde=? WHERE id_viagem=?", (agora, row["id_viagem"]))
                ausentes += 1
    return {"viagens": len(viagens), "notas": len(notas), "notas_removidas": removidas,
            "viagens_ausentes": ausentes}


def gravar_itens(db, linhas: list[dict], unidades_pedidas: list[int], inicio: str, fim: str) -> dict:
    agora = _agora_txt()
    por_nf: dict[tuple, list[dict]] = {}
    for r in linhas:
        if r["id_viagem"] and r["numero_nf"]:
            por_nf.setdefault((r["id_viagem"], r["numero_nf"]), []).append(r)
    for (vid, nf), itens in por_nf.items():
        db.execute("DELETE FROM entregas_itens WHERE id_viagem=? AND numero_nf=?", (vid, nf))
        db.executemany("INSERT INTO entregas_itens(id_viagem, numero_nf, codigo, descricao, quantidade, peso_liquido) "
                       "VALUES(?,?,?,?,?,?)",
                       [(vid, nf, i["codigo"], i["descricao"], _num(i["quantidade"]), _num(i["peso_liquido"]))
                        for i in itens])
    # marca a tentativa nas notas do período/unidades pedidos
    marc = ",".join("?" * len(unidades_pedidas))
    alvo = db.execute(f"""SELECT n.chave, n.id_viagem, n.numero_nf FROM entregas_nfs n
                          JOIN entregas_viagens v ON v.id_viagem=n.id_viagem
                          WHERE v.unidade_id IN ({marc}) AND v.dia_coleta BETWEEN ? AND ? AND n.itens_qtd=0""",
                      (*unidades_pedidas, inicio, fim)).fetchall()
    for r in alvo:
        qtd = len(por_nf.get((r["id_viagem"], r["numero_nf"]), []))
        db.execute("UPDATE entregas_nfs SET itens_em=?, itens_tentativas=itens_tentativas+1, itens_qtd=? "
                   "WHERE id_viagem=? AND chave=?", (agora, qtd, r["id_viagem"], r["chave"]))
    return {"notas_com_itens": len(por_nf), "itens": sum(len(v) for v in por_nf.values())}


# ── eficiência do frio (Ravex › Viagens › Grid Operacional) ──────────
# Pedido do usuário 16/11/2026: replicar a coluna "Efet. Temp." da Ravex,
# sem calcular aqui. A Ravex manda a fração (0,989 = 98,9%), a margem
# ("70.00") e a situação: dentro/fora da margem ou "Viagem não rastreada"
# — esta vem com 0% e ícone cinza na Ravex, e aqui fica sem percentual.
_COR_EFICIENCIA = {"#6FE384": "dentro", "#FF0000": "fora", "#7F7A7A": "nao_rastreada"}


def situacao_eficiencia(status, cor) -> str:
    import html
    n = _norm(html.unescape(str(status or "")))
    if "nao rastread" in n:
        return "nao_rastreada"
    if "fora da margem" in n:
        return "fora"
    if "dentro da margem" in n:
        return "dentro"
    return _COR_EFICIENCIA.get(str(cor or "").strip().upper(), "")


def _num_ravex(x) -> float | None:
    """Número da lista da Ravex: vem como número (0.989, -25) ou texto
    ("70.00"); NaN e infinito ficam de fora."""
    if isinstance(x, bool):
        return None
    v = float(x) if isinstance(x, (int, float)) else _num(x)
    return v if v is not None and v == v and abs(v) != float("inf") else None


def _id_ravex(bruto) -> str:
    """Id da viagem na lista da Ravex (número ou texto) → texto; '' se inválido."""
    if isinstance(bruto, bool):
        return ""
    if isinstance(bruto, float):
        return str(int(bruto)) if bruto == bruto and abs(bruto) != float("inf") and bruto == int(bruto) else ""
    if isinstance(bruto, int):
        return str(bruto)
    return str(bruto or "").strip()[:40]


def gravar_eficiencia(db, linhas: list[dict], unidades: list[int]) -> int:
    """Grava a eficiência do frio das viagens das unidades pedidas. Liga a
    linha da Ravex à viagem pelo Id (o mesmo "Id da viagem" do relatório de
    notas). Identificador + placa vale somente quando nenhum Id da lista é
    conhecido no banco (numeração diferente entre a Grid e o relatório) e
    o par é único, no banco e na lista — revisão de 16/11/2026: linha de
    outra viagem com o mesmo identificador e a mesma placa gravava o
    percentual dela na viagem errada. Viagem de outra unidade fica como está."""
    linhas = [r for r in linhas if isinstance(r, dict)] if unidades else []
    if not linhas:
        return 0
    marc = ",".join("?" * len(unidades))
    limite = (_hoje() - timedelta(days=DIAS_MAX_ATRAS + 1)).isoformat()
    alvo: set[str] = set()
    por_ident: dict[tuple, str | None] = {}
    for r in db.execute(f"SELECT id_viagem, identificador, placa FROM entregas_viagens "
                        f"WHERE unidade_id IN ({marc}) AND dia_coleta>=?", (*unidades, limite)):
        alvo.add(r["id_viagem"])
        k = (_norm(r["identificador"]), _placa(r["placa"]))
        if k[0] and k[1]:
            # identificador repetido na mesma placa: ambíguo, não usa
            por_ident[k] = None if k in por_ident else r["id_viagem"]
    ids = [_id_ravex(r.get("Id")) for r in linhas]
    # algum Id da lista existe no banco (qualquer unidade)? então a numeração
    # é a mesma do relatório e o casamento é somente pelo Id
    unicos = sorted({i for i in ids if i})
    conhecido = False
    for i in range(0, len(unicos), 400):
        lote = unicos[i:i + 400]
        if db.execute(f"SELECT 1 FROM entregas_viagens WHERE id_viagem IN ({','.join('?' * len(lote))}) LIMIT 1",
                      lote).fetchone():
            conhecido = True
            break
    escolhidas: list[tuple[str, dict]] = []
    vistos: set[str] = set()
    for vid, r in zip(ids, linhas):
        if vid in alvo and vid not in vistos:
            escolhidas.append((vid, r))
            vistos.add(vid)
    if not conhecido:
        chaves: dict[tuple, int] = {}
        for r in linhas:
            k = (_norm(r.get("IdentificadorViagem")), _placa(r.get("Placa")))
            chaves[k] = chaves.get(k, 0) + 1
        for r in linhas:
            k = (_norm(r.get("IdentificadorViagem")), _placa(r.get("Placa")))
            vid = por_ident.get(k) if chaves.get(k) == 1 else None
            if vid and vid not in vistos:
                escolhidas.append((vid, r))
                vistos.add(vid)
    agora = _agora_txt()
    gravadas: set[str] = set()
    for vid, r in escolhidas:
        sit = situacao_eficiencia(r.get("StatusEficienciaTemperatura"), r.get("CorEficienciaTemperatura"))
        frac = _num_ravex(r.get("PercentEficienciaTemperatura"))
        if sit == "nao_rastreada":
            pct = None
        elif frac is None or not (0 <= frac <= 1.0001):
            continue
        else:
            pct = round(min(frac, 1.0) * 100, 2)
        margem = _num_ravex(r.get("ParametroEficienciaTemperatura"))
        tmin, tmax = (t if t is not None and -80 <= t <= 80 else None
                      for t in (_num_ravex(r.get("TempMin")), _num_ravex(r.get("TempMax"))))
        if tmin is not None and tmax is not None and tmin > tmax:
            tmin = tmax = None
        db.execute("""UPDATE entregas_viagens SET efet_temp=?, efet_temp_em=?, efet_situacao=?, efet_margem=?,
                        temp_min=?, temp_max=? WHERE id_viagem=?""",
                   (pct, agora, sit, margem if margem is not None and 0 <= margem <= 100 else None,
                    tmin, tmax, vid))
        gravadas.add(vid)
    return len(gravadas)


def limpar_antigos(db) -> int:
    """Retenção de 180 dias (6 meses) pela data da viagem, aberta ou não:
    viagem tão antiga já saiu da janela de coleta (6 dias) e não muda mais —
    inclusive a que ficou "aberta" por ter sumido da Ravex. Antes de sair do
    banco, a viagem com as notas e os itens vai para o arquivo mensal
    compactado (data/entregas/arquivo/entregas_AAAA-MM.jsonl.gz), guardado
    por mais 18 meses (pedido do usuário 16/11/2026). O histórico de
    posições segue a mesma regra, no banco próprio (entregas_pnp)."""
    from app.services import entregas_pnp
    corte = (_hoje() - timedelta(days=RETENCAO_DIAS)).isoformat()
    velhas = [dict(r) for r in db.execute(
        """SELECT * FROM entregas_viagens
           WHERE (dia<>'' AND dia<?) OR (dia='' AND dia_coleta<>'' AND dia_coleta<?)""", (corte, corte))]
    por_mes: dict[str, list[dict]] = {}
    for v in velhas:
        vid = v["id_viagem"]
        por_mes.setdefault((v["dia"] or v["dia_coleta"])[:7], []).append({
            "viagem": v,
            "nfs": [dict(n) for n in db.execute("SELECT * FROM entregas_nfs WHERE id_viagem=?", (vid,))],
            "itens": [dict(i) for i in db.execute("SELECT * FROM entregas_itens WHERE id_viagem=?", (vid,))],
        })
    # arquivo gravado antes de apagar: uma falha no meio repete a viagem no
    # arquivo, sem perder nenhuma
    entregas_pnp.arquivar("entregas", por_mes)
    for v in velhas:
        vid = v["id_viagem"]
        db.execute("DELETE FROM entregas_itens WHERE id_viagem=?", (vid,))
        db.execute("DELETE FROM entregas_nfs WHERE id_viagem=?", (vid,))
        db.execute("DELETE FROM entregas_viagens WHERE id_viagem=?", (vid,))
    agora = _agora()
    # falha no histórico de posições (outro banco) não desfaz a exclusão das
    # viagens já arquivadas: senão o cron gravaria as mesmas viagens no
    # arquivo a cada ciclo (revisão de 16/11/2026); nova tentativa no dia seguinte
    try:
        entregas_pnp.limpar_historico(agora)
        entregas_pnp.podar_arquivo(agora)
    except Exception as e:
        print(f"[Entregas] Histórico de posições: limpeza com erro ({type(e).__name__}: {str(e)[:200]}).")
    return len(velhas)


# ── coleta (cron) ────────────────────────────────────────────────────
def _baixar_csv(rel: dict, unidades: list[int], inicio: str, fim: str, usuario: str, senha: str) -> str:
    from app.services import ravex_sessao as rs
    dados = {"Modulo": "Relatorios", "Objeto": "GerarRelatorio", "Acao": "GerarRelatorio",
             "ExportarDireto": "true", "ExportarPdf": "false", "ExportarCsv": "true",
             "DataInicial": f"{inicio} 00:00:00", "DataFinal": f"{fim} 23:59:59",
             "IncluirCabecalhoNasQuebras": "false", "Unidade": ",".join(str(u) for u in unidades), **rel}
    r = rs.requisitar(dados, usuario, senha, timeout=TIMEOUT_RELATORIO)
    ct = (r.headers.get("content-type") or "").lower()
    if "csv" not in ct:
        try:
            msg = str(r.json().get("mensagem") or "")[:200]
        except ValueError:
            msg = ""
        raise rs.ErroRavex(f"O relatório \"{rel['NomeRelatorio']}\" não veio em CSV"
                           + (f": {msg}" if msg else " (renomeado ou excluído na Ravex?)."))
    if len(r.content) > LIMITE_BYTES:
        raise rs.ErroRavex(f"Relatório \"{rel['NomeRelatorio']}\" acima de {LIMITE_BYTES // 1048576} MB.")
    return r.content.decode("utf-8-sig", errors="replace")


def _periodo_notas(db, unidades: list[int]) -> tuple[str, str]:
    hoje = _hoje()
    inicio = hoje
    limite = (hoje - timedelta(days=DIAS_MAX_ATRAS)).isoformat()
    marc = ",".join("?" * len(unidades))
    row = db.execute(f"""SELECT MIN(dia_coleta) AS d FROM entregas_viagens WHERE aberta=1 AND ausente_desde=''
                         AND unidade_id IN ({marc}) AND dia_coleta>=? AND dia_coleta<>''""",
                     (*unidades, limite)).fetchone()
    if row and row["d"]:
        inicio = min(inicio, date.fromisoformat(row["d"]))
    coletadas = unidades_coletadas(db)
    if any(u not in coletadas for u in unidades):
        inicio = min(inicio, hoje - timedelta(days=DIAS_UNIDADE_NOVA))
    return inicio.isoformat(), hoje.isoformat()


def _estado_coleta() -> dict:
    e = get_preferencia(PREF_COLETA, {})
    return e if isinstance(e, dict) else {}


# ── parâmetros da coleta ─────────────────────────────────────────────
def _hhmm(v) -> str | None:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(v or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def validar_parametros(d: dict) -> tuple[dict | None, str]:
    """(parâmetros, "") ou (None, mensagem para a tela)."""
    ini, fim = _hhmm(d.get("inicio")), _hhmm(d.get("fim"))
    if not ini or not fim:
        return None, "Informe o início e o término da coleta no formato HH:MM."
    if ini >= fim:
        return None, "O término da coleta precisa ser depois do início."
    try:
        iv, it = int(d.get("intervalo_min")), int(d.get("intervalo_itens_min"))
    except (TypeError, ValueError):
        return None, "Intervalos inválidos."
    if iv not in INTERVALOS_PERMITIDOS or it not in INTERVALOS_ITENS_PERMITIDOS:
        return None, "Intervalos fora das opções disponíveis."
    if it < iv:
        return None, "O intervalo dos produtos não pode ser menor que o da coleta das notas."
    # PNP: ausente (tela antiga, parâmetro salvo antes de 15/11/2026) = padrão
    try:
        pm = int(d.get("pnp_min", PARAMETROS_PADRAO["pnp_min"]))
        pr = int(d.get("pnp_raio_m", PARAMETROS_PADRAO["pnp_raio_m"]))
    except (TypeError, ValueError):
        return None, "Informe a parada não prevista em minutos e o raio do cliente em metros."
    if not PNP_MIN_FAIXA[0] <= pm <= PNP_MIN_FAIXA[1]:
        return None, f"O tempo da parada não prevista deve ficar entre {PNP_MIN_FAIXA[0]} e {PNP_MIN_FAIXA[1]} minutos."
    if not PNP_RAIO_FAIXA[0] <= pr <= PNP_RAIO_FAIXA[1]:
        return None, f"O raio do cliente deve ficar entre {PNP_RAIO_FAIXA[0]} e {PNP_RAIO_FAIXA[1]} metros."
    return {"inicio": ini, "fim": fim, "intervalo_min": iv, "intervalo_itens_min": it,
            "pnp_min": pm, "pnp_raio_m": pr}, ""


def parametros() -> dict:
    """Parâmetros salvos, com o padrão no que faltar ou estiver inválido."""
    salvo = get_preferencia(PREF_PARAMETROS, {})
    p = {**PARAMETROS_PADRAO, **(salvo if isinstance(salvo, dict) else {})}
    ok, _ = validar_parametros(p)
    return ok or dict(PARAMETROS_PADRAO)


def salvar_parametros(d: dict, usuario: str) -> tuple[dict | None, str]:
    p, erro = validar_parametros(d)
    if not p:
        return None, erro
    set_preferencia(PREF_PARAMETROS, {**p, "alterado_por": usuario, "alterado_em": _agora_txt()})
    return p, ""


def no_horario(p: dict | None = None, agora: datetime | None = None) -> bool:
    """Dentro da janela de coleta (início e término inclusive)."""
    p = p or parametros()
    hm = (agora or _agora()).strftime("%H:%M")
    return p["inicio"] <= hm <= p["fim"]


def pedir_catalogo(usuario: str) -> None:
    """A lista de unidades é atualizada na próxima coleta (a tela nunca
    chama a Ravex)."""
    set_preferencia(PREF_PEDIDO_CATALOGO, {"em": _agora_txt(), "por": usuario})


def alteracao_parametros() -> dict:
    """Quem alterou os parâmetros por último (para o gerente, na tela)."""
    salvo = get_preferencia(PREF_PARAMETROS, {})
    if not isinstance(salvo, dict):
        return {}
    return {"por": salvo.get("alterado_por", ""), "em": salvo.get("alterado_em", "")}


def situacao_catalogo() -> dict:
    """Para a tela: quando a lista de unidades foi atualizada e se há um
    pedido de atualização aguardando a próxima coleta."""
    est = _estado_coleta()
    feito = est.get("catalogo_em", "") or ""
    tentativa = est.get("catalogo_tentativa_em", "") or ""
    pedido = get_preferencia(PREF_PEDIDO_CATALOGO, {})
    pedido_em = pedido.get("em", "") if isinstance(pedido, dict) else ""
    return {"atualizado_em": feito, "pedido_em": pedido_em,
            "pendente": bool(pedido_em) and pedido_em > max(feito, tentativa),
            "erro": est.get("catalogo_erro", "") or ""}


def _precisa_catalogo(db, est: dict) -> bool:
    """Uma vez por dia (1º ciclo do dia dentro do horário), na primeira
    coleta ou quando pedido pela tela depois da última atualização. Depois
    de uma falha, com uma lista salva, nova tentativa só depois de 1 h."""
    if not listar_unidades(db, somente_cd=False):
        return True
    feito = est.get("catalogo_em", "") or ""
    pedido = get_preferencia(PREF_PEDIDO_CATALOGO, {})
    pedido_em = pedido.get("em", "") if isinstance(pedido, dict) else ""
    tentativa = est.get("catalogo_tentativa_em", "") or ""
    if pedido_em > max(feito, tentativa):
        return True                                   # pedido da tela ainda não atendido
    if feito[:10] == _hoje().isoformat():
        return False
    if est.get("catalogo_erro") and _agora().timestamp() - _ts(tentativa) < ESPERA_CATALOGO_APOS_FALHA:
        return False
    return True


def _ts(txt: str) -> float:
    try:
        return datetime.strptime(txt, "%Y-%m-%d %H:%M:%S").timestamp()
    except (TypeError, ValueError):
        return 0.0


def coletar(forcar: bool = False) -> tuple[bool, str]:
    """Um ciclo de coleta: catálogo (1x/dia), notas e itens, nos horários e
    intervalos dos parâmetros. Chamado pelo cron da Ravex
    (scripts/atualizar_ravex.py), no mesmo processo da grade de posição.
    forcar=True (execução manual) ignora horário e intervalos."""
    from app.database import get_ravex_credenciais
    from app.services import ravex_sessao as rs

    est = _estado_coleta()
    par = parametros()
    agora_ts = _agora().timestamp()
    if not forcar and not no_horario(par):
        return True, f"Fora do horário de coleta das entregas ({par['inicio']} às {par['fim']})."
    intervalo_seg = max(par["intervalo_min"] * 60 - FOLGA_CICLO_SEG, 60)
    intervalo_itens_seg = max(par["intervalo_itens_min"] * 60 - FOLGA_CICLO_SEG, 60)
    if not forcar and agora_ts - _ts(est.get("em", "")) < intervalo_seg:
        return True, "Coleta das entregas recente; aguardando o próximo ciclo."
    creds = get_ravex_credenciais()
    usuario, senha = creds.get("usuario", ""), creds.get("senha", "")
    if not usuario or not senha:
        return False, "Credenciais da Ravex não configuradas."

    t0 = time.monotonic()
    # o que não muda neste ciclo é levado do anterior (um ciclo com falha
    # nas notas não pode apagar o aviso dos produtos nem o da lista)
    resumo: dict = {"em": _agora_txt(), "ok": False, "mensagem": "", "ultima_ok_em": est.get("ultima_ok_em", ""),
                    "itens_em": est.get("itens_em", ""), "itens_erro": est.get("itens_erro", ""),
                    "catalogo_em": est.get("catalogo_em", ""), "catalogo_erro": est.get("catalogo_erro", ""),
                    "catalogo_tentativa_em": est.get("catalogo_tentativa_em", "")}
    try:
        with get_db() as db:
            garantir_tabelas(db)
            precisa_catalogo = _precisa_catalogo(db, est)
            tem_catalogo = bool(listar_unidades(db, somente_cd=False))
        if precisa_catalogo:
            resumo["catalogo_tentativa_em"] = _agora_txt()
            # falha na lista de unidades não para a coleta quando já existe
            # uma lista salva (a anterior segue valendo; nova tentativa no
            # próximo ciclo). Sem lista nenhuma, a coleta não tem o que fazer.
            try:
                r = rs.requisitar({"Modulo": "Cadastro", "Objeto": "Cliente",
                                   "Acao": "ListarUnidadesVisiveisAoUsuario"}, usuario, senha, timeout=30)
                try:
                    body = r.json()
                except ValueError:
                    raise rs.ErroRavex("Lista de unidades da Ravex não veio em JSON.")
                if not body.get("status") or not isinstance(body.get("dados"), list):
                    raise rs.ErroRavex(f"Lista de unidades recusada: {str(body.get('mensagem') or '')[:200]}")
                with get_db() as db:
                    n = gravar_catalogo(db, body["dados"])
                resumo["catalogo_em"] = _agora_txt()
                resumo["catalogo_erro"] = ""
                print(f"[Entregas] Catálogo de unidades atualizado ({n}).")
            except rs.BloqueioRavex as e:
                # bloqueio é só desta consulta (hotfix 14/11/2026): com a
                # lista salva, a coleta segue para as notas
                if not tem_catalogo:
                    raise
                resumo["catalogo_erro"] = str(e)[:300]
                print(f"[Entregas] Lista de unidades não atualizada: {resumo['catalogo_erro']}")
            except (rs.SessaoIndisponivel, rs.FalhaLogin):
                raise   # sessão fora: as notas também não sairiam
            except rs.ErroRavex as e:
                if not tem_catalogo:
                    raise
                resumo["catalogo_erro"] = str(e)[:300]
                print(f"[Entregas] Lista de unidades não atualizada: {resumo['catalogo_erro']}")

        with get_db() as db:
            unidades = unidades_para_coletar(db)
            if not unidades:
                resumo.update(ok=True, mensagem="Nenhuma unidade selecionada nos filtros do painel.", unidades=[])
                return True, resumo["mensagem"]
            inicio, fim = _periodo_notas(db, unidades)

        texto = _baixar_csv(RELATORIO_NF, unidades, inicio, fim, usuario, senha)
        linhas = ler_csv(texto, COLUNAS_NF, OBRIGATORIAS_NF)
        with get_db() as db:
            r_nf = gravar_notas(db, linhas, unidades, inicio, fim)
            for u in unidades:
                db.execute("INSERT OR IGNORE INTO entregas_unidade_coleta(unidade_id, desde) VALUES(?,?)",
                           (u, _agora_txt()))
        resumo.update(unidades=unidades, periodo=[inicio, fim], bytes_notas=len(texto), **r_nf)

        # itens: notas sem produto ainda (novas ou com nova tentativa liberada).
        # Falha aqui não invalida as notas já gravadas: fica registrada à
        # parte e o relatório de itens volta depois do intervalo dos produtos
        # (Parâmetros, padrão 10 min).
        if forcar or agora_ts - _ts(est.get("itens_em", "")) >= intervalo_itens_seg:
            try:
                limite_tent = (_agora() - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
                with get_db() as db:
                    marc = ",".join("?" * len(unidades))
                    pend = db.execute(f"""SELECT v.unidade_id AS u, MIN(v.dia_coleta) AS d FROM entregas_nfs n
                                          JOIN entregas_viagens v ON v.id_viagem=n.id_viagem
                                          WHERE v.unidade_id IN ({marc}) AND v.dia_coleta>=? AND n.itens_qtd=0
                                            AND (n.itens_em='' OR (n.itens_tentativas<? AND n.itens_em<=?))
                                          GROUP BY v.unidade_id""",
                                      (*unidades, (_hoje() - timedelta(days=DIAS_MAX_ATRAS)).isoformat(),
                                       TENTATIVAS_ITENS, limite_tent)).fetchall()
                if pend:
                    u_itens = sorted(r["u"] for r in pend)
                    ini_itens = min(r["d"] for r in pend)
                    texto_i = _baixar_csv(RELATORIO_ITENS, u_itens, ini_itens, fim, usuario, senha)
                    linhas_i = ler_csv(texto_i, COLUNAS_ITENS, OBRIGATORIAS_ITENS)
                    with get_db() as db:
                        r_it = gravar_itens(db, linhas_i, u_itens, ini_itens, fim)
                    resumo.update(bytes_itens=len(texto_i), **r_it)
                resumo["itens_erro"] = ""
            except (rs.ErroRavex, FormatoInesperado) as e:
                resumo["itens_erro"] = str(e)[:300]
                print(f"[Entregas] Produtos das notas não atualizados: {resumo['itens_erro']}")
            resumo["itens_em"] = _agora_txt()
        else:
            resumo["itens_erro"] = est.get("itens_erro", "")

        # retenção diária: feita por scripts/atualizar_ravex._limpeza_diaria em
        # todo ciclo do cron, antes desta coleta e com espera de 1 h depois de
        # uma falha. Repetir aqui ignorava essa espera e marcava a coleta
        # como falha (revisão de 16/11/2026).
        resumo.update(ok=True, ultima_ok_em=resumo["em"],
                      mensagem=f"{resumo.get('viagens', 0)} viagens e {resumo.get('notas', 0)} notas "
                               f"({inicio} a {fim}).")
        return True, resumo["mensagem"]
    except (rs.ErroRavex, FormatoInesperado) as e:
        resumo["mensagem"] = str(e)
        return False, resumo["mensagem"]
    except Exception as e:
        resumo["mensagem"] = f"Erro inesperado na coleta das entregas: {type(e).__name__}: {str(e)[:200]}"
        return False, resumo["mensagem"]
    finally:
        resumo["segundos"] = round(time.monotonic() - t0, 1)
        # a limpeza diária é de outra rotina: o estado dela segue como está
        resumo.setdefault("limpeza_em", est.get("limpeza_em", ""))
        if est.get("limpeza_falha_em"):
            resumo.setdefault("limpeza_falha_em", est["limpeza_falha_em"])
        try:
            set_preferencia(PREF_COLETA, resumo)
        except Exception:
            pass


def estado_coleta() -> dict:
    e = _estado_coleta()
    return {k: e.get(k) for k in ("em", "ok", "mensagem", "ultima_ok_em", "periodo", "unidades", "itens_erro",
                                  "catalogo_em")}


# ── consulta do painel ───────────────────────────────────────────────
def _cores_da_entrega(notas: list[dict]) -> list[str]:
    cats = set()
    for n in notas:
        c = categoria_status(n["status_nf"], n.get("qtd_reentregas"))
        if c == "parc":
            cats.update(("ok", "dev"))
        else:
            cats.add(c)
    return [c for c in ORDEM_CORES if c in cats]


def montar_entregas(notas: list[dict]) -> list[dict]:
    """Agrupa as notas de UMA viagem por cliente (1 bolinha por cliente)."""
    from app.services.entregas_pnp import marcar_fora_de_sequencia
    grupos: dict[str, list[dict]] = {}
    for n in notas:
        grupos.setdefault(n["cod_cliente"] or n["cliente"] or n["numero_nf"], []).append(n)
    out = []
    primeira: dict[str, str] = {}
    for cod, ns in grupos.items():
        cats = [categoria_status(n["status_nf"], n.get("qtd_reentregas")) for n in ns]
        baixadas = [n for n in ns if teve_baixa(n["status_nf"])]
        seqs = [n["seq_programada"] for n in ns if n["seq_programada"] is not None]
        seqs_r = [n["seq_real"] for n in ns if n["seq_real"] is not None]
        fins = [n["fim_entrega"] for n in baixadas if n["fim_entrega"]]
        primeira[cod] = min(fins) if fins else ""
        ref = ns[0]
        out.append({
            "cliente_cod": cod,
            "cliente": ref["cliente"], "cidade": ref["cidade"], "bairro": ref["bairro"],
            "seq": min(seqs) if seqs else None,
            "seq_real": min(seqs_r) if seqs_r else None,
            "cores": _cores_da_entrega(ns),
            "fora_raio": any(_norm(n["homologada"]) != "sim" for n in baixadas),
            "em_atendimento": any(_norm(n["status_nf"]).startswith("entregando") for n in ns),
            "notas": len(ns),
            "realizada": bool(ns) and len(baixadas) == len(ns),
            "com_baixa": bool(baixadas),
            "reentrega": "reent" in cats,
            "devolucao": "dev" in cats or "parc" in cats,
            "devolucao_parcial": "parc" in cats or ("dev" in cats and "ok" in cats),
            "fim": max(fins) if fins else "",
        })
    out.sort(key=lambda e: (e["seq"] is None, e["seq"] if e["seq"] is not None else 0, e["cliente"]))
    # entrega feita fora da sequência programada (exclamação na bolinha)
    marcar_fora_de_sequencia(out, [primeira.get(e["cliente_cod"], "") for e in out])
    return out


def consultar_painel(db, unidades: list[int], de: str, ate: str, situacao: str = "todas", busca: str = "",
                     placas: list[str] | None = None) -> dict:
    if not unidades:
        return {"viagens": [], "kpis": _kpis([]), "veiculos": []}
    marc = ",".join("?" * len(unidades))
    # viagem em andamento no período: começou até `ate` e terminou a partir
    # de `de` (ou segue aberta — aberta antiga demais fica de fora)
    limite_aberta = (date.fromisoformat(de) - timedelta(days=DIAS_MAX_ATRAS)).isoformat()
    sql = f"""SELECT * FROM entregas_viagens WHERE unidade_id IN ({marc}) AND ausente_desde=''
              AND dia<=? AND ((aberta=1 AND dia>=?) OR fim_dia>=? OR (fim_dia='' AND dia>=?))"""
    params: list = [*unidades, ate, limite_aberta, de, de]
    if situacao == "abertas":
        sql += " AND aberta=1"
    elif situacao == "finalizadas":
        sql += " AND aberta=0"
    viagens = [dict(r) for r in db.execute(sql, params).fetchall()]
    # veículos do período (lista do filtro de veículos), antes do próprio filtro
    veic: dict[str, dict] = {}
    for v in viagens:
        p = _placa(v["placa"])
        if p and p not in veic:
            veic[p] = {"placa": p, "motorista": v["motorista"], "unidade": sigla_unidade(v["unidade"]),
                       "aberta": bool(v["aberta"])}
        elif p and v["aberta"]:
            veic[p]["aberta"] = True
    if placas:
        alvo = set(placas)
        viagens = [v for v in viagens if _placa(v["placa"]) in alvo]
    b = _norm(busca)
    if b:
        viagens = [v for v in viagens if b in _norm(v["placa"]) or b in _norm(v["motorista"])
                   or b in _norm(v["id_viagem"]) or b in _norm(v["transportadora"])]
    ids = [v["id_viagem"] for v in viagens]
    notas_por_viagem: dict[str, list[dict]] = {}
    for i in range(0, len(ids), 500):
        lote = ids[i:i + 500]
        q = ",".join("?" * len(lote))
        for n in db.execute(f"""SELECT id_viagem, numero_nf, cod_cliente, cliente, cidade, bairro, seq_programada,
                                       seq_real, status_nf, homologada, inicio_entrega, fim_entrega, qtd_reentregas,
                                       lat_ref, lon_ref, lat_ent, lon_ent, primeira_pos_raio, ultima_pos_raio
                                FROM entregas_nfs WHERE id_viagem IN ({q})""", lote):
            notas_por_viagem.setdefault(n["id_viagem"], []).append(dict(n))
    # paradas não previstas (15/11/2026): histórico de posições das placas
    from app.services import entregas_pnp as pnp
    from app.services import ravex_posicoes
    par = parametros()
    agora = _agora()
    cds = pnp.cds_cadastrados(db)
    cds_sig = pnp.assinatura_cds(cds)
    limites = pnp.proximos_inicios(db, viagens)
    saida = []
    with pnp.conexao_posicoes() as pcx:
        for v in viagens:
            notas_v = notas_por_viagem.get(v["id_viagem"], [])
            ents = montar_entregas(notas_v)
            limite = limites.get(v["id_viagem"])
            corrente = _corrente(v, limite)
            # posição atual só na viagem que o veículo está fazendo agora
            pos = ravex_posicoes.posicao(v["placa"]) if corrente else None
            # veículo no raio do cliente, atendimento não iniciado (15/11/2026);
            # viagem ainda não iniciada não usa a posição atual
            iniciada = corrente and bool(v["inicio_real"])
            pnp.marcar_no_raio(ents, notas_v, pos if iniciada else None, iniciada, agora, par["pnp_raio_m"])
            paradas_np = pnp.pnp_da_viagem(pcx, v, notas_v, cds, par, agora, limite, cds_sig)
            saida.append({
                "id_viagem": v["id_viagem"], "identificador": v["identificador"], "placa": v["placa"],
                "motorista": v["motorista"], "transportadora": v["transportadora"],
                "unidade": sigla_unidade(v["unidade"]), "unidade_id": v["unidade_id"],
                "status_viagem": v["status_viagem"], "aberta": bool(v["aberta"]), "corrente": corrente,
                "inicio": v["inicio_real"] or v["importacao"], "fim": v["fim_real"],
                "entregas": ents,
                "total": len(ents), "realizadas": sum(1 for e in ents if e["realizada"]),
                "pnp": len(paradas_np), "pnp_minutos": sum(p["minutos"] for p in paradas_np),
                "pnp_andamento": any(p["em_andamento"] for p in paradas_np),
                # temperatura ao lado da placa (pedido do usuário 16/11/2026)
                **temperatura_atual(pos, agora),
                **eficiencia_da_viagem(v),
            })
    saida.sort(key=lambda x: (not x["aberta"], x["unidade"], x["inicio"] or "", x["placa"]))
    return {"viagens": saida, "kpis": _kpis(saida),
            "veiculos": sorted(veic.values(), key=lambda x: (x["unidade"], x["placa"]))}


def _corrente(v, limite) -> bool:
    """Viagem que o veículo está fazendo agora: aberta, dentro da janela
    acompanhada (DIAS_MAX_ATRAS) e sem viagem seguinte da mesma placa.
    Revisão de 15/11/2026: viagem que ficou "aberta" na Ravex mostrava a
    posição atual do veículo já em outra viagem (até de outra unidade)."""
    return bool(v["aberta"]) and limite is None \
        and str(v["dia"] or "") >= (_hoje() - timedelta(days=DIAS_MAX_ATRAS)).isoformat()


def viagem_corrente(db, v) -> bool:
    from app.services.entregas_pnp import proximos_inicios
    v = dict(v)
    return _corrente(v, proximos_inicios(db, [v]).get(v["id_viagem"])) if v.get("aberta") else False


def _idade_min(em: str, agora: datetime) -> int | None:
    from app.services.entregas_pnp import instante
    t = instante(em)
    return None if t is None else max(0, round((agora - t).total_seconds() / 60))


def temperatura_atual(pos: dict | None, agora: datetime) -> dict:
    """Última temperatura do baú na grade da Ravex (UltimaTemperatura), com
    o horário da comunicação em que veio — pedido do usuário 16/11/2026:
    temperatura ao lado da placa. Sem posição ou sem leitura ("*"): vazio."""
    if not pos or pos.get("temp") is None:
        return {"temp": None, "temp_em": "", "temp_idade_min": None}
    em = pos.get("em") or ""
    return {"temp": pos["temp"], "temp_em": em, "temp_idade_min": _idade_min(em, agora)}


def _dh(s: str) -> str:
    """'2026-11-16 08:05:00' → '16/11 08:05'"""
    s = str(s or "")
    return f"{s[8:10]}/{s[5:7]} {s[11:16]}" if len(s) >= 16 else ""


def agora_da_viagem(db, v, entregas: list[dict], pnps: list[dict], corrente: bool) -> dict | None:
    """Bloco "Agora" da janela da viagem (pedido do usuário 16/11/2026: ao
    clicar na placa, temperatura atual e situação atual — entregando, no
    raio, parado...). Só na viagem que o veículo está fazendo agora
    (viagem_corrente): nas outras a posição atual é de outra viagem.
    `entregas` já com em_atendimento/no_raio; `pnps` da viagem (trajeto)."""
    if not corrente:
        return None
    from app.services import entregas_pnp as pnp
    from app.services import ravex_posicoes
    agora = _agora()
    pos = ravex_posicoes.posicao(v["placa"])
    out = {**temperatura_atual(pos, agora), "ignicao": (pos or {}).get("ignicao", ""),
           "em": (pos or {}).get("em", ""), "idade_min": _idade_min((pos or {}).get("em", ""), agora)}
    valida = pnp.posicao_valida(pos, agora)
    cd = None
    if valida:
        cd = next((c for c in pnp.cds_cadastrados(db)
                   if pnp.distancia_m(valida[0], valida[1], c["lat"], c["lon"]) <= c["raio_m"]), None)
    no_cd = f" · no CD {cd['sigla']}" if cd else ""
    atend = [e for e in entregas if e.get("em_atendimento")]
    raio = [e for e in entregas if e.get("no_raio")]
    andamento = [p for p in pnps if p.get("em_andamento")]
    def nome(e):
        return " ".join(x for x in (f"{e['seq']}." if e.get("seq") is not None else "", e.get("cliente") or "Cliente") if x)
    if not v["inicio_real"]:
        cod, txt = "aguardando", "Viagem ainda não iniciada na Ravex" + no_cd
    elif atend:
        cod, txt = "atendimento", "Em atendimento · " + ", ".join(nome(e) for e in atend)
    elif raio:
        cod, txt = "no_raio", "Veículo no raio do cliente · " + ", ".join(nome(e) for e in raio)
    elif andamento:
        cod, txt = "pnp", f"Parada não prevista desde {_dh(andamento[-1]['inicio'])}"
    elif not pos:
        cod, txt = "sem_posicao", "Veículo sem posição na grade da Ravex"
    elif not valida:
        cod, txt = "sem_comunicacao", f"Sem comunicação do rastreador desde {_dh(pos.get('em'))}"
    elif cd:
        cod, txt = "cd", f"No CD {cd['sigla']}"
    elif pnp._desligada(pos.get("ignicao")):
        cod, txt = "parado", "Parado, com a ignição desligada"
    else:
        cod, txt = "transito", "Em trânsito"
    out.update({"codigo": cod, "situacao": txt})
    return out


def _kpis(viagens: list[dict]) -> dict:
    ents = [e for v in viagens for e in v["entregas"]]
    realizadas = [e for e in ents if e["realizada"]]
    return {
        "viagens": len(viagens),
        "viagens_abertas": sum(1 for v in viagens if v["aberta"]),
        "entregas": len(ents),
        "realizadas": len(realizadas),
        "pendentes": len(ents) - len(realizadas),
        "reentregas": sum(1 for e in ents if e["reentrega"]),
        "devolucoes": sum(1 for e in ents if e["devolucao"]),
        "devolucoes_parciais": sum(1 for e in ents if e["devolucao_parcial"]),
        "fora_raio": sum(1 for e in ents if e["fora_raio"]),
        "baixadas": sum(1 for e in ents if e["com_baixa"]),
    }


def buscar_nf(db, unidades: list[int], termo: str, limite: int = 20) -> list[dict]:
    """Localiza a nota pelo número ou pela chave NF-e (44 dígitos) nas
    unidades informadas, em qualquer data coletada (pedido do usuário
    13/11/2026: achou a nota, abre o detalhe da entrega)."""
    dig = re.sub(r"\D", "", termo or "")
    if not dig or not unidades:
        return []
    marc = ",".join("?" * len(unidades))
    if len(dig) == 44:
        cond, vals = "n.chave=?", [dig]
    else:
        cond, vals = "(n.numero_nf=? OR n.numero_nf=?)", [dig, dig.lstrip("0") or "0"]
    rows = db.execute(f"""SELECT n.id_viagem, n.numero_nf, n.cod_cliente, n.cliente, n.cidade, n.status_nf,
                                 n.qtd_reentregas, v.placa, v.unidade, v.inicio_real, v.importacao
                          FROM entregas_nfs n JOIN entregas_viagens v ON v.id_viagem=n.id_viagem
                          WHERE v.unidade_id IN ({marc}) AND v.ausente_desde='' AND {cond}
                          ORDER BY v.dia DESC, v.inicio_real DESC LIMIT ?""",
                      (*unidades, *vals, limite)).fetchall()
    return [{"id_viagem": r["id_viagem"], "numero_nf": r["numero_nf"],
             "cliente_cod": r["cod_cliente"] or r["cliente"] or r["numero_nf"],
             "cliente": r["cliente"], "cidade": r["cidade"], "status_nf": r["status_nf"],
             "categoria": categoria_status(r["status_nf"], r["qtd_reentregas"]),
             "placa": r["placa"], "unidade": sigla_unidade(r["unidade"]),
             "inicio": r["inicio_real"] or r["importacao"]} for r in rows]


def _coord_valida(lat, lon) -> bool:
    return lat is not None and lon is not None and -34.5 <= lat <= 5.5 and -74.5 <= lon <= -28.5


def pontos_da_viagem(db, id_viagem: str, corrente: bool | None = None) -> dict:
    """Clientes da viagem no mapa do detalhe (pedido do usuário 15/11/2026):
    coordenada de referência do cliente na Ravex, sequência e cor da
    situação — a mesma bolinha da lista. `corrente` = viagem_corrente()
    (quem chama já calculou; sem ele, calcula aqui)."""
    from app.services import entregas_pnp as pnp
    from app.services import ravex_posicoes
    notas = [dict(n) for n in db.execute(
        """SELECT numero_nf, cod_cliente, cliente, cidade, bairro, seq_programada, seq_real, status_nf, homologada,
                  fim_entrega, qtd_reentregas, lat_ref, lon_ref, ultima_pos_raio
           FROM entregas_nfs WHERE id_viagem=?""", (id_viagem,))]
    coord: dict[str, tuple] = {}
    for n in notas:
        chave = n["cod_cliente"] or n["cliente"] or n["numero_nf"]
        if chave not in coord and _coord_valida(n["lat_ref"], n["lon_ref"]):
            coord[chave] = (round(n["lat_ref"], 6), round(n["lon_ref"], 6))
    v = db.execute("SELECT * FROM entregas_viagens WHERE id_viagem=?", (id_viagem,)).fetchone()
    if corrente is None:
        corrente = bool(v) and viagem_corrente(db, v)
    iniciada = bool(corrente and v["inicio_real"])
    ents = montar_entregas(notas)
    pnp.marcar_no_raio(ents, notas, ravex_posicoes.posicao(v["placa"]) if iniciada else None, iniciada, _agora(),
                       parametros()["pnp_raio_m"])
    pontos, sem, entregas = [], 0, []
    for e in ents:
        # todas as entregas da viagem, na ordem da lista: faixa de navegação
        # do detalhe (pedido do usuário 15/11/2026 — trocar de entrega sem
        # fechar o pop-up), com coordenada ou sem
        entregas.append({k: e[k] for k in ("cliente_cod", "cliente", "cidade", "bairro", "seq", "cores",
                                           "fora_raio", "em_atendimento", "no_raio", "realizada", "fora_seq")})
        c = coord.get(e["cliente_cod"])
        if not c:
            sem += 1
            continue
        pontos.append({"cliente_cod": e["cliente_cod"], "cliente": e["cliente"], "cidade": e["cidade"],
                       "seq": e["seq"], "cores": e["cores"], "realizada": e["realizada"],
                       "em_atendimento": e["em_atendimento"], "no_raio": e["no_raio"], "lat": c[0], "lon": c[1]})
    return {"pontos": pontos, "sem_coordenada": sem, "entregas": entregas}


def eficiencia_da_viagem(v) -> dict:
    """Eficiência do frio da Ravex gravada na viagem (Grid Operacional)."""
    cols = v.keys()
    def c(nome, padrao=None):
        return v[nome] if nome in cols else padrao
    return {"efet_temp": c("efet_temp"), "efet_situacao": c("efet_situacao", "") or "",
            "efet_margem": c("efet_margem"), "efet_temp_em": c("efet_temp_em", "") or ""}


def resumo_viagem(v, corrente: bool = False) -> dict:
    cols = v.keys()
    return {"id_viagem": v["id_viagem"], "identificador": v["identificador"], "placa": v["placa"],
            "motorista": v["motorista"], "unidade": sigla_unidade(v["unidade"]), "status_viagem": v["status_viagem"],
            "transportadora": v["transportadora"], "aberta": bool(v["aberta"]), "corrente": bool(corrente),
            "inicio": v["inicio_real"] or v["importacao"], "inicio_real": v["inicio_real"],
            "importacao": v["importacao"], "fim": v["fim_real"],
            **eficiencia_da_viagem(v),
            "temp_min": v["temp_min"] if "temp_min" in cols else None,
            "temp_max": v["temp_max"] if "temp_max" in cols else None}


def trajeto_viagem(db, id_viagem: str) -> dict | None:
    """Paradas não previstas e local do início e do fim da viagem (pedido do
    usuário 15/11/2026), do histórico de posições da grade da Ravex — ver
    app/services/entregas_pnp.py. Sem filtro de perfil: quem chama tira a
    localização exata de quem não pode ver."""
    from app.services import entregas_pnp as pnp
    v = db.execute("SELECT * FROM entregas_viagens WHERE id_viagem=?", (id_viagem,)).fetchone()
    if not v:
        return None
    v = dict(v)
    notas = [dict(n) for n in db.execute(
        """SELECT numero_nf, cod_cliente, cliente, seq_programada, inicio_entrega, fim_entrega, lat_ref, lon_ref,
                  lat_ent, lon_ent, primeira_pos_raio, ultima_pos_raio
           FROM entregas_nfs WHERE id_viagem=?""", (id_viagem,))]
    par = parametros()
    agora = _agora()
    ini = pnp.instante(v["inicio_real"])
    amostras, limite, fim_grafico = [], None, None
    if ini:
        # mesma regra do painel (viagem seguinte da placa, no banco inteiro)
        limite = pnp.proximos_inicios(db, [v]).get(id_viagem)
        # folga de TOLERANCIA_PONTA_MIN nas duas pontas (a comunicação da
        # saída e a da chegada podem vir minutos antes/depois); viagem
        # encerrada lê só até o fim dela (revisão 15/11: viagem antiga lia
        # semanas de histórico a cada abertura)
        tol = timedelta(minutes=pnp.TOLERANCIA_PONTA_MIN)
        _, fim, ate_agora = pnp.janela_viagem(v, notas, agora, limite)
        fim_real = pnp.instante(v["fim_real"])
        if ate_agora:
            ate = fim_grafico = None
        elif fim or fim_real:
            ate = max(t for t in (fim, fim_real) if t) + tol
            fim_grafico = fim or fim_real
        else:
            # encerrada sem fim real e sem baixa (cancelada depois de
            # iniciada): até a viagem seguinte da placa ou, sem ela, até
            # SEM_FIM_MAX_H depois do início — nunca o histórico da placa até
            # agora, que mostraria outras viagens (revisão de 16/11/2026)
            teto = min(agora, ini + timedelta(hours=SEM_FIM_MAX_H))
            ate = fim_grafico = min(limite, teto) if limite and limite > ini else teto
        # a linha gravada até AGRUPAR_ATE_MIN antes pode cobrir o início
        # (gravação enxuta, visto_ate)
        antes = timedelta(minutes=max(pnp.TOLERANCIA_PONTA_MIN, pnp.AGRUPAR_ATE_MIN))
        amostras = pnp.historico_placa(v["placa"], pnp.texto(ini - antes), pnp.texto(ate) if ate else "9999")
    r = pnp.analisar_viagem(v, notas, amostras, pnp.cds_cadastrados(db), par, agora, limite, pontas=True)
    r["parametros"] = {"pnp_min": par["pnp_min"], "pnp_raio_m": par["pnp_raio_m"]}
    r["historico_desde"] = amostras[0]["em"] if amostras else ""
    r["temperaturas"] = serie_temperatura(amostras, ini, fim_grafico)
    r["atendimentos"] = janelas_atendimento(notas)
    return r


def serie_temperatura(amostras: list[dict], ini: datetime | None, ate: datetime | None) -> list[list]:
    """Pontos [horário, °C] do gráfico de temperatura da viagem (pedido do
    usuário 16/11/2026), do histórico de posições. A linha que cobre um
    intervalo (gravação enxuta: mesma temperatura até `visto_ate`) vira dois
    pontos, o começo e o fim. Fora da janela da viagem fica de fora."""
    from app.services import entregas_pnp as pnp
    if ini is None:
        return []
    de = pnp.texto(ini)
    ate_s = pnp.texto(ate) if ate else "9999"
    out: list[list] = []
    for a in amostras:
        t = a.get("temp")
        if t is None:
            continue
        em, va = str(a.get("em") or "")[:19], str(a.get("visto_ate") or "")[:19]
        fim = va if va > em else em
        if fim < de or em > ate_s:
            continue
        out.append([max(em, de), t])
        if fim > em:
            out.append([min(fim, ate_s), t])
    return out


def janelas_atendimento(notas: list[dict]) -> list[dict]:
    """Início e fim do atendimento de cada cliente (primeira e última baixa
    das notas dele), para marcar no gráfico de temperatura: porta aberta no
    cliente aparece como subida da temperatura nessas faixas."""
    por: dict[str, dict] = {}
    for n in notas:
        a, b = str(n.get("inicio_entrega") or ""), str(n.get("fim_entrega") or "")
        if len(a) < 16 and len(b) < 16:
            continue
        a, b = a if len(a) >= 16 else b, b if len(b) >= 16 else a
        chave = n.get("cod_cliente") or n.get("cliente") or n.get("numero_nf")
        j = por.setdefault(chave, {"cliente": n.get("cliente") or "", "seq": n.get("seq_programada"),
                                   "inicio": a, "fim": b})
        j["inicio"], j["fim"] = min(j["inicio"], a), max(j["fim"], b)
    return sorted(por.values(), key=lambda j: j["inicio"])


def detalhe_entrega(db, id_viagem: str, cliente_cod: str) -> dict | None:
    v = db.execute("SELECT * FROM entregas_viagens WHERE id_viagem=?", (id_viagem,)).fetchone()
    if not v:
        return None
    notas = [dict(n) for n in db.execute(
        """SELECT * FROM entregas_nfs WHERE id_viagem=? AND (cod_cliente=? OR (cod_cliente='' AND (cliente=? OR numero_nf=?)))
           ORDER BY numero_nf""", (id_viagem, cliente_cod, cliente_cod, cliente_cod))]
    if not notas:
        return None
    for n in notas:
        n["categoria"] = categoria_status(n["status_nf"], n["qtd_reentregas"])
        n["teve_baixa"] = teve_baixa(n["status_nf"])
        n["fora_raio"] = n["teve_baixa"] and _norm(n["homologada"]) != "sim"
        n["itens"] = [dict(i) for i in db.execute(
            "SELECT codigo, descricao, quantidade, peso_liquido FROM entregas_itens WHERE id_viagem=? AND numero_nf=? "
            "ORDER BY descricao", (id_viagem, n["numero_nf"]))]
        n["itens_pendentes"] = not n["itens"] and n["itens_tentativas"] < TENTATIVAS_ITENS
    ref = notas[0]
    entrega = montar_entregas(notas)[0]
    corrente = viagem_corrente(db, v)
    mapa = pontos_da_viagem(db, id_viagem, corrente)
    # fora da sequência e veículo no raio dependem das outras entregas da viagem
    mesma = [e for e in mapa["entregas"] if e["cliente_cod"] == entrega["cliente_cod"]]
    entrega["fora_seq"] = any(e["fora_seq"] for e in mesma)
    entrega["no_raio"] = any(e["no_raio"] for e in mesma)
    return {
        "viagem": resumo_viagem(v, corrente),
        "entrega": entrega,
        "cliente": {"codigo": ref["cod_cliente"], "nome": ref["cliente"], "cidade": ref["cidade"],
                    "bairro": ref["bairro"], "endereco": ref["endereco"]},
        "notas": [{k: n[k] for k in ("numero_nf", "status_nf", "categoria", "teve_baixa", "homologada", "fora_raio", "metros",
                                      "qtd_reentregas", "inicio_entrega", "fim_entrega", "data_estimada",
                                      "seq_programada", "seq_real", "peso_bruto", "valor_nf", "itens",
                                      "itens_pendentes")} for n in notas],
        "sequencia": mapa.pop("entregas"),
        "mapa": mapa,
    }
