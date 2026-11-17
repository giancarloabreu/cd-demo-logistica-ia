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


def gravar(destino: Path | str = "planilhas_modelo", hoje: _dt.date | None = None) -> list[Path]:
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    hoje = hoje or _dt.date.today()
    arquivos = {
        "produto_paletizado_ALFA.txt": produto_paletizado(hoje),
        "follow_up_pedidos.xlsx": follow_up(hoje + _dt.timedelta(days=1)),
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
