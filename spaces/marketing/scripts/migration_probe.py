"""Migration und Proben in EINER Transaktion gegen die echte Datenbank, am Ende
ROLLBACK - aendert nichts. psql laeuft mit ON_ERROR_STOP (streng): der erste
Fehler bricht ab, die offene Transaktion verfaellt.

    python -m spaces.marketing.scripts.migration_probe spaces/marketing/db/056_newsletter_bilder.sql spaces/marketing/db/verify_056.sql
"""
from __future__ import annotations

import pathlib
import re
import sys

from spaces.marketing.sync import _db

_KLAMMER = re.compile(r"^[ \t]*(BEGIN|COMMIT|ROLLBACK)[ \t]*;[ \t]*$", re.IGNORECASE | re.MULTILINE)


def zusammensetzen(dateien: list[str]) -> str:
    teile = [_KLAMMER.sub("", pathlib.Path(d).read_text(encoding="utf-8")) for d in dateien]
    return "BEGIN;\n" + "\n".join(teile) + "\nROLLBACK;\n"


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    ausgabe = _db._run_psql(zusammensetzen(argv), None, streng=True)
    print(ausgabe[-3000:])
    print("PROBE OK (zurueckgerollt)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
