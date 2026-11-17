# Cria um atalho para a retaguarda dentro da pasta de rede.
#
# Nao e um executavel: e um arquivo .url que abre o navegador padrao no
# endereco do servidor. Qualquer maquina da rede consegue usar, sem instalar
# nada e sem copiar nada.

$ErrorActionPreference = 'Stop'

$destino = Read-Host "Pasta de rede (Enter para \\SERVIDOR\Logistica\AppSeparaRedespacho)"
if ([string]::IsNullOrWhiteSpace($destino)) {
    $destino = '\\SERVIDOR\Logistica\AppSeparaRedespacho'
}

$ip = Read-Host "IP ou nome do servidor (ex.: 192.168.0.10)"
if ([string]::IsNullOrWhiteSpace($ip)) { throw "Informe o endereco do servidor." }

$porta = Read-Host "Porta (Enter para 8080)"
if ([string]::IsNullOrWhiteSpace($porta)) { $porta = '8080' }

New-Item -ItemType Directory -Force $destino | Out-Null
$arquivo = Join-Path $destino 'Separacao Redespacho.url'

@"
[InternetShortcut]
URL=http://${ip}:${porta}/
IconIndex=0
"@ | Set-Content -Path $arquivo -Encoding ASCII

Write-Host ""
Write-Host "Atalho criado em $arquivo" -ForegroundColor Green
Write-Host "Qualquer maquina da rede pode abrir com dois cliques."
Write-Host ""
Write-Host "Para consulta sem risco, crie um usuario com perfil CONSULTA"
Write-Host "na aba Usuarios: ele so enxerga, nao altera nada."
Write-Host ""
