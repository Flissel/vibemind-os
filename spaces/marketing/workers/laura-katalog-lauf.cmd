@echo off
REM Ein Durchlauf des Laura-Videokatalogs nach Rowboat.
REM
REM WARUM ALS GEPLANTE AUFGABE: der Worker ist ein Start-Process-Kind einer
REM PowerShell-Sitzung und stirbt mit ihr -- dieselbe Bauart, die die
REM Marketing-Dienste vom 12. bis 15.09.2026 still hat liegen lassen, ohne dass
REM es jemandem auffiel. Am 22.09. gemessen: ALLE acht Katalogeintraege waren
REM veraltet, die Objekt-Adressen aus der Speicher-Kette hatten Rowboat nie
REM erreicht.
REM
REM Bewusst `--once` statt Dauerschleife: ein haengender Lauf blockiert dann
REM nicht alle folgenden, die naechste Ausfuehrung raeumt es von selbst auf.
cd /d C:\Users\User\Desktop\Vibemind_V1\vibemind-os
..\.venv\Scripts\python.exe -m spaces.marketing.workers.laura_rowboat_export --once >> C:\Users\User\Desktop\Vibemind_V1\logs\marketing\laura-katalog.log 2>&1
