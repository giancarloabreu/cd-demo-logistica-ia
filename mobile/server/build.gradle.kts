plugins {
    // Sem versão: o AGP já traz o Kotlin no classpath do build, e declarar
    // versão aqui conflita ("plugin is already on the classpath").
    id("org.jetbrains.kotlin.jvm")
    application
}

kotlin {
    jvmToolchain(21)
}

application {
    mainClass.set("br.com.cddemo.redespacho.AplicacaoKt")
}

dependencies {
    implementation(libs.sqlite.jdbc)
    implementation(libs.ktor.server.core)
    implementation(libs.ktor.server.netty)
    implementation(libs.ktor.server.content.negotiation)
    implementation(libs.ktor.serialization.jackson)
    implementation(libs.jackson.module.kotlin)
    implementation(libs.jackson.jsr310)
    implementation(libs.ktor.server.status.pages)
    implementation(libs.ktor.server.call.logging)
    implementation(libs.ktor.server.cors)
    implementation(libs.logback.classic)
    implementation(libs.zxing.core)

    testImplementation(kotlin("test"))
    testImplementation(libs.ktor.server.test.host)
    testImplementation(libs.ktor.client.content.negotiation)
}

tasks.test {
    useJUnitPlatform()
    testLogging {
        events("passed", "failed", "skipped")
    }
}

/**
 * Classpath exato do pacote, num arquivo de argumentos do Java
 * (`javaw @lib\classpath.txt`).
 *
 * Com `-cp lib\*`, extrair uma versão nova por cima da antiga deixaria na pasta
 * os jars que mudaram de versão, e o Java carregaria os dois — defeito que só
 * aparece na atualização, em produção. Com a lista, jar que sobrou de versão
 * anterior é ignorado.
 */
val classpathDoPacote by tasks.registering {
    val destino = layout.buildDirectory.file("argumentos/classpath.txt")
    val jarPrincipal = tasks.named<Jar>("jar").flatMap { it.archiveFileName }
    val dependencias = files(configurations.runtimeClasspath)
    inputs.property("jarPrincipal", jarPrincipal)
    inputs.files(dependencias)
    outputs.file(destino)
    doLast {
        val nomes = listOf(jarPrincipal.get()) + dependencias.files.map { it.name }
        // Barra normal: no arquivo de argumentos a contrabarra é escape.
        destino.get().asFile.apply {
            parentFile.mkdirs()
            writeText("-cp\n" + nomes.joinToString(";") { "lib/$it" } + "\n")
        }
    }
}

/**
 * Monta a pasta que vai para o servidor: jars, scripts e configuração.
 * Sem instalador e sem privilégio de administrador — ver docs/OPERACAO.md §1.
 */
val pacote by tasks.registering(Sync::class) {
    group = "distribution"
    description = "Monta server/build/pacote pronto para copiar ao servidor"
    dependsOn(tasks.named("jar"))

    into(layout.buildDirectory.dir("pacote"))
    from("pacote")                                   // scripts e configuracao.padrao.txt
    into("lib") {
        from(tasks.named("jar"))
        from(configurations.runtimeClasspath)
        from(classpathDoPacote)
    }
    doLast {
        logger.lifecycle("Pacote pronto em ${layout.buildDirectory.get()}/pacote")
    }
}

/**
 * JDK completo (com `jmods`) para o jlink.
 *
 * O JBR que vem com o Android Studio não traz `jmods`, então não serve. Procura
 * um instalado; dá para apontar outro com `-PjdkComJmods=<caminho>`.
 */
fun jdkComJmods(): File? {
    (project.findProperty("jdkComJmods") as String?)?.let { return File(it) }
    System.getenv("JDK_JMODS_HOME")?.let { return File(it) }

    val candidatos = listOf(
        File(System.getProperty("user.home"), ".jdks"),
        File("C:/Program Files/Java"),
        File("C:/Program Files/Eclipse Adoptium"),
        File("C:/Program Files/Amazon Corretto"),
        File("C:/Program Files/Microsoft/jdk"),
        File("/usr/lib/jvm"),
    )
    return candidatos.filter { it.isDirectory }
        .flatMap { it.listFiles()?.toList().orEmpty() }
        .filter { File(it, "jmods").isDirectory && File(it, "bin/jlink" + (if (ehWindows) ".exe" else "")).exists() }
        // Precisa ser 21+ para rodar o que compilamos; 21 exato é o alvo.
        .sortedByDescending { it.name }
        .firstOrNull { it.name.contains("21") }
}

val ehWindows = System.getProperty("os.name").startsWith("Windows", ignoreCase = true)

/**
 * Runtime Java enxuto dentro do pacote.
 *
 * O PC do CD não tem Java e o operador não tem privilégio para instalar. Com o
 * `jre` aqui dentro, descompactar e clicar em iniciar.bat basta — que é o
 * requisito de "sem instalação e sem administrador" de docs/OPERACAO.md §1.
 */
val runtimeEmbutido by tasks.registering {
    group = "distribution"
    description = "Gera o Java embutido (jre) via jlink"

    // Fora de build/pacote de propósito: `pacote` é um Sync e apaga tudo que
    // não veio da origem. Gerando aqui dentro, rodar `:server:pacote` sozinho
    // levaria o Java embutido junto — e o ZIP sairia sem Java sem avisar.
    val destino = layout.buildDirectory.dir("jre")
    outputs.dir(destino)

    doLast {
        val jdk = jdkComJmods() ?: error(
            "Nenhum JDK 21 com jmods encontrado. Instale um (adoptium.net) ou " +
                "aponte com -PjdkComJmods=<caminho>. Sem ele o ZIP sai sem Java " +
                "embutido e a maquina de destino precisa ter Java 21.",
        )
        val saida = destino.get().asFile
        saida.deleteRecursively()

        providers.exec {
            commandLine(
                File(jdk, "bin/jlink" + (if (ehWindows) ".exe" else "")).absolutePath,
                "--module-path", File(jdk, "jmods").absolutePath,
                // java.se cobre sql, naming e management; os extras são o que
                // SQLite (Unsafe) e Netty pedem fora dele.
                "--add-modules", "java.se,jdk.unsupported,jdk.crypto.ec,jdk.zipfs",
                "--strip-debug", "--no-header-files", "--no-man-pages",
                "--compress", "zip-6",
                "--output", saida.absolutePath,
            )
        }.result.get()

        logger.lifecycle("Java embutido em ${saida.absolutePath} (JDK de origem: ${jdk.name})")
    }
}

/**
 * ZIP único para levar ao CD: servidor, Java e APK do coletor.
 *
 * Um arquivo só porque a entrega é por pendrive ou pasta de rede, e pasta
 * meio-copiada é o tipo de falha que só aparece na hora de usar.
 */
val versaoDoApp by tasks.registering {
    val destino = layout.buildDirectory.file("coletor/versao.txt")
    val versao = providers.gradleProperty("versaoApp")
    // Sem declarar a propriedade como entrada, o Gradle vê só o arquivo de
    // saída e considera a task atualizada — o ZIP sairia com a versão anterior.
    inputs.property("versaoApp", versao)
    outputs.file(destino)
    doLast {
        val f = destino.get().asFile
        f.parentFile.mkdirs()
        f.writeText(versao.get())
    }
}

val zipDistribuicao by tasks.registering(Zip::class) {
    group = "distribution"
    description = "Gera o ZIP com servidor, Java embutido e APK"
    dependsOn(pacote, runtimeEmbutido, versaoDoApp, ":app:assembleDebug")

    archiveFileName.set("SeparacaoRedespacho.zip")
    destinationDirectory.set(layout.buildDirectory.dir("distribuicao"))

    // Uma pasta raiz: descompactar em qualquer lugar não espalha arquivo solto.
    into("SeparacaoRedespacho") {
        // `from(pacote)` e nao o caminho da pasta: referenciar o diretorio cru
        // deixa o Gradle sem aresta de dependencia, e o ZIP sai com o conteudo
        // da build anterior — ou vazio.
        from(pacote)
        into("jre") { from(runtimeEmbutido) }
        into("coletor") {
            from(project(":app").layout.buildDirectory.dir("outputs/apk/debug")) {
                include("*.apk")
                rename { "separacao-redespacho.apk" }
            }
            // O servidor lê daqui a versão para publicar o APK sozinho na
            // primeira subida — sem isso alguém teria de digitá-la à mão.
            from(versaoDoApp)
        }
    }

    doLast {
        val f = archiveFile.get().asFile
        logger.lifecycle("ZIP pronto: ${f.absolutePath} (${f.length() / 1024 / 1024} MB)")
    }
}
