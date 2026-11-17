"""
Gerador de dados fictícios da demo.

Tudo aqui é inventado: empresas, produtos, clientes, placas e pessoas.
Os arquivos saem nos MESMOS formatos dos relatórios reais que o sistema lê
(o TXT "Produto Paletizado" do WMS e o Excel "Follow up de pedidos" do ERP),
com as datas calculadas a partir de hoje — assim a demo nunca envelhece.

Uso:
    python demo/gerar_dados.py            # grava em planilhas_modelo/
"""
from __future__ import annotations

import datetime as _dt
import io
import math
import random
from pathlib import Path

EMPRESA = "ALFA ALIMENTOS"
LOCAIS = {
    "RESFRIADO": ("101", "CD DEMO RESFRIADO"),
    "CONGELADO": ("102", "CD DEMO CONGELADO"),
}
LOCAL_DEVOLUCAO = ("190", "CD DEMO DEVOLUCAO")

# código, nome, temperatura, kg por caixa, caixas por pallet cheio,
# vida útil (dias), saída diária média (caixas)
PRODUTOS = [
    ("10101", "CAPA DE FILE RESF CX 20KG", "RESFRIADO", 20.0, 48, 60, 90),
    ("10102", "PICANHA RESF CX 18KG", "RESFRIADO", 18.0, 54, 60, 70),
    ("10103", "CONTRAFILE RESF CX 22KG", "RESFRIADO", 22.0, 44, 60, 60),
    ("10104", "ALCATRA RESF CX 21KG", "RESFRIADO", 21.0, 46, 60, 55),
    ("10105", "FRALDINHA RESF CX 16KG", "RESFRIADO", 16.0, 60, 60, 40),
    ("10106", "ACEM RESF CX 25KG", "RESFRIADO", 25.0, 40, 60, 80),
    ("10107", "PATINHO RESF CX 22KG", "RESFRIADO", 22.0, 44, 60, 50),
    ("10108", "COXAO MOLE RESF CX 24KG", "RESFRIADO", 24.0, 42, 60, 45),
    ("10109", "COSTELA RIPA RESF CX 20KG", "RESFRIADO", 20.0, 48, 60, 35),
    ("10110", "MAMINHA RESF CX 15KG", "RESFRIADO", 15.0, 60, 60, 20),
    ("10111", "LINGUICA TOSCANA RESF CX 10KG", "RESFRIADO", 10.0, 80, 45, 65),
    ("10112", "MUSCULO RESF CX 24KG", "RESFRIADO", 24.0, 42, 60, 25),
    ("20201", "ACEM CONG CX 25KG", "CONGELADO", 25.0, 40, 365, 70),
    ("20202", "PALETA CONG CX 25KG", "CONGELADO", 25.0, 40, 365, 60),
    ("20203", "COSTELA JANELA CONG CX 22KG", "CONGELADO", 22.0, 44, 365, 50),
    ("20204", "CUPIM CONG CX 18KG", "CONGELADO", 18.0, 50, 365, 15),
    ("20205", "FIGADO CONG CX 12KG", "CONGELADO", 12.0, 70, 365, 30),
    ("20206", "MOIDA PRIMEIRA CONG CX 10KG", "CONGELADO", 10.0, 90, 365, 85),
    ("20207", "HAMBURGUER BOVINO CONG CX 6KG", "CONGELADO", 6.0, 120, 365, 40),
    ("20208", "PEITO DE FRANGO CONG CX 18KG", "CONGELADO", 18.0, 55, 365, 75),
    ("20209", "COXA E SOBRECOXA CONG CX 20KG", "CONGELADO", 20.0, 50, 365, 55),
    ("20210", "ASA DE FRANGO CONG CX 15KG", "CONGELADO", 15.0, 60, 365, 25),
    ("20211", "TILAPIA FILE CONG CX 10KG", "CONGELADO", 10.0, 80, 365, 20),
    ("20212", "LOMBO SUINO CONG CX 20KG", "CONGELADO", 20.0, 48, 365, 30),
]

CLIENTES = [
    "MERCADO BOA ESPERANCA", "SUPERMERCADO PRIMAVERA", "ATACAREJO CENTRO-OESTE",
    "ACOUGUE DO VALE", "RESTAURANTE SABOR DA SERRA", "REDE MAIS VAREJO",
    "EMPORIO SAO JUDAS", "CHURRASCARIA BRASA VIVA", "SUPERMERCADO ESTRELA",
    "DISTRIBUIDORA NOVO HORIZONTE", "HIPER COMPRA CERTA", "MERCEARIA PONTO FINAL",
]

# Reservas comerciais (o trio Código / Nome / Sigla do relatório)
RESERVAS = [("1", "LIVRE", "LIV")] * 8 + [("20", "EXPORTACAO", "EXP"), ("31", "VAREJO PREMIUM", "VPR")]

CABECALHO = [
    "Temperatura", "Tipo de Produto", "Linha", "Destino", "Família", "Família Subgrupo",
    "Maturado", "Código", "Nome", "CB", "Contra Marca",
    "Código", "Nome", "Rastreabilidade",
    "Câmara", "Tipo", "Rua", "Box", "Altura", "Profundidade", "Palete",
    "Peso Bruto", "Peso Líquido", "Peso Médio", "Peças", "Status",
    "Produção", "Saída Estoque", "Embalagem", "Endereçamento", "Validade",
    "Pedido Comercial", "Terceiro", "Carga", "Instrução", "SIF Produtor",
    "Cert. Sanitário", "Nota Fiscal", "Unidade", "Área", "Média",
    "Código", "Nome", "Sigla",
    "Lote", "Sequencial", "Banda", "Cód. Hablitação", "Sigla Habilitação",
    "Usuário", "HAB", "Período", "Ciclo", "Lote Embarque", "Temperatura Medida",
]


def _br(n: float, casas: int = 3) -> str:
    s = f"{n:,.{casas}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _d(d: _dt.date) -> str:
    return d.strftime("%d/%m/%Y")


def produto_paletizado(hoje: _dt.date | None = None, semente: int = 7) -> bytes:
    """TXT tab-separado, cp1252, uma linha por CAIXA (como o WMS exporta)."""
    hoje = hoje or _dt.date.today()
    rnd = random.Random(semente)
    linhas = [
        "Relatório: Produto Paletizado",
        f"Empresa: {EMPRESA}",
        f"Emitido em: {_d(hoje)} 06:15",
        "Estrutura: Temperatura / Tipo de Produto / Linha / Destino / Família",
        "Filtro: Local de Estoque = Todos",
        "",
        "\t".join(CABECALHO),
    ]
    palete_seq = 400000
    cb_seq = 7_890_000_000
    camaras = {"RESFRIADO": ["01", "02"], "CONGELADO": ["03", "04", "05"]}

    def endereco(temp, picking):
        cam = rnd.choice(camaras[temp])
        rua = rnd.choice("ABCDEF")
        box = f"{rnd.randint(1, 40):03d}"
        altura = "1" if picking else str(rnd.randint(2, 5))
        return cam, rua, box, altura

    def emitir_palete(prod, n_caixas, prod_data, local, picking=False, reserva=None, tipo_end=None):
        nonlocal palete_seq, cb_seq
        cod, nome, temp, kg, _cheio, vida, _saida = prod
        palete_seq += rnd.randint(1, 9)
        cam, rua, box, altura = endereco(temp, picking)
        res = reserva or rnd.choice(RESERVAS)
        tipo = tipo_end or ("2-Picking" if picking else "1-Estocagem")
        ender = prod_data + _dt.timedelta(days=rnd.randint(1, 3))
        for _ in range(n_caixas):
            cb_seq += rnd.randint(1, 40)
            liq = round(kg * rnd.uniform(0.97, 1.03), 3)
            bruto = round(liq + 0.8, 3)
            campos = {
                "Temperatura": temp,
                "Tipo de Produto": "BOVINO" if cod.startswith("101") or cod in ("20201", "20202", "20203", "20204", "20205", "20206", "20207") else "AVES/OUTROS",
                "Linha": "IN NATURA", "Destino": "MERCADO INTERNO",
                "Família": nome.split(" ")[0], "Família Subgrupo": "",
                "Maturado": "N",
            }
            row = [
                campos["Temperatura"], campos["Tipo de Produto"], campos["Linha"],
                campos["Destino"], campos["Família"], campos["Família Subgrupo"],
                campos["Maturado"], cod, nome, str(cb_seq), "",
                local[0], local[1], f"RST{rnd.randint(100000, 999999)}",
                cam, tipo, rua, f"{cam}-{box}", altura, str(rnd.randint(1, 2)),
                _br(palete_seq, 0) if palete_seq >= 1000 else str(palete_seq),
                _br(bruto), _br(liq), _br(kg), "1", "Disponível",
                _d(prod_data), "", "CAIXA", _d(ender), _d(prod_data + _dt.timedelta(days=vida)),
                "", "", "", "", "SIF 0000",
                "", "", "UN DEMO", "A1", _br(kg),
                res[0], res[1], res[2],
                f"L{prod_data.strftime('%y%j')}", str(rnd.randint(1, 999)), "", "", "",
                "sistema", "", "", "", "", "-18,0" if temp == "CONGELADO" else "1,5",
            ]
            linhas.append("\t".join(row))

    for prod in PRODUTOS:
        cod, nome, temp, kg, cheio, vida, saida = prod
        local = LOCAIS[temp]
        idade_max = 40 if temp == "RESFRIADO" else 200
        # pallets cheios no estoque alto, em vários lotes de produção
        n_pal = rnd.randint(5, 9)
        for _ in range(n_pal):
            idade = rnd.randint(3, idade_max)
            emitir_palete(prod, cheio, hoje - _dt.timedelta(days=idade), local)
        # pallets fracos (sobras) — o alvo do adensamento
        for _ in range(rnd.randint(2, 5)):
            idade = rnd.randint(3, idade_max)
            emitir_palete(prod, rnd.randint(4, max(5, cheio // 3)), hoje - _dt.timedelta(days=idade), local)
        # face de picking, no chão
        emitir_palete(prod, rnd.randint(3, 12), hoje - _dt.timedelta(days=rnd.randint(2, 20)), local, picking=True)

    # devolução: tudo que está no local de devolução vai para o relatório
    for prod in rnd.sample(PRODUTOS, 6):
        emitir_palete(prod, rnd.randint(2, 15), hoje - _dt.timedelta(days=rnd.randint(10, 50)),
                      LOCAL_DEVOLUCAO)

    # perto do vencimento (resfriado com 50+ dias, vida útil de 60)
    for prod in [p for p in PRODUTOS if p[2] == "RESFRIADO"][:5]:
        emitir_palete(prod, rnd.randint(10, 30), hoje - _dt.timedelta(days=rnd.randint(48, 56)),
                      LOCAIS["RESFRIADO"])

    linhas.append("")
    linhas.append(f"Total de registros:\t{len(linhas) - 8}")
    return ("\r\n".join(linhas) + "\r\n").encode("cp1252", errors="replace")


FOLLOW_GRUPOS = [
    ("Pedido", ["Pedido", "Pedido Principal", "Entrega", "Item", "Operação Comercial",
                "Situação do Pedido", "Aguardando", "Nome do Terceiro", "Nome Classificação de Preço"]),
    ("Produto", ["TEMPERATURA", "Empresa de Entrega", "Local Estoque", "Nome Local Estoque",
                 "Produto", "Nome Produto", "Descrição Detalhada"]),
    ("Quantidades", ["Qtde", "Unidade", "Qtde Pendente", "Peso Líquido Estimado", "Volume Estimado"]),
    ("Embarque", ["Data de Embarque", "Data de Entrega", "Limite Mínimo Embarque",
                  "Limite Máximo Embarque", "Situação da Entrega", "Data de Cancelamento"]),
    ("Reserva", ["Reserva", "Nome Reserva", "Exige Reserva"]),
]


def follow_up(alvo: _dt.date | None = None, semente: int = 11) -> bytes:
    """Excel do follow up de pedidos: 2 linhas de cabeçalho (grupos + colunas)."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill

    alvo = alvo or (_dt.date.today() + _dt.timedelta(days=1))
    rnd = random.Random(semente)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Follow up"
    colunas = [c for _, cs in FOLLOW_GRUPOS for c in cs]
    grupos = []
    for g, cs in FOLLOW_GRUPOS:
        grupos += [g] + [""] * (len(cs) - 1)
    ws.append(grupos)
    ws.append(colunas)
    for c in ws[1] + ws[2]:
        c.font = Font(bold=True)
    for c in ws[2]:
        c.fill = PatternFill("solid", fgColor="DDEBF7")

    pedido = 5_100_000
    for i in range(320):
        prod = rnd.choices(PRODUTOS, weights=[p[6] for p in PRODUTOS])[0]
        cod, nome, temp, kg, cheio, vida, _ = prod
        local = LOCAIS[temp]
        pedido += rnd.randint(1, 3)
        caixas = rnd.choice([5, 8, 10, 12, 15, 20, 24, 30, 40, 48])
        peso = round(caixas * kg, 3)
        # variedade realista para o funil de cortes mostrar alguma coisa
        sorteio = rnd.random()
        data_emb = alvo if sorteio < 0.78 else alvo + _dt.timedelta(days=rnd.choice([-1, 1, 2]))
        situacao_ent = "Pendente" if rnd.random() < 0.93 else "Faturada"
        situacao_ped = rnd.choices(["Liberado", "PRC - Preço", "PRL - Limite", "Bloqueado Status"],
                                   weights=[80, 8, 7, 5])[0]
        aguardando = rnd.choices(["02-INSTRUÇÃO", "03-TRANSPORTE", "04-EMBARQUE"], weights=[85, 10, 5])[0]
        operacao = rnd.choices(["S-VENDA MERCADO INTERNO", "S-VENDA REDESPACHO", "E-TRANSFERENCIA COLIGADA"],
                               weights=[88, 7, 5])[0]
        lim_min = 0
        lim_max = rnd.choice([30, 45, 60]) if temp == "RESFRIADO" else rnd.choice([180, 365, 733])
        res = rnd.choice(RESERVAS)
        ws.append([
            pedido, "", rnd.randint(1, 4), rnd.randint(1, 12), operacao, situacao_ped, aguardando,
            rnd.choice(CLIENTES), rnd.choice(["TABELA A", "TABELA B", "PROMOCIONAL"]),
            temp, EMPRESA, local[0], local[1], int(cod), nome, nome.title(),
            peso, "KG", peso, peso, caixas,
            data_emb, data_emb + _dt.timedelta(days=1), lim_min, lim_max,
            situacao_ent, None,
            float(res[0]), res[1], "N" if res[0] == "1" else "S",
        ])
    for col in ws.columns:
        ws.column_dimensions[col[1].column_letter].width = max(12, len(str(col[1].value)) + 2)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── painel do WMS da noite (acompanhamento da expedição) ──────────────
SEPARADORES = ["ANDRE MOURA", "BRUNO TAVARES", "CARLA DIAS", "DIEGO PRADO", "EDUARDO LIMA",
               "FABIO REIS", "GUSTAVO NERY", "HELENA COSTA", "IGOR SALES", "JULIO MATOS"]
CONFERENTES = ["KAREN LOPES", "LEANDRO VAZ", "MARCIA FREITAS", "NELSON ROCHA"]

PAINEL_COLUNAS = [
    ("Empresa", "Nome"), ("Data Operação", "Atual"),
    ("Separador", "Código"), ("Separador", "Nome"),
    ("Escala", "Instrução"), ("Escala", "Terceiro"), ("Escala", "Escalas"), ("Escala", "SKU"),
    ("Escala", "Prioridade"), ("Escala", "Tipo de Operação"), ("Escala", "Tipo de Carga"),
    ("Escala", "Calcula Produtividade"),
    ("Veículo", "Placa"), ("Veículo", "Tipo de Frete"), ("Veículo", "Temperatura"),
    ("Previsto", "Peso Líquido"), ("Separado", "Peso Líquido"), ("Separado", "Volume"),
    ("Embarcado", "Peso Líquido"), ("Embarcado", "Volume"), ("Embarcado", "Última Caixa"),
    ("Doca", "Doca"), ("Conferente", "Código"), ("Conferente", "Nome"),
    ("Separação", "Início"), ("Separação", "Fim"), ("Embarque", "Início"), ("Embarque", "Fim"),
    ("Data", "Fechamento Status"), ("Status", "Operação"), ("Status", "Veículo"),
]


def painel_noite(data_op: _dt.date | None = None, semente: int = 23) -> bytes:
    """Painel de produtividade do WMS de uma noite já encerrada (Excel com
    cabeçalho em dois níveis: grupo > detalhe). A noite rotulada `data_op`
    começa às 18h do dia anterior e termina de madrugada."""
    import openpyxl
    from openpyxl.styles import Font

    data_op = data_op or _dt.date.today()
    rnd = random.Random(semente + data_op.toordinal())
    inicio_noite = _dt.datetime.combine(data_op - _dt.timedelta(days=1), _dt.time(18, 30))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Painel"
    ws.append(["Painel de Produtividade de Separação"])
    grupos, detalhes, ultimo = [], [], None
    for g, d in PAINEL_COLUNAS:
        grupos.append(g if g != ultimo else "")
        detalhes.append(d)
        ultimo = g
    ws.append(grupos)
    ws.append(detalhes)
    for c in ws[2] + ws[3]:
        c.font = Font(bold=True)

    livre = {s: inicio_noite + _dt.timedelta(minutes=rnd.randint(0, 40)) for s in SEPARADORES}
    instr = 81200 + rnd.randint(0, 500)
    for i in range(46):
        instr += rnd.randint(1, 4)
        temp = rnd.choice(["RESFRIADO", "CONGELADO"])
        prev = round(rnd.uniform(1500, 9000), 3)
        sep_kg = round(prev * rnd.uniform(0.96, 1.0), 3)
        vol = int(sep_kg / rnd.uniform(14, 22))
        sep = min(livre, key=livre.get)
        ini = livre[sep] + _dt.timedelta(minutes=rnd.randint(2, 12))
        # ritmo do separador: alguns mais rápidos, alguns mais lentos
        ritmo = 2600 + 350 * (SEPARADORES.index(sep) % 4) + rnd.randint(-200, 200)   # kg/h
        fim = ini + _dt.timedelta(minutes=int(sep_kg / ritmo * 60))
        livre[sep] = fim
        emb_ini = fim + _dt.timedelta(minutes=rnd.randint(5, 35))
        emb_fim = emb_ini + _dt.timedelta(minutes=rnd.randint(18, 50))
        ult = emb_fim - _dt.timedelta(minutes=rnd.randint(0, 6))
        fech = emb_fim + _dt.timedelta(minutes=rnd.randint(5, 90))
        conf = rnd.choice(CONFERENTES)
        placa = "DEM" + str(rnd.randint(0, 9)) + rnd.choice("ABCDEFGHIJ") + f"{rnd.randint(0, 99):02d}"
        ws.append([
            EMPRESA, data_op, 1000 + SEPARADORES.index(sep), sep,
            instr, rnd.choice(CLIENTES), rnd.randint(1, 6), rnd.randint(3, 28),
            rnd.randint(1, 3), "VENDA", "PALETIZADA", "S",
            placa, rnd.choice(["CIF", "FOB"]), temp,
            prev, sep_kg, vol, sep_kg, vol, ult,
            f"D{rnd.randint(1, 8):02d}", 2000 + CONFERENTES.index(conf), conf,
            ini, fim, emb_ini, emb_fim, fech, "Faturado", "Em Viagem",
        ])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── Abastecimento CD: carretas em trânsito para o CD ──────────────────
# CD fictício em Brasília-DF (coordenada pública da cidade)
CD_DEMO = {"nome": "CDA", "nome2": "CDB", "latitude": "-15.7939", "longitude": "-47.8828"}

FABRICAS = [
    # empresa que expede, cidade, UF, lat, lon
    ("FRIGORIFICO ALFA - UN CAMPO GRANDE", "CAMPO GRANDE", "MS", -20.4697, -54.6201),
    ("FRIGORIFICO ALFA - UN CUIABA", "CUIABA", "MT", -15.6014, -56.0979),
    ("FRIGORIFICO ALFA - UN RIO VERDE", "RIO VERDE", "GO", -17.7923, -50.9192),
    ("ALIMENTOS BETA - UN UBERLANDIA", "UBERLANDIA", "MG", -18.9186, -48.2772),
    ("ALIMENTOS BETA - UN ARAGUAINA", "ARAGUAINA", "TO", -7.1911, -48.2072),
]
TRANSPORTADORAS = ["TRANSPORTES RAPIDO CERRADO", "LOGFRIO CARGAS", "TRANSVALE REFRIGERADOS",
                   "EXPRESSO PLANALTO", "RODOFRIO LOGISTICA"]
SITUACOES = ["Em viagem", "Em viagem", "Em viagem", "Parado", "Aguardando descarga"]


def _ponto_na_rota(rnd, origem, destino, fracao):
    lat = origem[0] + (destino[0] - origem[0]) * fracao + rnd.uniform(-0.15, 0.15)
    lon = origem[1] + (destino[1] - origem[1]) * fracao + rnd.uniform(-0.15, 0.15)
    return round(lat, 6), round(lon, 6)


def _carretas(hoje: _dt.date, semente: int = 31):
    """A mesma lista de carretas alimenta os dois arquivos (faturamento e rastreio)."""
    rnd = random.Random(semente + hoje.toordinal())
    cd = (float(CD_DEMO["latitude"]), float(CD_DEMO["longitude"]))
    lista, usadas = [], set()
    for i in range(16):
        fab = FABRICAS[i % len(FABRICAS)] if i < 10 else rnd.choice(FABRICAS)
        while True:
            placa = "DEM" + str(rnd.randint(0, 9)) + rnd.choice("ABCDEFGHIJ") + f"{rnd.randint(0, 99):02d}"
            if placa not in usadas:
                usadas.add(placa)
                break
        cavalo = "CAV" + str(rnd.randint(0, 9)) + rnd.choice("ABCDEFGHIJ") + f"{rnd.randint(0, 99):02d}"
        fracao = rnd.choice([0.15, 0.3, 0.45, 0.6, 0.75, 0.9, 0.98])
        lat, lon = _ponto_na_rota(rnd, (fab[3], fab[4]), cd, fracao)
        temp = rnd.choice(["RESFRIADO", "CONGELADO"])
        lista.append({
            "fabrica": fab, "placa": placa, "cavalo": cavalo, "lat": lat, "lon": lon,
            "fracao": fracao, "temperatura": temp,
            "temp_medida": (rnd.uniform(-1.0, 3.5) if temp == "RESFRIADO" else rnd.uniform(-21, -15)),
            "transportadora": rnd.choice(TRANSPORTADORAS),
            "empresa_compra": "CDA" if rnd.random() < 0.7 else "CDB",
            "faturamento": hoje - _dt.timedelta(days=rnd.choice([0, 1, 1, 2, 3])),
            "ae": str(37_000_000 + rnd.randint(0, 999_999)),
            "situacao": "Aguardando descarga" if fracao >= 0.98 else rnd.choice(SITUACOES[:4]),
        })
    return lista


def _notas_das_carretas(hoje: _dt.date, semente: int = 31):
    """[(carreta, carga, [(nf, pedido, [(produto, caixas, kg, preço)])])] —
    a mesma base para o Estoque Faturado e o Controle de Distribuição."""
    rnd = random.Random(semente * 7 + hoje.toordinal())
    nf = 610_000 + rnd.randint(0, 9000)
    pedido = 2_400_000 + rnd.randint(0, 9000)
    saida = []
    for car in _carretas(hoje):
        produtos = [p for p in PRODUTOS if p[2] == car["temperatura"]]
        carga = str(880_000 + rnd.randint(0, 99_999))
        notas = []
        for _ in range(rnd.randint(1, 4)):
            nf += rnd.randint(1, 30)
            pedido += rnd.randint(1, 15)
            itens = []
            for prod in rnd.sample(produtos, rnd.randint(2, 6)):
                caixas = rnd.randint(20, 220)
                itens.append((prod, caixas, round(caixas * prod[3] * rnd.uniform(0.98, 1.02), 3),
                              round(rnd.uniform(18, 42), 2)))
            notas.append((nf, pedido, itens))
        saida.append((car, carga, notas))
    return saida


def historico_temperatura_carretas(agora: _dt.datetime, semente: int = 31) -> list[tuple]:
    """Leituras do baú de cada carreta, a cada 15 min desde a saída da fábrica
    (até 48 h), terminando na temperatura que o painel de rastreio mostra.
    Uma carreta tem abertura de porta no caminho (salto brusco), como o
    painel detecta na operação."""
    hoje = agora.date()
    rnd = random.Random(semente * 3 + hoje.toordinal())
    saida = []
    for n, car in enumerate(_carretas(hoje)):
        alvo = car["temp_medida"]
        horas = min(48, 6 + int(car["fracao"] * 40))
        pontos = horas * 4
        porta = n == 2                       # a terceira carreta abriu a porta no caminho
        for k in range(pontos + 1):
            ts = agora - _dt.timedelta(minutes=15 * (pontos - k))
            base = alvo + (0.6 if car["temperatura"] == "RESFRIADO" else 1.2) * math.sin(k / 7) \
                + rnd.uniform(-0.25, 0.25)
            if porta and pontos // 2 <= k <= pontos // 2 + 1:
                base += 9.0 if car["temperatura"] == "RESFRIADO" else 11.0
            if k == pontos:
                base = alvo
            saida.append((car["placa"], round(base, 1), ts.strftime("%Y-%m-%d %H:%M:%S")))
    return saida


def estoque_faturado(hoje: _dt.date | None = None, semente: int = 31) -> bytes:
    """Relatório "Estoque Faturado" do ERP: uma linha por produto de cada NF,
    cabeçalho em dois níveis (grupo > coluna)."""
    import openpyxl
    from openpyxl.styles import Font

    hoje = hoje or _dt.date.today()
    rnd = random.Random(semente * 7 + hoje.toordinal())
    grupos = [
        ("Empresa Venda", ["Empresa", "Faturamento", "Número", "Transportadora", "Placa"]),
        ("Empresa Compra", ["Empresa", "Local de Estoque", "Pedido", "Carga"]),
        ("Produto", ["Cód Produto", "Temperatura", "Linha", "Produto", "Unidade"]),
        ("Compra", ["Quantidade", "Volume", "Preço"]),
    ]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Estoque Faturado"
    l1, l2 = [], []
    for g, cs in grupos:
        l1 += [g] + [""] * (len(cs) - 1)
        l2 += cs
    ws.append(l1)
    ws.append(l2)
    for c in ws[1] + ws[2]:
        c.font = Font(bold=True)
    for car, carga, notas in _notas_das_carretas(hoje, semente):
        local = f"{car['empresa_compra']} {car['temperatura']}"
        for nf, pedido, itens in notas:
            for prod, caixas, kg_total, preco in itens:
                cod, nome, temp, _kg, _cheio, _v, _s = prod
                ws.append([car["fabrica"][0], car["faturamento"], nf, car["transportadora"], car["placa"],
                           car["empresa_compra"], local, pedido, carga,
                           int(cod), temp, "IN NATURA", nome, "KG", kg_total, caixas, preco])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def controle_distribuicao(hoje: _dt.date | None = None, semente: int = 31) -> bytes:
    """Controle de Distribuição do ERP: uma linha por pedido, com a instrução
    de entrada no CD (o que o card mostra) e a previsão de entrega."""
    import openpyxl
    from openpyxl.styles import Font

    hoje = hoje or _dt.date.today()
    rnd = random.Random(semente * 13 + hoje.toordinal())
    grupos = [
        ("Pedido Comercial", ["Código", "Carga"]),
        ("Embarque/Desembarque(Fábrica)", ["Instrução Filial", "Peso", "Volume"]),
        ("Instrução", ["Código", "Código AE (OpenTech)"]),
        ("Empresa Filial", ["Sigla"]),
        ("Operação Comercial", ["Nome"]),
        ("Terceiro", ["Nome"]),
        ("Terceiro (Endereço)", ["Cidade", "UF", "Latitude", "Longitude"]),
        ("Transportadora", ["Nome"]),
        ("Veículo Cavalo", ["Placa"]),
        ("Veículo Reboque", ["Placa"]),
        ("Nota Fiscal", ["Formulário NF"]),
        ("Faturamento", ["Data Faturamento"]),
        ("Previsão de Entrega (Carga)", ["Data Entrega", "Hora Entrega"]),
        ("Produto (Pedido)", ["Produto (Primeiro)"]),
    ]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Controle"
    l1, l2 = [], []
    for g, cs in grupos:
        l1 += [g] + [""] * (len(cs) - 1)
        l2 += cs
    ws.append(l1)
    ws.append(l2)
    for c in ws[1] + ws[2]:
        c.font = Font(bold=True)
    inst_filial = 51_000 + rnd.randint(0, 900)
    inst = 7_700_000 + rnd.randint(0, 9999)
    for car, carga, notas in _notas_das_carretas(hoje, semente):
        # a última carreta fica sem instrução — o card avisa, como na operação
        sem_instrucao = car is _notas_das_carretas(hoje, semente)[-1][0] or rnd.random() < 0.12
        inst_filial += rnd.randint(1, 6)
        inst += rnd.randint(1, 40)
        prev = hoje + _dt.timedelta(days=1 if car["fracao"] < 0.9 else 0)
        for nf, pedido, itens in notas:
            peso = sum(i[2] for i in itens)
            vol = sum(i[1] for i in itens)
            ws.append([pedido, carga, "" if sem_instrucao else inst_filial, round(peso, 3), vol,
                       inst, car["ae"], car["empresa_compra"], "S-TRANSFERENCIA ENTRE FILIAIS",
                       f"CD DEMO {car['empresa_compra']}", "BRASILIA", "DF",
                       CD_DEMO["latitude"], CD_DEMO["longitude"], car["transportadora"],
                       car["cavalo"], car["placa"], nf, car["faturamento"], prev,
                       rnd.choice(["06:00", "08:00", "10:00", "14:00"]), itens[0][0][1]])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def painel_rastreio(hoje: _dt.date | None = None, semente: int = 31) -> bytes:
    """Exportação do painel do rastreador de frota: posição, km restante,
    temperatura do baú (o rastreador manda multiplicada por 1000)."""
    import math
    import openpyxl

    hoje = hoje or _dt.date.today()
    cd = (float(CD_DEMO["latitude"]), float(CD_DEMO["longitude"]))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Monitoração"
    ws.append(["Placa veículo", "Placa carreta", "Aut. de embarque", "Situação viagem",
               "Última posição", "Referência posição cidade", "Latitude última posição",
               "Longitude última posição", "Km Restante", "Última temperatura recebida",
               "Situação ignição", "Situação do veículo"])
    agora = _dt.datetime.combine(hoje, _dt.time(8, 0))
    for car in _carretas(hoje):
        dlat = math.radians(cd[0] - car["lat"])
        dlon = math.radians(cd[1] - car["lon"])
        a = (math.sin(dlat / 2) ** 2 + math.cos(math.radians(car["lat"])) * math.cos(math.radians(cd[0]))
             * math.sin(dlon / 2) ** 2)
        km = 6371 * 2 * math.asin(math.sqrt(a)) * 1.25
        fab = car["fabrica"]
        ref = f"{int(km)} km de BRASILIA/DF" if car["fracao"] < 0.98 else "CD DEMO - BRASILIA/DF"
        ws.append([car["cavalo"], car["placa"], car["ae"], "Em andamento",
                   agora.strftime("%d/%m/%Y %H:%M"), ref, car["lat"], car["lon"], round(km),
                   int(round(car["temp_medida"] * 1000)),
                   "Ligada" if car["situacao"] == "Em viagem" else "Desligada", car["situacao"]])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── Entregas: viagens de distribuição a partir do CD (relatórios do
#    roteirizador/rastreador de entregas) ─────────────────────────────
UNIDADE_ENTREGAS = {"Id": 101, "Nome": "Marca CDA"}
BAIRROS = [  # bairro, lat, lon (coordenadas públicas aproximadas do DF)
    ("ASA SUL", -15.8170, -47.9020), ("ASA NORTE", -15.7630, -47.8830), ("TAGUATINGA", -15.8330, -48.0560),
    ("CEILANDIA", -15.8190, -48.1080), ("GUARA", -15.8300, -47.9800), ("AGUAS CLARAS", -15.8390, -48.0280),
    ("SAMAMBAIA", -15.8760, -48.0850), ("SOBRADINHO", -15.6520, -47.7890), ("GAMA", -16.0190, -48.0620),
    ("PLANALTINA", -15.6200, -47.6530), ("LAGO SUL", -15.8420, -47.8700), ("RIACHO FUNDO", -15.8830, -48.0170),
]
MOTORISTAS = ["ADEMIR CRUZ", "BENEDITO ALVES", "CLEITON RAMOS", "DOUGLAS PIRES", "EVERALDO NUNES",
              "FRANCISCO TELES", "GILMAR SOUZA", "HAROLDO VIEIRA", "IVAN BORGES", "JOSIAS MELO"]


def _momento_entregas(agora: _dt.datetime):
    """O dia de referência e o "agora" das viagens. Fora do expediente a demo
    mostra o dia de trabalho mais recente já encerrado."""
    if agora.hour < 7:
        return agora.date() - _dt.timedelta(days=1), _dt.datetime.combine(
            agora.date() - _dt.timedelta(days=1), _dt.time(19, 30))
    if agora.hour >= 20:
        return agora.date(), _dt.datetime.combine(agora.date(), _dt.time(19, 30))
    return agora.date(), agora


def entregas(agora: _dt.datetime | None = None, semente: int = 41) -> dict:
    """{"notas": csv, "itens": csv, "posicoes": [(placa, em, lat, lon, ignicao, temp)]}
    no layout dos relatórios de viagens e de itens (CSV com ";")."""
    agora = agora or _dt.datetime.now()
    dia, ref = _momento_entregas(agora)
    rnd = random.Random(semente + dia.toordinal())
    cd = (float(CD_DEMO["latitude"]), float(CD_DEMO["longitude"]))
    cab_notas = ["Id da viagem", "Identificador", "Placa", "Percentual de eficiência", "Cidade", "Cliente",
                 "Fim real da viagem", "Fim entrega", "Data de importação da viagem", "Início real da viagem",
                 "Início entrega", "Motorista", "Homologada distância", "Número NF", "Quantidade de entregas",
                 "Quantidade reentregas", "Notas previstas", "Quantidade de entregas realizadas",
                 "Sequência programada", "Sequência real", "Código do cliente", "Latitude referência",
                 "Longitude referência", "Metros da entrega", "Status NF", "Peso bruto (NF)", "Valor NF",
                 "Endereço da referência", "Bairro", "Unidade", "Quantidade de notas entregues",
                 "Transportadora", "Status viagem", "Chave da NFe",
                 "Data hora primeira posição dentro do raio", "Data hora última posição dentro do raio",
                 "Latitude entrega", "Longitude entrega", "Data estimada de entrega"]
    cab_itens = ["Descrição", "Número NF", "Código item", "Descrição item", "Quantidade",
                 "Peso total líquido", "Unidade"]
    linhas_n, linhas_i, posicoes = [";".join(cab_notas)], [";".join(cab_itens)], []

    def dh(t):
        return t.strftime("%d/%m/%Y %H:%M:%S") if t else ""

    def br(v, casas=2):
        return f"{v:.{casas}f}".replace(".", ",")

    nf = 720_000 + rnd.randint(0, 9000)
    cliente_cod = 30_000
    for i in range(9):
        vid = str(915_000 + i * 37 + rnd.randint(0, 30))
        placa = "DEM" + str(rnd.randint(0, 9)) + rnd.choice("KLMNOPQRST") + f"{rnd.randint(0, 99):02d}"
        temp_alvo = rnd.choice([2.0, -18.0])
        inicio = _dt.datetime.combine(dia, _dt.time(6, rnd.randint(0, 50)))
        n_ent = rnd.randint(6, 12)
        paradas = rnd.sample(BAIRROS, k=min(n_ent, len(BAIRROS)))
        while len(paradas) < n_ent:
            paradas.append(rnd.choice(BAIRROS))
        # ordem programada: mais perto do CD primeiro
        paradas.sort(key=lambda b: abs(b[1] - cd[0]) + abs(b[2] - cd[1]) + rnd.uniform(0, 0.05))
        t = inicio
        pos_ant = cd
        entregas_viagem, realizadas, notas_ent = [], 0, 0
        for seq, (bairro, blat, blon) in enumerate(paradas, start=1):
            lat = round(blat + rnd.uniform(-0.012, 0.012), 6)
            lon = round(blon + rnd.uniform(-0.012, 0.012), 6)
            desloc = int(abs(lat - pos_ant[0]) * 520 + abs(lon - pos_ant[1]) * 520) + rnd.randint(8, 20)
            chegada = t + _dt.timedelta(minutes=desloc)
            saida = chegada + _dt.timedelta(minutes=rnd.randint(15, 45))
            cliente_cod += rnd.randint(1, 50)
            cliente = rnd.choice(CLIENTES) + f" - {bairro.title()}"
            feita = saida <= ref
            em_andamento = chegada <= ref < saida
            entregas_viagem.append((seq, bairro, lat, lon, chegada, saida, feita, em_andamento, cliente_cod, cliente))
            # trajeto: pontos entre a parada anterior e esta, até o "agora"
            for k in range(1, 4):
                tk = t + _dt.timedelta(minutes=desloc * k / 4)
                if tk > ref:
                    break
                plat = pos_ant[0] + (lat - pos_ant[0]) * k / 4
                plon = pos_ant[1] + (lon - pos_ant[1]) * k / 4
                posicoes.append((placa, tk, round(plat, 6), round(plon, 6), "Ligada",
                                 round(temp_alvo + rnd.uniform(-1.2, 1.2), 1)))
            if chegada <= ref:
                posicoes.append((placa, chegada, lat, lon, "Desligada", round(temp_alvo + rnd.uniform(-0.8, 1.6), 1)))
            if feita:
                posicoes.append((placa, saida, lat, lon, "Ligada", round(temp_alvo + rnd.uniform(-0.8, 1.6), 1)))
            pos_ant = (lat, lon)
            t = saida
        aberta = not all(e[6] for e in entregas_viagem)
        fim_viagem = None if aberta else entregas_viagem[-1][5] + _dt.timedelta(minutes=rnd.randint(20, 50))
        status_viagem = "Em andamento" if aberta else "Finalizada"
        total_notas = 0
        blocos = []
        for (seq, bairro, lat, lon, chegada, saida, feita, andamento, ccod, cliente) in entregas_viagem:
            n_nfs = rnd.choice([1, 1, 1, 2, 2, 3])
            for _ in range(n_nfs):
                nf += rnd.randint(1, 9)
                total_notas += 1
                if feita:
                    status = rnd.choices(["Entregue", "Devolução parcial", "Devolução", "Reentrega"],
                                         weights=[86, 6, 4, 4])[0]
                elif andamento:
                    status = "Entregando"
                else:
                    status = "A entregar"
                fora = feita and rnd.random() < 0.04          # baixa longe do cliente (anel branco)
                lat_ent = lat + (rnd.uniform(0.01, 0.02) if fora else rnd.uniform(-0.0004, 0.0004))
                lon_ent = lon + (rnd.uniform(0.01, 0.02) if fora else rnd.uniform(-0.0004, 0.0004))
                metros = int(rnd.uniform(1200, 2400)) if fora else int(rnd.uniform(5, 140))
                if feita and status in ("Entregue", "Devolução parcial"):
                    notas_ent += 1
                peso = round(rnd.uniform(80, 1400), 2)
                chave = "53" + dia.strftime("%y%m") + "00000000000191" + "55" + "001" + f"{nf:09d}" + \
                        f"{rnd.randint(0, 99999999):08d}" + str(rnd.randint(0, 9))
                blocos.append([
                    vid, f"VG{vid}", placa, "", "BRASILIA", cliente,
                    dh(fim_viagem), dh(saida if feita else None), dh(inicio - _dt.timedelta(minutes=40)),
                    dh(inicio), dh(chegada if (feita or andamento) else None),
                    MOTORISTAS[i % len(MOTORISTAS)], "Não" if fora else "Sim", str(nf), str(len(entregas_viagem)),
                    "1" if status == "Reentrega" else "0", "", "", str(seq),
                    str(seq if feita else ""), str(ccod), br(lat, 6), br(lon, 6),
                    str(metros) if feita else "", status, br(peso), br(peso * rnd.uniform(28, 46)),
                    f"QUADRA {rnd.randint(1, 30)} CONJUNTO {rnd.choice('ABCDEFGH')} LOTE {rnd.randint(1, 40)}",
                    bairro, UNIDADE_ENTREGAS["Nome"], "", rnd.choice(TRANSPORTADORAS), status_viagem, chave,
                    dh(chegada if (feita or andamento) else None), dh(saida if feita else None),
                    br(lat_ent, 6) if feita else "", br(lon_ent, 6) if feita else "",
                    dh(chegada),
                ])
                for prod in rnd.sample(PRODUTOS, rnd.randint(1, 4)):
                    cx = rnd.randint(2, 30)
                    linhas_i.append(";".join([vid, str(nf), prod[0], prod[1], str(cx), br(cx * prod[3], 3), "CX"]))
        realizadas = sum(1 for e in entregas_viagem if e[6])
        efic = 100.0 * notas_ent / total_notas if total_notas else 0
        for b in blocos:
            b[3] = br(efic, 1)
            b[16] = str(total_notas)
            b[17] = str(realizadas)
            b[30] = str(notas_ent)
            linhas_n.append(";".join(b))
    return {"notas": "\n".join(linhas_n) + "\n", "itens": "\n".join(linhas_i) + "\n",
            "posicoes": posicoes, "dia": dia}


# ── Câmaras frias: sensores de temperatura das câmaras do CD ─────────
CAMARAS = [
    # id, nome, tipo, mínimo, máximo, limite duro, alvo, comportamento recente
    ("C01", "Câmara 01 · Resfriado", "resfriada", 0.0, 4.0, 8.0, 2.0, "normal"),
    ("C02", "Câmara 02 · Resfriado", "resfriada", 0.0, 4.0, 8.0, 2.4, "porta"),
    ("C03", "Câmara 03 · Congelado", "congelada", -22.0, -16.0, -12.0, -19.0, "normal"),
    ("C04", "Câmara 04 · Congelado", "congelada", -22.0, -16.0, -12.0, -19.5, "excursao"),
    ("C05", "Câmara 05 · Congelado", "congelada", -22.0, -16.0, -12.0, -18.6, "normal"),
    ("ANT", "Antecâmara · Expedição", "antecamara", 2.0, 10.0, 14.0, 6.5, "normal"),
]


def leituras_camaras(agora: _dt.datetime, dias: int = 30, passo_min: int = 10, semente: int = 51) -> dict:
    """{id: [(ts, valor)]} — uma leitura a cada `passo_min` minutos nos últimos
    `dias`. Degelo programado a cada 6 h (sobe e volta), portas abrindo no
    horário de expedição e, nas últimas horas, uma câmara oscilando (porta
    aberta de novo e de novo) e outra numa excursão sustentada."""
    rnd = random.Random(semente + agora.date().toordinal())
    fim = agora.replace(second=0, microsecond=0)
    fim = fim - _dt.timedelta(minutes=fim.minute % passo_min)
    n = dias * 24 * 60 // passo_min
    saida = {}
    for cid, _nome, tipo, tmin, tmax, _duro, alvo, recente in CAMARAS:
        serie = []
        amp = 0.5 if tipo != "congelada" else 0.8
        for k in range(n + 1):
            ts = fim - _dt.timedelta(minutes=passo_min * (n - k))
            v = alvo + amp * math.sin(k / 9) + rnd.uniform(-0.25, 0.25)
            if ts.hour % 6 == 0 and ts.minute < 30 and tipo == "congelada":
                v += 2.2                                  # degelo programado
            if 19 <= ts.hour <= 23 and rnd.random() < 0.06:
                v += rnd.uniform(1.5, 3.0)                # porta na expedição
            falta = (fim - ts).total_seconds() / 60
            if recente == "porta" and falta <= 150 and (falta // 20) % 2 == 0:
                v = tmax + rnd.uniform(0.6, 2.2)          # oscila fora e volta
            if recente == "excursao" and falta <= 100:
                v = tmax + (100 - falta) / 100 * 4.0 + rnd.uniform(0, 0.4)
            serie.append((ts.strftime("%Y-%m-%d %H:%M:%S"), round(v, 2)))
        saida[cid] = serie
    return saida


def gravar(destino: Path | str = "planilhas_modelo", hoje: _dt.date | None = None) -> list[Path]:
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    hoje = hoje or _dt.date.today()
    arquivos = {
        "produto_paletizado_ALFA.txt": produto_paletizado(hoje),
        "follow_up_pedidos.xlsx": follow_up(hoje + _dt.timedelta(days=1)),
        "painel_wms_noite.xlsx": painel_noite(hoje),
        "estoque_faturado.xlsx": estoque_faturado(hoje),
        "controle_distribuicao.xlsx": controle_distribuicao(hoje),
        "painel_rastreio.xlsx": painel_rastreio(hoje),
    }
    saida = []
    for nome, conteudo in arquivos.items():
        p = destino / nome
        p.write_bytes(conteudo)
        saida.append(p)
    return saida


if __name__ == "__main__":
    for p in gravar():
        print(p, p.stat().st_size, "bytes")
