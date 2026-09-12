# sales-claw wird der einzige Versandweg

**Auftrag des Betreibers, 12.09.2026:** „wir geben den sales claw die aufgabe
für sämtliche versendungen".

**Spec-Typ:** Vertrag zwischen zwei Spaces. Wird umgesetzt von
`plans/2026-09-12-versand-nur-ueber-sales-claw.md`.

---

## 1. Warum — die Messung vom 12.09.2026

Vor jeder Zeile Code gemessen, weil zwei Lesarten des Auftrags zu sehr
verschiedener Arbeit geführt hätten („Marketing sendet gar nicht" gegen
„Marketing behält den Sammelversand und reicht die Zustellung durch").

### Was tatsächlich versendet wurde

| Pfad | zugestellt | abgelehnt |
|---|---|---|
| sales-claw (`sales.drafts`) | **25** — 13 whatsapp, 7 email, 5 linkedin | 54 |
| Marketing (`marketing.campaign_sends`) | **0** | — |
| Marketing (`marketing.campaign_sends_openfang`) | **0** | — |
| Marketing (`marketing.campaign_sends_telegram`) | **0** | — |

Marketing hat in seiner ganzen Existenz **keine einzige Nachricht
zugestellt**. sales-claws Freigabe-Tor wird benutzt und lehnt zwei von drei
Entwürfen ab.

### Wie groß die Publika sind

| | |
|---|---|
| `marketing.emails` | 4 Empfänger, davon **1** mit `consent_given_at` |
| `marketing.telegram_recipients` | 1 |
| `marketing.audiences` | 4 Publika, das größte hat **2** Mitglieder |
| `sales.leads` | 529 — davon **2** mit Einwilligung (527 × `unknown`) |
| `marketing.broadcast_proposals` | 30 (18 draft, 7 rejected, 3 pending, 1 approved, 1 „sent" ohne zugehörige Send-Zeile) |

**Folge für den Entwurf:** Die naheliegende Sorge — „ein Rundschreiben an N
Empfänger wird zu N einzeln freizugebenden Entwürfen" — ist gegenstandslos.
N ist 1 bis 4. Ein Sammelversand existiert nirgends, also muss auch keiner
überbrückt werden. Der Auftrag wird wörtlich umgesetzt: **pro Kontakt, über
sales-claw.**

### Was NICHT das Argument ist

Marketings Versender ist nicht torlos. `tools/_send_paranoid.py` trägt 13
Gates (Kill-Switch, FREEZE-Datei, DKIM/SPF/DMARC-Ausrichtung, Domain-
Allowlist mit Unicode-Lookalike-Abwehr, Investor-Lockout, Confirm-Token,
Postfix-Loopback-Probe, Mailq-Audit) und seit F1 (03.09.2026) **dieselbe**
gemeinsame Sperrliste wie sales-claw. `tools/_send_telegram.py` spiegelt das
für `chat_id` samt Opt-in-Prüfung gegen `marketing.telegram_recipients`.

Das Argument ist die **Doppelung**, nicht die Qualität: zwei Versandwege
heißen zwei Orte, an denen Einwilligung, Sperrliste, Widerruf und Audit
synchron bleiben müssen. F1 musste die gemeinsame Sperrliste bereits an
beide anschrauben. Und der Weg mit den meisten Toren hat nie etwas
zugestellt.

### Die Lücke, die dabei entstand — und noch am selben Tag geschlossen wurde

**sales-claw konnte kein Telegram.** Es hatte drei Dispatcher —
`dispatch.py` (WhatsApp über OpenWA), `mail_dispatch.py` (SMTP),
`linkedin_dispatch.py`. Marketings Telegram-Weg ist in
`marketing.channel_config` `enabled` und `send_implemented`, hat aber 0
Sendungen bei 1 Empfänger; dort warten 11+ Entwürfe im Entwürfe-Tab.

Diese Spec schloss Telegram zunächst **nicht** an, sondern stellte den Kanal
still und benannte ihn als offene Arbeit — statt so zu tun, als sei er
abgedeckt. Auf Entscheid des Betreibers wurde er am selben Tag nachgebaut
(`telegram_dispatch.py`, Dienst `sales-telegram`); **Abschnitt 6.1 trägt,
was dabei herauskam und was dort noch nicht stand.** Dieser Absatz bleibt
stehen, weil er den Stand beschreibt, aus dem die Entscheidung erwuchs.

---

## 2. Der Vertrag

Marketing **schreibt**, sales-claw **versendet**. Dazwischen liegt eine
Auftragswarteschlange in der Datenbank — nach demselben Muster wie die
Lead-Brücke F2/F3 (Migrationen 041/042), die zwischen denselben zwei Spaces
bereits in beide Richtungen läuft.

```
  marketing-claw                          sales-claw
  (schreibt Text + Unterlage)             (kennt Kontakte, Tore, Transport)
        |                                        ^
        | versandauftrag_anlegen(...)            | versandauftraege_offen(limit)
        v                                        |
   marketing.versandauftraege  ------------------+
        ^                                        |
        | versandauftrag_erledigen(id, ...)      v
        +------------------------------  entwurf_erstellen(lead_id, ...)
                                                 |
                                                 v
                                          sales.drafts (pending)
                                                 |
                                        Betreiber gibt frei
                                                 |
                                                 v
            dispatch / mail_dispatch / linkedin_dispatch / telegram_dispatch
```

**Warum eine Tabelle und kein direkter Aufruf:** marketing-claw ist ein
eigener Container mit eigenem MCP-Sidecar (`:8130`); sales-claws Werkzeuge
liegen in dessen Container. Es gibt keinen Netzweg zwischen beiden, und es
soll auch keiner entstehen — die Datenbank ist der Ort, an dem beide Spaces
sich ohnehin schon treffen, und eine Zeile dort ist prüfbar, wiederholbar
und überlebt den Neustart beider Seiten.

**Warum sales-claw nicht direkt in `sales.drafts` geschrieben bekommt:**
weil Marketings Auftrag dann an allen Toren vorbeiliefe, die in
`entwurf_erstellen` hängen (gemeinsame Sperrliste, Löschantrag, Privat-Flag,
UWG-Erstansprache, WhatsApp-Kontakt-Freigabe, Anhangsprüfung). Der Auftrag
ist eine **Bitte**, kein Befehl; sales-claw entscheidet.

### 2.1 Was ein Auftrag trägt

| Feld | Bedeutung |
|---|---|
| `kanal` | `whatsapp` \| `email` \| `telegram` \| `linkedin` \| `linkedin_post` — was sales-claw zustellen kann (`telegram` und `linkedin_post` kamen beim Bauen dazu, s. 6.1) |
| `empfaenger` | E-Mail-Adresse, Telefonnummer oder — bei `telegram` — die **chat_id**. Marketing kennt keine `lead_id`. Eine chat_id ist KEINE Telefonnummer: als solche gelesen würde `1092040975` zu `tel:+1092040975`, einem fremden Anschluss. Eigene Kennungsform `tg:<ziffern>`. |
| `betreff` | nur bei E-Mail sinnvoll, sonst leer |
| `text` | der fertige Text |
| `medien_datei` | **bloßer Dateiname** aus `/media-erzeugt`, ohne jede Pfadangabe (sales-claws `medien.pruefe` weist Pfadanteile ab) |
| `kampagne` | freier Name, landet in der Notiz am Kontakt |
| `quelle` | Herkunft, z. B. `broadcast_proposal:<uuid>` — hält den Auftrag rückverfolgbar |

### 2.2 Was beim Anlegen geprüft wird (in der DB-Funktion, nicht im Agenten)

Nach dem Vorbild des Triggers in 042, der die Sperrliste vor dem INSERT
prüft:

1. **Kanal** muss einer der zulässigen sein (s. 2.1).
2. **Text** darf nicht leer sein.
3. **Gemeinsame Sperrliste** (`compliance.ist_gesperrt`) für
   `email:<adresse>` bzw. `tel:+<nummer>` — gesperrt heißt: kein Auftrag,
   mit Grund. Bei `telegram` geht das nicht: eine `tg:`-Kennung passt nicht
   in `compliance.sperrliste` (deren CHECK kennt nur `email:` und `tel:`).
   Die Sperre greift dort über den **Kontakt** — sales-claw löst die
   chat_id einem Lead zu, und dessen E-Mail und Nummer prüfen dieselben
   Tore wie bei jedem anderen Kanal.
4. **Widerruf im Marketing** (`marketing.emails.unsubscribed_at`) — dito.
5. **Idempotenz:** derselbe `(kanal, empfaenger, text)`-Auftrag entsteht
   innerhalb von 24 h nur einmal. Ein Agent, der zweimal dasselbe möchte,
   erzeugt keine zwei Nachrichten.

Was hier **nicht** geprüft wird, weil es sales-claws Sache ist: Einwilligung
nach UWG, Löschantrag, Privat-Flag, Kontakt-Freigabe für WhatsApp,
Zulässigkeit des Anhangs. Diese Tore stehen in `entwurf_erstellen` und
bleiben dort — ein zweiter Ort dafür wäre genau der Fehler, den diese Spec
abschafft.

### 2.3 Wie sales-claw einen Auftrag übernimmt

`versandauftrag_uebernehmen(auftrag_id)`:

1. Auftrag über `marketing.versandauftraege_offen` holen.
2. `empfaenger` gegen `sales.leads` auflösen — E-Mail bzw. Telefon,
   normalisiert wie an allen anderen Stellen im Haus.
3. **Kein Kontakt gefunden** → Auftrag `abgelehnt`, Grund „kein Kontakt in
   sales-claw". sales-claw legt **keinen** Lead an: ein frisch angelegter
   Kontakt hätte `consent_status='unknown'` und fiele sofort am UWG-Tor
   durch; der Betreiber soll das entscheiden, nicht die Maschine.
4. **Kontakt gefunden** → `entwurf_erstellen(lead_id, kanal, text, betreff,
   medien_datei)` — mit allen Toren, unverändert.
5. Gibt `entwurf_erstellen` einen Fehler zurück → Auftrag `abgelehnt` mit
   genau diesem Fehlertext. Der Grund steht damit in `marketing.*` und ist
   für Marketing sichtbar, ohne dass Marketing `sales.*` lesen muss.
6. Sonst → Auftrag `angenommen`, `draft_id` eingetragen. Der Entwurf ist
   `pending` und wartet auf die Freigabe des Betreibers wie jeder andere.

**Der Auftrag löst nie einen Versand aus.** Er erzeugt höchstens einen
Entwurf. Die Freigabe bleibt beim Menschen, der Versand bei den drei
Dispatchern.

### 2.4 Rechte

Wie bei 041/042: `sales_app` bekommt `EXECUTE` auf die zwei
SECURITY-DEFINER-Funktionen und sonst nichts in `marketing.*`. Marketing
schreibt als `supabase_admin` (so läuft `sync/_db.py` heute schon) — eine
eigene `marketing_app`-Rolle existiert nicht und wird hier auch keine
erfunden.

---

## 3. Marketings eigene Versender werden stillgelegt

Nicht gelöscht — **gesperrt**, mit sichtbarem Grund. Löschen würde ein Jahr
sorgfältige Arbeit an DKIM-Ausrichtung, Unicode-Lookalikes und Mailq-Audit
wegwerfen, die beim Telegram-Anschluss (Abschnitt 6) noch gebraucht wird.

Der Riegel sitzt **so früh wie möglich**, in `main()` jedes Versand-Werkzeugs,
vor jeder Konfiguration und jedem Netzkontakt.

Wer ihn doch braucht, setzt `MARKETING_VERSAND_TROTZDEM=1` — dann läuft alles
wie bisher, aber der Riegel hat den Vorgang protokolliert. Ein Schalter, der
sich nicht umlegen lässt, wird umgangen statt benutzt; einer, der beim
Umlegen laut wird, wird gelesen.

Betroffen: `tools/_send_paranoid.py`, `tools/_send_telegram.py`,
`tools/_send_openfang.py`, `workers/batch_sender.py`, `workers/send_worker.py`.

---

## 4. Was der Marketing-Agent davon merkt

`claw/werkzeuge.py` hat seit dem 12.09. bereits `_kanal_kann_senden`, das
`whatsapp` und `linkedin` abweist und auf sales-claw verweist. Diese Regel
verallgemeinert sich:

* **Neues Werkzeug `versand_beauftragen(kanal, empfaenger, text, …)`** legt
  einen Auftrag an und gibt zurück, was die DB-Prüfung gesagt hat.
* `kampagne_entwerfen` bleibt, was es ist: das **redaktionelle** Artefakt für
  die Freigabe-Oberfläche. Sein Hinweistext sagt künftig klar, dass ein
  Vorschlag nichts versendet und `versand_beauftragen` der Weg nach draußen
  ist.
* Die Fertigkeiten `email-kampagne` und `whatsapp-nachricht` ziehen nach.

---

## 5. Abnahme

| # | Behauptung | Beleg |
|---|---|---|
| A1 | Migration 043 läuft auf der maßgeblichen DB durch | `verify_043.sql` grün gegen `offload-vm` |
| A2 | Ein Auftrag für einen gesperrten Empfänger entsteht gar nicht | Funktion liefert `ok:false` mit Grund; Zeilenzahl unverändert |
| A3 | Derselbe Auftrag zweimal → ein Auftrag | zweiter Aufruf liefert dieselbe `id` |
| A4 | `sales_app` darf die zwei Funktionen rufen und sonst nichts in `marketing.*` | `set role sales_app` + Gegenprobe auf `select * from marketing.versandauftraege` |
| A5 | Auftrag ohne passenden Kontakt wird abgelehnt, mit Grund | danach `status='abgelehnt'`, `grund` nicht leer |
| A6 | Auftrag mit Kontakt erzeugt einen `pending`-Entwurf, sonst nichts | `sales.drafts` +1 mit `status='pending'`; `campaign_sends*` unverändert 0 |
| A7 | Ein Auftrag, den ein Tor abweist, trägt den Torfehler als Grund | WhatsApp ohne Kontakt-Freigabe → Grund nennt die Freigabe |
| A8 | Marketings Versender laufen nicht mehr an | Aufruf endet mit Exit≠0 und nennt sales-claw |
| A9 | Mit `MARKETING_VERSAND_TROTZDEM=1` laufen sie wie zuvor | derselbe Aufruf kommt bis Gate 1 |
| A10 | Die volle Marketing-Suite bleibt grün | pytest EXIT 0, Zahl ≥ Ausgangswert |
| A11 | Die volle sales-claw-Suite bleibt grün | pytest EXIT 0, Zahl ≥ 1565 |

**Kein `tail` in der Pytest-Pipe.** Das hat in dieser Arbeitslinie schon
zweimal einen roten Lauf als grün ausgegeben (`fa74444d`); der Exit-Code wird
direkt nach pytest gelesen.

---

## 6. Ausdrücklich offen

1. ~~**Telegram hat keinen Dispatcher in sales-claw.**~~ **ERLEDIGT am
   selben Tag** (Betreiber-Entscheid „Dispatcher bauen"). Gebaut wurde genau
   das, was hier stand: `telegram` in `entwurf_erstellen`s Kanalliste, ein
   `telegram_dispatch.py` nach dem Muster von `mail_dispatch.py`, und der
   Dienst `sales-telegram` im Compose.

   **Was beim Bauen dazukam und hier nicht stand:**

   * **Eine chat_id ist keine Telefonnummer.** `sperrliste.kennung_tel`
     hätte aus `1092040975` die Rufnummer `tel:+1092040975` gemacht — einen
     fremden Anschluss. Eigenes Modul `telegram_chat.py`, eigene Form
     `tg:<ziffern>`, und nur positive IDs: negative sind bei Telegram
     Gruppen, und eine Nachricht an eine Gruppe statt an einen Menschen ist
     der teuerste Irrtum dieses Kanals.
   * **`drafts.channel` trug einen CHECK ohne `telegram`.** Jeder Entwurf
     wäre erst beim INSERT gescheitert, also lange nachdem der Agent den
     Text geschrieben hat. `db/provision.sql` zieht ihn jetzt idempotent
     nach; beide Datenbanken eingespielt.
   * **Erreichbarkeit ist nicht Einwilligung.** Die Opt-in-Überlegung aus
     `tools/_send_telegram.py` ist mitgenommen — aber als das, was sie ist:
     dass ein Bot keinen Chat eröffnen kann, macht eine Person
     *erreichbar*. Das UWG-Tor bleibt davon unberührt, und `telegram` steht
     ausdrücklich in dessen Kanalliste.
   * **Keine Anhänge.** Reiner Text, ein `media_ref` wird ausdrücklich
     fehlgeschlagen gebucht — dieselbe Regel und derselbe Grund wie bei
     `mail_dispatch`.

   **Was NICHT der Grund war, es zu bauen:** die 11 wartenden Entwürfe. Sie
   sind (gemessen 12.09.2026) *elfmal dieselbe Nachricht* — „VibeMind Early
   Access / Warteliste für Solo-Gründer", alle vom 02.–04.09., alle von
   `curator:marketing-claw`. Das ist das Wiederholungsmuster, das schon am
   04.09. gemessen wurde: ohne Zugriff auf die eigene Historie schreibt der
   Agent dieselbe Aufzählung wieder und wieder. Sie gehören aufgeräumt,
   nicht zugestellt. Und das Telegram-Publikum besteht aus **einer** Person
   (`marketing.telegram_recipients`: 1 Zeile, der Betreiber selbst) — der
   Dispatcher ist Vorbau für einen Kanal, den es gibt, den aber nie etwas
   benutzt hat.
2. **Erfolgsmetriken zurück an Marketing** — der Betreiber hat das selbst auf
   „später" gelegt. Die Auftragstabelle trägt mit `draft_id` bereits den
   Faden, an dem das später hängen kann.
3. **Kein Sammelversand.** Bei N=1..4 unnötig. Wächst ein Publikum über ~20,
   ist ein `versandauftrag_fuer_publikum(...)` die naheliegende Erweiterung —
   dann aber mit einer Freigabe für den ganzen Schwung, nicht mit N einzelnen.
