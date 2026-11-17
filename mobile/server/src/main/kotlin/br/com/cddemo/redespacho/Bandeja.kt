package br.com.cddemo.redespacho

import org.slf4j.LoggerFactory
import java.awt.Color
import java.awt.Desktop
import java.awt.EventQueue
import java.awt.Font
import java.awt.GraphicsEnvironment
import java.awt.Image
import java.awt.MenuItem
import java.awt.PopupMenu
import java.awt.RenderingHints
import java.awt.SystemTray
import java.awt.TrayIcon
import java.awt.image.BufferedImage
import java.net.URI
import java.nio.file.Path
import javax.swing.JOptionPane
import javax.swing.UIManager
import kotlin.concurrent.thread

/**
 * Ícone na bandeja do Windows, perto do relógio.
 *
 * O servidor roda sem janela (javaw). A janela preta aberta o dia inteiro era
 * fechada por engano e derrubava os coletores; no lugar dela fica o ícone, que
 * mostra que o servidor está no ar e dá os comandos do dia a dia. Sem ambiente
 * gráfico (teste, sessão sem área de trabalho) não faz nada.
 */
object Bandeja {

    private val log = LoggerFactory.getLogger(Bandeja::class.java)

    @Volatile private var icone: TrayIcon? = null

    /** Desligamento em ordem (Aplicacao.kt); o ícone só chama. */
    @Volatile private var aoParar: () -> Unit = {}

    fun mostrar(porta: Int, pastaLogs: Path, aviso: String?, aoParar: () -> Unit) {
        this.aoParar = aoParar
        if (GraphicsEnvironment.isHeadless() || !SystemTray.isSupported()) {
            log.info("Sem bandeja do sistema; o servidor segue sem ícone.")
            return
        }
        runCatching {
            EventQueue.invokeAndWait {
                // Diálogo com a cara do Windows e botões "Sim"/"Não", não o
                // visual padrão do Java.
                runCatching { UIManager.setLookAndFeel(UIManager.getSystemLookAndFeelClassName()) }

                val local = URI("http://localhost:$porta/")
                val naRede = "http://${Rede.enderecoLocal()}:$porta/"
                val menu = PopupMenu().apply {
                    add(item("Abrir retaguarda") { abrir(local) })
                    add(item("Abrir pasta de logs") { Desktop.getDesktop().open(pastaLogs.toAbsolutePath().toFile()) })
                    addSeparator()
                    add(item("Parar servidor") { confirmarParada() })
                }
                val t = TrayIcon(desenho(), "Separação Redespacho — no ar em $naRede", menu).apply {
                    isImageAutoSize = true
                    addActionListener { abrir(local) }   // duplo clique no ícone
                }
                SystemTray.getSystemTray().add(t)
                icone = t
                t.displayMessage(
                    "Separação Redespacho",
                    "Servidor no ar em $naRede" + (aviso?.let { "\n$it" } ?: ""),
                    if (aviso != null) TrayIcon.MessageType.WARNING else TrayIcon.MessageType.INFO,
                )
            }
        }.onFailure { log.warn("Não foi possível pôr o ícone na bandeja: {}", it.message) }
    }

    private fun item(texto: String, acao: () -> Unit) = MenuItem(texto).apply {
        addActionListener { runCatching(acao).onFailure { log.warn("Bandeja, '{}': {}", texto, it.message) } }
    }

    private fun abrir(uri: URI) {
        if (Desktop.isDesktopSupported()) Desktop.getDesktop().browse(uri)
    }

    /**
     * Parar pede confirmação: o menu da bandeja fica ao lado de outros ícones,
     * e um clique errado deixaria os coletores sem servidor no meio do turno.
     */
    private fun confirmarParada() {
        val painel = JOptionPane(
            "Parar o servidor?\n\nOs coletores ficam sem conexão até alguém iniciar de novo pelo iniciar.bat.",
            JOptionPane.WARNING_MESSAGE,
            JOptionPane.YES_NO_OPTION,
        )
        val dialogo = painel.createDialog("Separação Redespacho").apply { isAlwaysOnTop = true }
        dialogo.isVisible = true   // modal: volta quando responderem
        dialogo.dispose()
        if (painel.value == JOptionPane.YES_OPTION) {
            // O ícone sai aqui, na thread da interface e com o AWT vivo. Tirá-lo
            // no gancho de desligamento travava a saída: o AWT já estava parando
            // e o gancho nunca chegava ao backup final.
            runCatching { icone?.let { SystemTray.getSystemTray().remove(it) } }
            icone = null
            thread(name = "parada") { aoParar() }
        }
    }

    /** Quadrado verde da identidade do sistema com um "R" branco. */
    private fun desenho(): Image {
        val tam = 64
        val img = BufferedImage(tam, tam, BufferedImage.TYPE_INT_ARGB)
        val g = img.createGraphics()
        g.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON)
        g.setRenderingHint(RenderingHints.KEY_TEXT_ANTIALIASING, RenderingHints.VALUE_TEXT_ANTIALIAS_ON)
        g.color = Color(0x14, 0x53, 0x2D)
        g.fillRoundRect(2, 2, tam - 4, tam - 4, 20, 20)
        g.color = Color.WHITE
        g.font = Font(Font.SANS_SERIF, Font.BOLD, 46)
        val fm = g.fontMetrics
        g.drawString("R", (tam - fm.stringWidth("R")) / 2, (tam - fm.height) / 2 + fm.ascent)
        g.dispose()
        return img
    }
}
