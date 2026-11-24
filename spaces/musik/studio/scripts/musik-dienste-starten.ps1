<#
.SYNOPSIS
    Startet die drei PC-Dienste des Musik-Studios, falls ihr /health nicht antwortet:
    Musik-Dienst, Musik-Shim (Modell-Tuer der Musik-Claw) und Musik-Telegram-Bot.

.BESCHREIBUNG
    Idempotent: Ein Dienst, dessen /health antwortet, wird nicht neu gestartet. Gestartet wird
    per Start-Process (versteckt) mit Ausgabe in Dateien (logs\musik\<name>.log und .err im
    Studio-Ordner), nie in eine Pipe: Kindprozesse mit Pipe-Ausgabe sterben mit der startenden
    Sitzung. Alle Pfade stammen aus $PSScriptRoot, nie aus dem Arbeitsordner des Aufrufers.

    Die Umgebung eines Dienstes gilt nur fuer dessen Kindprozess: sie wird vor dem Start gesetzt
    und danach zurueckgerollt. Geheimnisse (Bot-Token, Chat-ID, Link-Schluessel, Laura-Token)
    liest, druckt und reicht dieses Skript nie weiter; die Dienste laden sie selbst aus der .env
    des aeusseren Repos.

    SYSTEMPROMPT DER MUSIK-CLAW: Die Shim liest keinen Prompt aus der Umgebung. Sie nimmt die
    system-Nachricht der Anfrage (render_messages in marketing_shim.py). Den Prompt
    (spaces\musik\claw\AGENTS.md) schickt der Telegram-Bot selbst mit; die Shim bekommt ihn deshalb
    hier nicht noch einmal, sonst kaeme er doppelt an.

    Nicht ohne -Liste ausfuehren, wenn nur geprueft werden soll: es startet echte Dienste.

.PARAMETER Nur
    Nur diese Dienste (ein oder mehrere Namen, auch kommagetrennt).

.PARAMETER Liste
    Name, Port und Health-Pfad je Dienst als JSON ausgeben (fuer einen Waechter) und nichts starten.
#>
[CmdletBinding()]
param([string[]]$Nur, [switch]$Liste)

$ErrorActionPreference = 'Stop'

# scripts -> studio -> musik -> spaces -> vibemind-os -> aeusseres Repo (nur im Hauptcheckout vorhanden).
$StudioDir = Split-Path -Parent $PSScriptRoot
$MusikDir  = Split-Path -Parent $StudioDir
$OsRoot    = Split-Path -Parent (Split-Path -Parent $MusikDir)
$Outer     = Split-Path -Parent $OsRoot
$LogDir    = Join-Path $StudioDir 'logs\musik'
$StudioExe = Join-Path $StudioDir '.venv\Scripts'
$ShimSkript = Join-Path $OsRoot 'spaces\marketing\claw\shim\marketing_shim.py'
$ShimMcp    = Join-Path $MusikDir 'claw\musik-mcp.json'
$BudgetModul = Join-Path $Outer 'scripts\claude_budget.py'
$RootPython  = Join-Path $Outer '.venv\Scripts\python.exe'

# Die Umgebungen. Das Budget-Modul ist fail-open: fehlt es (z. B. in einem Arbeitsbaum ohne
# aeusseres Repo), wird die Variable weggelassen und die Pruefung ist aus.
$BudgetVorhanden = Test-Path -LiteralPath $BudgetModul
$Daten = if ($env:MUSIK_DATEN) { $env:MUSIK_DATEN } else { Join-Path $env:USERPROFILE 'VibemindDaten\musik' }

$EnvDienst = @{
    MUSIK_STUDIO_PORT = '8148'
    VIBEMIND_AGENT    = 'musik-kette'
    MUSIK_DATEN       = $Daten
}
$EnvShim = @{
    VIBEMIND_AGENT        = 'musik-claw'
    SHIM_EXTRA_MCP_CONFIG = $ShimMcp
    # laura_rendern wartet im Dienst, nicht in der CLI; die Vorgabe der CLI ist trotzdem zu kurz fuer lange Werkzeuge.
    MCP_TOOL_TIMEOUT      = '900000'
}
$EnvTelegram = @{
    MUSIK_TELEGRAM_PORT    = '8142'
    MUSIK_TELEGRAM_LLM_URL = 'http://127.0.0.1:8152/v1'
    MUSIK_STUDIO_URL       = 'http://127.0.0.1:8148'
}
if ($BudgetVorhanden) {
    $EnvDienst['VIBEMIND_BUDGET_MODUL'] = $BudgetModul
    $EnvShim['VIBEMIND_BUDGET_MODUL']   = $BudgetModul
}

$Dienste = @(
    @{
        Name   = 'musik_dienst'
        Port   = 8148
        Health = '/health'
        Datei  = Join-Path $StudioExe 'musik-dienst.exe'
        Args   = @()
        Cwd    = $StudioDir
        Env    = $EnvDienst
        Python = $false
        Muster = @()
        Was    = 'Musik-Dienst (MCP /mcp und JSON-Routen, arbeitet Bestellungen ab)'
    },
    @{
        Name   = 'musik_shim'
        Port   = 8152
        Health = '/health'
        Datei  = $RootPython
        Args   = @('-u', $ShimSkript, '--host', '127.0.0.1', '--port', '8152', '--timeout', '1800')
        Cwd    = $(if (Test-Path -LiteralPath $Outer) { $Outer } else { $OsRoot })
        Env    = $EnvShim
        Python = $true
        # Eigene Woerter, keine Teilstrings: sonst traefe jede Zahl mit 8152 oder jeder Editor.
        Muster = @('marketing_shim\.py(\s|"|$)', '(^|\s)--port\s+8152(\s|$)')
        Was    = 'Musik-Shim (Modell-Tuer der Musik-Claw, Claude Code als LLM, Budget musik-claw)'
    },
    @{
        Name   = 'musik_telegram'
        Port   = 8142
        Health = '/health'
        Datei  = Join-Path $StudioExe 'musik-telegram.exe'
        Args   = @()
        Cwd    = $StudioDir
        Env    = $EnvTelegram
        Python = $false
        Muster = @()
        Was    = 'Musik-Telegram-Bot (nur der eigene Chat, fragt ueber die Musik-Shim)'
    }
)

if ($Liste) {
    # -InputObject statt Pipe: so bleibt es auch in Windows PowerShell 5.1 ein JSON-Array.
    ConvertTo-Json -Depth 3 -Compress -InputObject @($Dienste | ForEach-Object {
        [pscustomobject]@{ name = $_.Name; port = $_.Port; health = $_.Health; was = $_.Was }
    })
    return
}

if ($Nur) {
    $namen = @($Nur | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $unbekannt = @($namen | Where-Object { $_ -notin $Dienste.Name })
    if ($unbekannt) { throw "Unbekannte Dienste: $($unbekannt -join ', ')" }
    $Dienste = @($Dienste | Where-Object { $_.Name -in $namen })
}

if (-not $BudgetVorhanden) {
    Write-Warning "claude_budget.py nicht gefunden ($BudgetModul) - Budget-Pruefung bleibt aus (fail-open)."
}

# Immer die Zahl-Adresse 127.0.0.1, nie den Namen (Namensaufloesung kostet auf diesem PC 2 s). Ohne Origin-Kopf:
# der Musik-Dienst weist Anfragen mit Origin ab.
function Test-Gesund($d) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri ("http://127.0.0.1:{0}{1}" -f $d.Port, $d.Health)
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

# Passt dieser Prozess zum Dienst? Gleicht das Programm ab, nicht die Kommandozeile allein: ein Teilstring wie
# "musik-dienst" steckt auch im Namen dieses Skripts und in jedem Wrapper, Editor oder Wachhund, der es nennt.
function Test-ProzessPasst($d, [string]$exePfad, [string]$befehl, [int]$prozessId) {
    if ($prozessId -eq $PID -or -not $exePfad -or -not $befehl) { return $false }
    $name = [IO.Path]::GetFileName($exePfad)
    if ($name -match '^(powershell|pwsh)(\.exe)?$') { return $false }
    if ($d.Python) {
        if ($name -notmatch '^pythonw?(\d[\d.]*)?\.exe$') { return $false }
        foreach ($m in $d.Muster) { if ($befehl -notmatch $m) { return $false } }
        return $true
    }
    try {
        return [string]::Equals([IO.Path]::GetFullPath($exePfad), [IO.Path]::GetFullPath($d.Datei),
                                [StringComparison]::OrdinalIgnoreCase)
    } catch { return $false }
}

# Lebt schon ein Prozess (laedt noch, /health noch zu), wird kein zweiter gestartet.
function Test-Kopie($d) {
    try {
        foreach ($p in Get-CimInstance Win32_Process -ErrorAction Stop) {
            if (Test-ProzessPasst $d $p.ExecutablePath $p.CommandLine $p.ProcessId) { return $true }
        }
    } catch { }
    return $false
}

function ConvertTo-Argument([string]$a) {
    if ($a -match '\s') { return '"' + $a + '"' }
    return $a
}

if (-not (Test-Path -LiteralPath $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

$gestartet = @()
foreach ($d in $Dienste) {
    if (Test-Gesund $d) {
        Write-Output ("  laeuft    :{0,-5} {1}" -f $d.Port, $d.Name)
        continue
    }
    if (Test-Kopie $d) {
        Write-Output ("  laeuft    :{0,-5} {1} (Prozess lebt, /health noch zu - kein zweiter Start)" -f $d.Port, $d.Name)
        $gestartet += $d
        continue
    }
    if (-not (Test-Path -LiteralPath $d.Datei)) {
        Write-Warning ("  FEHLT     :{0,-5} {1}: {2} nicht gefunden" -f $d.Port, $d.Name, $d.Datei)
        continue
    }

    $out = Join-Path $LogDir "$($d.Name).log"
    $err = Join-Path $LogDir "$($d.Name).err"
    foreach ($f in $out, $err) {
        if (Test-Path -LiteralPath $f) { Move-Item -LiteralPath $f -Destination "$f.prev" -Force -ErrorAction SilentlyContinue }
    }

    # Umgebung nur fuer dieses Kind: alten Stand merken, setzen, nach Start-Process zurueckrollen.
    $vorher = @{}
    $vorherU = [Environment]::GetEnvironmentVariable('PYTHONUNBUFFERED', 'Process')
    try {
        foreach ($k in $d.Env.Keys) {
            $vorher[$k] = [Environment]::GetEnvironmentVariable($k, 'Process')
            [Environment]::SetEnvironmentVariable($k, [string]$d.Env[$k], 'Process')
        }
        [Environment]::SetEnvironmentVariable('PYTHONUNBUFFERED', '1', 'Process')
        $start = @{
            FilePath               = $d.Datei
            WorkingDirectory       = $d.Cwd
            RedirectStandardOutput = $out
            RedirectStandardError  = $err
            WindowStyle            = 'Hidden'
            PassThru               = $true
        }
        if ($d.Args.Count -gt 0) { $start.ArgumentList = @($d.Args | ForEach-Object { ConvertTo-Argument $_ }) }
        $p = Start-Process @start
    } finally {
        foreach ($k in $vorher.Keys) { [Environment]::SetEnvironmentVariable($k, $vorher[$k], 'Process') }
        [Environment]::SetEnvironmentVariable('PYTHONUNBUFFERED', $vorherU, 'Process')
    }
    Write-Output ("  gestartet :{0,-5} PID {1,-6} {2}" -f $d.Port, $p.Id, $d.Name)
    $gestartet += $d
}

# Bis zu 20 s auf /health warten, dann je Dienst berichten.
$frist = (Get-Date).AddSeconds(20)
$offen = @($gestartet)
while ($offen.Count -gt 0 -and (Get-Date) -lt $frist) {
    $offen = @($offen | Where-Object { -not (Test-Gesund $_) })
    if ($offen.Count -gt 0) { Start-Sleep -Seconds 1 }
}
foreach ($d in $gestartet) {
    if ($d -in $offen) {
        Write-Output ("  NICHT ERREICHBAR :{0,-5} {1} - siehe {2}" -f $d.Port, $d.Name, (Join-Path $LogDir "$($d.Name).err"))
    } else {
        Write-Output ("  OK               :{0,-5} {1}" -f $d.Port, $d.Name)
    }
}
