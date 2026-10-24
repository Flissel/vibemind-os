# Rowboat als Geschäftsdaten-Zentrale – Entwurf

Stand 24.10.2026 · Betreiber: Felix · Status: Entwurf zur Freigabe

## 1. Absicht

Der Betreiber will mit seinen eigenen Daten chatten – vom Handy, über Telegram, auch wenn sein PC aus ist.
Rowboat soll dafür die Zentrale sein: es kennt die Geschäftsdaten (Kontakte, Newsletter, Wochenlage je Firma),
führt die eigene Todo-Liste, speichert Notizen und erledigt Aufgaben mit seinen Werkzeugen.

Typische Nachrichten (wörtlich vom Betreiber):

- „Hey, hast du noch das Wissen zu X?“ · „Kann ich das Profil von X erhalten?“
- „Was ist heute noch auf der Todo?“ · „Kannst du die Notiz für mich speichern?“
- „Kannst du die GitHub-Projekte updaten?“ · „Kannst du dir die neuesten Docs holen?“
- „Kannst du mir ein Dashboard für meine Daten erstellen?“ · „Kannst du auf meinem PC schnell nach X suchen?“

Umfang laut Betreiber: **alles, was in Rowboat geht**, ohne Vorrang zwischen Wissen, Alltag und Aktionen.

## 2. Ziele

1. **Rowboat läuft rund um die Uhr auf dem Mini-PC (VM `offload-vm`)** als Rowboat-Server ohne Oberfläche
   (upstream `apps/x/apps/server`, `standalone.ts`). Wissen, Todo-Liste (`todo.md`), Notizen und Chats liegen dort.
2. **Telegram-Kanal**: Rowboats eingebauter Telegram-Transport (`packages/core/src/channels/transports/telegram.ts`)
   hängt am Server auf dem Mini-PC. Eigener Bot (neu bei @BotFather), nur freigegebene Chat-IDs (`allowFrom`).
   Rückfragen des Agenten (ask_human) kommen und gehen über Telegram.
3. **Desktop-App am PC = Ansicht**: Fern-Modus der Desktop-App (`server-host.ts`, Modus `remote`) verbindet sich
   mit dem Server auf dem Mini-PC. Die Multiverse-Einbettung (`--embed`, Roarboot) bleibt.
4. **Geschäftsdaten in Rowboat** (Notizen je Firma, nach Kategorien), nachts aktualisiert, Wochenbild montags früh:
   - **Kontakte**: ein Steckbrief für **jeden** Kontakt (heute 569), abgelegt nach Kategorie (Einordnung in sales-claw).
   - **Newsletter-Archiv**: jeder versendete Newsletter als Notiz.
   - **Wochenbild**: Pipeline-Zahlen, Newsletter-Ergebnisse, gebaute Reels/Podcasts der Woche.
5. **Bedeutungssuche** über das Rowboat-Wissen (Betreiber: „semantisch suchbar“). Embeddings rechnet der **PC**
   (Ollama); Index liegt auf dem Mini-PC; ist der PC aus, sucht Rowboat per Stichwort weiter.
6. **Werkzeuge** für die Aktionen aus Abschnitt 1 sind vorhanden und vom Mini-PC aus nutzbar; Dinge, die nur am PC
   gehen (auf dem PC suchen), laufen über den verbundenen PC und melden sonst sauber „PC ist aus“.

## 3. Nicht-Ziele

- Kein Schreiben aus Rowboat in Supabase/sales-claw. Rowboat liest Geschäftsdaten nur; Pflege bleibt in sales-claw.
- Keine Echtzeit-Spiegelung (Minuten). Nächtlich reicht (Betreiber-Entscheid).
- Composio und Attio werden nicht eingerichtet (Betreiber: „später“ bzw. „nur Code übernehmen“).
- Kein eigener Telegram-Bot-Code: der eingebaute Rowboat-Kanal wird genutzt.

## 4. Anti-Ziele (wäre ein Fehlschlag, auch wenn es technisch läuft)

- Ein Modell läuft auf dem Mini-PC. (Feste Regel: auf dem Mini-PC läuft kein Modell. Die Claude-Kommandozeile ist
  kein Modell – Betreiber hat sie dort freigegeben.)
- Sensible Inhalte landen unverschlüsselt in Rowboat: **keine Mail-Inhalte, keine Sozialdaten**, keine Passwörter oder
  Schlüssel in Steckbriefen (Rowboat-Wissen ist unverschlüsselt; siehe Memory „Sensible Daten NICHT in Rowboat-KB“).
- Zwei Rowboat-Kerne laufen gleichzeitig auf demselben Wissen (upstream warnt: doppelte Planer, gespaltener
  Sitzungsindex). Nach dem Umzug läuft der Kern **nur** auf dem Mini-PC.
- Fremde Chats können Rowboat steuern (allowFrom leer oder zu weit).
- Die Telegram-Antwort hängt still, wenn der PC aus ist (statt „PC ist aus, Stichwortsuche“).
- Wissen geht beim Umzug verloren oder liegt danach doppelt.

## 5. Randbedingungen

- **Modellzugang**: Rowboat denkt mit dem Claude-Abo, kein API-Budget. Heute über die Shim `:8121`
  (`SHIM_TOOL_CALLS=1`) auf dem PC. Für den Betrieb ohne PC läuft eine Shim-Instanz **auf dem Mini-PC** mit der
  Claude-Kommandozeile (Abo-Anmeldung dort). Budget-Wächter gilt auch dort (eigener Agent-Eintrag, Tagesgrenze).
- **Mini-PC**: 23 GB RAM (8 GB verfügbar gemessen 24.10.), 12 Kerne, 52 GB Platte frei. Supabase, sales-claw und
  Marketing-API laufen dort schon (Datenquelle in Reichweite).
- **Netz**: PC ↔ Mini-PC über Tailscale. Ollama am PC ist heute nur bei Bedarf an; für Embeddings muss es per Tailnet
  erreichbar sein, wenn der PC an ist.
- **Upstream-Reife**: der eigenständige Server ist upstream ausdrücklich noch „für Tests und Entwicklung“
  (`standalone.ts`). Wir sind früh dran; Fork `Flissel/rowboat` Branch `vibemind` (Stand 13e01004).
- **Bestehendes, das weiterlaufen muss**: Brain-Kurator schreibt heute nach `~/.rowboat/knowledge` auf dem PC
  (Projekte, Agenten, Themen …); Marketing liest/schreibt `companys/<Firma>/` (Markenwissen, Agent-Notizen);
  MCP `rowboat-ui` (Claude Code, Brain) spricht die Einbettung auf dem PC an.
- **Sales-UI/Repo-Regeln** gelten unverändert; Deploys, Migrationen, Secrets nur mit Betreiber-OK.

## 6. Vom Betreiber entschieden

| Frage | Entscheid |
|---|---|
| Umfang | Alles, was in Rowboat geht (Wissen, Alltag, Aktionen) |
| Todo | Rowboats eigene Todo-Liste |
| Handy-Weg | Telegram (remote) |
| Ohne PC erreichbar | wichtig → Server auf dem Mini-PC |
| Claude-Kommandozeile auf dem Mini-PC | ok |
| Steckbriefe | alle Kontakte, nach Kategorien, semantisch suchbar |
| Embeddings | auf dem PC |
| Aktualität | nächtlich reicht |
| Rückfragen vor Aktionen | offen gelassen; für den Start „ok“ zu: Telegram ohne Werkzeuge mit Außenwirkung (siehe 7) |

## 7. Vom Entwurf festgelegt (meine Calls, änderbar)

- **Telegram-Aktionen (Betreiber „ok“ zu Weg b)**: Telegram-Läufe bekommen **keine Werkzeuge mit Außenwirkung**
  (kein GitHub-Push, kein Senden, kein Löschen). Grund: Rowboats Werkzeug-Freigabe lässt sich nur am Desktop
  beantworten (`channels/bridge.ts` meldet „open Rowboat on your desktop“), ein Telegram-Lauf hinge bei PC aus;
  zudem ist `autoPermission` für Agenten-Chats bewusst an. Lesen, Suchen, Notizen speichern, Todos pflegen gehen
  ohne Rückfrage. Freigaben per Telegram (eigener Code im Fork) sind ein späterer Schritt.
  Folge für Abschnitt 1: „GitHub-Projekte updaten“ per Telegram heißt vorerst lesen/zusammenfassen bzw. als Todo
  vormerken; ausführen am Desktop.
- **Private Kontakte (vorsichtige Vorgabe, Betreiber hat nicht entschieden)**: in sales-claw als privat markierte
  Kontakte (`_privat`, fail-closed) und Sammelkontakte (Unbekannte Eingänge, LinkedIn-Profil) bekommen **keinen**
  Steckbrief. „Jeder Kontakt“ = alle übrigen.
- **Mails (vorsichtige Vorgabe, Betreiber hat nicht entschieden)**: Rowboats eigener Gmail-/Kalender-Abgleich
  (`~/.rowboat/gmail_sync`, `calendar_sync`, heute am PC) zieht **nicht** mit auf den Mini-PC und wird dort nicht
  gestartet. Die Ordner bleiben am PC unangetastet (kein Löschen).
- **Läden → Marke**: jedes sales-claw-Schema geht zur Marke seines Ladens (`sales` → `companys/VibeMind`,
  Ivans Laden → `companys/fin2gether`, künftige Läden über ihre Laden-Marke); `sales_test` nie.
- **Kategorie-Ordner**: erster Bereich aus der Einordnung (`enrichment.bereiche`), sonst `_ohne`; weitere Bereiche und
  die Art (Person/Firma) als Schlagwörter in der Notiz; keine Kopien.
- **Budget**: der Nachtlauf schreibt nur geänderte Dateien. Rowboats Hintergrund-Agenten (Graph, Tagging,
  Agent-Notizen, Live-Notes) lassen die erzeugten Ordner (`Kontakte/`, `Newsletter/`, `Wochenbild/`) aus.
  Eigener Budget-Eintrag für den Rowboat-Server auf dem Mini-PC mit Tagesgrenze.
- **Bestehende `knowledge/People/`-Notizen** bleiben; Steckbriefe verlinken darauf, wenn Name/Mail passt.
- **Anmeldungen**: OAuth-Token (GitHub, ChatGPT …) werden auf dem Server anders verschlüsselt (eigene `cipher-key`)
  und ziehen nicht mit; einmalige Neu-Anmeldung über die verbundene Desktop-App ist Teil des Umzugs.
- **Ablage der Geschäftsnotizen**: unter `knowledge/companys/<Firma>/` (dort liegt schon Markenwissen):
  `Kontakte/<Kategorie>/<Name>.md`, `Newsletter/<JJJJ-MM-TT> <Betreff>.md`, `Wochenbild/<JJJJ>-KW<nn>.md`.
  Notizen tragen eine Kopfzeile „automatisch erzeugt, Quelle sales-claw, Stand …“; Rowboat-Agent ändert sie nicht
  (nächtlicher Lauf überschreibt). Eigene Anmerkungen des Betreibers gehören in getrennte Notizen.
- **Abgleich**: ein nächtlicher Lauf auf dem Mini-PC liest Supabase (nur lesend) und schreibt Notizen; gelöschte bzw.
  archivierte Kontakte wandern nach `Kontakte/_archiv/`, nichts wird hart gelöscht.
- **Reihenfolge**: (A) Server + Telegram auf dem Mini-PC mit Umzug des Wissens, (B) Geschäftsdaten-Abgleich,
  (C) Bedeutungssuche, (D) Werkzeuge prüfen/ergänzen. A zuerst, weil alles andere dort hinschreibt.

## 8. Dem Bauenden überlassen

- Wie der Server auf dem Mini-PC betrieben wird (Container vs. systemd), Start nach Neustart, Wächter, Logs.
- Wie die Shim auf dem Mini-PC läuft (Port, nur Tailnet/loopback, Budget-Hülle).
- Index-Technik für die Bedeutungssuche (z. B. SQLite-Vektor oder Qdrant auf dem Mini-PC), Embedding-Modell am PC
  (heute vorhanden: `qwen3-embedding:0.6b` in Ollama), und wie Rowboat die Suche nutzt (MCP-Werkzeug am Server).
- Wie der Umzug von `~/.rowboat` abläuft (Kopie, Prüfsumme, Umschalten, Rückweg), und wie Brain-Kurator, Marketing
  und `rowboat-ui` danach auf das Wissen am Mini-PC zugreifen.
- Welche Werkzeuge für GitHub, Docs, Dashboard, PC-Suche nötig sind. Heute in Rowboat: MCP `exa` (Websuche),
  `desktop-commander`, `playwright`, `filesystem` – diese laufen dort, wo der Kern läuft; PC-Zugriffe brauchen einen
  Weg zum PC (z. B. Werkzeug am PC über Tailnet oder Rowboats Fähigkeits-Weiterleitung an den verbundenen Client).
- Format/Inhalt der Steckbriefe im Detail (Felder aus sales-claw: Name, Firma, Rolle, Einordnung, Pipeline-Stufe,
  letzter Kontakt, offene Termine, Newsletter-Einwilligung, Einlader/Struktur) – ohne Mail-Inhalte.

## 9. Erfolg

- Vom Handy: „Was ist heute noch auf der Todo?“ und „Gib mir das Profil von <Kunde>“ werden bei **ausgeschaltetem PC**
  richtig beantwortet; „speicher die Notiz …“ legt eine Notiz an, die danach am PC in der App sichtbar ist.
- „Kunden, die Interesse an Altersvorsorge hatten“ findet passende Steckbriefe auch ohne genau diese Wörter (PC an);
  bei PC aus kommt eine Stichwort-Antwort mit Hinweis.
- Fremder Telegram-Chat bekommt keine Antwort.
- Kein Modell läuft auf dem Mini-PC; Budget-Wächter bucht die Telegram-Läufe.
