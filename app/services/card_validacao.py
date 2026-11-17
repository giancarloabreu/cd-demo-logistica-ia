"""
Marcações do card de coligada (regra de data, entregas na frente, observação,
descarga concluída) gravadas pela VIAGEM — fase 3 do painel de carretas,
19/10/2026.

Antes:
- `validacoes` era por (unidade, NF), com a unidade igual à string inteira
  "CDA · CDB"; se a composição do card mudava, a marcação sumia;
- `observacoes` era lida pela PLACA: a observação da viagem anterior aparecia
  na viagem nova da mesma carreta, e a nova voltava à antiga no ciclo seguinte;
- duas cópias do mesmo laço no servidor (upload, restauração, rastreio), uma
  delas procurando "n1/n2" inteiro e nunca casando em card com várias NFs.

Agora cada card grava em `card_validacao` sob várias chaves:
  V:<viagem>|<local>   a AE (ou RVX) da viagem + o local físico do card
                       (CDA·CDB e CNT da mesma carreta são cards distintos);
  N:<placa>|<NF>       uma por nota — vale enquanto o card ainda não tem
                       viagem e mantém a marcação se a viagem mudar.
A leitura pega, campo a campo, o primeiro valor gravado na ordem V → N →
tabelas antigas (por NF; nunca só pela placa). As tabelas antigas seguem
sendo gravadas, para voltar atrás sem perder nada.
"""
from __future__ import annotations

from app.database import get_db
from app.services.carretas_viagem import _agora, _txt, local_de
from app.utils.text_utils import normalize_plate
from app.utils.number_utils import parse_int_br

CAMPOS = {
    # coluna do card        coluna da tabela
    "Regra Data CD":       "regra_cd",
    "Entregas Frente":     "entregas_frente",
    "Entregas Frente Obs": "entregas_frente_obs",
    "Descarga Concluída":  "descarga_concluida",
    "Descarga Iniciada":   "descarga_iniciada",
    "Observação":          "observacao",
}

_tabelas_ok: set = set()


def _garantir(db) -> None:
    from app import database
    chave = str(database.DB_PATH)
    if chave in _tabelas_ok:
        return
    db.executescript("""
        CREATE TABLE IF NOT EXISTS card_validacao (
            usuario             TEXT NOT NULL,
            chave               TEXT NOT NULL,
            regra_cd            TEXT,
            entregas_frente     TEXT,
            entregas_frente_obs TEXT,
            descarga_concluida  TEXT,
            observacao          TEXT,
            updated_at          TEXT NOT NULL,
            PRIMARY KEY (usuario, chave)
        );
    """)
    cols = {r[1] for r in db.execute("PRAGMA table_info(card_validacao)").fetchall()}
    if "descarga_iniciada" not in cols:
        db.execute("ALTER TABLE card_validacao ADD COLUMN descarga_iniciada TEXT")
    _tabelas_ok.add(chave)


def _nfs(texto) -> list[str]:
    return list(dict.fromkeys(n.strip() for n in str(texto or "").split("/")
                              if n.strip() and n.strip().lower() not in ("nan", "none")))


def _unidades(texto) -> list[str]:
    return [u.strip() for u in str(texto or "").split("·") if u.strip()]


def chaves_do_card(row, mapa: dict | None = None) -> list[str]:
    """Chaves de gravação do card, da mais forte para a mais fraca."""
    placa = normalize_plate(row.get("Placa", "") or "") or "-"
    chaves = []
    viagem = str(row.get("Viagem", "") or "").strip()
    unidades = _unidades(row.get("Empresa Compra", ""))
    local = local_de(unidades[0], mapa) if unidades else ""
    if viagem and viagem.lower() not in ("nan", "none"):
        chaves.append(f"V:{viagem}|{local}")
    # sem placa, o número da NF sozinho colide entre emitentes e CDs: a chave
    # leva o local (revisão 19/10/2026)
    pref = f"N:{placa}|" if placa != "-" else f"N:-|{local}|"
    chaves += [pref + nf for nf in _nfs(row.get("NF", ""))]
    return chaves


def _usuario(u: str) -> str:
    return str(u or "").strip().lower()


def gravar(usuario: str, row, campo: str, valor) -> None:
    """Grava um campo do card em todas as chaves dele."""
    col = CAMPOS.get(campo)
    if not col:
        return
    from app.database import get_mapa_consolidacao
    chaves = chaves_do_card(row, get_mapa_consolidacao())
    if not chaves:
        return
    u, agora = _usuario(usuario), _txt(_agora())
    with get_db() as db:
        _garantir(db)
        db.executemany(
            f"""INSERT INTO card_validacao (usuario, chave, {col}, updated_at) VALUES (?,?,?,?)
                ON CONFLICT(usuario, chave) DO UPDATE SET {col}=excluded.{col},
                updated_at=excluded.updated_at""",
            [(u, k, str(valor), agora) for k in chaves])


def _legado(db, usuario: str) -> tuple[dict, dict]:
    """Tabelas antigas: validacoes por (unidade, NF) e observacoes por
    (placa, NF) — a leitura só pela placa, que vazava entre viagens, não
    existe mais."""
    val = {(r["unidade"], r["nf"]): dict(r) for r in db.execute(
        """SELECT unidade, nf, regra_cd, entregas_frente, entregas_frente_obs, descarga_concluida
           FROM validacoes WHERE usuario=?""", (usuario,)).fetchall()}
    obs = {(r["placa"], r["nf"]): r["observacao"] for r in db.execute(
        "SELECT placa, nf, observacao FROM observacoes WHERE usuario=?", (usuario,)).fetchall()}
    return val, obs


def aplicar(df, usuario: str):
    """Reaplica as marcações gravadas aos cards do DataFrame (coligada) e
    limpa "Entregas na frente" de quem já chegou ao CD. Devolve o df."""
    if df is None or df.empty:
        return df
    from app.database import get_mapa_consolidacao, set_validacao
    mapa = get_mapa_consolidacao()
    u = _usuario(usuario)
    df = df.copy()
    for c in CAMPOS:
        if c not in df.columns:
            df[c] = ""
    chaves_por_idx = {idx: chaves_do_card(r, mapa) for idx, r in df.iterrows()}
    todas = sorted({k for ks in chaves_por_idx.values() for k in ks})
    with get_db() as db:
        _garantir(db)
        novo: dict = {}
        for i in range(0, len(todas), 500):
            parte = todas[i:i + 500]
            for r in db.execute(
                    f"SELECT * FROM card_validacao WHERE usuario=? AND chave IN ({','.join('?' * len(parte))})",
                    [u, *parte]).fetchall():
                novo[r["chave"]] = dict(r)
        val_ant, obs_ant = _legado(db, u)
    val_ant_por_nf: dict = {}
    for (un, nf), v in val_ant.items():
        val_ant_por_nf.setdefault(nf, []).append(v)

    limpar_entregas = []
    for idx, r in df.iterrows():
        chaves = chaves_por_idx[idx]
        placa = normalize_plate(r.get("Placa", "") or "")
        nfs = _nfs(r.get("NF", ""))
        # tabela antiga: a unidade gravada era a string do card na época
        # ("CDA · CDB"); casa a NF com qualquer gravação que tenha uma das
        # unidades do card, a exata primeiro
        ec = str(r.get("Empresa Compra", "") or "").strip()
        partes = set(_unidades(ec))
        antigos = []
        for nf in nfs:
            cands = val_ant_por_nf.get(nf, [])
            antigos += [a for a in cands if a["unidade"] == ec]
            antigos += [a for a in cands if a["unidade"] != ec and partes & set(_unidades(a["unidade"]))]

        def valor(col):
            for k in chaves:
                v = (novo.get(k) or {}).get(col)
                if v is not None:
                    return v
            if col == "observacao":
                for nf in nfs:
                    if (placa, nf) in obs_ant and obs_ant[(placa, nf)]:
                        return obs_ant[(placa, nf)]
                return None
            for a in antigos:
                v = a.get(col)
                if v not in (None, "") and not (col in ("regra_cd", "entregas_frente") and v == "Não Verificado"):
                    return v
            return None

        regra = valor("regra_cd")
        if regra and regra != "Não Verificado":
            df.at[idx, "Regra Data CD"] = regra
        entregas = valor("entregas_frente")
        entregas_obs = valor("entregas_frente_obs") or ""
        if entregas == "Sim" and _chegou(r, mapa):
            # já chegou ao CD: "entregas na frente" não se aplica mais (BUG-8)
            entregas, entregas_obs = "Não", ""
            limpar_entregas.append(idx)
        if entregas and entregas != "Não Verificado":
            df.at[idx, "Entregas Frente"] = entregas
        df.at[idx, "Entregas Frente Obs"] = entregas_obs
        df.at[idx, "Descarga Concluída"] = valor("descarga_concluida") or "Não"
        df.at[idx, "Descarga Iniciada"] = valor("descarga_iniciada") or "Não"
        df.at[idx, "Observação"] = valor("observacao") or ""

    for idx in limpar_entregas:
        r = df.loc[idx]
        gravar(u, r, "Entregas Frente", "Não")
        gravar(u, r, "Entregas Frente Obs", "")
        ec = str(r.get("Empresa Compra", "") or "").strip()
        for nf in _nfs(r.get("NF", "")):
            set_validacao(unidade=ec, nf=nf, usuario=u, entregas_frente="Não", entregas_frente_obs="")
    return df


def _chegou(r, mapa) -> bool:
    """KM arredondado em 0 ou estadia aberta no raio do CD do card."""
    if parse_int_br(r.get("KM Restante", "")) == 0:
        return True
    placa = normalize_plate(r.get("Placa", "") or "")
    unidades = _unidades(r.get("Empresa Compra", ""))
    if not placa or not unidades:
        return False
    local = local_de(unidades[0], mapa)
    try:
        from app.services.carretas_viagem import garantir_tabelas
        with get_db() as db:
            garantir_tabelas(db)
            return db.execute(
                """SELECT 1 FROM carretas_estadia WHERE saida_em IS NULL AND local=?
                     AND (placa=? OR placa_carreta=? OR placa_veiculo=?)""",
                (local, placa, placa, placa)).fetchone() is not None
    except Exception:
        return False


def limpar(usuario: str, validacoes: bool = False, observacoes: bool = False) -> None:
    """'Limpar painel': apaga as marcações da conta na tabela nova também."""
    cols = []
    if validacoes:
        cols += ["regra_cd", "entregas_frente", "entregas_frente_obs", "descarga_concluida",
                 "descarga_iniciada"]
    if observacoes:
        cols += ["observacao"]
    if not cols:
        return
    with get_db() as db:
        _garantir(db)
        db.execute(f"UPDATE card_validacao SET {', '.join(c + '=NULL' for c in cols)} WHERE usuario=?",
                   (_usuario(usuario),))
