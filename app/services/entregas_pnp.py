"""
Parada não prevista (PNP) e pontas da viagem (início e fim) no Monitoramento
de Entregas — pedido do usuário 15/11/2026: "se ficar parado em um local fora
do raio de algum cliente da viagem, um triângulo amarelo com uma exclamação
para sinalizar uma parada não prevista" e "uma bandeira para o início e fim
da viagem; ao clicar mostra os dados e a localização". Nos Parâmetros, o
tempo parado no mesmo lugar que gera a PNP (padrão 15 minutos, para todos)
e o raio do cliente.

Fonte: histórico das posições da grade da Ravex (Grid de Monitoramento) —
uma linha por placa e horário de comunicação do rastreador, gravada no cron
ao aplicar a grade (ravex_automation.aplicar_grade), só das placas com
viagem em aberto. A parada é medida entre as comunicações do rastreador no
mesmo lugar, então a precisão acompanha o intervalo da grade (Configurações
› Ravex): com a grade a cada 5 minutos, uma parada de 15 minutos aparece com
3 ou 4 comunicações no mesmo ponto.

O cálculo é feito na leitura (painel e detalhe), com os parâmetros do
momento: mudar o tempo ou o raio vale na hora, inclusive para as viagens
já encerradas que ainda têm histórico (RETENCAO_DIAS).

Armazenamento (pedido do usuário 16/11/2026: "6 meses de histórico, depois
compactamos para mais 18 meses e depois apagamos"):
- arquivo próprio, data/entregas_posicoes.db, fora do monitoramento.db: o
  backup diário (scripts/cleanup.py, 3 cópias), o backup de cada deploy
  (scripts/atualizar.sh, 2 cópias) e o VACUUM diário copiam o banco
  principal inteiro — o disco da VM tem 9,7 GB e já encheu (BUG-11);
- gravação enxuta: comunicação nova no mesmo ponto (até MESMO_PONTO_M), com
  a mesma ignição e a mesma temperatura, não vira linha nova por até
  AGRUPAR_ATE_MIN — estende o `visto_ate` da linha anterior (veículo
  parado no CD ou no cliente gera uma linha a cada 30 min, não a cada grade);
- passados RETENCAO_DIAS, as linhas vão para um arquivo mensal compactado
  (data/entregas/arquivo/posicoes_AAAA-MM.jsonl.gz), guardado por
  MESES_ARQUIVO e depois apagado. As viagens e notas seguem a mesma regra
  (entregas_ravex.limpar_antigos).
"""
from __future__ import annotations

import gzip
import json
import math
import os
import re
import sqlite3
import threading
from bisect import bisect_left, bisect_right
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from app.utils.text_utils import normalize_plate

RAIO_MESMO_LUGAR_M = 200       # comunicações a até 200 m do centro da parada = mesmo lugar
RETENCAO_DIAS = 180            # histórico de posições no banco: 6 meses (pedido do usuário 16/11/2026)
MESES_ARQUIVO = 18             # depois, arquivo mensal compactado por mais 18 meses; então apagado
MESMO_PONTO_M = 30             # gravação enxuta: comunicação a até 30 m da linha anterior = mesmo ponto
AGRUPAR_ATE_MIN = 30           # ... junta na linha anterior por até 30 min (uma linha a cada 30 min parado)
VARIACAO_TEMP_C = 0.5          # temperatura que mudou 0,5 °C ou mais grava linha nova (gráfico da viagem)
TOLERANCIA_PONTA_MIN = 20      # posição do início/fim = comunicação mais próxima, até 20 min de distância
RAIO_CD_PADRAO_KM = 1.0        # mesmo padrão do raio de chegada do CD (carretas_viagem.RAIO_PADRAO_KM)
PNP_MIN_PADRAO = 15
PNP_RAIO_PADRAO_M = 500


# ── utilitários ──────────────────────────────────────────────────────
def _dt(s) -> datetime | None:
    """'YYYY-MM-DD HH:MM[:SS]' → datetime. fromisoformat (rápido): o painel
    converte milhares de comunicações a cada leitura — com strptime a
    revisão mediu 22 s para 50 placas em 7 dias."""
    s = str(s or "").strip()
    if len(s) < 16:
        return None
    try:
        return datetime.fromisoformat(s[:19])
    except ValueError:
        try:
            return datetime.fromisoformat(s[:16])
        except ValueError:
            return None


def _txt(t: datetime | None) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S") if t else ""


def _num(v) -> float | None:
    try:
        return float(str(v).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def coord_valida(lat, lon) -> bool:
    return lat is not None and lon is not None and -34.5 <= lat <= 5.5 and -74.5 <= lon <= -28.5 \
        and not (lat == 0 and lon == 0)


def distancia_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distância em metros (haversine)."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(min(1.0, a)))


# ── histórico de posições ────────────────────────────────────────────
def caminho_posicoes() -> Path:
    """data/entregas_posicoes.db, ao lado do banco principal (lido na hora:
    os testes apontam database.DB_PATH para um banco temporário)."""
    from app import database
    return Path(database.DB_PATH).with_name("entregas_posicoes.db")


def pasta_arquivo() -> Path:
    """Arquivos mensais compactados das entregas e posições com mais de 6 meses."""
    from app import database
    return Path(database.DB_PATH).parent / "entregas" / "arquivo"


@contextmanager
def conexao_posicoes():
    """Conexão com o banco do histórico de posições (arquivo próprio — ver
    o cabeçalho do módulo). Mesmo padrão de app.database.get_db: WAL, espera
    de 5 s pela trava, commit no fim e rollback em erro."""
    caminho = caminho_posicoes()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    cx = sqlite3.connect(str(caminho), check_same_thread=False)
    try:
        cx.execute("PRAGMA busy_timeout=5000")
        cx.execute("PRAGMA journal_mode=WAL")
        cx.execute("PRAGMA synchronous=NORMAL")
        garantir_tabela(cx)
        yield cx
        cx.commit()
    except Exception:
        cx.rollback()
        raise
    finally:
        cx.close()


def garantir_tabela(cx) -> None:
    """Tabela do histórico, no banco de conexao_posicoes()."""
    # execute (não executescript): executescript faz COMMIT da transação em
    # andamento de quem chamou
    cx.execute("""
        CREATE TABLE IF NOT EXISTS entregas_posicoes (
            placa     TEXT NOT NULL,
            em        TEXT NOT NULL,
            lat       REAL NOT NULL,
            lon       REAL NOT NULL,
            endereco  TEXT NOT NULL DEFAULT '',
            ignicao   TEXT NOT NULL DEFAULT '',
            visto_ate TEXT NOT NULL DEFAULT '',
            temp      REAL,
            PRIMARY KEY (placa, em)
        ) WITHOUT ROWID""")
    cx.execute("CREATE INDEX IF NOT EXISTS ix_entregas_posicoes_em ON entregas_posicoes(em)")
    colunas = {r[1] for r in cx.execute("PRAGMA table_info(entregas_posicoes)")}
    if "visto_ate" not in colunas:
        cx.execute("ALTER TABLE entregas_posicoes ADD COLUMN visto_ate TEXT NOT NULL DEFAULT ''")
    if "temp" not in colunas:
        cx.execute("ALTER TABLE entregas_posicoes ADD COLUMN temp REAL")


def _desligada(ignicao) -> bool:
    return str(ignicao or "").strip().lower().startswith("deslig")


def _ligada(ignicao) -> bool:
    return str(ignicao or "").strip().lower().startswith("lig")


def _mesma_temp(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) < VARIACAO_TEMP_C


def registrar_historico(db, posicoes: dict, visto_em: str = "") -> int:
    """Grava as comunicações da grade das placas com viagem em aberto, no
    banco do histórico (conexao_posicoes). `db` = banco principal (viagens
    abertas); `posicoes` = ravex_posicoes.extrair(): {placa: {lat, lon, em,
    em_real, endereco, ignicao, temp}}; `visto_em` = horário da grade.
    Posição sem o horário do rastreador fica de fora (o horário da grade não
    mede parada).

    - comunicação repetida (mesma placa e horário: o rastreador não comunicou
      entre duas grades) não duplica. Com a ignição desligada, o horário da
      grade vai para `visto_ate`: o rastreador de veículo estacionado
      comunica menos, e sem isso a parada não cresce (revisão 15/11/2026).
      Com a ignição ligada não estende — pode ser veículo andando sem sinal;
    - comunicação nova no mesmo ponto da linha anterior (até MESMO_PONTO_M),
      com a mesma ignição e a mesma temperatura (variação menor que
      VARIACAO_TEMP_C), até AGRUPAR_ATE_MIN depois dela: estende o
      `visto_ate` da linha anterior em vez de gravar outra (16/11/2026). O
      veículo esteve ali até aquele horário, então a parada mede o mesmo.
    Devolve quantas comunicações foram consideradas."""
    if not posicoes:
        return 0
    # viagem aberta acompanhada pelo painel (até DIAS_MAX_ATRAS): a que ficou
    # "aberta" esquecida na Ravex não grava posição por meses (revisão 15/11)
    from app.services.entregas_ravex import DIAS_MAX_ATRAS, _hoje
    desde = (_hoje() - timedelta(days=DIAS_MAX_ATRAS)).isoformat()
    try:
        abertas = {normalize_plate(r[0]) for r in db.execute(
            "SELECT DISTINCT placa FROM entregas_viagens WHERE aberta=1 AND ausente_desde='' AND dia>=?", (desde,))}
    except Exception:
        return 0   # módulo de entregas ainda sem tabelas neste banco
    candidatas = [(p, d) for p, d in posicoes.items() if p in abertas and d.get("em_real")]
    if not candidatas:
        return 0
    junta_seg = AGRUPAR_ATE_MIN * 60
    with conexao_posicoes() as cx:
        for p, d in candidatas:
            em = d["em"]
            desligada = _desligada(d.get("ignicao"))
            visto = visto_em if visto_em and desligada and visto_em > em else ""
            temp = d.get("temp")
            ult = cx.execute("SELECT em, lat, lon, ignicao, temp, visto_ate FROM entregas_posicoes "
                             "WHERE placa=? ORDER BY em DESC LIMIT 1", (p,)).fetchone()
            if ult is not None and em == ult[0]:
                # mesma comunicação vista de novo
                if visto and visto > ult[5]:
                    cx.execute("UPDATE entregas_posicoes SET visto_ate=? WHERE placa=? AND em=?", (visto, p, em))
                continue
            if ult is not None and em > ult[0]:
                t0, t1 = _dt(ult[0]), _dt(em)
                if t0 and t1 and (t1 - t0).total_seconds() < junta_seg \
                        and _desligada(ult[3]) == desligada and _mesma_temp(ult[4], temp) \
                        and distancia_m(ult[1], ult[2], d["lat"], d["lon"]) <= MESMO_PONTO_M:
                    novo = max(ult[5], em, visto)
                    if novo > ult[5]:
                        cx.execute("UPDATE entregas_posicoes SET visto_ate=? WHERE placa=? AND em=?", (novo, p, ult[0]))
                    continue
            # primeira linha da placa, ponto novo, ou comunicação fora de ordem
            cx.execute("""INSERT INTO entregas_posicoes(placa, em, lat, lon, endereco, ignicao, visto_ate, temp)
                          VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(placa, em) DO NOTHING""",
                       (p, em, d["lat"], d["lon"], d.get("endereco") or "", d.get("ignicao") or "", visto, temp))
    return len(candidatas)


def _fim_dos_blocos_inteiros(caminho: Path) -> int:
    """Tamanho do arquivo até o último bloco gzip inteiro. Um bloco pela
    metade no fim (disco cheio ou VM desligada no meio da gravação) faria o
    gzip.open parar nele e esconder os blocos gravados depois (revisão de
    16/11/2026). As linhas desse bloco seguem no banco: a exclusão só é
    confirmada depois da gravação do arquivo."""
    import zlib
    bom = 0
    with open(caminho, "rb") as f:
        while True:
            f.seek(bom)
            d = zlib.decompressobj(16 + zlib.MAX_WBITS)
            lidos = 0
            sobra = b""
            try:
                while not d.eof:
                    pedaco = f.read(1 << 16)
                    if not pedaco:
                        break
                    lidos += len(pedaco)
                    d.decompress(pedaco)                # saída descartada: confere o CRC do bloco
                sobra = d.unused_data
            except zlib.error:
                return bom
            if not d.eof:
                return bom                              # bloco interrompido no fim do arquivo
            bom += lidos - len(sobra)
            if not sobra and not f.read(1):
                return bom


def _anexar_arquivo(nome: str, linhas: list[dict]) -> None:
    """Acrescenta as linhas (JSON, uma por linha) ao arquivo mensal
    compactado. Cada chamada grava um bloco gzip novo no fim do arquivo —
    gzip.open e zcat leem os blocos em sequência como um arquivo só. Antes,
    descarta um bloco pela metade deixado por uma gravação interrompida; se
    esta gravação falhar, o arquivo volta ao tamanho anterior."""
    if not linhas:
        return
    pasta = pasta_arquivo()
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / nome
    dados = gzip.compress("".join(json.dumps(x, ensure_ascii=False, default=str) + "\n"
                                  for x in linhas).encode("utf-8"), 6)
    fd = os.open(caminho, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    try:
        tam = os.lseek(fd, 0, os.SEEK_END)
        bom = _fim_dos_blocos_inteiros(caminho) if tam else 0
        if bom < tam:
            os.ftruncate(fd, bom)
        os.lseek(fd, bom, os.SEEK_SET)
        try:
            vista = memoryview(dados)
            while vista:
                vista = vista[os.write(fd, vista):]
            os.fsync(fd)
        except BaseException:
            try:
                os.ftruncate(fd, bom)
                os.fsync(fd)
            except OSError:
                pass
            raise
    finally:
        os.close(fd)


def arquivar(prefixo: str, por_mes: dict[str, list[dict]]) -> None:
    """{'AAAA-MM': [linhas]} → data/entregas/arquivo/<prefixo>_AAAA-MM.jsonl.gz"""
    for mes, linhas in sorted(por_mes.items()):
        if re.fullmatch(r"\d{4}-\d{2}", mes or ""):
            _anexar_arquivo(f"{prefixo}_{mes}.jsonl.gz", linhas)


_RE_ARQUIVO = re.compile(r"^[a-z]+_(\d{4})-(\d{2})\.jsonl\.gz$")


def podar_arquivo(agora: datetime) -> int:
    """Apaga os arquivos mensais com mais de RETENCAO_DIAS + MESES_ARQUIVO
    (6 + 18 = 24 meses): o mês inteiro do arquivo já passou do prazo."""
    pasta = pasta_arquivo()
    if not pasta.is_dir():
        return 0
    meses = round(RETENCAO_DIAS / 30) + MESES_ARQUIVO
    ref = agora.year * 12 + agora.month - 1 - meses       # primeiro mês que ainda fica
    n = 0
    for f in pasta.iterdir():
        m = _RE_ARQUIVO.match(f.name)
        if m and int(m.group(1)) * 12 + int(m.group(2)) - 1 < ref:
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
    return n


def limpar_historico(agora: datetime) -> int:
    """Posições com mais de RETENCAO_DIAS vão para o arquivo mensal
    compactado e saem do banco do histórico. Grava o arquivo antes de
    apagar: uma falha no meio repete o bloco no arquivo, sem perder linha."""
    corte = _txt(agora - timedelta(days=RETENCAO_DIAS))
    with conexao_posicoes() as cx:
        por_mes: dict[str, list[dict]] = {}
        for placa, em, la, lo, en, ig, va, tp in cx.execute(
                "SELECT placa, em, lat, lon, endereco, ignicao, visto_ate, temp FROM entregas_posicoes "
                "WHERE em < ? ORDER BY em", (corte,)):
            por_mes.setdefault(em[:7], []).append({"placa": placa, "em": em, "lat": la, "lon": lo, "endereco": en,
                                                   "ignicao": ig, "visto_ate": va, "temp": tp})
        if not por_mes:
            return 0
        arquivar("posicoes", por_mes)
        return cx.execute("DELETE FROM entregas_posicoes WHERE em < ?", (corte,)).rowcount


def placa_chave(p) -> str:
    return normalize_plate(p or "")


instante = _dt     # 'YYYY-MM-DD HH:MM[:SS]' → datetime (None se vazio/inválido)
texto = _txt       # datetime → 'YYYY-MM-DD HH:MM:SS'


def janela_viagem(v: dict, notas: list[dict], agora: datetime, limite: datetime | None):
    return _janela_viagem(v, notas, agora, limite)


_SQL_HIST = ("SELECT em, lat, lon, endereco, visto_ate, temp, ignicao FROM entregas_posicoes "
             "WHERE placa=? AND em>=? AND em<=? ORDER BY em")


def _ler_hist(cx, placa: str, desde: str, ate: str) -> list[dict]:
    cur = cx.cursor()
    cur.row_factory = None      # tuplas: milhares de linhas por leitura do painel
    return [{"em": em, "lat": la, "lon": lo, "endereco": en, "visto_ate": va, "temp": tp, "ignicao": ig}
            for em, la, lo, en, va, tp, ig in cur.execute(_SQL_HIST, (placa, desde, ate))]


def historico_placa(placa: str, desde: str, ate: str = "9999", cx=None) -> list[dict]:
    """Comunicações da placa no intervalo, do banco do histórico (abre a
    conexão quando `cx` não vem)."""
    if cx is None:
        with conexao_posicoes() as cx2:
            return _ler_hist(cx2, placa_chave(placa), desde, ate)
    return _ler_hist(cx, placa_chave(placa), desde, ate)


def _inicio_leitura(ini: datetime) -> str:
    """A linha que começou até AGRUPAR_ATE_MIN antes da janela pode seguir
    dentro dela pelo `visto_ate` (gravação enxuta): a leitura começa antes."""
    return _txt(ini - timedelta(minutes=AGRUPAR_ATE_MIN))


# Resultado da PNP por viagem, guardado em memória (por processo do
# gunicorn). Revisão de 15/11/2026: com 100 placas e o período "7 dias" o
# painel refazia a PNP de todas as viagens a cada minuto (3–7 s por leitura
# num worker síncrono). A chave leva tudo de que o resultado depende — janela
# da viagem, quantidade e última comunicação nela (e o último visto_ate),
# parâmetros, notas e CDs —, então parâmetro novo, comunicação nova ou baixa
# nova recalculam na hora; viagem encerrada e viagem aberta sem comunicação
# nova saem da memória.
_MEMO: "OrderedDict[tuple, list]" = OrderedDict()
_MEMO_MAX = 20000
_MEMO_TRAVA = threading.Lock()


def _assinatura_notas(notas: list[dict]) -> int:
    return hash(tuple((n.get("cod_cliente"), n.get("cliente"), n.get("numero_nf"), n.get("lat_ref"), n.get("lon_ref"),
                       n.get("lat_ent"), n.get("lon_ent"), n.get("inicio_entrega"), n.get("fim_entrega"),
                       n.get("primeira_pos_raio"), n.get("ultima_pos_raio")) for n in notas))


def assinatura_cds(cds: list[dict]) -> int:
    return hash(tuple((c["sigla"], c["lat"], c["lon"], c["raio_m"]) for c in cds))


def pnp_da_viagem(cx, v: dict, notas: list[dict], cds: list[dict], par: dict, agora: datetime,
                  limite: datetime | None = None, cds_sig: int | None = None) -> list[dict]:
    """PNPs da viagem para o painel: lê do histórico (`cx` =
    conexao_posicoes()) só a janela da viagem (uma consulta pelo índice
    placa+horário) e reaproveita o resultado anterior quando nada de que ele
    depende mudou. Devolve a lista guardada: quem chama não altera."""
    ini, fim, ate_agora = _janela_viagem(v, notas, agora, limite)
    placa = placa_chave(v.get("placa"))
    if ini is None or not placa:
        return []
    de_s, ate_s = _inicio_leitura(ini), _txt(fim)
    n, ult, vis = cx.execute("SELECT COUNT(*), MAX(em), MAX(visto_ate) FROM entregas_posicoes "
                             "WHERE placa=? AND em>=? AND em<=?", (placa, de_s, ate_s)).fetchone()
    if not n:
        return []
    chave = (v.get("id_viagem"), placa, de_s, "agora" if ate_agora else ate_s, n, ult, vis,
             int(par.get("pnp_min") or PNP_MIN_PADRAO), float(par.get("pnp_raio_m") or PNP_RAIO_PADRAO_M),
             _assinatura_notas(notas), assinatura_cds(cds) if cds_sig is None else cds_sig)
    with _MEMO_TRAVA:
        r = _MEMO.get(chave)
        if r is not None:
            _MEMO.move_to_end(chave)
            return ainda_parado(r, agora)
    amostras = _ler_hist(cx, placa, de_s, ate_s)
    r = analisar_viagem(v, notas, amostras, cds, par, agora, limite)["pnp"]
    with _MEMO_TRAVA:
        _MEMO[chave] = r
        while len(_MEMO) > _MEMO_MAX:
            _MEMO.popitem(last=False)
    return r


def _parado_recente(p: dict, agora: datetime) -> bool:
    fim = _dt(p.get("fim"))
    return fim is not None and (agora - fim).total_seconds() <= POSICAO_VALIDA_MIN * 60


def ainda_parado(pnps: list[dict], agora: datetime) -> list[dict]:
    """"Parado agora" exige comunicação recente (revisão 16/11/2026: viagem
    aberta com a última comunicação de 5 h antes aparecia como parada em
    andamento). A memória guarda a lista pela chave "agora", então a idade
    é conferida a cada leitura; devolve lista nova quando muda algo."""
    if not any(p.get("em_andamento") and not _parado_recente(p, agora) for p in pnps):
        return pnps
    return [{**p, "em_andamento": False} if p.get("em_andamento") and not _parado_recente(p, agora) else p
            for p in pnps]


def cds_cadastrados(db=None) -> list[dict]:
    """CDs do cadastro do master (latitude, longitude e raio de chegada):
    parada no raio de um CD não é PNP; o CD da unidade é o local do início
    quando ainda não há posição registrada. Com `db`, lê pela mesma conexão
    de quem chama: get_cds_localizacao() abre outra e pode precisar migrar
    a tabela (ALTER), o que trava atrás da transação de escrita do painel
    (registrar_acesso)."""
    if db is not None:
        try:
            linhas = [dict(r) for r in db.execute(
                "SELECT sigla, latitude, longitude, raio_chegada_km FROM cds_localizacao")]
        except Exception:
            try:
                linhas = [dict(r) for r in db.execute("SELECT sigla, latitude, longitude FROM cds_localizacao")]
            except Exception:
                linhas = []      # cadastro de CDs ainda não criado neste banco
    else:
        from app.database import get_cds_localizacao
        linhas = get_cds_localizacao()
    out = []
    for c in linhas:
        la, lo = _num(c.get("latitude")), _num(c.get("longitude"))
        if not coord_valida(la, lo):
            continue
        raio = _num(c.get("raio_chegada_km")) or RAIO_CD_PADRAO_KM
        out.append({"sigla": str(c.get("sigla") or "").strip().upper(), "lat": la, "lon": lo,
                    "raio_m": max(raio, 0.05) * 1000})
    return out


# ── paradas ──────────────────────────────────────────────────────────
def paradas(amostras: list[dict]) -> list[dict]:
    """Agrupa comunicações seguidas no mesmo lugar (até RAIO_MESMO_LUGAR_M
    do centro do grupo). Duração = da primeira à última comunicação do
    grupo — a parada real é igual ou maior (o veículo chegou entre a
    comunicação anterior e a primeira do grupo). Comunicação com `visto_ate`
    (ignição desligada, a grade seguiu mostrando o veículo ali) estende o
    fim até esse horário, sem passar da comunicação seguinte em outro lugar."""
    # horários comparados como texto 'YYYY-MM-DD HH:MM:SS' (mesma ordem do
    # tempo); só o início e o fim de cada grupo viram datetime no final
    grupos: list[dict] = []
    g = None
    graus = RAIO_MESMO_LUGAR_M / 110_000      # 1° de latitude ≥ 110,5 km (descarte rápido, sem trigonometria)
    for a in amostras:
        t = str(a.get("em") or "")[:19]
        if len(t) < 16 or not coord_valida(a.get("lat"), a.get("lon")):
            continue
        visto = str(a.get("visto_ate") or "")[:19]
        ate = visto if visto > t else t
        if g and abs(a["lat"] - g["lat"]) <= graus \
                and distancia_m(g["lat"], g["lon"], a["lat"], a["lon"]) <= RAIO_MESMO_LUGAR_M:
            g["n"] += 1
            g["lat"] += (a["lat"] - g["lat"]) / g["n"]
            g["lon"] += (a["lon"] - g["lon"]) / g["n"]
            if ate > g["fim"]:
                g["fim"] = ate
            if not g["endereco"] and a.get("endereco"):
                g["endereco"] = a["endereco"]
        else:
            if g and g["fim"] > t:
                g["fim"] = t   # saiu antes da comunicação seguinte, em outro lugar
            g = {"inicio": t, "fim": ate, "lat": a["lat"], "lon": a["lon"], "n": 1, "endereco": a.get("endereco") or ""}
            grupos.append(g)
    out = []
    for g in grupos:
        g["inicio"], g["fim"] = _dt(g["inicio"]), _dt(g["fim"])
        if g["inicio"] is not None and g["fim"] is not None:
            out.append(g)
    return out


def _janela_viagem(v: dict, notas: list[dict], agora: datetime, limite: datetime | None):
    """(início, fim, até_agora) da viagem para olhar o histórico. Viagem em
    aberto vai até agora; encerrada sem fim real vai até a última baixa.
    `limite` = início da viagem seguinte da mesma placa (viagem que ficou
    aberta na Ravex não pega as paradas da próxima). `até_agora` = a janela
    chega ao momento atual (só aí a última parada é "parado agora")."""
    ini = _dt(v.get("inicio_real"))
    if ini is None:
        return None, None, False
    fim = _dt(v.get("fim_real"))
    ate_agora = False
    if fim is None:
        if v.get("aberta"):
            fim, ate_agora = agora, True
        else:
            fins = [t for t in (_dt(n.get("fim_entrega")) for n in notas) if t]
            fim = max(fins) if fins else None
    if fim is not None and limite is not None and limite > ini and limite < fim:
        fim, ate_agora = limite, False
    if fim is None or fim < ini:
        return None, None, False
    return ini, fim, ate_agora


def _clientes(notas: list[dict]) -> tuple[list[tuple], list[tuple]]:
    """Locais e janelas de horário que contam como "no cliente":
    - coordenada de referência do cliente (lat_ref/lon_ref);
    - local da baixa (lat_ent/lon_ent): a entrega feita longe da referência
      (baixa fora do raio) não vira também parada não prevista (achado da
      revisão de 15/11/2026);
    - baixa sem local (sem GPS): o horário da entrega (início → fim);
    - cliente sem coordenada: a janela em que a Ravex viu o veículo dentro
      do raio dele."""
    coords, extras, janelas, sem = {}, [], [], []
    for n in notas:
        chave = n.get("cod_cliente") or n.get("cliente") or n.get("numero_nf")
        la, lo = n.get("lat_ref"), n.get("lon_ref")
        if coord_valida(la, lo):
            coords.setdefault(chave, (la, lo))
        else:
            sem.append((chave, n))
        le, oe = n.get("lat_ent"), n.get("lon_ent")
        if coord_valida(le, oe):
            extras.append((le, oe))
        else:
            a, b = _dt(n.get("inicio_entrega")), _dt(n.get("fim_entrega"))
            if a or b:
                a, b = a or b, b or a
                janelas.append((min(a, b), max(a, b)))
    for chave, n in sem:
        if chave in coords:
            continue
        a, b = _dt(n.get("primeira_pos_raio")), _dt(n.get("ultima_pos_raio"))
        if a or b:
            a, b = a or b, b or a
            janelas.append((min(a, b), max(a, b)))
    return list(coords.values()) + extras, janelas


def analisar_viagem(v: dict, notas: list[dict], amostras: list[dict], cds: list[dict], par: dict,
                    agora: datetime, limite: datetime | None = None, pontas: bool = False,
                    ems: list[str] | None = None) -> dict:
    """PNPs da viagem e, com `pontas`, o local do início e do fim.

    v: linha de entregas_viagens; notas: linhas de entregas_nfs da viagem
    (com lat_ref, lon_ref, lat_ent, lon_ent, inicio_entrega, fim_entrega,
    primeira_pos_raio, ultima_pos_raio); amostras: comunicações da placa
    (historico()), em ordem; ems: os horários das amostras ('YYYY-MM-DD
    HH:MM:SS', mesma ordem) — o painel monta uma vez por placa e o recorte
    da janela vira busca binária; par: parâmetros da coleta (pnp_min,
    pnp_raio_m)."""
    minutos = int(par.get("pnp_min") or PNP_MIN_PADRAO)
    raio_m = float(par.get("pnp_raio_m") or PNP_RAIO_PADRAO_M)
    ini, fim, ate_agora = _janela_viagem(v, notas, agora, limite)
    out: dict = {"pnp": []}
    if ini is not None:
        # recorte pelo texto 'YYYY-MM-DD HH:MM:SS' (sem converter cada
        # comunicação da placa a cada viagem)
        de_s, ate_s = _inicio_leitura(ini), _txt(fim)
        if ems is None:
            ems = [str(a.get("em") or "")[:19] for a in amostras]
        dentro = amostras[bisect_left(ems, de_s):bisect_right(ems, ate_s)]
        grupos = paradas(dentro)
        clientes, janelas = _clientes(notas)
        for k, g in enumerate(grupos):
            # a linha de antes do início (gravação enxuta) conta a partir dele
            g["inicio"] = max(g["inicio"], ini)
            g["fim"] = min(g["fim"], fim)
            dur = (g["fim"] - g["inicio"]).total_seconds() / 60
            if dur < minutos:
                continue
            if any(distancia_m(g["lat"], g["lon"], la, lo) <= raio_m for la, lo in clientes):
                continue
            if any(distancia_m(g["lat"], g["lon"], c["lat"], c["lon"]) <= c["raio_m"] for c in cds):
                continue
            if any(a <= g["fim"] and b >= g["inicio"] for a, b in janelas):
                continue
            out["pnp"].append({
                "inicio": _txt(g["inicio"]), "fim": _txt(g["fim"]), "minutos": int(dur),
                "lat": round(g["lat"], 6), "lon": round(g["lon"], 6), "endereco": g["endereco"],
                "comunicacoes": g["n"],
                # última parada de viagem em aberto cuja janela chega a agora
                # (não cortada pela viagem seguinte da placa), com comunicação
                # recente: o veículo segue nela (ver ainda_parado)
                "em_andamento": ate_agora and k == len(grupos) - 1
                                and (agora - g["fim"]).total_seconds() <= POSICAO_VALIDA_MIN * 60,
            })
    if pontas:
        out["inicio"] = _ponta(amostras, _dt(v.get("inicio_real")))
        if out["inicio"] is None and _dt(v.get("inicio_real")):
            out["inicio"] = _ponta_cd(v, cds)
        out["fim"] = _ponta(amostras, _dt(v.get("fim_real")))
    return out


def _ponta(amostras: list[dict], quando: datetime | None) -> dict | None:
    """Comunicação mais próxima do horário (até TOLERANCIA_PONTA_MIN)."""
    if quando is None:
        return None
    melhor, dif = None, None
    for a in amostras:
        t = _dt(a.get("em"))
        if t is None or not coord_valida(a.get("lat"), a.get("lon")):
            continue
        # linha que cobre um intervalo (visto_ate, gravação enxuta): distância
        # zero quando o horário cai dentro dele
        t2 = _dt(a.get("visto_ate")) or t
        d = 0.0 if t <= quando <= t2 else min(abs((t - quando).total_seconds()), abs((t2 - quando).total_seconds()))
        if dif is None or d < dif:
            melhor, dif = a, d
    if melhor is None or dif > TOLERANCIA_PONTA_MIN * 60:
        return None
    return {"origem": "posicao", "lat": melhor["lat"], "lon": melhor["lon"], "em": melhor["em"],
            "endereco": melhor.get("endereco") or ""}


def _ponta_cd(v: dict, cds: list[dict]) -> dict | None:
    """Sem posição registrada no início: o CD da unidade, do cadastro."""
    nome = str(v.get("unidade") or "").strip()
    sigla = (nome[len("Marca "):] if nome.lower().startswith("marca ") else nome).strip().upper()
    for c in cds:
        if c["sigla"] and c["sigla"] == sigla:
            return {"origem": "cd", "lat": c["lat"], "lon": c["lon"], "sigla": c["sigla"]}
    return None


def proximos_inicios(db, viagens: list[dict]) -> dict[str, datetime]:
    """{id_viagem: início real da viagem seguinte da mesma placa}, procurado
    no banco — não só nas viagens que passaram pelos filtros da tela (achado
    da revisão de 15/11/2026: com a viagem seguinte fora do filtro, o
    triângulo da lista contava paradas que a janela da viagem não mostrava).
    Painel e janela da viagem usam esta mesma regra."""
    chaves, inis = set(), []
    for v in viagens:
        p, ini = placa_chave(v.get("placa")), str(v.get("inicio_real") or "")
        if p and ini:
            chaves.add(p)
            inis.append(ini)
    if not inis:
        return {}
    por_placa: dict[str, list[datetime]] = {}
    for r in db.execute("SELECT placa, inicio_real FROM entregas_viagens WHERE ausente_desde='' AND inicio_real>?",
                        (min(inis),)):
        p = placa_chave(r[0])
        t = _dt(r[1]) if p in chaves else None
        if t:
            por_placa.setdefault(p, []).append(t)
    for lista in por_placa.values():
        lista.sort()
    out: dict[str, datetime] = {}
    for v in viagens:
        lista = por_placa.get(placa_chave(v.get("placa")))
        t = _dt(v.get("inicio_real"))
        if lista and t:
            k = bisect_right(lista, t)
            if k < len(lista):
                out[v["id_viagem"]] = lista[k]
    return out


# ── veículo no raio do cliente ───────────────────────────────────────
POSICAO_VALIDA_MIN = 60          # ignição desligada (ou sem informação): rastreador parado comunica menos
POSICAO_VALIDA_LIGADA_MIN = 20   # ignição ligada: o rastreador comunica a cada poucos minutos — posição
                                 # mais velha que isso é sinal perdido, não veículo parado (revisão 16/11/2026)
JANELA_RAVEX_MIN = 10            # "última posição dentro do raio" da Ravex recente = ainda no raio
MESMO_ENDERECO_M = 20            # clientes a até 20 m do cliente mais próximo (mesmo endereço) contam juntos


def posicao_valida(posicao: dict | None, agora: datetime) -> tuple[float, float] | None:
    """(lat, lon) da posição atual da grade quando ela ainda vale: horário
    do rastreador (não o da grade) até POSICAO_VALIDA_MIN, ou até
    POSICAO_VALIDA_LIGADA_MIN com a ignição ligada."""
    if not posicao or not coord_valida(posicao.get("lat"), posicao.get("lon")):
        return None
    if posicao.get("em_real") is False:
        return None          # sem o horário do rastreador: idade desconhecida
    t = _dt(posicao.get("em"))
    if t is None:
        return None
    limite = POSICAO_VALIDA_LIGADA_MIN if _ligada(posicao.get("ignicao")) else POSICAO_VALIDA_MIN
    if not -300 <= (agora - t).total_seconds() <= limite * 60:
        return None
    return posicao["lat"], posicao["lon"]


def marcar_no_raio(entregas: list[dict], notas: list[dict], posicao: dict | None, aberta: bool,
                   agora: datetime, raio_m: float) -> None:
    """Marca `no_raio` nas entregas ainda não atendidas com o veículo no raio
    do cliente — bolinha cinza com anel âmbar pulsando (pedido do usuário
    15/11/2026; em atendimento = bolinha âmbar). O veículo está em um lugar
    só: marca um cliente (e quem divide o endereço com ele).

    1. Cliente sem coordenada: vale a "última posição dentro do raio" da
       própria Ravex nos últimos JANELA_RAVEX_MIN minutos. Quando a Ravex
       aponta o veículo num cliente, ele é o local (revisão 16/11/2026: o
       vizinho a 300 m com coordenada acendia junto).
    2. Senão, a posição atual da grade (posicao_valida) a até `raio_m` (o
       raio do cliente dos Parâmetros, o mesmo da PNP): o cliente com
       coordenada mais próximo do veículo (empate: a menor sequência) e os
       clientes a até MESMO_ENDERECO_M dele. Revisão 16/11/2026: antes a
       folga de 20 m era medida até o veículo, e dois clientes a 300 m um do
       outro, com o veículo no meio, acendiam juntos."""
    for e in entregas:
        e["no_raio"] = False
    if not aberta:
        return
    coords: dict = {}
    janelas: dict = {}
    for n in notas:
        chave = n.get("cod_cliente") or n.get("cliente") or n.get("numero_nf")
        la, lo = n.get("lat_ref"), n.get("lon_ref")
        if coord_valida(la, lo):
            coords.setdefault(chave, (la, lo))
        u = _dt(n.get("ultima_pos_raio"))
        if u and (chave not in janelas or u > janelas[chave]):
            janelas[chave] = u
    marcados = {cod for cod, u in janelas.items()
                if cod not in coords and -300 <= (agora - u).total_seconds() <= JANELA_RAVEX_MIN * 60}
    if not marcados:
        atual = posicao_valida(posicao, agora)
        if atual:
            perto = {k: d for k, c in coords.items()
                     if (d := distancia_m(atual[0], atual[1], c[0], c[1])) <= raio_m}
            if perto:
                seq = {e["cliente_cod"]: e["seq"] for e in entregas if e.get("seq") is not None}
                alvo = min(perto, key=lambda k: (round(perto[k]), seq.get(k, 10 ** 9)))
                la, lo = coords[alvo]
                marcados = {k for k in perto if k == alvo or distancia_m(la, lo, *coords[k]) <= MESMO_ENDERECO_M}
    for e in entregas:
        if not (e.get("realizada") or e.get("em_atendimento")):
            e["no_raio"] = e["cliente_cod"] in marcados


# ── sequência ────────────────────────────────────────────────────────
def marcar_fora_de_sequencia(entregas: list[dict], primeira_baixa: list[str]) -> None:
    """Marca `fora_seq` nas entregas feitas fora da sequência programada
    (pedido do usuário 15/11/2026: exclamação na bolinha).

    Ordem realizada = entregas com baixa pela hora da primeira baixa, e
    depois as pendentes pela sequência programada (a próxima esperada).
    A maior subsequência em ordem de sequência programada fica sem marca;
    as demais são as que saíram da ordem. Assim um salto marca só a entrega
    que saltou (1, 5, 2, 3, 4 → marca a 5), e a entrega pulada que segue
    pendente com as seguintes já feitas fica marcada (1, 2, 4, 5 com a 3
    pendente → marca a 3). Entrega sem sequência programada fica de fora.

    Baixa sem horário (achado da revisão de 15/11/2026): com a sequência
    real em todas as feitas, a ordem é a sequência real; sem ela, a entrega
    sem horário fica fora da comparação (sem marca) em vez de ir para o fim."""
    for e in entregas:
        e["fora_seq"] = False
    idx = [i for i, e in enumerate(entregas) if e.get("seq") is not None]
    feitas = [i for i in idx if entregas[i].get("com_baixa")]
    sem_hora = any(not primeira_baixa[i] for i in feitas)
    if sem_hora and all(entregas[i].get("seq_real") is not None for i in feitas):
        feitas.sort(key=lambda i: (entregas[i]["seq_real"], entregas[i]["seq"]))
    else:
        feitas = sorted((i for i in feitas if primeira_baixa[i]),
                        key=lambda i: (primeira_baixa[i],
                                       entregas[i]["seq_real"] if entregas[i].get("seq_real") is not None else 10 ** 9,
                                       entregas[i]["seq"]))
    pendentes = sorted((i for i in idx if not entregas[i].get("com_baixa")), key=lambda i: entregas[i]["seq"])
    ordem = feitas + pendentes
    if len(ordem) < 2:
        return
    caudas, caudas_k, ant = [], [], [None] * len(ordem)
    for k, i in enumerate(ordem):
        s = entregas[i]["seq"]
        pos = bisect_right(caudas, s)          # não decrescente: sequência repetida não marca
        if pos > 0:
            ant[k] = caudas_k[pos - 1]
        if pos == len(caudas):
            caudas.append(s)
            caudas_k.append(k)
        else:
            caudas[pos] = s
            caudas_k[pos] = k
    manter, k = set(), caudas_k[-1]
    while k is not None:
        manter.add(k)
        k = ant[k]
    for k, i in enumerate(ordem):
        if k not in manter:
            entregas[i]["fora_seq"] = True
