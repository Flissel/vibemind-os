"""Geschaeftsfakten fuer den Marketing-Agenten (Paket D2, 19.10.2026).

Der Agent schrieb ohne jedes Wissen ueber den Sales-Bestand - "ueber 470 Firmen" musste er raten. Jetzt bekommt er
zusammengefasste Kennzahlen aus dem Laden-Schema der Firma: Kontakte (Firmen/Personen), Pipeline-Stufen, Herkunft,
Bereiche, Einwilligungen, wie viele ein Newsletter hoechstens erreicht, Termine, Vertraege, Empfehlungslisten und
das Gespraechsvolumen der letzten sechs Monate.

User-Entscheid D1: NUR ZAHLEN - keine Namen, keine Adressen, keine Nachrichtentexte, keine KI-Auswertung privater
Gespraeche; Zahlen immer exakt. Als privat markierte Kontakte (`enrichment ? '_privat'`) zaehlen nirgends mit.

`newsletter_hoechstens` folgt der Versandregel (sales-mcp server._newsletter_empfaenger: opt_in, E-Mail, nicht
archiviert); Sperrliste und Adresspruefung leben im Sales-Code - darum "hoechstens", nicht "genau".

Welche Firma welches Schema hat, steht in MARKETING_FAKTEN_SCHEMAS ("firma:schema,..."); Vorgabe nur
vibemind:sales. fin2gether/Ivan erst mit dem Paket "Marketing fuer Ivan".
"""
from __future__ import annotations

import datetime as _dt
import os
import re

from fastapi import APIRouter, Header, HTTPException, Query

from spaces.marketing.api.bilder import _agent_schluessel
from spaces.marketing.api.plan import _mandant
from spaces.marketing.api.pult import _lesen_einer

router = APIRouter(prefix="/api/fakten")

VORGABE = "vibemind:sales"
_PAAR = re.compile(r"^([a-z][a-z0-9_-]{0,40}):([a-z][a-z0-9_]{0,40})$")
HINWEIS = ("Nur Zahlen aus dem Sales-Bestand dieser Firma - keine Namen, keine Texte. "
           "newsletter_hoechstens: so viele Kontakte haben ausdruecklich eingewilligt und eine E-Mail; "
           "tatsaechlich erreicht ein Newsletter hoechstens so viele.")


def zuordnung(roh: str) -> dict[str, str]:
    """"firma:schema,..." -> {firma: schema}; ein ungueltiges Paar macht die ganze Zuordnung leer (fail-closed)."""
    aus: dict[str, str] = {}
    for teil in (roh or "").split(","):
        teil = teil.strip()
        if not teil:
            continue
        m = _PAAR.fullmatch(teil)
        if not m:
            return {}
        aus[m.group(1)] = m.group(2)
    return aus


def _sql(s: str) -> str:
    ohne_privat = "NOT (coalesce(l.enrichment, '{}'::jsonb) ? '_privat')"
    return f"""
WITH l AS (SELECT * FROM {s}.leads l WHERE {ohne_privat}),
z AS (SELECT
  count(*) AS kontakte,
  count(*) FILTER (WHERE l.enrichment->>'art' = 'firma') AS firmen,
  count(*) FILTER (WHERE l.enrichment->>'art' = 'person') AS personen,
  count(*) FILTER (WHERE l.status = 'meeting') AS termine,
  count(*) FILTER (WHERE l.consent_status = 'opt_in' AND coalesce(btrim(l.email), '') <> ''
                   AND NOT (coalesce(l.enrichment, '{{}}'::jsonb) ? 'archiviert')) AS newsletter_hoechstens,
  coalesce(sum(CASE WHEN jsonb_typeof(l.enrichment->'vertraege') = 'array'
                    THEN jsonb_array_length(l.enrichment->'vertraege') ELSE 0 END), 0) AS vertraege
  FROM l)
SELECT jsonb_build_object(
  'kontakte', z.kontakte, 'firmen', z.firmen, 'personen', z.personen, 'termine', z.termine,
  'newsletter_hoechstens', z.newsletter_hoechstens, 'vertraege', z.vertraege,
  'stufen', (SELECT coalesce(jsonb_object_agg(k, n), '{{}}') FROM
             (SELECT coalesce(l.status, 'ohne') k, count(*) n FROM l GROUP BY 1) x),
  'herkunft', (SELECT coalesce(jsonb_object_agg(k, n), '{{}}') FROM
               (SELECT coalesce(l.source, 'ohne') k, count(*) n FROM l GROUP BY 1) x),
  'einwilligung', (SELECT coalesce(jsonb_object_agg(k, n), '{{}}') FROM
                   (SELECT coalesce(l.consent_status, 'ohne') k, count(*) n FROM l GROUP BY 1) x),
  'bereiche', (SELECT coalesce(jsonb_object_agg(k, n), '{{}}') FROM
               (SELECT b k, count(*) n FROM l, jsonb_array_elements_text(
                  CASE WHEN jsonb_typeof(l.enrichment->'bereiche') = 'array' THEN l.enrichment->'bereiche'
                       ELSE '[]'::jsonb END) b GROUP BY 1) x),
  'empfehlungslisten', (SELECT count(*) FROM {s}.empfehlungslisten),
  'gespraeche_je_monat', (SELECT coalesce(jsonb_agg(jsonb_build_object('monat', m, 'ausgehend', a, 'antworten', k)
                                                    ORDER BY m), '[]') FROM
      (SELECT to_char(date_trunc('month', a.created_at), 'YYYY-MM') m,
              count(*) FILTER (WHERE a.type = 'nachricht_ausgehend') a,
              count(*) FILTER (WHERE a.type = 'kundenantwort') k
         FROM {s}.activities a JOIN l ON l.id = a.lead_id
        WHERE a.created_at >= date_trunc('month', now()) - interval '5 months'
          AND a.type IN ('nachricht_ausgehend', 'kundenantwort')
        GROUP BY 1) g)
) AS f FROM z"""


@router.get("")
def geschaeftsfakten(firma: str = Query(...), x_agent_key: str | None = Header(None)):
    _agent_schluessel(x_agent_key)
    m = _mandant(firma, "firma")
    schema = zuordnung(os.environ.get("MARKETING_FAKTEN_SCHEMAS", VORGABE)).get(m)
    if not schema:
        raise HTTPException(404, f"Fuer {m} gibt es keinen Laden mit Sales-Daten (MARKETING_FAKTEN_SCHEMAS)")
    zeile = _lesen_einer(lambda: _sql(schema)) or {}
    werte = zeile.get("f") if isinstance(zeile.get("f"), dict) else {}
    return {"firma": m, "stand": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "hinweis": HINWEIS, **werte}
