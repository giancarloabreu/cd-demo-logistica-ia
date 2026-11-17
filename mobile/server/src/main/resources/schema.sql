-- Schema do App Separação Redespacho.
-- Ver docs/MODELO-DE-DADOS.md. Timestamps em TEXT ISO-8601 UTC.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- sequenciais

CREATE TABLE sequencia (
    nome  TEXT    PRIMARY KEY,
    valor INTEGER NOT NULL DEFAULT 0
);

INSERT INTO sequencia (nome, valor) VALUES
    ('INSTRUCAO', 0),
    ('PALLET', 0),
    ('PALLET_DEVOLUCAO', 0);

-- --------------------------------------------------------------------- acesso

CREATE TABLE impressora (
    id                INTEGER PRIMARY KEY,
    nome              TEXT    NOT NULL UNIQUE,
    ip                TEXT    NOT NULL,
    porta             INTEGER NOT NULL DEFAULT 9100,
    dpi               INTEGER,
    ultimo_contato_em TEXT,
    ativo             INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE usuario (
    id            INTEGER PRIMARY KEY,
    login         TEXT    NOT NULL UNIQUE,
    nome          TEXT    NOT NULL,
    senha_hash    TEXT    NOT NULL,
    senha_salt    TEXT    NOT NULL,
    perfil        TEXT    NOT NULL CHECK (perfil IN ('CONSULTA','SEPARADOR','SUPERVISOR','ADMIN')),
    impressora_id INTEGER REFERENCES impressora(id),
    multi_item    INTEGER NOT NULL DEFAULT 0,
    ativo         INTEGER NOT NULL DEFAULT 1,
    criado_em     TEXT    NOT NULL,
    criado_por    INTEGER REFERENCES usuario(id)
);

-- ---------------------------------------------------------------- armazenagem

CREATE TABLE deposito (
    id     INTEGER PRIMARY KEY,
    codigo TEXT    NOT NULL UNIQUE,
    nome   TEXT    NOT NULL,
    ativo  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE rua (
    id          INTEGER PRIMARY KEY,
    deposito_id INTEGER NOT NULL REFERENCES deposito(id),
    sigla       TEXT    NOT NULL,
    UNIQUE (deposito_id, sigla)
);

CREATE TABLE box (
    id            INTEGER PRIMARY KEY,
    codigo        TEXT    NOT NULL UNIQUE,
    codigo_barras TEXT    NOT NULL UNIQUE,
    deposito_id   INTEGER NOT NULL REFERENCES deposito(id),
    rua_id        INTEGER NOT NULL REFERENCES rua(id),
    tipo          TEXT    NOT NULL CHECK (tipo IN ('ESTOCAGEM','PICKING')),
    qtd_niveis    INTEGER NOT NULL DEFAULT 6,
    ativo         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE posicao (
    id     INTEGER PRIMARY KEY,
    box_id INTEGER NOT NULL REFERENCES box(id),
    nivel  INTEGER NOT NULL,
    UNIQUE (box_id, nivel)
);

-- ------------------------------------------------------------------- produtos

CREATE TABLE produto (
    id                  INTEGER PRIMARY KEY,
    codigo              TEXT    NOT NULL UNIQUE,
    descricao           TEXT    NOT NULL,
    unidades_por_caixa  INTEGER,
    gramas_por_unidade  INTEGER,
    un_cx_origem        TEXT    CHECK (un_cx_origem IN ('XML_VALIDADO','MANUAL')),
    origem              TEXT    NOT NULL CHECK (origem IN ('XML','IMPORTACAO','MANUAL')),
    ativo               INTEGER NOT NULL DEFAULT 1,
    criado_em           TEXT    NOT NULL
);

CREATE TABLE produto_codigo_barras (
    id            INTEGER PRIMARY KEY,
    produto_id    INTEGER NOT NULL REFERENCES produto(id),
    codigo        TEXT    NOT NULL UNIQUE,
    simbologia    TEXT,
    origem        TEXT    NOT NULL CHECK (origem IN ('XML','IMPORTACAO','MANUAL','VINCULO_COLETOR')),
    confirmado    INTEGER NOT NULL DEFAULT 0,
    confirmado_em TEXT,
    ativo         INTEGER NOT NULL DEFAULT 1,
    criado_em     TEXT    NOT NULL,
    criado_por    INTEGER REFERENCES usuario(id)
);

CREATE TABLE codigo_nao_identificado (
    id                   INTEGER PRIMARY KEY,
    codigo               TEXT    NOT NULL UNIQUE,
    qtd_leituras         INTEGER NOT NULL DEFAULT 1,
    primeira_leitura_em  TEXT    NOT NULL,
    ultima_leitura_em    TEXT    NOT NULL,
    ultimo_usuario_id    INTEGER REFERENCES usuario(id),
    resolvido_em         TEXT,
    resolvido_produto_id INTEGER REFERENCES produto(id),
    resolvido_por        INTEGER REFERENCES usuario(id)
);

-- --------------------------------------------------- instrução, nota fiscal e item

CREATE TABLE instrucao (
    id            INTEGER PRIMARY KEY,
    numero        TEXT    NOT NULL UNIQUE,
    status        TEXT    NOT NULL CHECK (status IN ('ABERTA','EM_SEPARACAO','CONCLUIDA','CANCELADA')),
    observacao    TEXT,
    criado_em     TEXT    NOT NULL,
    criado_por    INTEGER NOT NULL REFERENCES usuario(id),
    concluido_em  TEXT,
    concluido_por INTEGER REFERENCES usuario(id)
);

CREATE TABLE nota_fiscal (
    id                INTEGER PRIMARY KEY,
    instrucao_id      INTEGER NOT NULL REFERENCES instrucao(id),
    chave             TEXT    NOT NULL UNIQUE,
    numero            TEXT    NOT NULL,
    serie             TEXT    NOT NULL,
    emissao           TEXT    NOT NULL,
    dest_cnpj         TEXT,
    dest_nome         TEXT    NOT NULL,
    dest_logradouro   TEXT,
    dest_numero       TEXT,
    dest_complemento  TEXT,
    dest_bairro       TEXT,
    dest_municipio    TEXT,
    dest_uf           TEXT,
    dest_cep          TEXT,
    qtd_volumes_xml   INTEGER,
    bonificacao       INTEGER NOT NULL DEFAULT 0,
    status            TEXT    NOT NULL CHECK (status IN
                          ('PENDENTE','EM_SEPARACAO','CONCLUIDA','EXPEDIDA','RETORNO_PARCIAL')),
    xml_original      BLOB,
    importado_em      TEXT    NOT NULL,
    importado_por     INTEGER NOT NULL REFERENCES usuario(id)
);

CREATE INDEX ix_nota_instrucao ON nota_fiscal(instrucao_id);

CREATE TABLE item_nota (
    id             INTEGER PRIMARY KEY,
    nota_fiscal_id INTEGER NOT NULL REFERENCES nota_fiscal(id),
    n_item         INTEGER NOT NULL,
    produto_id     INTEGER NOT NULL REFERENCES produto(id),
    qtd_prevista   INTEGER NOT NULL,
    qtd_separada   INTEGER NOT NULL DEFAULT 0,
    status         TEXT    NOT NULL CHECK (status IN
                       ('ABERTO','EM_SEPARACAO','FECHADO','FECHADO_DIVERGENCIA')),
    fechado_em     TEXT,
    fechado_por    INTEGER REFERENCES usuario(id),
    justificativa  TEXT,
    reaberto_em    TEXT,
    reaberto_por   INTEGER REFERENCES usuario(id),
    UNIQUE (nota_fiscal_id, n_item)
);

-- --------------------------------------------------------------------- pallet

CREATE TABLE pallet (
    id                   INTEGER PRIMARY KEY,
    codigo               TEXT    NOT NULL UNIQUE,
    tipo                 TEXT    NOT NULL CHECK (tipo IN ('SEPARACAO','DEVOLUCAO')),
    status               TEXT    NOT NULL CHECK (status IN
                             ('CRIADO','EM_USO','ENCERRADO','ENDERECADO','EXPEDIDO','FUNDIDO','ESVAZIADO')),
    nota_fiscal_id       INTEGER REFERENCES nota_fiscal(id),
    nota_devolucao_id    INTEGER REFERENCES nota_devolucao(id),
    usuario_atual_id     INTEGER REFERENCES usuario(id),
    fundido_em_pallet_id INTEGER REFERENCES pallet(id),
    criado_em            TEXT    NOT NULL,
    criado_por           INTEGER NOT NULL REFERENCES usuario(id),
    impresso_em          TEXT,
    encerrado_em         TEXT,
    encerrado_por        INTEGER REFERENCES usuario(id)
);

CREATE INDEX ix_pallet_nota ON pallet(nota_fiscal_id);

CREATE TABLE pallet_movimentacao (
    id                INTEGER PRIMARY KEY,
    tipo              TEXT    NOT NULL CHECK (tipo IN ('FUSAO','TRANSFERENCIA')),
    pallet_origem_id  INTEGER NOT NULL REFERENCES pallet(id),
    pallet_destino_id INTEGER NOT NULL REFERENCES pallet(id),
    item_nota_id      INTEGER REFERENCES item_nota(id),
    qtd_caixas        INTEGER NOT NULL,
    justificativa     TEXT,
    movido_em         TEXT    NOT NULL,
    movido_por        INTEGER NOT NULL REFERENCES usuario(id)
);

CREATE TABLE enderecamento (
    id               INTEGER PRIMARY KEY,
    pallet_id        INTEGER NOT NULL REFERENCES pallet(id),
    posicao_id       INTEGER NOT NULL REFERENCES posicao(id),
    enderecado_em    TEXT    NOT NULL,
    enderecado_por   INTEGER NOT NULL REFERENCES usuario(id),
    liberado_em      TEXT,
    liberado_por     INTEGER REFERENCES usuario(id),
    motivo_liberacao TEXT    CHECK (motivo_liberacao IN ('EXPEDICAO','CORRECAO','FUSAO','TRANSFERENCIA'))
);

-- Invariante 9 (revista): uma posição comporta um pallet, exceto quando há
-- bonificação do mesmo CNPJ — caso que o banco não consegue expressar, e por
-- isso é validado em EnderecamentoServico.

CREATE UNIQUE INDEX ux_enderecamento_pallet_ativo
    ON enderecamento(pallet_id) WHERE liberado_em IS NULL;

-- -------------------------------------------------------------------- leitura

CREATE TABLE leitura (
    id                  INTEGER PRIMARY KEY,
    idempotency_key     TEXT    NOT NULL UNIQUE,
    pallet_id           INTEGER NOT NULL REFERENCES pallet(id),
    pallet_origem_id    INTEGER REFERENCES pallet(id),
    item_nota_id        INTEGER NOT NULL REFERENCES item_nota(id),
    produto_id          INTEGER NOT NULL REFERENCES produto(id),
    codigo_lido         TEXT    NOT NULL,
    usuario_id          INTEGER NOT NULL REFERENCES usuario(id),
    coletor_id          TEXT,
    lido_em             TEXT,
    registrado_em       TEXT    NOT NULL,
    cancelada           INTEGER NOT NULL DEFAULT 0,
    cancelada_em        TEXT,
    cancelada_por       INTEGER REFERENCES usuario(id),
    cancelamento_motivo TEXT
);

CREATE INDEX ix_leitura_item   ON leitura(item_nota_id, cancelada);
CREATE INDEX ix_leitura_pallet ON leitura(pallet_id, cancelada);

CREATE TABLE item_em_separacao (
    item_nota_id INTEGER NOT NULL REFERENCES item_nota(id),
    usuario_id   INTEGER NOT NULL REFERENCES usuario(id),
    pallet_id    INTEGER REFERENCES pallet(id),
    iniciado_em  TEXT    NOT NULL,
    PRIMARY KEY (item_nota_id, usuario_id)
);

-- ------------------------------------------------- expedição, estorno e reentrega

CREATE TABLE expedicao (
    id             INTEGER PRIMARY KEY,
    nota_fiscal_id INTEGER NOT NULL REFERENCES nota_fiscal(id),
    expedido_em    TEXT    NOT NULL,
    expedido_por   INTEGER NOT NULL REFERENCES usuario(id),
    observacao     TEXT,
    estornado_em   TEXT,
    estornado_por  INTEGER REFERENCES usuario(id),
    estorno_motivo TEXT,
    estorno_tipo   TEXT    CHECK (estorno_tipo IN ('ERRO_OPERACIONAL','RETORNO_CLIENTE'))
);

CREATE TABLE expedicao_pallet (
    id            INTEGER PRIMARY KEY,
    expedicao_id  INTEGER NOT NULL REFERENCES expedicao(id),
    pallet_id     INTEGER NOT NULL REFERENCES pallet(id),
    retornado_em  TEXT,
    retornado_por INTEGER REFERENCES usuario(id)
);

-- ------------------------------------------------------------------ devolução

CREATE TABLE nota_devolucao (
    id                    INTEGER PRIMARY KEY,
    chave                 TEXT    NOT NULL UNIQUE,
    numero                TEXT    NOT NULL,
    serie                 TEXT    NOT NULL,
    emissao               TEXT    NOT NULL,
    emitente_cnpj         TEXT,
    emitente_nome         TEXT    NOT NULL,
    nota_fiscal_origem_id INTEGER NOT NULL UNIQUE REFERENCES nota_fiscal(id),
    status                TEXT    NOT NULL CHECK (status IN
                              ('AGUARDANDO_CONFERENCIA','EM_CONFERENCIA','CONFERIDA','BAIXADA')),
    xml_original          BLOB,
    importado_em          TEXT    NOT NULL,
    importado_por         INTEGER NOT NULL REFERENCES usuario(id)
);

CREATE TABLE item_devolucao (
    id                       INTEGER PRIMARY KEY,
    nota_devolucao_id        INTEGER NOT NULL REFERENCES nota_devolucao(id),
    n_item                   INTEGER NOT NULL,
    cprod_cliente            TEXT,
    xprod_cliente            TEXT,
    cean_cliente             TEXT,
    produto_id               INTEGER REFERENCES produto(id),
    qtd_esperada_un          INTEGER NOT NULL,
    qtd_conferida_un         INTEGER NOT NULL DEFAULT 0,
    status                   TEXT    NOT NULL CHECK (status IN
                                 ('ABERTO','EM_CONFERENCIA','FECHADO','FECHADO_DIVERGENCIA')),
    divergencia_justificativa TEXT,
    UNIQUE (nota_devolucao_id, n_item)
);

CREATE TABLE leitura_devolucao (
    id                  INTEGER PRIMARY KEY,
    idempotency_key     TEXT    NOT NULL UNIQUE,
    pallet_id           INTEGER REFERENCES pallet(id),
    item_devolucao_id   INTEGER NOT NULL REFERENCES item_devolucao(id),
    produto_id          INTEGER NOT NULL REFERENCES produto(id),
    codigo_lido         TEXT,
    modo                TEXT    NOT NULL CHECK (modo IN ('BIPE','DIGITADO')),
    qtd_un              INTEGER NOT NULL,
    usuario_id          INTEGER NOT NULL REFERENCES usuario(id),
    coletor_id          TEXT,
    lido_em             TEXT,
    registrado_em       TEXT    NOT NULL,
    cancelada           INTEGER NOT NULL DEFAULT 0,
    cancelada_em        TEXT,
    cancelada_por       INTEGER REFERENCES usuario(id),
    cancelamento_motivo TEXT
);

CREATE INDEX ix_leitura_dev_item ON leitura_devolucao(item_devolucao_id, cancelada);

CREATE TABLE baixa_devolucao (
    id                    INTEGER PRIMARY KEY,
    nota_devolucao_id     INTEGER NOT NULL UNIQUE REFERENCES nota_devolucao(id),
    tipo                  TEXT    NOT NULL CHECK (tipo IN ('DESCARTE','REVENDA')),
    observacao            TEXT,
    nf_revenda_referencia TEXT,
    baixado_em            TEXT    NOT NULL,
    baixado_por           INTEGER NOT NULL REFERENCES usuario(id)
);

-- ------------------------------------------------------------------ auditoria

CREATE TABLE log_auditoria (
    id           INTEGER PRIMARY KEY,
    entidade     TEXT    NOT NULL,
    entidade_id  INTEGER,
    acao         TEXT    NOT NULL,
    usuario_id   INTEGER REFERENCES usuario(id),
    em           TEXT    NOT NULL,
    detalhe_json TEXT
);

CREATE INDEX ix_auditoria_entidade ON log_auditoria(entidade, entidade_id);
