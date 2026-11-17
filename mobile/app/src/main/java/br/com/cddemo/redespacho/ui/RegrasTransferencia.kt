package br.com.cddemo.redespacho.ui

import br.com.cddemo.redespacho.rede.ConclusaoRepaletizacao
import br.com.cddemo.redespacho.rede.Repaletizacao

/**
 * Regras da tela "Repaletização" (Tela.TRANSFERENCIA), sem Android: o que um
 * bipe faz e o que dizer ao operador. Separadas do ViewModel para caberem num
 * teste de unidade — é aqui que um bipe no passo errado moveria carga.
 */

/** O prefixo distingue pallet de box e de código de produto. */
internal fun ehCodigoDePallet(codigo: String): Boolean =
    codigo.trim().uppercase().let { it.startsWith("P-RED-") || it.startsWith("P-DEV-") }

/** Em que passo a tela está: decide o texto, o rótulo da digitação e o bipe. */
enum class PassoTransferencia { ORIGEM, DESTINO, CAIXAS }

val EstadoApp.passoTransferencia: PassoTransferencia
    get() = when {
        repaletizacao != null -> PassoTransferencia.CAIXAS
        palletOrigem == null -> PassoTransferencia.ORIGEM
        else -> PassoTransferencia.DESTINO
    }

/** O que um bipe faz na tela, decidido pelo estado e pelo tipo de código lido. */
enum class AcaoTransferencia {
    /** O pallet lido vira a origem. */
    CARREGAR_ORIGEM,
    /** Esperava a etiqueta de um pallet e veio outra coisa. */
    PEDIR_PALLET,
    /** Bipou como destino o próprio pallet de origem. */
    MESMO_PALLET,
    /** Interruptor ligado: todas as caixas da origem vão para o destino. */
    FUNDIR,
    /** Interruptor desligado: confere o par para passar caixa a caixa. */
    ABRIR_REPALETIZACAO,
    PASSAR_CAIXA,
    /** A origem já ficou vazia: não há mais caixa a passar. */
    ORIGEM_ESVAZIADA,
    /** Etiqueta de pallet no passo das caixas: nada troca antes do Concluir. */
    CONCLUIR_ANTES,
}

/**
 * Um bipe só, vários significados, decididos pelo que já está na tela.
 *
 * Com o par aberto, só caixa conta — etiqueta de pallet não troca nada, senão
 * um bipe distraído deixaria os cartões dos dois pallets mentindo sem ninguém
 * mandar imprimir. Origem vazia na repaletização não tem caixa para dar: o
 * próximo pallet bipado a substitui em vez de virar destino.
 */
fun acaoNaTransferencia(e: EstadoApp, codigo: String): AcaoTransferencia {
    val pallet = ehCodigoDePallet(codigo)
    val origem = e.palletOrigem
    val par = e.repaletizacao
    return when {
        par != null -> when {
            pallet -> AcaoTransferencia.CONCLUIR_ANTES
            par.origemEsvaziada -> AcaoTransferencia.ORIGEM_ESVAZIADA
            else -> AcaoTransferencia.PASSAR_CAIXA
        }
        !pallet -> AcaoTransferencia.PEDIR_PALLET
        origem == null -> AcaoTransferencia.CARREGAR_ORIGEM
        origem.codigo.equals(codigo.trim(), ignoreCase = true) -> AcaoTransferencia.MESMO_PALLET
        e.unirArmado -> AcaoTransferencia.FUNDIR
        origem.totalCaixas == 0 -> AcaoTransferencia.CARREGAR_ORIGEM
        else -> AcaoTransferencia.ABRIR_REPALETIZACAO
    }
}

/**
 * Caixa de repaletização que foi e não voltou resposta: pode ter passado ou
 * não. Leva o par dela — o reenvio vai para onde a caixa foi bipada — e a
 * chave: com a mesma chave, se a primeira chegou, o servidor devolve a
 * situação em vez de passar outra caixa.
 */
data class CaixaRepaletizacaoSemResposta(
    val origem: String,
    val destino: String,
    val codigoLido: String,
    val idempotencyKey: String,
    val lidoEm: String,
)

/** Por onde o operador tentou sair com a repaletização por imprimir. */
enum class SaidaRepaletizacao { VOLTAR, TROCAR_ORIGEM }

internal fun Repaletizacao.mesmoPar(origem: String, destino: String): Boolean =
    this.origem.codigo.equals(origem, ignoreCase = true) && this.destino.codigo.equals(destino, ignoreCase = true)

internal fun Repaletizacao.mesmoPar(outra: Repaletizacao): Boolean = mesmoPar(outra.origem.codigo, outra.destino.codigo)

/**
 * O par aberto mudou o conteúdo dos pallets e os cartões ainda não saíram.
 * Sair assim deixa no palete um cartão que mente sobre a quantidade.
 */
val EstadoApp.repaletizacaoPorImprimir: Boolean
    get() = repaletizacao != null &&
        (repaletizacao.movidas > 0 || cartoesDesatualizados || caixaRepaletizacaoSemResposta != null)

/** O que a impressora disse de um cartão, em palavras do operador. */
internal fun motivoDoCartao(situacao: String): String = when (situacao) {
    "SEM_IMPRESSORA" -> "nenhuma impressora cadastrada para o seu usuário"
    // O cartão lista os itens da NF: sem conteúdo não há o que imprimir.
    "SEM_CONTEUDO" -> "o pallet não tem conteúdo para o cartão"
    else -> "a impressora não aceitou o trabalho (veja se está ligada)"
}

/**
 * Textos do fim da repaletização: o que deu certo (aviso verde) e o que não
 * saiu na impressora (aviso vermelho). Cartão que não saiu precisa aparecer
 * separado — o operador ficaria esperando um papel que não vem.
 */
internal fun textosDaConclusao(c: ConclusaoRepaletizacao): Pair<String, String?> {
    val r = c.repaletizacao
    val o = r.origem.codigo
    val d = r.destino.codigo
    val saiu = c.impressao.filterValues { it == "ENVIADA" }.keys
    val naoSaiu = c.impressao.filterValues { it != "ENVIADA" }

    val ok = buildString {
        append("Repaletização concluída: ${r.movidas} caixa(s) passadas de $o para $d")
        append(" · $o com ${r.origem.totalCaixas} cx, $d com ${r.destino.totalCaixas} cx.")
        if (saiu.isNotEmpty()) append(" Cartão enviado à impressora: ${saiu.joinToString()}.")
        if (r.origemEsvaziada) append(" $o ficou vazio e saiu de circulação: descarte o cartão dele.")
    }
    val falhas = naoSaiu.takeIf { it.isNotEmpty() }?.entries?.joinToString("\n") { (pallet, situacao) ->
        "Cartão de $pallet NÃO saiu: ${motivoDoCartao(situacao)}. Use Reimprimir › Cartão."
    }
    return ok to falhas
}
