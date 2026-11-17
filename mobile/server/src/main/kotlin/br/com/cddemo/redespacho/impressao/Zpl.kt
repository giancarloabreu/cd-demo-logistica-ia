package br.com.cddemo.redespacho.impressao

import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/**
 * Geração de ZPL para as etiquetas Zebra.
 *
 * Todas as medidas são declaradas em milímetros e convertidas para pontos pelo
 * dpi da impressora, detectado no cadastro. Assim o mesmo layout serve para
 * 203 e 300 dpi sem duas versões do código. Ver docs/OPERACAO.md §5.
 */
object Zpl {

    private val ZONA = ZoneId.of("America/Sao_Paulo")
    private val DATA_HORA = DateTimeFormatter.ofPattern("dd/MM/yyyy HH:mm")

    /** Pontos por milímetro: 203 dpi ≈ 8/mm, 300 dpi ≈ 11,8/mm. */
    private fun pt(mm: Double, dpi: Int): Int = Math.round(mm * dpi / 25.4).toInt()

    // ------------------------------------------------ etiqueta de separação --

    /**
     * 80 mm de largura × 50 mm de altura, com Code 128, o nome do pallet e
     * "Pallet Separação" no rodapé. Ver docs/TELAS-COLETOR.md §3.
     */
    fun etiquetaPallet(codigo: String, dpi: Int, rodape: String = "Pallet Separação"): String {
        val largura = pt(80.0, dpi)
        val altura = pt(50.0, dpi)
        val margem = pt(6.0, dpi)
        val util = largura - 2 * margem
        val alturaBarras = pt(20.0, dpi)

        return buildString {
            append("^XA")
            append("^CI28")                       // UTF-8, para acento no rodapé
            append("^PW$largura")
            append("^LL$altura")
            append("^LH0,0")
            // Code 128 centralizado na área útil, e não na etiqueta inteira:
            // centralizar sobre a borda deixava o código encostado nela.
            append("^FO$margem,${pt(5.0, dpi)}^FB$util,1,0,C,0")
            append("^BY${moduloBarras(codigo, util)},2.5,$alturaBarras")
            append("^BCN,$alturaBarras,N,N,N^FD$codigo^FS")
            // Nome do pallet, grande e legível de longe.
            append("^FO$margem,${pt(29.0, dpi)}^FB$util,1,0,C,0")
            append("^A0N,${pt(8.0, dpi)},${pt(8.0, dpi)}^FD$codigo^FS")
            append("^FO$margem,${pt(41.0, dpi)}^FB$util,1,0,C,0")
            append("^A0N,${pt(4.5, dpi)},${pt(4.5, dpi)}^FD$rodape^FS")
            append("^XZ")
        }
    }

    // ------------------------------------------- etiqueta do produto (2ª via) --

    /**
     * 80 × 50 mm, no modelo aprovado em campo: código do produto e nome no
     * alto, Code 128 no meio com o número embaixo (dá para digitar se o leitor
     * falhar) e a faixa preta "2ª VIA ETIQUETA" no rodapé. É para a caixa que
     * chegou sem código que o coletor leia — ou com o de outro produto, na
     * devolução trocada — poder ser bipada. Ver docs/TELAS-RETAGUARDA.md §10.7.
     */
    fun etiquetaProduto(codigoBarras: String, produtoCodigo: String, descricao: String, dpi: Int): String {
        val largura = pt(80.0, dpi)
        val altura = pt(50.0, dpi)
        val margem = pt(6.0, dpi)
        val util = largura - 2 * margem
        val alturaBarras = pt(16.0, dpi)
        val alturaFaixa = pt(7.0, dpi)
        val codigo = escapar(codigoBarras)

        return buildString {
            append("^XA")
            append("^CI28")                       // UTF-8, para o "ª" da faixa
            append("^PW$largura")
            append("^LL$altura")
            append("^LH0,0")
            // Código do produto e nome, centralizados no alto.
            append("^FO$margem,${pt(4.0, dpi)}^FB$util,1,0,C,0")
            append("^A0N,${pt(5.5, dpi)},${pt(5.5, dpi)}^FD${escapar(caber(produtoCodigo, 68.0, 5.5))}^FS")
            quebrar(descricao, 68.0, 3.8, 2).forEachIndexed { i, linha ->
                append("^FO$margem,${pt(11.0 + i * 4.6, dpi)}^FB$util,1,0,C,0")
                append("^A0N,${pt(3.8, dpi)},${pt(3.8, dpi)}^FD${escapar(linha)}^FS")
            }
            // Modo A no ^BC: com o código todo numérico a impressora usa o
            // subconjunto C e codifica os dígitos aos pares. Sem ele o Code 128
            // saía mais largo que a etiqueta e o coletor não lia.
            append("^FO$margem,${pt(20.0, dpi)}^FB$util,1,0,C,0")
            append("^BY${moduloDoCodigo(codigo, util)},2.5,$alturaBarras")
            append("^BCN,$alturaBarras,N,N,N,A^FD$codigo^FS")
            // O número embaixo das barras: dá para digitar se o leitor falhar.
            append("^FO$margem,${pt(36.5, dpi)}^FB$util,1,0,C,0")
            append("^A0N,${pt(3.0, dpi)},${pt(3.0, dpi)}^FD$codigo^FS")
            // Faixa preta arredondada, com o texto em branco (^FR).
            append("^FO$margem,${pt(41.0, dpi)}^GB$util,$alturaFaixa,$alturaFaixa,B,3^FS")
            append("^FO$margem,${pt(42.6, dpi)}^FB$util,1,0,C,0")
            append("^A0N,${pt(4.2, dpi)},${pt(4.2, dpi)}^FR^FD2ª VIA ETIQUETA^FS")
            append("^XZ")
        }
    }

    /**
     * Módulo do Code 128 da etiqueta 2ª via, no modo A: código todo numérico
     * entra aos pares (subconjunto C); o resto, caractere a caractere. Em
     * módulos: início (11) + dados + verificador (11) + parada (13), com 10%
     * de folga para não encostar nas margens.
     */
    private fun moduloDoCodigo(codigo: String, larguraUtil: Int): Int {
        val dados = if (codigo.all(Char::isDigit)) (codigo.length + 1) / 2 else codigo.length
        val modulos = 11 * (dados + 2) + 13
        return ((larguraUtil * 9 / 10) / modulos).coerceIn(2, 5)
    }

    /**
     * Maior módulo do Code 128 que ainda cabe na área útil.
     *
     * Módulo fixo fazia o código ocupar pouco mais da metade da etiqueta e
     * parecer apertado no meio de tanto branco. Aqui ele cresce até encostar
     * nas margens: quanto mais largo o módulo, mais folga o leitor tem.
     *
     * Largura em módulos do Code 128, sem contar a compressão de dígitos que a
     * impressora possa fazer — estimar por cima nunca estoura a etiqueta:
     * início (11) + N caracteres (11 cada) + troca de subconjunto (11) +
     * verificador (11) + parada (13).
     */
    private fun moduloBarras(codigo: String, larguraUtil: Int): Int {
        val modulos = 11 * (codigo.length + 3) + 13
        return (larguraUtil / modulos).coerceIn(2, 6)
    }

    // ---------------------------------------------------- cartão do pallet --

    data class LinhaCartao(val codigo: String, val descricao: String, val quantidade: Int)

    data class Cartao(
        val palletCodigo: String,
        val instrucao: String,
        val notaNumero: String,
        val cliente: String,
        /** Logradouro e número, como vêm da NF. */
        val endereco: String,
        val complemento: String?,
        val totalCaixas: Int,
        val itens: List<LinhaCartao>,
        val usuario: String,
        val bonificacao: Boolean = false,
        val em: Instant = Instant.now(),
        val bairro: String? = null,
        /** Município/UF. */
        val cidade: String? = null,
        val cep: String? = null,
    )

    private const val LINHAS_POR_COLUNA = 10
    private const val COLUNAS = 3
    const val ITENS_POR_FOLHA = LINHAS_POR_COLUNA * COLUNAS

    /**
     * Largura média de um caractere da fonte 0 em relação à altura, com folga.
     *
     * Medida na prévia: ~0,49 em maiúsculas e dígitos. O 0,54 estima por
     * cima, e estimar por cima só quebra a linha um pouco antes. Estimar por
     * baixo era o defeito da 0.5.0: a linha que não cabia no ^FB de uma linha
     * era impressa por cima dela mesma, e o endereço saía ilegível.
     */
    private const val LARGURA_CARACTERE = 0.54

    /**
     * Mídia de 80 mm × 220 mm com o conteúdo girado 90° (`^FWR`): a área de
     * leitura fica 220 mm de largura × 80 mm de altura, o que libera espaço
     * para os itens em três colunas. Ver docs/TELAS-COLETOR.md §5.4.
     *
     * Passando de 30 itens, emite folhas adicionais — nunca corta a lista.
     */
    fun cartaoPallet(c: Cartao, dpi: Int): String {
        val folhas = c.itens.chunked(ITENS_POR_FOLHA).ifEmpty { listOf(emptyList()) }
        return folhas.mapIndexed { i, itens ->
            umaFolha(c, itens, dpi, i + 1, folhas.size)
        }.joinToString("")
    }

    /**
     * Uma folha do cartão, em coordenadas de leitura (ver [Desenho]):
     *
     * ```
     * ┌───────────────────────────────────────────────────────────────┐
     * │ itens em três colunas, na ordem da NF, de cima para baixo      │
     * ├───────────────────┬──────────────────────┬────────────────────┤
     * │ 45 caixas         │ NOME DO CLIENTE      │▓▓▓▓▓▓ NF ▓▓▓▓▓▓▓▓▓▓│
     * │ data e hora       │ (até duas linhas)    │▓▓              ▓▓▓▓│
     * │ separador         │ endereço, quebrado   │▓▓   953120     ▓▓▓▓│
     * │ instrução         │ em até cinco linhas  │▓▓              ▓▓▓▓│
     * │ P-RED-000123      │                      │▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓│
     * │ ║▌║▌║║▌║▌║▌║      │                      │▓ CONTÉM BONIFICAÇÃO│
     * └───────────────────┴──────────────────────┴────────────────────┘
     * ```
     *
     * A NF ganha a coluna inteira, em branco sobre preto: é o que o
     * conferente procura de longe no carregamento.
     */
    private fun umaFolha(
        c: Cartao,
        itens: List<LinhaCartao>,
        dpi: Int,
        folha: Int,
        totalFolhas: Int,
    ): String {
        val d = Desenho(dpi)

        // --- itens, no alto, em até três colunas
        itens.forEachIndexed { i, item ->
            val x = 5.0 + (i / LINHAS_POR_COLUNA) * 70.0
            val y = 3.3 + (i % LINHAS_POR_COLUNA) * 3.0
            d.texto(x, y, 2.7, caber("${item.codigo} ${item.descricao}", 57.0, 2.7))
            d.texto(x + 58.0, y, 2.7, item.quantidade.toString(), largura = 9.0, alinhar = 'R')
        }
        d.caixa(5.0, 35.2, 210.0, 0.4)

        // --- coluna esquerda: o que foi separado, por quem e quando; embaixo,
        // o número do pallet e o código de barras.
        d.texto(5.0, 38.0, 6.5, "${c.totalCaixas} caixas")
        d.texto(5.0, 45.7, 3.6, DATA_HORA.format(c.em.atZone(ZONA)))
        d.texto(5.0, 50.1, 3.6, caber(c.usuario, 56.0, 3.6))
        d.texto(5.0, 54.5, 3.6, caber("Instrução ${c.instrucao}", 56.0, 3.6))
        d.texto(5.0, 59.5, 6.0, c.palletCodigo)
        d.barras(5.0, 66.5, 10.5, c.palletCodigo)

        // --- coluna do meio: destinatário. Fluxo de cima para baixo: nome
        // curto deixa mais linhas para o endereço.
        var y = 38.0
        quebrar(c.cliente, LARGURA_MEIO, ALTURA_NOME, maxLinhas = 2).forEach {
            d.texto(X_MEIO, y, ALTURA_NOME, it)
            y += 5.6
        }
        y += 1.2
        val limite = if (totalFolhas > 1) 72.0 else 77.0
        val cabem = ((limite - y + (PASSO_ENDERECO - ALTURA_ENDERECO)) / PASSO_ENDERECO).toInt()
        linhasDeEndereco(c, cabem).forEach {
            d.texto(X_MEIO, y, ALTURA_ENDERECO, it)
            y += PASSO_ENDERECO
        }
        if (totalFolhas > 1) d.texto(X_MEIO, 73.4, 3.5, "Folha $folha de $totalFolhas")

        // --- coluna direita: a NF, grande, branca sobre preto
        d.caixa(138.0, 37.5, 77.0, 39.5)
        d.texto(138.0, 39.5, 5.5, "NF", largura = 77.0, alinhar = 'C', inverso = true)
        // Quanto maior melhor, desde que o número caiba na largura da caixa:
        // NF de 6 dígitos sai com ~2 cm de altura, e de 9 dígitos encolhe.
        val baseNumero = if (c.bonificacao) 69.5 else 75.5
        val alturaNumero = minOf(20.0, 71.0 / (c.notaNumero.length.coerceAtLeast(1) * LARGURA_CARACTERE))
        val topoNumero = 46.0 + ((baseNumero - 46.0) - alturaNumero) / 2
        d.texto(138.0, topoNumero, alturaNumero, c.notaNumero, largura = 77.0, alinhar = 'C', inverso = true)

        // Bonificação vai no mesmo pallet da venda; quem confere no
        // carregamento precisa ver isso sem procurar — na faixa branca dentro
        // da caixa da NF, onde o olho já está.
        if (c.bonificacao) {
            d.caixa(141.0, 70.8, 71.0, 5.2, branca = true)
            d.texto(141.0, 71.4, 4.0, "CONTÉM BONIFICAÇÃO", largura = 71.0, alinhar = 'C')
        }

        return buildString {
            append("^XA")
            append("^CI28")
            append("^PW${pt(80.0, dpi)}")
            append("^LL${pt(220.0, dpi)}")
            append("^LH0,0")
            append("^FWR")
            append(d)
            append("^XZ")
        }
    }

    private const val X_MEIO = 66.0
    private const val LARGURA_MEIO = 68.0
    private const val ALTURA_NOME = 4.8
    private const val ALTURA_ENDERECO = 3.9
    private const val PASSO_ENDERECO = 4.6

    /**
     * Endereço em linhas, da forma mais arejada que couber: primeiro cada
     * parte na sua linha (logradouro, complemento, bairro, cidade/CEP); depois
     * o complemento junto do número; depois o bairro junto da cidade; por fim
     * tudo corrido, com a última linha em reticências. Nada sai sobreposto.
     */
    private fun linhasDeEndereco(c: Cartao, maxLinhas: Int): List<String> {
        if (maxLinhas <= 0) return emptyList()
        fun limpo(s: String?) = s?.trim()?.takeIf(String::isNotEmpty)
        fun juntar(vararg partes: String?) = partes.filterNotNull().joinToString(" - ").ifEmpty { null }

        // "CEP 15090-000" não se parte entre duas linhas.
        val cep = c.cep?.filter(Char::isDigit)?.takeIf { it.length == 8 }
            ?.let { "CEP$COLA${it.take(5)}-${it.drop(5)}" }
        val endereco = limpo(c.endereco)
        val complemento = limpo(c.complemento)
        val bairro = limpo(c.bairro)
        val cidade = juntar(limpo(c.cidade), cep)

        val tentativas = listOf(
            listOfNotNull(endereco, complemento, bairro, cidade),
            listOfNotNull(juntar(endereco, complemento), bairro, cidade),
            listOfNotNull(juntar(endereco, complemento), juntar(bairro, cidade)),
        )
        for (partes in tentativas) {
            val linhas = partes.flatMap { quebrar(it, LARGURA_MEIO, ALTURA_ENDERECO, Int.MAX_VALUE) }
            if (linhas.size <= maxLinhas) return linhas
        }
        val corrido = listOfNotNull(endereco, complemento, bairro, cidade).joinToString(", ")
        return quebrar(corrido, LARGURA_MEIO, ALTURA_ENDERECO, maxLinhas)
    }

    /** Espaço que não quebra linha; vira espaço comum na saída. */
    private const val COLA = ''

    /** Quantos caracteres cabem na largura, estimando por cima. */
    private fun capacidade(larguraMm: Double, alturaMm: Double): Int =
        (larguraMm / (alturaMm * LARGURA_CARACTERE)).toInt().coerceAtLeast(4)

    /** Uma linha só: o que passar da largura vira reticências. */
    private fun caber(texto: String, larguraMm: Double, alturaMm: Double): String {
        val n = capacidade(larguraMm, alturaMm)
        val t = texto.trim()
        return if (t.length <= n) t else t.take(n - 3).trimEnd() + "..."
    }

    /**
     * Quebra por palavra em até [maxLinhas] linhas que cabem na largura.
     * Palavra maior que a linha é partida; sobrando texto, a última linha
     * termina em reticências.
     */
    internal fun quebrar(texto: String, larguraMm: Double, alturaMm: Double, maxLinhas: Int): List<String> =
        quebrarColado(texto, larguraMm, alturaMm, maxLinhas).map { it.replace(COLA, ' ') }

    private fun quebrarColado(texto: String, larguraMm: Double, alturaMm: Double, maxLinhas: Int): List<String> {
        val n = capacidade(larguraMm, alturaMm)
        // O traço separador fica colado na palavra de antes: linha começando
        // com "- JARDIM ..." parece continuação de outra coisa.
        val palavras = texto.trim().replace(" - ", "$COLA- ")
            .split(Regex("\\s+")).filter(String::isNotEmpty)
            .flatMap { it.chunked(n) }
        val linhas = mutableListOf<String>()
        var atual = ""
        for (p in palavras) {
            val candidata = if (atual.isEmpty()) p else "$atual $p"
            if (candidata.length <= n) {
                atual = candidata
            } else {
                linhas += atual
                atual = p
            }
        }
        if (atual.isNotEmpty()) linhas += atual
        if (linhas.size <= maxLinhas) return linhas

        val cortadas = linhas.take(maxLinhas).toMutableList()
        val ultima = cortadas.last()
        cortadas[cortadas.lastIndex] =
            if (ultima.length + 3 <= n) "$ultima..." else ultima.take(n - 3).trimEnd() + "..."
        return cortadas
    }

    /**
     * Desenho do cartão em coordenadas de LEITURA: `x` ao longo dos 220 mm, a
     * partir da esquerda; `y` ao longo dos 80 mm, a partir de cima; e (x, y) é
     * o canto superior esquerdo do campo como o operador o lê.
     *
     * Com `^FWR` o texto desce na mídia com o topo das letras voltado para a
     * direita, e o `^FO` marca o canto superior esquerdo do campo **na mídia** —
     * que, na leitura, é o canto inferior esquerdo. Por isso a origem desconta
     * a altura do campo. A 0.5.0 não descontava: cada campo descia uma altura
     * inteira e a ordem de cima para baixo saía invertida (itens da NF de baixo
     * para cima, o décimo cortado na borda).
     *
     * `^GB` não gira com o `^FW`: largura e altura dele são as da mídia, e aqui
     * se trocam para a leitura.
     */
    private class Desenho(private val dpi: Int) {
        private val corpo = StringBuilder()
        private val larguraMidia = pt(80.0, dpi)

        private fun p(mm: Double) = pt(mm, dpi)

        private fun origem(x: Double, y: Double, altura: Double) =
            "^FO${larguraMidia - p(y) - p(altura)},${p(x)}"

        fun texto(
            x: Double,
            y: Double,
            altura: Double,
            conteudo: String,
            largura: Double? = null,
            alinhar: Char = 'L',
            inverso: Boolean = false,
        ) {
            corpo.append(origem(x, y, altura))
            corpo.append("^A0R,${p(altura)},${p(altura)}")
            largura?.let { corpo.append("^FB${p(it)},1,0,$alinhar,0") }
            if (inverso) corpo.append("^FR")
            corpo.append("^FD${escapar(conteudo)}^FS")
        }

        /** Retângulo cheio (preto, ou branco sobre o preto). */
        fun caixa(x: Double, y: Double, largura: Double, altura: Double, branca: Boolean = false) {
            val w = p(altura)
            val h = p(largura)
            corpo.append(origem(x, y, altura))
            corpo.append("^GB$w,$h,${minOf(w, h)},${if (branca) 'W' else 'B'}^FS")
        }

        fun barras(x: Double, y: Double, altura: Double, codigo: String) {
            corpo.append(origem(x, y, altura))
            corpo.append("^BY${if (dpi >= 300) 3 else 2},2.5,${p(altura)}")
            corpo.append("^BCR,${p(altura)},N,N,N^FD${escapar(codigo)}^FS")
        }

        override fun toString() = corpo.toString()
    }

    /** Etiqueta curta para conferir dpi e alinhamento antes de produzir. */
    fun etiquetaTeste(dpi: Int, nomeImpressora: String): String =
        etiquetaPallet("P-RED-000000", dpi, "Teste · $nomeImpressora · $dpi dpi")

    /** `^`, `~` e `,` têm significado no ZPL e precisam ser neutralizados. */
    private fun escapar(s: String): String =
        s.replace("^", " ").replace("~", " ").replace("\\", " ")
}
