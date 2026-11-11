# Integrationen: die nächsten Stufen

Stand 11.11.2026. Grundlage sind der Anmeldetest (`integrations/abgleich/anmeldetest-2026-11-11.md`)
und eine neue Prüfung am selben Tag (unten). Felix' Auftrag: einen Plan für die nächsten
Schritte machen, alles durchtesten und überall dort, wo er einen Account braucht, eine
Aufgabe in Rowboat anlegen.

## Was am 11.11. ohne Anmeldung getestet wurde

Ein Prüfskript hat für jeden Anbieter die Ermittlung von OpenFang Schritt für Schritt
nachgestellt. Es notiert jede Regel, die verletzt wird, und versucht danach mit gelockerten
Regeln die Selbstregistrierung. Weiter als bis zur Anmeldeseite kommt kein Test ohne
Felix' Login. Ob danach wirklich Werkzeuge kommen, zeigt erst Stufe 1.

### Die 38 Anbieter, die OpenFangs Prüfung ablehnt

| Gruppe | Anbieter | Was abweicht | Registrierung |
|---|---|---|---|
| A: OpenFang liest zu eng (normgerecht lösbar) | airtable, brighthire, monday-com, stripe | Die Anmeldeserver-Beschreibung liegt unter dem Pfad-Einschub nach RFC 8414 (`/.well-known/oauth-authorization-server/<pfad>`). OpenFang hängt nur hinten an. | klappt |
| A | brand24, cloudinary, digitalocean, fyxer, otter-ai, razorpay, vantage | Die Schutzbeschreibung nennt als `resource` den Ursprung (`https://mcp.x.com`) statt der vollen Adresse. OpenFang lässt das nur bei der Ursprungs-Variante gelten. | klappt |
| A | replayio | `resource` nennt einen anderen Pfad auf demselben Host | klappt |
| B: Endpunkte auf einem Nachbar-Host derselben Domain | brex, clay, close, datadog, mem, mixpanel, read-ai, semrush | z. B. Anmeldeserver `api.close.com`, Anmeldeseite `app.close.com`. OpenFang erlaubt nur den Host des Ausstellers und seine Unterdomains. | klappt |
| B | happenstance, domotz-preview | dazu weicht der `issuer` ab (`clerk.happenstance.ai`, `auth.domotz.com`) | klappt |
| C: fremde Domain | catalyst-by-zoho | `zohomcp.com` → `zoho.com` | klappt |
| D: keine Selbstregistrierung | box, canva, conductor, docusign, zoom, sharepoint, teams | Der Anbieter bietet keine offene Registrierung an. Man braucht eine eigene App beim Anbieter. | – |
| E: Registrierung abgelehnt | figma (403), quickbooks (403), similarweb (403), moody-s (Rückruf nicht erlaubt), s-p (keine Client-ID) | Der Anbieter lässt fremde Clients nicht zu. | nein |
| F: keine Beschreibung | govtribe, thoughtspot, zoominfo | Keine Schutzbeschreibung lesbar. | – |

Dazu kommen aus dem Abgleich 5 Anbieter mit der Klasse `braucht_registrierung` (alpaca, asana,
coveo, dovetail, hubspot). Sie gehören zu Gruppe D.

### https-Rückruf über tailscale

Die Rückruf-Adresse war `https://desktop-p303ria.tail6c7d61.ts.net/api/integrations/<id>/oauth/callback`.
Auf dem PC läuft dort schon `tailscale serve`.

| Anbieter | Registrierung | Anmeldeseite |
|---|---|---|
| calendly | 201 | 302 zur Calendly-Anmeldung, **geht** |
| signnow | 201 | 400, Rückruf nicht erlaubt |
| omni-analytics | 201 | 401, Rückruf ungültig |
| teamwork-com | 400, Host nicht freigegeben | – |
| moody-s | 400, Rückruf nicht erlaubt | – |

`http://localhost:4200` wurde ebenfalls probiert. SignNow und Omni lehnen es genauso ab.
Teamwork hat `localhost` am 01.08.2026 abgeschaltet.
**Ergebnis: Ein https-Rückruf hilft nur Calendly.**

## Die Stufen

Jede Stufe endet mit einem echten Test. Grün heißt: Anmeldung erfolgreich,
`tools/list` liefert Werkzeuge, ein lesendes Werkzeug antwortet. Nur dann wird die Vorlage
auf `verified = true` gesetzt und verliert die Marke „ungeprüft“.

### Stufe 1: Echttest der 52 bereiten Integrationen (braucht Felix)

- **Felix:** Er meldet sich bei den Diensten an, die er wirklich nutzt: in Rowboat unter
  Integrationen auf „Anmelden“. Ein Konto anzulegen nur zum Testen ist nicht nötig.
- **Claude:** Ein Echttest-Skript prüft je verbundener Integration Folgendes:
  - Zustand `verbunden`
  - Zahl der Werkzeuge
  - Aufruf eines lesenden Werkzeugs über `:4200/mcp`, mit dem Agenten `integrations-hub`
- Danach markiert der Erzeuger die grünen Vorlagen als geprüft und schreibt das Ergebnis in
  den Abgleich.
- Eine Integration, die nach dem Login keine Werkzeuge liefert, wird mit Grund als
  nicht nutzbar markiert (⛔), wie die 7 von heute.

### Stufe 2: Ermittlung normgerecht machen (Gruppe A, 12 Anbieter)

Das ist eine Korrektur nach RFC 8414 und RFC 9728, keine Lockerung der Sicherheit:

1. Die Anmeldeserver-Beschreibung zuerst mit Pfad-Einschub suchen, dann wie bisher.
2. Ein `resource`, das den Ursprung der Server-Adresse nennt, immer gelten lassen. Gleicher
   Host mit anderem Pfad (replayio) gilt nur, wenn die Beschreibung von genau diesem Host kam.
3. Alle anderen Prüfungen bleiben: Endpunkt-Bindung, S256, kein Weiterleiten, URL-Richtlinie.

Test: Rust-Einheitstests je Fall, dann `katalog_pruefen` und `anmeldeweg_pruefen` gegen die 12.
Neue Vorlagen werden erzeugt, danach folgt die Felix-Anmeldung wie in Stufe 1.
Rollout mit OpenFang-Neubau. Davor freien RAM messen, `-j 2` und `RUST_MIN_STACK`.

**Ergebnis Stufe 2 (11.11., OpenFang e8621c4):** Zwei Korrekturen in `discover`:

- Pfad-Einschub für die Anmeldeserver-Beschreibung.
- Eine im 401-Header angekündigte Wurzel-Beschreibung gilt als Wurzel-Variante. Dort
  darf `resource` den Ursprung nennen. Eine Pfad-Beschreibung, die nur den Ursprung
  nennt, bleibt abgelehnt.

Ergebnisse:

- 12 Anbieter sind einrichtbar: airtable, brand24, brighthire, cloudinary, digitalocean,
  fyxer, govtribe, monday-com, otter-ai, razorpay, s-p, stripe.
- Bei allen 12 klappen Registrierung und Anmelde-Link mit dem echten Rückruf.
  Der Testbrowser kam bei 11 bis zur Anmeldeseite des Anbieters. Razorpays
  Dashboard sperrt den Testbrowser (403).
- replayio bleibt draußen. Der Fall bräuchte eine echte Lockerung, und replayio
  antwortet ohnehin ohne Anmeldung.
- vantage lieferte beim Lauf keine lesbare Beschreibung, beim Prüfskript am Vormittag
  schon. Das wird beim nächsten Lauf neu geprüft.
- Airtable meldet Scopes, verlangt sie aber vielleicht beim Login. Das zeigt erst der
  Echttest in Stufe 1. Bei `invalid_scope` bekommt airtable feste `SCOPES` wie lovable.

### Stufe 3: Nachbar-Hosts derselben Domain (Gruppe B, 10 Anbieter) – Felix entscheidet

- Endpunkte und `issuer` dürfen auf einem anderen Host derselben registrierbaren Domain
  liegen, also eTLD+1 nach der Public Suffix List. Beispiel: `app.close.com` neben
  `api.close.com`.
- Risiko: Ein übernommener Nachbar-Host beim Anbieter könnte die Anmeldung umlenken. Das
  Risiko ist klein, denn die Beschreibung selbst kommt weiter nur vom Server des Anbieters.
- Gruppe C (Zoho) bekäme eine ausdrückliche Ausnahme je Vorlage, ohne allgemeine Lockerung.

### Stufe 4: Eigene Apps beim Anbieter (Gruppe D, 12 Anbieter) – nur auf Wunsch

- OpenFang lernt vorregistrierte Clients: Die Vorlage trägt `client_id`, ein Secret liegt
  nur im Tresor (`INTEGRATION_OAUTH_CLIENT_<ID>`).
- **Felix** legt je gewünschtem Anbieter in dessen Entwicklerkonsole eine App an. Den
  Rückruf gibt Claude vor. Zugangsdaten trägt Felix selbst per Einmal-Link ein.
- Lohnt sich nur für Dienste, die Felix nutzt, etwa HubSpot, Zoom, Asana, Box,
  Microsoft 365 oder DocuSign.

### Stufe 5: https-Rückruf (nur Calendly) – nur wenn Felix Calendly nutzt

- `tailscale serve` leitet `/api/integrations/calendly/oauth/callback` an `127.0.0.1:4200`
  weiter.
- Die Vorlage bekommt `[auth] rueckruf = "tailnet"`.
- Das ist eine maschinenweite Änderung, deshalb mit Claim in `002_Koordination_Live.md`.

### Bleibt markiert (keine Stufe)

signnow, omni-analytics, teamwork-com, highlevel, yepcode, scite (bisher ⛔) und
figma, quickbooks, similarweb, moody-s, s-p, govtribe, thoughtspot, zoominfo bleiben
markiert. Die Anbieter lassen OpenFang nicht zu. Das kann sich ändern. Ein neuer Lauf von
`scripts/ermittlung_locker.py` zeigt es.

## Weitere offene Punkte (nicht Integrationen)

- **Telegram-Token des OpenFang-Bots erneuern:** Er stand im Klartext in `err.log`.
  Felix holt bei BotFather einen neuen Token und trägt ihn per Einmal-Link ein. Claude
  stellt den Dienst um und prüft.
- **Kalender Teil B (Google):** Felix legt ein Google-Cloud-Projekt mit OAuth-Client an,
  nur Lesen. Die Anleitung steht in sales-claw `docs/03_RUNBOOK.md`, Abschnitt „Teil B“.

## Reihenfolge

1. Stufe 1 (Skript bauen, sobald Felix die ersten Logins hat)
2. Stufe 2 (sofort möglich, keine Felix-Arbeit außer Login)
3. Stufe 3, Stufe 4, Stufe 5 je nach Felix' Antwort in Rowboat

Die Rowboat-Aufgaben dazu stehen in Felix' Aufgabenliste mit dem Präfix „[Integrationen]“.
