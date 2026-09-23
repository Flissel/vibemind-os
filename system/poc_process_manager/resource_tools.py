"""
Resource Doctor — Zusatz-Tools fuer den Process Manager MCP
===========================================================
Ressourcen-Diagnose: wer macht Platte / CPU / RAM / Netz dicht?

Diagnose-Tools (read-only, greifen NICHT ein):
  - disk_pressure:    Auslastung pro Laufwerk ueber Zeit statt Momentaufnahme
  - disk_culprit:     Taeter fuer EIN Laufwerk: Datei-Events -> Prozess
  - io_top:           Prozesse nach I/O-Rate (laufwerksuebergreifend)
  - cpu_ram_hogs:     Dauerlast-CPU und RAM-Wachstum per Sampling
  - net_top:          Verbindungen pro Prozess + Adapter-Durchsatz
  - stale_workloads:  vergessene Test-/Build-Laeufe und Temp-Leichen
  - resource_report:  Triage ueber alle Achsen in einem Aufruf

Zum Beenden dient process_kill aus mcp_server.py. Messen und Handeln
bleiben bewusst getrennt.

Warum eigene Tools noetig sind:
  * Get-Counter ist auf lokalisiertem Windows unbrauchbar (deutsche
    Counter-Namen), deshalb ueberall WMI-Klassen — die sind sprachneutral.
  * Windows zaehlt I/O pro Prozess, aber NICHT pro Laufwerk. Diese Luecke
    schliesst disk_culprit mit einem FileSystemWatcher: der Pfad verraet
    die Workload, die Workload den Prozess.
"""

import json
import os
import re
import subprocess
import tempfile

# ── PowerShell-Helfer (self-contained, kein Import aus mcp_server) ──

def _ps(script: str, timeout: int = 90):
    """PowerShell ueber temporaere Skriptdatei ausfuehren (kein Quoting-Aerger)."""
    body = "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n" + script.strip()
    path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ps1", delete=False, encoding="utf-8") as f:
            f.write(body)
            path = f.name
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path],
            capture_output=True, timeout=timeout, encoding="utf-8", errors="replace",
        )
        return r.stdout.strip() if r.stdout else None
    except Exception:
        return None
    finally:
        if path:
            try:
                os.unlink(path)
            except Exception:
                pass


def _ps_json(script: str, timeout: int = 90):
    raw = _ps("& {\n" + script.strip() + "\n} | ConvertTo-Json -Depth 6 -Compress", timeout)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _as_list(v):
    """
    Ein Element -> Liste, nichts -> leere Liste.

    Achtung PowerShell-Falle: ein leeres Array serialisiert als Eigenschaft
    nicht zu [] sondern zu {} — ohne das Aussortieren leerer Objekte zaehlt
    man null Treffer als einen.
    """
    if v is None or v == "":
        return []
    items = v if isinstance(v, list) else [v]
    return [i for i in items if not (isinstance(i, dict) and not i)]


def _human(bps):
    """Bytes/s lesbar machen."""
    try:
        b = float(bps)
    except Exception:
        return "0 B/s"
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if b < 1024 or unit == "GB/s":
            return f"{b:.1f} {unit}"
        b /= 1024


# ═══════════════════════════════════════════════════════════
#  1) DISK PRESSURE — Auslastung pro Laufwerk ueber Zeit
# ═══════════════════════════════════════════════════════════

_PS_DISK_PRESSURE = r"""
$ErrorActionPreference = 'SilentlyContinue'

# Medientyp pro Laufwerksbuchstabe (HDD vs SSD aendert die Bewertung komplett)
$media = @{}
foreach ($part in (Get-Partition | Where-Object { $_.DriveLetter })) {
  $disk = Get-PhysicalDisk | Where-Object { $_.DeviceId -eq [string]$part.DiskNumber }
  $media[[string]$part.DriveLetter] = @{
    media = "$($disk.MediaType)"; bus = "$($disk.BusType)"; model = "$($disk.FriendlyName)"
  }
}

$space = @{}
foreach ($vol in (Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3')) {
  $space[$vol.DeviceID] = @{
    free_gb = [math]::Round($vol.FreeSpace / 1GB, 1)
    size_gb = [math]::Round($vol.Size / 1GB, 1)
  }
}

$acc = @{}
for ($i = 0; $i -lt __SAMPLES__; $i++) {
  foreach ($d in (Get-CimInstance Win32_PerfFormattedData_PerfDisk_LogicalDisk)) {
    if ($d.Name -notmatch '^[A-Za-z]:$') { continue }
    if (-not $acc.ContainsKey($d.Name)) {
      $acc[$d.Name] = @{ busy = @(); read = @(); write = @(); queue = @() }
    }
    $acc[$d.Name].busy  += [int](100 - $d.PercentIdleTime)
    $acc[$d.Name].read  += [int64]$d.DiskReadBytesPersec
    $acc[$d.Name].write += [int64]$d.DiskWriteBytesPersec
    $acc[$d.Name].queue += [int]$d.CurrentDiskQueueLength
  }
  Start-Sleep -Milliseconds 950
}

$out = @()
foreach ($key in $acc.Keys) {
  $b = $acc[$key].busy
  $letter = $key.Substring(0, 1)
  $out += [pscustomobject]@{
    drive           = $key
    busy_avg_pct    = [int][math]::Round(($b | Measure-Object -Average).Average, 0)
    busy_max_pct    = [int]($b | Measure-Object -Maximum).Maximum
    samples_hot     = [int]($b | Where-Object { $_ -ge 90 }).Count
    samples         = [int]$b.Count
    read_bps_avg    = [int64](($acc[$key].read | Measure-Object -Average).Average)
    write_bps_avg   = [int64](($acc[$key].write | Measure-Object -Average).Average)
    queue_max       = [int]($acc[$key].queue | Measure-Object -Maximum).Maximum
    media           = $media[$letter].media
    bus             = $media[$letter].bus
    model           = $media[$letter].model
    free_gb         = $space[$key].free_gb
    size_gb         = $space[$key].size_gb
  }
}
$out | Sort-Object busy_avg_pct -Descending
"""


def _verdict(row):
    """Auslastung und Durchsatz auseinanderhalten — das ist der springende Punkt."""
    busy = row.get("busy_avg_pct") or 0
    hot = row.get("samples_hot") or 0
    thr = (row.get("read_bps_avg") or 0) + (row.get("write_bps_avg") or 0)
    hdd = str(row.get("media") or "").upper() == "HDD"
    size = row.get("size_gb") or 0
    free = row.get("free_gb") or 0
    notes = []

    if size and free / size < 0.10:
        notes.append(f"nur noch {free:.0f} GB frei ({free / size * 100:.0f} %) — Platzproblem, nicht Lastproblem")

    if busy >= 80 or hot >= 2:
        if thr < 5 * 1024 * 1024:
            notes.append(
                "hohe Auslastung bei geringem Durchsatz "
                f"({_human(thr)}) — viele kleine Random-Writes"
                + (", auf einer HDD reicht das fuer 100 %" if hdd else "")
            )
        else:
            notes.append(f"hohe Auslastung bei {_human(thr)} — echter Durchsatz, vermutlich legitim")
        notes.append("naechster Schritt: disk_culprit auf diesem Laufwerk")
    elif busy >= 40:
        notes.append("spuerbare, aber unkritische Last")
    else:
        notes.append("unauffaellig")
    return "; ".join(notes)


async def disk_pressure(seconds: int = 10):
    """
    Auslastung aller Laufwerke ueber einen Zeitraum messen — nicht als Momentaufnahme.

    Liefert pro Laufwerk Durchschnitt und Maximum der Auslastung, Anzahl der
    Sekunden ueber 90 %, Durchsatz, Queue, Medientyp (HDD/SSD) und freien Platz.
    Wichtig: 100 % Auslastung bei wenig Durchsatz bedeutet viele kleine
    Random-Writes, nicht viel Datenvolumen — auf einer HDD genuegen dafuer
    schon rund 1 MB/s.

    Args:
        seconds: Messdauer in Sekunden (Standard: 10, sinnvoll 5-60)
    """
    seconds = max(3, min(int(seconds), 120))
    rows = _as_list(_ps_json(_PS_DISK_PRESSURE.replace("__SAMPLES__", str(seconds)), timeout=seconds + 45))
    if not rows:
        return json.dumps({"error": "Messung fehlgeschlagen (WMI-Perf-Klassen nicht lesbar?)"})

    for r in rows:
        r["read_avg"] = _human(r.get("read_bps_avg"))
        r["write_avg"] = _human(r.get("write_bps_avg"))
        r["verdict"] = _verdict(r)

    worst = rows[0]
    return json.dumps({
        "measured_seconds": seconds,
        "drives": rows,
        "busiest": worst.get("drive"),
        "next_step": (
            f"disk_culprit(drive='{str(worst.get('drive'))[0]}') fuer den Taeter"
            if (worst.get("busy_avg_pct") or 0) >= 40 else
            "keine auffaellige Plattenlast — CPU/RAM mit cpu_ram_hogs pruefen"
        ),
    }, indent=2, default=str)


# ═══════════════════════════════════════════════════════════
#  2) DISK CULPRIT — vom Laufwerk ueber die Datei zum Prozess
# ═══════════════════════════════════════════════════════════

_PS_DISK_CULPRIT = r"""
$ErrorActionPreference = 'SilentlyContinue'

$watcher = New-Object System.IO.FileSystemWatcher
$watcher.Path = '__DRIVE__:\'
$watcher.IncludeSubdirectories = $true
$watcher.InternalBufferSize = 65536
$watcher.NotifyFilter = [System.IO.NotifyFilters]::LastWrite -bor `
                        [System.IO.NotifyFilters]::FileName -bor `
                        [System.IO.NotifyFilters]::Size

$hits = @{}
$io = @{}
$deadline = (Get-Date).AddSeconds(__SECONDS__)
$nextIoSample = Get-Date

while ((Get-Date) -lt $deadline) {
  if ((Get-Date) -ge $nextIoSample) {
    foreach ($proc in (Get-CimInstance Win32_PerfFormattedData_PerfProc_Process)) {
      if ($proc.Name -eq '_Total' -or $proc.Name -eq 'Idle') { continue }
      $key = "$($proc.IDProcess)|$($proc.Name)"
      if (-not $io.ContainsKey($key)) { $io[$key] = @{ r = @(); w = @() } }
      $io[$key].r += [int64]$proc.IOReadBytesPersec
      $io[$key].w += [int64]$proc.IOWriteBytesPersec
    }
    $nextIoSample = (Get-Date).AddSeconds(5)
  }

  $change = $watcher.WaitForChanged([System.IO.WatcherChangeTypes]::All, 900)
  if (-not $change.TimedOut -and $change.Name) {
    if (-not $hits.ContainsKey($change.Name)) { $hits[$change.Name] = 0 }
    $hits[$change.Name] = $hits[$change.Name] + 1
  }
}

$hotPaths = @()
foreach ($k in ($hits.Keys | Sort-Object { $hits[$_] } -Descending | Select-Object -First 30)) {
  $hotPaths += [pscustomobject]@{ path = $k; events = [int]$hits[$k] }
}

$ioRows = @()
foreach ($k in $io.Keys) {
  $parts = $k -split '\|'
  $rd = [int64](($io[$k].r | Measure-Object -Average).Average)
  $wr = [int64](($io[$k].w | Measure-Object -Average).Average)
  # Schwelle bewusst niedrig: genau die kleinen Dauerschreiber sind der Fall,
  # der eine Platte auf 100 % bringt, ohne nennenswerten Durchsatz zu erzeugen.
  if (($rd + $wr) -lt 8192) { continue }
  $ioRows += [pscustomobject]@{
    proc_id = [int]$parts[0]; name = $parts[1]; read_bps = $rd; write_bps = $wr; total_bps = $rd + $wr
  }
}
$ioRows = $ioRows | Sort-Object total_bps -Descending | Select-Object -First 40

$allProcs = @()
foreach ($p in (Get-CimInstance Win32_Process)) {
  $cmd = "$($p.CommandLine)"
  if ($cmd.Length -gt 400) { $cmd = $cmd.Substring(0, 400) }
  $allProcs += [pscustomobject]@{
    proc_id = [int]$p.ProcessId; parent_id = [int]$p.ParentProcessId
    name = "$($p.Name)"; cmd = $cmd
  }
}

# @() erzwingt Array-Serialisierung auch bei 0 oder 1 Treffer
[pscustomobject]@{
  total_events = [int](($hits.Values | Measure-Object -Sum).Sum)
  hot_paths    = @($hotPaths)
  io           = @($ioRows)
  processes    = @($allProcs)
}
"""

# Pfad-Fragment -> Prozess-Stichworte. Sagt: "wer schreibt sowas ueblicherweise"
_PATH_HINTS = [
    (r"pytest-of-|pytest-\d|\.pytest_cache", ["pytest", "python"], "pytest-Arbeitsverzeichnis"),
    (r"node_modules|\.next|\.turbo|\.vite|\.parcel", ["node", "npm", "yarn", "pnpm", "vite", "next", "esbuild"], "Node-Build"),
    (r"target[\\/](debug|release)|RustTargets|\.cargo", ["cargo", "rustc", "rust-analyzer"], "Rust-Build"),
    (r"\.gradle|build[\\/]classes", ["gradle", "java", "kotlin"], "JVM-Build"),
    (r"\.vhdx|DockerDesktop|docker[\\/]wsl|ext4\.vhdx", ["docker", "vmwp", "wsl", "vmmem"], "Container-/WSL-Datenplatte"),
    (r"huggingface|ollama|\.safetensors|\.gguf", ["ollama", "python", "llama"], "Modell-Download oder -Cache"),
    (r"Temp[\\/]claude|claude-\w+-cwd", ["claude"], "Claude-Code-Arbeitsdateien"),
    (r"\.etl$|DiagOutputDir|RdClientAutoTrace", ["msrdc", "svchost", "mstsc"], "Windows-Tracing"),
    (r"pagefile\.sys|swapfile\.sys", ["System"], "Auslagerungsdatei"),
    (r"\.db-wal$|\.db-journal$|\.sqlite", [], "SQLite-Schreiblast (WAL/Journal)"),
    (r"\.log$|\.etl$", [], "Logdatei"),
    (r"uv-cache|\.pnpm-store|pip[\\/]cache", ["uv", "pip", "pnpm", "python", "node"], "Paket-Cache"),
]

# Segmente, die als Suchbegriff nichts taugen
_NOISE = {
    "temp", "tmp", "ws", "appdata", "local", "roaming", "users", "user", "data",
    "cache", "logs", "log", "bin", "obj", "src", "lib", "test", "tests", "new",
    "windows", "program files", "programdata", "documents", "desktop", "downloads",
}


def _tokens_from_path(path: str):
    """Aussagekraeftige Pfadsegmente als Suchbegriffe fuer CommandLines."""
    out = []
    for seg in re.split(r"[\\/]", path):
        seg = seg.strip()
        base = re.sub(r"\.\w{1,6}$", "", seg)
        if len(base) < 4 or base.lower() in _NOISE:
            continue
        if re.fullmatch(r"[0-9a-f\-]{8,}", base.lower()):  # GUIDs/Hashes bringen nichts
            continue
        out.append(base)
    return out[:6]


def _build_tree(proc_id, by_id, depth=6):
    """Elternkette hochlaufen — der Aufrufer verraet oft mehr als der Prozess selbst."""
    chain, seen, cur = [], set(), proc_id
    while cur and cur not in seen and len(chain) < depth:
        seen.add(cur)
        p = by_id.get(cur)
        if not p:
            break
        chain.append({"pid": p["proc_id"], "name": p["name"], "cmd": (p.get("cmd") or "")[:200]})
        cur = p.get("parent_id")
    return chain


def _rank_suspects(hot_paths, procs, io_rows, total_events=0):
    """
    Von den heissen Pfaden zurueck auf die Prozesse schliessen.

    Zwei Beweisarten, bewusst unterschiedlich gewichtet:
      * stark  — ein charakteristisches Pfadsegment steht woertlich in der
                 CommandLine. Traegt fuer sich allein.
      * schwach— nur ein Stichwort der Heuristik passt ('python', 'node' ...).
                 Das trifft auf Dutzende Prozesse zu und zaehlt deshalb NUR,
                 wenn der Prozess im Messfenster auch wirklich I/O gemacht hat.

    Entscheidend ist aber nicht die Art des Treffers, sondern WIE VIEL Last am
    Pfad haengt: ein Pfad mit 85 % aller Schreib-Events wiegt ein Vielfaches
    eines Pfads mit 2 %. Ohne diese Gewichtung gewinnt sonst ein guter
    Namenstreffer auf einer nebensaechlichen Datei — genau so hat eine fruehere
    Fassung den falschen Prozess beschuldigt.
    """
    by_id = {p["proc_id"]: p for p in procs}
    io_by_id = {r["proc_id"]: r for r in io_rows}
    total_events = max(int(total_events or 0), sum((h.get("events") or 0) for h in hot_paths), 1)
    scores = {}

    for hp in hot_paths[:12]:
        path, events = hp.get("path") or "", hp.get("events") or 0
        hints, label = [], None
        for pattern, kws, lbl in _PATH_HINTS:
            if re.search(pattern, path, re.I):
                hints.extend(k.lower() for k in kws)
                label = label or lbl
        strong_terms = [t.lower() for t in _tokens_from_path(path)]
        hint_terms = [h for h in dict.fromkeys(hints) if h not in strong_terms]

        for p in procs:
            proc_id = p["proc_id"]
            hay = ((p.get("cmd") or "") + " " + (p.get("name") or "")).lower()
            strong = [t for t in strong_terms if t and t in hay]
            weak = [t for t in hint_terms if t and t in hay]
            has_io = proc_id in io_by_id

            # Anteil an der Gesamtlast dominiert die Bewertung
            share = events / total_events
            if strong:
                factor, why, conf = 2.0, f"Pfadsegment '{strong[0]}' steht in der CommandLine", "hoch"
            elif weak and has_io:
                factor, why, conf = 1.0, f"{label or 'Heuristik'}: '{weak[0]}' passt, und der Prozess schreibt messbar", "mittel"
            else:
                continue
            gained = events * factor * (1 + 3 * share)

            entry = scores.setdefault(proc_id, {"score": 0, "why": [], "paths": [], "conf": conf})
            entry["score"] += gained
            entry["paths"].append(path)
            if conf == "hoch":
                entry["conf"] = "hoch"
            reason = f"{why} ({path}, {events}x)"
            if reason not in entry["why"]:
                entry["why"].append(reason)

    # Gemessenes I/O ist harte Evidenz — und je mehr davon, desto schwerer wiegt sie.
    # Ohne das haetten bei einem generischen Stichwort wie 'python' alle Treffer
    # denselben Punktestand, und die Reihenfolge waere Zufall.
    for proc_id, s in scores.items():
        io = io_by_id.get(proc_id)
        if io:
            mb = (io.get("total_bps") or 0) / (1024 * 1024)
            s["score"] = int(s["score"] * (2 + min(mb, 6)))

    ranked = []
    for proc_id, s in sorted(scores.items(), key=lambda kv: kv[1]["score"], reverse=True)[:5]:
        p = by_id.get(proc_id, {})
        io = io_by_id.get(proc_id, {})
        ranked.append({
            "pid": proc_id,
            "name": p.get("name"),
            "score": int(s["score"]),
            "confidence": s["conf"],
            "why": s["why"][:3],
            "hot_paths": list(dict.fromkeys(s["paths"]))[:3],
            "io_now": _human(io.get("total_bps", 0)) if io else "unter Messschwelle",
            "cmd": (p.get("cmd") or "")[:300],
            "tree": _build_tree(proc_id, by_id),
        })
    return ranked


async def disk_culprit(drive: str = "C", seconds: int = 20):
    """
    Findet heraus, WER ein bestimmtes Laufwerk belastet — bis auf Datei und Prozess.

    Windows zaehlt I/O pro Prozess, aber nicht pro Laufwerk. Diese Luecke wird hier
    mit einem FileSystemWatcher geschlossen: es wird mitgeschnitten, welche Dateien
    auf dem Laufwerk geschrieben werden, und die Pfade werden auf laufende Prozesse
    zurueckgefuehrt (Pfadsegmente gegen CommandLines, plus Heuristiken fuer pytest,
    Node-, Rust- und JVM-Builds, Docker/WSL, SQLite, Modell-Caches).

    Beendet nichts. Zum Beenden anschliessend process_kill mit der genannten PID
    benutzen und danach erneut messen.

    Args:
        drive: Laufwerksbuchstabe, z. B. 'E' oder 'E:' (Standard: C)
        seconds: Beobachtungsdauer (Standard: 20, sinnvoll 10-60)
    """
    letter = str(drive).strip().rstrip(":\\/").upper()[:1] or "C"
    seconds = max(5, min(int(seconds), 120))

    if not os.path.exists(letter + ":\\"):
        return json.dumps({"error": f"Laufwerk {letter}: existiert nicht"})

    data = _ps_json(
        _PS_DISK_CULPRIT.replace("__DRIVE__", letter).replace("__SECONDS__", str(seconds)),
        timeout=seconds + 60,
    )
    if not data:
        return json.dumps({"error": "Beobachtung fehlgeschlagen (Rechte auf dem Laufwerk?)"})

    hot_paths = _as_list(data.get("hot_paths"))
    procs = _as_list(data.get("processes"))
    io_rows = _as_list(data.get("io"))
    total = data.get("total_events") or 0
    suspects = _rank_suspects(hot_paths, procs, io_rows, total)

    if total == 0:
        summary = f"Keine Schreibzugriffe auf {letter}: in {seconds}s — die Last kommt woanders her."
    elif suspects:
        top = suspects[0]
        summary = (
            f"{total} Schreib-Events auf {letter}: in {seconds}s. "
            f"Hauptverdacht: PID {top['pid']} ({top['name']}). "
            f"Heissester Pfad: {hot_paths[0]['path']} ({hot_paths[0]['events']}x)."
        )
    else:
        summary = (
            f"{total} Events, heissester Pfad {hot_paths[0]['path']} ({hot_paths[0]['events']}x) — "
            "kein Prozess sicher zuzuordnen. Kandidaten in top_io_processes pruefen."
        )

    return json.dumps({
        "drive": letter + ":",
        "watched_seconds": seconds,
        "total_write_events": total,
        "summary": summary,
        "hot_paths": hot_paths[:15],
        "suspects": suspects,
        "top_io_processes": [
            {"pid": r.get("proc_id"), "name": r.get("name"), "io": _human(r.get("total_bps")),
             "read": _human(r.get("read_bps")), "write": _human(r.get("write_bps"))}
            for r in io_rows[:10]
        ],
        "note": "I/O-Raten gelten laufwerksuebergreifend; die Zuordnung zum Laufwerk kommt aus hot_paths.",
        "next_step": "process_kill(target=<PID>) — danach disk_pressure oder disk_culprit erneut zur Verifikation.",
    }, indent=2, default=str)


# ═══════════════════════════════════════════════════════════
#  3) IO TOP — Prozesse nach I/O-Rate
# ═══════════════════════════════════════════════════════════

_PS_IO_TOP = r"""
$ErrorActionPreference = 'SilentlyContinue'
$acc = @{}
for ($i = 0; $i -lt __SAMPLES__; $i++) {
  foreach ($proc in (Get-CimInstance Win32_PerfFormattedData_PerfProc_Process)) {
    if ($proc.Name -eq '_Total' -or $proc.Name -eq 'Idle') { continue }
    $key = "$($proc.IDProcess)|$($proc.Name)"
    if (-not $acc.ContainsKey($key)) { $acc[$key] = @{ r = @(); w = @() } }
    $acc[$key].r += [int64]$proc.IOReadBytesPersec
    $acc[$key].w += [int64]$proc.IOWriteBytesPersec
  }
  Start-Sleep -Milliseconds 1500
}
$rows = @()
foreach ($key in $acc.Keys) {
  $parts = $key -split '\|'
  $rd = [int64](($acc[$key].r | Measure-Object -Average).Average)
  $wr = [int64](($acc[$key].w | Measure-Object -Average).Average)
  if (($rd + $wr) -lt 32768) { continue }
  $procObj = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $parts[0])
  $cmd = "$($procObj.CommandLine)"
  if ($cmd.Length -gt 220) { $cmd = $cmd.Substring(0, 220) }
  $rows += [pscustomobject]@{
    proc_id = [int]$parts[0]; name = $parts[1]
    read_bps = $rd; write_bps = $wr; total_bps = $rd + $wr; cmd = $cmd
  }
}
$rows | Sort-Object total_bps -Descending | Select-Object -First __LIMIT__
"""


async def io_top(seconds: int = 6, limit: int = 15):
    """
    Prozesse nach durchschnittlicher I/O-Rate ueber mehrere Samples.

    Achtung: Windows zaehlt I/O pro Prozess laufwerksuebergreifend — diese Liste
    sagt WER viel schreibt, aber nicht WOHIN. Fuer die Zuordnung zu einem
    bestimmten Laufwerk disk_culprit benutzen.

    Args:
        seconds: ungefaehre Messdauer (Standard: 6)
        limit: maximale Anzahl Prozesse (Standard: 15)
    """
    samples = max(2, min(int(seconds) // 2, 20))
    limit = max(3, min(int(limit), 50))
    rows = _as_list(_ps_json(
        _PS_IO_TOP.replace("__SAMPLES__", str(samples)).replace("__LIMIT__", str(limit)),
        timeout=samples * 3 + 45,
    ))
    return json.dumps({
        "samples": samples,
        "processes": [{
            "pid": r.get("proc_id"), "name": r.get("name"),
            "read": _human(r.get("read_bps")), "write": _human(r.get("write_bps")),
            "total": _human(r.get("total_bps")), "cmd": r.get("cmd"),
        } for r in rows],
        "note": "Raten sind laufwerksuebergreifend. Fuer ein bestimmtes Laufwerk: disk_culprit.",
    }, indent=2, default=str)


# ═══════════════════════════════════════════════════════════
#  4) CPU / RAM — Dauerlast statt Momentaufnahme
# ═══════════════════════════════════════════════════════════

_PS_CPU_RAM = r"""
$ErrorActionPreference = 'SilentlyContinue'
$cores = (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors
$before = @{}
foreach ($p in (Get-Process)) {
  $before[$p.Id] = @{ cpu = $p.TotalProcessorTime.TotalSeconds; ram = [int64]$p.WorkingSet64 }
}
Start-Sleep -Seconds __SECONDS__
$rows = @()
foreach ($p in (Get-Process)) {
  if (-not $before.ContainsKey($p.Id)) { continue }
  $deltaCpu = $p.TotalProcessorTime.TotalSeconds - $before[$p.Id].cpu
  $pct = [math]::Round(($deltaCpu / __SECONDS__) / $cores * 100, 1)
  $deltaRam = [int](([int64]$p.WorkingSet64 - $before[$p.Id].ram) / 1MB)
  if ($pct -lt 1 -and [math]::Abs($deltaRam) -lt 25) { continue }
  $runtime = $null
  if ($p.StartTime) { $runtime = [int]((Get-Date) - $p.StartTime).TotalMinutes }
  $procObj = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $p.Id)
  $cmd = "$($procObj.CommandLine)"
  if ($cmd.Length -gt 200) { $cmd = $cmd.Substring(0, 200) }
  $rows += [pscustomobject]@{
    proc_id = $p.Id; name = $p.ProcessName; cpu_pct = $pct
    ram_mb = [int]($p.WorkingSet64 / 1MB); ram_delta_mb = $deltaRam
    runtime_min = $runtime; cmd = $cmd
  }
}
[pscustomobject]@{
  cores = $cores
  rows = @($rows | Sort-Object cpu_pct -Descending | Select-Object -First __LIMIT__)
}
"""


async def cpu_ram_hogs(seconds: int = 10, limit: int = 15):
    """
    Prozesse mit echter Dauerlast auf CPU und wachsendem RAM finden.

    Misst die CPU-Zeit zweimal und bildet die Differenz — dadurch werden auch
    stille Dauerlaeufer sichtbar, die im Task-Manager-Moment harmlos aussehen.
    ram_delta_mb zeigt Wachstum im Messfenster (Leak-Verdacht bei stetigem Plus).

    Args:
        seconds: Messfenster in Sekunden (Standard: 10)
        limit: maximale Anzahl Prozesse (Standard: 15)
    """
    seconds = max(3, min(int(seconds), 60))
    limit = max(3, min(int(limit), 50))
    data = _ps_json(
        _PS_CPU_RAM.replace("__SECONDS__", str(seconds)).replace("__LIMIT__", str(limit)),
        timeout=seconds + 60,
    ) or {}
    rows = _as_list(data.get("rows"))
    return json.dumps({
        "window_seconds": seconds,
        "logical_cores": data.get("cores"),
        "processes": rows,
        "hint": "cpu_pct ist auf alle Kerne normiert: 100 % = die ganze Maschine.",
    }, indent=2, default=str)


# ═══════════════════════════════════════════════════════════
#  5) NETZWERK
# ═══════════════════════════════════════════════════════════

_PS_NET = r"""
$ErrorActionPreference = 'SilentlyContinue'
$conns = Get-NetTCPConnection -State Established
$rows = @()
foreach ($grp in ($conns | Group-Object OwningProcess)) {
  $owner = [int]$grp.Name
  $procObj = Get-Process -Id $owner
  $remotes = (($grp.Group | Select-Object -ExpandProperty RemoteAddress -Unique) | Select-Object -First 6) -join ', '
  $external = ($grp.Group | Where-Object { $_.RemoteAddress -notmatch '^(127\.|::1|0\.0\.0\.0)' }).Count
  $rows += [pscustomobject]@{
    proc_id = $owner; name = "$($procObj.ProcessName)"
    connections = [int]$grp.Count; external = [int]$external; remotes = $remotes
  }
}
$statsBefore = Get-NetAdapterStatistics
Start-Sleep -Seconds 3
$adapters = @()
foreach ($s in (Get-NetAdapterStatistics)) {
  $prev = $statsBefore | Where-Object { $_.Name -eq $s.Name }
  if (-not $prev) { continue }
  $rx = [int64](($s.ReceivedBytes - $prev.ReceivedBytes) / 3)
  $tx = [int64](($s.SentBytes - $prev.SentBytes) / 3)
  if (($rx + $tx) -lt 2048) { continue }
  $adapters += [pscustomobject]@{ adapter = "$($s.Name)"; rx_bps = $rx; tx_bps = $tx }
}
[pscustomobject]@{
  by_process = @($rows | Sort-Object external, connections -Descending | Select-Object -First __LIMIT__)
  adapters = @($adapters)
}
"""


async def net_top(limit: int = 15):
    """
    Netzwerk-Ueberblick: offene Verbindungen pro Prozess und Durchsatz pro Adapter.

    Windows liefert Bandbreite ohne ETW-Tracing nicht pro Prozess. Deshalb hier
    zwei Achsen: Verbindungen pro Prozess (wer redet ueberhaupt, und wie viel
    davon nach draussen) und gemessener Durchsatz pro Netzwerkadapter.

    Args:
        limit: maximale Anzahl Prozesse (Standard: 15)
    """
    limit = max(3, min(int(limit), 50))
    data = _ps_json(_PS_NET.replace("__LIMIT__", str(limit)), timeout=60) or {}
    adapters = _as_list(data.get("adapters"))
    return json.dumps({
        "processes": [{
            "pid": r.get("proc_id"), "name": r.get("name"),
            "connections": r.get("connections"), "external": r.get("external"),
            "remotes": r.get("remotes"),
        } for r in _as_list(data.get("by_process"))],
        "adapters": [{
            "adapter": a.get("adapter"), "down": _human(a.get("rx_bps")), "up": _human(a.get("tx_bps")),
        } for a in adapters],
        "note": "Bandbreite pro Prozess liefert Windows ohne ETW nicht; Adapterwerte sind die Gesamtsumme.",
    }, indent=2, default=str)


# ═══════════════════════════════════════════════════════════
#  6) STALE WORKLOADS — vergessene Laeufe und Temp-Leichen
# ═══════════════════════════════════════════════════════════

_PS_STALE = r"""
$ErrorActionPreference = 'SilentlyContinue'
$pattern = 'pytest|unittest| jest|vitest|mocha|cargo |rustc |msbuild|dotnet build|tsc |webpack|rollup|gradle|maven|ninja|cmake|go build|tox |nox |playwright|cypress|docker build'
# Dauerlaeufer, die absichtlich lange leben - keine vergessenen Laeufe
$exclude = 'mcp[_\-/\\ ]|[_\-/\\@]mcp|mcp_server|language.?server|--serve|serve\b|daemon|watch\b|--watch|nodemon|Code\.exe'
$now = Get-Date
$procs = @()
foreach ($p in (Get-CimInstance Win32_Process)) {
  $cmd = "$($p.CommandLine)"
  if ($cmd -eq '' -or $cmd -notmatch $pattern) { continue }
  if ($cmd -match $exclude) { continue }
  $obj = Get-Process -Id $p.ProcessId
  if (-not $obj -or -not $obj.StartTime) { continue }
  $ageMin = [int]($now - $obj.StartTime).TotalMinutes
  if ($ageMin -lt __MINUTES__) { continue }
  $parent = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $p.ParentProcessId)
  if ($cmd.Length -gt 260) { $cmd = $cmd.Substring(0, 260) }
  $procs += [pscustomobject]@{
    proc_id = [int]$p.ProcessId; name = "$($p.Name)"; age_min = $ageMin
    cpu_s = [math]::Round($obj.TotalProcessorTime.TotalSeconds, 1)
    ram_mb = [int]($obj.WorkingSet64 / 1MB)
    orphaned = (-not $parent)
    cmd = $cmd
  }
}

$bases = @()
if ($env:TEMP) { $bases += $env:TEMP }
foreach ($v in (Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3')) {
  $candidate = Join-Path $v.DeviceID '\Temp'
  if (Test-Path $candidate) { $bases += $candidate }
}
$dirs = @()
foreach ($base in ($bases | Select-Object -Unique)) {
  foreach ($d in (Get-ChildItem $base -Directory -ErrorAction SilentlyContinue)) {
    if ($d.Name -notmatch 'pytest|tmp|temp|target|build|codex|test|worktree') { continue }
    if ($d.Name -match 'node-compile-cache|^\.?cache$|pip-cache|uv-cache') { continue }
    $ageH = [int]((Get-Date) - $d.LastWriteTime).TotalHours
    if ($ageH -lt __HOURS__) { continue }
    $dirs += [pscustomobject]@{
      path = $d.FullName; age_hours = $ageH
      last_write = $d.LastWriteTime.ToString('yyyy-MM-dd HH:mm')
    }
  }
}
[pscustomobject]@{
  processes = @($procs | Sort-Object age_min -Descending | Select-Object -First 25)
  temp_dirs = @($dirs | Sort-Object age_hours -Descending | Select-Object -First 25)
}
"""


async def stale_workloads(older_than_minutes: int = 60, temp_older_than_hours: int = 6):
    """
    Vergessene Test- und Build-Laeufe sowie verwaiste Temp-Verzeichnisse finden.

    Sucht laufende pytest/jest/cargo/msbuild/gradle/docker-build-Prozesse, die
    laenger laufen als erwartet (haeufigste Ursache stiller Dauerlast), markiert
    verwaiste Prozesse ohne lebenden Elternprozess und listet alte Temp-Ordner
    auf allen Laufwerken. Loescht und beendet nichts.

    Args:
        older_than_minutes: ab welchem Alter ein Lauf verdaechtig ist (Standard: 60)
        temp_older_than_hours: ab welchem Alter ein Temp-Ordner gemeldet wird (Standard: 6)
    """
    minutes = max(1, min(int(older_than_minutes), 10080))
    hours = max(1, min(int(temp_older_than_hours), 8760))
    data = _ps_json(
        _PS_STALE.replace("__MINUTES__", str(minutes)).replace("__HOURS__", str(hours)),
        timeout=120,
    ) or {}
    procs = _as_list(data.get("processes"))
    dirs = _as_list(data.get("temp_dirs"))
    orphans = [p for p in procs if p.get("orphaned")]
    return json.dumps({
        "threshold_minutes": minutes,
        "long_running": procs,
        "orphaned_count": len(orphans),
        "stale_temp_dirs": dirs,
        "summary": (
            f"{len(procs)} Test-/Build-Prozesse aelter als {minutes} min"
            + (f", davon {len(orphans)} verwaist" if orphans else "")
            + f"; {len(dirs)} Temp-Ordner aelter als {hours} h."
        ),
        "next_step": "Verdaechtige mit process_kill beenden; Temp-Ordner nur nach Ruecksprache loeschen.",
    }, indent=2, default=str)


# ═══════════════════════════════════════════════════════════
#  7) RESOURCE REPORT — Triage in einem Aufruf
# ═══════════════════════════════════════════════════════════

async def resource_report(deep: bool = True):
    """
    Gesamt-Triage: Platte, CPU/RAM und vergessene Laeufe in einem Aufruf.

    Misst zuerst die Plattenlast pro Laufwerk, danach CPU/RAM ueber ein
    Zeitfenster und sucht nach zu lange laufenden Test-/Build-Prozessen. Ist ein
    Laufwerk auffaellig und deep=True, wird dort automatisch der Taeter-Finder
    nachgeschaltet. Greift nicht ein.

    Args:
        deep: bei auffaelliger Plattenlast automatisch disk_culprit nachschalten
    """
    disk = json.loads(await disk_pressure(seconds=8))
    drives = disk.get("drives", [])
    hot = [d for d in drives if (d.get("busy_avg_pct") or 0) >= 60 or (d.get("samples_hot") or 0) >= 2]

    culprit = None
    if deep and hot:
        culprit = json.loads(await disk_culprit(drive=str(hot[0]["drive"])[0], seconds=15))

    cpu = json.loads(await cpu_ram_hogs(seconds=8, limit=10))
    stale = json.loads(await stale_workloads())

    findings = []
    for d in drives:
        if (d.get("busy_avg_pct") or 0) >= 60 or (d.get("samples_hot") or 0) >= 2:
            findings.append(f"{d['drive']} ausgelastet ({d['busy_avg_pct']} % im Schnitt): {d['verdict']}")
        size, free = d.get("size_gb") or 0, d.get("free_gb") or 0
        if size and free / size < 0.10:
            findings.append(f"{d['drive']} fast voll: {free:.0f} von {size:.0f} GB frei")
    if culprit and culprit.get("suspects"):
        top = culprit["suspects"][0]
        findings.append(f"Plattenlast-Verdacht: PID {top['pid']} ({top['name']}) — {top['why'][0] if top['why'] else ''}")
    for p in cpu.get("processes", [])[:3]:
        if (p.get("cpu_pct") or 0) >= 15:
            findings.append(f"CPU: {p['name']} (PID {p['proc_id']}) bei {p['cpu_pct']} %, laeuft seit {p.get('runtime_min')} min")
    if stale.get("long_running"):
        s = stale["long_running"][0]
        findings.append(f"Langlaeufer: {s['name']} (PID {s['proc_id']}) seit {s['age_min']} min")

    return json.dumps({
        "findings": findings or ["Nichts Auffaelliges gefunden."],
        "disk": disk,
        "disk_culprit": culprit,
        "cpu_ram": cpu,
        "stale": stale,
        "next_step": "Verdaechtige mit process_kill beenden, danach resource_report erneut zur Verifikation.",
    }, indent=2, default=str)


# ═══════════════════════════════════════════════════════════
#  Registrierung
# ═══════════════════════════════════════════════════════════

_TOOLS = (
    disk_pressure, disk_culprit, io_top,
    cpu_ram_hogs, net_top, stale_workloads, resource_report,
)


def register(mcp):
    """Alle Diagnose-Tools an einer bestehenden FastMCP-Instanz anmelden."""
    for fn in _TOOLS:
        mcp.tool()(fn)
    return [fn.__name__ for fn in _TOOLS]
