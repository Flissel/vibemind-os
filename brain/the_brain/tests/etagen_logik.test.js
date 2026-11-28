// Logik der Etagen-Ansicht (web/static/etagen/etagen-logik.js). Lauf: node --test <diese Datei>.
// Alle Saetze und Namen sind erfunden.
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const L = require(path.join(__dirname, '..', 'web', 'static', 'etagen', 'etagen-logik.js'));

function antwort(eintraege) {
  const nodes = [], edges = [];
  const kante = (type, source, target) => edges.push({ data: { id: type + source + target, type, source, target } });
  eintraege.forEach((e, i) => {
    const eid = 'ent_' + i, sid = 'satz:' + i;
    nodes.push({ data: { id: sid, type: 'satz', intent: e.text || '', reiter: e.reiter || '', ts: e.ts || 100 } });
    nodes.push({ data: Object.assign({ id: eid, type: 'entscheidung', reiter: e.reiter || '', ts: e.ts || 100 }, e.ent) });
    kante('entschieden_durch', sid, eid);
    if (e.bereich) {
      const bid = 'bereich:' + e.bereich;
      if (!nodes.some(n => n.data.id === bid)) nodes.push({ data: { id: bid, type: 'bereich', label: e.bereich } });
      kante('waehlt', eid, bid);
    }
    if (e.hops) {
      const pid = 'plan_' + i;
      nodes.push({ data: { id: pid, type: 'plan' } });
      kante('fuehrt_zu', eid, pid);
      e.hops.forEach((h, k) => {
        const hid = pid + ':s' + k;
        nodes.push({ data: { id: hid, type: 'hop', elapsed_s: h.s } });
        kante('has_hop', pid, hid);
        if (h.freigabe) {
          nodes.push({ data: { id: hid + '::t', type: 'toolcall', approval_status: h.freigabe } });
          kante('used_tool', hid, hid + '::t');
        }
      });
    }
  });
  return { nodes, edges };
}

const MUSTER = { etage: 'muster', stufe: 'ideen-fest', weg: 'plan-executor', ausgang: 'erledigt', ok: true,
                 faehigkeit: 'notiz_anlegen', schritte: 1, dauer_ms: 800 };

test('ein Muster-Befehl geht durch Eingang, Muster, Ausfuehrung, Ergebnis', () => {
  const [b] = L.befehleAus(antwort([{ text: 'Leg eine Notiz zu Thema A an', reiter: 'ideas', bereich: 'ideas', ent: MUSTER }]));
  assert.equal(b.art, 'muster');
  assert.equal(b.text, 'Leg eine Notiz zu Thema A an');
  assert.deepEqual(b.weg.map(p => p.etage), ['eingang', 'muster', 'ausfuehrung', 'ergebnis']);
  assert.deepEqual(b.weg.map(p => p.station), ['ideas', 'ideen-fest', 'notiz_anlegen', 'ideas']);
  assert.equal(b.dauerMs, 800);
});

test('Hoeflichkeit beruehrt keine Ausfuehrung und endet ohne Bereich beim Ausgang', () => {
  const [b] = L.befehleAus(antwort([{ reiter: 'ideas', ent: { etage: 'gespraech', stufe: 'hoeflichkeit', weg: 'hoeflichkeit', ausgang: 'antwort' } }]));
  assert.deepEqual(b.weg.map(p => p.etage), ['eingang', 'gespraech', 'ergebnis']);
  assert.equal(b.weg[2].station, 'Antwort');
});

test('Plan mit Schritten: Dauer der Ausfuehrung aus den Hops, Freigabe auf der Etage Pruefung', () => {
  const [b] = L.befehleAus(antwort([{ reiter: 'ideas', bereich: 'sales', hops: [{ s: 1.5 }, { s: 0.5, freigabe: 'requested' }],
    ent: { etage: 'planung', stufe: 'planer', weg: 'plan-executor', ausgang: 'erledigt', ok: true, schritte: 2, faehigkeit: 'entwurf' } }]));
  assert.deepEqual(b.weg.map(p => p.etage), ['eingang', 'planung', 'ausfuehrung', 'pruefung', 'ergebnis']);
  assert.equal(b.weg[2].station, '2 Schritte');
  assert.equal(b.weg[2].ms, 2000);
  assert.equal(b.weg[3].station, 'freigabe: requested');
});

test('unbekannte Etage und fehlender Ausgang fallen ehrlich zurueck', () => {
  const [b] = L.befehleAus(antwort([{ ent: { etage: 'neu-und-fremd', stufe: 'irgendwas', ok: false } }]));
  assert.equal(b.art, 'unbekannt');
  assert.equal(b.ausgang, 'fehler');
  assert.equal(b.weg[0].station, 'ohne Reiter');
});

test('ohne Satztext (Antwort ausserhalb der Ansicht) bleibt der Text leer', () => {
  const a = antwort([{ reiter: 'ideas', ent: MUSTER }]);
  a.nodes.forEach(n => { delete n.data.intent; });
  assert.equal(L.befehleAus(a)[0].text, '');
});

test('buendeln legt gleiche Wege zusammen, auch bei verschiedener Faehigkeit', () => {
  const liste = L.befehleAus(antwort([
    { reiter: 'ideas', bereich: 'ideas', ent: MUSTER },
    { reiter: 'ideas', bereich: 'ideas', ent: Object.assign({}, MUSTER, { faehigkeit: 'liste_lesen', dauer_ms: 400 }) },
    { reiter: 'sales', bereich: 'ideas', ent: MUSTER },
    { reiter: 'ideas', bereich: 'ideas', ent: Object.assign({}, MUSTER, { ok: false, ausgang: 'fehler' }) },
  ]));
  const gruppen = L.buendeln(liste);
  assert.equal(gruppen.length, 3);
  assert.equal(gruppen[0].befehle.length, 2);
  assert.equal(gruppen[0].mittelMs, 600);
  assert.equal(gruppen[0].weg.find(p => p.etage === 'ausfuehrung').station, '2 verschiedene');
  // Der Weg des Buendels ist eine Kopie: die Befehle behalten ihre eigene Station.
  assert.equal(liste.find(b => b.faehigkeit === 'liste_lesen').weg[2].station, 'liste_lesen');
});

test('filtern nach Zeitfenster, Art und Ausgang', () => {
  const liste = L.befehleAus(antwort([
    { ts: 1000, ent: MUSTER },
    { ts: 400, ent: MUSTER },
    { ts: 990, ent: { etage: 'planung', stufe: 'planer', ausgang: 'fehler', ok: false } },
  ]));
  assert.equal(L.filtern(liste, { seitS: 100, jetztS: 1000 }).length, 2);
  assert.equal(L.filtern(liste, { seitS: 0 }).length, 3);
  assert.equal(L.filtern(liste, { arten: new Set(['planung']) }).length, 1);
  assert.equal(L.filtern(liste, { ausgang: 'fehler' }).length, 1);
});

test('spuren: Gespraech links, Muster in der Mitte, Reasoning rechts; bekannte Reihenfolge', () => {
  const liste = L.befehleAus(antwort([
    { ent: { etage: 'planung', stufe: 'planer' } },
    { ent: { etage: 'muster', stufe: 'ideen-fest' } },
    { ent: { etage: 'muster', stufe: 'lesesatz-fest' } },
    { ent: { etage: 'gespraech', stufe: 'hoeflichkeit' } },
  ]));
  const s = L.spuren(liste);
  assert.ok(s['gespraech|hoeflichkeit'] < s['muster|lesesatz-fest']);
  assert.ok(s['muster|lesesatz-fest'] < s['muster|ideen-fest']);
  assert.ok(s['muster|ideen-fest'] < s['planung|planer']);
  assert.equal(s['gespraech|hoeflichkeit'], -0.5);
  assert.equal(s['planung|planer'], 0.5);
});

test('bahnen: jeder Reiter und jeder Zielbereich hat eine Bahn', () => {
  const liste = L.befehleAus(antwort([
    { reiter: 'ideas', bereich: 'orders', ent: MUSTER },
    { reiter: 'ideas', bereich: 'ideas', ent: MUSTER },
    { ent: { etage: 'gespraech', stufe: 'hoeflichkeit' } },
  ]));
  const b = L.bahnen(liste);
  assert.deepEqual(Object.keys(b).sort(), ['ideas', 'orders', L.OHNE_BEREICH].sort());
  assert.equal(b.ideas, 0.5);                       // haeufigster Bereich vorn
  assert.equal(b[L.OHNE_BEREICH], -0.5);            // ohne Bereich immer hinten
});

test('eine einzige Spur oder Bahn liegt in der Mitte', () => {
  const liste = L.befehleAus(antwort([{ reiter: 'ideas', bereich: 'ideas', ent: MUSTER }]));
  assert.equal(L.spuren(liste)['muster|ideen-fest'], 0);
  assert.equal(L.bahnen(liste).ideas, 0);
});

test('Texte fuer Dauer und Alter', () => {
  assert.equal(L.dauerText(850), '850 ms');
  assert.equal(L.dauerText(2500), '2,5 s');
  assert.equal(L.dauerText(null), '–');
  assert.equal(L.alterText(940, 1000), 'vor 1 min');
});
