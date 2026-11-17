package br.com.cddemo.redespacho.servico

import br.com.cddemo.redespacho.api.Erros
import com.fasterxml.jackson.annotation.JsonIgnore
import com.fasterxml.jackson.databind.DeserializationFeature
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.module.kotlin.registerKotlinModule
import org.slf4j.LoggerFactory
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.time.Instant

data class VersaoApp(
    val versao: String = "",
    val obrigatoria: Boolean = false,
    val tamanho: Long = 0,
    val enviadoEm: String = "",
    val enviadoPor: String = "",
    val url: String = "/app/apk",
) {
    /**
     * Derivado: não vai para o arquivo. Sem isto o Jackson o grava e depois
     * falha ao ler de volta, porque não existe onde atribuí-lo.
     */
    @get:JsonIgnore
    val disponivel: Boolean get() = versao.isNotBlank()
}

/**
 * A mesma comparação que o coletor faz para decidir atualizar: número a
 * número, e não como texto ("0.10.0" é mais nova que "0.9.0"). Trecho que não
 * é número conta como zero.
 */
internal fun versaoMaisNova(candidata: String, atual: String): Boolean {
    val a = candidata.trim().split('.', '-').map { it.toIntOrNull() ?: 0 }
    val b = atual.trim().split('.', '-').map { it.toIntOrNull() ?: 0 }
    for (i in 0 until maxOf(a.size, b.size)) {
        val x = a.getOrElse(i) { 0 }
        val y = b.getOrElse(i) { 0 }
        if (x != y) return x > y
    }
    return false
}

/**
 * Distribuição do APK pela própria rede do CD.
 *
 * Os coletores não têm internet, então não há loja de aplicativos. O servidor
 * guarda o APK e o entrega por HTTP — que é o que o StageNow da Zebra sabe
 * baixar (ele fala HTTP/HTTPS/FTP, não SMB) e o que o próprio app usa para se
 * atualizar. Ver docs/OPERACAO.md §3.
 */
class AtualizacaoServico(private val pastaDados: Path) {

    private val pasta: Path = pastaDados.resolve("apk")
    private val arquivo: Path get() = pasta.resolve("app.apk")
    private val metadados: Path get() = pasta.resolve("versao.json")
    private val json = ObjectMapper().registerKotlinModule()
        // Arquivos gravados por versões anteriores podem ter campos que não
        // existem mais; ignorar é melhor que perder a versão publicada.
        .configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, false)

    fun versao(): VersaoApp {
        if (!Files.exists(metadados) || !Files.exists(arquivo)) return VersaoApp()
        return runCatching {
            json.readValue(Files.readString(metadados), VersaoApp::class.java)
        }.getOrElse {
            // Não fica em silêncio: APK publicado que "some" da tela é
            // exatamente o tipo de falha que ninguém investiga.
            LoggerFactory.getLogger(AtualizacaoServico::class.java)
                .warn("Metadados do APK ilegíveis em {}: {}", metadados, it.message)
            VersaoApp()
        }
    }

    fun apk(): Path {
        if (!Files.exists(arquivo)) {
            throw Erros.naoEncontrado("APK — nenhuma versão publicada")
        }
        return arquivo
    }

    /**
     * Adota o APK que veio dentro do pacote: na primeira subida e em toda
     * atualização que traga versão mais nova que a publicada.
     *
     * O ZIP entregue no CD já traz o APK em `coletor/`. Sem isto alguém teria
     * de abrir a aba Coletores e publicá-lo à mão antes de qualquer aparelho
     * conseguir baixar — passo fácil de esquecer, e que só aparece quando o
     * separador está com o coletor na mão.
     *
     * Antes adotava só na primeira subida. Com a versão da instalação já no
     * ar, o APK das atualizações seguintes ficava parado em `coletor/`: o
     * servidor seguia entregando o antigo (no aviso do login e no QR), e
     * nenhum coletor saía da versão com que o CD foi instalado.
     *
     * Nunca rebaixa: APK mais novo publicado à mão pelo admin continua valendo.
     */
    fun adotarApkDoPacote(pastaColetor: Path) {
        val log = LoggerFactory.getLogger(AtualizacaoServico::class.java)
        val apkDoPacote = pastaColetor.resolve("separacao-redespacho.apk")
        val versaoTxt = pastaColetor.resolve("versao.txt")
        if (!Files.exists(apkDoPacote)) return

        val publicada = versao()
        val versao = runCatching { Files.readString(versaoTxt).trim() }
            .getOrNull()
            ?.takeIf { it.isNotBlank() }
            ?: run {
                log.warn("APK em {} sem versao.txt legível; publique pela aba Coletores.", pastaColetor)
                return
            }
        if (publicada.disponivel && !versaoMaisNova(versao, publicada.versao)) return

        runCatching {
            publicar(Files.readAllBytes(apkDoPacote), versao, false, "pacote de instalação")
        }.onSuccess {
            if (publicada.disponivel) {
                log.info(
                    "APK {} do pacote substitui o {} publicado; coletores atualizam no próximo login.",
                    versao, publicada.versao,
                )
            } else {
                log.info("APK {} publicado a partir do pacote; coletores já podem baixar.", versao)
            }
        }.onFailure {
            log.warn("Não foi possível publicar o APK do pacote: {}", it.message)
        }
    }

    fun publicar(
        bytes: ByteArray,
        versao: String,
        obrigatoria: Boolean,
        usuario: String,
    ): VersaoApp {
        if (versao.isBlank()) {
            throw Erros.invalido("VERSAO_OBRIGATORIA", "Informe a versão do APK")
        }
        // Todo APK é um ZIP e começa com "PK". Barra arquivo trocado por engano
        // antes de ele virar download para dez coletores.
        if (bytes.size < 4 || bytes[0] != 'P'.code.toByte() || bytes[1] != 'K'.code.toByte()) {
            throw Erros.invalido("ARQUIVO_INVALIDO", "O arquivo enviado não parece um APK")
        }

        // Republicar o mesmo número é pior do que não publicar: o coletor decide
        // atualizar comparando a versão, então o APK novo fica no servidor e
        // nenhum aparelho o busca — e ninguém descobre até alguém reparar que o
        // comportamento antigo continua lá.
        val atual = versao()
        if (atual.disponivel && atual.versao == versao.trim()) {
            throw Erros.conflito(
                "VERSAO_JA_PUBLICADA",
                "A versão ${atual.versao} já está publicada. Os coletores comparam o " +
                    "número para decidir se atualizam, então republicar o mesmo não " +
                    "chega a aparelho nenhum. Suba o número no app antes de publicar.",
                mapOf("versaoPublicada" to atual.versao),
            )
        }

        Files.createDirectories(pasta)
        val temporario = pasta.resolve("app.apk.tmp")
        Files.write(temporario, bytes)
        Files.move(temporario, arquivo, StandardCopyOption.REPLACE_EXISTING)

        val v = VersaoApp(
            versao = versao.trim(),
            obrigatoria = obrigatoria,
            tamanho = bytes.size.toLong(),
            enviadoEm = Instant.now().toString(),
            enviadoPor = usuario,
        )
        Files.writeString(metadados, json.writeValueAsString(v))
        return v
    }
}
