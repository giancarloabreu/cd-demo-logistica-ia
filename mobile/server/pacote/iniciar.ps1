# Sobe o servidor — ou abre a consulta, se já houver um no ar.
#
# Antes de iniciar, verifica se outra máquina da rede já está servindo. Se
# estiver, abre o navegador nela em vez de criar um segundo servidor: dois
# servidores significam dois bancos, com numeração de instrução repetida e
# sem conserto possível.
#
# -SemPerguntas: nunca para esperando resposta. É como a tarefa agendada roda,
# no logon, sem ninguém olhando a tela.
param([switch]$SemPerguntas)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "Separacao Redespacho" -ForegroundColor Cyan

# --- 0. o pacote esta completo? ---------------------------------------------
# Primeiro porque e instantaneo, e porque o engano mais provavel e rodar o
# modelo do repositorio (server\pacote) em vez do ZIP montado: la existe
# iniciar.bat, mas nao existem lib\ nem jre\.
if (-not (Test-Path 'lib')) {
    Write-Host ""
    Write-Host "ERRO: esta pasta nao e o pacote completo." -ForegroundColor Red
    Write-Host "Falta a pasta 'lib' aqui do lado - sem ela nao ha o que executar."
    Write-Host ""
    Write-Host "A pasta certa e a que sai do ZIP de distribuicao e tem, lado a lado:"
    Write-Host "  iniciar.bat   lib\   jre\   coletor\   configuracao.padrao.txt"
    Write-Host ""
    Write-Host "Pasta atual: $PSScriptRoot" -ForegroundColor DarkGray
    Write-Host ""
    if (-not $SemPerguntas) { Read-Host "Enter para sair" }
    exit 1
}

# --- 0b. rodando de dentro da rede? -----------------------------------------
# O banco nasce ao lado deste script. Rodando de \\SERVIDOR\..., o SQLite iria
# para o compartilhamento — onde ele corrompe — e cada maquina que clicasse aqui
# criaria um servidor com numeracao propria de instrucao, sem conserto.
# O ZIP fica na rede; a pasta que roda fica no disco local.
$naRede = $PSScriptRoot.StartsWith('\\')
if (-not $naRede) {
    $letra = (Split-Path -Qualifier $PSScriptRoot).TrimEnd(':')
    $drive = Get-PSDrive -Name $letra -ErrorAction SilentlyContinue
    if ($drive -and $drive.DisplayRoot) { $naRede = $true }   # unidade mapeada
}
if ($naRede) {
    Write-Host ""
    Write-Host "ERRO: esta pasta esta na rede." -ForegroundColor Red
    Write-Host "  $PSScriptRoot"
    Write-Host ""
    Write-Host "O servidor tem de rodar do disco local. Na rede, o banco SQLite"
    Write-Host "corrompe, e cada maquina que abrisse daqui criaria um servidor"
    Write-Host "com numeracao de instrucao propria - sem conserto depois."
    Write-Host ""
    Write-Host "Copie a pasta para o disco local (ex.: C:\SeparacaoRedespacho)"
    Write-Host "e rode o iniciar.bat de la."
    Write-Host ""
    Write-Host "Para so consultar de outra maquina, use o atalho"
    Write-Host "'Separacao Redespacho.url' na pasta de rede."
    Write-Host ""
    if (-not $SemPerguntas) { Read-Host "Enter para sair" }
    exit 1
}

# --- configuracao.txt -------------------------------------------------------
# -Encoding UTF8 em toda leitura: o arquivo e UTF-8 sem BOM e o PowerShell 5.1
# o leria como ANSI. Sem isto, regravar o arquivo troca os acentos dos
# comentarios por lixo — e os comentarios sao metade da utilidade dele.
function Ler-Configuracao {
    $mapa = @{}
    Get-Content 'configuracao.txt' -Encoding UTF8 | ForEach-Object {
        $linha = $_.Trim()
        if ($linha -and -not $linha.StartsWith('#') -and $linha.Contains('=')) {
            $par = $linha.Split('=', 2)
            $mapa[$par[0].Trim()] = $par[1].Trim()
        }
    }
    return $mapa
}

# Reescreve uma chave preservando comentarios e ordem: o arquivo e documentacao
# tanto quanto configuracao, e reescrever tudo apagaria as explicacoes.
function Gravar-Configuracao($chave, $valor) {
    $linhas = Get-Content 'configuracao.txt' -Encoding UTF8
    $achou = $false
    $novas = $linhas | ForEach-Object {
        if ($_ -match "^\s*$chave\s*=") { $achou = $true; "$chave=$valor" } else { $_ }
    }
    if (-not $achou) { $novas += "$chave=$valor" }
    $novas | Set-Content 'configuracao.txt' -Encoding UTF8
}

# O ZIP traz configuracao.padrao.txt, e nao configuracao.txt: extrair uma
# versao nova por cima nao pode desfazer o que ja foi ajustado aqui (a pasta de
# backup, a porta). Na primeira vez, a padrao vira a configuracao.
if (-not (Test-Path 'configuracao.txt') -and (Test-Path 'configuracao.padrao.txt')) {
    Copy-Item 'configuracao.padrao.txt' 'configuracao.txt'
}

$cfg       = Ler-Configuracao
$porta     = if ($cfg.PORTA)   { $cfg.PORTA }   else { '8080' }
$banco     = if ($cfg.BANCO)   { $cfg.BANCO }   else { 'dados\redespacho.db' }
$logs      = if ($cfg.LOGS)    { $cfg.LOGS }    else { 'logs' }
$backup    = $cfg.BACKUP
$backupMin = if ($cfg.BACKUP_MIN) { $cfg.BACKUP_MIN } else { '15' }

function Abrir($url, $motivo) {
    Write-Host ""
    Write-Host $motivo -ForegroundColor Yellow
    Write-Host "Abrindo $url" -ForegroundColor Green
    Write-Host ""
    Start-Process $url
}

<#
Diz se um caminho de rede responde, sem ficar pendurado.

Test-Path num compartilhamento fora do ar espera o timeout do SMB — dezenas de
segundos com a janela parada, sem explicar nada. Job com Wait-Job nao resolve:
Stop-Job espera a propria thread presa terminar. A saida e nem chegar no SMB:
bate na porta 445 do servidor com prazo curto e so continua se ele atender.

Caminho local sempre "responde" - quem decide se existe e o Test-Path depois.
#>
function Rede-Responde($caminho, $prazoMs = 1500) {
    if (-not $caminho -or -not $caminho.StartsWith('\\')) { return $true }
    $maquina = $caminho.TrimStart('\').Split('\')[0]
    if (-not $maquina) { return $false }

    $cliente = New-Object System.Net.Sockets.TcpClient
    try {
        $tentativa = $cliente.BeginConnect($maquina, 445, $null, $null)
        if (-not $tentativa.AsyncWaitHandle.WaitOne($prazoMs, $false)) { return $false }
        $cliente.EndConnect($tentativa)
        return $true
    } catch {
        return $false
    } finally {
        $cliente.Close()
    }
}

# --- 1. já está rodando nesta máquina? --------------------------------------
try {
    $r = Invoke-RestMethod "http://localhost:$porta/api/v1/saude" -TimeoutSec 3
    Abrir "http://localhost:$porta/" "O servidor ja esta rodando nesta maquina (versao $($r.versao))."
    exit 0
} catch { }

# --- 2. a pasta de backup responde? -----------------------------------------
# O backup e a unica coisa que depende da rede. Nao respondendo, resolve agora
# em vez de deixar o problema para o dia em que a maquina morrer.
$backupInacessivel = $false

# Sem ninguem olhando (tarefa de logon), a rede costuma subir alguns segundos
# depois do logon. Desistir no primeiro teste deixava o servidor o dia inteiro
# sem backup, sem sinal de presenca e sem conferir a restauracao.
if ($backup -and $SemPerguntas -and -not (Rede-Responde $backup)) {
    Write-Host "Esperando a rede (ate 3 minutos)..." -ForegroundColor DarkGray
    for ($t = 0; $t -lt 36 -and -not (Rede-Responde $backup); $t++) { Start-Sleep -Seconds 5 }
}

while ($backup -and -not $backupInacessivel -and -not (Rede-Responde $backup)) {
    Write-Host ""
    Write-Host "A pasta de backup nao respondeu:" -ForegroundColor Yellow
    Write-Host "  $backup"
    Write-Host ""
    Write-Host "O backup e a copia do banco na rede. Sem ele o sistema funciona"
    Write-Host "normalmente, mas uma pancada nesta maquina leva os dados junto."
    Write-Host ""

    # Seguir sem a rede nao desliga o backup: o servidor recebe a pasta mesmo
    # assim, tenta de novo a cada ciclo e avisa na retaguarda enquanto nao chegar.
    if ($SemPerguntas) {
        Write-Host "Seguindo; o servidor tenta o backup de novo sozinho e avisa na retaguarda." -ForegroundColor DarkGray
        $backupInacessivel = $true
        break
    }

    Write-Host "  [1] Informar outro caminho"
    Write-Host "  [2] Seguir agora (o servidor tenta o backup de novo sozinho)"
    Write-Host "  [3] Desativar o backup de vez"
    Write-Host "  [4] Tentar de novo"
    Write-Host ""
    switch ((Read-Host "Opcao").Trim()) {
        '1' {
            $novo = (Read-Host "Caminho da pasta de backup").Trim().TrimEnd('\')
            if (-not $novo) { continue }
            try {
                # Cria de verdade: permissao de leitura sem escrita passaria no
                # teste e falharia so na primeira copia, 15 minutos depois.
                New-Item -ItemType Directory -Force $novo -ErrorAction Stop | Out-Null
                $prova = Join-Path $novo '.teste-escrita'
                Set-Content $prova 'ok' -ErrorAction Stop
                Remove-Item $prova -Force -ErrorAction SilentlyContinue

                Gravar-Configuracao 'BACKUP' $novo
                $backup = $novo
                Write-Host "Pasta de backup agora e $novo" -ForegroundColor Green
            } catch {
                Write-Host "Nao consegui criar ou gravar em $novo" -ForegroundColor Red
                Write-Host "  $($_.Exception.Message)" -ForegroundColor DarkGray
            }
        }
        '2' { $backupInacessivel = $true }
        '3' {
            Gravar-Configuracao 'BACKUP' ''
            $backup = $null
            Write-Host "Backup desativado no configuracao.txt." -ForegroundColor Yellow
        }
        '4' { }
        default { Write-Host "Escolha 1, 2, 3 ou 4." -ForegroundColor DarkGray }
    }
}

# O sinal de presença mora ao lado da pasta de backup.
$presenca = $null
if ($backup) { $presenca = Join-Path (Split-Path -Parent $backup) 'servidor-ativo.txt' }

# --- 3. outra máquina já está servindo? -------------------------------------
# Com a rede fora, Test-Path num caminho \\ fica pendurado no timeout do SMB.
if ($presenca -and -not $backupInacessivel -and (Test-Path $presenca)) {
    try {
        $sinal = @{}
        Get-Content $presenca | ForEach-Object {
            if ($_ -notmatch '^\s*#' -and $_.Contains('=')) {
                $par = $_.Split('=', 2); $sinal[$par[0].Trim()] = $par[1].Trim()
            }
        }
        $idade = (Get-Date).ToUniversalTime() - [datetime]::Parse($sinal['em']).ToUniversalTime()
        $outra = $sinal['maquina'] -and $sinal['maquina'] -ne $env:COMPUTERNAME

        # Sinal com mais de 3 minutos e de servidor que morreu: pode assumir.
        if ($outra -and $idade.TotalMinutes -lt 3) {
            # Confirma que responde mesmo antes de mandar o usuario para la.
            try { Invoke-RestMethod "$($sinal['url'])api/v1/saude" -TimeoutSec 3 | Out-Null } catch {
                Write-Host "Sinal de $($sinal['maquina']) esta la, mas o servidor nao responde." -ForegroundColor Yellow
                Write-Host "Subindo o servidor nesta maquina." -ForegroundColor Yellow
                $outra = $false
            }
            if ($outra) {
                Abrir $sinal['url'] "Ja existe um servidor no ar em $($sinal['maquina']). Nao vou subir outro."
                exit 0
            }
        }
    } catch {
        Write-Host "Sinal de presenca ilegivel; seguindo com a inicializacao." -ForegroundColor DarkGray
    }
}

# --- 4. Java ----------------------------------------------------------------
$java = if (Test-Path 'jre\bin\java.exe') { (Resolve-Path 'jre\bin\java.exe').Path }
        elseif (Get-Command java -ErrorAction SilentlyContinue) { 'java' }
        else { $null }

if (-not $java) {
    Write-Host ""
    Write-Host "ERRO: Java nao encontrado." -ForegroundColor Red
    Write-Host "A pasta 'jre' deveria ter vindo no ZIP, aqui do lado. Se ela sumiu,"
    Write-Host "extraia o ZIP de novo - ele traz o Java junto e nao exige instalar nada."
    Write-Host ""
    if (-not $SemPerguntas) { Read-Host "Enter para sair" }
    exit 1
}

# --- 5. sobe ----------------------------------------------------------------
New-Item -ItemType Directory -Force $logs | Out-Null
New-Item -ItemType Directory -Force (Split-Path -Parent $banco) | Out-Null

$env:REDESPACHO_PORTA      = $porta
$env:REDESPACHO_BANCO      = $banco
$env:REDESPACHO_LOGS       = $logs
$env:REDESPACHO_BACKUP     = $backup
$env:REDESPACHO_BACKUP_MIN = $backupMin
$env:REDESPACHO_PRESENCA   = $presenca
# O servidor avisa que a restauracao nao foi conferida, em vez de achar que esta em dia.
$env:REDESPACHO_BACKUP_INACESSIVEL = if ($backupInacessivel) { '1' } else { $null }
# Sem janela nao ha console: o log vai so para logs\servidor.log.
$env:REDESPACHO_CONSOLE    = 'NADA'

# javaw, sem janela: o servidor fica na bandeja do Windows, perto do relogio.
# A janela preta aberta o dia todo era fechada por engano e derrubava os
# coletores.
$javaw = if ($java -ne 'java') { Join-Path (Split-Path -Parent $java) 'javaw.exe' }
         else { (Get-Command javaw -ErrorAction SilentlyContinue).Source }
if (-not $javaw -or -not (Test-Path $javaw)) { $javaw = $java }

# Classpath exato do pacote: jar que sobrou de versao anterior, depois de
# extrair uma nova por cima, fica de fora.
$classpath = if (Test-Path 'lib\classpath.txt') { @('@lib\classpath.txt') } else { @('-cp', 'lib\*') }
$argumentos = @('-Xmx512m', '-Dfile.encoding=UTF-8') + $classpath + @('br.com.cddemo.redespacho.AplicacaoKt')
Write-Host "Iniciando na porta $porta" -NoNewline -ForegroundColor Green
# Sem redirecionar nada, de proposito. Com qualquer redirecionamento o
# Start-Process cria o processo herdando os handles desta janela, e o Java
# ficava preso a ela: a janela nao fechava, e quem esperasse o iniciar
# terminar (tarefa agendada, outro script) esperava para sempre. Erro de
# partida do javaw aparece em caixa de dialogo; o resto vai para o log.
$processo = Start-Process -FilePath $javaw -ArgumentList $argumentos -WorkingDirectory $PSScriptRoot -PassThru

# Espera responder. Pode demorar: quando o banco vem do backup da rede, a copia
# acontece antes de o servidor abrir a porta.
# Espera enquanto o processo estiver vivo, ate 15 minutos: copiar do backup
# um banco grande pela rede pode passar de 3 minutos, e desistir antes dizia
# "ERRO" com o servidor ainda subindo.
$saude = $null
for ($i = 0; $i -lt 900 -and -not $saude; $i++) {
    Start-Sleep -Seconds 1
    if ($processo.HasExited) { break }
    if ($i -eq 30) { Write-Host ""; Write-Host "Ainda subindo (pode estar conferindo ou copiando o backup)" -NoNewline -ForegroundColor DarkGray }
    try { $saude = Invoke-RestMethod "http://localhost:$porta/api/v1/saude" -TimeoutSec 2 }
    catch { if ($i % 2 -eq 0) { Write-Host "." -NoNewline } }
}
Write-Host ""

if (-not $saude) {
    Write-Host ""
    Write-Host "ERRO: o servidor nao respondeu." -ForegroundColor Red
    if ($processo.HasExited) { Write-Host "O processo terminou (codigo $($processo.ExitCode))." }
    Write-Host ""
    Write-Host "Ultimas linhas de $logs\servidor.log:" -ForegroundColor DarkGray
    Get-Content (Join-Path $logs 'servidor.log') -Tail 15 -Encoding UTF8 -ErrorAction SilentlyContinue
    Write-Host ""
    if (-not $SemPerguntas) { Read-Host "Enter para sair" }
    exit 1
}

Start-Sleep -Milliseconds 500
if ($processo.HasExited) {
    # Quem respondeu foi outro servidor (outra pasta, mesma porta); o novo desistiu.
    Write-Host "Ja havia um servidor respondendo na porta $porta; o que acabou de ser iniciado nao ficou no ar." -ForegroundColor Yellow
}
Write-Host "Servidor no ar em http://$($saude.enderecoLocal):$porta/ (versao $($saude.versao))" -ForegroundColor Green
if ($saude.backup) {
    Write-Host ""
    Write-Host $saude.backup -ForegroundColor Yellow
}
if ($saude.restauracao) {
    Write-Host ""
    Write-Host $saude.restauracao -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Ele fica na bandeja do Windows, perto do relogio (icone verde com R):"
Write-Host "  duplo clique no icone ...... abre a retaguarda"
Write-Host "  botao direito > Parar ...... encerra (ou use parar.bat)"
Write-Host ""
Write-Host "Esta janela fecha sozinha; o servidor continua rodando." -ForegroundColor DarkGray
if (-not $SemPerguntas) { Start-Sleep -Seconds 10 }
exit 0
