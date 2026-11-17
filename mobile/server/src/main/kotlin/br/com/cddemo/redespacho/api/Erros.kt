package br.com.cddemo.redespacho.api

import io.ktor.http.HttpStatusCode

/**
 * Erro de negócio com código estável.
 *
 * O coletor decide som, diálogo e comportamento pelo `codigo`, nunca pelo
 * texto — mudar a redação de uma mensagem não pode quebrar o app.
 * Ver docs/API.md §5.
 */
class ErroNegocio(
    val codigo: String,
    val mensagemUsuario: String,
    val status: HttpStatusCode = HttpStatusCode.Conflict,
    val detalhe: Map<String, Any?> = emptyMap(),
) : RuntimeException(mensagemUsuario)

data class CorpoErro(val erro: DetalheErro)

data class DetalheErro(
    val codigo: String,
    val mensagem: String,
    val detalhe: Map<String, Any?>? = null,
)

object Erros {

    fun naoAutenticado(msg: String = "Sessão expirada. Faça login novamente.") =
        ErroNegocio("SESSAO_EXPIRADA", msg, HttpStatusCode.Unauthorized)

    fun credenciaisInvalidas() =
        ErroNegocio("CREDENCIAIS_INVALIDAS", "Usuário ou senha inválidos", HttpStatusCode.Unauthorized)

    fun semPermissao() =
        ErroNegocio("PERFIL_SEM_PERMISSAO", "Seu perfil não permite esta ação", HttpStatusCode.Forbidden)

    fun naoEncontrado(oque: String) =
        ErroNegocio("NAO_ENCONTRADO", "$oque não encontrado", HttpStatusCode.NotFound)

    fun invalido(codigo: String, msg: String, detalhe: Map<String, Any?> = emptyMap()) =
        ErroNegocio(codigo, msg, HttpStatusCode.UnprocessableEntity, detalhe)

    fun conflito(codigo: String, msg: String, detalhe: Map<String, Any?> = emptyMap()) =
        ErroNegocio(codigo, msg, HttpStatusCode.Conflict, detalhe)
}
