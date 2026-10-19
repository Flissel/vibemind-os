<#
.SYNOPSIS
    Legt die geplante Aufgabe des Marketing-Waechters an oder entfernt sie.

.BESCHREIBUNG
    Die Aufgabe "VibeMind Marketing (Waechter)" ruft alle 5 Minuten
    `pythonw -m spaces.marketing.claw.dienst_waechter` auf (unsichtbar). Was ein Lauf tut,
    steht im Kopf von claw/dienst_waechter.py. Abschalten ohne die Aufgabe zu entfernen:
    leere Datei logs\marketing\waechter.aus anlegen.

.EXAMPLE
    powershell -File marketing-waechter.ps1 -Install
    powershell -File marketing-waechter.ps1 -Remove
#>
[CmdletBinding()]
param([switch]$Install, [switch]$Remove)

$ErrorActionPreference = 'Stop'
$TaskName = 'VibeMind Marketing (Waechter)'
$SpaceRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)   # spaces/marketing
$OsRoot    = Split-Path -Parent (Split-Path -Parent $SpaceRoot)      # vibemind-os
$Root      = Split-Path -Parent $OsRoot                             # Vibemind_V1
$Pythonw   = Join-Path $Root '.venv\Scripts\pythonw.exe'

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Aufgabe entfernt: $TaskName"
} elseif ($Install) {
    if (-not (Test-Path $Pythonw)) { throw "pythonw fehlt: $Pythonw" }
    $action = New-ScheduledTaskAction -Execute $Pythonw -Argument '-m spaces.marketing.claw.dienst_waechter' -WorkingDirectory $OsRoot
    # Zwei Ausloeser wie beim Rowboat-Waechter: die Wiederholung eines Anmelde-Ausloesers beginnt erst
    # mit der naechsten Anmeldung; der Einmal-Ausloeser ab jetzt traegt sie sofort.
    $logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $now = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
    $logon.Repetition = $now.Repetition
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden `
        -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 4)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($logon, $now) -Settings $settings -Force | Out-Null
    Write-Output "Aufgabe angelegt: $TaskName -> $Pythonw -m spaces.marketing.claw.dienst_waechter"
} else {
    Write-Output 'Bitte -Install oder -Remove angeben.'
}
