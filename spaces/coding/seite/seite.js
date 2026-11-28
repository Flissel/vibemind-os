"use strict";
// Liest nur. Alle Inhalte kommen als Text in die Seite (textContent), nie als HTML.

const STUFE = { annahme: "Annahme", plan: "Plan", bau: "Bau", pruefung: "Prüfung", abschluss: "Abschluss" };
const ZUSTAND = { wartet: "wartet", laeuft: "läuft", fertig: "fertig", ungeprueft: "ungeprüft",
                  fehlgeschlagen: "gescheitert", abgebrochen: "abgebrochen" };
const GEBAUT = ["fertig", "ungeprueft"];
const APP_NAME = "Die App starten und prüfen";
let gewaehlt = null;

function el(art, text, klasse) {
  const e = document.createElement(art);
  if (text !== undefined && text !== null) e.textContent = text;
  if (klasse) e.className = klasse;
  return e;
}

function leeren(knoten) {
  while (knoten.firstChild) knoten.removeChild(knoten.firstChild);
}

function pille(zustand) {
  const p = el("span", ZUSTAND[zustand] || zustand, "pille");
  p.dataset.zustand = zustand;
  return p;
}

function dauer(sekunden) {
  if (sekunden === null || sekunden === undefined || isNaN(sekunden)) return "";
  const s = Math.max(0, Math.round(Number(sekunden)));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${s % 60} s`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

function zeitpunkt(sekunden) {
  if (!sekunden) return "";
  const d = new Date(Number(sekunden) * 1000);
  const heute = new Date();
  const uhr = d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === heute.toDateString()) return `heute ${uhr}`;
  return `${d.toLocaleDateString("de-DE", { day: "numeric", month: "short" })} ${uhr}`;
}

// Name eines Pakets ohne Kennung; ein aelterer Dienst liefert noch kein "name".
function paketname(p) {
  if (p.name) return p.name;
  if (p.paket === "APP") return APP_NAME;
  const titel = String(p.titel || "");
  return p.paket && titel.startsWith(p.paket) ? titel.slice(p.paket.length).trim() : titel || p.paket || "";
}

function wasGeprueft(befehl) {
  const b = String(befehl || "");
  if (/^npm start\b/.test(b)) return "Die App startet und antwortet";
  if (/^npm test\b/.test(b)) return "Alle Tests des Projekts";
  if (/test/.test(b)) return "Tests dieses Pakets";
  return "Prüfung";
}

function laufzeile(stand) {
  const k = stand.kurz || {};
  if (stand.zustand === "laeuft" || stand.zustand === "wartet") {
    const paket = k.pakete_gesamt ? ` · Paket ${k.paket_nr} von ${k.pakete_gesamt}` : "";
    return `${STUFE[k.stufe || stand.stufe] || stand.stufe}${paket}`;
  }
  return [zeitpunkt(stand.angelegt), dauer(stand.dauer_s)].filter(Boolean).join(" · ");
}

function zeigeListe(daten) {
  const liste = document.getElementById("liste");
  leeren(liste);
  document.getElementById("leer").hidden = daten.auftraege.length > 0;
  for (const a of daten.auftraege) {
    const knopf = el("button");
    knopf.type = "button";
    knopf.setAttribute("aria-current", String(a.auftrag === gewaehlt));
    knopf.appendChild(el("span", a.titel || "Ohne Titel", "name"));
    const zeile = el("span", null, "zeile");
    zeile.appendChild(pille(a.zustand));
    zeile.appendChild(el("small", laufzeile(a)));
    knopf.appendChild(zeile);
    knopf.addEventListener("click", () => { waehle(a.auftrag, a.titel); laden(); });
    const li = el("li");
    li.appendChild(knopf);
    liste.appendChild(li);
  }
}

function satz(stand, ergebnis) {
  const pakete = stand.pakete || [];
  const k = stand.kurz || {};
  if (stand.zustand === "fertig") {
    const zeit = dauer(stand.dauer_s || (ergebnis && ergebnis.dauer_s));
    const geprueft = ergebnis && ergebnis.pruefung && ergebnis.pruefung.bestanden ? " Die Abnahme ist bestanden." : "";
    return `Fertig${zeit ? ` in ${zeit}` : ""}.${geprueft}`;
  }
  if (stand.zustand === "ungeprueft") return (stand.abnahme && stand.abnahme.satz) || "Gebaut, aber ungeprüft.";
  if (stand.zustand === "laeuft" && stand.abnahme && stand.abnahme.zustand === "laeuft") return stand.abnahme.satz;
  if (stand.zustand === "fehlgeschlagen") return stand.fehler || "Der Lauf ist gescheitert.";
  if (stand.zustand === "abgebrochen") return "Der Lauf wurde abgebrochen.";
  if (stand.zustand === "laeuft") {
    const aktuell = pakete.find((p) => p.zustand !== "fertig");
    const wo = aktuell && k.pakete_gesamt ? `, Paket ${k.paket_nr} von ${k.pakete_gesamt}: ${paketname(aktuell)}` : "";
    const seit = stand.angelegt ? dauer(Date.now() / 1000 - Number(stand.angelegt)) : "";
    return `Läuft${seit ? ` seit ${seit}` : ""} – ${STUFE[stand.stufe] || stand.stufe}${wo}.`;
  }
  if (stand.art === "engine" && !stand.angestossen) return "Der Lauf ist angelegt, der Erzeuger ist noch nicht gestartet.";
  return "Wartet auf den Start.";
}

function meta(stand) {
  const teile = [];
  if (stand.angelegt) teile.push(`angelegt ${zeitpunkt(stand.angelegt)}`);
  teile.push(stand.herkunft === "getippt" ? "aus einem getippten Satz" : "aus einem Entwurf");
  const n = (stand.pakete || []).length;
  if (n) teile.push(`${n} ${n === 1 ? "Paket" : "Pakete"}`);
  if (stand.modellaufrufe_erwartet) {
    const bis = stand.modellaufrufe_hoechstens ? ` bis ${stand.modellaufrufe_hoechstens}` : "";
    teile.push(`${stand.modellaufrufe_erwartet}${bis} Modellaufrufe vorgesehen`);
  }
  return teile.join(" · ");
}

function zeigeStand(stand, ergebnis) {
  document.getElementById("lauf").hidden = false;
  const karte = document.querySelector(".zusammenfassung");
  karte.dataset.zustand = stand.zustand;
  document.getElementById("titel").textContent = stand.titel || "Ohne Titel";
  const z = document.getElementById("zustand");
  z.textContent = ZUSTAND[stand.zustand] || stand.zustand;
  z.dataset.zustand = stand.zustand;
  document.getElementById("satz").textContent = satz(stand, ergebnis);
  document.getElementById("meta").textContent = meta(stand);

  const grund = document.getElementById("grund");
  grund.hidden = !(stand.zustand === "fehlgeschlagen" && stand.fehler_grund);
  document.getElementById("grund-text").textContent = stand.fehler_grund || "";

  const hinweise = document.getElementById("hinweise");
  leeren(hinweise);
  for (const h of stand.hinweise || []) hinweise.appendChild(el("li", h));

  const stufen = document.getElementById("stufen");
  leeren(stufen);
  for (const s of stand.stufen || []) {
    const li = el("li");
    li.dataset.zustand = s.zustand;
    li.appendChild(el("span", STUFE[s.stufe] || s.stufe, "name"));
    li.appendChild(el("span", ZUSTAND[s.zustand] || s.zustand));
    stufen.appendChild(li);
  }

  const start = document.getElementById("start");
  const befehl = ergebnis && ergebnis.start;
  start.hidden = !befehl;
  if (befehl) {
    document.getElementById("start-befehl").textContent = befehl;
    document.getElementById("start-ordner").textContent = ergebnis.zielordner || "";
    const ok = ergebnis.pruefung && ergebnis.pruefung.bestanden;
    document.getElementById("start-pruefung").textContent = ok
      ? "✓ Geprüft: Die App startet mit diesem Befehl und antwortet."
      : "Die Prüfung des Starts ist nicht bestanden.";
  }

  document.getElementById("pakete-titel").textContent = GEBAUT.includes(stand.zustand) ? "Was gebaut wurde"
    : stand.zustand === "laeuft" || stand.zustand === "wartet" ? "Was gebaut wird" : "Die Pakete";
  const pakete = document.getElementById("pakete");
  leeren(pakete);
  (stand.pakete || []).forEach((p, i) => pakete.appendChild(paketzeile(p, i + 1)));
}

function paketzeile(p, nr) {
  const li = el("li");
  li.dataset.zustand = p.zustand;
  li.appendChild(el("span", String(nr), "nr"));

  const kopf = el("div", null, "kopfzeile");
  // Nur der Name; die Kennung des Pakets (AP-01) steht nicht auf der Seite.
  kopf.appendChild(el("h3", paketname(p)));
  kopf.appendChild(pille(p.zustand));
  li.appendChild(kopf);

  if (p.dateien && p.dateien.length) {
    const chips = el("div", null, "chips");
    for (const d of p.dateien) chips.appendChild(el("code", d));
    li.appendChild(chips);
  } else if (p.zustand === "abgebrochen") {
    li.appendChild(el("p", "Nicht mehr begonnen, weil ein früheres Paket gescheitert ist.", "text"));
  }

  const pruefungen = el("div");
  for (const x of p.pruefungen || []) {
    // Eine Prüfung, die noch nicht gelaufen ist, ist nicht "nicht bestanden".
    const offen = x.ausgefuehrt === false
      || (x.ausgefuehrt === undefined && p.pruefung !== "fertig" && p.pruefung !== "fehlgeschlagen");
    const was = wasGeprueft(x.befehl);
    const zeile = el("p", offen ? `○ ${was}: noch nicht geprüft` : x.bestanden ? `✓ ${was}: bestanden`
      : `✕ ${was}: nicht bestanden`, "pruefzeile");
    zeile.dataset.ok = offen ? "offen" : String(Boolean(x.bestanden));
    // Der Befehl steht nur im Hinweis beim Darüberfahren, nicht als Text.
    if (x.befehl) zeile.title = `Befehl: ${x.befehl}`;
    pruefungen.appendChild(zeile);
  }
  if (pruefungen.firstChild) li.appendChild(pruefungen);
  return li;
}

async function holen(pfad) {
  const antwort = await fetch(pfad, { cache: "no-store" });
  return { ok: antwort.ok, daten: await antwort.json() };
}

async function laden() {
  try {
    const liste = await holen("/api/liste");
    if (liste.ok) zeigeListe(liste.daten);
    const frage = gewaehlt ? `?auftrag=${encodeURIComponent(gewaehlt)}` : "";
    const stand = await holen(`/api/stand${frage}`);
    if (stand.ok) {
      if (!gewaehlt) waehle(stand.daten.auftrag, stand.daten.titel);
      const ergebnis = GEBAUT.includes(stand.daten.zustand)
        ? await holen(`/api/ergebnis?auftrag=${encodeURIComponent(stand.daten.auftrag)}`) : null;
      zeigeStand(stand.daten, ergebnis && ergebnis.ok ? ergebnis.daten : null);
      if (liste.ok) zeigeListe(liste.daten);
      await ladeLaufsicht(stand.daten);
    } else {
      document.getElementById("lauf").hidden = true;
    }
    document.getElementById("verbindung").textContent = "";
  } catch (fehler) {
    document.getElementById("verbindung").textContent = "Der Dienst des Project Space antwortet nicht.";
  }
}

// ── Laufsicht: Fortschritt, Aufgaben, Protokoll (wie früher im Engine-Dashboard, aber aus den Dateien) ──

let gewaehltTitel = "";
let reiter = "ueberblick";
let protokollAb = null;          // nächstes Byte des Protokolls; null = von vorn
let protokollFuer = null;        // für welchen Lauf das Protokoll im Fenster steht
const PROTOKOLL_MAX = 200000;    // so viele Zeichen bleiben höchstens im Fenster
const verlaeufe = new Map();     // Vibe-Coder: Verlauf je Lauf, nur im Speicher

function waehle(auftrag, titel) {
  if (auftrag === gewaehlt) return;
  gewaehlt = auftrag;
  gewaehltTitel = titel || "";
  protokollAb = null;
  protokollFuer = null;
  leeren(document.getElementById("protokoll"));
  zeigeChat();
}

function zeigeFortschritt(daten, zustand) {
  const box = document.getElementById("fortschritt");
  box.hidden = !daten || !daten.gesamt;
  if (box.hidden) return;
  const fuellung = document.getElementById("balken-fuellung");
  fuellung.style.width = `${daten.anteil}%`;
  document.getElementById("balken").setAttribute("aria-valuenow", String(daten.anteil));
  box.dataset.zustand = zustand || "";
  const teile = [`${daten.fertig} von ${daten.gesamt} Aufgaben fertig`];
  if (daten.laeuft) teile.push(`${daten.laeuft} läuft`);
  if (daten.fehlgeschlagen) teile.push(`${daten.fehlgeschlagen} gescheitert`);
  document.getElementById("fortschritt-text").textContent = teile.join(" · ");
}

function zeigeAufgaben(daten) {
  const ziel = document.getElementById("aufgaben");
  leeren(ziel);
  const pakete = (daten && daten.pakete) || [];
  document.getElementById("aufgaben-leer").hidden = pakete.some((p) => p.aufgaben.length);
  for (const paket of pakete) {
    const karte = el("article", null, "karte aufgabenpaket");
    karte.appendChild(el("h3", paket.name));
    const liste = el("ol", null, "aufgabenliste");
    for (const a of paket.aufgaben) {
      const li = el("li");
      li.dataset.zustand = a.zustand;
      li.appendChild(pille(a.zustand));
      li.appendChild(el("span", a.art, "art"));
      if (a.dateien.length) {
        const chips = el("span", null, "chips");
        for (const d of a.dateien) chips.appendChild(el("code", d));
        li.appendChild(chips);
      }
      if (a.versuche) li.appendChild(el("small", `${a.versuche + 1}. Versuch`, "leise"));
      if (a.befehl) li.title = `Befehl: ${a.befehl}`;
      if (a.fehler) li.appendChild(el("p", a.fehler, "aufgabe-fehler"));
      liste.appendChild(li);
    }
    karte.appendChild(liste);
    ziel.appendChild(karte);
  }
}

async function ladeProtokoll(auftrag) {
  const fenster = document.getElementById("protokoll");
  if (protokollFuer !== auftrag) { protokollAb = null; leeren(fenster); protokollFuer = auftrag; }
  const ab = protokollAb === null ? "" : `&ab=${protokollAb}`;
  const antwort = await holen(`/api/protokoll?auftrag=${encodeURIComponent(auftrag)}${ab}`);
  if (!antwort.ok || protokollFuer !== auftrag) return;
  const d = antwort.daten;
  if (d.neu) leeren(fenster);
  if (d.text) fenster.appendChild(document.createTextNode(d.text));
  if (fenster.textContent.length > PROTOKOLL_MAX) {
    const rest = fenster.textContent.slice(-PROTOKOLL_MAX);
    leeren(fenster);
    fenster.appendChild(document.createTextNode(rest.slice(rest.indexOf("\n") + 1)));
  }
  protokollAb = d.weiter;
  const kb = Math.round((d.groesse || 0) / 1024);
  document.getElementById("protokoll-satz").textContent = d.satz
    || (d.gekuerzt ? `Ende des Protokolls (${kb} KB, ältere Zeilen ausgelassen)` : `Protokoll (${kb} KB)`);
  if (document.getElementById("protokoll-folgen").checked) fenster.scrollTop = fenster.scrollHeight;
}

let letzteAufgaben = 0;
async function ladeLaufsicht(stand) {
  const auftrag = stand.auftrag;
  const laeuft = stand.zustand === "laeuft" || stand.zustand === "wartet";
  // Ein fertiger Lauf ändert sich nicht mehr: dann reicht es, alle 10 s nachzusehen.
  if (laeuft || reiter === "aufgaben" || Date.now() - letzteAufgaben > 10000 || protokollFuer !== auftrag) {
    const aufgaben = await holen(`/api/aufgaben?auftrag=${encodeURIComponent(auftrag)}`);
    letzteAufgaben = Date.now();
    if (aufgaben.ok) {
      zeigeFortschritt(aufgaben.daten, stand.zustand);
      zeigeAufgaben(aufgaben.daten);
    }
  }
  if (reiter === "protokoll" && (laeuft || protokollFuer !== auftrag)) await ladeProtokoll(auftrag);
}

function zeigeReiter(name) {
  reiter = name;
  for (const knopf of document.querySelectorAll(".reiter button")) {
    knopf.setAttribute("aria-selected", String(knopf.dataset.reiter === name));
  }
  for (const teil of ["ueberblick", "aufgaben", "protokoll"]) {
    document.getElementById(`reiter-${teil}`).hidden = teil !== name;
  }
  if (name === "protokoll" && gewaehlt) ladeProtokoll(gewaehlt);
}
for (const knopf of document.querySelectorAll(".reiter button")) {
  knopf.addEventListener("click", () => zeigeReiter(knopf.dataset.reiter));
}

// ── Vibe-Coder (Stufe V1): Chat-Gerüst, noch ohne Modell ──

const chat = document.getElementById("chat");
const NICHT_ANGESCHLOSSEN = "Nachbessern ist noch nicht angeschlossen. Ich habe deinen Satz nur angezeigt; "
  + "an der App wurde nichts geändert.";

function zeigeChat() {
  if (!chat || !chat.verlauf) return;
  chat.titel = gewaehltTitel ? `Vibe-Coder · ${gewaehltTitel}` : "Vibe-Coder";
  chat.kontext = { auftrag: gewaehlt };
  chat.verlauf(verlaeufe.get(gewaehlt) || []);
  chat.gesperrt = !gewaehlt;
}

if (chat) {
  customElements.whenDefined("vm-chat").then(() => {
    chat.connect({
      // Stufe V1: kein Modell, kein Aufruf an den Dienst. Die Antwort sagt ehrlich, was geschah.
      send: async () => ({ text: NICHT_ANGESCHLOSSEN, marke: "fehler" }),
      kontext: { auftrag: gewaehlt },
    });
    chat.addEventListener("vm-chat-paar", (e) => {
      const auftrag = chat.kontext.auftrag;
      if (!auftrag) return;
      const liste = verlaeufe.get(auftrag) || [];
      liste.push(e.detail);
      verlaeufe.set(auftrag, liste.slice(-50));
    });
    zeigeChat();
  });
}

// ── Ansichten: Aufträge und Vibe-Coder sind diese Seite, die Live-Vorschau ist rechts immer dabei ──
// "vorschau" zeigt sie in voller Breite. Die Ansicht steht im Anker (#vibe), damit die App sie direkt öffnen kann.

let oberflaechen = {};
let ansicht = "auftraege";
const ANSICHTEN = ["auftraege", "vibe", "vorschau"];

function setzeRahmen(rahmen, satz, eintrag, an) {
  // Nur ein Rahmen hält die Verbindung zur Vorschau, der andere ist leer.
  if (!an || !eintrag) {
    if (rahmen.getAttribute("src")) rahmen.removeAttribute("src");
    rahmen.hidden = true;
    satz.hidden = true;
    return;
  }
  rahmen.title = eintrag.titel;
  if (eintrag.laeuft) {
    satz.hidden = true;
    if (rahmen.getAttribute("src") !== eintrag.url) rahmen.src = eintrag.url;
    rahmen.hidden = false;
  } else {
    if (rahmen.getAttribute("src")) rahmen.removeAttribute("src");
    rahmen.hidden = true;
    satz.textContent = eintrag.satz_aus;
    satz.hidden = false;
  }
}

function zeigeAnsicht(name) {
  ansicht = ANSICHTEN.includes(name) ? name : "auftraege";
  for (const knopf of document.querySelectorAll(".knoepfe button")) {
    knopf.setAttribute("aria-pressed", String(knopf.dataset.ansicht === ansicht));
  }
  const voll = ansicht === "vorschau";
  document.getElementById("arbeit").hidden = voll;
  document.getElementById("fremd").hidden = !voll;
  document.getElementById("lauf").dataset.ansicht = ansicht;
  document.getElementById("vibe").hidden = ansicht !== "vibe";
  // "lauf" zeigt zeigeStand(); in der Vibe-Ansicht tritt der Chat an seine Stelle.
  document.getElementById("lauf").classList.toggle("verdeckt", ansicht !== "auftraege");
  const vorschau = oberflaechen.vorschau;
  setzeRahmen(document.getElementById("fremd-rahmen"), document.getElementById("fremd-satz"), vorschau, voll);
  setzeRahmen(document.getElementById("vorschau-rahmen"), document.getElementById("vorschau-satz"), vorschau, !voll);
  if (!vorschau && !voll) {
    const satz = document.getElementById("vorschau-satz");
    satz.textContent = "Die Live-Vorschau wird gesucht …";
    satz.hidden = false;
  }
}

async function ladeOberflaechen() {
  try {
    const antwort = await holen("/api/oberflaechen");
    if (!antwort.ok) return;
    oberflaechen = {};
    for (const eintrag of antwort.daten) oberflaechen[eintrag.name] = eintrag;
    const knopf = document.querySelector('.knoepfe button[data-ansicht="vorschau"]');
    if (oberflaechen.vorschau) knopf.dataset.laeuft = String(oberflaechen.vorschau.laeuft);
    zeigeAnsicht(ansicht);
  } catch (fehler) {
    // Die Verbindungszeile von laden() sagt schon, wenn der Dienst fehlt.
  }
}

function wechsle(name) {
  history.replaceState(null, "", name === "auftraege" ? "#" : `#${name}`);
  zeigeAnsicht(name);
}
for (const knopf of document.querySelectorAll(".knoepfe button")) {
  knopf.addEventListener("click", () => wechsle(knopf.dataset.ansicht));
}
document.getElementById("vorschau-gross").addEventListener("click", () => wechsle("vorschau"));
window.addEventListener("hashchange", () => zeigeAnsicht(location.hash.slice(1) || "auftraege"));
ansicht = location.hash.slice(1) || "auftraege";
zeigeAnsicht(ansicht);

laden();
ladeOberflaechen();
setInterval(laden, 2000);
setInterval(ladeOberflaechen, 5000);
