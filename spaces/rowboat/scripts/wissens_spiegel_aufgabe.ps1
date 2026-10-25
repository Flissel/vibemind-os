#Requires -Version 7
<#
.SYNOPSIS
    Wissens-Spiegel: legt die Aufgabe "VibeMind Wissens-Spiegel" (alle 5 min) an/entfernt sie oder laeuft einmal.
.DESCRIPTION
    Die Aufgabe startet `pythonw spaces\rowboat\scripts\wissens_spiegel.py` im vibemind-os-Checkout, in dem
    dieses Skript liegt (gedacht: Haupt-Checkout). Als Datei, nicht per `-m`: spaces\rowboat\__init__.py
    importiert ein fehlendes `.config` und bricht `-m spaces.rowboat.scripts...` ab; der Spiegel braucht nur stdlib.
    pythonw hat kein Konsolenfenster und keine Standardeingabe; ssh bekommt seine Eingabe immer explizit vom Spiegel. Log: <VIBEMIND_ROOT>\logs\rowboat\wissens_spiegel.log,
    Zustand: %LOCALAPPDATA%\VibeMind\wissens_spiegel\zuletzt.json.
    Ohne Schalter: ein Lauf jetzt (python, Eingabe aus $null). -DryRun: ein Trockenlauf (--trocken).
    Anlegen (-Install) nur nach Betreiber-OK (Plan Rowboat-Zentrale Teil A, Global Constraints).
.EXAMPLE
    pwsh -File spaces\rowboat\scripts\wissens_spiegel_aufgabe.ps1 -DryRun
    pwsh -File spaces\rowboat\scripts\wissens_spiegel_aufgabe.ps1 -Install
    pwsh -File spaces\rowboat\scripts\wissens_spiegel_aufgabe.ps1 -Remove
#>
[CmdletBinding()]
param([switch]$Install, [switch]$Remove, [switch]$DryRun)
$ErrorActionPreference = 'Stop'
$taskName = 'VibeMind Wissens-Spiegel'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path   # vibemind-os
$outer = Split-Path $root -Parent                                     # Vibemind_V1

function Find-SpiegelPython {
    param([Parameter(Mandatory)][string]$Exe)   # 'pythonw.exe' oder 'python.exe'
    # Dritte Stelle: aeusseres Repo (VIBEMIND_ROOT, Vorgabe Desktop\Vibemind_V1) - der Betriebs-Checkout
    # unter ClaudeWork\vibemind-betrieb hat keine eigene .venv (Umstellung 25.10.2026).
    $vibemind = if ($env:VIBEMIND_ROOT) { $env:VIBEMIND_ROOT } else { 'C:\Users\User\Desktop\Vibemind_V1' }
    foreach ($venv in @((Join-Path $root '.venv'), (Join-Path $outer '.venv'), (Join-Path $vibemind '.venv'))) {
        $p = Join-Path $venv "Scripts\$Exe"
        if (Test-Path -LiteralPath $p) { return $p }
    }
    throw "Kein $Exe in $root\.venv oder $outer\.venv gefunden"
}

if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Aufgabe entfernt: $taskName"
} elseif ($Install) {
    $pyw = Find-SpiegelPython -Exe 'pythonw.exe'
    $argument = '"{0}"' -f (Join-Path $PSScriptRoot 'wissens_spiegel.py')
    $action = New-ScheduledTaskAction -Execute $pyw -Argument $argument -WorkingDirectory $root
    # Wie rowboat_waechter.ps1: Anmelde-Ausloeser plus Einmal-Ausloeser ab jetzt, beide mit 5-min-Wiederholung
    # (die Wiederholung eines Anmelde-Ausloesers beginnt sonst erst mit der naechsten Anmeldung).
    $logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $now = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
    $logon.Repetition = $now.Repetition
    # Ein Lauf darf die Uebertragung (300 s je ssh-Schritt) ausschoepfen; nie zwei Laeufe gleichzeitig
    # (zusaetzlich Sperrdatei %LOCALAPPDATA%\VibeMind\wissens_spiegel\lauf.lock im Spiegel selbst).
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($logon, $now) -Settings $settings -Force | Out-Null
    Write-Output "Aufgabe angelegt: $taskName -> $pyw $argument (in $root)"
} else {
    $py = Find-SpiegelPython -Exe 'python.exe'
    $spiegelArgs = @((Join-Path $PSScriptRoot 'wissens_spiegel.py'))
    if ($DryRun) { $spiegelArgs += '--trocken' }
    Push-Location $root
    try {
        $null | & $py @spiegelArgs
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    Write-Output ("Wissens-Spiegel: exit={0}{1}" -f $code, $(if ($DryRun) { ' (Trockenlauf)' } else { '' }))
    exit $code
}
