package br.com.cddemo.redespacho.seguranca

import java.security.SecureRandom
import java.util.Base64
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.PBEKeySpec

/**
 * Senha numérica com PBKDF2 e salt por usuário.
 *
 * A senha é curta por exigência de operação — teclado numérico, com luva, na
 * câmara. Não dá para compensar isso com política de complexidade, então a
 * defesa é o custo de derivação e o fato de a rede ser local e fechada.
 */
object Senhas {

    private const val ITERACOES = 120_000
    private const val TAMANHO_BITS = 256
    private val random = SecureRandom()

    fun gerarSalt(): String = ByteArray(16)
        .also { random.nextBytes(it) }
        .let { Base64.getEncoder().encodeToString(it) }

    fun hash(senha: String, salt: String): String {
        val spec = PBEKeySpec(
            senha.toCharArray(),
            Base64.getDecoder().decode(salt),
            ITERACOES,
            TAMANHO_BITS,
        )
        val chave = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec)
        return Base64.getEncoder().encodeToString(chave.encoded)
    }

    /** Comparação em tempo constante, para não vazar o prefixo correto. */
    fun confere(senha: String, salt: String, hashEsperado: String): Boolean {
        val calculado = hash(senha, salt).toByteArray()
        val esperado = hashEsperado.toByteArray()
        if (calculado.size != esperado.size) return false
        var diferenca = 0
        for (i in calculado.indices) diferenca = diferenca or (calculado[i].toInt() xor esperado[i].toInt())
        return diferenca == 0
    }
}
