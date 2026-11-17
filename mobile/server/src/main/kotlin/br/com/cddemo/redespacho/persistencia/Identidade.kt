package br.com.cddemo.redespacho.persistencia

import java.sql.Connection
import java.time.Instant
import java.util.UUID

/**
 * Quem é este banco, por qual caminho ele veio e quanto já andou.
 *
 * - **linhagem**: sorteada quando o banco nasce e levada junto em toda cópia e
 *   restauração. Linhagens diferentes são bancos diferentes — o cenário de dois
 *   servidores, em que não existe "o mais novo" certo.
 * - **ramo**: sorteado quando o banco nasce e **a cada restauração**, e
 *   registrado em `banco_ramo` com o ramo pai e a geração do pai no ponto em
 *   que nasceu. É o que separa "outra máquina continuou este banco depois de
 *   mim" (a cópia descende do meu estado atual) de "outra máquina continuou a
 *   partir de um ponto anterior ao meu" — bifurcação, em que cada lado tem
 *   operações que o outro não tem. Comparar só gerações escondia a segunda.
 * - **geração**: sobe a cada transação que grava ([Db.transacao]).
 *
 * Ver [Restauracao].
 */
data class Identidade(
    val linhagem: String,
    val ramo: String,
    val geracao: Long,
    /** Última transação que gravou (Instant). Null se nada gravou desde a criação. */
    val alteradoEm: String?,
) {

    /** Os 8 primeiros caracteres: o que vai no nome da cópia de backup. */
    val linhagemCurta: String get() = curto(linhagem)
    val ramoCurto: String get() = curto(ramo)

    companion object {
        fun curto(id: String): String = id.take(8).lowercase()

        /** Null em banco anterior à migração 4, que ainda não tem identidade. */
        fun ler(c: Connection): Identidade? = runCatching {
            c.buscarUm("SELECT linhagem, ramo, geracao, alterado_em FROM banco_identidade WHERE id = 1") {
                Identidade(it.getString(1), it.getString(2), it.getLong(3), it.getString(4))
            }
        }.getOrNull()

        /** Árvore de ramos: ramo curto → (pai curto, geração do pai quando o ramo nasceu). */
        fun historico(c: Connection): Map<String, Pair<String?, Long>> = runCatching {
            c.buscarTodos("SELECT ramo, ramo_pai, geracao_base FROM banco_ramo") {
                curto(it.getString(1)) to (it.getString(2)?.let(::curto) to it.getLong(3))
            }.toMap()
        }.getOrDefault(emptyMap())

        /**
         * O estado (ramo, geração) de um banco inclui o estado (ramoAlvo, geracaoAlvo)?
         *
         * Sobe a árvore a partir do ramo do banco: achando o ramo alvo, inclui
         * se chegou nele numa geração igual ou maior que a do alvo. Um ramo que
         * nasceu do alvo antes de o alvo chegar onde está não inclui — é a
         * bifurcação.
         */
        fun contem(
            historico: Map<String, Pair<String?, Long>>,
            ramo: String,
            geracao: Long,
            ramoAlvo: String,
            geracaoAlvo: Long,
        ): Boolean {
            val alvo = curto(ramoAlvo)
            var r: String? = curto(ramo)
            var g = geracao
            val vistos = mutableSetOf<String>()
            while (r != null && vistos.add(r)) {
                if (r == alvo) return g >= geracaoAlvo
                val (pai, base) = historico[r] ?: return false
                r = pai
                g = base
            }
            return false
        }

        /**
         * Banco recém-restaurado: o **primeiro trabalho** daqui em diante abre
         * um ramo novo ([abrirRamoSePendente], chamado por [Db.transacao]).
         *
         * Não abre agora: uma restauração sem trabalho nenhum depois (a máquina
         * subiu e parou) criaria um ramo irmão com exatamente o mesmo conteúdo
         * da origem. A cópia dele apareceria como bifurcação falsa e ainda
         * impediria restaurar, mais tarde, a cópia legítima mais nova da origem.
         */
        fun marcarRamoPendente(c: Connection) {
            c.atualizar("UPDATE banco_identidade SET ramo_pendente = 1 WHERE id = 1")
        }

        /**
         * Abre o ramo marcado por [marcarRamoPendente], com base na geração de
         * agora — dentro da transação que está gravando, antes de ela contar.
         */
        internal fun abrirRamoSePendente(c: Connection, maquina: String?) {
            val (ramo, geracao, pendente) = c.buscarUm(
                "SELECT ramo, geracao, ramo_pendente FROM banco_identidade WHERE id = 1",
            ) { Triple(it.getString(1), it.getLong(2), it.getInt(3) == 1) } ?: return
            if (!pendente) return
            val novo = UUID.randomUUID().toString()
            c.atualizar(
                "INSERT INTO banco_ramo (ramo, ramo_pai, geracao_base, criado_em, maquina) VALUES (?,?,?,?,?)",
                novo, ramo, geracao, Instant.now().toString(), maquina,
            )
            c.atualizar("UPDATE banco_identidade SET ramo = ?, ramo_pendente = 0 WHERE id = 1", novo)
        }
    }
}
