# programar.ps1 — Programa as postagens automáticas
#
# Uso:
#   .\programar.ps1                              → 30-45 min (padrão)
#   .\programar.ps1 -MinMin 15 -MaxMin 25        → intervalo personalizado
#   .\programar.ps1 -Horarios "09:00,14:00,20:00" → horários fixos por dia
#   .\programar.ps1 -Parar                       → cancela agendamentos

[CmdletBinding()]
param(
    [int]$MinMin = 30,
    [int]$MaxMin = 45,
    [string]$Horarios = "",
    [switch]$Parar,
    [switch]$Silencioso = $true
)

$BASE = $PSScriptRoot
Set-Location $BASE

# ─── Parar todos agendamentos ─────────────────────────────────────────────
if ($Parar) {
    Write-Host "Parando bot e agendamentos..." -ForegroundColor Yellow
    Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -like "*rastreador.py*" -or $_.CommandLine -like "*startup.py*"
    } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    # Remove tarefas do Task Scheduler
    Get-ScheduledTask -TaskName "BotOfertas-*" -ErrorAction SilentlyContinue |
        Unregister-ScheduledTask -Confirm:$false
    Write-Host "OK. Bot e agendamentos removidos." -ForegroundColor Green
    exit 0
}

# ─── Garante modo silencioso (não rouba foco) ────────────────────────────
if ($Silencioso) {
    $env:WHATSAPP_MODO_ATRAPALHA = "0"
    Write-Host "Modo SILENCIOSO ativo — postagens não atrapalham seu PC." -ForegroundColor Green
}

# ─── Modo horarios fixos: cria uma tarefa Windows por horario ────────────
if ($Horarios) {
    Write-Host "Programando horários fixos: $Horarios" -ForegroundColor Yellow

    # Primeiro remove tarefas anteriores
    Get-ScheduledTask -TaskName "BotOfertas-Post-*" -ErrorAction SilentlyContinue |
        Unregister-ScheduledTask -Confirm:$false

    $python = (Get-Command python).Source
    $script = Join-Path $BASE "rastreador.py"
    $lista = $Horarios -split "," | ForEach-Object { $_.Trim() }

    foreach ($h in $lista) {
        $taskName = "BotOfertas-Post-$($h -replace ':','h')"
        $action = New-ScheduledTaskAction -Execute $python `
            -Argument "`"$script`"" -WorkingDirectory $BASE
        $trigger = New-ScheduledTaskTrigger -Daily -At $h
        $settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -MultipleInstances IgnoreNew
        $principal = New-ScheduledTaskPrincipal `
            -UserId $env:USERNAME -LogonType Interactive
        Register-ScheduledTask -TaskName $taskName -Action $action `
            -Trigger $trigger -Settings $settings -Principal $principal `
            -Description "Postagem programada às $h" -Force | Out-Null
        Write-Host "  OK: postagem às $h" -ForegroundColor Green
    }

    Write-Host ""
    Write-Host "$($lista.Count) postagens programadas por dia." -ForegroundColor Cyan
    Write-Host "Ver com: Get-ScheduledTask -TaskName 'BotOfertas-Post-*'"
    exit 0
}

# ─── Modo intervalo aleatório (padrão) ───────────────────────────────────
if ($MinMin -gt $MaxMin) {
    Write-Host "ERRO: MinMin ($MinMin) maior que MaxMin ($MaxMin)" -ForegroundColor Red
    exit 1
}

Write-Host "Intervalo: $MinMin a $MaxMin minutos (aleatório)" -ForegroundColor Yellow

# Para instância atual se existir
Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like "*rastreador.py*--random*" -or
    $_.CommandLine -like "*startup.py*"
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

# Inicia com intervalo personalizado
$python = (Get-Command python).Source
$script = Join-Path $BASE "rastreador.py"
$log = Join-Path $BASE "data\rastreador_local.log"

Start-Process -FilePath $python `
    -ArgumentList "-u", "`"$script`"", "--random", `
                  "--loop-min", $MinMin, "--loop-max", $MaxMin `
    -WorkingDirectory $BASE `
    -RedirectStandardOutput $log `
    -RedirectStandardError "$log.err" `
    -WindowStyle Hidden

Start-Sleep -Seconds 5

# Confirma que subiu
$proc = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like "*rastreador.py*--random*"
}
if ($proc) {
    Write-Host ""
    Write-Host "OK — Bot rodando (PID $($proc[0].ProcessId))" -ForegroundColor Green
    Write-Host "Postagens automaticas a cada $MinMin-$MaxMin minutos"
    Write-Host ""
    Write-Host "Ver status:  .\status.ps1"
    Write-Host "Parar:       .\programar.ps1 -Parar"
    Write-Host "Ver log:     Get-Content data\rastreador_local.log -Wait -Tail 20"
}
else {
    Write-Host "ERRO: rastreador nao iniciou" -ForegroundColor Red
}
