package br.com.cddemo.redespacho.servico

import br.com.cddemo.redespacho.api.Erros
import br.com.cddemo.redespacho.dominio.Gtin
import br.com.cddemo.redespacho.dominio.ResultadoGtin
import br.com.cddemo.redespacho.importacao.NfeInvalidoException
import br.com.cddemo.redespacho.importacao.NfeParser
import br.com.cddemo.redespacho.importacao.NotaFiscalXml
import br.com.cddemo.redespacho.importacao.Planilha
import br.com.cddemo.redespacho.persistencia.Db
import com.fasterxml.jackson.annotation.JsonProperty
import br.com.cddemo.redespacho.persistencia.atualizar
import br.com.cddemo.redespacho.persistencia.buscarTodos
import br.com.cddemo.redespacho.persistencia.buscarUm
import br.com.cddemo.redespacho.persistencia.inserir
import java.sql.Connection
import java.time.Instant

data class SugestaoProduto(
    val produtoId: Long,
    val codigo: String,
    val descricao: String,
    val unidadesPorCaixa: Int?,
    val origem: String,
)

data class ItemPreviaDevolucao(
    @get:JsonProperty("nItem") val nItem: Int,
    val cprodCliente: String,
    val xprodCliente: String,
    val ceanCliente: String?,
    /** A quantidade do XML (qCom), na unidade dele. */
    val qtdCaixas: Int,
    /** Nulo enquanto não houver produto sugerido: sem ele não se sabe o fator. */
    val qtdUn: Int?,
    val sugestao: SugestaoProduto?,
    /** A unidade como veio no XML do cliente (uCom): CX, UN… */
    val unidadeXml: String = "",
    /** CX ou UN: o que a tela já deixa escolhido, lido de [unidadeXml]. */
    val unidadeSugerida: String = "CX",
)

/**
 * A unidade do XML do cliente em caixa ou unidade. O que não se reconhece fica
 * caixa, que é como a devolução sempre foi lançada.
 */
internal fun unidadeDoXml(uCom: String): String {
    val u = uCom.trim().uppercase()
    return if (u.startsWith("UN") || u in setOf("PC", "PÇ", "PECA", "PEÇA", "LT", "LATA")) "UN" else "CX"
}

/**
 * Código interno do produto para a etiqueta 2ª via: GTIN-14 com prefixo 02 —
 * a faixa que a GS1 reserva para uso interno, que nenhum fabricante recebe —
 * e o id do produto, com dígito verificador. O coletor e o servidor o leem
 * como qualquer GTIN-14.
 */
internal fun codigoInterno(produtoId: Long): String {
    val base = "029" + produtoId.toString().padStart(10, '0')
    val soma = base.reversed().mapIndexed { i, c -> (c - '0') * if (i % 2 == 0) 3 else 1 }.sum()
    return base + (10 - soma % 10) % 10
}

/** Raiz do CNPJ da empresa: NF de devolução emitida por ela é devolução total. */
private const val RAIZ_CNPJ_EMPRESA = "00000000"

/** Quem devolve, com o endereço — é o que a lista mostra como cliente. */
internal data class ClienteDevolucao(val nome: String, val cnpj: String?, val bairro: String?, val cidade: String?)

/**
 * O cliente da devolução. Na parcial, o cliente emite a NF: é o emitente. Na
 * total, quem emite é a empresa, e o cliente é o destinatário.
 */
internal fun clienteDaDevolucao(nf: NotaFiscalXml): ClienteDevolucao {
    fun cidade(municipio: String?, uf: String?) =
        listOfNotNull(municipio?.trim()?.takeIf { it.isNotEmpty() }, uf?.trim()?.takeIf { it.isNotEmpty() })
            .joinToString("/").ifEmpty { null }
    val emitidaPelaEmpresa = nf.emitCnpj?.startsWith(RAIZ_CNPJ_EMPRESA) == true ||
        nf.emitNome.orEmpty().uppercase().contains("EMPRESA DEMO")
    return if (emitidaPelaEmpresa) {
        ClienteDevolucao(nf.destNome, nf.destCnpj, nf.destBairro, cidade(nf.destMunicipio, nf.destUf))
    } else {
        ClienteDevolucao(nf.emitNome.orEmpty(), nf.emitCnpj, nf.emitBairro, cidade(nf.emitMunicipio, nf.emitUf))
    }
}

/** O que vai na etiqueta 2ª via de um item da devolução. */
data class EtiquetaDoItem(
    val codigoBarras: String,
    val produtoCodigo: String,
    val produtoDescricao: String,
    /** Criado agora pelo sistema: o produto não tinha código que o coletor lesse. */
    val codigoInterno: Boolean,
    /** Caixas que a conferência ainda espera do item — quantas etiquetas sugerir. */
    val caixasFaltando: Int?,
)

data class PreviaDevolucao(
    val chave: String,
    val numero: String,
    val serie: String,
    val emissao: String,
    val emitenteNome: String,
    val emitenteCnpj: String?,
    val notaOrigemId: Long?,
    val notaOrigemNumero: String?,
    val itens: List<ItemPreviaDevolucao>,
    val avisos: List<String>,
    /** SISTEMA, XML ou DIGITADA; nulo se a prévia veio sem origem. */
    val origemTipo: String? = null,
    val origemChave: String? = null,
    val origemCliente: String? = null,
    /** A NF de venda que o próprio XML da devolução aponta (refNFe), se apontar. */
    val referencia: ReferenciaOrigem? = null,
)

/** A NF de venda citada no XML da devolução; [notaId] quando o sistema a tem. */
data class ReferenciaOrigem(
    val chave: String,
    val numero: String,
    val notaId: Long?,
    val cliente: String?,
)

/**
 * De onde saiu a mercadoria devolvida. Uma das três: a NF do sistema, o XML
 * dela (NF que não passou por aqui, de antes do sistema, por exemplo) ou só o
 * número digitado.
 */
data class OrigemDevolucao(
    val notaId: Long? = null,
    val xml: ByteArray? = null,
    val numero: String? = null,
)

data class ItemDevolucaoResumo(
    val id: Long,
    val produtoCodigo: String?,
    val produtoDescricao: String?,
    val cprodCliente: String?,
    val xprodCliente: String?,
    val unidadesPorCaixa: Int?,
    val qtdEsperadaUn: Int,
    val qtdConferidaUn: Int,
    val status: String,
    val justificativa: String?,
    @get:JsonProperty("nItem") val nItem: Int = 0,
    /** Item de produto trocado: o nº do item da NF no lugar do qual ele veio. */
    val trocaDeItem: Int? = null,
    val trocaJustificativa: String? = null,
    val trocadoPor: String? = null,
    val trocadoEm: String? = null,
    /** O motivo deste item, quando difere do da NF; nulo segue o da NF. */
    val motivo: String? = null,
)

data class DevolucaoResumo(
    val id: Long,
    val numero: String,
    /**
     * Quem devolve: o cliente — o emitente na devolução parcial, o destinatário
     * na total (NF emitida pela empresa). O nome do campo é o de antes: o coletor lê.
     */
    val emitenteNome: String,
    val notaOrigemNumero: String?,
    val status: String,
    val qtdEsperadaUn: Int,
    val qtdConferidaUn: Int,
    val importadoEm: String,
    /** SISTEMA, XML ou DIGITADA. */
    val origemTipo: String? = null,
    val origemChave: String? = null,
    /** Motivo da devolução: vale para todo item sem motivo próprio. */
    val motivo: String? = null,
    /** Os motivos da nota — o da NF e os dos itens que diferem —, para a lista mostrar sem abrir. */
    val motivos: List<String> = emptyList(),
    /** Conferência de estoque: devolução sem NF, só com o que foi achado no estoque. */
    val sobra: Boolean = false,
    /** Endereço do cliente (bairro; cidade/UF), para a lista mostrar sem abrir. */
    val clienteBairro: String? = null,
    val clienteCidade: String? = null,
)

/** Pallet que voltou da baixa sem o endereço de antes: a posição já é de outro. */
data class PalletSemEnderecoDevolucao(val palletCodigo: String, val endereco: String, val ocupante: String)

/** O que a devolução recuperou ao ter a baixa estornada. */
data class EstornoBaixa(
    val numero: String,
    val status: String,
    val caixas: Int,
    val palletsEnderecados: List<String>,
    val palletsSemEndereco: List<PalletSemEnderecoDevolucao>,
    /** Caixas que não voltam: saíram antes, uma a uma, pela baixa por caixa da sobra. */
    val caixasBaixadasPorCaixa: Int,
)

/** O que saiu numa baixa de sobra, e o que ficou no estoque. */
data class BaixaSobra(val caixas: Int, val qtdUn: Int, val restanteUn: Int, val itens: List<ItemDevolucaoResumo>)

/** Um motivo do cadastro de motivos de devolução. */
data class MotivoDevolucao(val id: Long, val descricao: String, val ativo: Boolean)

/** O que saiu junto ao apagar uma devolução. */
data class DevolucaoApagada(
    val numero: String,
    val leiturasApagadas: Int,
    val palletsLiberados: List<String>,
)

/** Uma leitura da conferência: a caixa bipada, ou as unidades soltas digitadas. */
data class LeituraDevolucaoResumo(
    val id: Long,
    val itemId: Long,
    @get:JsonProperty("nItem") val nItem: Int,
    val produtoCodigo: String?,
    val produtoDescricao: String?,
    /** BIPE (caixa fechada) ou DIGITADO (unidades soltas). */
    val modo: String,
    val qtdUn: Int,
    /** Nulo: conferida sem pallet — não aparece no estoque até ganhar um. */
    val palletCodigo: String?,
    val usuario: String?,
    val lidoEm: String?,
)

/** O que mudou ao excluir caixas ou trocá-las de pallet. */
data class CaixasAlteradas(
    val caixas: Int,
    val palletsLiberados: List<String>,
    val itens: List<ItemDevolucaoResumo>,
)

data class VinculoItem(
    @param:JsonProperty("nItem") @get:JsonProperty("nItem") val nItem: Int = 0,
    val produtoId: Long = 0,
    /** O que o operador informou; nulo = a quantidade do XML. */
    val quantidade: Int? = null,
    /** CX ou UN; nulo = caixas. */
    val unidade: String? = null,
    /** Motivo do item, quando difere do da NF. */
    val motivo: String? = null,
)

data class ResultadoConferencia(
    val itemId: Long,
    val qtdConferidaUn: Int,
    val qtdEsperadaUn: Int,
    val completo: Boolean,
)

/**
 * Devolução: o cliente recebeu, abriu a carga e devolve parte dela.
 *
 * Distinta da reentrega, que é pallet fechado que nunca foi recebido. Aqui a
 * granularidade é item e quantidade, e a mercadoria vem com NF própria emitida
 * pelo cliente. Ver docs/MODELO-DE-DADOS.md §9A.
 */
class DevolucaoServico(private val db: Db) {

    /**
     * Lê o XML do cliente e sugere o de-para.
     *
     * O `cProd` é do sistema do cliente, mas o `cEAN` é GTIN — código global.
     * O mesmo GTIN-14 da caixa aparece nos dois XMLs, então o vínculo sai
     * sozinho na maioria das linhas e o operador só confirma.
     */
    fun previa(bytes: ByteArray, notaOrigemId: Long?): PreviaDevolucao =
        previa(bytes, notaOrigemId?.let { OrigemDevolucao(notaId = it) })

    fun previa(bytes: ByteArray, origemPedida: OrigemDevolucao?): PreviaDevolucao = db.leitura { c ->
        val nota = try {
            NfeParser.parse(bytes)
        } catch (e: NfeInvalidoException) {
            throw Erros.invalido(e.codigo, e.message.orEmpty())
        }

        val avisos = mutableListOf<String>()
        c.buscarUm("SELECT numero FROM nota_devolucao WHERE chave = ?", nota.chave) { it.getString(1) }
            ?.let { throw Erros.conflito("DEVOLUCAO_JA_IMPORTADA", "NF de devolução $it já foi importada") }

        val resolvida = resolverOrigem(c, origemPedida)
        resolvida?.let { avisos += avisosDaOrigem(c, it, clienteDaDevolucao(nota).cnpj) }
        val origem = resolvida?.numero

        val itens = nota.itens.map { item ->
            val gtin = when (val r = Gtin.normalizar(item.cEan)) {
                is ResultadoGtin.Ok -> r.gtin14
                is ResultadoGtin.Invalido -> null
            }
            val sugestao = gtin?.let { buscarPorGtin(c, it) }
            if (sugestao == null) {
                avisos += "Item ${item.nItem} (${item.cProd}) sem GTIN reconhecido — vincular à mão"
            }
            val caixas = item.qCom.toBigInteger().toInt()
            val unidade = unidadeDoXml(item.uCom)
            ItemPreviaDevolucao(
                nItem = item.nItem,
                cprodCliente = item.cProd,
                xprodCliente = item.xProd,
                ceanCliente = item.cEan,
                qtdCaixas = caixas,
                qtdUn = if (unidade == "UN") caixas else sugestao?.unidadesPorCaixa?.let { it * caixas },
                sugestao = sugestao,
                unidadeXml = item.uCom,
                unidadeSugerida = unidade,
            )
        }

        PreviaDevolucao(
            chave = nota.chave,
            numero = nota.numero,
            serie = nota.serie,
            emissao = nota.emissao,
            emitenteNome = clienteDaDevolucao(nota).nome,
            emitenteCnpj = clienteDaDevolucao(nota).cnpj,
            notaOrigemId = resolvida?.notaId,
            notaOrigemNumero = origem,
            itens = itens,
            avisos = avisos,
            origemTipo = resolvida?.tipo,
            origemChave = resolvida?.chave,
            origemCliente = resolvida?.cliente,
            referencia = nota.referencias.firstOrNull()?.let { referenciaDe(c, it) },
        )
    }

    /** Grava a devolução com o de-para confirmado e manda para conferência. */
    fun importar(
        usuarioId: Long,
        bytes: ByteArray,
        notaOrigemId: Long,
        vinculos: List<VinculoItem>,
    ): DevolucaoResumo = importar(usuarioId, bytes, OrigemDevolucao(notaId = notaOrigemId), vinculos)

    /**
     * A mesma NF de venda pode ter mais de uma devolução — o cliente devolve
     * uma parte hoje e outra depois, cada uma com a sua NF. A prévia avisa;
     * não impede.
     */
    fun importar(
        usuarioId: Long,
        bytes: ByteArray,
        origemPedida: OrigemDevolucao,
        vinculos: List<VinculoItem>,
        motivo: String? = null,
    ): DevolucaoResumo = db.transacao { c ->
        val motivoNota = motivo?.trim()?.takeIf { it.isNotEmpty() }
        val nota = try {
            NfeParser.parse(bytes)
        } catch (e: NfeInvalidoException) {
            throw Erros.invalido(e.codigo, e.message.orEmpty())
        }

        c.buscarUm("SELECT numero FROM nota_devolucao WHERE chave = ?", nota.chave) { it.getString(1) }
            ?.let { throw Erros.conflito("DEVOLUCAO_JA_IMPORTADA", "NF de devolução $it já foi importada") }

        val origem = resolverOrigem(c, origemPedida) ?: throw Erros.invalido(
            "NOTA_ORIGEM_OBRIGATORIA",
            "Informe a NF de origem: escolha a NF do sistema, envie o XML dela ou digite o número",
        )

        val porItem = vinculos.associateBy { it.nItem }
        val semVinculo = nota.itens.filter { porItem[it.nItem] == null }
        if (semVinculo.isNotEmpty()) {
            throw Erros.invalido(
                "ITENS_SEM_VINCULO",
                "Vincule todos os itens antes de lançar: " +
                    semVinculo.joinToString { "item ${it.nItem}" },
            )
        }

        val agora = Instant.now().toString()
        val cliente = clienteDaDevolucao(nota)
        val devolucaoId = c.inserir(
            """INSERT INTO nota_devolucao
               (chave, numero, serie, emissao, emitente_cnpj, emitente_nome,
                nota_fiscal_origem_id, status, xml_original, importado_em, importado_por,
                origem_tipo, origem_numero, origem_serie, origem_chave, origem_emissao, origem_xml, motivo,
                cliente_nome, cliente_cnpj, cliente_bairro, cliente_cidade)
               VALUES (?,?,?,?,?,?,?, 'AGUARDANDO_CONFERENCIA', ?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            nota.chave, nota.numero, nota.serie, nota.emissao,
            nota.emitCnpj, nota.emitNome.orEmpty(), origem.notaId, bytes, agora, usuarioId,
            origem.tipo, origem.numero, origem.serie, origem.chave, origem.emissao, origem.xml, motivoNota,
            cliente.nome, cliente.cnpj, cliente.bairro, cliente.cidade,
        )

        nota.itens.forEach { item ->
            val vinculo = porItem.getValue(item.nItem)
            val produtoId = vinculo.produtoId
            // O operador pode corrigir a quantidade e dizer se ela está em
            // caixas ou em unidades: a NF do cliente nem sempre lança na
            // unidade certa. Sem isso, vale a quantidade do XML, em caixas.
            val unidade = (vinculo.unidade ?: "CX").trim().uppercase()
            if (unidade !in setOf("CX", "UN")) {
                throw Erros.invalido(
                    "UNIDADE_INVALIDA", "Item ${item.nItem}: a unidade deve ser caixas (CX) ou unidades (UN)",
                )
            }
            val quantidade = vinculo.quantidade ?: item.qCom.toBigInteger().toInt()
            if (quantidade <= 0) {
                throw Erros.invalido("QUANTIDADE_INVALIDA", "Item ${item.nItem}: informe uma quantidade maior que zero")
            }

            // Tudo é guardado na menor unidade: a devolução pode vir de uma
            // lata avulsa, e caixa não divide. Ver docs/MODELO-DE-DADOS.md §9A.
            // Em unidades o fator de caixa não entra — nem precisa existir.
            val esperadaUn = if (unidade == "UN") quantidade else {
                val unPorCaixa = c.buscarUm(
                    "SELECT unidades_por_caixa FROM produto WHERE id = ?", produtoId,
                ) { it.getInt(1).takeIf { _ -> !it.wasNull() } }
                    ?: throw Erros.conflito(
                        "UNIDADES_POR_CAIXA_NAO_CADASTRADA",
                        "O produto do item ${item.nItem} está sem unidades por caixa. " +
                            "Cadastre em Produtos ou lance o item em unidades.",
                    )
                quantidade * unPorCaixa
            }

            c.inserir(
                """INSERT INTO item_devolucao
                   (nota_devolucao_id, n_item, cprod_cliente, xprod_cliente, cean_cliente,
                    produto_id, qtd_esperada_un, qtd_conferida_un, status, motivo)
                   VALUES (?,?,?,?,?,?,?,0,'ABERTO',?)""",
                devolucaoId, item.nItem, item.cProd, item.xProd, item.cEan,
                produtoId, esperadaUn,
                // Igual ao da NF não se grava: o item segue a NF.
                vinculo.motivo?.trim()?.takeIf { it.isNotEmpty() && it != motivoNota },
            )
        }

        buscarResumo(c, devolucaoId)!!
    }

    fun listar(status: List<String>? = null): List<DevolucaoResumo> = db.leitura { c ->
        // A situação vem da tela: entra por parâmetro, nunca colada no SQL.
        val filtros = status?.filter { it.isNotEmpty() }.orEmpty()
        val filtro = if (filtros.isEmpty()) "" else "WHERE d.status IN (${filtros.joinToString(",") { "?" }})"
        comMotivos(c, c.buscarTodos(sqlResumo(filtro), *filtros.toTypedArray()) { mapaResumo(it) })
    }

    fun itens(devolucaoId: Long): List<ItemDevolucaoResumo> = db.leitura { c -> itensDe(c, devolucaoId) }

    private fun itensDe(c: Connection, devolucaoId: Long): List<ItemDevolucaoResumo> = c.buscarTodos(
        """SELECT i.id, p.codigo, p.descricao, i.cprod_cliente, i.xprod_cliente,
                  p.unidades_por_caixa, i.qtd_esperada_un, i.qtd_conferida_un,
                  i.status, i.divergencia_justificativa, i.n_item,
                  (SELECT o.n_item FROM item_devolucao o WHERE o.id = i.troca_de_item_id),
                  i.troca_justificativa, u.nome, i.troca_em, i.motivo
             FROM item_devolucao i
             LEFT JOIN produto p ON p.id = i.produto_id
             LEFT JOIN usuario u ON u.id = i.troca_por
            WHERE i.nota_devolucao_id = ? ORDER BY i.n_item""",
        devolucaoId,
    ) {
        ItemDevolucaoResumo(
            it.getLong(1), it.getString(2), it.getString(3), it.getString(4), it.getString(5),
            it.getInt(6).takeIf { _ -> !it.wasNull() },
            it.getInt(7), it.getInt(8), it.getString(9), it.getString(10),
            nItem = it.getInt(11),
            trocaDeItem = it.getInt(12).takeIf { _ -> !it.wasNull() },
            trocaJustificativa = it.getString(13),
            trocadoPor = it.getString(14),
            trocadoEm = it.getString(15),
            motivo = it.getString(16),
        )
    }

    // ---------------------------------------------------------- produto trocado

    /**
     * Produto trocado: o cliente declarou um produto na NF e mandou outro. O que
     * veio vira um item novo, ligado ao declarado, e é conferido no coletor como
     * qualquer outro. Com [itemTodo], nada do declarado veio: o item dele fecha
     * com a justificativa da troca. Sem, ele segue aberto para conferir a parte
     * dele que veio.
     */
    fun trocarProduto(
        usuarioId: Long,
        itemId: Long,
        produtoId: Long,
        quantidade: Int,
        unidade: String,
        itemTodo: Boolean,
        justificativa: String,
    ): List<ItemDevolucaoResumo> = db.transacao { c ->
        val just = justificativa.trim().ifEmpty {
            throw Erros.invalido("JUSTIFICATIVA_OBRIGATORIA", "Informe a justificativa da troca")
        }
        data class Item(
            val devolucaoId: Long, val nItem: Int, val produtoId: Long?, val cprod: String?, val xprod: String?,
            val conferida: Int, val trocaDe: Long?, val notaStatus: String,
        )
        val i = c.buscarUm(
            """SELECT i.nota_devolucao_id, i.n_item, i.produto_id, i.cprod_cliente, i.xprod_cliente,
                      i.qtd_conferida_un, i.troca_de_item_id, d.status
                 FROM item_devolucao i JOIN nota_devolucao d ON d.id = i.nota_devolucao_id
                WHERE i.id = ?""",
            itemId,
        ) {
            Item(
                it.getLong(1), it.getInt(2), it.getLong(3).takeIf { _ -> !it.wasNull() }, it.getString(4),
                it.getString(5), it.getInt(6), it.getLong(7).takeIf { _ -> !it.wasNull() }, it.getString(8),
            )
        } ?: throw Erros.naoEncontrado("Item de devolução")

        if (i.notaStatus == "BAIXADA") {
            throw Erros.conflito("DEVOLUCAO_BAIXADA", "A devolução já foi baixada: não dá para registrar troca")
        }
        if (i.trocaDe != null) {
            throw Erros.conflito("ITEM_E_TROCA", "Este item já é a troca de outro: registre a troca no item da NF")
        }
        if (i.produtoId == produtoId) {
            throw Erros.invalido("MESMO_PRODUTO", "O produto que veio é o mesmo do item da NF")
        }
        val u = unidade.trim().uppercase()
        if (u !in setOf("CX", "UN")) {
            throw Erros.invalido("UNIDADE_INVALIDA", "A unidade deve ser caixas (CX) ou unidades (UN)")
        }
        if (quantidade <= 0) throw Erros.invalido("QUANTIDADE_INVALIDA", "Informe uma quantidade maior que zero")
        if (itemTodo && i.conferida > 0) {
            throw Erros.conflito(
                "ITEM_JA_CONFERIDO",
                "O item ${i.nItem} já tem ${i.conferida} un conferidas do produto da NF: registre a troca só da " +
                    "parte que veio trocada (desmarque \"veio só o produto trocado\")",
            )
        }
        data class Produto(val codigo: String, val descricao: String, val unPorCaixa: Int?)
        val p = c.buscarUm("SELECT codigo, descricao, unidades_por_caixa FROM produto WHERE id = ?", produtoId) {
            Produto(it.getString(1), it.getString(2), it.getInt(3).takeIf { _ -> !it.wasNull() })
        } ?: throw Erros.naoEncontrado("Produto")
        // Em unidades o fator de caixa não entra, como no lançamento.
        val esperadaUn = if (u == "UN") quantidade else quantidade * (
            p.unPorCaixa ?: throw Erros.conflito(
                "UNIDADES_POR_CAIXA_NAO_CADASTRADA",
                "${p.codigo} está sem unidades por caixa. Cadastre em Produtos ou informe em unidades.",
            )
            )

        val agora = Instant.now().toString()
        val nItemNovo = (c.buscarUm(
            "SELECT MAX(n_item) FROM item_devolucao WHERE nota_devolucao_id = ?", i.devolucaoId,
        ) { it.getInt(1) } ?: 0) + 1
        c.inserir(
            """INSERT INTO item_devolucao
               (nota_devolucao_id, n_item, cprod_cliente, xprod_cliente, cean_cliente, produto_id,
                qtd_esperada_un, qtd_conferida_un, status,
                troca_de_item_id, troca_justificativa, troca_em, troca_por)
               VALUES (?,?,?,?,NULL,?,?,0,'ABERTO',?,?,?,?)""",
            i.devolucaoId, nItemNovo, i.cprod, "NO LUGAR DO ITEM ${i.nItem}: ${i.xprod.orEmpty()}".trim(),
            produtoId, esperadaUn, itemId, just, agora, usuarioId,
        )
        if (itemTodo) {
            // Item já fechado (ex.: "não veio") guarda o fechamento dele: desfazer
            // a troca depois não o reabre.
            c.atualizar(
                """UPDATE item_devolucao SET status = 'FECHADO_DIVERGENCIA', divergencia_justificativa = ?
                    WHERE id = ? AND status NOT IN ('FECHADO','FECHADO_DIVERGENCIA')""",
                "Produto trocado: veio ${p.codigo} ${p.descricao} no lugar (item $nItemNovo). $just", itemId,
            )
        }
        // Item novo, aberto: a devolução volta a ter o que conferir.
        c.atualizar(
            "UPDATE nota_devolucao SET status = 'EM_CONFERENCIA' WHERE id = ? AND status = 'CONFERIDA'",
            i.devolucaoId,
        )
        auditarDevolucao(
            c, i.devolucaoId, "DEVOLUCAO_PRODUTO_TROCADO", usuarioId,
            "Item ${i.nItem} (${i.xprod.orEmpty()}): veio ${p.codigo} ${p.descricao}, $quantidade $u" +
                (if (itemTodo) ", no lugar do item todo" else ", parte do item") + " — item $nItemNovo: $just",
        )
        itensDe(c, i.devolucaoId)
    }

    /**
     * Troca registrada por engano: o item do produto trocado sai, e o item da NF
     * que ela tinha fechado volta a esperar. Só enquanto o trocado não tem
     * conferência — depois disso a caixa bipada existe.
     */
    fun desfazerTroca(usuarioId: Long, itemId: Long): List<ItemDevolucaoResumo> = db.transacao { c ->
        data class Troca(val devolucaoId: Long, val nItem: Int, val trocaDe: Long?, val notaStatus: String)
        val t = c.buscarUm(
            """SELECT i.nota_devolucao_id, i.n_item, i.troca_de_item_id, d.status
                 FROM item_devolucao i JOIN nota_devolucao d ON d.id = i.nota_devolucao_id
                WHERE i.id = ?""",
            itemId,
        ) { Troca(it.getLong(1), it.getInt(2), it.getLong(3).takeIf { _ -> !it.wasNull() }, it.getString(4)) }
            ?: throw Erros.naoEncontrado("Item de devolução")
        val original = t.trocaDe ?: throw Erros.conflito("ITEM_NAO_E_TROCA", "Este item não é de produto trocado")
        if (t.notaStatus == "BAIXADA") {
            throw Erros.conflito("DEVOLUCAO_BAIXADA", "A devolução já foi baixada: não dá para desfazer a troca")
        }
        val leituras = c.buscarUm(
            "SELECT COUNT(*) FROM leitura_devolucao WHERE item_devolucao_id = ? AND cancelada = 0", itemId,
        ) { it.getInt(1) } ?: 0
        if (leituras > 0) {
            throw Erros.conflito(
                "TROCA_COM_CONFERENCIA",
                "O produto trocado já tem $leituras leitura(s) de conferência: não dá para desfazer a troca",
            )
        }

        // Leitura excluída ainda aponta o item e travaria o DELETE pela FK:
        // sai junto — o log da exclusão continua contando o que houve.
        val excluidas = c.atualizar(
            "DELETE FROM leitura_devolucao WHERE item_devolucao_id = ? AND cancelada = 1", itemId,
        )
        c.atualizar("DELETE FROM item_devolucao WHERE id = ?", itemId)
        // O item da NF volta a esperar, se foi esta troca que o fechou.
        c.atualizar(
            """UPDATE item_devolucao
                  SET status = CASE WHEN qtd_conferida_un > 0 THEN 'EM_CONFERENCIA' ELSE 'ABERTO' END,
                      divergencia_justificativa = NULL
                WHERE id = ? AND status = 'FECHADO_DIVERGENCIA'
                  AND divergencia_justificativa LIKE ?""",
            original, "Produto trocado:%(item ${t.nItem})%",
        )
        // Item reaberto numa NF conferida a devolve a em conferência — sem
        // isso a baixa passaria com item em aberto.
        reavaliarNota(c, t.devolucaoId)
        auditarDevolucao(
            c, t.devolucaoId, "DEVOLUCAO_TROCA_DESFEITA", usuarioId,
            "Item ${t.nItem} saiu da devolução" + (if (excluidas > 0) " (com $excluidas leitura(s) já excluída(s))" else ""),
        )
        itensDe(c, t.devolucaoId)
    }

    private fun auditarDevolucao(c: Connection, devolucaoId: Long, acao: String, usuarioId: Long, detalhe: String) {
        c.inserir(
            """INSERT INTO log_auditoria (entidade, entidade_id, acao, usuario_id, em, detalhe_json)
               VALUES ('nota_devolucao', ?, ?, ?, ?, ?)""",
            devolucaoId, acao, usuarioId, Instant.now().toString(), detalhe,
        )
    }

    // ------------------------------------------------- conferência de estoque

    /**
     * Abre uma conferência de estoque. A sobra — mercadoria que está aqui sem
     * NF de venda nem de devolução — entra como uma devolução sem NF: mesmo
     * pallet P-DEV, mesma conferência no coletor, mesma baixa e mesma planilha.
     */
    fun criarSobra(usuarioId: Long): DevolucaoResumo = db.transacao { c ->
        val numero = Sequencias.proximo(c, Sequencias.SOBRA)
        val agora = Instant.now().toString()
        val id = c.inserir(
            """INSERT INTO nota_devolucao
               (chave, numero, serie, emissao, emitente_nome, status, importado_em, importado_por,
                cliente_nome, sobra)
               VALUES (?,?, '0', ?, 'SOBRA DE ESTOQUE', 'AGUARDANDO_CONFERENCIA', ?, ?, 'SOBRA DE ESTOQUE', 1)""",
            numero, numero, agora, agora, usuarioId,
        )
        auditarDevolucao(c, id, "SOBRA_ABERTA", usuarioId, "Conferência de estoque $numero aberta")
        buscarResumo(c, id)!!
    }

    /**
     * Uma caixa de sobra: o coletor bipa qualquer produto e ela entra no
     * pallet. [codigoLido] vem do bipe; [itemId] vem do "digitar quantidade",
     * quando são várias caixas do mesmo produto.
     */
    fun biparSobra(
        usuarioId: Long,
        devolucaoId: Long,
        idempotencyKey: String,
        codigoLido: String?,
        itemId: Long?,
        caixas: Int,
        palletCodigo: String?,
    ): List<ItemDevolucaoResumo> = db.transacao { c ->
        // Reenvio da mesma leitura não conta outra caixa.
        c.buscarUm("SELECT 1 FROM leitura_devolucao WHERE idempotency_key = ?", idempotencyKey) { 1 }
            ?.let { return@transacao itensDe(c, devolucaoId) }

        data class Nota(val numero: String, val status: String, val sobra: Boolean)
        val d = c.buscarUm("SELECT numero, status, sobra FROM nota_devolucao WHERE id = ?", devolucaoId) {
            Nota(it.getString(1), it.getString(2), it.getInt(3) == 1)
        } ?: throw Erros.naoEncontrado("Conferência de estoque")
        if (!d.sobra) {
            throw Erros.conflito("NAO_E_SOBRA", "A NF ${d.numero} é devolução: a caixa entra pelo item dela")
        }
        if (d.status == "BAIXADA") {
            throw Erros.conflito("DEVOLUCAO_BAIXADA", "A conferência ${d.numero} já foi baixada")
        }
        if (caixas !in 1..999) throw Erros.invalido("QUANTIDADE_INVALIDA", "Informe de 1 a 999 caixas")
        val cod = palletCodigo?.trim()?.takeIf { it.isNotEmpty() } ?: throw Erros.conflito(
            "PALLET_OBRIGATORIO", "Bipe a etiqueta do pallet de devolução (P-DEV-) antes das caixas",
        )

        data class Produto(val id: Long, val codigo: String, val unPorCaixa: Int?)
        val p = (
            if (itemId != null) {
                c.buscarUm(
                    """SELECT p.id, p.codigo, p.unidades_por_caixa FROM item_devolucao i
                         JOIN produto p ON p.id = i.produto_id
                        WHERE i.id = ? AND i.nota_devolucao_id = ?""",
                    itemId, devolucaoId,
                ) { Produto(it.getLong(1), it.getString(2), it.getInt(3).takeIf { _ -> !it.wasNull() }) }
                    ?: throw Erros.naoEncontrado("Item da conferência")
            } else {
                val gtin = (Gtin.normalizar(codigoLido) as? ResultadoGtin.Ok)?.gtin14
                gtin?.let {
                    c.buscarUm(
                        """SELECT p.id, p.codigo, p.unidades_por_caixa
                             FROM produto_codigo_barras b JOIN produto p ON p.id = b.produto_id
                            WHERE b.codigo = ? AND b.ativo = 1""",
                        it,
                    ) { r -> Produto(r.getLong(1), r.getString(2), r.getInt(3).takeIf { _ -> !r.wasNull() }) }
                } ?: throw Erros.conflito(
                    "CODIGO_NAO_CADASTRADO", "Código não cadastrado",
                    mapOf("codigoLido" to codigoLido),
                )
            }
            )
        val unPorCaixa = p.unPorCaixa?.takeIf { it > 0 } ?: throw Erros.conflito(
            "UNIDADES_POR_CAIXA_NAO_CADASTRADA",
            "${p.codigo} está sem unidades por caixa. Cadastre em Produtos antes de bipar.",
        )
        val palletId = amarrarPallet(c, cod, devolucaoId, usuarioId)

        // Um item por produto: o segundo bipe do mesmo produto soma no mesmo.
        val item = c.buscarUm(
            "SELECT id FROM item_devolucao WHERE nota_devolucao_id = ? AND produto_id = ?", devolucaoId, p.id,
        ) { it.getLong(1) } ?: run {
            val n = (
                c.buscarUm("SELECT MAX(n_item) FROM item_devolucao WHERE nota_devolucao_id = ?", devolucaoId) {
                    it.getInt(1)
                } ?: 0
                ) + 1
            c.inserir(
                """INSERT INTO item_devolucao
                   (nota_devolucao_id, n_item, cprod_cliente, xprod_cliente, produto_id,
                    qtd_esperada_un, qtd_conferida_un, status)
                   VALUES (?,?,?, 'Sobra de estoque', ?, 0, 0, 'EM_CONFERENCIA')""",
                devolucaoId, n, p.codigo, p.id,
            )
        }
        c.inserir(
            """INSERT INTO leitura_devolucao
               (idempotency_key, pallet_id, item_devolucao_id, produto_id, codigo_lido, modo, qtd_un,
                usuario_id, lido_em, registrado_em)
               VALUES (?,?,?,?,?,?,?,?,NULL,?)""",
            idempotencyKey, palletId, item, p.id, codigoLido,
            if (itemId == null) "BIPE" else "DIGITADO", unPorCaixa * caixas, usuarioId, Instant.now().toString(),
        )
        recontarItens(c, listOf(item))
        // Caixa nova reabre a conferência já encerrada: na sobra sempre pode
        // aparecer mais uma caixa, até a baixa.
        c.atualizar(
            """UPDATE nota_devolucao SET status = 'EM_CONFERENCIA'
                WHERE id = ? AND status IN ('AGUARDANDO_CONFERENCIA','CONFERIDA')""",
            devolucaoId,
        )
        itensDe(c, devolucaoId)
    }

    /**
     * Baixa de parte da sobra: N caixas de um produto saem (descarte ou
     * revenda) e o resto fica no estoque. A caixa que sai não é apagada —
     * fica marcada como baixada, ligada à baixa. Saindo a última, a
     * conferência fecha e os pallets vazios saem do estoque.
     */
    fun baixarSobraParcial(
        usuarioId: Long,
        itemId: Long,
        caixas: Int,
        tipo: String,
        observacao: String?,
        nfRevenda: String?,
    ): BaixaSobra = db.transacao { c ->
        if (tipo !in setOf("DESCARTE", "REVENDA")) {
            throw Erros.invalido("TIPO_INVALIDO", "Tipo deve ser DESCARTE ou REVENDA")
        }
        if (caixas <= 0) throw Erros.invalido("QUANTIDADE_INVALIDA", "Informe quantas caixas saem")

        data class Item(
            val devolucaoId: Long, val nItem: Int, val produto: String?, val unPorCaixa: Int?,
            val conferida: Int, val notaNumero: String, val notaStatus: String, val sobra: Boolean,
        )
        val i = c.buscarUm(
            """SELECT i.nota_devolucao_id, i.n_item, p.codigo, p.unidades_por_caixa, i.qtd_conferida_un,
                      d.numero, d.status, d.sobra
                 FROM item_devolucao i
                 JOIN nota_devolucao d ON d.id = i.nota_devolucao_id
                 LEFT JOIN produto p ON p.id = i.produto_id
                WHERE i.id = ?""",
            itemId,
        ) {
            Item(
                it.getLong(1), it.getInt(2), it.getString(3), it.getInt(4).takeIf { _ -> !it.wasNull() },
                it.getInt(5), it.getString(6), it.getString(7), it.getInt(8) == 1,
            )
        } ?: throw Erros.naoEncontrado("Item da conferência")
        if (!i.sobra) {
            throw Erros.conflito("NAO_E_SOBRA", "Baixa por caixa é só da conferência de estoque; a devolução baixa inteira")
        }
        if (i.notaStatus == "BAIXADA") throw Erros.conflito("DEVOLUCAO_BAIXADA", "A conferência ${i.notaNumero} já foi baixada")
        val unPorCaixa = i.unPorCaixa?.takeIf { it > 0 } ?: throw Erros.conflito(
            "UNIDADES_POR_CAIXA_NAO_CADASTRADA", "${i.produto.orEmpty()} está sem unidades por caixa",
        )
        val pedido = caixas * unPorCaixa
        if (pedido > i.conferida) {
            throw Erros.conflito(
                "QUANTIDADE_MAIOR_QUE_O_ESTOQUE",
                "O item ${i.nItem} tem ${i.conferida / unPorCaixa} caixa(s) no estoque",
                mapOf("caixasEmEstoque" to i.conferida / unPorCaixa),
            )
        }

        val agora = Instant.now().toString()
        val baixaId = c.inserir(
            """INSERT INTO baixa_sobra
               (nota_devolucao_id, item_devolucao_id, qtd_un, tipo, observacao, nf_revenda, baixado_em, baixado_por)
               VALUES (?,?,?,?,?,?,?,?)""",
            i.devolucaoId, itemId, pedido, tipo, observacao, nfRevenda, agora, usuarioId,
        )

        // As caixas saem das leituras, da mais antiga para a mais nova. A
        // leitura maior que o pedido se divide: parte sai, parte fica.
        data class Leitura(val id: Long, val qtdUn: Int, val palletId: Long?, val produtoId: Long, val modo: String)
        var restante = pedido
        c.buscarTodos(
            """SELECT id, qtd_un, pallet_id, produto_id, modo FROM leitura_devolucao
                WHERE item_devolucao_id = ? AND cancelada = 0 ORDER BY id""",
            itemId,
        ) {
            Leitura(it.getLong(1), it.getInt(2), it.getLong(3).takeIf { _ -> !it.wasNull() }, it.getLong(4), it.getString(5))
        }.forEach { l ->
            if (restante <= 0) return@forEach
            if (l.qtdUn <= restante) {
                c.atualizar(
                    """UPDATE leitura_devolucao SET cancelada = 3, cancelada_em = ?, cancelada_por = ?,
                              cancelamento_motivo = ?, baixa_sobra_id = ?
                        WHERE id = ?""",
                    agora, usuarioId, "Baixa da sobra por $tipo", baixaId, l.id,
                )
                restante -= l.qtdUn
            } else {
                c.atualizar("UPDATE leitura_devolucao SET qtd_un = qtd_un - ? WHERE id = ?", restante, l.id)
                c.inserir(
                    """INSERT INTO leitura_devolucao
                       (idempotency_key, pallet_id, item_devolucao_id, produto_id, codigo_lido, modo, qtd_un,
                        usuario_id, lido_em, registrado_em, cancelada, cancelada_em, cancelada_por,
                        cancelamento_motivo, baixa_sobra_id)
                       VALUES (?,?,?,?,NULL,?,?,?,NULL,?,3,?,?,?,?)""",
                    java.util.UUID.randomUUID().toString(), l.palletId, itemId, l.produtoId, l.modo, restante,
                    usuarioId, agora, agora, usuarioId, "Baixa da sobra por $tipo", baixaId,
                )
                restante = 0
            }
        }
        recontarItens(c, listOf(itemId))

        // Pallet que ficou sem caixa saiu do estoque junto com a mercadoria.
        c.buscarTodos(
            """SELECT p.id FROM pallet p
                WHERE p.nota_devolucao_id = ? AND p.status IN ('EM_USO','ENCERRADO','ENDERECADO')
                  AND NOT EXISTS (SELECT 1 FROM leitura_devolucao l WHERE l.pallet_id = p.id AND l.cancelada = 0)""",
            i.devolucaoId,
        ) { it.getLong(1) }.forEach { palletId ->
            c.atualizar(
                """UPDATE enderecamento SET liberado_em=?, liberado_por=?, motivo_liberacao='EXPEDICAO'
                    WHERE liberado_em IS NULL AND pallet_id = ?""",
                agora, usuarioId, palletId,
            )
            c.atualizar("UPDATE pallet SET status='EXPEDIDO' WHERE id = ?", palletId)
        }

        val resta = c.buscarUm(
            """SELECT COALESCE(SUM(l.qtd_un), 0) FROM leitura_devolucao l
                 JOIN item_devolucao i ON i.id = l.item_devolucao_id
                WHERE i.nota_devolucao_id = ? AND l.cancelada = 0""",
            i.devolucaoId,
        ) { it.getInt(1) } ?: 0
        if (resta == 0) {
            // Saiu a última caixa: a conferência fecha, como a devolução baixada.
            c.atualizar(
                """INSERT OR IGNORE INTO baixa_devolucao
                   (nota_devolucao_id, tipo, observacao, nf_revenda_referencia, baixado_em, baixado_por)
                   VALUES (?,?,?,?,?,?)""",
                i.devolucaoId, tipo, observacao ?: "Sobra baixada em partes", nfRevenda, agora, usuarioId,
            )
            c.atualizar(
                """UPDATE item_devolucao SET status = 'FECHADO'
                    WHERE nota_devolucao_id = ? AND status NOT IN ('FECHADO','FECHADO_DIVERGENCIA')""",
                i.devolucaoId,
            )
            c.atualizar("UPDATE nota_devolucao SET status = 'BAIXADA' WHERE id = ?", i.devolucaoId)
        }
        auditarDevolucao(
            c, i.devolucaoId, "SOBRA_BAIXADA", usuarioId,
            "Item ${i.nItem} ${i.produto.orEmpty()}: $caixas caixa(s) por $tipo" +
                (if (resta == 0) "; saiu a última caixa, conferência baixada" else ""),
        )
        BaixaSobra(caixas, pedido, resta, itensDe(c, i.devolucaoId))
    }

    // ---------------------------------------------------- cadastro de motivos

    /** Os motivos para escolher; [todos] traz também os desativados (tela do cadastro). */
    fun motivos(todos: Boolean = false): List<MotivoDevolucao> = db.leitura { c ->
        c.buscarTodos(
            "SELECT id, descricao, ativo FROM motivo_devolucao" + (if (todos) "" else " WHERE ativo = 1") +
                " ORDER BY descricao COLLATE NOCASE",
        ) { MotivoDevolucao(it.getLong(1), it.getString(2), it.getInt(3) == 1) }
    }

    /** Motivo novo no cadastro. O que já existe desativado volta a valer. */
    fun criarMotivo(usuarioId: Long, descricao: String): MotivoDevolucao = db.transacao { c ->
        val d = descricaoDoMotivo(descricao)
        c.buscarUm("SELECT id, descricao, ativo FROM motivo_devolucao WHERE descricao = ? COLLATE NOCASE", d) {
            MotivoDevolucao(it.getLong(1), it.getString(2), it.getInt(3) == 1)
        }?.let { existente ->
            if (existente.ativo) throw Erros.conflito("MOTIVO_DUPLICADO", "O motivo \"${existente.descricao}\" já está cadastrado")
            c.atualizar("UPDATE motivo_devolucao SET ativo = 1 WHERE id = ?", existente.id)
            return@transacao existente.copy(ativo = true)
        }
        val id = c.inserir(
            "INSERT INTO motivo_devolucao (descricao, ativo, criado_em, criado_por) VALUES (?, 1, ?, ?)",
            d, Instant.now().toString(), usuarioId,
        )
        MotivoDevolucao(id, d, true)
    }

    /**
     * Renomeia ou desativa. Desativar tira da lista de escolha; o que já foi
     * gravado nas devoluções fica como está.
     */
    fun atualizarMotivo(id: Long, descricao: String, ativo: Boolean): MotivoDevolucao = db.transacao { c ->
        val d = descricaoDoMotivo(descricao)
        c.buscarUm("SELECT 1 FROM motivo_devolucao WHERE id = ?", id) { 1 } ?: throw Erros.naoEncontrado("Motivo")
        c.buscarUm("SELECT id FROM motivo_devolucao WHERE descricao = ? COLLATE NOCASE AND id <> ?", d, id) { it.getLong(1) }
            ?.let { throw Erros.conflito("MOTIVO_DUPLICADO", "Já existe o motivo \"$d\"") }
        c.atualizar("UPDATE motivo_devolucao SET descricao = ?, ativo = ? WHERE id = ?", d, if (ativo) 1 else 0, id)
        MotivoDevolucao(id, d, ativo)
    }

    private fun descricaoDoMotivo(bruto: String): String {
        val d = bruto.trim().replace(Regex("\\s+"), " ")
        if (d.isEmpty()) throw Erros.invalido("MOTIVO_OBRIGATORIO", "Escreva o motivo")
        if (d.length > 80) throw Erros.invalido("MOTIVO_LONGO", "O motivo pode ter até 80 letras")
        return d
    }

    // ------------------------------------------------------------------ motivo

    /** Motivo da devolução, na NF: vale para todo item que não tem o seu. */
    fun definirMotivo(usuarioId: Long, devolucaoId: Long, motivo: String?): DevolucaoResumo = db.transacao { c ->
        val m = motivo?.trim()?.takeIf { it.isNotEmpty() }
        val numero = c.buscarUm("SELECT numero FROM nota_devolucao WHERE id = ?", devolucaoId) { it.getString(1) }
            ?: throw Erros.naoEncontrado("Devolução")
        c.atualizar("UPDATE nota_devolucao SET motivo = ? WHERE id = ?", m, devolucaoId)
        auditarDevolucao(c, devolucaoId, "DEVOLUCAO_MOTIVO", usuarioId, "NF $numero: ${m ?: "sem motivo"}")
        buscarResumo(c, devolucaoId)!!
    }

    /** Motivo de um item só. Vazio, ou igual ao da NF, volta a seguir a NF. */
    fun definirMotivoDoItem(usuarioId: Long, itemId: Long, motivo: String?): List<ItemDevolucaoResumo> =
        db.transacao { c ->
            data class Item(val devolucaoId: Long, val nItem: Int, val motivoNota: String?)
            val i = c.buscarUm(
                """SELECT i.nota_devolucao_id, i.n_item, d.motivo
                     FROM item_devolucao i JOIN nota_devolucao d ON d.id = i.nota_devolucao_id WHERE i.id = ?""",
                itemId,
            ) { Item(it.getLong(1), it.getInt(2), it.getString(3)) } ?: throw Erros.naoEncontrado("Item de devolução")
            val m = motivo?.trim()?.takeIf { it.isNotEmpty() && it != i.motivoNota }
            c.atualizar("UPDATE item_devolucao SET motivo = ? WHERE id = ?", m, itemId)
            auditarDevolucao(c, i.devolucaoId, "DEVOLUCAO_MOTIVO_ITEM", usuarioId, "Item ${i.nItem}: ${m ?: "o da NF"}")
            itensDe(c, i.devolucaoId)
        }

    /** A devolução em planilha, um item por linha — para mandar por e-mail. */
    fun planilha(devolucaoId: Long): Pair<String, ByteArray> = db.leitura { c ->
        val d = buscarResumo(c, devolucaoId) ?: throw Erros.naoEncontrado("Devolução")
        "devolucao-NF${d.numero}.xlsx" to
            Planilha.xlsx("Devolucao NF ${d.numero}", CABECALHO_PLANILHA, linhasDaPlanilha(c, d), LARGURAS_PLANILHA)
    }

    /** Todas as devoluções numa planilha só, um item por linha. */
    fun planilhaGeral(): Pair<String, ByteArray> = db.leitura { c ->
        val linhas = c.buscarTodos(sqlResumo("")) { mapaResumo(it) }.flatMap { linhasDaPlanilha(c, it) }
        "devolucoes.xlsx" to Planilha.xlsx("Devolucoes", CABECALHO_PLANILHA, linhas, LARGURAS_PLANILHA)
    }

    private fun linhasDaPlanilha(c: Connection, d: DevolucaoResumo): List<List<String>> {
        val importada = runCatching {
            Instant.parse(d.importadoEm).atZone(java.time.ZoneId.of("America/Sao_Paulo"))
                .format(java.time.format.DateTimeFormatter.ofPattern("dd/MM/yyyy HH:mm"))
        }.getOrDefault(d.importadoEm)
        return itensDe(c, d.id).map { i ->
            listOf(
                d.numero, d.emitenteNome, d.notaOrigemNumero.orEmpty(), i.nItem.toString(),
                i.produtoCodigo.orEmpty(), i.produtoDescricao.orEmpty(), i.cprodCliente.orEmpty(), i.xprodCliente.orEmpty(),
                i.qtdEsperadaUn.toString(), i.qtdConferidaUn.toString(),
                (i.qtdEsperadaUn - i.qtdConferidaUn).coerceAtLeast(0).toString(),
                i.status.replace('_', ' ').lowercase(), i.motivo ?: d.motivo.orEmpty(), i.justificativa.orEmpty(),
                d.status.replace('_', ' ').lowercase(), importada, d.clienteBairro.orEmpty(), d.clienteCidade.orEmpty(),
            )
        }
    }

    /**
     * Os motivos de cada devolução — o da NF e os dos itens que diferem, na
     * ordem dos itens —, numa consulta só para a lista inteira.
     */
    private fun comMotivos(c: Connection, lista: List<DevolucaoResumo>): List<DevolucaoResumo> {
        if (lista.isEmpty()) return lista
        val porNota = c.buscarTodos(
            """SELECT i.nota_devolucao_id, COALESCE(i.motivo, d.motivo)
                 FROM item_devolucao i JOIN nota_devolucao d ON d.id = i.nota_devolucao_id
                WHERE COALESCE(i.motivo, d.motivo) IS NOT NULL
                GROUP BY 1, 2 ORDER BY 1, MIN(i.n_item)""",
        ) { it.getLong(1) to it.getString(2) }.groupBy({ it.first }, { it.second })
        return lista.map { it.copy(motivos = porNota[it.id].orEmpty()) }
    }

    // ------------------------------------------------------- caixas conferidas

    /** As leituras que valem (sem as excluídas), na ordem em que chegaram. */
    fun leituras(devolucaoId: Long): List<LeituraDevolucaoResumo> = db.leitura { c ->
        c.buscarTodos(
            """SELECT l.id, i.id, i.n_item, p.codigo, p.descricao, l.modo, l.qtd_un, pl.codigo, u.nome,
                      COALESCE(l.lido_em, l.registrado_em)
                 FROM leitura_devolucao l
                 JOIN item_devolucao i ON i.id = l.item_devolucao_id
                 LEFT JOIN produto p ON p.id = l.produto_id
                 LEFT JOIN pallet pl ON pl.id = l.pallet_id
                 LEFT JOIN usuario u ON u.id = l.usuario_id
                WHERE i.nota_devolucao_id = ? AND l.cancelada = 0
                ORDER BY l.id""",
            devolucaoId,
        ) {
            LeituraDevolucaoResumo(
                it.getLong(1), it.getLong(2), it.getInt(3), it.getString(4), it.getString(5), it.getString(6),
                it.getInt(7), it.getString(8), it.getString(9), it.getString(10),
            )
        }
    }

    /**
     * Caixa conferida por engano sai da devolução (supervisor e admin). A
     * leitura fica marcada como excluída, com quem e por quê; o item recalcula
     * e, se estava fechado, reabre para fechar de novo com o que ficou. Pallet
     * que fica sem caixa nenhuma volta livre, sem endereço.
     */
    fun excluirLeituras(
        usuarioId: Long,
        devolucaoId: Long,
        leituraIds: List<Long>,
        justificativa: String,
    ): CaixasAlteradas = db.transacao { c ->
        val just = justificativa.trim().ifEmpty {
            throw Erros.invalido("JUSTIFICATIVA_OBRIGATORIA", "Informe por que as caixas saem da devolução")
        }
        val numero = devolucaoAlteravel(c, devolucaoId, "excluir caixas")
        val alvo = leiturasDaDevolucao(c, devolucaoId, leituraIds)
        val agora = Instant.now().toString()
        alvo.forEach {
            c.atualizar(
                """UPDATE leitura_devolucao SET cancelada = 1, cancelada_em = ?, cancelada_por = ?,
                          cancelamento_motivo = ?
                    WHERE id = ?""",
                agora, usuarioId, just, it.id,
            )
        }
        recontarItens(c, alvo.map { it.itemId }.distinct())
        val liberados = liberarPalletsVazios(c, devolucaoId, usuarioId, agora)
        reavaliarNota(c, devolucaoId)
        auditarDevolucao(
            c, devolucaoId, "DEVOLUCAO_CAIXAS_EXCLUIDAS", usuarioId,
            "NF $numero: ${alvo.size} leitura(s), ${alvo.sumOf { it.qtdUn }} un" +
                (if (liberados.isNotEmpty()) "; pallets liberados ${liberados.joinToString()}" else "") + ": $just",
        )
        CaixasAlteradas(alvo.size, liberados, itensDe(c, devolucaoId))
    }

    /**
     * Põe as caixas marcadas num pallet de devolução: as conferidas sem
     * pallet (que por isso não apareciam no estoque) ou as de outro pallet da
     * mesma NF, para juntar tudo num só. O pallet se amarra à NF como na
     * conferência; o que ficar vazio volta livre.
     */
    fun atribuirPallet(
        usuarioId: Long,
        devolucaoId: Long,
        leituraIds: List<Long>,
        palletCodigo: String,
    ): CaixasAlteradas = db.transacao { c ->
        val numero = devolucaoAlteravel(c, devolucaoId, "mudar o pallet das caixas")
        val cod = palletCodigo.trim().uppercase().ifEmpty {
            throw Erros.invalido("PALLET_OBRIGATORIO", "Informe o pallet de devolução (P-DEV-)")
        }
        val alvo = leiturasDaDevolucao(c, devolucaoId, leituraIds)
        val palletId = amarrarPallet(c, cod, devolucaoId, usuarioId)
        val mover = alvo.filter { it.palletId != palletId }
        mover.forEach { c.atualizar("UPDATE leitura_devolucao SET pallet_id = ? WHERE id = ?", palletId, it.id) }
        val liberados = liberarPalletsVazios(c, devolucaoId, usuarioId, Instant.now().toString())
        if (mover.isNotEmpty()) {
            val de = mover.map { it.palletCodigo ?: "sem pallet" }.distinct().joinToString()
            auditarDevolucao(
                c, devolucaoId, "DEVOLUCAO_CAIXAS_PALLET", usuarioId,
                "NF $numero: ${mover.size} leitura(s), ${mover.sumOf { it.qtdUn }} un de $de para $cod" +
                    (if (liberados.isNotEmpty()) "; pallets liberados ${liberados.joinToString()}" else ""),
            )
        }
        CaixasAlteradas(mover.size, liberados, itensDe(c, devolucaoId))
    }

    private data class LeituraAlvo(
        val id: Long, val itemId: Long, val qtdUn: Int, val palletId: Long?, val palletCodigo: String?,
    )

    /** As leituras pedidas — todas desta devolução e ainda valendo, ou nenhuma. */
    private fun leiturasDaDevolucao(c: Connection, devolucaoId: Long, ids: List<Long>): List<LeituraAlvo> {
        val unicos = ids.distinct()
        if (unicos.isEmpty()) throw Erros.invalido("CAIXAS_OBRIGATORIAS", "Marque ao menos uma caixa")
        // Em lotes: o SQLite limita os parâmetros de uma consulta.
        val achadas = unicos.chunked(500).flatMap { lote ->
            c.buscarTodos(
                """SELECT l.id, l.item_devolucao_id, l.qtd_un, l.pallet_id, pl.codigo
                     FROM leitura_devolucao l
                     JOIN item_devolucao i ON i.id = l.item_devolucao_id
                     LEFT JOIN pallet pl ON pl.id = l.pallet_id
                    WHERE i.nota_devolucao_id = ? AND l.cancelada = 0
                      AND l.id IN (${lote.joinToString(",") { "?" }})""",
                *(listOf<Any?>(devolucaoId) + lote).toTypedArray(),
            ) {
                LeituraAlvo(
                    it.getLong(1), it.getLong(2), it.getInt(3),
                    it.getLong(4).takeIf { _ -> !it.wasNull() }, it.getString(5),
                )
            }
        }
        if (achadas.size != unicos.size) {
            throw Erros.conflito(
                "CAIXAS_MUDARAM",
                "${unicos.size - achadas.size} caixa(s) já não estão nesta devolução — outra pessoa mexeu. Atualize a tela.",
            )
        }
        return achadas
    }

    /** O número da NF, se ela ainda pode mudar: baixada, a mercadoria já saiu. */
    private fun devolucaoAlteravel(c: Connection, devolucaoId: Long, oQue: String): String {
        val (numero, status) = c.buscarUm("SELECT numero, status FROM nota_devolucao WHERE id = ?", devolucaoId) {
            it.getString(1) to it.getString(2)
        } ?: throw Erros.naoEncontrado("Devolução")
        if (status == "BAIXADA") {
            throw Erros.conflito("DEVOLUCAO_BAIXADA", "A NF $numero já foi baixada: não dá para $oQue")
        }
        return numero
    }

    /**
     * Recalcula o conferido dos itens que perderam leitura. Item fechado
     * reabre: a quantidade com que foi fechado já não é a que ficou.
     */
    private fun recontarItens(c: Connection, itemIds: List<Long>) = itemIds.forEach { id ->
        c.atualizar(
            """UPDATE item_devolucao SET
                 qtd_conferida_un = (SELECT COALESCE(SUM(qtd_un), 0) FROM leitura_devolucao
                                      WHERE item_devolucao_id = ? AND cancelada = 0),
                 divergencia_justificativa = CASE WHEN status IN ('FECHADO','FECHADO_DIVERGENCIA')
                                                  THEN NULL ELSE divergencia_justificativa END
               WHERE id = ?""",
            id, id,
        )
        c.atualizar(
            """UPDATE item_devolucao
                  SET status = CASE WHEN qtd_conferida_un > 0 THEN 'EM_CONFERENCIA' ELSE 'ABERTO' END
                WHERE id = ?""",
            id,
        )
        // Sobra não tem NF dizendo quanto era: o esperado é o que veio.
        c.atualizar(
            """UPDATE item_devolucao SET qtd_esperada_un = qtd_conferida_un
                WHERE id = ? AND (SELECT sobra FROM nota_devolucao WHERE id = nota_devolucao_id) = 1""",
            id,
        )
    }

    /** Com item reaberto a NF volta a em conferência; com todos fechados, conferida. */
    private fun reavaliarNota(c: Connection, devolucaoId: Long) {
        val abertos = c.buscarUm(
            """SELECT COUNT(*) FROM item_devolucao
                WHERE nota_devolucao_id = ? AND status NOT IN ('FECHADO','FECHADO_DIVERGENCIA')""",
            devolucaoId,
        ) { it.getInt(1) } ?: 0
        if (abertos == 0) {
            c.atualizar("UPDATE nota_devolucao SET status = 'CONFERIDA' WHERE id = ? AND status <> 'BAIXADA'", devolucaoId)
        } else {
            c.atualizar("UPDATE nota_devolucao SET status = 'EM_CONFERENCIA' WHERE id = ? AND status = 'CONFERIDA'", devolucaoId)
        }
    }

    /**
     * Pallet desta NF que ficou sem caixa volta livre e sem endereço, como no
     * apagar: P-DEV vazio não fica no estoque com zero caixas.
     */
    private fun liberarPalletsVazios(c: Connection, devolucaoId: Long, usuarioId: Long, agora: String): List<String> {
        val vazios = c.buscarTodos(
            """SELECT p.id, p.codigo FROM pallet p
                WHERE p.nota_devolucao_id = ? AND p.status NOT IN ('EXPEDIDO','FUNDIDO','ESVAZIADO')
                  AND NOT EXISTS (SELECT 1 FROM leitura_devolucao l WHERE l.pallet_id = p.id AND l.cancelada = 0)
                ORDER BY p.codigo""",
            devolucaoId,
        ) { it.getLong(1) to it.getString(2) }
        vazios.forEach { (id, _) ->
            c.atualizar(
                """UPDATE enderecamento SET liberado_em=?, liberado_por=?, motivo_liberacao='CORRECAO'
                    WHERE liberado_em IS NULL AND pallet_id = ?""",
                agora, usuarioId, id,
            )
            c.atualizar(
                """UPDATE pallet SET nota_devolucao_id = NULL, usuario_atual_id = NULL,
                          encerrado_em = NULL, encerrado_por = NULL,
                          status = CASE WHEN status IN ('EM_USO','ENCERRADO','ENDERECADO') THEN 'CRIADO' ELSE status END
                    WHERE id = ?""",
                id,
            )
        }
        return vazios.map { it.second }
    }

    // --------------------------------------------------------- etiqueta 2ª via

    /**
     * O código para a etiqueta 2ª via do produto de um item: o GTIN-14 que o
     * produto já tem ou, sem nenhum que o coletor leia, o [codigoInterno]
     * gravado agora no produto — é por ele que o bipe acha o produto.
     */
    fun etiquetaDoItem(usuarioId: Long, itemId: Long): EtiquetaDoItem = db.transacao { c ->
        data class Item(
            val devolucaoId: Long, val nItem: Int, val produtoId: Long?, val codigo: String?, val descricao: String?,
            val unPorCaixa: Int?, val esperada: Int, val conferida: Int, val notaStatus: String,
        )
        val i = c.buscarUm(
            """SELECT i.nota_devolucao_id, i.n_item, i.produto_id, p.codigo, p.descricao, p.unidades_por_caixa,
                      i.qtd_esperada_un, i.qtd_conferida_un, d.status
                 FROM item_devolucao i
                 JOIN nota_devolucao d ON d.id = i.nota_devolucao_id
                 LEFT JOIN produto p ON p.id = i.produto_id
                WHERE i.id = ?""",
            itemId,
        ) {
            Item(
                it.getLong(1), it.getInt(2), it.getLong(3).takeIf { _ -> !it.wasNull() }, it.getString(4),
                it.getString(5), it.getInt(6).takeIf { _ -> !it.wasNull() }, it.getInt(7), it.getInt(8), it.getString(9),
            )
        } ?: throw Erros.naoEncontrado("Item de devolução")
        if (i.notaStatus == "BAIXADA") {
            throw Erros.conflito("DEVOLUCAO_BAIXADA", "A devolução já foi baixada: não há caixa para etiquetar")
        }
        val produtoId = i.produtoId ?: throw Erros.conflito("ITEM_SEM_PRODUTO", "O item ${i.nItem} não tem produto vinculado")

        // Sem unidades por caixa o bipe é recusado: a etiqueta sairia para nada.
        if ((i.unPorCaixa ?: 0) <= 0) {
            throw Erros.conflito(
                "UNIDADES_POR_CAIXA_NAO_CADASTRADA",
                "${i.codigo} está sem unidades por caixa: cadastre em Produtos, senão a caixa " +
                    "etiquetada é recusada no bipe.",
            )
        }

        val (codigo, criado) = codigoParaEtiqueta(c, produtoId, usuarioId)
        if (criado) {
            auditarDevolucao(
                c, i.devolucaoId, "DEVOLUCAO_CODIGO_INTERNO", usuarioId,
                "Código interno $codigo gravado em ${i.codigo} ${i.descricao} para a etiqueta 2ª via do item ${i.nItem}",
            )
        }
        val faltam = (i.esperada - i.conferida).coerceAtLeast(0)
        EtiquetaDoItem(
            codigo, i.codigo.orEmpty(), i.descricao.orEmpty(), criado,
            i.unPorCaixa?.takeIf { it > 0 }?.let { (faltam + it - 1) / it },
        )
    }

    /**
     * Reimpressão da etiqueta 2ª via de qualquer produto, pela retaguarda
     * (usuário admin): o mesmo código da devolução.
     */
    fun etiquetaDoProduto(usuarioId: Long, produtoId: Long): EtiquetaDoItem = db.transacao { c ->
        val (produto, descricao) = c.buscarUm("SELECT codigo, descricao FROM produto WHERE id = ?", produtoId) {
            it.getString(1) to it.getString(2)
        } ?: throw Erros.naoEncontrado("Produto")
        val (codigo, criado) = codigoParaEtiqueta(c, produtoId, usuarioId)
        c.inserir(
            """INSERT INTO log_auditoria (entidade, entidade_id, acao, usuario_id, em, detalhe_json)
               VALUES ('produto', ?, 'ETIQUETA_REIMPRESSA', ?, ?, ?)""",
            produtoId, usuarioId, Instant.now().toString(),
            "Etiqueta 2ª via de $produto com $codigo" + (if (criado) " (código interno gravado)" else ""),
        )
        EtiquetaDoItem(codigo, produto, descricao, criado, null)
    }

    /**
     * O código da etiqueta 2ª via: o GTIN-14 ativo que o produto já tem ou,
     * sem nenhum que o coletor leia, o [codigoInterno] gravado agora — é por
     * ele que o bipe acha o produto. Devolve o código e se foi gravado.
     */
    private fun codigoParaEtiqueta(c: Connection, produtoId: Long, usuarioId: Long): Pair<String, Boolean> {
        // O bipe só acha GTIN-14 válido: código gravado de outro jeito não serve.
        c.buscarTodos(
            "SELECT codigo FROM produto_codigo_barras WHERE produto_id = ? AND ativo = 1 ORDER BY confirmado DESC, id DESC",
            produtoId,
        ) { it.getString(1) }
            .firstOrNull { (Gtin.normalizar(it) as? ResultadoGtin.Ok)?.gtin14 == it }
            ?.let { return it to false }

        val interno = codigoInterno(produtoId)
        val dono = c.buscarUm("SELECT produto_id FROM produto_codigo_barras WHERE codigo = ?", interno) { it.getLong(1) }
        val agora = Instant.now().toString()
        when (dono) {
            null -> c.inserir(
                """INSERT INTO produto_codigo_barras
                   (produto_id, codigo, simbologia, origem, confirmado, confirmado_em, ativo, criado_em, criado_por)
                   VALUES (?,?,'INTERNO','MANUAL',1,?,1,?,?)""",
                produtoId, interno, agora, agora, usuarioId,
            )
            // Desativado no cadastro de produtos: volta a valer.
            produtoId -> c.atualizar("UPDATE produto_codigo_barras SET ativo = 1 WHERE codigo = ?", interno)
            else -> throw Erros.conflito("GTIN_DUPLICADO", "O código interno $interno já está em outro produto")
        }
        return interno to true
    }

    // ------------------------------------------------------------- conferência

    /**
     * Conferência física. Bipe soma a caixa completa; digitação soma unidades
     * avulsas — o cliente pode devolver uma lata só.
     */
    fun conferir(
        usuarioId: Long,
        idempotencyKey: String,
        itemDevolucaoId: Long,
        modo: String,
        codigoLido: String?,
        qtdUn: Int?,
        palletCodigo: String?,
        lidoEm: String?,
    ): ResultadoConferencia = db.transacao { c ->
        c.buscarUm(
            "SELECT item_devolucao_id FROM leitura_devolucao WHERE idempotency_key = ?", idempotencyKey,
        ) { it.getLong(1) }?.let { return@transacao situacao(c, it) }

        val item = c.buscarUm(
            """SELECT i.id, i.produto_id, i.qtd_esperada_un, i.qtd_conferida_un, i.status,
                      p.unidades_por_caixa, d.id, d.status
                 FROM item_devolucao i
                 JOIN nota_devolucao d ON d.id = i.nota_devolucao_id
                 LEFT JOIN produto p ON p.id = i.produto_id
                WHERE i.id = ?""",
            itemDevolucaoId,
        ) {
            ItemConferencia(
                it.getLong(1), it.getLong(2), it.getInt(3), it.getInt(4), it.getString(5),
                it.getInt(6).takeIf { _ -> !it.wasNull() }, it.getLong(7), it.getString(8),
            )
        } ?: throw Erros.naoEncontrado("Item de devolução")

        if (item.status in setOf("FECHADO", "FECHADO_DIVERGENCIA")) {
            throw Erros.conflito("ITEM_FECHADO", "Item já conferido")
        }

        val quantidade = when (modo) {
            "BIPE" -> {
                val normalizado = when (val r = Gtin.normalizar(codigoLido)) {
                    is ResultadoGtin.Ok -> r.gtin14
                    is ResultadoGtin.Invalido -> null
                }
                val produtoLido = normalizado?.let { g ->
                    c.buscarUm(
                        "SELECT produto_id FROM produto_codigo_barras WHERE codigo = ? AND ativo = 1", g,
                    ) { it.getLong(1) }
                } ?: throw Erros.conflito(
                    "CODIGO_NAO_CADASTRADO", "Código não cadastrado",
                    mapOf("codigoLido" to codigoLido),
                )

                if (produtoLido != item.produtoId) {
                    val desc = c.buscarUm(
                        "SELECT descricao FROM produto WHERE id = ?", produtoLido,
                    ) { it.getString(1) }
                    throw Erros.conflito(
                        "PRODUTO_DIVERGENTE", "Item errado: ${desc ?: "produto desconhecido"}",
                        mapOf("produtoLido" to desc),
                    )
                }
                // Bipar a caixa presume caixa completa.
                item.unidadesPorCaixa ?: throw Erros.conflito(
                    "UNIDADES_POR_CAIXA_NAO_CADASTRADA",
                    "Produto sem unidades por caixa cadastradas. Cadastre antes de bipar.",
                )
            }

            "DIGITADO" -> qtdUn?.takeIf { it > 0 }
                ?: throw Erros.invalido("QUANTIDADE_INVALIDA", "Informe a quantidade em unidades")

            else -> throw Erros.invalido("MODO_INVALIDO", "Modo deve ser BIPE ou DIGITADO")
        }

        if (item.conferida + quantidade > item.esperada) {
            throw Erros.conflito(
                "QUANTIDADE_COMPLETA",
                "Passaria do declarado na NF (${item.conferida}/${item.esperada} un)",
                mapOf("qtdConferida" to item.conferida, "qtdEsperada" to item.esperada),
            )
        }

        // Caixa sem pallet some do estoque: o P-DEV endereçado mostraria zero.
        val palletCod = palletCodigo?.trim()?.takeIf { it.isNotEmpty() } ?: throw Erros.conflito(
            "PALLET_OBRIGATORIO",
            "Bipe a etiqueta do pallet de devolução (P-DEV-) antes das caixas",
        )
        val palletId = amarrarPallet(c, palletCod, item.devolucaoId, usuarioId)

        c.inserir(
            """INSERT INTO leitura_devolucao
               (idempotency_key, pallet_id, item_devolucao_id, produto_id, codigo_lido,
                modo, qtd_un, usuario_id, lido_em, registrado_em)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            idempotencyKey, palletId, itemDevolucaoId, item.produtoId, codigoLido,
            modo, quantidade, usuarioId, lidoEm, Instant.now().toString(),
        )

        c.atualizar(
            """UPDATE item_devolucao SET
                 qtd_conferida_un = (SELECT COALESCE(SUM(qtd_un), 0) FROM leitura_devolucao
                                      WHERE item_devolucao_id = ? AND cancelada = 0),
                 status = CASE WHEN status = 'ABERTO' THEN 'EM_CONFERENCIA' ELSE status END
               WHERE id = ?""",
            itemDevolucaoId, itemDevolucaoId,
        )
        c.atualizar(
            "UPDATE nota_devolucao SET status='EM_CONFERENCIA' WHERE id=? AND status='AGUARDANDO_CONFERENCIA'",
            item.devolucaoId,
        )

        situacao(c, itemDevolucaoId)
    }

    fun fecharItem(usuarioId: Long, itemId: Long, justificativa: String?): ItemDevolucaoResumo =
        db.transacao { c ->
            val item = c.buscarUm(
                """SELECT qtd_esperada_un, qtd_conferida_un, status, nota_devolucao_id
                     FROM item_devolucao WHERE id = ?""",
                itemId,
            ) { Quadra(it.getInt(1), it.getInt(2), it.getString(3), it.getLong(4)) }
                ?: throw Erros.naoEncontrado("Item de devolução")

            if (item.status in setOf("FECHADO", "FECHADO_DIVERGENCIA")) {
                throw Erros.conflito("ITEM_FECHADO", "Item já conferido")
            }

            // Divergência aqui é informação de negócio: revela cliente
            // devolvendo menos do que declarou na nota.
            val divergente = item.conferida != item.esperada
            if (divergente && justificativa.isNullOrBlank()) {
                throw Erros.conflito(
                    "ITEM_INCOMPLETO",
                    "Conferido ${item.conferida} de ${item.esperada} un. Informe a justificativa.",
                    mapOf("qtdConferida" to item.conferida, "qtdEsperada" to item.esperada),
                )
            }

            c.atualizar(
                "UPDATE item_devolucao SET status=?, divergencia_justificativa=? WHERE id=?",
                if (divergente) "FECHADO_DIVERGENCIA" else "FECHADO", justificativa, itemId,
            )

            val abertos = c.buscarUm(
                """SELECT COUNT(*) FROM item_devolucao
                    WHERE nota_devolucao_id = ? AND status NOT IN ('FECHADO','FECHADO_DIVERGENCIA')""",
                item.devolucaoId,
            ) { it.getInt(1) } ?: 0
            if (abertos == 0) {
                c.atualizar("UPDATE nota_devolucao SET status='CONFERIDA' WHERE id=?", item.devolucaoId)
            }

            itens(item.devolucaoId).first { it.id == itemId }
        }

    // ----------------------------------------------------------------- apagar

    /**
     * Devolução lançada por engano — XML errado, NF de origem errada — some,
     * para lançar a certa.
     *
     * Baixada não se apaga: a mercadoria já saiu do estoque. Com conferência
     * começada, só com [comConferencia]: as leituras vão junto e os pallets
     * P-DEV voltam livres e sem endereço — a mercadoria continua neles e é
     * conferida de novo na devolução certa. O que foi apagado fica descrito
     * no log de auditoria.
     */
    fun apagar(usuarioId: Long, devolucaoId: Long, justificativa: String, comConferencia: Boolean): DevolucaoApagada =
        db.transacao { c ->
            val just = justificativa.trim().ifEmpty {
                throw Erros.invalido("JUSTIFICATIVA_OBRIGATORIA", "Informe por que a devolução está sendo apagada")
            }
            data class Nota(val numero: String, val chave: String, val emitente: String, val status: String, val origem: String?)
            val d = c.buscarUm(
                """SELECT d.numero, d.chave, d.emitente_nome, d.status, COALESCE(n.numero, d.origem_numero)
                     FROM nota_devolucao d LEFT JOIN nota_fiscal n ON n.id = d.nota_fiscal_origem_id
                    WHERE d.id = ?""",
                devolucaoId,
            ) { Nota(it.getString(1), it.getString(2), it.getString(3), it.getString(4), it.getString(5)) }
                ?: throw Erros.naoEncontrado("Devolução")
            if (d.status == "BAIXADA") {
                throw Erros.conflito(
                    "DEVOLUCAO_BAIXADA",
                    "A NF ${d.numero} já foi baixada: a mercadoria saiu do estoque e a devolução não pode ser apagada",
                )
            }
            // Baixa que já aconteceu — mesmo estornada, mesmo de algumas
            // caixas da sobra — é histórico com mercadoria atrás: apagar a
            // devolução levaria junto o registro de uma saída de verdade.
            val baixas = c.buscarUm(
                """SELECT (SELECT COUNT(*) FROM baixa_sobra WHERE nota_devolucao_id = ?)
                        + (SELECT COUNT(*) FROM baixa_devolucao_estornada WHERE nota_devolucao_id = ?)""",
                devolucaoId, devolucaoId,
            ) { it.getInt(1) } ?: 0
            if (baixas > 0) {
                throw Erros.conflito(
                    "DEVOLUCAO_COM_BAIXA",
                    "A NF ${d.numero} já teve baixa (de caixas ou estornada): o registro da saída fica, " +
                        "e por isso ela não pode ser apagada",
                )
            }

            val leituras = c.buscarUm(
                """SELECT COUNT(*) FROM leitura_devolucao l JOIN item_devolucao i ON i.id = l.item_devolucao_id
                    WHERE i.nota_devolucao_id = ? AND l.cancelada = 0""",
                devolucaoId,
            ) { it.getInt(1) } ?: 0
            val pallets = c.buscarTodos(
                "SELECT codigo FROM pallet WHERE nota_devolucao_id = ? ORDER BY codigo", devolucaoId,
            ) { it.getString(1) }
            if ((leituras > 0 || pallets.isNotEmpty()) && !comConferencia) {
                throw Erros.conflito(
                    "DEVOLUCAO_COM_CONFERENCIA",
                    "A NF ${d.numero} já tem $leituras leitura(s) de conferência" +
                        (if (pallets.isNotEmpty()) " em ${pallets.joinToString()}" else "") +
                        ". Apagar tira essa conferência e deixa os pallets livres para conferir de novo.",
                    mapOf("leituras" to leituras, "pallets" to pallets),
                )
            }

            val agora = Instant.now().toString()
            c.atualizar(
                """DELETE FROM leitura_devolucao
                    WHERE item_devolucao_id IN (SELECT id FROM item_devolucao WHERE nota_devolucao_id = ?)""",
                devolucaoId,
            )
            // A mercadoria continua nos pallets: eles voltam livres, sem
            // endereço, para a conferência da devolução certa.
            c.atualizar(
                """UPDATE enderecamento SET liberado_em=?, liberado_por=?, motivo_liberacao='CORRECAO'
                    WHERE liberado_em IS NULL AND pallet_id IN (SELECT id FROM pallet WHERE nota_devolucao_id = ?)""",
                agora, usuarioId, devolucaoId,
            )
            c.atualizar(
                """UPDATE pallet SET nota_devolucao_id = NULL, usuario_atual_id = NULL,
                          encerrado_em = NULL, encerrado_por = NULL,
                          status = CASE WHEN status IN ('EM_USO','ENCERRADO','ENDERECADO') THEN 'CRIADO' ELSE status END
                    WHERE nota_devolucao_id = ?""",
                devolucaoId,
            )
            c.atualizar("DELETE FROM item_devolucao WHERE nota_devolucao_id = ?", devolucaoId)
            c.atualizar("DELETE FROM nota_devolucao WHERE id = ?", devolucaoId)
            c.inserir(
                """INSERT INTO log_auditoria (entidade, entidade_id, acao, usuario_id, em, detalhe_json)
                   VALUES ('nota_devolucao', ?, 'DEVOLUCAO_APAGADA', ?, ?, ?)""",
                devolucaoId, usuarioId, agora,
                "NF ${d.numero} (chave ${d.chave}) de ${d.emitente}, origem NF ${d.origem ?: "—"} · " +
                    "$leituras leitura(s)" + (if (pallets.isNotEmpty()) " em ${pallets.joinToString()}" else "") +
                    ": $just",
            )
            DevolucaoApagada(d.numero, leituras, pallets)
        }

    // ------------------------------------------------------------------ baixa

    fun baixar(
        usuarioId: Long,
        devolucaoId: Long,
        tipo: String,
        observacao: String?,
        nfRevenda: String?,
    ): DevolucaoResumo = db.transacao { c ->
        if (tipo !in setOf("DESCARTE", "REVENDA")) {
            throw Erros.invalido("TIPO_INVALIDO", "Tipo deve ser DESCARTE ou REVENDA")
        }
        val nota = c.buscarUm("SELECT status, sobra FROM nota_devolucao WHERE id = ?", devolucaoId) {
            it.getString(1) to (it.getInt(2) == 1)
        } ?: throw Erros.naoEncontrado("Devolução")
        // A sobra não tem NF dizendo quanto era: não há divergência a encerrar
        // item a item. Baixar tudo encerra os itens e segue.
        val status = if (nota.second && nota.first == "EM_CONFERENCIA") {
            c.atualizar(
                """UPDATE item_devolucao SET status = 'FECHADO'
                    WHERE nota_devolucao_id = ? AND status NOT IN ('FECHADO','FECHADO_DIVERGENCIA')""",
                devolucaoId,
            )
            "CONFERIDA"
        } else {
            nota.first
        }

        if (status != "CONFERIDA") {
            throw Erros.conflito(
                "DEVOLUCAO_NAO_CONFERIDA",
                "Conclua a conferência antes de baixar (situação: ${status.replace('_', ' ').lowercase()})",
            )
        }

        val agora = Instant.now().toString()
        c.inserir(
            """INSERT INTO baixa_devolucao
               (nota_devolucao_id, tipo, observacao, nf_revenda_referencia, baixado_em, baixado_por)
               VALUES (?,?,?,?,?,?)""",
            devolucaoId, tipo, observacao, nfRevenda, agora, usuarioId,
        )

        // Os pallets de devolução saem do estoque e liberam os endereços.
        c.atualizar(
            """UPDATE enderecamento SET liberado_em=?, liberado_por=?, motivo_liberacao='EXPEDICAO'
                WHERE liberado_em IS NULL AND pallet_id IN
                      (SELECT id FROM pallet WHERE nota_devolucao_id = ?)""",
            agora, usuarioId, devolucaoId,
        )
        c.atualizar(
            // Só sai do estoque o que ainda estava nele: pallet já juntado a
            // outro (FUNDIDO) fica como está.
            "UPDATE pallet SET status='EXPEDIDO' WHERE nota_devolucao_id = ? AND status IN ('EM_USO','ENCERRADO','ENDERECADO')",
            devolucaoId,
        )
        c.atualizar("UPDATE nota_devolucao SET status='BAIXADA' WHERE id=?", devolucaoId)

        buscarResumo(c, devolucaoId)!!
    }

    /**
     * Fecha a conferência de estoque sem baixar.
     *
     * A sobra não tem NF dizendo quanto era, então ela nunca "termina"
     * sozinha: ficava em conferência para sempre, aparecendo no coletor e na
     * lista de pendências. Fechar encerra os itens e deixa a mercadoria no
     * estoque, esperando a baixa — que pode vir dias depois, por caixa ou de
     * uma vez.
     */
    fun fecharConferencia(usuarioId: Long, devolucaoId: Long): DevolucaoResumo = db.transacao { c ->
        val d = exigirSobra(c, devolucaoId)
        if (d.second == "BAIXADA") {
            throw Erros.conflito("DEVOLUCAO_BAIXADA", "A conferência ${d.first} já foi baixada")
        }
        val caixas = c.buscarUm(
            """SELECT COUNT(*) FROM leitura_devolucao l JOIN item_devolucao i ON i.id = l.item_devolucao_id
                WHERE i.nota_devolucao_id = ? AND l.cancelada = 0""",
            devolucaoId,
        ) { it.getInt(1) } ?: 0
        if (caixas == 0) {
            throw Erros.conflito(
                "CONFERENCIA_VAZIA",
                "Nenhuma caixa foi bipada nesta conferência. Bipe as caixas no coletor ou exclua a conferência.",
            )
        }
        c.atualizar(
            """UPDATE item_devolucao SET status = 'FECHADO'
                WHERE nota_devolucao_id = ? AND status NOT IN ('FECHADO','FECHADO_DIVERGENCIA')""",
            devolucaoId,
        )
        c.atualizar("UPDATE nota_devolucao SET status = 'CONFERIDA' WHERE id = ?", devolucaoId)
        // O P-DEV também fecha: sai de "em uso" e vai para a frente da fila de
        // endereçamento, com o nome de quem encerrou em "Montado por".
        c.atualizar(
            """UPDATE pallet SET status = 'ENCERRADO', usuario_atual_id = NULL,
                      encerrado_em = ?, encerrado_por = ?
                WHERE nota_devolucao_id = ? AND status = 'EM_USO'
                  AND EXISTS (SELECT 1 FROM leitura_devolucao l WHERE l.pallet_id = pallet.id AND l.cancelada = 0)""",
            Instant.now().toString(), usuarioId, devolucaoId,
        )
        auditarDevolucao(c, devolucaoId, "SOBRA_CONFERIDA", usuarioId, "Conferência encerrada com $caixas caixa(s)")
        buscarResumo(c, devolucaoId)!!
    }

    /**
     * A conferência volta a aceitar bipe.
     *
     * Vale para a sobra, onde sempre pode aparecer mais uma caixa, e para a
     * devolução do cliente: caixa que ficou fora da contagem só entra com o
     * item aberto, e a NF precisa voltar à lista do coletor. Todos os itens
     * reabrem — o que estava fechado com divergência perde a justificativa,
     * porque ela descrevia uma falta que está sendo recontada, exatamente
     * como na exclusão de caixa.
     */
    fun reabrirConferencia(usuarioId: Long, devolucaoId: Long): DevolucaoResumo = db.transacao { c ->
        data class Nota(val numero: String, val status: String, val sobra: Boolean)
        val d = c.buscarUm("SELECT numero, status, sobra FROM nota_devolucao WHERE id = ?", devolucaoId) {
            Nota(it.getString(1), it.getString(2), it.getInt(3) == 1)
        } ?: throw Erros.naoEncontrado("Devolução")
        if (d.status == "BAIXADA") {
            throw Erros.conflito(
                "DEVOLUCAO_BAIXADA",
                "${if (d.sobra) "A conferência" else "A NF"} ${d.numero} foi baixada. " +
                    "Estorne a baixa para mexer nela de novo.",
            )
        }
        val itens = c.buscarTodos(
            "SELECT id FROM item_devolucao WHERE nota_devolucao_id = ?", devolucaoId,
        ) { it.getLong(1) }
        if (itens.isEmpty()) {
            throw Erros.conflito("CONFERENCIA_VAZIA", "A NF ${d.numero} não tem itens para conferir")
        }
        recontarItens(c, itens)
        c.atualizar("UPDATE nota_devolucao SET status = 'EM_CONFERENCIA' WHERE id = ?", devolucaoId)
        auditarDevolucao(
            c, devolucaoId, if (d.sobra) "SOBRA_REABERTA" else "DEVOLUCAO_REABERTA", usuarioId,
            "Conferência reaberta: ${itens.size} item(ns) voltaram a aceitar bipe",
        )
        buscarResumo(c, devolucaoId)!!
    }

    private fun exigirSobra(c: Connection, devolucaoId: Long): Pair<String, String> {
        val d = c.buscarUm("SELECT numero, status, sobra FROM nota_devolucao WHERE id = ?", devolucaoId) {
            Triple(it.getString(1), it.getString(2), it.getInt(3) == 1)
        } ?: throw Erros.naoEncontrado("Devolução")
        if (!d.third) {
            throw Erros.conflito(
                "NAO_E_SOBRA",
                "A NF ${d.first} é devolução do cliente: a conferência dela fecha item a item, pelo cadeado",
            )
        }
        return d.first to d.second
    }

    /**
     * Desfaz a baixa: a mercadoria volta ao estoque.
     *
     * Baixou por engano — descarte no lugar de revenda, NF trocada — e a
     * mercadoria continua aqui. Os pallets voltam do EXPEDIDO; o endereço de
     * antes é retomado se ninguém ocupou a posição, senão o pallet volta sem
     * endereço, para o coletor endereçar de novo (mesma regra do estorno de
     * expedição, docs/MODELO-DE-DADOS.md §9.2).
     *
     * O pallet que ficou vazio não volta: ele saiu do estoque porque a última
     * caixa dele saiu antes, numa baixa por caixa da sobra — e essa caixa não
     * é desta baixa. A baixa estornada não some: vai inteira para
     * `baixa_devolucao_estornada`, com quem estornou e por quê.
     */
    fun estornarBaixa(usuarioId: Long, devolucaoId: Long, motivo: String): EstornoBaixa = db.transacao { c ->
        if (motivo.isBlank()) throw Erros.invalido("MOTIVO_OBRIGATORIO", "Informe o motivo do estorno")
        val d = c.buscarUm("SELECT numero, status FROM nota_devolucao WHERE id = ?", devolucaoId) {
            it.getString(1) to it.getString(2)
        } ?: throw Erros.naoEncontrado("Devolução")
        if (d.second != "BAIXADA") {
            throw Erros.conflito(
                "DEVOLUCAO_NAO_BAIXADA",
                "A NF ${d.first} não está baixada (situação: ${d.second.replace('_', ' ').lowercase()})",
            )
        }
        data class Baixa(val id: Long, val tipo: String, val obs: String?, val nf: String?, val em: String, val por: Long)
        val baixa = c.buscarUm(
            """SELECT id, tipo, observacao, nf_revenda_referencia, baixado_em, baixado_por
                 FROM baixa_devolucao WHERE nota_devolucao_id = ?""",
            devolucaoId,
        ) { Baixa(it.getLong(1), it.getString(2), it.getString(3), it.getString(4), it.getString(5), it.getLong(6)) }
            ?: throw Erros.conflito("BAIXA_NAO_ENCONTRADA", "Não há baixa registrada para a NF ${d.first}")

        // Revenda cujo estoque já voltou pela NF nova: estornar aqui contaria
        // a mesma mercadoria duas vezes.
        baixa.nf?.trim()?.takeIf { it.isNotEmpty() }?.let { nfRevenda ->
            c.buscarUm(
                """SELECT n.numero FROM nota_fiscal n
                    WHERE n.numero = ? AND EXISTS (
                        SELECT 1 FROM item_nota i JOIN leitura l ON l.item_nota_id = i.id
                         WHERE i.nota_fiscal_id = n.id AND l.cancelada = 0)
                    LIMIT 1""",
                nfRevenda,
            ) { it.getString(1) }?.let {
                throw Erros.conflito(
                    "REVENDA_EM_SEPARACAO",
                    "A NF de revenda $it já entrou e está sendo separada: estornar esta baixa duplicaria o estoque. " +
                        "Exclua a separação dela antes.",
                    mapOf("nfRevenda" to it),
                )
            }
        }

        val agora = Instant.now().toString()
        val enderecados = mutableListOf<String>()
        val pendentes = mutableListOf<PalletSemEnderecoDevolucao>()
        val sobra = c.buscarUm("SELECT sobra FROM nota_devolucao WHERE id = ?", devolucaoId) {
            it.getInt(1) == 1
        } ?: false

        // Só volta o pallet que ainda tem caixa: o que esvaziou saiu do
        // estoque por outra razão, e trazê-lo de volta criaria pallet com zero
        // caixas no estoque — exatamente o que a 0.7.8 corrigiu.
        c.buscarTodos(
            """SELECT p.id, p.codigo FROM pallet p
                WHERE p.nota_devolucao_id = ? AND p.status = 'EXPEDIDO'
                  AND EXISTS (SELECT 1 FROM leitura_devolucao l WHERE l.pallet_id = p.id AND l.cancelada = 0)
                ORDER BY p.codigo""",
            devolucaoId,
        ) { it.getLong(1) to it.getString(2) }.forEach { (palletId, codigo) ->
            // O endereço que ESTA baixa liberou, pelo instante dela.
            val anterior = c.buscarUm(
                """SELECT e.posicao_id, b.codigo, po.nivel
                     FROM enderecamento e
                     JOIN posicao po ON po.id = e.posicao_id
                     JOIN box b ON b.id = po.box_id
                    WHERE e.pallet_id = ? AND e.motivo_liberacao = 'EXPEDICAO' AND e.liberado_em = ?
                    ORDER BY e.id DESC LIMIT 1""",
                palletId, baixa.em,
            ) { Triple(it.getLong(1), it.getString(2), it.getInt(3)) }

            val ocupante = anterior?.let {
                c.buscarUm(
                    """SELECT p.codigo FROM enderecamento e JOIN pallet p ON p.id = e.pallet_id
                        WHERE e.posicao_id = ? AND e.liberado_em IS NULL""",
                    it.first,
                ) { r -> r.getString(1) }
            }

            if (anterior != null && ocupante == null) {
                c.inserir(
                    """INSERT INTO enderecamento (pallet_id, posicao_id, enderecado_em, enderecado_por)
                       VALUES (?,?,?,?)""",
                    palletId, anterior.first, agora, usuarioId,
                )
                c.atualizar("UPDATE pallet SET status='ENDERECADO' WHERE id=?", palletId)
                enderecados += codigo
            } else {
                c.atualizar("UPDATE pallet SET status='ENCERRADO' WHERE id=?", palletId)
                if (anterior != null && ocupante != null) {
                    pendentes += PalletSemEnderecoDevolucao(
                        codigo, "${anterior.second} nível ${anterior.third}", ocupante,
                    )
                }
            }
        }

        // A baixa sai da tabela viva (uma por NF) e fica guardada inteira.
        c.inserir(
            """INSERT INTO baixa_devolucao_estornada
               (nota_devolucao_id, tipo, observacao, nf_revenda_referencia, baixado_em, baixado_por,
                estornado_em, estornado_por, motivo)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            devolucaoId, baixa.tipo, baixa.obs, baixa.nf, baixa.em, baixa.por, agora, usuarioId, motivo,
        )
        c.atualizar("DELETE FROM baixa_devolucao WHERE id = ?", baixa.id)
        // A devolução volta a CONFERIDA, de onde a baixa a tirou. A sobra
        // volta a EM_CONFERENCIA com os itens abertos: foi a baixa que os
        // fechou à força, e sempre pode aparecer mais uma caixa.
        if (sobra) {
            c.atualizar(
                """UPDATE item_devolucao SET status = 'EM_CONFERENCIA'
                    WHERE nota_devolucao_id = ? AND status = 'FECHADO' AND qtd_conferida_un > 0""",
                devolucaoId,
            )
        }
        c.atualizar(
            "UPDATE nota_devolucao SET status = ? WHERE id = ?",
            if (sobra) "EM_CONFERENCIA" else "CONFERIDA", devolucaoId,
        )

        val caixas = c.buscarUm(
            """SELECT COUNT(*) FROM leitura_devolucao l JOIN item_devolucao i ON i.id = l.item_devolucao_id
                WHERE i.nota_devolucao_id = ? AND l.cancelada = 0""",
            devolucaoId,
        ) { it.getInt(1) } ?: 0
        val porCaixa = c.buscarUm(
            """SELECT COUNT(*) FROM leitura_devolucao l JOIN item_devolucao i ON i.id = l.item_devolucao_id
                WHERE i.nota_devolucao_id = ? AND l.cancelada = 3""",
            devolucaoId,
        ) { it.getInt(1) } ?: 0
        auditarDevolucao(
            c, devolucaoId, "DEVOLUCAO_BAIXA_ESTORNADA", usuarioId,
            "Baixa por ${baixa.tipo} de ${baixa.em} estornada: $motivo",
        )
        EstornoBaixa(
            d.first, if (sobra) "EM_CONFERENCIA" else "CONFERIDA", caixas, enderecados, pendentes, porCaixa,
        )
    }

    /**
     * Amarra o pallet à devolução na primeira leitura e recusa mistura depois.
     * Mesma trava da separação: um pallet, uma nota — senão a baixa por
     * DESCARTE leva junto mercadoria de outro cliente.
     */
    private fun amarrarPallet(c: Connection, codigo: String, devolucaoId: Long, usuarioId: Long): Long {
        val cod = codigo.trim().uppercase()
        val p = c.buscarUm(
            "SELECT id, tipo, status, nota_devolucao_id FROM pallet WHERE codigo = ?", cod,
        ) {
            PalletDev(
                it.getLong(1), it.getString(2), it.getString(3),
                it.getLong(4).takeIf { _ -> !it.wasNull() },
            )
        } ?: throw Erros.conflito(
            "PALLET_NAO_ENCONTRADO", "Pallet $cod não encontrado", mapOf("palletCodigo" to cod),
        )

        if (p.tipo != "DEVOLUCAO") {
            throw Erros.conflito(
                "PALLET_TIPO_INVALIDO",
                "$cod é pallet de separação. Crie um pallet de devolução (P-DEV-).",
            )
        }
        if (p.status in setOf("EXPEDIDO", "FUNDIDO", "ESVAZIADO")) {
            throw Erros.conflito("PALLET_ENCERRADO", "Pallet $cod saiu de circulação")
        }
        if (p.notaDevolucaoId != null && p.notaDevolucaoId != devolucaoId) {
            val outra = c.buscarUm(
                "SELECT numero FROM nota_devolucao WHERE id = ?", p.notaDevolucaoId,
            ) { it.getString(1) }
            throw Erros.conflito(
                "PALLET_DE_OUTRA_NOTA",
                "Pallet $cod já tem devolução da NF ${outra ?: "?"}",
                mapOf("palletCodigo" to cod, "notaAtual" to outra),
            )
        }
        if (p.notaDevolucaoId == null) {
            c.atualizar(
                ATUALIZAR_PALLET,
                devolucaoId, usuarioId, p.id,
            )
        }
        return p.id
    }

    // ---------------------------------------------------------------- internos

    private fun buscarPorGtin(c: Connection, gtin: String): SugestaoProduto? = c.buscarUm(
        """SELECT p.id, p.codigo, p.descricao, p.unidades_por_caixa
             FROM produto_codigo_barras b JOIN produto p ON p.id = b.produto_id
            WHERE b.codigo = ? AND b.ativo = 1""",
        gtin,
    ) {
        SugestaoProduto(
            it.getLong(1), it.getString(2), it.getString(3),
            it.getInt(4).takeIf { _ -> !it.wasNull() }, "GTIN",
        )
    }

    private fun situacao(c: Connection, itemId: Long): ResultadoConferencia {
        val i = c.buscarUm(
            "SELECT qtd_conferida_un, qtd_esperada_un FROM item_devolucao WHERE id = ?", itemId,
        ) { it.getInt(1) to it.getInt(2) }!!
        return ResultadoConferencia(itemId, i.first, i.second, i.first >= i.second)
    }

    private fun buscarResumo(c: Connection, id: Long): DevolucaoResumo? =
        c.buscarUm(sqlResumo("WHERE d.id = ?"), id) { mapaResumo(it) }

    private fun sqlResumo(filtro: String) = """
        SELECT d.id, d.numero, COALESCE(d.cliente_nome, d.emitente_nome), COALESCE(n.numero, d.origem_numero), d.status,
               -- O item da NF trocado por inteiro (fechado, sem nada conferido, com a troca
               -- apontando para ele) não conta: o que se espera é o que veio no lugar.
               COALESCE((SELECT SUM(i.qtd_esperada_un) FROM item_devolucao i
                          WHERE i.nota_devolucao_id = d.id
                            AND NOT (i.status = 'FECHADO_DIVERGENCIA' AND i.qtd_conferida_un = 0
                                     AND EXISTS (SELECT 1 FROM item_devolucao t WHERE t.troca_de_item_id = i.id))), 0),
               COALESCE((SELECT SUM(i.qtd_conferida_un) FROM item_devolucao i
                          WHERE i.nota_devolucao_id = d.id), 0),
               d.importado_em, d.origem_tipo, COALESCE(d.origem_chave, n.chave), d.motivo, d.cliente_bairro, d.cliente_cidade, d.sobra
          FROM nota_devolucao d
          LEFT JOIN nota_fiscal n ON n.id = d.nota_fiscal_origem_id
        $filtro
        ORDER BY d.id DESC
    """.trimIndent()

    private fun mapaResumo(rs: java.sql.ResultSet) = DevolucaoResumo(
        rs.getLong(1), rs.getString(2), rs.getString(3), rs.getString(4),
        rs.getString(5), rs.getInt(6), rs.getInt(7), rs.getString(8),
        origemTipo = rs.getString(9), origemChave = rs.getString(10), motivo = rs.getString(11),
        clienteBairro = rs.getString(12), clienteCidade = rs.getString(13), sobra = rs.getInt(14) == 1,
    )

    // --------------------------------------------------------- NF de origem

    private data class OrigemResolvida(
        val tipo: String,
        val notaId: Long?,
        val numero: String,
        val serie: String?,
        val chave: String?,
        val emissao: String?,
        val xml: ByteArray?,
        val clienteCnpj: String?,
        val cliente: String?,
    )

    private fun resolverOrigem(c: Connection, o: OrigemDevolucao?): OrigemResolvida? {
        if (o == null) return null
        o.notaId?.let { id ->
            return c.buscarUm(
                "SELECT id, numero, serie, chave, emissao, dest_cnpj, dest_nome FROM nota_fiscal WHERE id = ?", id,
            ) {
                OrigemResolvida(
                    "SISTEMA", it.getLong(1), it.getString(2), it.getString(3), it.getString(4),
                    it.getString(5), null, it.getString(6), it.getString(7),
                )
            } ?: throw Erros.naoEncontrado("Nota de origem")
        }
        o.xml?.let { bytes ->
            val nf = try {
                NfeParser.parse(bytes)
            } catch (e: NfeInvalidoException) {
                throw Erros.invalido("XML_ORIGEM_INVALIDO", "XML da NF de origem: ${e.message.orEmpty()}")
            }
            // XML de uma NF que o sistema já tem: fica ligada a ela também.
            val noSistema = c.buscarUm("SELECT id FROM nota_fiscal WHERE chave = ?", nf.chave) { it.getLong(1) }
            return OrigemResolvida(
                "XML", noSistema, nf.numero, nf.serie, nf.chave, nf.emissao, bytes, nf.destCnpj, nf.destNome,
            )
        }
        return o.numero?.trim()?.takeIf { it.isNotEmpty() }?.let {
            OrigemResolvida("DIGITADA", null, it, null, null, null, null, null, null)
        }
    }

    /** O que vale conferir antes de lançar: cliente errado e devolução repetida. */
    private fun avisosDaOrigem(c: Connection, o: OrigemResolvida, cnpjDevolucao: String?): List<String> {
        val avisos = mutableListOf<String>()
        if (o.clienteCnpj != null && cnpjDevolucao != null && o.clienteCnpj != cnpjDevolucao) {
            avisos += "A NF de origem ${o.numero} foi para outro CNPJ (${o.clienteCnpj}); " +
                "quem devolve é ${cnpjDevolucao}. Confira se é a NF certa."
        }
        val anteriores = c.buscarTodos(
            """SELECT numero FROM nota_devolucao
                WHERE (nota_fiscal_origem_id IS NOT NULL AND nota_fiscal_origem_id = ?)
                   OR (origem_chave IS NOT NULL AND origem_chave = ?)
                ORDER BY id""",
            o.notaId ?: -1L, o.chave ?: "",
        ) { it.getString(1) }
        if (anteriores.isNotEmpty()) {
            avisos += "A NF de origem ${o.numero} já tem devolução: NF ${anteriores.joinToString()}"
        }
        return avisos
    }

    /** O número da NF-e fica nas posições 26 a 34 da chave de acesso. */
    private fun referenciaDe(c: Connection, chave: String): ReferenciaOrigem =
        c.buscarUm("SELECT id, numero, dest_nome FROM nota_fiscal WHERE chave = ?", chave) {
            ReferenciaOrigem(chave, it.getString(2), it.getLong(1), it.getString(3))
        } ?: ReferenciaOrigem(chave, chave.substring(25, 34).trimStart('0').ifEmpty { "0" }, null, null)

    private companion object {
        val CABECALHO_PLANILHA = listOf(
            "NF devolução", "Cliente", "NF de origem", "Item", "Produto", "Descrição", "Cód. do cliente",
            "Descrição do cliente", "Esperado (un)", "Conferido (un)", "Falta (un)", "Situação do item", "Motivo",
            "Justificativa", "Situação da NF", "Importada em", "Bairro", "Cidade",
        )
        val LARGURAS_PLANILHA = listOf(12, 30, 12, 6, 10, 40, 14, 40, 12, 12, 10, 18, 30, 40, 16, 16, 20, 20)

        const val ATUALIZAR_PALLET =
            "UPDATE pallet SET nota_devolucao_id = ?, usuario_atual_id = ?, " +
                "status = CASE WHEN status = 'CRIADO' THEN 'EM_USO' ELSE status END WHERE id = ?"
    }

    private data class ItemConferencia(
        val id: Long, val produtoId: Long, val esperada: Int, val conferida: Int,
        val status: String, val unidadesPorCaixa: Int?, val devolucaoId: Long, val statusNota: String,
    )

    private data class PalletDev(
        val id: Long, val tipo: String, val status: String, val notaDevolucaoId: Long?,
    )

    private data class Quadra(
        val esperada: Int, val conferida: Int, val status: String, val devolucaoId: Long,
    )
}
