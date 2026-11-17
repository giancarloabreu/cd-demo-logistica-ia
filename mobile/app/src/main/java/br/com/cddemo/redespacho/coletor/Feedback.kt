package br.com.cddemo.redespacho.coletor

import android.content.Context
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager

enum class Evento { ACEITA, ITEM_ERRADO, CODIGO_DESCONHECIDO, ITEM_COMPLETO }

/**
 * Som e vibração distintos por evento.
 *
 * O separador não olha a tela a cada bipe — ele escuta. Um som só para "ok" e
 * outro para "erro" já muda a operação: errar e continuar bipando no automático
 * é o que gera inversão de caixa. Ver docs/TELAS-COLETOR.md §0.
 */
class Feedback(context: Context) {

    private val vibrador: Vibrator? = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        (context.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager)?.defaultVibrator
    } else {
        @Suppress("DEPRECATION")
        context.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
    }

    fun sinalizar(evento: Evento) {
        when (evento) {
            Evento.ACEITA -> {
                tom(ToneGenerator.TONE_PROP_BEEP, 90)
                vibrar(50)
            }
            Evento.ITEM_ERRADO -> {
                tom(ToneGenerator.TONE_CDMA_LOW_L, 350)
                vibrar(300)
            }
            Evento.CODIGO_DESCONHECIDO -> {
                tom(ToneGenerator.TONE_PROP_NACK, 250)
                vibrar(200)
            }
            Evento.ITEM_COMPLETO -> {
                tom(ToneGenerator.TONE_PROP_ACK, 300)
                vibrar(100)
            }
        }
    }

    private fun tom(tipo: Int, duracaoMs: Int) {
        runCatching {
            // Instância curta por bipe: manter um ToneGenerator vivo trava o
            // canal de áudio do aparelho em uso prolongado.
            val g = ToneGenerator(AudioManager.STREAM_NOTIFICATION, 100)
            g.startTone(tipo, duracaoMs)
            android.os.Handler(android.os.Looper.getMainLooper())
                .postDelayed({ runCatching { g.release() } }, (duracaoMs + 150).toLong())
        }
    }

    private fun vibrar(ms: Long) {
        runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                vibrador?.vibrate(VibrationEffect.createOneShot(ms, VibrationEffect.DEFAULT_AMPLITUDE))
            } else {
                @Suppress("DEPRECATION")
                vibrador?.vibrate(ms)
            }
        }
    }
}
