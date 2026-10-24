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

.PARAMETER Liste
    Die Dienstliste als JSON ausgeben (Name, Port, Args, Was) und nichts starten. Liest der
    Marketing-Waechter (claw/dienst_waechter.py) - die Liste steht so nur an dieser einen Stelle.

.PARAMETER Nur
    Nur diese Dienste (Namen, kommagetrennt) betrachten. Der Waechter startet damit gezielt.
#>
[CmdletBinding()]
param([switch]$Pruefen, [switch]$Liste, [string[]]$Nur)

$ErrorActionPreference = 'Stop'

# Zwei Ebenen hoch aus claw/scripts/ ist der Space, vier Ebenen das Repo.
$SpaceRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)   # spaces/marketing
$OsRoot    = Split-Path -Parent (Split-Path -Parent $SpaceRoot)      # vibemind-os
$Root      = Split-Path -Parent $OsRoot                             # Vibemind_V1
$Venv      = Join-Path $Root '.venv\Scripts\python.exe'
$LogDir    = Join-Path $Root 'logs\marketing'

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
    },
    @{
        # Podcast (17.10.2026): Chatterbox Multilingual in eigener venv (Torch+CUDA).
        # Video-Claw (E5): zusaetzlich auf der Tailscale-Adresse des PCs, damit Laura auf der VM
        # ueber /voiceover sprechen laesst - angenommen nur von der VM (Firewall-Regel ebenso eng).
        # Port 8138 (19.10.2026): 8899 gehoert Lauras LivePortrait-Container (seit Juni, unless-stopped).
        # Solange der nicht lief, fiel die Doppelbelegung nicht auf.
        Name   = 'marketing_ton_dienst'
        Port   = 8138
        Python = 'C:\Users\User\ClaudeWork\chatterbox-venv\Scripts\python.exe'
        Args   = @('-u', '-m', 'spaces.marketing.claw.ton_dienst')
        Cwd    = $OsRoot
        Env    = @{
            HF_HOME             = 'E:\huggingface'
            TON_STIMMEN_ORDNER  = 'E:\marketing-stimmen'
            TON_DIENST_ADRESSEN = '100.84.127.84'
            TON_DIENST_ERLAUBT  = '100.67.177.45'
            # Standardstimme (User-Wahl 19.10.2026): Probe C aus den Poetry-Aufnahmen; 'eingebaut' = Modellstimme.
            TON_STANDARD_STIMME = 'eigene-c'
            # 24.10.2026: Gewichte-Kopie auf der SSD (E: kalt ~6 min). Vorladen + nie freigeben: Laden bei
            # Bedarf scheiterte am Commit-Limit des PCs (os error 1455), Stimmen muessen zuverlaessig sein.
            TON_MODELL_ORDNER   = 'C:\Users\User\.cache\chatterbox-mtl'
            TON_VORLADEN        = '1'
            TON_LEERLAUF_S      = '0'
        }
        Was    = 'Sprech-Dienst (Chatterbox Multilingual, kein API-Budget; Laura auf der VM via Tailscale)'
    },
    @{
        Name = 'marketing_ton_arbeiter'
        Port = 8135
        Args = @('-u', '-m', 'spaces.marketing.workers.ton_worker')
        Cwd  = $OsRoot
        Env  = @{ TON_STIMMEN_ORDNER = 'E:\marketing-stimmen' }
        Was  = 'Ton-Arbeiter (Podcast-Auftraege von der VM, spricht ueber :8138, MP3 in die Medien)'
    },
    # Video-Claw (17.10.2026, Plan 2026-10-17-video-claw-reels): Werkzeuge (nur loopback), eigene Shim mit
    # genau diesen Werkzeugen, Arbeiter. Laura-Schreibrechte gibt es nur hier, nicht beim Marketing-Agenten.
    @{
        Name = 'marketing_video_werkzeuge'
        Port = 8137
        Args = @('-u', '-m', 'spaces.marketing.video_claw.server')
        Cwd  = $OsRoot
        Env  = @{}
        Was  = 'Video-Claw MCP-Sidecar (enge Laura-Huellen, 127.0.0.1)'
    },
    @{
        Name = 'marketing_video_shim'
        Port = 8118
        Args = @((Join-Path $SpaceRoot 'claw\shim\marketing_shim.py'),
                 '--host', '127.0.0.1', '--port', '8118', '--timeout', '1800')
        Cwd  = $Root
        Env  = @{
            SHIM_EXTRA_MCP_CONFIG = (Join-Path $SpaceRoot 'video_claw\video-mcp.json')
            # reel_bauen wartet bis zu 10 min auf Laura; Vorgabe der CLI fuer MCP-Aufrufe ist kuerzer.
            MCP_TOOL_TIMEOUT      = '900000'
            VIBEMIND_AGENT        = 'marketing-video'
            VIBEMIND_BUDGET_MODUL = 'C:\Users\User\Desktop\Vibemind_V1\scripts\claude_budget.py'
        }
        Was  = 'Modell-Tuer :8118 nur fuer den Video-Claw (eigene Werkzeugliste, Budget marketing-video)'
    },
    @{
        Name = 'marketing_video_claw'
        Port = 8136
        Args = @('-u', '-m', 'spaces.marketing.workers.video_claw')
        Cwd  = $OsRoot
        Env  = @{}
        Was  = 'Video-Claw-Arbeiter (Reel-Auftraege von der VM, baut mit Laura, MP4 in die Medien)'
    },
    @{
        # Telegram (20.10.2026): eigene Modell-Tuer mit eigenem Budget-Namen, damit der Telegram-Assistent
        # die Tagesgrenze des Pult-Chats (marketing-chat) nicht mitbenutzt. Werkzeuge wie :8117.
        Name = 'marketing_telegram_shim'
        Port = 8143
        Args = @((Join-Path $SpaceRoot 'claw\shim\marketing_shim.py'),
                 '--host', '127.0.0.1', '--port', '8143', '--timeout', '900')
        Cwd  = $Root
        Env  = @{
            SHIM_EXTRA_MCP_CONFIG = (Join-Path $SpaceRoot 'claw\shim\marketing-mcp.json')
            VIBEMIND_AGENT        = 'marketing-telegram'
            VIBEMIND_BUDGET_MODUL = 'C:\Users\User\Desktop\Vibemind_V1\scripts\claude_budget.py'
        }
        Was  = 'Modell-Tuer :8143 nur fuer den Telegram-Assistenten (Budget marketing-telegram)'
    },
    @{
        Name = 'marketing_telegram_bot'
        Port = 8144
        Args = @('-u', '-m', 'spaces.marketing.workers.telegram_bot')
        Cwd  = $OsRoot
        Env  = @{}
        Was  = 'Telegram-Assistent @VibeMindMarketingBot (nur der Betreiber, fragt ueber :8143)'
    }
)

# Markenwissen-Vorlagen (Spec 2026-10-06): je Firma ohne Ordner eine leere Marke.md in der Rowboat-Ablage
# (ROWBOAT_WISSEN_ORDNER bleibt ungesetzt, es gilt die Vorgabe). Bestehendes bleibt unberuehrt; ein Fehler
# wird nur protokolliert und bricht den Start nicht ab.
function Initialize-Markenwissen {
    try {
        Push-Location $OsRoot
        $ausgabe = & $Venv -m spaces.marketing.claw.markenwissen vorlagen VibeMind fin2gether 2>&1
        if ($LASTEXITCODE -ne 0) { throw ($ausgabe | Out-String) }
        $ausgabe | ForEach-Object { Write-Output "  $_" }
    } catch {
        Write-Warning ("  Markenwissen-Vorlagen nicht angelegt: " + $_.Exception.Message.Trim())
    } finally {
        Pop-Location
    }
}

if ($Liste) {
    # -InputObject statt Pipe: so bleibt es auch in Windows PowerShell 5.1 ein JSON-Array.
    ConvertTo-Json -Depth 3 -Compress -InputObject @($Dienste | ForEach-Object {
        [pscustomobject]@{ name = $_.Name; port = $_.Port; args = @($_.Args); was = $_.Was }
    })
    return
}
if ($Nur) {
    $namen = @($Nur | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $unbekannt = @($namen | Where-Object { $_ -notin $Dienste.Name })
    if ($unbekannt) { throw "Unbekannte Dienste: $($unbekannt -join ', ')" }
    $Dienste = @($Dienste | Where-Object { $_.Name -in $namen })
}

# reportlab liegt NUR im gemeinsamen venv. Gemessen 12.09.2026: mit
# pyenv-Python gestartet, meldete der Agent korrekt „No module named
# 'reportlab'" - und konnte kein einziges PDF setzen.
if (-not (Test-Path $Venv)) {
    throw "Das gemeinsame venv fehlt: $Venv (ohne reportlab kann der Sidecar kein PDF setzen)"
}
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

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

    if ($d.Name -eq 'marketing_chat_arbeiter') { Initialize-Markenwissen }

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
