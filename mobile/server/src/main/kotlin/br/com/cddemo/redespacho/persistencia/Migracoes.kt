package br.com.cddemo.redespacho.persistencia

import br.com.cddemo.redespacho.importacao.NfeParser
import br.com.cddemo.redespacho.servico.clienteDaDevolucao
import java.sql.Connection

/**
 * Evolução do schema em bancos que já têm dados.
 *
 * O `schema.sql` cria o banco novo; estas migrações levam um banco existente
 * até a mesma versão. Toda alteração de estrutura entra aqui, nunca só no
 * schema.sql — senão o servidor em produção fica para trás do código.
 */
object Migracoes {

    /** Versão alvo do schema. Subir junto com cada passo novo. */
    const val VERSAO_ALVO = 16

    fun aplicar(c: Connection) {
        criarControle(c)
        val atual = versaoAtual(c)

        if (atual < 2) {
            // Bonificação: NF própria, poucas caixas, endereçada junto da venda.
            executar(c, "ALTER TABLE nota_fiscal ADD COLUMN bonificacao INTEGER NOT NULL DEFAULT 0")

            // A unicidade da posição deixa de ser garantida pelo banco: uma
            // posição passa a comportar mais de um pallet quando são do mesmo
            // CNPJ e há bonificação envolvida. A regra vira do serviço, que é o
            // único lugar capaz de comparar CNPJ. Ver EnderecamentoServico.
            executar(c, "DROP INDEX IF EXISTS ux_enderecamento_posicao_ativa")
            registrar(c, 2, "bonificação e coabitação de posição")
        }

        if (atual < 3) {
            // O CHECK do perfil não aceita CONSULTA. SQLite não altera
            // constraint: recria a tabela e traz os dados.
            executar(c, "PRAGMA foreign_keys = OFF")
            executar(
                c,
                """CREATE TABLE usuario_novo (
                       id            INTEGER PRIMARY KEY,
                       login         TEXT    NOT NULL UNIQUE,
                       nome          TEXT    NOT NULL,
                       senha_hash    TEXT    NOT NULL,
                       senha_salt    TEXT    NOT NULL,
                       perfil        TEXT    NOT NULL CHECK (perfil IN
                                         ('CONSULTA','SEPARADOR','SUPERVISOR','ADMIN')),
                       impressora_id INTEGER REFERENCES impressora(id),
                       multi_item    INTEGER NOT NULL DEFAULT 0,
                       ativo         INTEGER NOT NULL DEFAULT 1,
                       criado_em     TEXT    NOT NULL,
                       criado_por    INTEGER REFERENCES usuario(id)
                   )""",
            )
            executar(c, "INSERT INTO usuario_novo SELECT * FROM usuario")
            executar(c, "DROP TABLE usuario")
            executar(c, "ALTER TABLE usuario_novo RENAME TO usuario")
            executar(c, "PRAGMA foreign_keys = ON")
            registrar(c, 3, "perfil CONSULTA")
        }

        if (atual < 4) {
            // Só acrescenta: tabela nova, nenhum dado existente é tocado.
            // Identidade do banco para a restauração automática. Ver Identidade.
            executar(
                c,
                """CREATE TABLE IF NOT EXISTS banco_identidade (
                       id          INTEGER PRIMARY KEY CHECK (id = 1),
                       linhagem    TEXT    NOT NULL,
                       ramo        TEXT    NOT NULL,
                       geracao     INTEGER NOT NULL,
                       criado_em   TEXT    NOT NULL,
                       alterado_em TEXT,
                       ramo_pendente INTEGER NOT NULL DEFAULT 0
                   )""",
            )
            // Banco que já tinha a tabela sem a coluna (versão de teste).
            executar(c, "ALTER TABLE banco_identidade ADD COLUMN ramo_pendente INTEGER NOT NULL DEFAULT 0")
            // Árvore de ramos: cada restauração abre um ramo novo, filho do
            // ramo e da geração de onde veio. É o que revela bifurcação.
            executar(
                c,
                """CREATE TABLE IF NOT EXISTS banco_ramo (
                       ramo         TEXT    PRIMARY KEY,
                       ramo_pai     TEXT,
                       geracao_base INTEGER NOT NULL,
                       criado_em    TEXT    NOT NULL,
                       maquina      TEXT
                   )""",
            )
            val jaTem = c.buscarUm("SELECT COUNT(*) FROM banco_identidade") { it.getInt(1) } ?: 0
            if (jaTem == 0) {
                // Banco que já tem operação começa com a geração proporcional
                // ao conteúdo: assim um banco novo e vazio, criado por engano em
                // outra máquina, nunca passa na frente dele.
                val semente = c.buscarUm(
                    """SELECT (SELECT COUNT(*) FROM leitura) + (SELECT COUNT(*) FROM pallet)
                            + (SELECT COUNT(*) FROM instrucao) + (SELECT COUNT(*) FROM leitura_devolucao)""",
                ) { it.getLong(1) } ?: 0L
                val agora = java.time.Instant.now().toString()
                val ramo = java.util.UUID.randomUUID().toString()
                c.atualizar(
                    "INSERT INTO banco_identidade (id, linhagem, ramo, geracao, criado_em) VALUES (1, ?, ?, ?, ?)",
                    java.util.UUID.randomUUID().toString(), ramo, semente, agora,
                )
                c.atualizar(
                    "INSERT INTO banco_ramo (ramo, ramo_pai, geracao_base, criado_em, maquina) VALUES (?, NULL, ?, ?, ?)",
                    ramo, semente, agora, System.getenv("COMPUTERNAME"),
                )
            }
            registrar(c, 4, "identidade do banco (restauração automática)")
        }

        if (atual < 5) {
            // Retorno de NF expedida (recusa total, reentrega) com conferência
            // caixa a caixa. Só tabelas novas: nota_fiscal e pallet ficam como
            // estão — mudar o CHECK delas exigiria recriar a tabela com os
            // dados de produção dentro. Ver RetornoServico.
            executar(
                c,
                """CREATE TABLE IF NOT EXISTS retorno (
                       id                   INTEGER PRIMARY KEY,
                       nota_fiscal_id       INTEGER NOT NULL REFERENCES nota_fiscal(id),
                       motivo               TEXT    NOT NULL,
                       status               TEXT    NOT NULL CHECK (status IN
                                                ('EM_CONFERENCIA','AGUARDANDO_TRATATIVA','TRATADO','CANCELADO')),
                       nota_status_anterior TEXT    NOT NULL,
                       registrado_em        TEXT    NOT NULL,
                       registrado_por       INTEGER NOT NULL REFERENCES usuario(id),
                       conferido_em         TEXT,
                       conferido_por        INTEGER REFERENCES usuario(id),
                       conferencia_manual   INTEGER NOT NULL DEFAULT 0,
                       tratativa            TEXT    CHECK (tratativa IN ('REENTREGA','REFATURAMENTO','DESCARTE')),
                       nfs_refaturamento    TEXT,
                       tratativa_obs        TEXT,
                       tratado_em           TEXT,
                       tratado_por          INTEGER REFERENCES usuario(id),
                       cancelado_em         TEXT,
                       cancelado_por        INTEGER REFERENCES usuario(id),
                       cancelamento_motivo  TEXT
                   )""",
            )
            executar(c, "CREATE INDEX IF NOT EXISTS ix_retorno_nota ON retorno(nota_fiscal_id)")
            executar(
                c,
                """CREATE TABLE IF NOT EXISTS retorno_pallet (
                       id                      INTEGER PRIMARY KEY,
                       retorno_id              INTEGER NOT NULL REFERENCES retorno(id),
                       pallet_id               INTEGER NOT NULL REFERENCES pallet(id),
                       expedicao_pallet_id     INTEGER NOT NULL REFERENCES expedicao_pallet(id),
                       qtd_esperada            INTEGER NOT NULL,
                       status                  TEXT    NOT NULL CHECK (status IN ('ABERTO','CONFERIDO','CONFERIDO_FALTA')),
                       manual                  INTEGER NOT NULL DEFAULT 0,
                       justificativa           TEXT,
                       fechado_em              TEXT,
                       fechado_por             INTEGER REFERENCES usuario(id),
                       status_pallet_tratativa TEXT
                   )""",
            )
            executar(c, "CREATE INDEX IF NOT EXISTS ix_retorno_pallet_retorno ON retorno_pallet(retorno_id)")
            executar(c, "CREATE INDEX IF NOT EXISTS ix_retorno_pallet_pallet ON retorno_pallet(pallet_id)")
            // O que cada pallet levou, por produto, no momento do registro: a
            // conferência compara com isto, e não com o pallet de agora.
            executar(
                c,
                """CREATE TABLE IF NOT EXISTS retorno_pallet_produto (
                       retorno_pallet_id INTEGER NOT NULL REFERENCES retorno_pallet(id),
                       produto_id        INTEGER NOT NULL REFERENCES produto(id),
                       esperada          INTEGER NOT NULL,
                       PRIMARY KEY (retorno_pallet_id, produto_id)
                   )""",
            )
            executar(
                c,
                """CREATE TABLE IF NOT EXISTS retorno_leitura (
                       id                INTEGER PRIMARY KEY,
                       idempotency_key   TEXT    NOT NULL UNIQUE,
                       retorno_pallet_id INTEGER NOT NULL REFERENCES retorno_pallet(id),
                       produto_id        INTEGER NOT NULL REFERENCES produto(id),
                       codigo_lido       TEXT    NOT NULL,
                       usuario_id        INTEGER NOT NULL REFERENCES usuario(id),
                       lido_em           TEXT,
                       registrado_em     TEXT    NOT NULL,
                       cancelada         INTEGER NOT NULL DEFAULT 0,
                       cancelada_em      TEXT,
                       cancelada_por     INTEGER REFERENCES usuario(id)
                   )""",
            )
            executar(
                c,
                "CREATE INDEX IF NOT EXISTS ix_retorno_leitura_pallet ON retorno_leitura(retorno_pallet_id, cancelada)",
            )
            registrar(c, 5, "retorno de NF expedida com conferência")
        }

        if (atual < 6) {
            // Reentrega com falta: a NF sai com o que voltou e o cliente emite
            // NF de devolução da falta. As caixas que não voltaram saem do
            // pallet como leitura.cancelada = 2 ("faltou no retorno"), ligadas
            // ao retorno. Só coluna nova: nada muda nas leituras que existem.
            executar(c, "ALTER TABLE leitura ADD COLUMN retorno_falta_id INTEGER REFERENCES retorno(id)")
            executar(
                c,
                """CREATE INDEX IF NOT EXISTS ix_leitura_retorno_falta ON leitura(retorno_falta_id)
                   WHERE retorno_falta_id IS NOT NULL""",
            )
            registrar(c, 6, "caixa que faltou no retorno sai do pallet")
        }

        if (atual < 7) {
            // Repaletização: caixa a caixa, de um pallet para outro da mesma NF.
            // Uma linha por caixa passada, com a chave de idempotência de quem
            // bipou (o reenvio não move outra caixa) e o necessário para
            // desfazer a última. O movimento continua em pallet_movimentacao.
            // Só tabela nova.
            executar(
                c,
                """CREATE TABLE IF NOT EXISTS repaletizacao_caixa (
                       id                      INTEGER PRIMARY KEY,
                       idempotency_key         TEXT    NOT NULL UNIQUE,
                       leitura_id              INTEGER NOT NULL REFERENCES leitura(id),
                       produto_id              INTEGER NOT NULL REFERENCES produto(id),
                       pallet_origem_id        INTEGER NOT NULL REFERENCES pallet(id),
                       pallet_destino_id       INTEGER NOT NULL REFERENCES pallet(id),
                       origem_status_anterior  TEXT    NOT NULL,
                       destino_status_anterior TEXT    NOT NULL,
                       origem_esvaziou         INTEGER NOT NULL DEFAULT 0,
                       pallet_movimentacao_id  INTEGER REFERENCES pallet_movimentacao(id),
                       codigo_lido             TEXT    NOT NULL,
                       usuario_id              INTEGER NOT NULL REFERENCES usuario(id),
                       movido_em               TEXT    NOT NULL,
                       desfeita_em             TEXT,
                       desfeita_por            INTEGER REFERENCES usuario(id),
                       concluida_em            TEXT
                   )""",
            )
            // Banco de teste que criou a tabela antes da coluna.
            executar(c, "ALTER TABLE repaletizacao_caixa ADD COLUMN concluida_em TEXT")
            executar(
                c,
                """CREATE INDEX IF NOT EXISTS ix_repaletizacao_par
                   ON repaletizacao_caixa(usuario_id, pallet_origem_id, pallet_destino_id)""",
            )
            registrar(c, 7, "repaletização caixa a caixa")
        }

        if (atual < 8) recriarNotaDevolucao(c)

        if (atual < 9) {
            // Produto trocado na devolução: o item do que veio aponta o item
            // declarado na NF do cliente. Só colunas novas.
            executar(c, "ALTER TABLE item_devolucao ADD COLUMN troca_de_item_id INTEGER REFERENCES item_devolucao(id)")
            executar(c, "ALTER TABLE item_devolucao ADD COLUMN troca_justificativa TEXT")
            executar(c, "ALTER TABLE item_devolucao ADD COLUMN troca_em TEXT")
            executar(c, "ALTER TABLE item_devolucao ADD COLUMN troca_por INTEGER REFERENCES usuario(id)")
            registrar(c, 9, "produto trocado na devolução")
        }

        if (atual < 10) {
            // Motivo da devolução: na NF e, quando difere, no item. Só colunas novas.
            executar(c, "ALTER TABLE nota_devolucao ADD COLUMN motivo TEXT")
            executar(c, "ALTER TABLE item_devolucao ADD COLUMN motivo TEXT")
            registrar(c, 10, "motivo da devolução")
        }

        if (atual < 11) {
            // Pallet novo que recebeu uma fusão ficava CRIADO com as caixas
            // dentro: sumia do estoque e da expedição. Volta a ENCERRADO (fila
            // de endereçamento), com quem e quando juntou. Nada é apagado.
            executar(
                c,
                """UPDATE pallet SET status = 'ENCERRADO',
                          encerrado_em = COALESCE(encerrado_em,
                              (SELECT MAX(m.movido_em) FROM pallet_movimentacao m WHERE m.pallet_destino_id = pallet.id)),
                          encerrado_por = COALESCE(encerrado_por,
                              (SELECT m.movido_por FROM pallet_movimentacao m
                                WHERE m.pallet_destino_id = pallet.id ORDER BY m.id DESC LIMIT 1))
                    WHERE status = 'CRIADO'
                      AND EXISTS (SELECT 1 FROM leitura l WHERE l.pallet_id = pallet.id AND l.cancelada = 0)""",
            )
            registrar(c, 11, "pallet que recebeu fusão sai de CRIADO")
        }

        if (atual < 12) {
            // Cliente da devolução: na total (NF emitida pela empresa) é o
            // destinatário, não o emitente. Colunas novas; as já lançadas se
            // preenchem relendo o XML guardado. Nada é apagado.
            executar(c, "ALTER TABLE nota_devolucao ADD COLUMN cliente_nome TEXT")
            executar(c, "ALTER TABLE nota_devolucao ADD COLUMN cliente_cnpj TEXT")
            executar(c, "ALTER TABLE nota_devolucao ADD COLUMN cliente_bairro TEXT")
            executar(c, "ALTER TABLE nota_devolucao ADD COLUMN cliente_cidade TEXT")
            c.buscarTodos("SELECT id, xml_original FROM nota_devolucao WHERE cliente_nome IS NULL") {
                it.getLong(1) to it.getBytes(2)
            }.forEach { (id, xml) ->
                // XML que não se lê fica como estava: a lista cai no emitente.
                runCatching { clienteDaDevolucao(NfeParser.parse(xml)) }.getOrNull()?.let { cl ->
                    c.atualizar(
                        "UPDATE nota_devolucao SET cliente_nome=?, cliente_cnpj=?, cliente_bairro=?, cliente_cidade=? WHERE id=?",
                        cl.nome, cl.cnpj, cl.bairro, cl.cidade, id,
                    )
                }
            }
            registrar(c, 12, "cliente da devolução (total e parcial) com bairro e cidade")
        }

        if (atual < 13) {
            // Cadastro de motivos de devolução, para padronizar. Começa com os
            // motivos já usados nas devoluções lançadas. Só tabela nova.
            executar(
                c,
                """CREATE TABLE IF NOT EXISTS motivo_devolucao (
                       id         INTEGER PRIMARY KEY,
                       descricao  TEXT    NOT NULL UNIQUE COLLATE NOCASE,
                       ativo      INTEGER NOT NULL DEFAULT 1,
                       criado_em  TEXT    NOT NULL,
                       criado_por INTEGER REFERENCES usuario(id)
                   )""",
            )
            executar(
                c,
                """INSERT OR IGNORE INTO motivo_devolucao (descricao, criado_em)
                   SELECT TRIM(m), strftime('%Y-%m-%dT%H:%M:%SZ', 'now') FROM (
                       SELECT motivo AS m FROM nota_devolucao WHERE TRIM(COALESCE(motivo, '')) <> ''
                       UNION SELECT motivo FROM item_devolucao WHERE TRIM(COALESCE(motivo, '')) <> '')""",
            )
            registrar(c, 13, "cadastro de motivos de devolução")
        }

        if (atual < 14) {
            // Conferência de estoque: a sobra entra como devolução sem NF, no
            // mesmo pallet e com a mesma baixa. Só coluna e sequência novas.
            executar(c, "ALTER TABLE nota_devolucao ADD COLUMN sobra INTEGER NOT NULL DEFAULT 0")
            executar(c, "INSERT OR IGNORE INTO sequencia (nome, valor) VALUES ('SOBRA', 0)")
            registrar(c, 14, "conferência de estoque (sobra)")
        }

        if (atual < 15) {
            // A sobra pode sair em partes: algumas caixas de um produto hoje,
            // o resto depois. Tabela nova e uma coluna na leitura; nada é
            // apagado — a caixa que sai fica marcada como baixada.
            executar(
                c,
                """CREATE TABLE IF NOT EXISTS baixa_sobra (
                       id                INTEGER PRIMARY KEY,
                       nota_devolucao_id INTEGER NOT NULL REFERENCES nota_devolucao(id),
                       item_devolucao_id INTEGER NOT NULL REFERENCES item_devolucao(id),
                       qtd_un            INTEGER NOT NULL,
                       tipo              TEXT    NOT NULL CHECK (tipo IN ('DESCARTE','REVENDA')),
                       observacao        TEXT,
                       nf_revenda        TEXT,
                       baixado_em        TEXT    NOT NULL,
                       baixado_por       INTEGER NOT NULL REFERENCES usuario(id)
                   )""",
            )
            executar(c, "ALTER TABLE leitura_devolucao ADD COLUMN baixa_sobra_id INTEGER REFERENCES baixa_sobra(id)")
            registrar(c, 15, "baixa de sobra por caixa")
        }

        if (atual < 16) {
            // Desembarque na retaguarda: o pallet montado por quantidade, não
            // bipe a bipe. Cada desembarque é um pallet, com o responsável
            // escolhido no cadastro — é o nome que aparece em "Montado por".
            // A leitura ganha de qual desembarque veio; as antigas ficam nulas.
            executar(
                c,
                """CREATE TABLE IF NOT EXISTS desembarque (
                       id             INTEGER PRIMARY KEY,
                       nota_fiscal_id INTEGER NOT NULL REFERENCES nota_fiscal(id),
                       pallet_id      INTEGER NOT NULL REFERENCES pallet(id),
                       usuario_id     INTEGER NOT NULL REFERENCES usuario(id),
                       status         TEXT    NOT NULL CHECK (status IN ('ABERTO','ENCERRADO','CANCELADO')),
                       aberto_em      TEXT    NOT NULL,
                       aberto_por     INTEGER NOT NULL REFERENCES usuario(id),
                       encerrado_em   TEXT,
                       encerrado_por  INTEGER REFERENCES usuario(id),
                       justificativa  TEXT
                   )""",
            )
            executar(c, "CREATE INDEX IF NOT EXISTS ix_desembarque_nota ON desembarque(nota_fiscal_id, status)")
            executar(c, "ALTER TABLE leitura ADD COLUMN desembarque_id INTEGER REFERENCES desembarque(id)")

            // Estorno da baixa de devolução: baixa_devolucao é uma por NF
            // (UNIQUE), então a estornada sai de lá — mas não se perde: vai
            // inteira para esta tabela, com quem estornou e por quê.
            executar(
                c,
                """CREATE TABLE IF NOT EXISTS baixa_devolucao_estornada (
                       id                    INTEGER PRIMARY KEY,
                       nota_devolucao_id     INTEGER NOT NULL REFERENCES nota_devolucao(id),
                       tipo                  TEXT    NOT NULL,
                       observacao            TEXT,
                       nf_revenda_referencia TEXT,
                       baixado_em            TEXT    NOT NULL,
                       baixado_por           INTEGER NOT NULL REFERENCES usuario(id),
                       estornado_em          TEXT    NOT NULL,
                       estornado_por         INTEGER NOT NULL REFERENCES usuario(id),
                       motivo                TEXT    NOT NULL
                   )""",
            )
            registrar(c, 16, "desembarque por quantidade e estorno da baixa de devolução")
        }
    }

    /**
     * Devolução com a NF de origem escolhida no sistema, lida do XML dela ou só
     * digitada. A coluna de origem era NOT NULL UNIQUE — uma devolução por NF,
     * sempre de NF do sistema — e SQLite não altera constraint: a tabela é
     * recriada com os dados dentro, numa transação (vai tudo ou nada muda). As
     * colunas antigas ficam na mesma ordem; as de origem entram no fim, e as
     * devoluções que já existem ganham número e chave da NF a que já apontavam.
     */
    private fun recriarNotaDevolucao(c: Connection) {
        executar(c, "PRAGMA foreign_keys = OFF")
        val autoCommitAntes = c.autoCommit
        c.autoCommit = false
        try {
            executar(
                c,
                """CREATE TABLE nota_devolucao_nova (
                       id                    INTEGER PRIMARY KEY,
                       chave                 TEXT    NOT NULL UNIQUE,
                       numero                TEXT    NOT NULL,
                       serie                 TEXT    NOT NULL,
                       emissao               TEXT    NOT NULL,
                       emitente_cnpj         TEXT,
                       emitente_nome         TEXT    NOT NULL,
                       nota_fiscal_origem_id INTEGER REFERENCES nota_fiscal(id),
                       status                TEXT    NOT NULL CHECK (status IN
                                                 ('AGUARDANDO_CONFERENCIA','EM_CONFERENCIA','CONFERIDA','BAIXADA')),
                       xml_original          BLOB,
                       importado_em          TEXT    NOT NULL,
                       importado_por         INTEGER NOT NULL REFERENCES usuario(id),
                       origem_tipo           TEXT    CHECK (origem_tipo IN ('SISTEMA','XML','DIGITADA')),
                       origem_numero         TEXT,
                       origem_serie          TEXT,
                       origem_chave          TEXT,
                       origem_emissao        TEXT,
                       origem_xml            BLOB
                   )""",
            )
            executar(
                c,
                """INSERT INTO nota_devolucao_nova
                       (id, chave, numero, serie, emissao, emitente_cnpj, emitente_nome, nota_fiscal_origem_id,
                        status, xml_original, importado_em, importado_por,
                        origem_tipo, origem_numero, origem_serie, origem_chave, origem_emissao)
                   SELECT d.id, d.chave, d.numero, d.serie, d.emissao, d.emitente_cnpj, d.emitente_nome,
                          d.nota_fiscal_origem_id, d.status, d.xml_original, d.importado_em, d.importado_por,
                          CASE WHEN d.nota_fiscal_origem_id IS NULL THEN NULL ELSE 'SISTEMA' END,
                          n.numero, n.serie, n.chave, n.emissao
                     FROM nota_devolucao d LEFT JOIN nota_fiscal n ON n.id = d.nota_fiscal_origem_id""",
            )
            executar(c, "DROP TABLE nota_devolucao")
            executar(c, "ALTER TABLE nota_devolucao_nova RENAME TO nota_devolucao")
            executar(c, "CREATE INDEX IF NOT EXISTS ix_devolucao_origem ON nota_devolucao(nota_fiscal_origem_id)")
            registrar(c, 8, "devolução com NF de origem livre")
            c.commit()
        } catch (e: Exception) {
            c.rollback()
            throw e
        } finally {
            c.autoCommit = autoCommitAntes
            executar(c, "PRAGMA foreign_keys = ON")
        }
    }

    private fun criarControle(c: Connection) {
        c.createStatement().use {
            it.executeUpdate(
                """CREATE TABLE IF NOT EXISTS schema_versao (
                       versao    INTEGER PRIMARY KEY,
                       descricao TEXT NOT NULL,
                       aplicada_em TEXT NOT NULL
                   )"""
            )
        }
        // Banco criado pelo schema.sql já nasce na versão 1.
        if (versaoAtual(c) == 0) registrar(c, 1, "schema inicial")
    }

    private fun versaoAtual(c: Connection): Int =
        c.buscarUm("SELECT COALESCE(MAX(versao), 0) FROM schema_versao") { it.getInt(1) } ?: 0

    private fun registrar(c: Connection, versao: Int, descricao: String) {
        c.atualizar(
            "INSERT OR IGNORE INTO schema_versao (versao, descricao, aplicada_em) VALUES (?,?,?)",
            versao, descricao, java.time.Instant.now().toString(),
        )
    }

    /** Ignora erro de coluna já existente: migração precisa poder rodar duas vezes. */
    private fun executar(c: Connection, sql: String) {
        try {
            c.createStatement().use { it.executeUpdate(sql) }
        } catch (e: Exception) {
            if (e.message?.contains("duplicate column", ignoreCase = true) != true) throw e
        }
    }
}
