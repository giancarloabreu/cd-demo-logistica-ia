package br.com.cddemo.redespacho.api

import com.google.zxing.BarcodeFormat
import com.google.zxing.EncodeHintType
import com.google.zxing.qrcode.QRCodeWriter
import com.google.zxing.qrcode.decoder.ErrorCorrectionLevel
import java.awt.image.BufferedImage
import java.io.ByteArrayOutputStream
import javax.imageio.ImageIO

/**
 * QR do endereço do servidor, para o coletor se configurar por leitura.
 *
 * Provisionar dez aparelhos digitando IP à mão é lento e erra. O QR é gerado
 * aqui porque a rede não tem internet — nenhuma biblioteca de fora carrega.
 */
object QrCode {

    fun png(conteudo: String, lado: Int = 320): ByteArray {
        val matriz = QRCodeWriter().encode(
            conteudo, BarcodeFormat.QR_CODE, lado, lado,
            mapOf(
                // Correção alta: a etiqueta vai para a parede de um CD, sujeita
                // a poeira e amassado.
                EncodeHintType.ERROR_CORRECTION to ErrorCorrectionLevel.H,
                EncodeHintType.MARGIN to 2,
                EncodeHintType.CHARACTER_SET to "UTF-8",
            ),
        )

        val imagem = BufferedImage(lado, lado, BufferedImage.TYPE_INT_RGB)
        for (x in 0 until lado) {
            for (y in 0 until lado) {
                imagem.setRGB(x, y, if (matriz[x, y]) 0x000000 else 0xFFFFFF)
            }
        }

        return ByteArrayOutputStream().use { saida ->
            ImageIO.write(imagem, "png", saida)
            saida.toByteArray()
        }
    }
}
