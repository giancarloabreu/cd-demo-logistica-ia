package br.com.cddemo.redespacho.coletor

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.core.content.FileProvider
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Atualização do app pela rede do CD.
 *
 * Os coletores não têm internet, então não há loja de aplicativos: o servidor
 * guarda o APK e o app o baixa. Sem isto, atualizar dez coletores é trabalho
 * manual e sempre sobra um esquecido rodando regra antiga.
 * Ver docs/OPERACAO.md §3.
 */
object Atualizador {

    /**
     * Compara versões no formato `1.2.3`. Trecho não numérico conta como zero,
     * então "1.2-beta" não trava a comparação.
     */
    fun ehMaisNova(disponivel: String, instalada: String): Boolean {
        val a = disponivel.split('.', '-').map { it.toIntOrNull() ?: 0 }
        val b = instalada.split('.', '-').map { it.toIntOrNull() ?: 0 }
        for (i in 0 until maxOf(a.size, b.size)) {
            val x = a.getOrElse(i) { 0 }
            val y = b.getOrElse(i) { 0 }
            if (x != y) return x > y
        }
        return false
    }

    /** Baixa para a área privada do app e devolve o arquivo. */
    fun baixar(contexto: Context, urlBase: String, aoProgredir: (Int) -> Unit = {}): File {
        val destino = File(contexto.cacheDir, "atualizacao.apk")
        val conexao = (URL("$urlBase/api/v1/app/apk").openConnection() as HttpURLConnection).apply {
            connectTimeout = 10_000
            readTimeout = 60_000
        }
        conexao.inputStream.use { entrada ->
            val total = conexao.contentLengthLong.takeIf { it > 0 } ?: -1
            destino.outputStream().use { saida ->
                val buffer = ByteArray(64 * 1024)
                var lidos = 0L
                while (true) {
                    val n = entrada.read(buffer)
                    if (n <= 0) break
                    saida.write(buffer, 0, n)
                    lidos += n
                    if (total > 0) aoProgredir(((lidos * 100) / total).toInt())
                }
            }
        }
        conexao.disconnect()
        return destino
    }

    /**
     * Abre o instalador do Android. O usuário confirma — instalação silenciosa
     * exigiria o app ser device owner, o que não vale a complexidade aqui.
     */
    fun instalar(contexto: Context, apk: File) {
        val uri: Uri = FileProvider.getUriForFile(
            contexto, "${contexto.packageName}.arquivos", apk,
        )
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        contexto.startActivity(intent)
    }
}
