"""
Base unificada de rastreio de frota — junta OpenTech e Ravex numa única
visão por placa (ver MEL-14 no BACKLOG.md). Cada placa é rastreada por só
um dos dois sistemas (confirmado pelo usuário em 27/07/2026), então a união
é uma concatenação simples, sem precisar reconciliar conflito linha a linha.

Todo o resto do sistema (consolidacao_service, redespacho_service,
temperatura_service) deve ler a frota através daqui, não mais direto de
opentech_state — assim um novo sistema de rastreio no futuro só precisa
entrar nessa junção, sem tocar em mais nada.
"""
import pandas as pd

from app.services import opentech_state, ravex_state


def get_df_monitoramento_unificado() -> pd.DataFrame:
    df_ot = opentech_state.get_df_monitoramento()
    df_rx = ravex_state.get_df_ravex()

    partes = [df for df in (df_ot, df_rx) if df is not None and not df.empty]
    if not partes:
        return pd.DataFrame()
    if len(partes) == 1:
        return partes[0]

    unificado = pd.concat(partes, ignore_index=True)
    # Defesa: se por algum motivo a mesma placa aparecer nos dois sistemas ao
    # mesmo tempo (não deveria, mas na dúvida não trava nada), mantém a
    # última — como os dois DataFrames não têm timestamp por linha, "última"
    # aqui é só "a que veio da Ravex" (concatenada por último), critério
    # arbitrário só pra não deixar duplicata.
    return unificado.drop_duplicates(subset=["Placa Cruzamento"], keep="last")
