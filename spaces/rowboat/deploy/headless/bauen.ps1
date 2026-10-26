<#
.SYNOPSIS
  Baut den Rowboat-Server ohne Oberflaeche (dist-headless) und uebertraegt ihn auf offload-vm.

.DESCRIPTION
  1. In -Quelle (apps/x des Betriebs-Checkouts): npm run deps, dann in apps/server npm run build:headless.
  2. Ohne -NurBauen: legt auf der VM ~/rowboat-headless/app und ~/rowboat-data (0700) an, falls sie fehlen,
     kopiert dist-headless nach -Ziel und Dockerfile, .dockerignore, compose.yml, start.sh,
     rowboat-firewall.nft eine Ebene darueber. Ein -Ziel im Arbeitsordner rowboat-data wird verweigert.
  3. Ohne -NurBauen ausserdem den Bau-Kontext des Claude-Shims nach ~/rowboat-headless/shim/:
     Dockerfile, start.sh, rowboat_shim_huelle.py aus shim/ hier, dazu als KOPIEN (nicht im Repo)
     claude_code_openai_shim.py und claude_code_openai_tools.py aus -ShimQuelle sowie
     scripts/claude_budget.py und config/agent_budget.yaml aus -BudgetRepo. Legt ~/rowboat-headless/budget
     (0700) an. Bricht vor dem Bauen ab, wenn eine Datei fehlt oder agent_budget.yaml rowboat-server nicht kennt.
  4. Ohne -NurBauen ausserdem den Bau-Kontext der Bedeutungssuche nach ~/rowboat-headless/wissen/:
     Dockerfile und requirements.txt aus wissen/ hier, das Paket spaces/rowboat/wissen_suche (*.py) nach
     wissen/wissen_suche/. Legt ~/rowboat-headless/wissen-index (0700) fuer den Index an.
  5. Ohne -NurBauen ausserdem die Disk-Skills skills/wissen-suchen/SKILL.md und
     skills/zentrale-werkzeuge/SKILL.md nach ~/rowboat-data/skills/<name>/ (nur diese Ordner; andere
     Skills bleiben unberuehrt).
  6. Ohne -NurBauen ausserdem den Bau-Kontext des Werkzeug-Containers (rowboat-werkzeuge) nach
     ~/rowboat-headless/werkzeuge/ (Dockerfile, server.py, github_lesen.py, gitea_lesen.py, pc_suche_relay.py) sowie
     werkzeuge-zugang-eintragen.sh und mcp-eintrag.json nach ~/rowboat-headless/. Die Zugangsdatei
     dort legt dieses Skript NIE an (sie kommt nur per rowboat-zugang ueber ssh-stdin).
  Mit -NurWerkzeuge: kein npm-Bau; kopiert nur Schritt 6 plus compose.yml und .dockerignore.
  Loescht nichts auf der VM; vorhandene Dateien gleichen Namens werden ueberschrieben.
  Der Container wird hier NICHT gestartet (siehe README.md).

.EXAMPLE
  pwsh -File spaces/rowboat/deploy/headless/bauen.ps1 -NurBauen
#>
param(
    [switch]$NurBauen,
    [switch]$NurWerkzeuge,
    [string]$Quelle = 'C:/Users/User/ClaudeWork/vibemind-betrieb/spaces/rowboat/rowboat/apps/x',
    [string]$Ziel = 'offload-vm:~/rowboat-headless/app/',
    [string]$ShimQuelle = (Join-Path $HOME '.local/bin'),
    [string]$BudgetRepo = 'C:/Users/User/Desktop/Vibemind_V1'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Invoke-Schritt([string]$Ort, [string[]]$Befehl) {
    Write-Host "==> ($Ort) $($Befehl -join ' ')"
    Push-Location $Ort
    try {
        $exe = $Befehl[0]
        # npm.ps1 liest seine Argumente aus der Aufrufzeile, nicht aus $args -> npm.cmd nehmen.
        if ($IsWindows -and $exe -eq 'npm') { $exe = 'npm.cmd' }
        $rest = @($Befehl | Select-Object -Skip 1)
        & $exe @rest
        if ($LASTEXITCODE -ne 0) { throw "Fehlgeschlagen (Exit $LASTEXITCODE): $($Befehl -join ' ')" }
    } finally {
        Pop-Location
    }
}

function Get-ZielTeile([string]$Ziel) {
    if ($Ziel -notmatch '^(?<host>[^:]+):(?<pfad>.+?)/?$') { throw "Ziel muss host:pfad sein: $Ziel" }
    return @($Matches.host, $Matches.pfad, ($Matches.pfad -replace '/[^/]+$', ''))
}

# Werkzeug-Container (Teil D): Bau-Kontext unter ~/rowboat-headless/werkzeuge/, Begleitdateien eine Ebene darueber.
$werkzeugeOrdner = Join-Path $PSScriptRoot 'werkzeuge'
$werkzeugeDateien = @('Dockerfile', 'server.py', 'github_lesen.py', 'gitea_lesen.py', 'pc_suche_relay.py')
$werkzeugeOben = @('werkzeuge-zugang-eintragen.sh', 'mcp-eintrag.json')
function Test-WerkzeugeKontext {
    foreach ($datei in $werkzeugeDateien) {
        if (-not (Test-Path (Join-Path $werkzeugeOrdner $datei))) { throw "Werkzeug-Kontext unvollstaendig: $werkzeugeOrdner/$datei" }
    }
    foreach ($datei in $werkzeugeOben) {
        if (-not (Test-Path (Join-Path $PSScriptRoot $datei))) { throw "Werkzeug-Kontext unvollstaendig: $datei" }
    }
}

if ($Ziel -match 'rowboat-data') { throw "Ziel darf nicht im Arbeitsordner rowboat-data liegen: $Ziel" }
if ($NurWerkzeuge) {
    # Kein npm-Bau: nur Werkzeug-Kontext, compose.yml und .dockerignore uebertragen.
    Test-WerkzeugeKontext
    $vmHost, $appPfad, $basis = Get-ZielTeile $Ziel
    Invoke-Schritt $PSScriptRoot @('ssh', $vmHost, "mkdir -p $basis/werkzeuge")
    Invoke-Schritt $werkzeugeOrdner (@('scp', '-q') + $werkzeugeDateien + @("${vmHost}:$basis/werkzeuge/"))
    Invoke-Schritt $PSScriptRoot (@('scp', '-q', 'compose.yml', '.dockerignore') + $werkzeugeOben + @("${vmHost}:$basis/"))
    Write-Host "Werkzeuge uebertragen nach ${vmHost}:$basis - Start: ssh $vmHost 'cd $basis && docker compose up -d --build rowboat-werkzeuge'"
    return
}
if (-not (Test-Path (Join-Path $Quelle 'package.json'))) { throw "Keine Rowboat-Quelle: $Quelle" }
$server = Join-Path $Quelle 'apps/server'

# Shim-Kontext vor dem langen Bau pruefen: (Ordner, Datei) - scp laeuft je Ordner mit relativen Namen.
$shimTeile = @(
    @((Join-Path $PSScriptRoot 'shim'), @('Dockerfile', 'start.sh', 'rowboat_shim_huelle.py')),
    @($ShimQuelle, @('claude_code_openai_shim.py', 'claude_code_openai_tools.py')),
    @((Join-Path $BudgetRepo 'scripts'), @('claude_budget.py')),
    @((Join-Path $BudgetRepo 'config'), @('agent_budget.yaml'))
)
# Bau-Kontext der Bedeutungssuche (rowboat-wissen): (Ordner, Dateien, Unterordner unter ~/rowboat-headless).
# Vom Paket nur die *.py-Dateien (kein __pycache__ aus lokalen Testlaeufen).
$wissenPaket = Join-Path $PSScriptRoot '../../wissen_suche'
$wissenTeile = @(
    @((Join-Path $PSScriptRoot 'wissen'), @('Dockerfile', 'requirements.txt'), 'wissen'),
    @($wissenPaket, @(Get-ChildItem -LiteralPath $wissenPaket -Filter '*.py' -Name), 'wissen/wissen_suche')
)
# Disk-Skill fuer Rowboat: nur dieser eine Ordner unter ~/rowboat-data/skills/ wird ueberschrieben.
$skillOrdner = Join-Path $PSScriptRoot 'skills/wissen-suchen'
$skillZiel = '~/rowboat-data/skills/wissen-suchen'
# Teil D: Skill zentrale-werkzeuge (GitHub lesen, Docs, Dashboard, PC-Suche), ebenfalls nur sein eigener Ordner.
$werkzeugSkillOrdner = Join-Path $PSScriptRoot 'skills/zentrale-werkzeuge'
$werkzeugSkillZiel = '~/rowboat-data/skills/zentrale-werkzeuge'
if (-not $NurBauen) {
    foreach ($teil in $shimTeile) {
        foreach ($datei in $teil[1]) {
            if (-not (Test-Path (Join-Path $teil[0] $datei))) { throw "Shim-Kontext unvollstaendig: $(Join-Path $teil[0] $datei)" }
        }
    }
    foreach ($teil in $wissenTeile) {
        foreach ($datei in $teil[1]) {
            if (-not (Test-Path (Join-Path $teil[0] $datei))) { throw "Wissen-Kontext unvollstaendig: $(Join-Path $teil[0] $datei)" }
        }
    }
    if (-not (Test-Path (Join-Path $skillOrdner 'SKILL.md'))) { throw "Skill fehlt: $skillOrdner/SKILL.md" }
    Test-WerkzeugeKontext
    if (-not (Test-Path (Join-Path $werkzeugSkillOrdner 'SKILL.md'))) { throw "Skill fehlt: $werkzeugSkillOrdner/SKILL.md" }
    if ($wissenTeile[1][1] -notcontains 'server.py') { throw "Paket wissen_suche ohne server.py: $wissenPaket" }
    $budgetYaml = Get-Content -Raw (Join-Path $BudgetRepo 'config/agent_budget.yaml')
    if ($budgetYaml -notmatch '(?m)^\s+rowboat-server:') { throw 'agent_budget.yaml kennt rowboat-server nicht (ohne Eintrag lehnt der Waechter jeden Aufruf ab)' }
}

# Rowboats npm-Skripte nutzen rm -rf; unter Windows laeuft npm sonst ueber cmd.exe ohne rm.
if ($IsWindows) {
    $bash = Join-Path (Split-Path (Split-Path (Get-Command git).Source)) 'bin/bash.exe'
    if (-not (Test-Path $bash)) { $bash = 'C:/Program Files/Git/bin/bash.exe' }
    if (-not (Test-Path $bash)) { throw 'Git Bash nicht gefunden (Rowboats npm-Skripte brauchen eine POSIX-Shell)' }
    $env:npm_config_script_shell = $bash
}

Invoke-Schritt $Quelle @('npm', 'run', 'deps')
Invoke-Schritt $server @('npm', 'run', 'build:headless')

$dist = Join-Path $server 'dist-headless'
$bundle = Join-Path $dist 'rowboat-server.cjs'
if (-not (Test-Path $bundle)) { throw "Bau ohne Ergebnis: $bundle fehlt" }
Write-Host "OK: $bundle ($([math]::Round((Get-Item $bundle).Length / 1MB, 1)) MB)"
if ($NurBauen) { return }

$vmHost, $appPfad, $basis = Get-ZielTeile $Ziel

Invoke-Schritt $PSScriptRoot @('ssh', $vmHost, "mkdir -p $appPfad $basis/shim $basis/wissen/wissen_suche $basis/werkzeuge && mkdir -p -m 0700 ~/rowboat-data && mkdir -p -m 0700 $basis/budget && mkdir -p -m 0700 $basis/wissen-index && mkdir -p $skillZiel $werkzeugSkillZiel")
# Relative Namen aus dist-headless heraus (Invoke-Schritt wechselt hinein und zurueck): kein Laufwerks-
# Doppelpunkt, den scp als host: deuten koennte; jedes Element ein eigenes Argument.
$namen = @(Get-ChildItem -LiteralPath $dist -Name)
Invoke-Schritt $dist (@('scp', '-q', '-r') + $namen + @("${vmHost}:$appPfad/"))
Invoke-Schritt $PSScriptRoot (@('scp', '-q', 'Dockerfile', '.dockerignore', 'compose.yml', 'start.sh', 'rowboat-firewall.nft', 'rowboat-firewall.service') + $werkzeugeOben + @("${vmHost}:$basis/"))
Invoke-Schritt $werkzeugeOrdner (@('scp', '-q') + $werkzeugeDateien + @("${vmHost}:$basis/werkzeuge/"))
foreach ($teil in $shimTeile) {
    Invoke-Schritt $teil[0] (@('scp', '-q') + $teil[1] + @("${vmHost}:$basis/shim/"))
}
foreach ($teil in $wissenTeile) {
    Invoke-Schritt $teil[0] (@('scp', '-q') + $teil[1] + @("${vmHost}:$basis/$($teil[2])/"))
}
Invoke-Schritt $skillOrdner @('scp', '-q', 'SKILL.md', "${vmHost}:$skillZiel/")
Invoke-Schritt $werkzeugSkillOrdner @('scp', '-q', 'SKILL.md', "${vmHost}:$werkzeugSkillZiel/")
Write-Host "Uebertragen nach ${vmHost}:$basis - Start: ssh $vmHost 'cd $basis && docker compose up -d --build'"
