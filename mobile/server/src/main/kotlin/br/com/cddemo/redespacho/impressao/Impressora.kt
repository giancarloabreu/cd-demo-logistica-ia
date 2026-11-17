package br.com.cddemo.redespacho.impressao

import java.io.BufferedReader
import java.net.InetSocketAddress
import java.net.Socket
import java.nio.charset.Charset

/**
 * Comunicação com a Zebra por socket TCP na porta 9100.
 *
 * A porta 9100 aceita o job sem confirmar que o papel saiu. Por isso o
 * resultado aqui diz apenas se a impressora recebeu — e toda rota que imprime
 * tem contraparte de reimpressão. Ver docs/API.md §6.
 */
object Impressora {

    private const val TIMEOUT_MS = 4000
    private val CP850: Charset = Charset.forName("CP850")

    sealed interface Resultado {
        data object Enviada : Resultado
        data class Falhou(val motivo: String) : Resultado
    }

    fun enviar(ip: String, porta: Int, zpl: String): Resultado = try {
        Socket().use { s ->
            s.connect(InetSocketAddress(ip, porta), TIMEOUT_MS)
            s.soTimeout = TIMEOUT_MS
            // ^CI28 no ZPL põe a impressora em UTF-8; enviamos os bytes assim.
            s.getOutputStream().apply {
                write(zpl.toByteArray(Charsets.UTF_8))
                flush()
            }
        }
        Resultado.Enviada
    } catch (e: Exception) {
        Resultado.Falhou(e.message ?: e.javaClass.simpleName)
    }

    /**
     * Pergunta a resolução por SGD. Resolve na hora a dúvida de 203 × 300 dpi
     * e continua valendo se a impressora for trocada depois.
     */
    fun detectarDpi(ip: String, porta: Int): Int? = try {
        Socket().use { s ->
            s.connect(InetSocketAddress(ip, porta), TIMEOUT_MS)
            s.soTimeout = TIMEOUT_MS
            s.getOutputStream().apply {
                write("! U1 getvar \"head.resolution\"\r\n".toByteArray(CP850))
                flush()
            }
            val resposta = s.getInputStream()
                .bufferedReader(CP850)
                .let(BufferedReader::readLine)
                .orEmpty()
            // Vem como "203" ou "\"203\"" conforme o firmware.
            resposta.filter(Char::isDigit).toIntOrNull()?.takeIf { it in 100..1200 }
        }
    } catch (e: Exception) {
        null
    }
}
