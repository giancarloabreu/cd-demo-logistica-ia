package br.com.cddemo.redespacho.coletor

import android.content.Context
import android.content.Intent
import android.os.Bundle

/**
 * Provisiona o perfil do DataWedge por intent, na primeira execução.
 *
 * Sem isto seria preciso abrir o DataWedge e configurar à mão em cada coletor —
 * trabalho repetido e fonte de aparelho configurado errado. Em aparelho que não
 * é Zebra os intents simplesmente não são recebidos por ninguém, sem efeito.
 *
 * Ver docs/OPERACAO.md §6.
 */
object DataWedge {

    private const val ACTION_API = "com.symbol.datawedge.api.ACTION"
    private const val EXTRA_SET_CONFIG = "com.symbol.datawedge.api.SET_CONFIG"
    private const val PERFIL = "SeparacaoRedespacho"

    fun provisionar(context: Context) {
        val principal = Bundle().apply {
            putString("PROFILE_NAME", PERFIL)
            putString("PROFILE_ENABLED", "true")
            // CREATE_IF_NOT_EXIST, não OVERWRITE: OVERWRITE só reconfigura um
            // perfil que JÁ existe — no aparelho novo ele falha em silêncio, e o
            // gatilho fica mudo sem nenhum erro na tela. Foi exatamente isso que
            // aconteceu no primeiro teste em campo. CREATE_IF_NOT_EXIST cria
            // quando falta e atualiza quando já está lá.
            putString("CONFIG_MODE", "CREATE_IF_NOT_EXIST")

            putParcelableArray(
                "APP_LIST",
                arrayOf(
                    Bundle().apply {
                        putString("PACKAGE_NAME", context.packageName)
                        putStringArray("ACTIVITY_LIST", arrayOf("*"))
                    }
                )
            )

            putParcelableArrayList(
                "PLUGIN_CONFIG",
                arrayListOf(configBarcode(), configIntent(), configKeystroke()),
            )
        }

        context.sendBroadcast(
            Intent().apply {
                action = ACTION_API
                putExtra(EXTRA_SET_CONFIG, principal)
                // Necessário no Android 8+: sem isto o DataWedge não recebe.
                setPackage("com.symbol.datawedge")
            }
        )
    }

    /** As caixas usam Code 128 simples. Ver docs/MODELO-DE-DADOS.md §5.1. */
    private fun configBarcode() = Bundle().apply {
        putString("PLUGIN_NAME", "BARCODE")
        putString("RESET_CONFIG", "true")
        putBundle(
            "PARAM_LIST",
            Bundle().apply {
                putString("scanner_selection", "auto")
                putString("scanner_input_enabled", "true")
                putString("decoder_code128", "true")
                putString("decoder_ean13", "true")
                // ITF-14 fica ligado como rede de segurança: se um fornecedor
                // mudar a etiqueta, o coletor lê em vez de ficar mudo.
                putString("decoder_i2of5", "true")
                putString("decoder_i2of5_length1", "14")
                putString("decoder_i2of5_length2", "0")
            }
        )
    }

    private fun configIntent() = Bundle().apply {
        putString("PLUGIN_NAME", "INTENT")
        putString("RESET_CONFIG", "true")
        putBundle(
            "PARAM_LIST",
            Bundle().apply {
                putString("intent_output_enabled", "true")
                putString("intent_action", Scanner.ACTION_SCAN)
                putString("intent_delivery", "2") // 2 = broadcast
            }
        )
    }

    /**
     * Keystroke desligado de propósito: com ele a leitura vira digitação no
     * campo com foco, que é justamente o modo frágil que queremos evitar.
     */
    private fun configKeystroke() = Bundle().apply {
        putString("PLUGIN_NAME", "KEYSTROKE")
        putString("RESET_CONFIG", "true")
        putBundle(
            "PARAM_LIST",
            Bundle().apply { putString("keystroke_output_enabled", "false") }
        )
    }
}
