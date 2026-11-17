package br.com.cddemo.redespacho.ui

import br.com.cddemo.redespacho.rede.Box
import br.com.cddemo.redespacho.rede.Posicao

/*
 * Regras da "Conferência de box" (Tela.CONFERENCIA_BOX), sem Android, para
 * caberem num teste de unidade. Um nível pode ter mais de um pallet —
 * bonificação do mesmo CNPJ divide a posição — e nível vazio aparece em branco.
 */

/** Um nível do box com o que está nele; [vazio] quando não tem pallet. */
internal data class NivelDoBox(val nivel: Int, val pallets: List<Posicao>) {
    val vazio: Boolean get() = pallets.isEmpty()
}

/** Do nível mais alto para o mais baixo, como o prédio se vê de frente. */
internal fun niveisDoBox(box: Box): List<NivelDoBox> =
    box.posicoes.groupBy { it.nivel }
        .map { (nivel, posicoes) -> NivelDoBox(nivel, posicoes.filter { !it.livre }) }
        .sortedByDescending { it.nivel }

/** "3 de 6 nível(is) ocupado(s)". */
internal fun ocupacaoDoBox(niveis: List<NivelDoBox>): String =
    "${niveis.count { !it.vazio }} de ${niveis.size} nível(is) ocupado(s)"

/** "12 cx"; o pallet de devolução conta em unidades, como a conferência dele. */
internal fun quantidadeDaPosicao(p: Posicao): String =
    "${p.qtdCaixas ?: 0} ${if (p.tipoPallet == "DEVOLUCAO") "un" else "cx"}"

/**
 * O box de um endereço "C8-D28 nível 3". Servidor anterior à 0.7.2 não manda
 * o box separado na consulta de pallet, e o endereço tem o mesmo dado.
 */
internal fun boxDoEndereco(endereco: String?): String? =
    endereco?.takeIf { " nível " in it }?.substringBefore(" nível ")?.trim()?.takeIf { it.isNotEmpty() }
