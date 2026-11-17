from app.utils.text_utils import normalize_text


def find_column(df, candidates):
    normalized_columns = {normalize_text(col): col for col in df.columns}

    for candidate in candidates:
        normalized_candidate = normalize_text(candidate)
        if normalized_candidate in normalized_columns:
            return normalized_columns[normalized_candidate]

    for candidate in candidates:
        normalized_candidate = normalize_text(candidate)
        for norm_col, original_col in normalized_columns.items():
            if normalized_candidate in norm_col:
                return original_col

    return None


def find_column_exata(df, *nomes):
    """Casa o nome INTEIRO da coluna, sem substring.

    `find_column` cai num segundo laco de substring quando nao acha exato, e
    isso escolhe coluna errada em relatorio largo: no Controle de Distribuicao
    (210 colunas) o candidato "Pedido" casava com `Status Pedido > Aguardando`,
    cujo valor e "08-Embarque/Desembarque" — a busca de instrucao por pedido
    nunca casou nada desde que existe (achado 04/09/2026, com o arquivo real).
    Para chave de cruzamento, exato ou nada.
    """
    normalizadas = {normalize_text(c): c for c in df.columns}
    for nome in nomes:
        alvo = normalize_text(nome)
        if alvo in normalizadas:
            return normalizadas[alvo]
    return None


def find_column_detalhe(df, *detalhes):
    """Casa pelo DETALHE de um cabecalho "Grupo > Detalhe", exato.

    Serve quando o grupo varia entre exportacoes mas o detalhe nao — ex.:
    `Embarque/Desembarque(Fabrica) > Instrucao Filial`, em que so "Instrucao
    Filial" e estavel. So aceita se houver UMA coluna com aquele detalhe;
    havendo mais de uma, devolve None em vez de chutar.
    """
    for detalhe in detalhes:
        alvo = normalize_text(detalhe)
        achadas = [c for c in df.columns
                   if normalize_text(str(c).split(">")[-1]) == alvo]
        if len(achadas) == 1:
            return achadas[0]
    return None
