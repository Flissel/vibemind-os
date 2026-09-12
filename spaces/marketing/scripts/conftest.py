"""Dieses Verzeichnis enthaelt Skripte, keine Tests.

`real_case_test.py` ist ein Kommandozeilen-Werkzeug (`main(argv)`, kein
einziges `test_`), das eine ECHTE Schleife durch Mailcow, SMTP und IMAP
faehrt. Es heisst nur so, wie pytests Standardmuster `*_test.py` lautet —
und wurde deshalb bei jedem Suitelauf eingesammelt. Sein Modulrumpf laeuft
dabei mit, also auch seine Pfad-Zusicherung; die schlug fehl und nahm mit
`Interrupted: 1 error during collection` die GANZE Marketing-Suite mit
(gemessen 12.09.2026 — 360 Tests liefen erst, nachdem dieses Verzeichnis
ausgeschlossen war).

Der Pfadfehler ist getrennt behoben. Eingesammelt werden soll die Datei
trotzdem nicht: ein Smoke gegen echte Postfaecher gehoert nicht in einen
Suitelauf, der nebenher laufen koennen muss.
"""
collect_ignore = ["real_case_test.py"]
