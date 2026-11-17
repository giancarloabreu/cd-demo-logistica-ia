package br.com.cddemo.redespacho.seguranca

import java.security.SecureRandom
import java.time.Instant
import java.util.Base64
import java.util.concurrent.ConcurrentHashMap

/**
 * CONSULTA só enxerga: qualquer requisição que não seja GET é recusada, sem
 * depender de cada rota lembrar de se proteger. Ver Rotas.autenticar.
 */
enum class Perfil { CONSULTA, SEPARADOR, SUPERVISOR, ADMIN }

data class UsuarioAutenticado(
    val id: Long,
    val login: String,
    val nome: String,
    val perfil: Perfil,
    val multiItem: Boolean,
    val impressoraId: Long?,
)

data class Sessao(
    val usuario: UsuarioAutenticado,
    val dispositivoId: String?,
    val criadaEm: Instant,
    var ultimoUsoEm: Instant,
)

/**
 * Sessões em memória.
 *
 * **Sem expiração por inatividade.** O separador fica minutos sem tocar a tela
 * entre um pallet e outro; ser deslogado no meio da câmara custa tempo e
 * irrita. A sessão cai só no logout ou no reinício do servidor, e a rede é
 * local e fechada. Ver docs/OPERACAO.md §7.
 */
class Sessoes {

    private val random = SecureRandom()
    private val porToken = ConcurrentHashMap<String, Sessao>()

    fun abrir(usuario: UsuarioAutenticado, dispositivoId: String?): String {
        val token = ByteArray(32).also { random.nextBytes(it) }
            .let { Base64.getUrlEncoder().withoutPadding().encodeToString(it) }
        val agora = Instant.now()
        porToken[token] = Sessao(usuario, dispositivoId, agora, agora)
        return token
    }

    fun validar(token: String?): Sessao? {
        val s = porToken[token ?: return null] ?: return null
        s.ultimoUsoEm = Instant.now()
        return s
    }

    fun encerrar(token: String?) {
        porToken.remove(token ?: return)
    }

    /** Usada ao inativar usuário: derruba todas as sessões dele. */
    fun encerrarDoUsuario(usuarioId: Long) {
        porToken.entries.removeIf { it.value.usuario.id == usuarioId }
    }

    fun quantidade(): Int = porToken.size
}
