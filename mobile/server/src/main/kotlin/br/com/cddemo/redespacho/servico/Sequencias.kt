package br.com.cddemo.redespacho.servico

import br.com.cddemo.redespacho.persistencia.atualizar
import br.com.cddemo.redespacho.persistencia.buscarUm
import java.sql.Connection

/**
 * Sequenciais não reaproveitáveis.
 *
 * Nunca decrementa, mesmo com exclusão de instrução ou pallet: buraco na
 * numeração é inofensivo, número repetido apontando para dois pallets
 * diferentes não. Ver docs/MODELO-DE-DADOS.md §2.1.
 */
object Sequencias {

    const val INSTRUCAO = "INSTRUCAO"
    const val PALLET = "PALLET"
    const val PALLET_DEVOLUCAO = "PALLET_DEVOLUCAO"

    /** Conferência de estoque: a sobra entra como devolução sem NF. */
    const val SOBRA = "SOBRA"

    /** Reserva `quantidade` números e devolve a faixa, já formatada. */
    fun proximos(c: Connection, nome: String, quantidade: Int = 1): List<String> {
        require(quantidade >= 1)
        val atual = c.buscarUm("SELECT valor FROM sequencia WHERE nome = ?", nome) { it.getInt(1) }
            ?: error("sequência desconhecida: $nome")
        c.atualizar("UPDATE sequencia SET valor = ? WHERE nome = ?", atual + quantidade, nome)
        return (atual + 1..atual + quantidade).map { formatar(nome, it) }
    }

    fun proximo(c: Connection, nome: String): String = proximos(c, nome, 1).first()

    private fun formatar(nome: String, valor: Int): String = when (nome) {
        INSTRUCAO -> "RED-%06d".format(valor)
        PALLET -> "P-RED-%06d".format(valor)
        PALLET_DEVOLUCAO -> "P-DEV-%06d".format(valor)
        SOBRA -> "SOBRA-%06d".format(valor)
        else -> error("sequência desconhecida: $nome")
    }
}
