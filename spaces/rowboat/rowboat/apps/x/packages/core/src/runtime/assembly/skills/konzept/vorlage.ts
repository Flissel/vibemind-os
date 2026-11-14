import { KONZEPT_BLOCK_ID, type KonzeptDaten } from '@x/shared/dist/konzept.js';

// Seitenvorlage fuer Videokonzepte: eine Datei, nichts aus dem Netz. Die Seite
// zeichnet sich im Skript aus dem Datenblock (nur DOM-Methoden und textContent,
// kein innerHTML). Die verbindliche Pruefung ist checkVideoKonzept (App-Seite);
// das Skript rechnet nur die wenigen Anzeige-Hinweise nach.

const SEITE = String.raw`<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{{TITEL}}</title>
<script type="application/json" id="${KONZEPT_BLOCK_ID}">
{{DATEN}}
</script>
<style>
:root {
  /* Markenfarben: nur hier aendern */
  --akzent: #2f6f6a;
  --akzent-text: #ffffff;
  --flaeche: #f6f7f5;
  --karte: #ffffff;
  --text: #1d2321;
  --leise: #5b6663;
  --rand: #d9dedb;
  --warn: #8a5a00;
  --warn-flaeche: #fff4d6;
}
@media (prefers-color-scheme: dark) {
  :root {
    --akzent: #6fbab3;
    --akzent-text: #10201e;
    --flaeche: #151918;
    --karte: #1e2423;
    --text: #e8ecea;
    --leise: #a3aeaa;
    --rand: #323b39;
    --warn: #f0c36a;
    --warn-flaeche: #3a2f14;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--flaeche); color: var(--text); font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 52rem; margin: 0 auto; padding: 1.25rem 1rem 3rem; }
h1 { font-size: 1.75rem; line-height: 1.2; margin: 0 0 .25rem; overflow-wrap: anywhere; }
h2 { font-size: 1.1rem; margin: 2rem 0 .75rem; }
.leise { color: var(--leise); margin: 0 0 .5rem; }
blockquote { margin: 1rem 0; padding: .75rem 1rem; border-left: 4px solid var(--akzent); background: var(--karte); border-radius: 0 .5rem .5rem 0; font-size: 1.1rem; overflow-wrap: anywhere; }
.kern { background: var(--karte); border: 1px solid var(--rand); border-radius: .5rem; padding: .75rem 1rem; overflow-wrap: anywhere; }
.warnung { background: var(--warn-flaeche); color: var(--warn); border: 1px solid var(--warn); border-radius: .5rem; padding: .5rem 1rem; margin: 1rem 0; }
.warnung ul { margin: .25rem 0; padding-left: 1.25rem; }
details { background: var(--karte); border: 1px solid var(--rand); border-radius: .5rem; margin: .75rem 0; }
summary { cursor: pointer; padding: .65rem 1rem; font-weight: 600; overflow-wrap: anywhere; }
.szenen { display: grid; gap: .5rem; padding: 0 .75rem .75rem; }
.szene { display: flex; gap: .75rem; align-items: flex-start; border: 1px solid var(--rand); border-radius: .5rem; padding: .5rem .75rem; background: var(--flaeche); }
.nr { flex: none; min-width: 1.75rem; height: 1.75rem; border-radius: 50%; background: var(--akzent); color: var(--akzent-text); display: grid; place-items: center; font-size: .85rem; font-weight: 700; }
.szene .text { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.notiz { color: var(--leise); font-size: .9rem; margin-top: .25rem; }
.badge { flex: none; font-size: .75rem; border: 1px solid var(--akzent); color: var(--akzent); border-radius: 999px; padding: .1rem .55rem; white-space: nowrap; }
.zahlen { display: flex; flex-wrap: wrap; gap: .5rem; margin: 0; padding: 0; list-style: none; }
.zahlen li { background: var(--karte); border: 1px solid var(--rand); border-radius: .5rem; padding: .35rem .75rem; font-size: .9rem; overflow-wrap: anywhere; }
.bezuege { display: flex; flex-wrap: wrap; gap: .5rem; }
.nur-text { border: 1px dashed var(--rand); border-radius: .5rem; padding: .35rem .8rem; color: var(--leise); overflow-wrap: anywhere; }
button { overflow-wrap: anywhere; font: inherit; cursor: pointer; background: var(--karte); color: var(--text); border: 1px solid var(--akzent); border-radius: .5rem; padding: .35rem .8rem; }
button:hover, button:focus-visible { background: var(--akzent); color: var(--akzent-text); }
@media (max-width: 420px) { .szene { flex-wrap: wrap; } h1 { font-size: 1.4rem; } }
</style>
</head>
<body>
<main>
<h1>{{TITEL}}</h1>
<div id="inhalt"></div>
</main>
<script>
(function () {
  var BILDART = { slide: 'Folie', avatar_slide: 'Avatar + Folie', avatar_gross: 'Avatar groß', clip: 'Clip' };
  // Eine Szene traegt 35 bis 110 gesprochene Woerter, also etwa 15 bis 45 Sekunden.
  var SEKUNDEN_MIN = 15;
  var SEKUNDEN_MAX = 45;
  var SZENEN_MIN = 8, SZENEN_MAX = 15, MINUTEN_MIN = 3, MINUTEN_MAX = 5, STUFEN_MAX = 8;
  var wurzel = document.getElementById('inhalt');

  function el(tag, klasse, text) {
    var n = document.createElement(tag);
    if (klasse) n.className = klasse;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }
  function liste(v) { return Array.isArray(v) ? v : []; }
  function minSek(sek) { var s = Math.round(sek); return Math.floor(s / 60) + ':' + ('0' + (s % 60)).slice(-2); }
  function ist(v) { return v !== null && typeof v === 'object' && !Array.isArray(v); }

  // "[[Marke/Marke]]", "[[Marke/Marke|Name]]", "[[Marke/Marke#Teil]]" -> Pfad fuer die Fläche.
  // Ein Ziel mit ".." oder mit fuehrendem "/" ergibt keinen Knopf (pfad: null).
  function bezug(roh) {
    var s = String(roh).trim().replace(/^\[\[/, '').replace(/\]\]$/, '');
    var strich = s.indexOf('|');
    var name = strich > -1 ? s.slice(strich + 1).trim() : '';
    if (strich > -1) s = s.slice(0, strich);
    s = s.split('#')[0].trim();
    if (!s) return null;
    var label = name || s;
    if (s.indexOf('..') > -1 || s.charAt(0) === '/' || s.charAt(0) === String.fromCharCode(92)) return { pfad: null, label: label };
    return { pfad: 'knowledge/' + (/\.(md|html?|xhtml)$/i.test(s) ? s : s + '.md'), label: label };
  }

  function zeichne() {
    var daten = null;
    try { daten = JSON.parse(document.getElementById('${KONZEPT_BLOCK_ID}').textContent); } catch (e) { daten = null; }
    var v = daten && daten.video;
    if (!ist(v)) {
      wurzel.appendChild(el('div', 'warnung', 'Der Datenblock dieser Seite fehlt oder ist nicht lesbar.'));
      return;
    }
    if (!Array.isArray(v.stufen)) throw new Error('"stufen" muss eine Liste sein.');
    var szenen = [];
    var zaehler = {};
    v.stufen.forEach(function (st, i) {
      if (!ist(st) || !Array.isArray(st.szenen)) throw new Error('Stufe ' + (i + 1) + ' braucht eine Liste "szenen".');
      st.szenen.forEach(function (sz, j) {
        if (!ist(sz)) throw new Error('Szene ' + (j + 1) + ' in Stufe ' + (i + 1) + ' ist kein Objekt.');
        szenen.push(sz);
        zaehler[sz.bildart] = (zaehler[sz.bildart] || 0) + 1;
      });
    });
    var stufen = v.stufen;
    var von = szenen.length * SEKUNDEN_MIN;
    var bis = szenen.length * SEKUNDEN_MAX;
    var zielOk = typeof v.minuten === 'number';

    var hinweise = [];
    if (szenen.length < SZENEN_MIN || szenen.length > SZENEN_MAX) hinweise.push('Es sind ' + SZENEN_MIN + ' bis ' + SZENEN_MAX + ' Szenen vorgesehen, hier sind es ' + szenen.length + '.');
    if (!zielOk || v.minuten < MINUTEN_MIN || v.minuten > MINUTEN_MAX) hinweise.push('Die Länge soll zwischen ' + MINUTEN_MIN + ' und ' + MINUTEN_MAX + ' Minuten liegen.');
    if (stufen.length > STUFEN_MAX) hinweise.push('Es sind höchstens ' + STUFEN_MAX + ' Stufen vorgesehen, hier sind es ' + stufen.length + '.');
    if (zielOk && (v.minuten * 60 < von || v.minuten * 60 > bis)) hinweise.push('Mit ' + szenen.length + ' Szenen sind etwa ' + minSek(von) + ' bis ' + minSek(bis) + ' möglich; Ziel sind ' + v.minuten + ' Minuten.');
    if (hinweise.length) {
      var w = el('div', 'warnung');
      w.setAttribute('role', 'alert');
      w.appendChild(el('strong', null, 'Außerhalb der Grenzen'));
      var wl = el('ul');
      hinweise.forEach(function (h) { wl.appendChild(el('li', null, h)); });
      w.appendChild(wl);
      wurzel.appendChild(w);
    }

    wurzel.appendChild(el('p', 'leise', v.thema));
    wurzel.appendChild(el('p', 'leise', 'Zielgruppe: ' + v.zielgruppe + ' · ' + v.minuten + ' Minuten'));
    if (v.auftrag) wurzel.appendChild(el('p', 'leise', 'Übergeben als Auftrag ' + v.auftrag));

    wurzel.appendChild(el('h2', null, 'Hook'));
    wurzel.appendChild(el('blockquote', null, v.hook));
    wurzel.appendChild(el('h2', null, 'Kernaussage'));
    wurzel.appendChild(el('div', 'kern', v.kernaussage));

    wurzel.appendChild(el('h2', null, 'Stufen und Szenen'));
    var nr = 0;
    stufen.forEach(function (st, i) {
      var d = el('details');
      d.open = true;
      var anzahl = st.szenen.length;
      d.appendChild(el('summary', null, (i + 1) + '. ' + (typeof st.name === 'string' && st.name.trim() ? st.name : 'Stufe ' + (i + 1)) + ' (' + anzahl + (anzahl === 1 ? ' Szene)' : ' Szenen)')));
      var box = el('div', 'szenen');
      st.szenen.forEach(function (sz) {
        nr += 1;
        var k = el('div', 'szene');
        k.appendChild(el('span', 'nr', nr));
        var t = el('div', 'text', sz.kurz);
        if (sz.notiz) t.appendChild(el('div', 'notiz', sz.notiz));
        k.appendChild(t);
        k.appendChild(el('span', 'badge', BILDART[sz.bildart] || sz.bildart));
        box.appendChild(k);
      });
      d.appendChild(box);
      wurzel.appendChild(d);
    });

    wurzel.appendChild(el('h2', null, 'Abschluss'));
    wurzel.appendChild(el('div', 'kern', v.abschluss));

    wurzel.appendChild(el('h2', null, 'Zusammenfassung'));
    var z = el('ul', 'zahlen');
    z.appendChild(el('li', null, stufen.length + ' Stufen'));
    z.appendChild(el('li', null, szenen.length + ' Szenen'));
    Object.keys(BILDART).forEach(function (art) {
      if (zaehler[art]) z.appendChild(el('li', null, BILDART[art] + ': ' + zaehler[art]));
    });
    z.appendChild(el('li', null, 'Geschätzte Länge ' + minSek(von) + ' bis ' + minSek(bis) + ' (Ziel ' + minSek((zielOk ? v.minuten : 0) * 60) + ')'));
    wurzel.appendChild(z);

    var refs = liste(daten.bezuege).map(bezug).filter(Boolean);
    if (refs.length) {
      wurzel.appendChild(el('h2', null, 'Verweise'));
      var bz = el('div', 'bezuege');
      refs.forEach(function (r) {
        if (r.pfad === null) { bz.appendChild(el('span', 'nur-text', r.label)); return; }
        var b = el('button', null, r.label);
        b.type = 'button';
        b.addEventListener('click', function () {
          parent.postMessage({ type: 'rowboat:notiz-oeffnen', pfad: r.pfad }, '*');
        });
        bz.appendChild(b);
      });
      wurzel.appendChild(bz);
    }
  }

  try {
    zeichne();
  } catch (fehler) {
    // Nie halb gezeichnet stehen lassen.
    wurzel.textContent = '';
    var meldung = el('div', 'warnung');
    meldung.setAttribute('role', 'alert');
    meldung.appendChild(el('strong', null, 'Die Seite konnte nicht gezeichnet werden. Bitte die Daten im Datenblock prüfen.'));
    meldung.appendChild(el('div', null, String(fehler && fehler.message ? fehler.message : fehler)));
    wurzel.appendChild(meldung);
  }
})();
</script>
</body>
</html>
`;

function htmlEscape(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/** JSON fuer die Einbettung in ein script-Element: `<`, `>`, `&` als \uXXXX (JSON.parse liest sie zurueck). */
function blockJson(daten: unknown): string {
  return JSON.stringify(daten, null, 2)
    .replace(/</g, '\\u003c')
    .replace(/>/g, '\\u003e')
    .replace(/&/g, '\\u0026');
}

function baue(daten: KonzeptDaten, titel: string): string {
  // Funktionen als Ersatz, damit `$` in Titel/Daten nicht als Ersetzungsmuster gilt.
  return SEITE.replace(/\{\{(TITEL|DATEN)\}\}/g, (_m, name: string) => (name === 'TITEL' ? htmlEscape(titel) : blockJson(daten)));
}

export function videoKonzeptSeite(daten: KonzeptDaten, titel: string): string {
  return baue(daten, titel);
}

const GERUEST: KonzeptDaten = {
  art: 'videokonzept',
  bezuege: [],
  video: {
    thema: 'Thema des Videos',
    zielgruppe: 'Zielgruppe',
    minuten: 4,
    hook: 'Der erste Satz des Videos',
    kernaussage: 'Die eine Aussage, die hängen bleiben soll',
    stufen: [{ name: 'Einstieg', szenen: [{ kurz: 'Was in der Szene passiert', bildart: 'slide' }] }],
    abschluss: 'Klarer nächster Schritt',
    auftrag: null,
  },
};

/** Die Vorlage, wie der Agent sie sieht: Titel und Datenblock sind Platzhalter-Beispiele zum Ersetzen. */
export const VIDEOKONZEPT_VORLAGE: string = baue(GERUEST, 'Titel des Konzepts');
