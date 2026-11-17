package br.com.cddemo.redespacho.ui

import br.com.cddemo.redespacho.rede.ConsultaPallet
import br.com.cddemo.redespacho.rede.PalletConsultado
import br.com.cddemo.redespacho.rede.ROTA_AUSENTE

/**
 * Regras da tela "Consultar pallet" (Tela.CONSULTA_PALLET), sem Android: o que
 * cada pallet mostra como lugar e situação. Separadas da tela para caberem num
 * teste de unidade — "expedido" e "sem endereço" mandam o operador procurar em
 * lugares diferentes, e trocar um pelo outro é mandá-lo andar à toa.
 */

/** O bipado. O servidor manda primeiro, mas é a marca que vale. */
internal val ConsultaPallet.bipado: PalletConsultado?
    get() = pallets.firstOrNull { it.consultado } ?: pallets.firstOrNull()

/** Os demais pallets da mesma NF, na ordem do servidor. */
internal val ConsultaPallet.outros: List<PalletConsultado>
    get() {
        val b = bipado ?: return emptyList()
        return pallets.filter { it !== b }
    }

/** Onde procurar o pallet. Expedido não tem endereço: já saiu do armazém. */
internal fun localDoPallet(p: PalletConsultado): String = when {
    p.status == "EXPEDIDO" -> "expedido"
    p.endereco != null -> p.endereco
    else -> "sem endereço"
}

/** A situação do pallet em palavras do chão, não o código do banco. */
internal fun situacaoDoPallet(status: String): String = when (status) {
    "CRIADO" -> "novo, sem uso"
    "EM_USO" -> "em uso"
    "ENCERRADO" -> "encerrado"
    "ENDERECADO" -> "endereçado"
    "EXPEDIDO" -> "expedido"
    "FUNDIDO" -> "juntado a outro"
    "ESVAZIADO" -> "esvaziado"
    else -> status.replace('_', ' ').lowercase()
}

/** A situação da NF (de separação ou de devolução). */
internal fun situacaoDaNota(status: String): String = when (status) {
    "PENDENTE" -> "pendente"
    "EM_SEPARACAO" -> "em separação"
    "CONCLUIDA" -> "concluída"
    "EXPEDIDA" -> "expedida"
    "RETORNO_PARCIAL" -> "com retorno parcial"
    "AGUARDANDO_CONFERENCIA" -> "aguardando conferência"
    "EM_CONFERENCIA" -> "em conferência"
    "CONFERIDA" -> "conferida"
    "BAIXADA" -> "baixada"
    else -> status.replace('_', ' ').lowercase()
}

/** A linha miúda sob o lugar: depósito e situação. "Expedido" já está no lugar e não se repete. */
internal fun detalheDoPallet(p: PalletConsultado): String =
    listOfNotNull(
        p.deposito?.takeIf { it.isNotBlank() },
        situacaoDoPallet(p.status).takeIf { p.status != "EXPEDIDO" },
    ).joinToString(" · ")

/** "NF 952918 · ATACADAO S A", ou o aviso de que o pallet ainda não tem NF. */
internal fun textoDaNota(c: ConsultaPallet): String {
    val nf = c.notaNumero ?: return "Sem NF ainda: nenhuma caixa bipada neste pallet"
    return "NF $nf" + (c.cliente?.takeIf { it.isNotBlank() }?.let { " · $it" } ?: "")
}

/** "12 cx", "30 un": devolução conta unidade, separação conta caixa. */
internal fun quantidadeComUnidade(quantidade: Int, unidade: String): String = "$quantidade $unidade"

internal fun nomeDaUnidade(unidade: String): String = if (unidade == "un") "unidades" else "caixas"

/**
 * Resumo dos outros pallets com a seção recolhida: diz de relance se o resto
 * da NF está no armazém, antes de abrir a lista.
 */
internal fun resumoDosOutros(outros: List<PalletConsultado>): String {
    val expedidos = outros.count { it.status == "EXPEDIDO" }
    val enderecados = outros.count { it.status != "EXPEDIDO" && it.endereco != null }
    val semEndereco = outros.size - expedidos - enderecados
    return listOfNotNull(
        enderecados.takeIf { it > 0 }?.let { "$it endereçado(s)" },
        semEndereco.takeIf { it > 0 }?.let { "$it sem endereço" },
        expedidos.takeIf { it > 0 }?.let { "$it expedido(s)" },
    ).joinToString(" · ")
}

/**
 * O que dizer quando a consulta volta recusada. Servidor sem a rota ganha
 * texto próprio: "falha HTTP 404" não diz ao operador o que fazer.
 */
internal fun mensagemDaConsultaRecusada(codigo: String, mensagem: String): String = when (codigo) {
    ROTA_AUSENTE, "ERRO_HTTP_404" ->
        "Este servidor ainda não tem a consulta de pallet. Peça à retaguarda a atualização do servidor."
    else -> mensagem
}
