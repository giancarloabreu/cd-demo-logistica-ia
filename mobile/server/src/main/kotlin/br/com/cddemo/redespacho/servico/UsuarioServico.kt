package br.com.cddemo.redespacho.servico

import br.com.cddemo.redespacho.api.Erros
import br.com.cddemo.redespacho.persistencia.Db
import br.com.cddemo.redespacho.persistencia.atualizar
import br.com.cddemo.redespacho.persistencia.buscarTodos
import br.com.cddemo.redespacho.persistencia.buscarUm
import br.com.cddemo.redespacho.persistencia.inserir
import br.com.cddemo.redespacho.seguranca.Perfil
import br.com.cddemo.redespacho.seguranca.Senhas
import br.com.cddemo.redespacho.seguranca.UsuarioAutenticado
import java.sql.Connection
import java.time.Instant

data class UsuarioResumo(
    val id: Long,
    val login: String,
    val nome: String,
    val perfil: String,
    val multiItem: Boolean,
    val ativo: Boolean,
    val criadoEm: String,
    /** Com leituras registradas o usuário é inativado, nunca excluído. */
    val temMovimento: Boolean,
)

class UsuarioServico(private val db: Db) {

    private val loginValido = Regex("^[a-z0-9._-]{3,20}$")

    fun autenticar(login: String, senha: String): UsuarioAutenticado = db.leitura { c ->
        val linha = c.buscarUm(
            """SELECT id, login, nome, senha_hash, senha_salt, perfil, multi_item, impressora_id, ativo
                 FROM usuario WHERE login = ?""",
            login.trim().lowercase(),
        ) { rs ->
            Linha(
                id = rs.getLong(1),
                login = rs.getString(2),
                nome = rs.getString(3),
                hash = rs.getString(4),
                salt = rs.getString(5),
                perfil = rs.getString(6),
                multiItem = rs.getInt(7) == 1,
                impressoraId = rs.getLong(8).takeIf { !rs.wasNull() },
                ativo = rs.getInt(9) == 1,
            )
        }

        // Mesma resposta para login inexistente, senha errada e usuário
        // inativo: não entregamos ao atacante quais logins existem.
        if (linha == null || !linha.ativo) throw Erros.credenciaisInvalidas()
        if (!Senhas.confere(senha, linha.salt, linha.hash)) throw Erros.credenciaisInvalidas()

        UsuarioAutenticado(
            id = linha.id,
            login = linha.login,
            nome = linha.nome,
            perfil = Perfil.valueOf(linha.perfil),
            multiItem = linha.multiItem,
            impressoraId = linha.impressoraId,
        )
    }

    fun listar(): List<UsuarioResumo> = db.leitura { c ->
        c.buscarTodos(
            """SELECT u.id, u.login, u.nome, u.perfil, u.multi_item, u.ativo, u.criado_em,
                      EXISTS(SELECT 1 FROM leitura l WHERE l.usuario_id = u.id)
                 FROM usuario u ORDER BY u.ativo DESC, u.nome""",
        ) {
            UsuarioResumo(
                it.getLong(1), it.getString(2), it.getString(3), it.getString(4),
                it.getInt(5) == 1, it.getInt(6) == 1, it.getString(7), it.getInt(8) == 1,
            )
        }
    }

    fun criar(
        login: String,
        nome: String,
        senha: String,
        perfil: Perfil,
        multiItem: Boolean = false,
        criadoPor: Long? = null,
    ): Long = db.transacao { c ->
        val loginLimpo = login.trim().lowercase()
        exigirLoginValido(loginLimpo)
        exigirSenhaValida(senha)
        if (nome.isBlank()) throw Erros.invalido("NOME_OBRIGATORIO", "Informe o nome do usuário")

        if (c.buscarUm("SELECT 1 FROM usuario WHERE login = ?", loginLimpo) { it.getInt(1) } != null) {
            throw Erros.conflito("LOGIN_EM_USO", "Já existe um usuário com o login \"$loginLimpo\"")
        }

        val salt = Senhas.gerarSalt()
        c.inserir(
            """INSERT INTO usuario (login, nome, senha_hash, senha_salt, perfil, multi_item, criado_em, criado_por)
               VALUES (?,?,?,?,?,?,?,?)""",
            loginLimpo, nome.trim(), Senhas.hash(senha, salt), salt,
            perfil.name, multiItem, Instant.now().toString(), criadoPor,
        )
    }

    fun atualizar(
        id: Long,
        nome: String,
        perfil: Perfil,
        multiItem: Boolean,
        ativo: Boolean,
        autorId: Long,
    ): UsuarioResumo = db.transacao { c ->
        val atual = c.buscarUm(
            "SELECT perfil, ativo FROM usuario WHERE id = ?", id,
        ) { it.getString(1) to (it.getInt(2) == 1) } ?: throw Erros.naoEncontrado("Usuário")

        if (nome.isBlank()) throw Erros.invalido("NOME_OBRIGATORIO", "Informe o nome do usuário")

        // Duas travas contra ficar trancado para fora do próprio sistema.
        if (!ativo && id == autorId) {
            throw Erros.conflito("NAO_INATIVAR_A_SI", "Você não pode inativar o próprio usuário")
        }
        val perdeAdmin = (atual.first == "ADMIN") && (perfil != Perfil.ADMIN || !ativo)
        if (perdeAdmin && contarAdminsAtivos(c) <= 1) {
            throw Erros.conflito(
                "ULTIMO_ADMIN",
                "Este é o único administrador ativo. Crie outro antes de alterá-lo.",
            )
        }

        c.atualizar(
            "UPDATE usuario SET nome=?, perfil=?, multi_item=?, ativo=? WHERE id=?",
            nome.trim(), perfil.name, multiItem, ativo, id,
        )
        listarUm(c, id)
    }

    fun redefinirSenha(id: Long, novaSenha: String) = db.transacao { c ->
        exigirSenhaValida(novaSenha)
        c.buscarUm("SELECT 1 FROM usuario WHERE id = ?", id) { it.getInt(1) }
            ?: throw Erros.naoEncontrado("Usuário")
        val salt = Senhas.gerarSalt()
        c.atualizar(
            "UPDATE usuario SET senha_hash=?, senha_salt=? WHERE id=?",
            Senhas.hash(novaSenha, salt), salt, id,
        )
    }

    fun existeAlgum(): Boolean = db.leitura { c ->
        (c.buscarUm("SELECT COUNT(*) FROM usuario") { it.getInt(1) } ?: 0) > 0
    }

    /** O admin de implantação ainda está com a senha padrão? */
    fun adminComSenhaPadrao(): Boolean = db.leitura { c ->
        val l = c.buscarUm(
            "SELECT senha_hash, senha_salt FROM usuario WHERE login = 'admin' AND ativo = 1",
        ) { it.getString(1) to it.getString(2) } ?: return@leitura false
        Senhas.confere("0000", l.second, l.first)
    }

    // ---------------------------------------------------------------- internos

    private fun exigirLoginValido(login: String) {
        if (!loginValido.matches(login)) {
            throw Erros.invalido(
                "LOGIN_INVALIDO",
                "Login deve ter de 3 a 20 caracteres, apenas letras, números, ponto, hífen ou sublinhado",
            )
        }
    }

    /**
     * Senha numérica por exigência de operação — teclado numérico, com luva, na
     * câmara. Ver seguranca/Senhas.kt.
     */
    private fun exigirSenhaValida(senha: String) {
        if (senha.length < 4 || !senha.all { it.isDigit() }) {
            throw Erros.invalido("SENHA_INVALIDA", "A senha deve ter ao menos 4 dígitos numéricos")
        }
    }

    private fun contarAdminsAtivos(c: Connection): Int =
        c.buscarUm("SELECT COUNT(*) FROM usuario WHERE perfil='ADMIN' AND ativo=1") { it.getInt(1) } ?: 0

    private fun listarUm(c: Connection, id: Long): UsuarioResumo = c.buscarUm(
        """SELECT u.id, u.login, u.nome, u.perfil, u.multi_item, u.ativo, u.criado_em,
                  EXISTS(SELECT 1 FROM leitura l WHERE l.usuario_id = u.id)
             FROM usuario u WHERE u.id = ?""",
        id,
    ) {
        UsuarioResumo(
            it.getLong(1), it.getString(2), it.getString(3), it.getString(4),
            it.getInt(5) == 1, it.getInt(6) == 1, it.getString(7), it.getInt(8) == 1,
        )
    }!!

    private data class Linha(
        val id: Long, val login: String, val nome: String,
        val hash: String, val salt: String, val perfil: String,
        val multiItem: Boolean, val impressoraId: Long?, val ativo: Boolean,
    )
}
