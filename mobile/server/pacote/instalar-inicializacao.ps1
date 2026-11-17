# Registra o servidor para subir sozinho quando esta conta fizer logon.
#
# Tarefa criada na propria conta do usuario nao exige privilegio de
# administrador — e por isso o servico nao precisa ser um Servico do Windows.
# Ver docs/OPERACAO.md secao 1.

$ErrorActionPreference = 'Stop'
$pasta  = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $pasta 'iniciar.bat'
$nome   = 'Separacao Redespacho - Servidor'

if (-not (Test-Path $script)) { throw "iniciar.bat nao encontrado em $pasta" }

# Sem janela: o servidor roda em segundo plano, o log vai para arquivo.
# -SemPerguntas: no logon nao ha ninguem para responder. Pasta de rede fora do
# ar nao pode deixar o servidor esperando resposta que nunca vem.
$acao = New-ScheduledTaskAction -Execute 'cmd.exe' `
    -Argument "/c start `"`" /min `"$script`" -SemPerguntas" -WorkingDirectory $pasta

$gatilho = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$config = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilho `
    -Settings $config -Force | Out-Null

Write-Host ""
Write-Host "Tarefa '$nome' registrada." -ForegroundColor Green
Write-Host "O servidor sobe sozinho no proximo logon desta conta."
Write-Host ""
Write-Host "Para iniciar agora sem reiniciar:  Start-ScheduledTask -TaskName '$nome'"
Write-Host "Para remover:                      Unregister-ScheduledTask -TaskName '$nome'"
Write-Host ""
