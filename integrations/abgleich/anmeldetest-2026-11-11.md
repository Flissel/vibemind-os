# Anmeldetest der 62 ungeprueften Vorlagen (11.11.2026)

Geprueft wurde der Weg, den OpenFang bei einem Klick auf "Anmelden" geht:
Ermittlung beim Anbieter, Selbstregistrierung als oeffentlicher Client mit dem
echten Rueckruf `http://127.0.0.1:4200/api/integrations/<id>/oauth/callback`,
Anmelde-Link, dann Aufruf der Anmeldeseite in einem Browser ohne Login.
Es wurde nichts angemeldet und kein Token geholt. Der laufende Daemon blieb
unberuehrt.

Werkzeug: OpenFang-Beispielprogramm `anmeldeweg_pruefen`
(`cargo run -p openfang-extensions --example anmeldeweg_pruefen -- <integrations> <aus.json> [id ...]`),
danach Sichtpruefung der Seiten per Playwright (Screenshots, keine Seitentexte).

Grenze: Ob nach dem Login wirklich Werkzeuge kommen, zeigt erst eine echte
Anmeldung. "Bereit" heisst: Registrierung angenommen, Anmelde- oder
Zustimmungsseite erscheint.

## Ohne Anmeldung (3)

| Vorlage | Ergebnis |
|---|---|
| openai-developers | verbindet, 5 Werkzeuge |
| skywatch | verbindet, 4 Werkzeuge |
| particl-market-research | braucht einen kostenpflichtigen API-Schluessel (401 ohne) |

## Bereit, nur Login fehlt (46)

aiera, amplitude, apollo, atlassian-rovo, attio, base44, carta-crm,
cb-insights, circleback, clickup, cloudflare, common-room, cube, daloopa,
deepnote, docket, egnyte, factset, fal, fireflies, granola, help-scout, hex,
heygen, hostinger, intercom, jam, linear, lovable, marcopolo, midpage,
motherduck, neon-postgres, netlify, outreach, picsart, posthog, quartr,
ranked-ai, readwise, responsive, statsig, superhuman, supabase, windsor-ai, wix

- lovable: erst mit festen Scopes (`SCOPES` im Erzeuger). Ohne scope leitet
  Lovable mit `invalid_scope` zurueck.
- posthog: fragt zuerst nach der Region (US/EU).
- marcopolo: Die Anmeldeseite liegt auf einem Staging-Host des
  Anmeldedienstes. Das ist so beim Anbieter eingetragen, nicht bei uns.

## Wahrscheinlich bereit, im Testbrowser nicht sichtbar (6)

Registrierung und Anmelde-Link klappten. Die Seite selbst sperrte den
automatischen Browser. In einem normalen Browser ist das meist kein Hindernis.

| Vorlage | Was der Testbrowser sah |
|---|---|
| notion | "Browser nicht kompatibel" |
| fiscal-ai | 403, Anfrage blockiert |
| hugging-face | 403 von CloudFront |
| replit | Cloudflare-Sperre |
| quicknode | Bot-Pruefung |
| pylon | leere Seite |

## Geht nicht (7)

| Vorlage | Grund |
|---|---|
| calendly | Registrierung 400: verlangt einen https-Rueckruf |
| teamwork-com | Registrierung 400: `invalid_redirect_uri`, lehnt 127.0.0.1 ab |
| highlevel | Registrierung 400: `invalid_client_metadata`, auch mit scope |
| yepcode | Registrierung 403: nur mit Zugangsschluessel |
| signnow | Anmeldeseite 400: `redirect_uri not allowed` |
| omni-analytics | Anmeldeseite 401: Rueckruf nicht auf der Freigabeliste |
| scite | Ermittlung scheitert: Anmeldeserver-Metadaten nicht lesbar |

Vier davon scheitern am Rueckruf `http://127.0.0.1`. Ein https-Rueckruf
(z. B. ueber tailscale serve) koennte sie freischalten; das waere ein eigenes
Vorhaben.

Hinweis: Der Test nutzte den Client-Namen "OpenFang VibeMind" (OpenFang main
`d67dab7`). Der laufende Daemon sendet bis zum naechsten Neubau noch
"OpenFang (VibeMind)"; Calendly lehnte Klammern ab, andere Anbieter nicht
nachweislich.
