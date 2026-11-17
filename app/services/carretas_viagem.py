"""
Viagem e presença das carretas no CD — base do painel de carretas
(tempo de descarga, aviso de chegada pelo Telegram). Pedido de 18/10/2026.

Duas peças:

1. ID DA VIAGEM. A OpenTech traz a "Aut. de embarque" (AE): 8 dígitos, uma por
   carregamento, sequencial, nunca se repete. A Ravex não tem equivalente, então
   a viagem ganha um ID fictício "RVX-000001", sequencial, amarrado às NOTAS
   FISCAIS do card (decisão do usuário, 18/10/2026, até entender o número de
   viagem da Ravex). Nos dois casos o ID é gravado por (placa, NF) na primeira
   vez em que o card aparece e fica CONGELADO: depois que a carreta chega, a
   OpenTech pode já listar a AE da PRÓXIMA carga, e reler a AE "atual" trocaria
   o ID da viagem que está descarregando.

2. PRESENÇA NO CD. O horário de chegada é o do NOSSO sistema, não o da OpenTech
   (decisão do usuário, 18/10/2026: o relatório deles não é confiável para
   isso). A cada ciclo do rastreio (~20 min), o veículo dentro do raio do CD
   abre uma ESTADIA; ao sair, ela fecha. Roda no cron do rastreio, para todas
   as placas, sem depender de alguém estar com o painel aberto (os cards ficam
   na memória do worker e só são refeitos para quem está ativo — MEL-15).

   Saiu e voltou na mesma viagem = duas estadias da mesma VISITA. O tempo de
   descarga conta da PRIMEIRA chegada, e o tempo fora aparece à parte.

O raio é cadastrado por CD em Configurações → Localizações dos CDs
(`cds_localizacao.raio_chegada_km`); vazio vale RAIO_PADRAO_KM.
CDA e CDB dividem o endereço: a estadia é gravada no LOCAL consolidado
(`empresa_consolidada`, CDB→CDA), senão a mesma carreta abriria duas.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.database import get_db
from app.utils.text_utils import normalize_plate

_BRT = ZoneInfo("America/Sao_Paulo")
_FMT = "%Y-%m-%d %H:%M:%S"

# Raio de chegada quando o CD não tem um cadastrado (escolha do usuário,
# 18/10/2026). O Expedito já usava 1 km para "no pátio".
RAIO_PADRAO_KM = 1.0
# Histerese: entra a 1 km, só sai passando de 1,5 km. Sem isso o GPS
# oscilando na borda do raio vira "saiu e voltou" a cada ciclo.
FATOR_SAIDA = 1.5
# Posição da OpenTech mais velha que isso não vale: no relatório real há
# rastreador parado desde 2023 dentro do raio do CD (18/10/2026).
POSICAO_VALIDA_H = 6
# Estadia sem nenhuma posição válida por mais que isso fecha na última vez em
# que o veículo foi visto, como "sem sinal" (não como saída).
LACUNA_H = 12
# Sem AE (Ravex): voltar ao CD dentro desse prazo conta como a mesma visita.
VOLTA_H = 12

_tabelas_ok: set = set()   # por arquivo de banco (os testes usam outro)


def _agora() -> datetime:
    return datetime.now(_BRT).replace(tzinfo=None, microsecond=0)


def _txt(dt: datetime) -> str:
    return dt.strftime(_FMT)


def _dt(s) -> datetime | None:
    try:
        return datetime.strptime(str(s)[:19], _FMT)
    except (TypeError, ValueError):
        return None


def garantir_tabelas(db) -> None:
    from app import database
    chave = str(database.DB_PATH)
    if chave in _tabelas_ok:
        return
    db.executescript("""
        CREATE TABLE IF NOT EXISTS carretas_viagem_nf (
            placa     TEXT NOT NULL,
            nf        TEXT NOT NULL,
            viagem    TEXT NOT NULL,
            criado_em TEXT NOT NULL,
            PRIMARY KEY (placa, nf)
        );
        CREATE INDEX IF NOT EXISTS idx_carretas_viagem_nf_viagem
            ON carretas_viagem_nf(viagem);

        CREATE TABLE IF NOT EXISTS carretas_rvx (
            seq       INTEGER PRIMARY KEY AUTOINCREMENT,
            placa     TEXT NOT NULL,
            nfs       TEXT NOT NULL,
            criado_em TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS carretas_estadia (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            placa         TEXT NOT NULL,
            placa_carreta TEXT NOT NULL DEFAULT '',
            placa_veiculo TEXT NOT NULL DEFAULT '',
            local         TEXT NOT NULL,
            fonte         TEXT NOT NULL DEFAULT '',
            ae            TEXT NOT NULL DEFAULT '',
            visita        INTEGER,
            origem        TEXT NOT NULL DEFAULT 'entrada',
            chegada_em    TEXT NOT NULL,
            visto_ultimo  TEXT NOT NULL,
            saida_em      TEXT,
            motivo_saida  TEXT NOT NULL DEFAULT '',
            dist_km       REAL
        );
        CREATE INDEX IF NOT EXISTS idx_carretas_estadia_placa
            ON carretas_estadia(placa, local, chegada_em);
        CREATE INDEX IF NOT EXISTS idx_carretas_estadia_carreta
            ON carretas_estadia(placa_carreta);
        CREATE INDEX IF NOT EXISTS idx_carretas_estadia_veiculo
            ON carretas_estadia(placa_veiculo);
        CREATE INDEX IF NOT EXISTS idx_carretas_estadia_aberta
            ON carretas_estadia(saida_em);
        CREATE INDEX IF NOT EXISTS idx_carretas_estadia_visita
            ON carretas_estadia(visita);
    """)
    _tabelas_ok.add(chave)


# ---------------------------------------------------------------------------
# Locais (CDs consolidados) com coordenada e raio
# ---------------------------------------------------------------------------

def _num(v) -> float | None:
    try:
        x = float(str(v).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def distancia_km(lat1, lon1, lat2, lon2) -> float | None:
    """Distância em linha reta (haversine). O KM do card multiplica por 1,25
    para estimar estrada; para "está no pátio" a reta é a régua certa."""
    a, b, c, d = _num(lat1), _num(lon1), _num(lat2), _num(lon2)
    if None in (a, b, c, d) or not (-90 <= a <= 90 and -90 <= c <= 90):
        return None
    if a == 0 and b == 0:
        return None
    p1, p2 = math.radians(a), math.radians(c)
    dp, dl = p2 - p1, math.radians(d - b)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.atan2(math.sqrt(h), math.sqrt(1 - h))


def local_de(sigla: str, mapa: dict | None = None) -> str:
    """Sigla → local físico (empresa consolidada). CDB → CDA."""
    s = str(sigla or "").strip().upper()
    if not s:
        return ""
    if mapa is None:
        from app.database import get_mapa_consolidacao
        mapa = get_mapa_consolidacao()
    return mapa.get(s, s)


def locais_cadastrados() -> dict:
    """{local: {"lat", "lon", "raio"}} — um ponto por local físico. Se CDA e
    CDB tiverem coordenadas próprias, vale a do próprio local consolidado."""
    from app.database import get_cds_localizacao, get_mapa_consolidacao
    mapa = get_mapa_consolidacao()
    locais: dict = {}
    for cd in get_cds_localizacao():
        sigla = str(cd.get("sigla", "")).strip().upper()
        lat, lon = _num(cd.get("latitude")), _num(cd.get("longitude"))
        if not sigla or lat is None or lon is None:
            continue
        local = mapa.get(sigla, sigla)
        raio = _num(cd.get("raio_chegada_km"))
        item = {"lat": lat, "lon": lon,
                "raio": raio if raio and raio > 0 else RAIO_PADRAO_KM}
        if local not in locais or sigla == local:
            locais[local] = item
    return locais


# ---------------------------------------------------------------------------
# Presença: chamada pelo cron do rastreio a cada ciclo
# ---------------------------------------------------------------------------

def _posicao_em(valor) -> datetime | None:
    """'Última posição' da OpenTech = data/hora do GPS ("18/10/2026 16:41:00").
    Na Ravex o mesmo campo é texto de lugar — devolve None (sem idade)."""
    s = str(valor or "").strip()
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def _veiculos(df) -> list[dict]:
    """Uma linha por veículo físico. A OpenTech duplica o veículo (cruzamento
    pela carreta e pelo cavalo); aqui ele aparece uma vez só."""
    def _p(v):
        p = normalize_plate(v if isinstance(v, str) else "")
        return "" if p in ("NAN", "NONE") else p   # retrato antigo, de antes do conserto do parser

    if "Tipo Cruzamento" in df.columns:
        df = df.sort_values("Tipo Cruzamento", key=lambda s: s.ne("Carreta"), kind="stable")
    vistos: dict = {}
    for _, r in df.iterrows():
        carreta = _p(r.get("Placa Carreta", ""))
        veiculo = _p(r.get("Placa Veículo", ""))
        chave = carreta or veiculo
        if not chave or chave in vistos:
            continue
        ae = str(r.get("AE", "") or "").strip()
        if ae.lower() in ("nan", "none"):
            ae = ""
        vistos[chave] = {
            "placa": chave, "carreta": carreta, "veiculo": veiculo,
            "lat": r.get("Latitude", ""), "lon": r.get("Longitude", ""),
            "ae": ae, "pos_em": _posicao_em(r.get("Última Posição", "")),
        }
    return list(vistos.values())


def registrar_presenca(df, fonte: str, agora: datetime | None = None) -> dict:
    """Abre e fecha estadias a partir do retrato do rastreio que acabou de
    chegar. `fonte` é "OpenTech" ou "Ravex": cada cron só mexe nas estadias
    da própria fonte, senão o ciclo da Ravex fecharia as da OpenTech.
    Devolve contagens (para o log do cron)."""
    res = {"abertas": 0, "fechadas": 0, "atualizadas": 0, "novas_visitas": []}
    if df is None or getattr(df, "empty", True):
        return res
    agora = agora or _agora()
    locais = locais_cadastrados()
    if not locais:
        return res
    agora_s = _txt(agora)
    lacuna = timedelta(hours=LACUNA_H)

    with get_db() as db:
        garantir_tabelas(db)
        # estadias abertas desta fonte, achadas pela chave do veículo OU pelo
        # cavalo: quando a OpenTech conclui a viagem, a linha passa a trazer
        # só o cavalo (sem carreta) e a chave muda — no relatório de 18/10 as
        # 1.870 linhas sem AE vinham todas sem carreta
        abertas: dict = {}
        por_chave: dict = {}
        por_cavalo: dict = {}
        for e in db.execute(
                "SELECT * FROM carretas_estadia WHERE saida_em IS NULL AND fonte=?",
                (fonte,)).fetchall():
            e = dict(e)
            abertas[e["id"]] = e
            por_chave.setdefault(e["placa"], []).append(e["id"])
            if e["placa_veiculo"]:
                por_cavalo.setdefault(e["placa_veiculo"], []).append(e["id"])

        def abertas_do(v) -> list[dict]:
            ids = list(por_chave.get(v["placa"], []))
            if v["veiculo"]:
                ids += [i for i in por_cavalo.get(v["veiculo"], []) if i not in ids]
            return [abertas.pop(i) for i in ids if i in abertas]

        def fechar(e, quando, motivo):
            db.execute("UPDATE carretas_estadia SET saida_em=?, motivo_saida=? WHERE id=?",
                       (quando, motivo, e["id"]))
            res["fechadas"] += 1

        def abrir(v, local, dist, origem, visita=None):
            cur = db.execute(
                """INSERT INTO carretas_estadia
                   (placa, placa_carreta, placa_veiculo, local, fonte, ae, visita,
                    origem, chegada_em, visto_ultimo, dist_km)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (v["placa"], v["carreta"], v["veiculo"], local, fonte, v["ae"],
                 visita, origem, agora_s, agora_s, round(dist, 3)))
            novo = cur.lastrowid
            if visita is None:
                db.execute("UPDATE carretas_estadia SET visita=? WHERE id=?", (novo, novo))
                if origem == "entrada":
                    # chegada de verdade (não troca de AE nem volta do sinal):
                    # o bot de carretas avisa quem recebe esse local
                    res["novas_visitas"].append({
                        "visita": novo, "placa": v["placa"], "carreta": v["carreta"],
                        "veiculo": v["veiculo"], "local": local, "ae": v["ae"],
                        "chegada_em": agora_s, "fonte": fonte})
            res["abertas"] += 1

        for v in _veiculos(df):
            # posição velha não diz nada sobre agora — nem abre nem fecha
            if v["pos_em"] and agora - v["pos_em"] > timedelta(hours=POSICAO_VALIDA_H):
                continue
            dists = {}
            for local, cd in locais.items():
                d = distancia_km(v["lat"], v["lon"], cd["lat"], cd["lon"])
                if d is not None:
                    dists[local] = d
            if not dists:
                continue

            tratadas = abertas_do(v)
            for e in tratadas:
                d = dists.get(e["local"])
                cd = locais.get(e["local"])
                if d is None or cd is None:
                    continue
                visto = _dt(e["visto_ultimo"]) or agora
                sumiu = agora - visto > lacuna
                if d > cd["raio"] * FATOR_SAIDA:
                    fechar(e, e["visto_ultimo"] if sumiu else agora_s,
                           "sem_sinal" if sumiu else "saiu")
                elif e["ae"] and v["ae"] and e["ae"] != v["ae"]:
                    # dentro do pátio com outra AE: a OpenTech já lista a
                    # próxima carga, mas a carreta continua fisicamente aqui —
                    # segue a MESMA visita (a chegada é a de antes). A volta
                    # com a carga nova é que abre visita nova (ver entrada)
                    fechar(e, e["visto_ultimo"] if sumiu else agora_s, "troca_ae")
                    abrir(v, e["local"], d, "troca_ae", visita=e["visita"])
                elif sumiu:
                    fechar(e, e["visto_ultimo"], "sem_sinal")
                    abrir(v, e["local"], d, "sem_sinal", visita=e["visita"])
                else:
                    db.execute(
                        "UPDATE carretas_estadia SET visto_ultimo=?, dist_km=? WHERE id=?",
                        (agora_s, round(d, 3), e["id"]))
                    res["atualizadas"] += 1
                dists.pop(e["local"], None)   # já tratado neste local

            # entrada: o local mais perto cujo raio contém o veículo
            dentro = [(d, local) for local, d in dists.items() if d <= locais[local]["raio"]]
            ja_aberta = db.execute(
                """SELECT 1 FROM carretas_estadia WHERE saida_em IS NULL AND fonte=?
                     AND (placa=? OR (placa_veiculo<>'' AND placa_veiculo=?))""",
                (fonte, v["placa"], v["veiculo"] or "-")).fetchone()
            if not dentro or ja_aberta:
                continue
            d, local = min(dentro)
            ant = db.execute(
                """SELECT * FROM carretas_estadia
                   WHERE (placa=? OR (placa_veiculo<>'' AND placa_veiculo=?)) AND local=?
                     AND saida_em IS NOT NULL
                   ORDER BY chegada_em DESC LIMIT 1""",
                (v["placa"], v["veiculo"] or "-", local)).fetchone()
            visita = None
            if ant is not None:
                # a AE de uma estadia "troca_ae" é a da PRÓXIMA carga, vista no
                # pátio: voltar com ela é viagem nova, não a mesma visita
                mesma_ae = (bool(v["ae"]) and ant["ae"] == v["ae"]
                            and ant["origem"] != "troca_ae")
                sem_ae = not v["ae"] and not ant["ae"]
                recente = (_dt(ant["saida_em"]) or datetime.min) >= agora - timedelta(hours=VOLTA_H)
                # Ravex: RVX novo da placa depois da saída = outra viagem
                rvx_novo = sem_ae and db.execute(
                    "SELECT 1 FROM carretas_rvx WHERE placa=? AND criado_em > ?",
                    (v["placa"], ant["saida_em"])).fetchone() is not None
                if mesma_ae or (sem_ae and recente and not rvx_novo):
                    visita = ant["visita"]
            abrir(v, local, d, "entrada", visita=visita)

        # o que sobrou aberto não teve posição válida neste ciclo (rastreador
        # parado, veículo fora do relatório, cavalo trocou de carreta): passada
        # a lacuna, fecha na última vez em que foi visto
        for e in abertas.values():
            visto = _dt(e["visto_ultimo"]) or agora
            if agora - visto > lacuna:
                fechar(e, e["visto_ultimo"], "sem_sinal")
    return res


def registrar_presenca_do_ciclo(df, fonte: str) -> dict:
    """Versão para o cron: erro aqui não pode marcar o ciclo do rastreio
    como falho. Devolve o resultado para avisar_chegadas_do_ciclo, chamado
    DEPOIS de o painel ir ao disco (o envio ao Telegram é lento)."""
    try:
        r = registrar_presenca(df, fonte)
        if r["abertas"] or r["fechadas"]:
            print(f"[Carretas] {fonte}: {r['abertas']} chegada(s), {r['fechadas']} saída(s) "
                  f"no raio dos CDs")
        return r
    except Exception as e:
        print(f"[Carretas] Erro ao registrar chegada/saída ({fonte}): {e}")
        return {}


def avisar_chegadas_do_ciclo(r: dict, fonte: str) -> None:
    """Aviso de chegada pelo bot de carretas (fase 4): as visitas novas deste
    ciclo e as abertas das últimas horas ainda sem aviso. Falha aqui não
    derruba o ciclo do rastreio."""
    try:
        from app.services.carretas_bot import avisar_novas_chegadas, chegadas_pendentes
        chegadas = {c["visita"]: c for c in (r or {}).get("novas_visitas") or []}
        for c in chegadas_pendentes():
            chegadas.setdefault(c["visita"], c)
        a = avisar_novas_chegadas(list(chegadas.values()))
        if a["avisos"]:
            print(f"[Carretas] {fonte}: {a['avisos']} aviso(s) de chegada, {a['mensagens']} mensagem(ns)")
    except Exception as e:
        print(f"[Carretas] Erro no aviso de chegada ({fonte}): {e}")


# ---------------------------------------------------------------------------
# ID da viagem do card
# ---------------------------------------------------------------------------

def _nfs(texto) -> list[str]:
    s = str(texto or "")
    partes = s.replace(" / ", "/").replace("|", "/").split("/")
    return list(dict.fromkeys(p.strip() for p in partes
                              if p.strip() and p.strip().lower() not in ("nan", "none")))


def resolver_viagens(cards: list[dict], agora: datetime | None = None) -> list[tuple[str, str]]:
    """Para cada card {"placa", "nfs": [...], "ae": AE do rastreio agora,
    "fonte": "OpenTech"|"Ravex"|""} devolve o ID da viagem (AE, RVX-nnnnnn ou "").

    Uma NF já vista com aquela placa mantém o ID que recebeu. NF nova num card
    que já tem ID herda o mesmo (a nota pode entrar num upload posterior). Card
    sem nenhuma NF conhecida: AE do rastreio, ou um RVX novo se for Ravex.
    Sem rastreio ainda fica sem ID — ganha quando a placa aparecer.
    Cada item volta como (viagem, criado_em)."""
    agora_s = _txt(agora or _agora())
    saida = []
    with get_db() as db:
        garantir_tabelas(db)
        for c in cards:
            placa = normalize_plate(c.get("placa", "") or "")
            nfs = [n for n in (c.get("nfs") or []) if n]
            if not placa or not nfs:
                saida.append(("", ""))
                continue
            marca = ",".join("?" * len(nfs))
            ja = db.execute(
                f"SELECT viagem, COUNT(*) n, MIN(criado_em) c FROM carretas_viagem_nf "
                f"WHERE placa=? AND nf IN ({marca}) GROUP BY viagem ORDER BY n DESC, c DESC",
                [placa, *nfs]).fetchall()
            viagem = ja[0]["viagem"] if ja else ""
            criado = ja[0]["c"] if ja else agora_s
            if not viagem:
                ae = str(c.get("ae", "") or "").strip()
                fonte = str(c.get("fonte", "") or "").strip().lower()
                if ae and ae.lower() not in ("nan", "none"):
                    # A OpenTech pode seguir mostrando a AE da carga que já
                    # descarregou e foi embora; congelar essa AE no card da
                    # próxima carga trocaria a viagem. Fica sem ID até a AE nova.
                    ult = db.execute(
                        """SELECT saida_em, motivo_saida FROM carretas_estadia
                           WHERE ae=? AND origem<>'troca_ae'
                           ORDER BY chegada_em DESC LIMIT 1""", (ae,)).fetchone()
                    if not (ult and ult["saida_em"] and ult["motivo_saida"] == "saiu"):
                        viagem = ae
                elif fonte == "ravex":
                    cur = db.execute(
                        "INSERT INTO carretas_rvx (placa, nfs, criado_em) VALUES (?,?,?)",
                        (placa, "/".join(nfs), agora_s))
                    viagem = f"RVX-{cur.lastrowid:06d}"
            if viagem:
                db.executemany(
                    "INSERT OR IGNORE INTO carretas_viagem_nf (placa, nf, viagem, criado_em) "
                    "VALUES (?,?,?,?)", [(placa, n, viagem, agora_s) for n in nfs])
            if viagem and ja:
                c0 = db.execute("SELECT MIN(criado_em) c FROM carretas_viagem_nf WHERE viagem=?",
                                (viagem,)).fetchone()
                criado = (c0["c"] if c0 and c0["c"] else criado)
            saida.append((viagem, criado if viagem else ""))
    return saida


# ---------------------------------------------------------------------------
# Leitura para o card
# ---------------------------------------------------------------------------

def _hm(minutos: float) -> str:
    m = max(0, int(round(minutos)))
    return f"{m // 60}h{m % 60:02d}" if m >= 60 else f"{m} min"


def _estadias_da_placa(db, placa: str, local: str, agora: datetime | None = None) -> list[dict]:
    # janela a partir do mesmo "agora" de quem pergunta (presenca(agora=...))
    rows = db.execute(
        """SELECT * FROM carretas_estadia
           WHERE (placa=? OR placa_carreta=? OR placa_veiculo=?) AND local=?
             AND chegada_em >= ?
           ORDER BY chegada_em""",
        (placa, placa, placa, local,
         _txt((agora or _agora()) - timedelta(days=4)))).fetchall()
    return [dict(r) for r in rows]


def _escolher_visita(estadias: list[dict], viagem: str, agora: datetime,
                     criado_em: str = "") -> list[dict]:
    """Estadias da visita que corresponde ao card. Sem AE para casar, vale a
    visita mais recente que ainda estava em curso quando o card apareceu —
    uma visita encerrada antes disso é de uma viagem anterior da placa."""
    if not estadias:
        return []
    por_visita: dict = {}
    for e in estadias:
        por_visita.setdefault(e["visita"] or e["id"], []).append(e)
    visitas = sorted(por_visita.values(), key=lambda es: es[0]["chegada_em"], reverse=True)
    eh_ae = bool(viagem) and viagem.isdigit()
    if eh_ae:
        for es in visitas:
            # a AE de uma estadia "troca_ae" é a da próxima carga vista no
            # pátio: não identifica a visita dela. Visita encerrada antes de o
            # card existir é de uma passagem anterior com a mesma AE.
            if any(e["ae"] == viagem and e["origem"] != "troca_ae" for e in es):
                fim = es[-1]["saida_em"]
                if fim is None or not criado_em or fim >= criado_em:
                    return es
    limite = _dt(criado_em) or (agora - timedelta(hours=VOLTA_H))
    for es in visitas:
        if eh_ae and any(e["ae"] and e["ae"] != viagem for e in es):
            continue
        fim = es[-1]["saida_em"]
        if fim is None or (_dt(fim) or datetime.min) >= limite:
            return es
        break
    return []


def presenca(placa: str, local: str, viagem: str = "", agora: datetime | None = None,
             estadias: list[dict] | None = None, criado_em: str = "") -> dict:
    """Resumo da visita do card ao CD:
        no_cd        True se a última estadia está aberta
        chegada      primeira chegada da visita ("HH:MM" ou "dd/mm HH:MM")
        chegada_em   ISO da primeira chegada
        desde_min    minutos desde a primeira chegada (até a última saída, se saiu)
        fora_min     minutos fora do raio entre estadias (saiu e voltou)
        idas         lista [{"saiu", "voltou"}] das saídas no meio da visita
        saiu         horário da última saída, se a visita acabou
        sem_sinal    a última posição válida é antiga
    Vazio ({}) se não há visita."""
    agora = agora or _agora()
    placa = normalize_plate(placa or "")
    if not placa or not local:
        return {}
    if estadias is None:
        with get_db() as db:
            garantir_tabelas(db)
            estadias = _estadias_da_placa(db, placa, local, agora)
    es = _escolher_visita(estadias, viagem, agora, criado_em)
    if not es:
        return {}

    def rot(dt):
        return dt.strftime("%H:%M") if dt.date() == agora.date() else dt.strftime("%d/%m %H:%M")

    def dm(dt):   # o card mostra sempre data e hora
        return dt.strftime("%d/%m %H:%M")

    ini = _dt(es[0]["chegada_em"])
    ultima = es[-1]
    aberta = ultima["saida_em"] is None
    fim = agora if aberta else (_dt(ultima["saida_em"]) or agora)
    idas, fora = [], 0.0
    for a, b in zip(es, es[1:]):
        s, v = _dt(a["saida_em"]), _dt(b["chegada_em"])
        if s and v and v > s:
            if a["motivo_saida"] == "saiu":
                fora += (v - s).total_seconds() / 60
                idas.append({"saiu": rot(s), "voltou": rot(v),
                             "saiu_dm": dm(s), "voltou_dm": dm(v),
                             "fora": _hm((v - s).total_seconds() / 60)})
    visto = _dt(ultima["visto_ultimo"]) or agora
    return {
        "no_cd": aberta,
        "chegada": rot(ini),
        "chegada_dm": dm(ini),
        "saiu_dm": "" if aberta else dm(fim),
        "visto_dm": dm(visto),
        "chegada_em": es[0]["chegada_em"],
        "desde_min": round((fim - ini).total_seconds() / 60),
        "desde": _hm((fim - ini).total_seconds() / 60),
        "fora_min": round(fora),
        "fora": _hm(fora) if fora else "",
        "idas": idas,
        "saiu": "" if aberta else rot(fim),
        "sem_sinal": aberta and (agora - visto) > timedelta(hours=2),
        "visto": rot(visto),
    }


def texto_presenca(p: dict, com_espera: bool = True) -> str:
    """Linha curta para o card. A tela do painel monta o "aguardando há" ao
    vivo a partir de "No CD Desde" (o df só é refeito a cada ciclo), por isso
    pode pedir o texto sem ele."""
    if not p:
        return ""
    partes = []
    if p["no_cd"]:
        partes.append(f"No CD desde {p['chegada']}"
                      + (f" · aguardando há {p['desde']}" if com_espera else ""))
    else:
        partes.append(f"Chegou {p['chegada']} · saiu {p['saiu']} ({p['desde']} no CD)")
    for ida in p["idas"]:
        partes.append(f"saiu {ida['saiu']} e voltou {ida['voltou']} ({ida['fora']} fora)")
    if p.get("sem_sinal"):
        partes.append(f"sem posição desde {p['visto']}")
    return " · ".join(partes)


COLUNAS_CARD = ("Viagem", "Presença", "No CD Desde", "Aguardando Min", "Chegada CD",
                "Saída CD", "Idas CD", "Fora CD", "Sem Sinal CD",
                "Descarga Início", "Descarga Término")


def aplicar_no_df(df, usuario: str = "", tipo: str = "coligada"):
    """Acrescenta ao DataFrame dos cards: "Viagem" (AE ou RVX), "Presença"
    (texto corrido), "No CD Desde" e "Aguardando Min", e os campos que a tela
    monta na linha da viagem: "Chegada CD", "Saída CD", "Idas CD" (uma saída
    e retorno por linha, para o tooltip), "Fora CD" e "Sem Sinal CD". Quando a estadia está
    aberta, o card passa a "Veículo no CD" mesmo que o KM arredondado ainda não
    seja 0 — a chegada é a do raio do CD, a mesma que alimenta o tempo de
    descarga. Falha aqui não pode derrubar o painel: devolve o df como veio."""
    try:
        return _aplicar_no_df(df, usuario, tipo)
    except Exception as e:
        print(f"[Carretas] Não consegui aplicar viagem/presença ({tipo}): {e}")
        for col in COLUNAS_CARD:
            if col not in df.columns:
                df[col] = ""
        return df


def _aplicar_no_df(df, usuario, tipo):
    import pandas as pd
    if df is None or df.empty:
        return df
    df = df.copy()
    from app.database import get_cd_usuario, get_mapa_consolidacao
    mapa = get_mapa_consolidacao()
    # local do usuário: o CD da conta canônica; get_cd_usuario não tem sigla
    sigla = ""
    if usuario:
        try:
            from app.utils.access_control import cds_da_conta_canonica
            siglas = cds_da_conta_canonica(usuario)
            sigla = siglas[0] if siglas else ""
        except Exception:
            sigla = ""
        if not sigla:
            cd_usuario = get_cd_usuario(usuario) or {}
            sigla = cd_usuario.get("sigla") or cd_usuario.get("nome") or ""
    meu_local = local_de(sigla, mapa)

    viagens = _viagens_do_df(df, tipo)

    def local_do(r) -> str:
        campo = "Empresa Compra" if tipo == "coligada" else "Unidade"
        unidades = [u.strip().upper() for u in str(r.get(campo, "") or "").split("·") if u.strip()]
        locais = [local_de(u, mapa) for u in unidades]
        if meu_local and (meu_local in locais or not locais):
            return meu_local
        return locais[0] if locais else meu_local

    return _presenca_no_df(df, tipo, viagens, local_do)


def com_viagem(df, tipo: str = "coligada"):
    """Só a coluna "Viagem" (AE/RVX) — as marcações do card (fase 3) são
    lidas pela viagem e precisam dela antes da estimativa e da presença."""
    if df is None or df.empty:
        return df
    try:
        df = df.copy()
        df["Viagem"] = [v for v, _ in _viagens_do_df(df, tipo)]
    except Exception as e:
        print(f"[Carretas] Não consegui resolver a viagem dos cards: {e}")
        if "Viagem" not in df.columns:
            df["Viagem"] = ""
    return df


def _viagens_do_df(df, tipo):
    """(viagem, criado_em) de cada linha do df, na ordem do índice."""
    # AE do rastreio por placa: só para quem não trouxe a coluna (redespacho)
    ae_por_placa = {}
    if "AE" not in df.columns or tipo != "coligada":
        try:
            from app.services import monitoramento_unificado
            mon = monitoramento_unificado.get_df_monitoramento_unificado()
            if mon is not None and not mon.empty and "AE" in mon.columns:
                m = mon[mon["AE"].fillna("").astype(str).str.strip().ne("")]
                ae_por_placa = dict(zip(m["Placa Cruzamento"].fillna("").astype(str).map(normalize_plate),
                                        m["AE"].astype(str).str.strip()))
        except Exception:
            ae_por_placa = {}

    col_nf = "NF" if tipo == "coligada" else "Formulário"

    def chave_card(r) -> str:
        """O card de redespacho junta várias linhas (uma por NF) da mesma
        placa — ou da mesma Instrução Origem, sem placa. A viagem é do card:
        resolver linha a linha dava um RVX por NF."""
        placa = normalize_plate(r.get("Placa", "") or "")
        if tipo == "coligada":
            return f"L{r.name}"
        return placa or ("AGUARDANDO:" + str(r.get("Instrução Origem", "") or "").strip())

    grupos: dict = {}
    for idx, r in df.iterrows():
        k = chave_card(r)
        g = grupos.setdefault(k, {"placa": normalize_plate(r.get("Placa", "") or ""),
                                  "nfs": [], "ae": "", "fonte": "", "idx": []})
        g["idx"].append(idx)
        g["nfs"] += [n for n in _nfs(r.get(col_nf, "")) if n not in g["nfs"]]
        g["ae"] = g["ae"] or str(r.get("AE", "") or "").strip() or ae_por_placa.get(g["placa"], "")
        g["fonte"] = g["fonte"] or str(r.get("Fonte Rastreio", "") or "").strip()
    resolvidas = resolver_viagens(list(grupos.values()))
    por_idx = {}
    for g, res in zip(grupos.values(), resolvidas):
        for i in g["idx"]:
            por_idx[i] = res
    return [por_idx[i] for i in df.index]


def _presenca_no_df(df, tipo, viagens, local_do):
    import pandas as pd
    agora = _agora()
    textos, desde, aguarda = [], [], []
    chegadas, saidas, idas_txt, foras, sem_sinal = [], [], [], [], []
    desc_ini, desc_fim = [], []
    from app.services import descarga_service
    if tipo == "redespacho" and "Instrução Entrada" in df.columns:
        # a instrução de entrada do redespacho não entra no tempo de descarga;
        # fica registrada porque some da Fila depois de recebida
        try:
            descarga_service.registrar_instrucoes_redespacho(df["Instrução Entrada"].tolist())
        except Exception as e:
            print(f"[Carretas] Não consegui registrar instruções de redespacho: {e}")
    cache: dict = {}
    with get_db() as db:
        garantir_tabelas(db)
        from app.services.arrival_estimator import estimar_chegada
        no_cd = estimar_chegada("0") or {}
        for (idx, r), (viagem, criado) in zip(df.iterrows(), viagens):
            placa = normalize_plate(r.get("Placa", "") or "")
            local = local_do(r)
            if (placa, local) not in cache:
                cache[(placa, local)] = _estadias_da_placa(db, placa, local) if placa and local else []
            p = presenca(placa, local, viagem, agora, estadias=cache[(placa, local)],
                         criado_em=criado)
            textos.append(texto_presenca(p, com_espera=False))
            desde.append(p.get("chegada_em", "") if p else "")
            aguarda.append(p.get("desde_min", "") if p and p["no_cd"] else "")
            chegadas.append(p.get("chegada_dm", "") if p else "")
            saidas.append(p.get("saiu_dm", "") if p else "")
            idas_txt.append("\n".join(
                f"Saiu {i['saiu_dm']} · retornou {i['voltou_dm']} · {i['fora']} fora do CD"
                for i in (p.get("idas") or [])) if p else "")
            foras.append(p.get("fora", "") if p else "")
            sem_sinal.append(p.get("visto_dm", "") if p and p.get("sem_sinal") else "")
            # descarga pelo painel de entrada do WMS (coligada; o redespacho
            # não entra no tempo de descarga)
            sit = {}
            if tipo == "coligada" and p:
                instrs = [i.strip() for i in str(r.get("Instrução Entrada", "") or "").split("/")]
                emps = [u.strip() for u in str(r.get("Empresa Compra", "") or "").split("·")]
                try:
                    sit = descarga_service.situacao_do_card(instrs, emps)
                    # término de antes da chegada é de outra viagem
                    if sit.get("termino") and sit["termino"] < p.get("chegada_em", ""):
                        sit = {}
                    elif sit.get("inicio") and sit["inicio"] < p.get("chegada_em", ""):
                        sit = {"inicio": "", "termino": sit.get("termino", "")}
                except Exception as e:
                    print(f"[Carretas] Situação da descarga indisponível: {e}")
            desc_ini.append(sit.get("inicio", ""))
            desc_fim.append(sit.get("termino", ""))
            if p and p["no_cd"] and str(r.get("Status Estimativa", "")) not in (
                    "no_cd", "no_cd_no_horario", "entregas_frente"):
                df.at[idx, "Estimativa Chegada"] = no_cd.get("estimativa", "Veículo no CD")
                df.at[idx, "Status Estimativa"] = no_cd.get("status", "no_cd")
                df.at[idx, "Tempo Estimado"] = no_cd.get("tempo", "0 min")
    df["Viagem"] = [v for v, _ in viagens]
    df["Presença"] = textos
    df["No CD Desde"] = desde
    df["Aguardando Min"] = pd.Series(aguarda, index=df.index).astype(str)
    df["Chegada CD"] = chegadas
    df["Saída CD"] = saidas
    df["Idas CD"] = idas_txt
    df["Fora CD"] = foras
    df["Sem Sinal CD"] = sem_sinal
    df["Descarga Início"] = desc_ini
    df["Descarga Término"] = desc_fim
    return df
