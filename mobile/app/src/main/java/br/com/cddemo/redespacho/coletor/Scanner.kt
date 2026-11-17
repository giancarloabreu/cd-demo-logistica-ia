package br.com.cddemo.redespacho.coletor

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build

/**
 * Recebe a leitura do DataWedge por intent.
 *
 * Intent em vez de keyboard-wedge: o wedge depende de um campo com foco, e
 * qualquer toque fora faz a leitura se perder. Ver docs/TELAS-COLETOR.md §0.
 *
 * O perfil do DataWedge precisa estar configurado com Intent Output ligado,
 * action `br.com.cddemo.redespacho.SCAN` e delivery por broadcast.
 */
class Scanner(private val context: Context) {

    private var receiver: BroadcastReceiver? = null
    private var ultimoCodigo: String? = null
    private var ultimoEm: Long = 0

    fun registrar(aoLer: (String) -> Unit) {
        cancelar()
        val r = object : BroadcastReceiver() {
            override fun onReceive(c: Context?, intent: Intent?) {
                val codigo = intent?.getStringExtra(EXTRA_DADOS)
                    ?: intent?.getStringExtra(EXTRA_DADOS_ALT)
                    ?: return
                // Entrega sem filtrar. O portão do debounce é `aceitar`, e ele
                // GRAVA estado a cada chamada: chamando aqui e de novo no
                // ViewModel, a segunda via o mesmo código a ~0 ms e devolvia
                // false — toda leitura do gatilho morria em silêncio, sem bipe
                // e sem log. Digitar funcionava porque não passa por aqui.
                // Um portão só, no ViewModel, que é por onde os dois caminhos
                // (gatilho e digitação) passam.
                aoLer(limpar(codigo) ?: return)
            }
        }
        val filtro = IntentFilter(ACTION_SCAN)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            context.registerReceiver(r, filtro, Context.RECEIVER_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            context.registerReceiver(r, filtro)
        }
        receiver = r
    }

    fun cancelar() {
        receiver?.let { runCatching { context.unregisterReceiver(it) } }
        receiver = null
    }

    /**
     * Debounce: o gatilho do TC8300 dispara duas vezes com facilidade, ainda
     * mais com luva.
     *
     * **Chame exatamente uma vez por leitura.** Não é predicado puro: cada
     * chamada grava `ultimoCodigo`/`ultimoEm`, então chamar duas vezes para o
     * mesmo código faz a segunda recusar sempre. Sem isto cada duplo-disparo vira caixa fantasma — e como
     * o código é fixo por produto, o sistema não teria como distinguir.
     * Ver docs/TELAS-COLETOR.md §5.1.
     */
    fun aceitar(codigo: String): Boolean {
        val agora = System.currentTimeMillis()
        if (codigo == ultimoCodigo && agora - ultimoEm < JANELA_DEBOUNCE_MS) return false
        ultimoCodigo = codigo
        ultimoEm = agora
        return true
    }

    companion object {
        /**
         * Tira Enter, Tab e qualquer outro caractere de controle da leitura.
         *
         * Na saída por intent o DataWedge não manda Enter — mas permite
         * configurar um sufixo nela, e com ele o código chegaria com um
         * Enter no fim. Aqui ele some antes de chegar ao ViewModel. Devolve
         * null se não sobrar nada, para um Enter solto não virar bipe.
         */
        fun limpar(bruto: String): String? =
            bruto.filterNot { it.isISOControl() }.trim().takeIf { it.isNotEmpty() }

        const val ACTION_SCAN = "br.com.cddemo.redespacho.SCAN"
        private const val EXTRA_DADOS = "com.symbol.datawedge.data_string"
        private const val EXTRA_DADOS_ALT = "com.motorolasolutions.emdk.datawedge.data_string"
        const val JANELA_DEBOUNCE_MS = 400L
    }
}
