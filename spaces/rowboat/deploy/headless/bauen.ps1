<#
.SYNOPSIS
  Baut den Rowboat-Server ohne Oberflaeche (dist-headless) und uebertraegt ihn auf offload-vm.

.DESCRIPTION
  1. In -Quelle (apps/x des Betriebs-Checkouts): npm run deps, dann in apps/server npm run build:headless.
  2. Ohne -NurBauen: legt auf der VM ~/rowboat-headless/app und ~/rowboat-data (0700) an, falls sie fehlen,
     kopiert dist-headless nach -Ziel und Dockerfile, compose.yml, start.sh eine Ebene darueber.
  Loescht nichts auf der VM; vorhandene Dateien gleichen Namens werden ueberschrieben.
  Der Container wird hier NICHT gestartet (siehe README.md).

.EXAMPLE
  pwsh -File spaces/rowboat/deploy/headless/bauen.ps1 -NurBauen
#>
param(
    [switch]$NurBauen,
    [string]$Quelle = 'C:/Users/User/ClaudeWork/vibemind-betrieb/spaces/rowboat/rowboat/apps/x',
    [string]$Ziel = 'offload-vm:~/rowboat-headless/app/'
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

if (-not (Test-Path (Join-Path $Quelle 'package.json'))) { throw "Keine Rowboat-Quelle: $Quelle" }
$server = Join-Path $Quelle 'apps/server'

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

if ($Ziel -notmatch '^(?<host>[^:]+):(?<pfad>.+?)/?$') { throw "Ziel muss host:pfad sein: $Ziel" }
$vmHost = $Matches.host
$appPfad = $Matches.pfad
$basis = $appPfad -replace '/[^/]+$', ''

Invoke-Schritt $PSScriptRoot @('ssh', $vmHost, "mkdir -p $appPfad && mkdir -p -m 0700 ~/rowboat-data")
$dateien = @(Get-ChildItem $dist | ForEach-Object FullName)
Invoke-Schritt $PSScriptRoot (@('scp', '-q', '-r') + $dateien + @("${vmHost}:$appPfad/"))
Invoke-Schritt $PSScriptRoot @('scp', '-q', 'Dockerfile', 'compose.yml', 'start.sh', "${vmHost}:$basis/")
Write-Host "Uebertragen nach ${vmHost}:$basis - Start: ssh $vmHost 'cd $basis && docker compose up -d --build'"
