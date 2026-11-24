"use strict";
// Liest nur. Alle Inhalte kommen als Text in die Seite (textContent), nie als HTML.

const STUFE = { annahme: "Annahme", plan: "Plan", bau: "Bau", pruefung: "Prüfung", abschluss: "Abschluss" };
const ZUSTAND = { wartet: "wartet", laeuft: "läuft", fertig: "fertig", fehlgeschlagen: "gescheitert",
                  abgebrochen: "abgebrochen" };
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
    knopf.addEventListener("click", () => { gewaehlt = a.auftrag; laden(); });
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
    const geprueft = ergebnis && ergebnis.pruefung && ergebnis.pruefung.bestanden ? " Alle Prüfungen bestanden." : "";
    return `Fertig${zeit ? ` in ${zeit}` : ""}.${geprueft}`;
  }
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

  document.getElementById("pakete-titel").textContent = stand.zustand === "fertig" ? "Was gebaut wurde"
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
      gewaehlt = gewaehlt || stand.daten.auftrag;
      const ergebnis = stand.daten.zustand === "fertig"
        ? await holen(`/api/ergebnis?auftrag=${encodeURIComponent(stand.daten.auftrag)}`) : null;
      zeigeStand(stand.daten, ergebnis && ergebnis.ok ? ergebnis.daten : null);
      if (liste.ok) zeigeListe(liste.daten);
    } else {
      document.getElementById("lauf").hidden = true;
    }
    document.getElementById("verbindung").textContent = "";
  } catch (fehler) {
    document.getElementById("verbindung").textContent = "Der Dienst des Project Space antwortet nicht.";
  }
}

laden();
setInterval(laden, 2000);
