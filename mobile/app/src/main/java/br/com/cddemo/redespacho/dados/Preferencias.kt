package br.com.cddemo.redespacho.dados

import android.content.Context

/**
 * Configuração local do coletor: endereço do servidor e último login usado.
 *
 * O token de sessão fica só em memória — vive no processo. Reiniciar o app
 * pede login de novo, o que numa operação de turno é aceitável e evita token
 * esquecido num aparelho compartilhado.
 */
class Preferencias(context: Context) {

    private val prefs = context.getSharedPreferences("redespacho", Context.MODE_PRIVATE)

    var servidor: String
        get() = prefs.getString("servidor", "") ?: ""
        set(v) = prefs.edit().putString("servidor", v.trim()).apply()

    var porta: Int
        get() = prefs.getInt("porta", 8080)
        set(v) = prefs.edit().putInt("porta", v).apply()

    var ultimoLogin: String
        get() = prefs.getString("ultimoLogin", "") ?: ""
        set(v) = prefs.edit().putString("ultimoLogin", v.trim()).apply()

    val configurado: Boolean get() = servidor.isNotBlank()

    val urlBase: String get() = "http://$servidor:$porta"
}
