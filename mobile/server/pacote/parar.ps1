# Encerra o servidor Separacao Redespacho DESTA pasta.
#
# Pede pelo caminho normal (HTTP local): assim o servidor para de aceitar
# pedidos, faz a ultima copia de backup e so entao apaga o sinal de presenca.
# So se ele nao atender o processo e derrubado a forca - e ai a ultima copia
# fica sendo a de ate 15 minutos atras.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$porta = '8080'
if (Test-Path 'configuracao.txt') {
    Get-Content 'configuracao.txt' -Encoding UTF8 | ForEach-Object {
        if ($_ -match '^\s*PORTA\s*=\s*(\d+)') { $porta = $Matches[1] }
    }
}

# So os processos desta pasta: o Java embutido fica em jre\ aqui dentro.
# Antes qualquer servidor da maquina era derrubado - o de outra pasta, ou um
# de teste, tambem. Sem Java embutido (Java do sistema) nao ha como separar,
# e vale o criterio antigo.
# Get-CimInstance e nao wmic: o wmic foi retirado das versoes novas do Windows 11.
function Processos {
    $todos = Get-CimInstance Win32_Process -Filter "Name='javaw.exe' OR Name='java.exe'" |
        Where-Object { $_.CommandLine -like '*br.com.cddemo.redespacho*' }
    if (Test-Path (Join-Path $PSScriptRoot 'jre')) {
        # Com a barra no fim: sem ela, C:\SeparacaoRedespacho tambem "comeca"
        # C:\SeparacaoRedespacho-teste, e o servidor da outra pasta seria derrubado.
        $raiz = $PSScriptRoot.TrimEnd('\') + '\'
        $todos | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($raiz, [StringComparison]::OrdinalIgnoreCase) }
    } else {
        $todos
    }
}

if (@(Processos).Count -eq 0) {
    Write-Host "Nenhum servidor desta pasta rodando." -ForegroundColor DarkGray
    exit 0
}

try {
    Invoke-RestMethod -Method Post "http://localhost:$porta/api/v1/interno/parar" -TimeoutSec 5 | Out-Null
    Write-Host "Pedido de parada enviado; aguardando a ultima copia de backup" -NoNewline
} catch {
    Write-Host "O servidor nao atendeu na porta $porta." -NoNewline -ForegroundColor Yellow
}

# Ate 3 minutos: a ultima copia vai para a rede, e banco grande demora.
for ($i = 0; $i -lt 360 -and @(Processos).Count -gt 0; $i++) {
    Start-Sleep -Milliseconds 500
    if ($i % 4 -eq 0) { Write-Host "." -NoNewline }
}
Write-Host ""

$restantes = @(Processos)
if ($restantes.Count -gt 0) {
    Write-Host "Nao parou sozinho; encerrando a forca." -ForegroundColor Yellow
    $restantes | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
}
Write-Host "Servidor encerrado." -ForegroundColor Green
