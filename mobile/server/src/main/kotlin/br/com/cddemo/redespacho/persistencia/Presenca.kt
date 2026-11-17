package br.com.cddemo.redespacho.persistencia

import br.com.cddemo.redespacho.Rede

import org.slf4j.LoggerFactory
import java.net.InetAddress
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths
import java.nio.file.StandardCopyOption
import java.time.Instant
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Sinal de vida na pasta de rede: diz que já existe um servidor no ar e onde.
 *
 * É o que impede o pior cenário do projeto — duas máquinas rodando o servidor
 * ao mesmo tempo, cada uma com seu banco, gerando `RED-000001` para conteúdos
 * diferentes. O `iniciar.bat` lê este arquivo antes de subir: achando um sinal
 * recente de outra máquina, abre o navegador nela em vez de criar um segundo
 * servidor.
 *
 * O arquivo é sinalização, não dado: some sem prejuízo nenhum.
 */
class Presenca(
    private val destino: Path?,
    private val porta: Int,
) {

    private val log = LoggerFactory.getLogger(Presenca::class.java)
    private val agenda = Executors.newSingleThreadScheduledExecutor { r ->
        Thread(r, "presenca").apply { isDaemon = true }
    }

    private val maquina: String = runCatching { InetAddress.getLocalHost().hostName }
        .getOrNull() ?: System.getenv("COMPUTERNAME") ?: "desconhecida"

    fun iniciar() {
        if (destino == null) return
        agenda.scheduleAtFixedRate({ escreverComSeguranca() }, 0, 60, TimeUnit.SECONDS)
        log.info("Sinal de presença em {} (máquina {})", destino, maquina)
    }

    fun encerrar() {
        // Espera a gravação em andamento: apagado no meio dela, o arquivo voltaria.
        agenda.shutdown()
        runCatching { agenda.awaitTermination(10, TimeUnit.SECONDS) }
        // Apaga ao desligar: outra máquina pode assumir imediatamente, sem
        // esperar o sinal expirar. Quem chama garante que é o último passo,
        // depois da cópia final do backup (Aplicacao.kt, Desligamento).
        runCatching { destino?.let { Files.deleteIfExists(it) } }
    }

    private fun escreverComSeguranca() {
        try {
            escrever()
        } catch (e: Exception) {
            // Pasta de rede fora do ar não pode derrubar o servidor.
            log.debug("Não foi possível gravar o sinal de presença: {}", e.message)
        }
    }

    private fun escrever() {
        val arquivo = destino ?: return
        Files.createDirectories(arquivo.parent)
        val conteudo = buildString {
            appendLine("# Sinal de que existe um servidor no ar. Nao edite.")
            appendLine("maquina=$maquina")
            appendLine("url=http://${Rede.enderecoLocal()}:$porta/")
            appendLine("em=${Instant.now()}")
        }
        // Grava em temporário e move: quem estiver lendo nunca pega meio arquivo.
        val temporario = arquivo.resolveSibling("${arquivo.fileName}.tmp")
        Files.writeString(temporario, conteudo)
        Files.move(temporario, arquivo, StandardCopyOption.REPLACE_EXISTING)
    }

    companion object {
        fun de(porta: Int): Presenca {
            val caminho = System.getenv("REDESPACHO_PRESENCA")
                ?.takeIf { it.isNotBlank() }
                ?.let { runCatching { Paths.get(it) }.getOrNull() }
            return Presenca(caminho, porta)
        }
    }
}
