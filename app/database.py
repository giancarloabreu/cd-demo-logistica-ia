"""
app/database.py
Camada única de acesso ao SQLite.
Substitui: users.json, validacoes.json, redespacho_sel.json, preferencias.json
"""
from __future__ import annotations

from app.utils.paths import DATA_DIR as _DATA_DIR
import hashlib
import hmac
import os
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH    = _DATA_DIR / "monitoramento.db"
_KEY_PATH  = _DATA_DIR / "secret.key"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # leituras simultâneas sem lock
    # sem isso, dois processos escrevendo ao mesmo tempo (ex.: cron do bot
    # Telegram rodando a cada 1 min, cron da coleta Xsense a cada 5 min,
    # OpenTech a cada 3 min — todos no mesmo monitoramento.db) davam
    # "database is locked" na hora em vez de esperar a vez (descoberto
    # 14/08/2026: processar_telegram_bot.py caiu assim quando dois ciclos
    # se sobrepuseram). 5s de espera cobre a folga de qualquer commit
    # rápido dos outros crons.
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── INICIALIZAÇÃO ─────────────────────────────────────────
def init_db():
    """Cria tabelas e usuário padrão se não existirem."""
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            username           TEXT UNIQUE NOT NULL,
            nome_exibicao      TEXT DEFAULT '',
            pwd_hash           TEXT NOT NULL,
            perfil             TEXT DEFAULT 'operador',
            unidades_permitidas TEXT DEFAULT '',
            modulos_permitidos TEXT DEFAULT '',
            cards_por_linha    INTEGER DEFAULT 4,
            operador_call_center INTEGER DEFAULT 0,
            ativo              INTEGER DEFAULT 1,
            created_at         DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        -- _chk placeholder removido, migração feita por ALTER TABLE abaixo

        CREATE TABLE IF NOT EXISTS preferencias (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS validacoes (
            unidade     TEXT NOT NULL DEFAULT '',
            nf          TEXT NOT NULL,
            regra_cd    TEXT DEFAULT 'Não Verificado',
            entregas_frente TEXT DEFAULT 'Não Verificado',
            updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (unidade, nf)
        );

        CREATE TABLE IF NOT EXISTS redespacho_sel (
            placa      TEXT PRIMARY KEY,
            selecionado INTEGER DEFAULT 0,
            updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS observacoes (
            placa      TEXT NOT NULL,
            nf         TEXT NOT NULL,
            observacao TEXT DEFAULT '',
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (placa, nf)
        );

        CREATE TABLE IF NOT EXISTS cds_localizacao (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            sigla      TEXT UNIQUE NOT NULL,
            latitude   TEXT NOT NULL DEFAULT '',
            longitude  TEXT NOT NULL DEFAULT '',
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- Conta "dona" dos dados de cada CD (Armazém, Adensamento, etc.) —
        -- Coordenador/Analista/Qualidade/Operador daquele CD sempre leem/
        -- gravam nela (ver app/utils/access_control.usuario_efetivo).
        -- Redesenho de papéis, pedido do usuário 24/08/2026.
        CREATE TABLE IF NOT EXISTS cd_conta_canonica (
            cd_sigla TEXT PRIMARY KEY,
            usuario  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS historico_temperatura (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            placa       TEXT NOT NULL,
            temperatura REAL NOT NULL,
            timestamp   TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_historico_temp_placa
            ON historico_temperatura(placa, timestamp);

        CREATE TABLE IF NOT EXISTS log_auditoria (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario   TEXT NOT NULL DEFAULT '',
            acao      TEXT NOT NULL,
            alvo      TEXT NOT NULL DEFAULT '',
            detalhe   TEXT NOT NULL DEFAULT '',
            ip        TEXT NOT NULL DEFAULT '',
            timestamp TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_log_auditoria_ts ON log_auditoria(timestamp);
        CREATE INDEX IF NOT EXISTS idx_log_auditoria_usuario ON log_auditoria(usuario, timestamp);
        CREATE INDEX IF NOT EXISTS idx_log_auditoria_acao ON log_auditoria(acao, timestamp);

        """)

        # Migração segura: adiciona colunas novas se banco vier de versão anterior
        for col, definition in [
            ("perfil",             "TEXT DEFAULT 'admin'"),
            ("nome_exibicao",      "TEXT DEFAULT ''"),
            ("unidades_permitidas","TEXT DEFAULT ''"),
            ("unidades_vinculadas","TEXT DEFAULT ''"),
            ("ativo",              "INTEGER DEFAULT 1"),
        ]:
            try:
                db.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
            except Exception:
                pass  # coluna já existe

        # Garante que o primeiro usuário criado é sempre admin
        db.execute("UPDATE users SET perfil='admin' WHERE perfil IS NULL OR perfil=''")

        # Cria usuário padrão se não existir nenhum
        count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count == 0:
            # senha do primeiro admin: variável ADMIN_PASSWORD ou aleatória
            # (mostrada uma vez no log de quem sobe o servidor)
            senha = os.environ.get("ADMIN_PASSWORD") or secrets.token_urlsafe(12)
            _criar_usuario_db(db, "admin", senha, perfil="admin")
            if not os.environ.get("ADMIN_PASSWORD"):
                print(f"[DB] Usuário padrão criado: admin / {senha} (troque no primeiro acesso)")

        # Migra preferencias.json se existir
        _migrar_json(db)

    # Migra colunas de usuario se banco antigo
    _migrate_db()


def _migrate_db():
    """Migra banco existente para estrutura multi-usuario."""
    try:
        with get_db() as db:
            # validacoes — adiciona coluna usuario
            cols_val = [r[1] for r in db.execute("PRAGMA table_info(validacoes)").fetchall()]
            if "usuario" not in cols_val:
                db.execute("ALTER TABLE validacoes ADD COLUMN usuario TEXT NOT NULL DEFAULT ''")
                print("[DB] Migração: coluna usuario adicionada em validacoes")

            # redespacho_sel — recria com estrutura correta (id, usuario, placa)
            cols_red = [r[1] for r in db.execute("PRAGMA table_info(redespacho_sel)").fetchall()]
            if "id" not in cols_red:
                db.executescript("""
                    DROP TABLE IF EXISTS redespacho_sel_old;
                    ALTER TABLE redespacho_sel RENAME TO redespacho_sel_old;
                    CREATE TABLE IF NOT EXISTS redespacho_sel (
                        id      INTEGER PRIMARY KEY AUTOINCREMENT,
                        usuario TEXT NOT NULL DEFAULT '',
                        placa   TEXT NOT NULL,
                        UNIQUE(usuario, placa)
                    );
                    DROP TABLE IF EXISTS redespacho_sel_old;
                """)
                print("[DB] Migração: redespacho_sel recriada com id e usuario")

            # observacoes — adiciona coluna usuario
            cols_obs = [r[1] for r in db.execute("PRAGMA table_info(observacoes)").fetchall()]
            if "usuario" not in cols_obs:
                db.execute("ALTER TABLE observacoes ADD COLUMN usuario TEXT NOT NULL DEFAULT ''")
                print("[DB] Migração: coluna usuario adicionada em observacoes")
            cols_obs2 = [r[1] for r in db.execute("PRAGMA table_info(observacoes)").fetchall()]
            if "empresa_compra" not in cols_obs2:
                db.execute("ALTER TABLE observacoes ADD COLUMN empresa_compra TEXT NOT NULL DEFAULT ''")
                print("[DB] Migração: empresa_compra adicionada em observacoes")
            cols_val2 = [r[1] for r in db.execute("PRAGMA table_info(validacoes)").fetchall()]
            if "placa" not in cols_val2:
                db.execute("ALTER TABLE validacoes ADD COLUMN placa TEXT NOT NULL DEFAULT ''")
                print("[DB] Migração: placa adicionada em validacoes")
            if "empresa_compra" not in cols_val2:
                db.execute("ALTER TABLE validacoes ADD COLUMN empresa_compra TEXT NOT NULL DEFAULT ''")
                print("[DB] Migração: empresa_compra adicionada em validacoes")
            # entregas_frente_obs / descarga_concluida — 27/08/2026: campo
            # livre "qual é a entrega na frente" (só aparece com Entregas
            # Frente=Sim) e o toggle "Descarga Concluída" do rodapé do card
            if "entregas_frente_obs" not in cols_val2:
                db.execute("ALTER TABLE validacoes ADD COLUMN entregas_frente_obs TEXT DEFAULT ''")
                print("[DB] Migração: entregas_frente_obs adicionada em validacoes")
            if "descarga_concluida" not in cols_val2:
                db.execute("ALTER TABLE validacoes ADD COLUMN descarga_concluida TEXT DEFAULT 'Não'")
                print("[DB] Migração: descarga_concluida adicionada em validacoes")
            # descarga iniciada (19/10/2026): o card passa a "Em desembarque no CD"
            if "descarga_iniciada" not in cols_val2:
                db.execute("ALTER TABLE validacoes ADD COLUMN descarga_iniciada TEXT DEFAULT 'Não'")
                print("[DB] Migração: descarga_iniciada adicionada em validacoes")

            # cards_por_linha — adiciona se não existir
            cols_users = [r[1] for r in db.execute("PRAGMA table_info(users)").fetchall()]
            if "cards_por_linha" not in cols_users:
                db.execute("ALTER TABLE users ADD COLUMN cards_por_linha INTEGER DEFAULT 4")
                print("[DB] Migração: coluna cards_por_linha adicionada em users")

            # modulos_permitidos — adiciona se não existir (MEL-3, vazio = todos os módulos)
            if "modulos_permitidos" not in cols_users:
                db.execute("ALTER TABLE users ADD COLUMN modulos_permitidos TEXT DEFAULT ''")
                print("[DB] Migração: coluna modulos_permitidos adicionada em users")

            # changelog_visto — última versão do changelog que o usuário já viu
            # e confirmou (MEL-10, tela de novidades no login)
            if "changelog_visto" not in cols_users:
                db.execute("ALTER TABLE users ADD COLUMN changelog_visto TEXT DEFAULT ''")
                print("[DB] Migração: coluna changelog_visto adicionada em users")

            # unidades_vinculadas — CDs que essa conta é DONA de verdade
            # (redesenho Local×Empresa/controle de acesso, 29/08/2026).
            # Diferente de unidades_permitidas (o que a conta pode VER —
            # pode incluir unidades de outros donos, liberadas pelo admin
            # pra cobertura temporária): vinculadas é o que alimenta
            # cd_conta_canonica (dono real dos dados de Armazém/Xsense/
            # Adensamento) e o nome inicial de CD1/CD2 em "Configurar CD".
            # Sem migração de dado — contas existentes ficam com vinculada
            # vazia até o admin editar o cadastro (cd_conta_canonica já
            # populada manualmente pro caso CDA/CDB continua valendo como
            # está, não é sobrescrita por estar vazia aqui).
            if "unidades_vinculadas" not in cols_users:
                db.execute("ALTER TABLE users ADD COLUMN unidades_vinculadas TEXT DEFAULT ''")
                print("[DB] Migração: coluna unidades_vinculadas adicionada em users")

            # operador_call_center — marcação que abre o sistema direto em
            # Monitoramento de Entregas (15/11/2026). É um flag independente
            # do perfil; 0 = usuário comum, não muda nada para quem já existe.
            if "operador_call_center" not in cols_users:
                db.execute("ALTER TABLE users ADD COLUMN operador_call_center INTEGER DEFAULT 0")
                print("[DB] Migração: coluna operador_call_center adicionada em users")

            # unificação admin/master (29/08/2026, pedido do usuário) — perfil
            # 'master' deixou de existir como nível à parte; contas antigas
            # viram 'admin' (mesmo acesso de sempre, sem perda nenhuma —
            # is_master() já foi atualizada pra checar perfil=='admin').
            n_master = db.execute("SELECT COUNT(*) c FROM users WHERE perfil='master'").fetchone()["c"]
            if n_master:
                db.execute("UPDATE users SET perfil='admin' WHERE perfil='master'")
                print(f"[DB] Migração: {n_master} conta(s) 'master' migrada(s) pra 'admin' (perfis unificados)")

            # validacoes — PK precisa incluir usuario, senão set_validacao() nunca
            # conseguia persistir por usuário (a query de leitura filtra por
            # usuario, mas a de escrita gravava tudo com usuario='' e colidia
            # entre usuários diferentes na mesma unidade+nf) — ver BACKLOG.
            info_val = db.execute("PRAGMA table_info(validacoes)").fetchall()
            usuario_e_pk = any(r[1] == "usuario" and r[5] > 0 for r in info_val)
            if not usuario_e_pk:
                db.executescript("""
                    DROP TABLE IF EXISTS validacoes_old;
                    ALTER TABLE validacoes RENAME TO validacoes_old;
                    CREATE TABLE validacoes (
                        unidade         TEXT NOT NULL DEFAULT '',
                        nf              TEXT NOT NULL,
                        usuario         TEXT NOT NULL DEFAULT '',
                        regra_cd        TEXT DEFAULT 'Não Verificado',
                        entregas_frente TEXT DEFAULT 'Não Verificado',
                        placa           TEXT NOT NULL DEFAULT '',
                        empresa_compra  TEXT NOT NULL DEFAULT '',
                        entregas_frente_obs TEXT DEFAULT '',
                        descarga_concluida  TEXT DEFAULT 'Não',
                        descarga_iniciada   TEXT DEFAULT 'Não',
                        updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (unidade, nf, usuario)
                    );
                    -- as duas colunas de 27/08 entram na cópia: num banco
                    -- novo esta recriação rodava logo depois do ALTER que as
                    -- criou e as apagava (18/10/2026)
                    INSERT OR IGNORE INTO validacoes
                        (unidade,nf,usuario,regra_cd,entregas_frente,placa,empresa_compra,
                         entregas_frente_obs,descarga_concluida,descarga_iniciada,updated_at)
                        SELECT unidade,nf,usuario,regra_cd,entregas_frente,placa,empresa_compra,
                               entregas_frente_obs,descarga_concluida,descarga_iniciada,updated_at
                        FROM validacoes_old;
                    DROP TABLE IF EXISTS validacoes_old;
                """)
                print("[DB] Migração: validacoes recriada com PK (unidade, nf, usuario)")
    except Exception as e:
        print(f"[DB] Aviso migração: {e}")


def _migrar_json(db: sqlite3.Connection):
    """Importa dados dos JSONs antigos para o SQLite (roda só uma vez)."""
    import json
    config_dir = Path(__file__).parent.parent / "config"

    # Preferências
    pref_file = config_dir / "preferencias.json"
    if pref_file.exists():
        try:
            prefs = json.loads(pref_file.read_text("utf-8"))
            for k, v in prefs.items():
                db.execute("INSERT OR IGNORE INTO preferencias(key,value) VALUES(?,?)",
                           (k, json.dumps(v) if isinstance(v, dict) else str(v)))
            pref_file.rename(pref_file.with_suffix(".json.migrated"))
            print("[DB] preferencias.json migrado")
        except Exception as e:
            print(f"[DB] Aviso ao migrar preferencias: {e}")

    # Validações
    val_file = config_dir / "validacoes.json"
    if val_file.exists():
        try:
            vals = json.loads(val_file.read_text("utf-8"))
            for key, data in vals.items():
                nf, pedido = key.split("|", 1) if "|" in key else (key, "")
                db.execute(
                    "INSERT OR IGNORE INTO validacoes(nf,pedido,regra_cd) VALUES(?,?,?)",
                    (nf, pedido, data.get("regra_data_cd", "Não Verificado"))
                )
            val_file.rename(val_file.with_suffix(".json.migrated"))
            print("[DB] validacoes.json migrado")
        except Exception as e:
            print(f"[DB] Aviso ao migrar validacoes: {e}")

    # Redespacho selecionados
    red_file = config_dir / "redespacho_sel.json"
    if red_file.exists():
        try:
            sel = json.loads(red_file.read_text("utf-8"))
            for placa in (sel if isinstance(sel, list) else []):
                db.execute("INSERT OR IGNORE INTO redespacho_sel(placa,selecionado) VALUES(?,1)", (placa,))
            red_file.rename(red_file.with_suffix(".json.migrated"))
            print("[DB] redespacho_sel.json migrado")
        except Exception as e:
            print(f"[DB] Aviso ao migrar redespacho_sel: {e}")

    # Users (só lê, não remove o json para segurança)
    users_file = config_dir / "users.json"
    if users_file.exists():
        try:
            users = json.loads(users_file.read_text("utf-8"))
            for username, pwd in users.items():
                exists = db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone()
                if not exists:
                    _criar_usuario_db(db, username, pwd)
            users_file.rename(users_file.with_suffix(".json.migrated"))
            print("[DB] users.json migrado")
        except Exception as e:
            print(f"[DB] Aviso ao migrar users: {e}")


# ── SENHAS ────────────────────────────────────────────────
def _hash_pwd(password: str, salt: str | None = None) -> str:
    """PBKDF2-SHA256 com salt — seguro sem bcrypt."""
    salt = salt or secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return f"{salt}${key.hex()}"


def _verify_pwd(password: str, stored: str) -> bool:
    try:
        salt, _ = stored.split("$", 1)
        expected = _hash_pwd(password, salt)
        return hmac.compare_digest(expected, stored)
    except Exception:
        return False


# Hash fixo (sem significado) só para gastar o mesmo tempo de PBKDF2 quando o
# usuário não existe — evita enumerar usuários válidos por diferença de
# tempo de resposta no login (ver verificar_login).
_DUMMY_PWD_HASH = _hash_pwd("dummy-timing-safety-placeholder")


def _criar_usuario_db(db, username, password, perfil='visualizador',
                      nome_exibicao='', unidades_permitidas='', modulos_permitidos='',
                      unidades_vinculadas='', operador_call_center=0):
    db.execute(
        'INSERT OR REPLACE INTO users'
        '(username,pwd_hash,perfil,nome_exibicao,unidades_permitidas,modulos_permitidos,unidades_vinculadas,operador_call_center) VALUES(?,?,?,?,?,?,?,?)',
        (username.strip().lower(), _hash_pwd(password),
         perfil, nome_exibicao, unidades_permitidas, modulos_permitidos, unidades_vinculadas,
         int(operador_call_center or 0))
    )


def _garantir_entregas(modulos_permitidos: str, operador_call_center) -> str:
    """Operador de call center pousa em /entregas; garante o módulo entregas
    liberado quando há restrição de módulos. Vazio = todos os módulos, já
    cobre entregas — não mexe nesse caso."""
    if not operador_call_center:
        return modulos_permitidos
    mods = [m.strip() for m in (modulos_permitidos or "").split(",") if m.strip()]
    if mods and "entregas" not in mods:
        mods.append("entregas")
        return ",".join(mods)
    return modulos_permitidos


def _siglas(csv: str) -> list[str]:
    return [s.strip().upper() for s in (csv or "").split(",") if s.strip()]


def sincronizar_vinculacao_cd(db, username: str, unidades_vinculadas: str) -> str | None:
    """Sincroniza cd_conta_canonica com as unidades vinculadas dessa conta —
    cada sigla vinculada aponta pro `username` como dono real dos dados de
    Armazém/Xsense/Adensamento (ver app/utils/access_control.usuario_efetivo).
    Remove vínculos antigos que não estão mais na lista nova, adiciona os
    novos. Recusa (devolve msg de erro) se alguma sigla já pertence a OUTRA
    conta — evita roubar dono sem querer; admin precisa desvincular a outra
    conta primeiro, à mão. Devolve None se deu tudo certo."""
    username = username.strip().lower()
    novas = set(_siglas(unidades_vinculadas))

    for sigla in novas:
        row = db.execute("SELECT usuario FROM cd_conta_canonica WHERE cd_sigla=?", (sigla,)).fetchone()
        if row and row["usuario"] != username:
            return (f"A sigla '{sigla}' já está vinculada à conta '{row['usuario']}'. "
                    "Remova esse vínculo antes de vincular a esta conta.")

    atuais = {r["cd_sigla"] for r in db.execute(
        "SELECT cd_sigla FROM cd_conta_canonica WHERE usuario=?", (username,)).fetchall()}
    for sigla in atuais - novas:
        db.execute("DELETE FROM cd_conta_canonica WHERE cd_sigla=? AND usuario=?", (sigla, username))
    for sigla in novas - atuais:
        db.execute("INSERT OR REPLACE INTO cd_conta_canonica(cd_sigla, usuario) VALUES (?,?)", (sigla, username))
    return None


def _uniao_permitidas_com_vinculadas(unidades_permitidas: str, unidades_vinculadas: str) -> str:
    """unidades_permitidas nunca pode ser menor que unidades_vinculadas —
    não faz sentido a conta ser DONA de um CD e não poder nem VER ele.
    União simples, preserva a ordem das permitidas digitadas e acrescenta
    vinculadas que estejam faltando no fim."""
    permitidas = _siglas(unidades_permitidas)
    vinculadas = _siglas(unidades_vinculadas)
    faltando = [s for s in vinculadas if s not in permitidas]
    return ",".join(permitidas + faltando)


# ── API USUÁRIOS ──────────────────────────────────────────
def verificar_login(username: str, password: str):
    """Retorna dict com dados do user se login OK, ou None se falhar."""
    with get_db() as db:
        row = db.execute(
            'SELECT * FROM users WHERE username=? AND ativo=1',
            (username.strip().lower(),)
        ).fetchone()
    # Sempre roda o PBKDF2 (contra hash real ou dummy) mesmo se o usuário não
    # existir — senão a ausência do usuário responde bem mais rápido que uma
    # senha errada, dando pra enumerar usuários válidos por timing.
    senha_ok = _verify_pwd(password, row['pwd_hash'] if row else _DUMMY_PWD_HASH)
    if not row or not senha_ok:
        return None
    return {
        'username':           row['username'],
        'nome_exibicao':      row['nome_exibicao'] or row['username'],
        'perfil':             row['perfil'],
        'unidades_permitidas': row['unidades_permitidas'],
        'unidades_vinculadas': row['unidades_vinculadas'] or '',
        'modulos_permitidos': row['modulos_permitidos'] or '',
        'operador_call_center': row['operador_call_center'] if 'operador_call_center' in row.keys() else 0,
    }


def listar_usuarios() -> list:
    with get_db() as db:
        rows = db.execute(
            "SELECT id,username,nome_exibicao,perfil,unidades_permitidas,unidades_vinculadas,modulos_permitidos,operador_call_center,ativo,created_at FROM users ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def validar_senha(senha: str) -> tuple[bool, str]:
    """
    Valida política de senha:
    - Mínimo 8 caracteres
    - Pelo menos 1 letra maiúscula
    - Pelo menos 1 número
    - Pelo menos 1 caractere especial (!@#$%^&*()_+-=[]{}|;:,.<>?)
    """
    import re
    if len(senha) < 8:
        return False, "A senha deve ter pelo menos 8 caracteres."
    if not re.search(r"[A-Z]", senha):
        return False, "A senha deve ter pelo menos uma letra maiúscula."
    if not re.search(r"[0-9]", senha):
        return False, "A senha deve ter pelo menos um número."
    if not re.search(r"[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]", senha):
        return False, "A senha deve ter pelo menos um caractere especial (!@#$%^&* etc)."
    return True, "OK"


def criar_usuario(username: str, password: str, perfil: str = "visualizador",
                  nome_exibicao: str = "", unidades_permitidas: str = "",
                  modulos_permitidos: str = "", unidades_vinculadas: str = "",
                  operador_call_center: int = 0) -> tuple[bool, str]:
    username = username.strip().lower()
    if not username or len(username) < 3:
        return False, "Usuário deve ter pelo menos 3 caracteres."
    ok_pwd, msg_pwd = validar_senha(password)
    if not ok_pwd:
        return False, msg_pwd
    unidades_permitidas = _uniao_permitidas_com_vinculadas(unidades_permitidas, unidades_vinculadas)
    modulos_permitidos = _garantir_entregas(modulos_permitidos, operador_call_center)
    try:
        with get_db() as db:
            erro = sincronizar_vinculacao_cd(db, username, unidades_vinculadas)
            if erro:
                return False, erro
            _criar_usuario_db(db, username, password, perfil, nome_exibicao,
                               unidades_permitidas, modulos_permitidos, unidades_vinculadas,
                               operador_call_center)
        # set_cd_usuario abre sua própria conexão (get_db() aninhado enquanto
        # a transação acima ainda não commitou trava a tabela) — chama só
        # DEPOIS que o `with` acima fechou e commitou. CD1/CD2 iniciais em
        # "Configurar CD" = as siglas vinculadas, só na CRIAÇÃO (conta nova,
        # cd_config sempre vazio ainda); só cosmético, o dono pode trocar o
        # texto depois sem afetar o vínculo real (pedido do usuário
        # 29/08/2026: "sem alterar do cadastro, senão ele poderia acessar
        # qualquer unidade").
        siglas = _siglas(unidades_vinculadas)
        if siglas:
            set_cd_usuario(username, siglas[0], "", "", nome2=(siglas[1] if len(siglas) > 1 else ""))
        return True, f"Usuário '{username}' criado."
    except Exception as e:
        if "UNIQUE" in str(e):
            return False, f"Usuário '{username}' já existe."
        return False, str(e)


def editar_usuario(user_id: int, nome_exibicao: str, perfil: str,
                   unidades_permitidas: str, ativo: int,
                   nova_senha: str = "", modulos_permitidos: str = "",
                   unidades_vinculadas: str = "", operador_call_center: int = 0) -> tuple[bool, str]:
    unidades_permitidas = _uniao_permitidas_com_vinculadas(unidades_permitidas, unidades_vinculadas)
    modulos_permitidos = _garantir_entregas(modulos_permitidos, operador_call_center)
    occ = int(operador_call_center or 0)
    with get_db() as db:
        row = db.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            return False, "Usuário não encontrado."
        erro = sincronizar_vinculacao_cd(db, row["username"], unidades_vinculadas)
        if erro:
            return False, erro
        if nova_senha:
            ok_pwd, msg_pwd = validar_senha(nova_senha)
            if not ok_pwd:
                return False, msg_pwd
            db.execute(
                "UPDATE users SET nome_exibicao=?,perfil=?,unidades_permitidas=?,modulos_permitidos=?,ativo=?,pwd_hash=?,unidades_vinculadas=?,operador_call_center=? WHERE id=?",
                (nome_exibicao, perfil, unidades_permitidas, modulos_permitidos, ativo, _hash_pwd(nova_senha), unidades_vinculadas, occ, user_id)
            )
        else:
            db.execute(
                "UPDATE users SET nome_exibicao=?,perfil=?,unidades_permitidas=?,modulos_permitidos=?,ativo=?,unidades_vinculadas=?,operador_call_center=? WHERE id=?",
                (nome_exibicao, perfil, unidades_permitidas, modulos_permitidos, ativo, unidades_vinculadas, occ, user_id)
            )
    return True, "Usuário atualizado."


def excluir_usuario(user_id: int, admin_username: str) -> tuple[bool, str]:
    with get_db() as db:
        row = db.execute("SELECT username,perfil FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            return False, "Usuário não encontrado."
        if row["username"] == admin_username:
            return False, "Você não pode excluir seu próprio usuário."
        if row["perfil"] == "admin":
            admins = db.execute("SELECT COUNT(*) FROM users WHERE perfil='admin' AND ativo=1").fetchone()[0]
            if admins <= 1:
                return False, "Não é possível excluir o único Admin ativo."
        db.execute("DELETE FROM users WHERE id=?", (user_id,))
        # chave da extensão Coletor Ravex do login: não pode voltar a valer
        # se outro login for criado com o mesmo nome
        try:
            db.execute("DELETE FROM ravex_coletores WHERE usuario=?", (row["username"].strip().lower(),))
        except sqlite3.OperationalError:
            pass                                    # tabela ainda não criada
    return True, "Usuário excluído."


def trocar_senha(username: str, senha_atual: str, nova_senha: str) -> tuple[bool, str]:
    if not verificar_login(username, senha_atual):
        return False, "Senha atual incorreta."
    ok_pwd, msg_pwd = validar_senha(nova_senha)
    if not ok_pwd:
        return False, msg_pwd
    with get_db() as db:
        db.execute(
            "UPDATE users SET pwd_hash=? WHERE username=?",
            (_hash_pwd(nova_senha), username.strip().lower())
        )
    return True, "Senha alterada com sucesso."


# ── API PREFERÊNCIAS ──────────────────────────────────────
def get_preferencia(key: str, default=None):
    import json
    with get_db() as db:
        row = db.execute("SELECT value FROM preferencias WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except Exception:
        return row["value"]


def set_preferencia(key: str, value):
    import json
    val = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
    with get_db() as db:
        db.execute("INSERT OR REPLACE INTO preferencias(key,value) VALUES(?,?)", (key, val))


def _get_fernet():
    """Carrega ou gera a chave de criptografia local (AES via Fernet)."""
    from cryptography.fernet import Fernet
    _KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _KEY_PATH.exists():
        key = _KEY_PATH.read_bytes()
    else:
        key = Fernet.generate_key()
        _KEY_PATH.write_bytes(key)
        try:
            os.chmod(_KEY_PATH, 0o600)
        except Exception:
            pass
    return Fernet(key)


def _encrypt(plain: str) -> str:
    if not plain:
        return ""
    f = _get_fernet()
    return f.encrypt(plain.encode("utf-8")).decode("utf-8")


def _decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        f = _get_fernet()
        return f.decrypt(token.encode("utf-8")).decode("utf-8")
    except Exception:
        return ""


# ── CREDENCIAIS OPENTECH (configuração global, qualquer master edita) ────

def get_opentech_config() -> dict:
    """Retorna config OpenTech sem expor a senha em texto puro."""
    cfg = get_preferencia("opentech", {})
    return {
        "usuario":  cfg.get("usuario", ""),
        "has_senha": bool(cfg.get("senha_enc", "")),
        "intervalo_min": int(cfg.get("intervalo_min", 20)),
        "status": cfg.get("status", ""),         # "ok" | "falha" | ""
        "ultima_atualizacao": cfg.get("ultima_atualizacao", ""),
        "atualizando": bool(cfg.get("atualizando", False)),
    }


def get_opentech_credenciais() -> dict:
    """Retorna usuario/senha em texto puro para uso interno (automação)."""
    cfg = get_preferencia("opentech", {})
    return {
        "usuario": cfg.get("usuario", ""),
        "senha":   _decrypt(cfg.get("senha_enc", "")),
        "intervalo_min": int(cfg.get("intervalo_min", 20)),
    }


def set_opentech_config(usuario: str = None, senha: str = None, intervalo_min: int = None) -> None:
    """Atualiza config OpenTech. Passa None para não alterar o campo."""
    cfg = get_preferencia("opentech", {})
    if usuario is not None:
        cfg["usuario"] = usuario.strip()
    if senha:  # só sobrescreve se senha não-vazia foi enviada
        cfg["senha_enc"] = _encrypt(senha)
    if intervalo_min is not None:
        cfg["intervalo_min"] = max(1, int(intervalo_min))
    set_preferencia("opentech", cfg)


def set_opentech_status(status: str, ultima_atualizacao: str) -> None:
    """Atualiza status da última tentativa de atualização automática."""
    cfg = get_preferencia("opentech", {})
    cfg["status"] = status
    cfg["ultima_atualizacao"] = ultima_atualizacao
    set_preferencia("opentech", cfg)


def set_opentech_atualizando(ativo: bool) -> None:
    """Marca se um ciclo de atualização (download Playwright) está em andamento.
    Usado para o processo do Flask/Gunicorn saber disso mesmo quando a
    automação roda em outro processo (script de cron — ver BUG-5)."""
    cfg = get_preferencia("opentech", {})
    cfg["atualizando"] = bool(ativo)
    set_preferencia("opentech", cfg)


# ── CREDENCIAIS RAVEX (configuração global, qualquer master edita) ───────
# Espelha exatamente o padrão da OpenTech acima (mesma criptografia AES via
# Fernet, mesmo formato de config) — sistema de rastreio de um segundo grupo
# de placas que não aparece na OpenTech (ver MEL-14 no BACKLOG).

def get_ravex_config() -> dict:
    """Retorna config Ravex sem expor a senha em texto puro."""
    cfg = get_preferencia("ravex", {})
    return {
        "usuario":  cfg.get("usuario", ""),
        "has_senha": bool(cfg.get("senha_enc", "")),
        "intervalo_min": int(cfg.get("intervalo_min", 20)),
        "status": cfg.get("status", ""),         # "ok" | "falha" | ""
        "ultima_atualizacao": cfg.get("ultima_atualizacao", ""),
        "atualizando": bool(cfg.get("atualizando", False)),
    }


def get_ravex_credenciais() -> dict:
    """Retorna usuario/senha em texto puro para uso interno (automação)."""
    cfg = get_preferencia("ravex", {})
    return {
        "usuario": cfg.get("usuario", ""),
        "senha":   _decrypt(cfg.get("senha_enc", "")),
        "intervalo_min": int(cfg.get("intervalo_min", 20)),
    }


def set_ravex_config(usuario: str = None, senha: str = None, intervalo_min: int = None) -> None:
    """Atualiza config Ravex. Passa None para não alterar o campo."""
    cfg = get_preferencia("ravex", {})
    if usuario is not None:
        cfg["usuario"] = usuario.strip()
    if senha:  # só sobrescreve se senha não-vazia foi enviada
        cfg["senha_enc"] = _encrypt(senha)
    if intervalo_min is not None:
        cfg["intervalo_min"] = max(1, int(intervalo_min))
    set_preferencia("ravex", cfg)


# ── CREDENCIAIS XSENSE (configuração global, qualquer master edita) ──────
# Mesmo padrão de OpenTech/Ravex acima. `node_id` é o nó da árvore de
# instalações do portal Xsense (ex.: o CD Demo) — cacheado depois da primeira
# sincronização bem-sucedida pra não precisar redescobrir a cada ciclo, mas
# pode ficar vazio (o sincronizador detecta sozinho na primeira vez).

def get_xsense_config() -> dict:
    """Retorna config Xsense sem expor a senha em texto puro."""
    cfg = get_preferencia("xsense", {})
    return {
        "usuario":  cfg.get("usuario", ""),
        "has_senha": bool(cfg.get("senha_enc", "")),
        "node_id": cfg.get("node_id", ""),
        "status": cfg.get("status", ""),         # "ok" | "falha" | ""
        "ultima_atualizacao": cfg.get("ultima_atualizacao", ""),
        "atualizando": bool(cfg.get("atualizando", False)),
    }


def get_xsense_credenciais() -> dict:
    """Retorna usuario/senha em texto puro para uso interno (automação)."""
    cfg = get_preferencia("xsense", {})
    return {
        "usuario": cfg.get("usuario", ""),
        "senha":   _decrypt(cfg.get("senha_enc", "")),
        "node_id": cfg.get("node_id", ""),
    }


def set_xsense_config(usuario: str = None, senha: str = None, node_id: str = None) -> None:
    """Atualiza config Xsense. Passa None para não alterar o campo."""
    cfg = get_preferencia("xsense", {})
    credencial_mudou = False
    if usuario is not None and usuario.strip() != cfg.get("usuario", ""):
        cfg["usuario"] = usuario.strip()
        credencial_mudou = True
    if senha:  # só sobrescreve se senha não-vazia foi enviada
        cfg["senha_enc"] = _encrypt(senha)
        credencial_mudou = True
    if node_id is not None:
        cfg["node_id"] = node_id.strip()
    set_preferencia("xsense", cfg)
    if credencial_mudou:
        # sem isso, o cookie de sessão cacheado (fora do banco, em
        # data/xsense/sessao_cookies.json) continua sendo reaproveitado com
        # a conta ANTIGA até expirar sozinho — achado ao vivo 29/08/2026,
        # ver invalidar_sessao_cache().
        try:
            from app.services.xsense_scraper import invalidar_sessao_cache
            invalidar_sessao_cache()
        except Exception:
            pass


def set_xsense_status(status: str, ultima_atualizacao: str) -> None:
    cfg = get_preferencia("xsense", {})
    cfg["status"] = status
    cfg["ultima_atualizacao"] = ultima_atualizacao
    set_preferencia("xsense", cfg)


# ── BOT TELEGRAM GENÉRICO (por chave em `preferencias`) ────────────────
# Cada bot tem a SUA chave (19/10/2026, bot de carretas = "carretas_telegram"):
# dois bots na mesma chave fariam o cron de um regravar o offset do outro.
# Token e segredo do webhook cifrados (Fernet), como as senhas.

def get_telegram_bot_config(chave: str) -> dict:
    cfg = get_preferencia(chave, {}) or {}
    return {
        "has_token": bool(cfg.get("token_enc", "")),
        "bot_username": cfg.get("bot_username", ""),
        "bot_id": cfg.get("bot_id", ""),
        "offset_updates": int(cfg.get("offset_updates", 0) or 0),
        "webhook_ativo": bool(cfg.get("webhook_ativo", False)),
        "site_url": cfg.get("site_url", ""),
        "contas_por_local": dict(cfg.get("contas_por_local", {}) or {}),
    }


def get_telegram_bot_token(chave: str) -> str:
    cfg = get_preferencia(chave, {}) or {}
    return _decrypt(cfg.get("token_enc", ""))


def get_telegram_bot_webhook_secret(chave: str) -> str:
    cfg = get_preferencia(chave, {}) or {}
    secret = _decrypt(cfg.get("webhook_secret_enc", ""))
    if not secret:
        import secrets as _secrets
        secret = _secrets.token_urlsafe(32)
        cfg["webhook_secret_enc"] = _encrypt(secret)
        set_preferencia(chave, cfg)
    return secret


def set_telegram_bot_config(chave: str, token: str = None, bot_username: str = None, bot_id: str = None,
                            webhook_ativo: bool = None, site_url: str = None,
                            contas_por_local: dict = None, offset_updates: int = None,
                            apagar_token: bool = False) -> None:
    """Altera só os campos informados (None = mantém)."""
    cfg = get_preferencia(chave, {}) or {}
    if apagar_token:
        for k in ("token_enc", "bot_username", "bot_id", "webhook_secret_enc"):
            cfg.pop(k, None)
        cfg["webhook_ativo"] = False
    if token:
        cfg["token_enc"] = _encrypt(token)
    if bot_username is not None:
        cfg["bot_username"] = bot_username.strip()
    if bot_id is not None:
        cfg["bot_id"] = str(bot_id).strip()
    if webhook_ativo is not None:
        cfg["webhook_ativo"] = bool(webhook_ativo)
    if site_url is not None:
        cfg["site_url"] = site_url.strip().rstrip("/")
    if contas_por_local is not None:
        cfg["contas_por_local"] = {str(k).strip().upper(): str(v).strip().lower()
                                   for k, v in contas_por_local.items() if str(k).strip()}
    if offset_updates is not None:
        cfg["offset_updates"] = int(offset_updates)
    set_preferencia(chave, cfg)


# ── BOT TELEGRAM (alertas Xsense) ─────────────────────────────────────
# Canal escolhido enquanto o WhatsApp Business API não destrava (bloqueio
# de conta no Meta for Developers, sem CNPJ pra verificação de empresa —
# 12/08/2026). Token do bot é segredo (dá pra mandar mensagem em nome do
# bot), mesmo tratamento de senha: criptografado via Fernet.

def get_xsense_telegram_config() -> dict:
    """Retorna config do bot sem expor o token/segredo em texto puro."""
    cfg = get_preferencia("xsense_telegram", {})
    return {
        "has_token": bool(cfg.get("token_enc", "")),
        "bot_username": cfg.get("bot_username", ""),
        "offset_updates": int(cfg.get("offset_updates", 0)),
        "webhook_ativo": bool(cfg.get("webhook_ativo", False)),
        "site_url": cfg.get("site_url", ""),
    }


def get_xsense_telegram_token() -> str:
    """Retorna o token em texto puro para uso interno (envio de mensagem)."""
    cfg = get_preferencia("xsense_telegram", {})
    return _decrypt(cfg.get("token_enc", ""))


def get_xsense_telegram_webhook_secret() -> str:
    """Segredo usado no header X-Telegram-Bot-Api-Secret-Token — confirma
    que um POST recebido em /api/xsense/telegram/webhook é do Telegram
    mesmo, e não de qualquer um que descubra a URL. Gera na primeira vez
    que for pedido e fica salvo (criptografado) pra sempre usar o mesmo."""
    cfg = get_preferencia("xsense_telegram", {})
    secret = _decrypt(cfg.get("webhook_secret_enc", ""))
    if not secret:
        import secrets as _secrets
        secret = _secrets.token_urlsafe(32)
        cfg["webhook_secret_enc"] = _encrypt(secret)
        set_preferencia("xsense_telegram", cfg)
    return secret


def set_xsense_telegram_config(token: str = None, bot_username: str = None, webhook_ativo: bool = None,
                               site_url: str = None) -> None:
    """Atualiza config do bot. Passa None para não alterar o campo."""
    cfg = get_preferencia("xsense_telegram", {})
    if token:  # só sobrescreve se token não-vazio foi enviado
        cfg["token_enc"] = _encrypt(token)
    if bot_username is not None:
        cfg["bot_username"] = bot_username.strip()
    if webhook_ativo is not None:
        cfg["webhook_ativo"] = bool(webhook_ativo)
    if site_url is not None:
        cfg["site_url"] = site_url.strip().rstrip("/")
    set_preferencia("xsense_telegram", cfg)


def set_xsense_telegram_offset(offset: int) -> None:
    """Marca até onde já lemos em getUpdates — evita reprocessar a mesma
    mensagem de /start toda vez que alguém consulta 'contatos pendentes'."""
    cfg = get_preferencia("xsense_telegram", {})
    cfg["offset_updates"] = int(offset)
    set_preferencia("xsense_telegram", cfg)


# ── IA DO EXPEDITO (Gemini) ───────────────────────────────────────────
# Chave da API do Gemini, colada pelo usuário em Configurações. Mesmo
# tratamento de senha (Fernet): com a chave na mão qualquer um gasta a cota
# da conta Google dele e responde em nome do sistema. Nunca volta em texto
# puro pra tela — a tela recebe has_chave e os 4 últimos caracteres, o
# bastante pra pessoa reconhecer qual chave está lá sem poder copiá-la.
#
# Sem chave configurada, a conversa do Expedito fica desligada e o painel
# calculado continua inteiro: a IA é camada em cima do número medido, não
# pré-requisito dele (decisão do usuário, 06/10/2026).

def _chave_ia_ambiente() -> str:
    """Na demo pública a chave do Gemini vem da variável de ambiente
    GEMINI_API_KEY (configurada na Vercel), nunca do banco nem do código."""
    if os.environ.get("DEMO_MODE", "").strip().lower() in ("1", "true", "sim", "yes"):
        return os.environ.get("GEMINI_API_KEY", "").strip()
    return ""


def get_expedito_ia_config() -> dict:
    if _chave_ia_ambiente():
        return {"has_chave": True, "chave_final": "", "ativo": True,
                "modelo": os.environ.get("GEMINI_MODEL", "").strip(),
                "ultimo_teste": "", "ultimo_teste_ok": None}
    cfg = get_preferencia("expedito_ia", {})
    return {
        "has_chave": bool(cfg.get("chave_enc", "")),
        "chave_final": cfg.get("chave_final", ""),
        "modelo": cfg.get("modelo", ""),
        "ativo": bool(cfg.get("ativo", True)),
        "ultimo_teste": cfg.get("ultimo_teste", ""),
        "ultimo_teste_ok": cfg.get("ultimo_teste_ok", None),
    }


def get_expedito_ia_chave() -> str:
    """Chave em texto puro, só para a chamada HTTP ao Gemini."""
    if _chave_ia_ambiente():
        return _chave_ia_ambiente()
    cfg = get_preferencia("expedito_ia", {})
    return _decrypt(cfg.get("chave_enc", ""))


def set_expedito_ia_config(chave: str = None, modelo: str = None,
                           ativo: bool = None, teste_ok: bool = None) -> None:
    """Atualiza a config da IA. Passa None para não alterar o campo.

    `chave=""` não apaga por acidente: a tela manda vazio toda vez que o
    usuário salva sem reescrever a chave (o campo nunca vem preenchido). Para
    apagar de propósito existe limpar_expedito_ia_chave().
    """
    cfg = get_preferencia("expedito_ia", {})
    if chave:
        cfg["chave_enc"] = _encrypt(chave.strip())
        cfg["chave_final"] = chave.strip()[-4:]
        cfg.pop("ultimo_teste", None)
        cfg.pop("ultimo_teste_ok", None)
    if modelo is not None:
        cfg["modelo"] = modelo.strip()
    if ativo is not None:
        cfg["ativo"] = bool(ativo)
    if teste_ok is not None:
        import datetime as _d
        cfg["ultimo_teste"] = _d.datetime.now().strftime("%Y-%m-%d %H:%M")
        cfg["ultimo_teste_ok"] = bool(teste_ok)
    set_preferencia("expedito_ia", cfg)


def limpar_expedito_ia_chave() -> None:
    """Remove a chave guardada. O resto da config (modelo, ativo) fica."""
    cfg = get_preferencia("expedito_ia", {})
    for campo in ("chave_enc", "chave_final", "ultimo_teste", "ultimo_teste_ok"):
        cfg.pop(campo, None)
    set_preferencia("expedito_ia", cfg)


# ── CAPTCHA (Cloudflare Turnstile) — anti brute-force no login ────────
# Pedido do usuário 29/08/2026: "podemos adicionar um captcha a partir da
# terceira tentativa de login?". Turnstile escolhido (privacidade, sem
# quebra-cabeça de imagem pro usuário legítimo) — precisa de site key
# (pública, vai pro HTML) e secret key (privada, só o backend usa pra
# verificar o token com a API da Cloudflare). Sem chave configurada, o
# captcha simplesmente não é exigido (rate limit de IP continua valendo
# sozinho, igual já era).
def get_captcha_config() -> dict:
    cfg = get_preferencia("captcha", {})
    return {
        "site_key": cfg.get("site_key", ""),
        "has_secret": bool(cfg.get("secret_key_enc", "")),
        "ativo_apos": int(cfg.get("ativo_apos", 2)),
    }


def get_captcha_secret() -> str:
    """Secret key em texto puro, só pro backend chamar a API de verificação."""
    cfg = get_preferencia("captcha", {})
    return _decrypt(cfg.get("secret_key_enc", ""))


def set_captcha_config(site_key: str = None, secret_key: str = None, ativo_apos: int = None) -> None:
    """Atualiza config do captcha. Passa None pra não alterar o campo."""
    cfg = get_preferencia("captcha", {})
    if site_key is not None:
        cfg["site_key"] = site_key.strip()
    if secret_key:  # só sobrescreve se não-vazio foi enviado
        cfg["secret_key_enc"] = _encrypt(secret_key)
    if ativo_apos is not None:
        cfg["ativo_apos"] = max(1, int(ativo_apos))
    set_preferencia("captcha", cfg)


_TIPOS_CAMARA_VALIDOS = ("congelada", "resfriada", "antecamara", "ambiente")


def get_xsense_dashboard_params() -> dict:
    """Limiares de % de eficiência (tempo dentro da faixa) que definem
    risco Alto/Médio/Sem risco no dashboard mensal, quais tipos de câmara
    (grupo da Xsense: congelada/resfriada/antecâmara/ambiente) entram na
    conta, e quais câmaras específicas ficam de fora mesmo sendo de um tipo
    incluído — configurável pelo usuário (pedido 29/08, 31/08 x2/2026: não é
    limiar fixo de código, é decisão de negócio/Qualidade — antecâmara já
    não gera alerta, e uma câmara instalada mas ainda sem uso real também
    não deveria poluir os indicadores). Defaults são só ponto de partida até
    alguém ajustar em Xsense → Dashboard Mensal → Parâmetros."""
    cfg = get_preferencia("xsense_dashboard_params", {})
    tipos = cfg.get("tipos_dashboard")
    if not isinstance(tipos, list) or not tipos:
        tipos = ["congelada", "resfriada"]
    excluidas = cfg.get("camaras_excluidas")
    if not isinstance(excluidas, list):
        excluidas = []
    return {
        "eficiencia_alto": float(cfg.get("eficiencia_alto", 95.0)),
        "eficiencia_medio": float(cfg.get("eficiencia_medio", 98.0)),
        "tipos_dashboard": [t for t in tipos if t in _TIPOS_CAMARA_VALIDOS],
        "camaras_excluidas": [str(cid) for cid in excluidas],
    }


def set_xsense_dashboard_params(eficiencia_alto: float, eficiencia_medio: float,
                                 tipos_dashboard: list[str], camaras_excluidas: list[str]) -> None:
    set_preferencia("xsense_dashboard_params", {
        "eficiencia_alto": float(eficiencia_alto),
        "eficiencia_medio": float(eficiencia_medio),
        "tipos_dashboard": [t for t in tipos_dashboard if t in _TIPOS_CAMARA_VALIDOS],
        "camaras_excluidas": [str(cid) for cid in camaras_excluidas],
    })


def set_ravex_status(status: str, ultima_atualizacao: str) -> None:
    """Atualiza status da última tentativa de atualização automática."""
    cfg = get_preferencia("ravex", {})
    cfg["status"] = status
    cfg["ultima_atualizacao"] = ultima_atualizacao
    set_preferencia("ravex", cfg)


def set_ravex_atualizando(ativo: bool) -> None:
    """Marca se um ciclo de atualização (download Playwright) está em andamento."""
    cfg = get_preferencia("ravex", {})
    cfg["atualizando"] = bool(ativo)
    set_preferencia("ravex", cfg)


def get_cd() -> dict:
    return get_preferencia("cd", {
        "nome": "CDA",
        "latitude": "",
        "longitude": "",
        "fator_rodoviario": 1.25,
    })


def set_cd(nome: str, latitude: str, longitude: str, fator_rodoviario: float = 1.25):
    set_preferencia("cd", {
        "nome": nome,
        "latitude": latitude,
        "longitude": longitude,
        "fator_rodoviario": fator_rodoviario,
    })


# ── API VALIDAÇÕES ────────────────────────────────────────
def get_validacao(unidade: str, nf: str) -> dict:
    with get_db() as db:
        row = db.execute(
            "SELECT regra_cd, entregas_frente, entregas_frente_obs, descarga_concluida "
            "FROM validacoes WHERE unidade=? AND nf=?",
            (str(unidade).strip(), str(nf).strip())
        ).fetchone()
    if not row:
        return {"regra_data_cd": "Não Verificado", "entregas_frente": "Não Verificado",
               "entregas_frente_obs": "", "descarga_concluida": "Não"}
    return {
        "regra_data_cd":       row["regra_cd"],
        "entregas_frente":     row["entregas_frente"],
        "entregas_frente_obs": row["entregas_frente_obs"],
        "descarga_concluida":  row["descarga_concluida"],
    }


def set_validacao(unidade: str, nf: str, usuario: str = '', regra_cd: str = None, entregas_frente: str = None,
                  entregas_frente_obs: str = None, descarga_concluida: str = None,
                  descarga_iniciada: str = None):
    """Salva Regra CD, Entregas Frente (e seu texto livre) e/ou Descarga
    Concluída, por usuário. Passa None para não alterar o campo."""
    unidade = str(unidade).strip()
    nf      = str(nf).strip()
    usuario = str(usuario).strip().lower()
    with get_db() as db:
        row = db.execute(
            "SELECT regra_cd, entregas_frente, entregas_frente_obs, descarga_concluida "
            "FROM validacoes WHERE unidade=? AND nf=? AND usuario=?",
            (unidade, nf, usuario)
        ).fetchone()
        if row is None:
            db.execute(
                "INSERT INTO validacoes(unidade,nf,usuario,regra_cd,entregas_frente,entregas_frente_obs,descarga_concluida) "
                "VALUES(?,?,?,?,?,?,?)",
                (unidade, nf, usuario,
                 regra_cd        if regra_cd        is not None else "Não Verificado",
                 entregas_frente if entregas_frente is not None else "Não Verificado",
                 entregas_frente_obs if entregas_frente_obs is not None else "",
                 descarga_concluida  if descarga_concluida  is not None else "Não")
            )
        else:
            new_regra = regra_cd        if regra_cd        is not None else row["regra_cd"]
            new_ent   = entregas_frente if entregas_frente is not None else row["entregas_frente"]
            new_ent_obs = entregas_frente_obs if entregas_frente_obs is not None else row["entregas_frente_obs"]
            new_descarga = descarga_concluida if descarga_concluida is not None else row["descarga_concluida"]
            db.execute(
                "UPDATE validacoes SET regra_cd=?,entregas_frente=?,entregas_frente_obs=?,descarga_concluida=?,"
                "updated_at=CURRENT_TIMESTAMP"
                " WHERE unidade=? AND nf=? AND usuario=?",
                (new_regra, new_ent, new_ent_obs, new_descarga, unidade, nf, usuario)
            )
        if descarga_iniciada is not None:
            # campo novo (19/10/2026): a linha já existe depois do insert/update acima
            db.execute("UPDATE validacoes SET descarga_iniciada=?, updated_at=CURRENT_TIMESTAMP "
                       "WHERE unidade=? AND nf=? AND usuario=?", (descarga_iniciada, unidade, nf, usuario))


# ── API REDESPACHO ────────────────────────────────────────
def get_redespacho_selecionados(usuario: str = '') -> set:
    with get_db() as db:
        if usuario:
            rows = db.execute(
                "SELECT placa FROM redespacho_sel WHERE usuario=?",
                (usuario.strip().lower(),)
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT placa FROM redespacho_sel WHERE selecionado=1"
            ).fetchall()
    return {r["placa"] for r in rows}


def toggle_redespacho(placa: str, usuario: str = "") -> bool:
    """Alterna seleção por usuario. Retorna novo estado (True = selecionado)."""
    u = usuario.strip().lower() or "_global"
    with get_db() as db:
        # Garante coluna usuario existe
        try:
            db.execute("ALTER TABLE redespacho_sel ADD COLUMN usuario TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass
        row = db.execute(
            "SELECT id FROM redespacho_sel WHERE placa=? AND usuario=?", (placa, u)
        ).fetchone()
        if row is None:
            db.execute(
                "INSERT OR IGNORE INTO redespacho_sel(usuario, placa) VALUES(?,?)", (u, placa)
            )
            return True
        else:
            db.execute(
                "DELETE FROM redespacho_sel WHERE placa=? AND usuario=?", (placa, u)
            )
            return False


# ── API OBSERVAÇÕES ───────────────────────────────────────


def get_cd_usuario(usuario: str) -> dict:
    """Config do CD desse usuário (nome/nome2/latitude/longitude/etc.) — se
    ele nunca salvou nada, cai pra config global (get_cd()). Antes só
    devolvia a config salva se latitude E longitude também estivessem
    preenchidas — qualquer usuário que salvasse só o nome (sem coordenadas)
    tinha TUDO ignorado silenciosamente, inclusive o nome (achado do
    usuário 24/08/2026 testando a personalização de nome do CD: "coloquei
    o nome... e não ficou salvo"). Latitude/longitude em branco agora só
    afetam quem precisa delas de verdade (ex.: /api/rota_maps, que já trata
    coordenada ausente com sua própria checagem)."""
    try:
        import json as _j
        with get_db() as db:
            try: db.execute('ALTER TABLE users ADD COLUMN cd_config TEXT DEFAULT ""')
            except: pass
            row = db.execute('SELECT cd_config FROM users WHERE username=?', (usuario.strip().lower(),)).fetchone()
        if row and row['cd_config']:
            return _j.loads(row['cd_config'])
    except: pass
    return get_cd()

def set_cd_usuario(usuario: str, nome: str, latitude: str, longitude: str,
                   fator_rodoviario: float = 1.25, horarios_corte: dict | None = None,
                   nome2: str = ""):
    import json as _j
    cfg = _j.dumps({'nome': nome, 'nome2': nome2, 'latitude': latitude, 'longitude': longitude,
                    'fator_rodoviario': fator_rodoviario, 'horarios_corte': horarios_corte or {}})
    with get_db() as db:
        try: db.execute('ALTER TABLE users ADD COLUMN cd_config TEXT DEFAULT ""')
        except: pass
        db.execute('UPDATE users SET cd_config=? WHERE username=?', (cfg, usuario.strip().lower()))

def get_todas_validacoes(usuario: str = '') -> dict:
    """Retorna {(unidade,nf): {regra_data_cd, entregas_frente, entregas_frente_obs,
    descarga_concluida}} para reaplicar em lote."""
    with get_db() as db:
        if usuario:
            rows = db.execute(
                "SELECT unidade, nf, regra_cd, entregas_frente, entregas_frente_obs, descarga_concluida, "
                "descarga_iniciada FROM validacoes WHERE usuario=?",
                (usuario.strip().lower(),)
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT unidade, nf, regra_cd, entregas_frente, entregas_frente_obs, descarga_concluida, "
                "descarga_iniciada FROM validacoes"
            ).fetchall()
    return {
        (r["unidade"], r["nf"]): {
            "regra_data_cd":       r["regra_cd"],
            "entregas_frente":     r["entregas_frente"],
            "entregas_frente_obs": r["entregas_frente_obs"] or "",
            "descarga_concluida":  r["descarga_concluida"] or "Não",
            "descarga_iniciada":   r["descarga_iniciada"] or "Não",
        }
        for r in rows
    }


def get_todas_observacoes(usuario: str = '') -> dict:
    """
    Retorna {(placa, empresa_compra): observacao} e {placa: observacao}
    (fallback sem empresa_compra) para reaplicar em lote, evitando
    uma query por linha do consolidado.
    """
    with get_db() as db:
        if usuario:
            rows = db.execute(
                "SELECT placa, empresa_compra, observacao FROM observacoes WHERE usuario=?",
                (usuario.strip().lower(),)
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT placa, empresa_compra, observacao FROM observacoes"
            ).fetchall()

    por_placa_empresa = {}
    por_placa = {}
    for r in rows:
        placa = r["placa"]
        ec = r["empresa_compra"] or ""
        obs = r["observacao"] or ""
        if ec:
            por_placa_empresa[(placa, ec)] = obs
        if placa not in por_placa:
            por_placa[placa] = obs

    return {"por_placa_empresa": por_placa_empresa, "por_placa": por_placa}


def get_observacao(placa: str, usuario: str = '', empresa_compra: str = '') -> str:
    with get_db() as db:
        if usuario and empresa_compra:
            row = db.execute(
                "SELECT observacao FROM observacoes WHERE placa=? AND usuario=? AND empresa_compra=?",
                (placa, usuario.strip().lower(), empresa_compra.strip())
            ).fetchone()
            if not row:
                row = db.execute(
                    "SELECT observacao FROM observacoes WHERE placa=? AND usuario=?",
                    (placa, usuario.strip().lower())
                ).fetchone()
        elif usuario:
            row = db.execute(
                "SELECT observacao FROM observacoes WHERE placa=? AND usuario=?",
                (placa, usuario.strip().lower())
            ).fetchone()
        else:
            row = db.execute(
                "SELECT observacao FROM observacoes WHERE placa=?", (placa,)
            ).fetchone()
    return row["observacao"] if row else ""


def set_observacao(placa: str, nf: str, observacao: str, usuario: str = '', empresa_compra: str = ''):
    u  = str(usuario).strip().lower()
    ec = str(empresa_compra).strip()
    with get_db() as db:
        db.execute(
            """INSERT INTO observacoes(placa, nf, observacao, usuario, empresa_compra, updated_at)
               VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(placa,nf) DO UPDATE SET
               observacao=excluded.observacao,
               usuario=excluded.usuario,
               empresa_compra=excluded.empresa_compra,
               updated_at=CURRENT_TIMESTAMP""",
            (placa, nf, observacao, u, ec)
        )


# ── API CDs LOCALIZAÇÃO ──────────────────────────────────────────

def _ensure_cds_localizacao(db):
    db.execute("""CREATE TABLE IF NOT EXISTS cds_localizacao (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sigla TEXT UNIQUE NOT NULL,
        latitude TEXT NOT NULL DEFAULT '',
        longitude TEXT NOT NULL DEFAULT '',
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    # raio em que a carreta conta como "no CD" (chegada/saída para o tempo de
    # descarga — app/services/carretas_viagem.py). Vazio = padrão de 1 km.
    cols = {r[1] for r in db.execute("PRAGMA table_info(cds_localizacao)").fetchall()}
    if "raio_chegada_km" not in cols:
        db.execute("ALTER TABLE cds_localizacao ADD COLUMN raio_chegada_km REAL")

def get_cds_localizacao() -> list:
    with get_db() as db:
        _ensure_cds_localizacao(db)
        rows = db.execute(
            "SELECT id, sigla, latitude, longitude, raio_chegada_km FROM cds_localizacao ORDER BY sigla"
        ).fetchall()
    return [dict(r) for r in rows]

def get_cd_localizacao(sigla: str) -> dict:
    with get_db() as db:
        _ensure_cds_localizacao(db)
        row = db.execute(
            "SELECT latitude, longitude FROM cds_localizacao WHERE sigla=?",
            (sigla.strip().upper(),)
        ).fetchone()
    return dict(row) if row else {}

def set_cd_localizacao(sigla: str, latitude: str, longitude: str,
                       raio_chegada_km: float | None = None) -> None:
    with get_db() as db:
        _ensure_cds_localizacao(db)
        db.execute(
            """INSERT INTO cds_localizacao(sigla, latitude, longitude, raio_chegada_km)
               VALUES(?,?,?,?)
               ON CONFLICT(sigla) DO UPDATE SET
               latitude=excluded.latitude,
               longitude=excluded.longitude,
               raio_chegada_km=excluded.raio_chegada_km,
               updated_at=CURRENT_TIMESTAMP""",
            (sigla.strip().upper(), latitude.strip(), longitude.strip(), raio_chegada_km)
        )

def del_cd_localizacao(cd_id: int) -> None:
    with get_db() as db:
        _ensure_cds_localizacao(db)
        db.execute("DELETE FROM cds_localizacao WHERE id=?", (cd_id,))

# ---------------------------------------------------------------------------
# Empresa consolidada — quais empresas ficam no MESMO LOCAL FÍSICO
#
# CDA e CDB dividem o mesmo endereço, então uma carreta que entrega nas duas
# descarrega num lugar só e é UM card. CDE e CDF idem, entre si. Já CNT é outra
# empresa em outro lugar, e não pode se misturar (usuário, 04/09/2026).
#
# É cadastro PRÓPRIO, e não `cd_conta_canonica`, de propósito: aquela tabela diz
# quem é DONO do dado (permissão), não onde a empresa FICA. Hoje as duas
# coincidem, mas dois CDs do mesmo dono em cidades diferentes cairiam no mesmo
# card se a gente reaproveitasse a conta canônica.
#
# Empresa não cadastrada resolve para ela mesma — o lado seguro do erro é
# separar cards demais, nunca juntar entregas de locais diferentes.
# ---------------------------------------------------------------------------

def _ensure_empresa_consolidada(db):
    db.execute("""CREATE TABLE IF NOT EXISTS empresa_consolidada (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sigla TEXT UNIQUE NOT NULL,
        consolidada TEXT NOT NULL,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")


def get_empresas_consolidadas() -> list:
    with get_db() as db:
        _ensure_empresa_consolidada(db)
        rows = db.execute(
            "SELECT id, sigla, consolidada FROM empresa_consolidada ORDER BY consolidada, sigla"
        ).fetchall()
    return [dict(r) for r in rows]


def get_mapa_consolidacao() -> dict:
    """{SIGLA: CONSOLIDADA} — lido de uma vez, para não consultar por linha."""
    return {r["sigla"]: r["consolidada"] for r in get_empresas_consolidadas()}


def set_empresa_consolidada(sigla: str, consolidada: str) -> None:
    with get_db() as db:
        _ensure_empresa_consolidada(db)
        db.execute(
            """INSERT INTO empresa_consolidada(sigla, consolidada)
               VALUES(?,?)
               ON CONFLICT(sigla) DO UPDATE SET
               consolidada=excluded.consolidada,
               updated_at=CURRENT_TIMESTAMP""",
            (sigla.strip().upper(), (consolidada.strip().upper() or sigla.strip().upper()))
        )


def del_empresa_consolidada(reg_id: int) -> None:
    with get_db() as db:
        _ensure_empresa_consolidada(db)
        db.execute("DELETE FROM empresa_consolidada WHERE id=?", (reg_id,))


def get_cards_por_linha(usuario: str) -> int:
    with get_db() as db:
        row = db.execute(
            "SELECT cards_por_linha FROM users WHERE username=?",
            (usuario.strip().lower(),)
        ).fetchone()
    val = row["cards_por_linha"] if row and row["cards_por_linha"] else 4
    return max(4, min(10, int(val)))


def set_cards_por_linha(usuario: str, valor: int) -> None:
    valor = max(4, min(10, int(valor)))
    with get_db() as db:
        db.execute(
            "UPDATE users SET cards_por_linha=? WHERE username=?",
            (valor, usuario.strip().lower())
        )


def get_changelog_visto(usuario: str) -> str:
    with get_db() as db:
        row = db.execute(
            "SELECT changelog_visto FROM users WHERE username=?",
            (usuario.strip().lower(),)
        ).fetchone()
    return (row["changelog_visto"] or "") if row else ""


def set_changelog_visto(usuario: str, versao: str) -> None:
    with get_db() as db:
        db.execute(
            "UPDATE users SET changelog_visto=? WHERE username=?",
            (str(versao).strip(), usuario.strip().lower())
        )


def is_master(usuario: str) -> bool:
    """Nome mantido por compatibilidade (muitas rotas de config global —
    OpenTech/Ravex/Xsense/Telegram/CDs — já chamam essa função) — desde a
    unificação de perfis (29/08/2026) admin e master são o mesmo nível de
    acesso, então isso virou só "é admin?". Contas antigas com perfil
    'master' são migradas pra 'admin' automaticamente (ver _migrate_db)."""
    with get_db() as db:
        row = db.execute(
            "SELECT perfil FROM users WHERE username=? AND ativo=1",
            (usuario.strip().lower(),)
        ).fetchone()
    return row is not None and row["perfil"] == "admin"


# ── API SESSÃO DE DADOS (relatório persistido) ────────────
import gzip as _gzip
import json as _json

def _ensure_sessao_dados(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS sessao_dados (
            id           INTEGER PRIMARY KEY,
            usuario      TEXT,
            atualizado   TEXT,
            df_consolidado BLOB,
            df_itens       BLOB,
            df_redespacho  BLOB,
            df_controle    BLOB,
            usou_opentech  INTEGER DEFAULT 0
        )
    """)
    cols = [r[1] for r in db.execute("PRAGMA table_info(sessao_dados)").fetchall()]
    if "df_controle" not in cols:
        db.execute("ALTER TABLE sessao_dados ADD COLUMN df_controle BLOB")
    if "usou_opentech" not in cols:
        db.execute("ALTER TABLE sessao_dados ADD COLUMN usou_opentech INTEGER DEFAULT 0")


def salvar_sessao(df_consolidado, df_itens, df_redespacho, usuario: str = "",
                  df_controle=None, usou_opentech: bool = False):
    """Persiste os DataFrames no banco após processamento — 1 sessão por usuário."""
    import pandas as pd

    def _pack(df: pd.DataFrame) -> bytes:
        if df is None or df.empty:
            return _gzip.compress(b"[]")
        j = df.to_json(orient="records", force_ascii=False, date_format="iso")
        return _gzip.compress(j.encode("utf-8"))

    ts = datetime.now().isoformat(timespec="seconds")
    with get_db() as db:
        _ensure_sessao_dados(db)
        # Só apaga a sessão DESTE usuário — um DELETE sem WHERE aqui apagava a
        # sessão de todo mundo toda vez que qualquer usuário processava um
        # relatório nolvo (achado em 27/07/2026, ver BACKLOG).
        db.execute("DELETE FROM sessao_dados WHERE usuario=?", (usuario,))
        db.execute(
            "INSERT INTO sessao_dados(usuario,atualizado,df_consolidado,df_itens,df_redespacho,df_controle,usou_opentech) VALUES(?,?,?,?,?,?,?)",
            (usuario, ts, _pack(df_consolidado), _pack(df_itens), _pack(df_redespacho),
             _pack(df_controle) if df_controle is not None else _pack(pd.DataFrame()),
             1 if usou_opentech else 0)
        )


def carregar_sessao(usuario: str = '') -> dict | None:
    """Recupera a última sessão salva do usuário. Retorna None se não houver."""
    import pandas as pd

    def _unpack(blob) -> pd.DataFrame:
        if not blob:
            return pd.DataFrame()
        try:
            j = _gzip.decompress(blob).decode("utf-8")
            data = _json.loads(j)
            return pd.DataFrame(data) if data else pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    try:
        with get_db() as db:
            _ensure_sessao_dados(db)
            if usuario:
                row = db.execute(
                    "SELECT * FROM sessao_dados WHERE usuario=? ORDER BY id DESC LIMIT 1",
                    (usuario,)
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT * FROM sessao_dados ORDER BY id DESC LIMIT 1"
                ).fetchone()
    except Exception:
        return None

    if not row:
        return None

    row_keys = row.keys()
    return {
        "usuario":        row["usuario"],
        "atualizado":     row["atualizado"],
        "df_consolidado": _unpack(row["df_consolidado"]),
        "df_itens":       _unpack(row["df_itens"]),
        "df_redespacho":  _unpack(row["df_redespacho"]),
        "df_controle":    _unpack(row["df_controle"]) if "df_controle" in row_keys else pd.DataFrame(),
        "usou_opentech":  bool(row["usou_opentech"]) if "usou_opentech" in row_keys else False,
    }


from datetime import datetime, timedelta
import zoneinfo as _zoneinfo
_BRT = _zoneinfo.ZoneInfo("America/Sao_Paulo")


def _now_brt_str() -> str:
    return datetime.now(_BRT).strftime("%Y-%m-%d %H:%M:%S")


# ── LOG DE AUDITORIA ──────────────────────────────────────
def _ensure_log_auditoria(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS log_auditoria (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario   TEXT NOT NULL DEFAULT '',
            acao      TEXT NOT NULL,
            alvo      TEXT NOT NULL DEFAULT '',
            detalhe   TEXT NOT NULL DEFAULT '',
            ip        TEXT NOT NULL DEFAULT '',
            timestamp TEXT NOT NULL
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_log_auditoria_ts ON log_auditoria(timestamp)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_log_auditoria_usuario ON log_auditoria(usuario, timestamp)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_log_auditoria_acao ON log_auditoria(acao, timestamp)")


def registrar_log(usuario: str, acao: str, alvo: str = '', detalhe: str = '', ip: str = '') -> None:
    """Grava uma entrada no log de auditoria. Nunca lança exceção pra fora —
    uma falha ao gravar o log não pode derrubar a ação que estava sendo
    registrada (ex.: criar usuário funciona mesmo se o log falhar)."""
    try:
        # Gravação feita durante um espelho precisa dizer EM NOME DE QUEM —
        # desde 01/10/2026 o Gerente atua no CD espelhado, e sem esta marca
        # o log diria que o admin mexeu na própria conta (usuário, 01/10).
        try:
            from flask import has_request_context, session as _sessao
            alvo_espelho = str(_sessao.get("espelhando") or "") if has_request_context() else ""
        except Exception:
            alvo_espelho = ""
        if alvo_espelho:
            marca = f"[espelhando {alvo_espelho}]"
            detalhe = f"{marca} {detalhe}".strip()
        with get_db() as db:
            _ensure_log_auditoria(db)
            db.execute(
                "INSERT INTO log_auditoria(usuario,acao,alvo,detalhe,ip,timestamp) VALUES(?,?,?,?,?,?)",
                (str(usuario or '').strip().lower(), acao, alvo, detalhe, ip, _now_brt_str())
            )
    except Exception as e:
        print(f"[LOG_AUDITORIA] Falha ao gravar ({acao}): {e}")


def listar_log_auditoria(usuario: str = '', acao: str = '', dias: int = 30, limite: int = 500) -> list:
    """Lista entradas do log, mais recentes primeiro. usuario/acao vazios = sem
    filtro nesse campo. dias<=0 = sem filtro de período."""
    with get_db() as db:
        _ensure_log_auditoria(db)
        where, params = [], []
        if usuario:
            where.append("usuario=?")
            params.append(usuario.strip().lower())
        if acao:
            where.append("acao=?")
            params.append(acao)
        if dias and dias > 0:
            desde = (datetime.now(_BRT) - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")
            where.append("timestamp>=?")
            params.append(desde)
        sql = "SELECT id,usuario,acao,alvo,detalhe,ip,timestamp FROM log_auditoria"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limite), 2000)))
        rows = db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def listar_acoes_log() -> list:
    """Lista distinta de 'acao' já registradas — alimenta o filtro da UI."""
    with get_db() as db:
        _ensure_log_auditoria(db)
        rows = db.execute("SELECT DISTINCT acao FROM log_auditoria ORDER BY acao").fetchall()
    return [r["acao"] for r in rows]


# ── ESPELHAMENTO ──────────────────────────────────────────
import threading as _threading
import time as _time
_espelhos_lock = _threading.Lock()
_espelhos: dict = {}  # {alvo: {observador: timestamp do último heartbeat}}
_ESPELHO_TTL_SEG = 45  # sem heartbeat por esse tempo = observador considerado desconectado

def registrar_espelho(observador: str, alvo: str):
    with _espelhos_lock:
        _espelhos.setdefault(alvo, {})[observador] = _time.time()

def heartbeat_espelho(observador: str, alvo: str):
    """Renova o registro de espelhamento — chamado periodicamente pelo front-end
    enquanto o observador está com o painel de `alvo` aberto."""
    with _espelhos_lock:
        if alvo in _espelhos and observador in _espelhos[alvo]:
            _espelhos[alvo][observador] = _time.time()

def remover_espelho(observador: str, alvo: str):
    with _espelhos_lock:
        if alvo in _espelhos:
            _espelhos[alvo].pop(observador, None)
            if not _espelhos[alvo]:
                del _espelhos[alvo]

def get_observadores(usuario: str) -> list:
    """Retorna observadores ativos de `usuario`, descartando (e removendo) quem
    não envia heartbeat há mais de _ESPELHO_TTL_SEG segundos (aba fechada,
    deslogou, etc. sem passar pelo /api/espelho/sair)."""
    agora = _time.time()
    with _espelhos_lock:
        obs = _espelhos.get(usuario, {})
        expirados = [o for o, ts in obs.items() if agora - ts >= _ESPELHO_TTL_SEG]
        for o in expirados:
            del obs[o]
        if not obs:
            _espelhos.pop(usuario, None)
            return []
        return sorted(obs.keys())

def get_usuarios_com_dados() -> list:
    """Usuários com dado recente em algum módulo que guarda estado por
    usuário — hoje Monitoramento (sessao_dados) e Painel do Armazém
    (armazem_diario) — pra alimentar o seletor de "Espelhar". A função de
    espelho passou a valer também pro Armazém/Xsense (pedido do usuário
    24/08/2026), não só pro painel principal; sem essa união, um analista
    que só usa o Armazém nunca apareceria na lista. Se o mesmo usuário tiver
    dado nos dois módulos, fica só o horário mais recente dos dois."""
    try:
        with get_db() as db:
            rows = db.execute("""
                SELECT usuario, MAX(atualizado) AS atualizado FROM (
                    SELECT usuario, atualizado FROM sessao_dados
                    UNION ALL
                    SELECT usuario, processado_em AS atualizado FROM armazem_diario
                )
                GROUP BY usuario
                ORDER BY atualizado DESC
            """).fetchall()
        return [{"usuario": r["usuario"], "atualizado": r["atualizado"]} for r in rows]
    except Exception:
        return []


# ── CACHE DE GEOCODIFICAÇÃO REVERSA (lat/lon -> cidade) ──────
# Usado pela Ravex, que não traz cidade no relatório (só lat/lon) — ver
# app/services/geocoding_service.py e MEL-14 no BACKLOG. Chave é lat/lon
# arredondados (~111m de precisão), então veículos parados no mesmo lugar
# (ex.: "no CD") reaproveitam o cache em vez de bater no serviço externo
# de novo a cada ciclo.

def _ensure_geocode_cache(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS geocode_cache (
            lat_round  REAL NOT NULL,
            lon_round  REAL NOT NULL,
            cidade     TEXT NOT NULL DEFAULT '',
            atualizado TEXT NOT NULL,
            PRIMARY KEY (lat_round, lon_round)
        )
    """)


def get_cidade_cache(lat_round: float, lon_round: float) -> str | None:
    """Retorna a cidade em cache pra essas coordenadas, ou None se nunca
    foi consultada (diferente de '' — que significa "consultada, mas o
    serviço não achou nada")."""
    with get_db() as db:
        _ensure_geocode_cache(db)
        row = db.execute(
            "SELECT cidade FROM geocode_cache WHERE lat_round=? AND lon_round=?",
            (lat_round, lon_round)
        ).fetchone()
    return row["cidade"] if row else None


def set_cidade_cache(lat_round: float, lon_round: float, cidade: str) -> None:
    with get_db() as db:
        _ensure_geocode_cache(db)
        db.execute(
            """INSERT INTO geocode_cache(lat_round,lon_round,cidade,atualizado)
               VALUES(?,?,?,?)
               ON CONFLICT(lat_round,lon_round) DO UPDATE SET
               cidade=excluded.cidade, atualizado=excluded.atualizado""",
            (lat_round, lon_round, cidade, _now_brt_str())
        )
