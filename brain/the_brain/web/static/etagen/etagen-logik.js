// Etagen-Ansicht der Entscheidungen (Plan 28.11.2026, Baustufe A): reine Logik, ohne Seite.
// Macht aus der Antwort von /api/decision_graph/query eine Liste von Befehlen mit ihrem Weg
// durch die Etagen und buendelt gleiche Wege. Laeuft im Browser und unter node (Tests).
(function (wurzel, bauen) {
  if (typeof module === 'object' && module.exports) module.exports = bauen();
  else wurzel.EtagenLogik = bauen();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const ETAGEN = [
    { id: 'eingang',     name: '1 Eingang',     hinweis: 'Satz kommt an, Reiter' },
    { id: 'gespraech',   name: '2 Gespräch',    hinweis: 'Höflichkeit, Bestätigung, Rückfrage' },
    { id: 'muster',      name: '3 Muster',      hinweis: 'fester Weg per Regex, ohne Modell' },
    { id: 'einordnung',  name: '4 Einordnung',  hinweis: 'Klassifikator und Schwelle' },
    { id: 'planung',     name: '5 Planung',     hinweis: 'Reasoning mit Modell' },
    { id: 'ausfuehrung', name: '6 Ausführung',  hinweis: 'Schritte und Werkzeuge' },
    { id: 'pruefung',    name: '7 Prüfung',     hinweis: 'Validator, Freigabe' },
    { id: 'ergebnis',    name: '8 Ergebnis',    hinweis: 'Zielbereich und Ausgang' },
  ];

  // Art = die Etage, die entschieden hat. Reihenfolge = Reihenfolge der Spuren von links nach rechts.
  const ARTEN = {
    gespraech:  { name: 'Gespräch',           farbe: '#90a4ae' },
    muster:     { name: 'Muster (Regex)',     farbe: '#26c6da' },
    einordnung: { name: 'Einordnung',         farbe: '#ffa726' },
    planung:    { name: 'Reasoning (Modell)', farbe: '#ba68c8' },
    unbekannt:  { name: 'unbekannter Weg',    farbe: '#78909c' },
  };

  const AUSGAENGE = {
    erledigt:   { name: 'erledigt',   farbe: '#66bb6a' },
    antwort:    { name: 'Antwort',    farbe: '#b0bec5' },
    angenommen: { name: 'angenommen', farbe: '#4fc3f7' },
    rueckfrage: { name: 'Rückfrage',  farbe: '#ffa726' },
    absage:     { name: 'Absage',     farbe: '#ff8a65' },
    fehler:     { name: 'Fehler',     farbe: '#ef5350' },
  };

  const FREIGABE_FARBE = { approved: '#66bb6a', approve: '#66bb6a', requested: '#ffd54f',
                           denied: '#ef5350', deny: '#ef5350' };
  const OHNE_BEREICH = '–';

  // Feste Reihenfolge bekannter Stationen je Art; unbekannte kommen alphabetisch dahinter.
  const STATIONEN = {
    gespraech: ['hoeflichkeit', 'abgelehnt', 'ideen-vorschlag', 'ideen-rueckfrage', 'ideen', 'sales-rueckfrage',
                'sales', 'coding-rueckfrage', 'coding', 'recherche-rueckfrage', 'rueckfrage', 'hinweis', 'angenommen'],
    muster: ['lesesatz-fest', 'lesen-fehlt', 'ideen-fest', 'ideen-lokal', 'abkuerzung-regex', 'sales-fest',
             'desktop-fest', 'recherche-fest'],
    einordnung: ['meta-reject', 'nicht-verstanden', 'kein-weg', 'no-plan'],
    planung: ['easy-chat', 'chat', 'planer', 'plan-executor', 'som-planner', 'som-team'],
    unbekannt: [],
  };

  function zahl(wert) { return typeof wert === 'number' && isFinite(wert) ? wert : null; }

  const KEIN_TREFFER = 'kein-treffer';
  const PRUEF_FARBE = { 'validator-ok': '#66bb6a', 'freigabe-ok': '#66bb6a', 'validator-offen': '#ffd54f',
                        'freigabe-offen': '#ffd54f', 'validator-stopp': '#ef5350', 'freigabe-nein': '#ef5350' };

  // Der Weg aus dem Stufen-Protokoll des Gehirns (drei gleich lange Listen), oder null, wenn es
  // fehlt oder nicht passt. Dauer -1 heisst "nicht gemessen" und wird zu null.
  function wegAusProtokoll(e, ausgang) {
    const et = e.stufen_etage, st = e.stufen_station, ms = e.stufen_ms;
    if (!Array.isArray(et) || !Array.isArray(st) || !Array.isArray(ms)) return null;
    if (et.length < 2 || et.length !== st.length || et.length !== ms.length) return null;
    const bekannt = ETAGEN.map(x => x.id);
    let letzte = -1;
    const weg = [];
    for (let i = 0; i < et.length; i++) {
      const stelle = bekannt.indexOf(et[i]);
      if (stelle <= letzte) return null;            // unbekannte Etage oder falsche Reihenfolge
      letzte = stelle;
      const punkt = { etage: et[i], station: String(st[i] || '?'), ms: zahl(ms[i]) !== null && ms[i] >= 0 ? ms[i] : null };
      if (punkt.station === KEIN_TREFFER) punkt.ohneTreffer = true;
      if (punkt.etage === 'pruefung') punkt.farbe = PRUEF_FARBE[punkt.station] || '#ffd54f';
      if (punkt.etage === 'ergebnis') punkt.farbe = AUSGAENGE[ausgang].farbe;
      weg.push(punkt);
    }
    if (weg[0].etage !== 'eingang' || weg[weg.length - 1].etage !== 'ergebnis') return null;
    return weg;
  }

  // Befehle aus der Graph-Antwort. Jeder Befehl: Kopf-Daten und `weg` = Liste {etage, station, ms?, farbe?}.
  function befehleAus(antwort) {
    const knoten = {};
    ((antwort && antwort.nodes) || []).forEach(n => { if (n && n.data) knoten[n.data.id] = n.data; });
    const satzZu = {}, bereichZu = {}, planZu = {}, hopsZu = {}, werkzeugeZu = {};
    ((antwort && antwort.edges) || []).forEach(k => {
      const d = (k && k.data) || {};
      if (d.type === 'entschieden_durch') satzZu[d.target] = d.source;
      else if (d.type === 'waehlt') bereichZu[d.source] = d.target;
      else if (d.type === 'fuehrt_zu') planZu[d.source] = d.target;
      else if (d.type === 'has_hop') (hopsZu[d.source] = hopsZu[d.source] || []).push(d.target);
      else if (d.type === 'used_tool') (werkzeugeZu[d.source] = werkzeugeZu[d.source] || []).push(d.target);
    });
    const liste = [];
    Object.keys(knoten).forEach(id => {
      const e = knoten[id];
      if (e.type !== 'entscheidung') return;
      const satz = knoten[satzZu[id]] || {};
      const bereichKnoten = knoten[bereichZu[id]];
      const bereich = bereichKnoten ? String(bereichKnoten.label || bereichKnoten.id || '') : '';
      const reiter = String(e.reiter || satz.reiter || '');
      const etage = ARTEN[e.etage] ? e.etage : 'unbekannt';
      const station = String(e.stufe || e.weg || 'unbekannt');
      const ausgang = AUSGAENGE[e.ausgang] ? e.ausgang : (e.ok === false ? 'fehler' : e.ok === true ? 'erledigt' : 'antwort');
      const schritte = zahl(e.schritte) || 0;
      const faehigkeit = String(e.faehigkeit || '');

      // Schritte und Freigaben des Plans, soweit die Antwort sie mitbringt.
      const hops = (hopsZu[planZu[id]] || []).map(h => knoten[h]).filter(Boolean);
      const hopMs = hops.reduce((s, h) => s + (zahl(h.elapsed_s) || 0) * 1000, 0);
      let freigabe = '';
      hops.forEach(h => (werkzeugeZu[h.id] || []).forEach(w => {
        const status = knoten[w] && String(knoten[w].approval_status || '');
        if (status && status !== 'none' && (!freigabe || status === 'denied' || status === 'deny')) freigabe = status;
      }));

      // Baustufe B: das Gehirn liefert den Weg mit Dauer je Stufe. Aeltere Saetze haben ihn nicht;
      // fuer sie wird der Weg wie in Baustufe A aus Stufe, Faehigkeit und Schritten gebaut.
      let weg = wegAusProtokoll(e, ausgang), gemessen = !!weg;
      if (!weg) {
        weg = [{ etage: 'eingang', station: reiter || 'ohne Reiter' }];
        weg.push({ etage: etage === 'unbekannt' ? 'einordnung' : etage, station });
        if (faehigkeit || schritte > 0) {
          weg.push({ etage: 'ausfuehrung', station: schritte > 1 ? schritte + ' Schritte' : (faehigkeit || '1 Schritt'),
                     ms: hops.length ? Math.round(hopMs) : null });
        }
        if (freigabe) weg.push({ etage: 'pruefung', station: 'freigabe: ' + freigabe, farbe: FREIGABE_FARBE[freigabe] || '#ffd54f' });
        weg.push({ etage: 'ergebnis', station: bereich ? bereich : AUSGAENGE[ausgang].name, farbe: AUSGAENGE[ausgang].farbe });
      }

      liste.push({
        gemessen,
        id, ts: zahl(e.ts) || zahl(satz.ts) || 0, reiter, bereich, art: etage, station, ausgang,
        text: typeof satz.intent === 'string' ? satz.intent : '',
        faehigkeit, schritte, ok: typeof e.ok === 'boolean' ? e.ok : null,
        dauerMs: zahl(e.dauer_ms), nurReiter: e.bereich_quelle === 'reiter' || e.bereich_quelle === 'keiner',
        freigabe, weg,
      });
    });
    liste.sort((a, b) => b.ts - a.ts);
    return liste;
  }

  // filter: { seitS: Sekunden oder 0 fuer alle, jetztS, arten: Set oder null, ausgang: Kennung oder '' }
  function filtern(befehle, filter) {
    const f = filter || {};
    const grenze = f.seitS ? (f.jetztS || Date.now() / 1000) - f.seitS : null;
    return befehle.filter(b =>
      (grenze === null || b.ts >= grenze) &&
      (!f.arten || f.arten.has(b.art)) &&
      (!f.ausgang || b.ausgang === f.ausgang));
  }

  // Gleiche Wege zusammenlegen. Die Faehigkeit gehoert nicht zum Schluessel: ein Muster mit zehn
  // Faehigkeiten bleibt ein Strang, die Faehigkeiten stehen in der Liste der Befehle.
  function schluessel(b) {
    // Die Folge der Etagen gehoert dazu: ein Buendel hat einen gemeinsamen Weg durch das Haus.
    const pruefung = b.weg.find(p => p.etage === 'pruefung');
    return [b.reiter, b.art, b.station, b.weg.map(p => p.etage).join('>'),
            pruefung ? pruefung.station : '', b.ausgang, b.bereich].join('|');
  }
  function buendeln(befehle) {
    const gruppen = new Map();
    befehle.forEach(b => {
      const key = schluessel(b);
      let g = gruppen.get(key);
      if (!g) {
        g = { key, art: b.art, station: b.station, reiter: b.reiter, bereich: b.bereich, ausgang: b.ausgang,
              weg: b.weg.map(p => Object.assign({}, p)), befehle: [] };
        gruppen.set(key, g);
      }
      g.befehle.push(b);
    });
    const out = Array.from(gruppen.values());
    out.forEach(g => {
      // Die Station der Ausfuehrung nennt im Buendel die Faehigkeit nur, wenn alle dieselbe haben.
      const stelle = g.weg.find(p => p.etage === 'ausfuehrung');
      if (stelle) {
        const namen = Array.from(new Set(g.befehle.map(b => (b.weg.find(p => p.etage === 'ausfuehrung') || {}).station)));
        stelle.station = namen.length === 1 ? namen[0] : namen.length + ' verschiedene';
        stelle.ms = null;
      }
      // Je Stufe: mittlere Dauer ueber die Befehle, die dort gemessen sind; Station nur, wenn alle
      // dieselbe haben (z. B. die Klasse der Einordnung kann je Satz anders sein).
      g.weg.forEach((p, i) => {
        if (p.etage === 'ausfuehrung') { const ms = g.befehle.map(b => b.weg[i] && b.weg[i].ms).filter(x => typeof x === 'number');
          p.ms = ms.length ? Math.round(ms.reduce((s, x) => s + x, 0) / ms.length) : null; return; }
        const namen = Array.from(new Set(g.befehle.map(b => b.weg[i] && b.weg[i].station)));
        if (namen.length > 1) p.station = namen.length + ' verschiedene';
        const ms = g.befehle.map(b => b.weg[i] && b.weg[i].ms).filter(x => typeof x === 'number');
        p.ms = ms.length ? Math.round(ms.reduce((s, x) => s + x, 0) / ms.length) : null;
      });
      const mit = g.befehle.filter(b => b.dauerMs !== null);
      g.mittelMs = mit.length ? Math.round(mit.reduce((s, b) => s + b.dauerMs, 0) / mit.length) : null;
    });
    out.sort((a, b) => b.befehle.length - a.befehle.length || (a.key < b.key ? -1 : 1));
    return out;
  }

  // Spuren (Breite): je entscheidender Station ein Platz zwischen -0.5 und 0.5, nach Art gruppiert.
  function spuren(befehle) {
    const jeArt = {};
    befehle.forEach(b => { (jeArt[b.art] = jeArt[b.art] || new Set()).add(b.station); });
    const reihe = [];
    Object.keys(ARTEN).forEach(art => {
      if (!jeArt[art]) return;
      const fest = STATIONEN[art] || [];
      const namen = Array.from(jeArt[art]).sort((a, b) => {
        const ia = fest.indexOf(a), ib = fest.indexOf(b);
        return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib) || (a < b ? -1 : a > b ? 1 : 0);
      });
      if (reihe.length) reihe.push(null);            // Luecke zwischen zwei Arten
      namen.forEach(n => reihe.push({ art, station: n }));
    });
    const out = {};
    const n = reihe.length;
    reihe.forEach((s, i) => { if (s) out[s.art + '|' + s.station] = n <= 1 ? 0 : i / (n - 1) - 0.5; });
    return out;
  }

  // Bahnen (Tiefe): je Bereich ein Platz zwischen -0.5 und 0.5; haeufige Bereiche vorn.
  function bahnen(befehle) {
    const anzahl = {};
    befehle.forEach(b => [b.reiter || OHNE_BEREICH, b.bereich || b.reiter || OHNE_BEREICH].forEach(k => {
      anzahl[k] = (anzahl[k] || 0) + 1; }));
    const namen = Object.keys(anzahl).sort((a, b) =>
      (a === OHNE_BEREICH) - (b === OHNE_BEREICH) || anzahl[b] - anzahl[a] || (a < b ? -1 : 1));
    const out = {};
    namen.forEach((k, i) => { out[k] = namen.length <= 1 ? 0 : 0.5 - i / (namen.length - 1); });
    return out;
  }

  function dauerText(ms) {
    if (typeof ms !== 'number') return '–';
    return ms < 1000 ? Math.round(ms) + ' ms' : (ms / 1000).toFixed(1).replace('.', ',') + ' s';
  }
  function alterText(ts, jetztS) {
    if (!ts) return '';
    const s = Math.max(0, Math.round((jetztS || Date.now() / 1000) - ts));
    if (s < 60) return 'vor ' + s + ' s';
    if (s < 3600) return 'vor ' + Math.floor(s / 60) + ' min';
    if (s < 86400) return 'vor ' + Math.floor(s / 3600) + ' h';
    return 'vor ' + Math.floor(s / 86400) + ' T';
  }

  return { ETAGEN, ARTEN, AUSGAENGE, OHNE_BEREICH, befehleAus, filtern, buendeln, spuren, bahnen,
           dauerText, alterText };
});
