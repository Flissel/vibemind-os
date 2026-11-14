import { app, session, type BrowserWindow, type Session, type WebContents } from "electron";
import {
  KONZEPT_PARTITION,
  WORKSPACE_HTML_CSP,
  guestAttachAllowed,
  guestProtocolAllowed,
  guestRequestDecision,
  guestTripwireDecision,
  guestWebPreferences,
} from "@x/shared/dist/konzept.js";

/**
 * Gastbereich der Konzept-Flaeche: Wissens-HTML laeuft in einem `<webview>` der nicht dauerhaften Partition
 * `konzept`. Nur diese Sitzung bekommt den toten Proxy, die Sperren und ein eigenes `app://`, das nur Wissens-HTML
 * mit der Kein-Netz-Regel ausliefert. Alles Entscheidende steht als reine Funktion in @x/shared/konzept; hier nur das
 * Verdrahten mit Electron. Die Standard-Sitzung bleibt unveraendert.
 *
 * Fail closed: Solange der tote Proxy nicht bestaetigt ist (setProxy erledigt UND resolveProxy zeigt ihn), wird kein
 * Gast angehaengt und jede Anfrage der Sitzung abgebrochen.
 */

/** Alle Schemata auf einen toten lokalen Port; `<-loopback>` noetig, sonst umgehen 127.0.0.1/localhost den Proxy. */
const TOTER_PROXY = { mode: "fixed_servers", proxyRules: "socks5://127.0.0.1:9", proxyBypassRules: "<-loopback>" } as const;
const PROXY_PROBEN = ["https://example.com/", "http://127.0.0.1:8765/", "ws://127.0.0.1:8765/", "wss://example.com/", "http://localhost:8765/"];
/**
 * So lange wartet der Start (nur vor dem ersten Fenster) hoechstens auf die Bestaetigung; sie kommt sonst in
 * Millisekunden. Danach entsteht das Fenster ohne Gastbereich (Kennzeichen aus, kein webviewTag).
 */
const PROXY_WARTEZEIT_MS = 2_000;

/** Tag-Attribute, die der Hauptprozess nie vom `<webview>` uebernimmt (Namen laut webview-tag.md, Electron 39). */
const VERBOTENE_PARAMETER = [
  "allowpopups", "webpreferences", "nodeintegration", "nodeintegrationinsubframes", "plugins", "preload",
  "disablewebsecurity", "blinkfeatures", "disableblinkfeatures", "httpreferrer", "useragent",
];

type Vorbereitung = { serve: (request: Request) => Promise<Response>; guestPreloadPath: string };

let vorbereitung: Promise<boolean> | null = null;
let eingerichtet = false;
let proxyBestaetigt = false;
let gastSitzung: Session | null = null;
let gastPreload = "";
const gastHosts = new WeakSet<WebContents>();
/** Angehaengte Adresse je Gast (webContents.id), uebergeben von will-attach-webview an web-contents-created. */
const gastAdressen = new Map<number, string>();
/** Beendete Gaeste (webContents.id) bis zu ihrem `destroyed`: kein Laden, keine Navigation mehr. */
const beendet = new Set<number>();
/** Adresse des gerade angehaengten Gasts: will-attach-webview und das Erzeugen des Gasts laufen synchron hintereinander. */
let wartendeAdresse: string | null = null;

function verweigern(): Response {
  return new Response("Forbidden", { status: 403 });
}

/**
 * Gast endgueltig beenden: als tot markieren (der Anfrage-Filter bricht ab jetzt alles ab, auch data:/blob:/about:, der
 * Stolperdraht haelt jede weitere Navigation an) und seine Adresse vergessen. Synchron geschieht NUR das, nie ein Aufruf
 * am Gast: `stop()` (oder jeder andere Aufruf) mitten in `did-start-navigation` stuerzt die ganze App ab
 * (Zugriffsverletzung). Erst im naechsten Durchlauf (`setImmediate`): `stop()`, dann `close()`. `close()` und kein
 * Renderer-Absturz, weil Chromium eine vom Browser gestartete, noch wartende Navigation (`loadURL`, `src`) nach dem
 * Absturz in einen frischen Prozess festschreibt; nur das Zerstoeren der webContents beendet sie. Das Element meldet
 * dann `close`/`destroyed`; faellt `close()` aus, bleibt der Absturz des Renderers als Notnagel.
 */
function gastBeenden(guest: WebContents, grund: string): void {
  const id = guest.id;
  if (!beendet.has(id)) console.error(`[konzept] Gast beendet: ${grund}`);
  beendet.add(id);
  gastAdressen.delete(id);
  setImmediate(() => {
    if (guest.isDestroyed()) return;
    try {
      guest.stop();
    } catch {
      // kein Renderer / schon zerstoert
    }
    try {
      guest.close();
    } catch {
      try {
        guest.forcefullyCrashRenderer();
      } catch {
        // der Anfrage-Filter haelt alles ab
      }
    }
  });
}

async function proxyBestaetigen(ses: Session): Promise<void> {
  await ses.setProxy(TOTER_PROXY);
  for (const url of PROXY_PROBEN) {
    const r = await ses.resolveProxy(url);
    if (r.toUpperCase().includes("DIRECT") || !r.includes("127.0.0.1:9")) {
      throw new Error(`konzept: Proxy greift nicht fuer ${url}: ${r}`);
    }
  }
  proxyBestaetigt = true;
}

/** `app://` der Gast-Sitzung: nur Wissens-Adressen, nur mit bestaetigtem Proxy, nur mit der Kein-Netz-Regel. */
function gastProtokoll(serve: Vorbereitung["serve"]) {
  return async (request: Request): Promise<Response> => {
    if (!proxyBestaetigt || !guestProtocolAllowed(request.url)) return verweigern();
    let res: Response;
    try {
      res = await serve(request);
    } catch {
      return verweigern();
    }
    if (res.headers.get("content-security-policy") === WORKSPACE_HTML_CSP) return res;
    // Kein Wissens-HTML (anderer Typ, Fehlerseite): im Gast nie ausfuehren.
    try {
      await res.body?.cancel();
    } catch {
      // Rumpf schon geschlossen
    }
    return verweigern();
  };
}

function sitzungEinrichten(ses: Session, serve: Vorbereitung["serve"]): void {
  ses.setPermissionCheckHandler(() => false);
  ses.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
  ses.setDevicePermissionHandler(() => false);
  ses.setDisplayMediaRequestHandler((_request, callback) => callback({}));
  ses.setBluetoothPairingHandler((_details, callback) => callback({ confirmed: false }));
  ses.on("select-hid-device", (event, _details, callback) => {
    event.preventDefault();
    callback();
  });
  ses.on("select-serial-port", (event, _ports, _wc, callback) => {
    event.preventDefault();
    callback("");
  });
  ses.on("select-usb-device", (event, _details, callback) => {
    event.preventDefault();
    callback?.();
  });
  ses.on("will-download", (event) => event.preventDefault());
  ses.setSpellCheckerEnabled(false);
  ses.protocol.handle("app", gastProtokoll(serve));
  // file: braucht die Sitzung nie. `file` ist fuer protocol.handle ein eingebautes Schema (http/https/file werden
  // abgefangen, interceptProtocol), also je Sitzung ueberschreibbar. chrome:/devtools: lassen sich so nicht abfangen;
  // dort sind onBeforeRequest und der Stolperdraht (did-start-navigation) die Sperre.
  ses.protocol.handle("file", () => verweigern());
  ses.webRequest.onBeforeRequest((details, callback) => {
    let erlaubt = false;
    try {
      const id = details.webContents?.id;
      erlaubt = guestRequestDecision({
        dead: id !== undefined && beendet.has(id),
        proxyConfirmed: proxyBestaetigt,
        hasWebContents: id !== undefined,
        url: details.url,
        documentUrl: id === undefined ? null : gastAdressen.get(id) ?? null,
      });
    } catch {
      erlaubt = false;
    }
    callback(erlaubt ? {} : { cancel: true });
  });
}

/**
 * Haertung jedes Konzept-Gasts, gesetzt beim Erzeugen (vor dem Anhaengen und vor jedem Laden): WebRTC nur ueber den
 * Proxy, keine Fenster, keine von der Seite ausgeloeste Navigation, Stolperdraht fuer vom Browser gestartete
 * Navigationen (loadURL, src-Wechsel, Verlauf), keine Geraetewahl. Faellt etwas aus, wird der Gast beendet.
 */
function gastAbsichern(guest: WebContents, adresse: string | null): void {
  const id = guest.id;
  if (adresse !== null) gastAdressen.set(id, adresse);
  // Aufraeumen erst beim Zerstoeren: so lange bleibt ein beendeter Gast tot, und die Mengen wachsen nicht unbegrenzt.
  guest.once("destroyed", () => {
    gastAdressen.delete(id);
    beendet.delete(id);
  });
  try {
    // Von der Seite ausgeloest (Link, location, meta refresh, Formular) oder Umleitung: nie. Gleiche-Dokument-
    // Navigationen (Anker, pushState) loesen diese Ereignisse nicht aus und bleiben moeglich; sie laden nichts.
    // Reihenfolge bei Seiten-Navigationen: did-start-navigation kommt VOR will-frame-navigate/will-navigate (etwa 0,3 ms);
    // vom Browser gestartete (loadURL, src, Verlauf) und about:blank loesen kein will-* aus.
    guest.on("will-navigate", (event) => event.preventDefault());
    guest.on("will-frame-navigate", (event) => event.preventDefault());
    guest.on("will-redirect", (event) => event.preventDefault());
    // Stolperdraht: beendet nur, was kein will-* abfaengt (vom Browser gestartet, about:, schon beendeter Gast). Eine
    // fremde Seiten-Navigation ueberlaesst er dem preventDefault oben. Hier nie etwas am Gast aufrufen (gastBeenden).
    guest.on("did-start-navigation", (event) => {
      let seiteAusgeloest = false;
      try {
        seiteAusgeloest = event.initiator != null;
      } catch {
        seiteAusgeloest = false;
      }
      const entscheidung = guestTripwireDecision({
        dead: beendet.has(id),
        isMainFrame: event.isMainFrame,
        isSameDocument: event.isSameDocument,
        url: event.url,
        attachedSrc: gastAdressen.get(id) ?? null,
        pageInitiated: seiteAusgeloest,
      });
      if (entscheidung === "beenden") gastBeenden(guest, `Navigation zu ${event.url.slice(0, 200)}`);
    });
    // Notnetz: ein festgeschriebener Hauptrahmen-Wechsel zu etwas Fremdem, das der Stolperdraht durchliess.
    guest.on("did-navigate", (_event, url) => {
      if (beendet.has(id)) return;
      const entscheidung = guestTripwireDecision({
        dead: false,
        isMainFrame: true,
        isSameDocument: false,
        url,
        attachedSrc: gastAdressen.get(id) ?? null,
        pageInitiated: false,
      });
      if (entscheidung === "beenden") gastBeenden(guest, `Festgeschrieben: ${url.slice(0, 200)}`);
    });
    // WebRTC und Fensteroeffnen zuletzt: wirft eines davon, ist der Gast schon voll bewacht (und wird beendet).
    guest.setWebRTCIPHandlingPolicy("disable_non_proxied_udp");
    guest.setWindowOpenHandler(() => ({ action: "deny" }));
    guest.on("select-bluetooth-device", (event, _devices, callback) => {
      event.preventDefault();
      callback("");
    });
  } catch (error) {
    gastBeenden(guest, `Haertung fehlgeschlagen: ${String(error)}`);
  }
}

/** Waechter fuer jede Ansicht der App: nur registrierte Fenster duerfen Gaeste anhaengen, und nur Wissens-Dokumente. */
function anhaengenPruefen(wc: WebContents): void {
  wc.on("will-attach-webview", (event, webPreferences, params) => {
    wartendeAdresse = null;
    const src = params.src ?? "";
    if (!gastHosts.has(wc) || !konzeptGastBereit() || !guestAttachAllowed(src)) {
      event.preventDefault();
      return;
    }
    // Nur zulassen, was hier gesetzt wird: alle Felder (auch zoomFactor aus webpreferences="...") verwerfen.
    const prefs = webPreferences as Record<string, unknown>;
    for (const key of Object.keys(prefs)) delete prefs[key];
    Object.assign(webPreferences, guestWebPreferences(), {
      preload: gastPreload,
      devTools: false,
      webSecurity: true,
      allowRunningInsecureContent: false,
      experimentalFeatures: false,
      enableWebSQL: false,
      spellcheck: false,
      navigateOnDragDrop: false,
      zoomFactor: wc.zoomFactor,
    });
    prefs.disablePopups = true;
    for (const key of VERBOTENE_PARAMETER) delete params[key];
    params.partition = KONZEPT_PARTITION;
    wartendeAdresse = src;
  });
}

/** Fuer jede neue Ansicht: Anhaenge-Waechter; ist es ein Gast der Partition `konzept`, sofort die Haertung. */
function ansichtErzeugt(wc: WebContents): void {
  anhaengenPruefen(wc);
  if (wc.getType() === "webview" && gastSitzung !== null && wc.session === gastSitzung) {
    const adresse = wartendeAdresse;
    wartendeAdresse = null;
    gastAbsichern(wc, adresse);
  }
}

/** Gastbereich eingerichtet und toter Proxy bestaetigt? Nur dann bekommt ein Fenster `webviewTag` und das Kennzeichen. */
export function konzeptGastBereit(): boolean {
  return eingerichtet && proxyBestaetigt;
}

/**
 * Richtet die Gast-Sitzung einmal je Prozess ein (nach app ready, vor dem ersten Fenster) und wartet zeitlich begrenzt
 * auf die Bestaetigung des toten Proxys. Liefert {@link konzeptGastBereit}; lehnt nie ab.
 */
export function prepareKonzeptGast(deps: Vorbereitung): Promise<boolean> {
  if (vorbereitung !== null) return vorbereitung;
  vorbereitung = (async () => {
    try {
      // Zuerst der Waechter: ab jetzt haengt keine Ansicht einen Gast an, die nicht ausdruecklich Gastgeber ist.
      app.on("web-contents-created", (_event, wc) => ansichtErzeugt(wc));
      gastPreload = deps.guestPreloadPath;
      const ses = session.fromPartition(KONZEPT_PARTITION);
      gastSitzung = ses;
      sitzungEinrichten(ses, deps.serve);
      eingerichtet = true;
      let zeitgeber: NodeJS.Timeout | undefined;
      const frist = new Promise<never>((_, reject) => {
        zeitgeber = setTimeout(
          () => reject(new Error(`konzept: Proxy nicht binnen ${PROXY_WARTEZEIT_MS} ms bestaetigt, erstes Fenster ohne Gastbereich`)),
          PROXY_WARTEZEIT_MS,
        );
      });
      try {
        await Promise.race([proxyBestaetigen(ses), frist]);
      } finally {
        clearTimeout(zeitgeber);
      }
    } catch (error) {
      console.error("[konzept] Gastbereich nicht verfuegbar, Gaeste werden verweigert:", error);
    }
    return konzeptGastBereit();
  })();
  return vorbereitung;
}

/** Macht das App-Fenster zum Gastgeber fuer Konzept-Gaeste (nur wenn {@link konzeptGastBereit}). */
export function setupKonzeptGast(win: BrowserWindow): void {
  if (konzeptGastBereit()) gastHosts.add(win.webContents);
}
