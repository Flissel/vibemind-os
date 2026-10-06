<#
.SYNOPSIS
    Startet die Host-Dienste der Marketing-Kette, falls sie nicht laufen.
    ComfyUI hat sein eigenes venv (E:\ComfyUI\.venv), der Eintrag nennt es in `Python`.

.BESCHREIBUNG
    WARUM ES DIESES SKRIPT GIBT (gemessen 15.09.2026):
    Marketing lag vom 12. bis zum 15.09. still, waehrend sales durchlief. Der
    Unterschied war nicht die Qualitaet, sondern die Betriebsform: sales-claws
    Dienste stehen als Container mit `restart: unless-stopped` im Compose und
    kommen nach jedem Neustart von selbst zurueck. Die Marketing-Dienste sind
    `Start-Process`-Kinder einer PowerShell-Sitzung - sie sterben mit ihr, und
    NIEMAND MERKT ES. Drei Tage lang konnte der Agent nichts erzeugen, und
    aufgefallen ist es erst, als jemand danach fragte.

    Dieses Skript ist idempotent: es prueft je Dienst den Port und startet nur,
    was schweigt. Es ist gefahrlos mehrfach aufrufbar - von Hand, aus einer
    geplanten Aufgabe, oder nach dem Launcher (der dieselben Dienste startet
    und ebenfalls ueberspringt, was schon laeuft).

    ES ERSETZT DEN LAUNCHER NICHT. Der startet den ganzen Stack; dieses Skript
    kuemmert sich um genau die drei Dienste, ohne die marketing-claw nichts tun
    kann. Die uebrigen Sidecars (bubble_*, laura_rowboat_export) bleiben
    unberuehrt - sie gehoeren zu anderen Ketten, und ein Starter, der mehr
    anfasst als noetig, wird zum zweiten Launcher.

    AUSGABE IN DATEIEN, NICHT IN EINE PIPE. Gemessen am 03.09.2026: Prozesse,
    deren Ausgabe an die startende PowerShell gepumpt wird, sterben mit ihr.
    `-RedirectStandard*` auf Dateien ueberlebt.

.PARAMETER Pruefen
    Nur nachsehen und berichten, nichts starten.
#>
[CmdletBinding()]
param([switch]$Pruefen)

$ErrorActionPreference = 'Stop'

# Zwei Ebenen hoch aus claw/scripts/ ist der Space, vier Ebenen das Repo.
$SpaceRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)   # spaces/marketing
$OsRoot    = Split-Path -Parent (Split-Path -Parent $SpaceRoot)      # vibemind-os
$Root      = Split-Path -Parent $OsRoot                             # Vibemind_V1
$Venv      = Join-Path $Root '.venv\Scripts\python.exe'
$LogDir    = Join-Path $Root 'logs\marketing'

# reportlab liegt NUR im gemeinsamen venv. Gemessen 12.09.2026: mit
# pyenv-Python gestartet, meldete der Agent korrekt „No module named
# 'reportlab'" - und konnte kein einziges PDF setzen.
if (-not (Test-Path $Venv)) {
    throw "Das gemeinsame venv fehlt: $Venv (ohne reportlab kann der Sidecar kein PDF setzen)"
}
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

$Dienste = @(
    @{
        Name = 'marketing_api'
        Port = 5510
        Args = @('-u', '-m', 'spaces.marketing.api.server')
        Cwd  = $OsRoot
        Env  = @{}
        Was  = 'Marketing-API (Entwuerfe, Vorlagen, Versandauftraege)'
    },
    @{
        Name = 'marketing_claw_mcp'
        Port = 8130
        Args = @('-u', '-m', 'spaces.marketing.claw.server')
        Cwd  = $OsRoot
        # MARKETING_PRUEFADRESSE und die Schluessel holt der Sidecar selbst aus
        # der Repo-.env (claw/server.py::_load_env_fallback) - hier steht
        # bewusst nichts davon, sonst gaebe es zwei Wahrheiten.
        Env  = @{}
        Was  = 'MCP-Sidecar (die Werkzeuge des Agenten)'
    },
    @{
        Name = 'marketing_claw_shim'
        Port = 8117
        Args = @((Join-Path $SpaceRoot 'claw\shim\marketing_shim.py'),
                 '--host', '127.0.0.1', '--port', '8117')
        Cwd  = $Root
        Env  = @{
            # Ohne diese Datei sieht die Claude-CLI die Marketing-Werkzeuge
            # nicht, und der Agent kann nur reden.
            SHIM_EXTRA_MCP_CONFIG = (Join-Path $SpaceRoot 'claw\shim\marketing-mcp.json')
            # openclaws [[...]]-Direktiven loesen sonst ein irrefuehrendes
            # „out of extra usage" aus (gemessen 03.09.2026).
            SHIM_NEUTRALIZE_DOUBLE_BRACKETS = '1'
            # Budget-Waechter (Spec 2026-10-06): ohne VIBEMIND_AGENT ist er aus.
            VIBEMIND_AGENT = 'marketing-chat'
            VIBEMIND_BUDGET_MODUL = 'C:\Users\User\Desktop\Vibemind_V1\scripts\claude_budget.py'
        }
        Was  = 'Modell-Tuer :8117 (eigene Shim-Instanz, :8114 bleibt unberuehrt)'
    },
    @{
        Name = 'marketing_vorlagen_arbeiter'
        Port = 8132
        Args = @('-u', '-m', 'spaces.marketing.workers.vorlagen_worker')
        Cwd  = $OsRoot
        Env  = @{}
        Was  = 'Vorlagen-Arbeiter (Terminkarten-Auftraege aus Sales)'
    },
    @{
        Name   = 'comfyui'
        Port   = 8188
        Python = 'E:\ComfyUI\.venv\Scripts\python.exe'
        Args   = @('main.py', '--listen', '127.0.0.1', '--port', '8188')
        Cwd    = 'E:\ComfyUI'
        Env    = @{}
        Was    = 'ComfyUI (Bildmodell FLUX.1-schnell, nur lokal)'
    },
    @{
        Name = 'marketing_bild_arbeiter'
        Port = 8133
        Args = @('-u', '-m', 'spaces.marketing.workers.bild_worker')
        Cwd  = $OsRoot
        Env  = @{}
        Was  = 'Bild-Arbeiter (Newsletter-Bilder, holt Auftraege von der VM)'
    },
    @{
        Name = 'marketing_chat_arbeiter'
        Port = 8134
        Args = @('-u', '-m', 'spaces.marketing.workers.chat_worker')
        Cwd  = $OsRoot
        Env  = @{}
        Was  = 'Chat-Arbeiter (Gestaltungs-Agent, fragt Claude ueber den Shim :8117)'
    }
)

function Test-Port([int]$Port) {
    $null -ne (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
               Select-Object -First 1)
}

$gestartet = 0
$liefen = 0
foreach ($d in $Dienste) {
    if (Test-Port $d.Port) {
        Write-Output ("  laeuft   :{0,-5} {1}" -f $d.Port, $d.Was)
        $liefen++
        continue
    }
    if ($Pruefen) {
        Write-Output ("  FEHLT    :{0,-5} {1}" -f $d.Port, $d.Was)
        continue
    }

    # Alten Stand wegrollen, damit die neue Ausgabe fuer sich steht.
    foreach ($suf in '.log', '.err.log') {
        $f = Join-Path $LogDir "$($d.Name)$suf"
        if (Test-Path $f) {
            $ziel = Join-Path $LogDir "$($d.Name)$($suf -replace '\.log$', '.prev.log')"
            Move-Item $f $ziel -Force -ErrorAction SilentlyContinue
        }
    }

    $env:PYTHONUNBUFFERED = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    # Nur der gestartete Dienst soll diese Schluessel erben: alten Stand merken
    # und direkt nach Start-Process zurueckrollen (sonst erbt z. B. der Chat-
    # Arbeiter VIBEMIND_AGENT des Shims und bucht unter dessen Namen).
    $vorher = @{}
    foreach ($k in $d.Env.Keys) {
        $vorher[$k] = [Environment]::GetEnvironmentVariable($k, 'Process')
        Set-Item -Path "env:$k" -Value $d.Env[$k]
    }

    $p = Start-Process -FilePath $(if ($d.Python) { $d.Python } else { $Venv }) -ArgumentList $d.Args -WorkingDirectory $d.Cwd `
        -RedirectStandardOutput (Join-Path $LogDir "$($d.Name).log") `
        -RedirectStandardError  (Join-Path $LogDir "$($d.Name).err.log") `
        -WindowStyle Hidden -PassThru
    foreach ($k in $d.Env.Keys) {
        if ($null -eq $vorher[$k]) { Remove-Item -Path "env:$k" -ErrorAction SilentlyContinue }
        else { Set-Item -Path "env:$k" -Value $vorher[$k] }
    }
    Write-Output ("  gestartet :{0,-5} PID {1,-6} {2}" -f $d.Port, $p.Id, $d.Was)
    $gestartet++
}

if ($gestartet -and -not $Pruefen) {
    # Kurz warten und nachsehen, ob sie auch stehenbleiben: ein Prozess, der
    # sofort stirbt, ist kein gestarteter Dienst. Der Log sagt dann warum.
    Start-Sleep -Seconds 8
    foreach ($d in $Dienste) {
        if (-not (Test-Port $d.Port)) {
            Write-Warning ("  :{0} antwortet nicht - siehe {1}" -f $d.Port,
                           (Join-Path $LogDir "$($d.Name).err.log"))
        }
    }
}
Write-Output ("  -> {0} liefen, {1} gestartet" -f $liefen, $gestartet)
