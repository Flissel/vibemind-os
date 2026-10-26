#Requires -Version 7
<#
.SYNOPSIS
    PC-Suche fuer Rowboat: legt die Aufgabe "VibeMind PC-Suche" (bei Anmeldung) an/entfernt/stoppt sie oder laeuft im Vordergrund.
.DESCRIPTION
    Die Aufgabe startet `pythonw spaces\rowboat\pc_suche\pc_suche.py --log <VIBEMIND_ROOT>\logs\rowboat\pc_suche.log`
    im vibemind-os-Checkout, in dem dieses Skript liegt. Als Datei, nicht per `-m`: spaces\rowboat\__init__.py
    bricht den Paket-Import ab; der Dienst braucht nur stdlib. Der Dienst lauscht nur auf 100.84.127.84:8149
    und nimmt nur Anfragen der VM (100.67.177.45) mit Schluessel an (Tresor: ROWBOAT_PC_SUCHE_KEY).
    Log nur Start/Stopp/Fehlertyp, keine Suchbegriffe.
    Ohne Schalter: Vordergrundlauf (python, Log auf der Konsole, Strg+C beendet).
    -Stop beendet die laufende Aufgabe per Stop-ScheduledTask (nie per Prozessmuster: ein Muster traf schon die eigene Shell).
    Anlegen (-Install) nur nach Betreiber-OK (Plan Rowboat-Zentrale Teil D, Global Constraints); dazu gehoert
    eine Firewall-Regel fuer 8149 nur von 100.67.177.45 (README/Task 8), die dieses Skript NICHT anlegt.
.EXAMPLE
    pwsh -File spaces\rowboat\pc_suche\pc_suche_aufgabe.ps1 -Install
    pwsh -File spaces\rowboat\pc_suche\pc_suche_aufgabe.ps1 -Stop
    pwsh -File spaces\rowboat\pc_suche\pc_suche_aufgabe.ps1 -Remove
#>
[CmdletBinding()]
param([switch]$Install, [switch]$Remove, [switch]$Stop)
$ErrorActionPreference = 'Stop'
$taskName = 'VibeMind PC-Suche'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path   # vibemind-os
$outer = Split-Path $root -Parent                                     # Vibemind_V1
$vibemind = if ($env:VIBEMIND_ROOT) { $env:VIBEMIND_ROOT } else { 'C:\Users\User\Desktop\Vibemind_V1' }
$logDatei = Join-Path $vibemind 'logs\rowboat\pc_suche.log'
$dienst = Join-Path $PSScriptRoot 'pc_suche.py'

function Find-SuchePython {
    param([Parameter(Mandatory)][string]$Exe)   # 'pythonw.exe' oder 'python.exe'
    # Wie wissens_spiegel_aufgabe.ps1: eigener Checkout, aeusseres Repo, dann VIBEMIND_ROOT.
    foreach ($venv in @((Join-Path $root '.venv'), (Join-Path $outer '.venv'), (Join-Path $vibemind '.venv'))) {
        $p = Join-Path $venv "Scripts\$Exe"
        if (Test-Path -LiteralPath $p) { return $p }
    }
    throw "Kein $Exe in $root\.venv, $outer\.venv oder $vibemind\.venv gefunden"
}

if ($Remove) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Aufgabe entfernt: $taskName"
} elseif ($Stop) {
    Stop-ScheduledTask -TaskName $taskName
    Write-Output "Aufgabe gestoppt: $taskName"
} elseif ($Install) {
    $pyw = Find-SuchePython -Exe 'pythonw.exe'
    $argument = '"{0}" --log "{1}"' -f $dienst, $logDatei
    $action = New-ScheduledTaskAction -Execute $pyw -Argument $argument -WorkingDirectory $root
    $logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    # Dauerdienst: kein Zeitlimit, nie zwei Instanzen, bei Absturz jede Minute neu (bis 999 Mal).
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden `
        -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $logon -Settings $settings -Force | Out-Null
    Write-Output "Aufgabe angelegt: $taskName -> $pyw $argument (in $root); Start mit Start-ScheduledTask oder bei Anmeldung"
} else {
    $py = Find-SuchePython -Exe 'python.exe'
    Push-Location $root
    try {
        & $py $dienst
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    Write-Output "PC-Suche: exit=$code"
    exit $code
}
