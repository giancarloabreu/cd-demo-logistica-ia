package br.com.cddemo.redespacho

import java.net.NetworkInterface

/**
 * Endereço desta máquina na rede do CD.
 *
 * Serve para o sinal de presença e para os QRs da aba Coletores: quem abre a
 * retaguarda o faz na própria máquina do servidor, e ali `location.origin` é
 * `localhost` — endereço que não leva a lugar nenhum a partir do coletor.
 */
object Rede {

    /**
     * Prefere faixa privada (10.x, 172.16–31.x, 192.168.x): máquinas com
     * adaptador virtual ou de VPN têm endereços que o coletor não alcança.
     */
    fun enderecoLocal(): String = runCatching {
        val candidatos = NetworkInterface.getNetworkInterfaces().toList()
            .filter { it.isUp && !it.isLoopback && !it.isVirtual }
            .flatMap { it.inetAddresses.toList() }
            .filter { it.hostAddress?.contains('.') == true && !it.isLoopbackAddress }

        (candidatos.firstOrNull { it.isSiteLocalAddress } ?: candidatos.firstOrNull())
            ?.hostAddress
    }.getOrNull() ?: "localhost"
}
