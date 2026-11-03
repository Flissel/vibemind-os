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
    # EIN Ausloeser. Bis 03.11.2026 waren es zwei (Anmeldung + Einmal, beide mit 5-min-Wiederholung):
    # nach jeder Anmeldung lief der Waechter zweimal je 5 min, ~20 s versetzt - IgnoreNew greift nur bei
    # Ueberlappung. Der zweite Lauf startete Dienste, die der erste gerade gestartet hatte. Der
    # Einmal-Ausloeser mit Wiederholung laeuft auch nach einem Neustart weiter (gemessen: Takt vom 19.10.).
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden `
        -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 4)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Write-Output "Aufgabe angelegt: $TaskName -> $Pythonw -m spaces.marketing.claw.dienst_waechter"
} else {
    Write-Output 'Bitte -Install oder -Remove angeben.'
}
