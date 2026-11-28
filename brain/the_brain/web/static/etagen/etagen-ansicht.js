// Etagen-Ansicht der Entscheidungen (Plan 28.11.2026, Baustufe A): die Seite.
//   Hoehe = Stufe (Etage), Breite = Art des Wegs (Spur), Tiefe = Bereich (Bahn).
// three.js wird erst beim ersten Oeffnen geladen und beim Schliessen wieder freigegeben.
// Gezeichnet wird nur bei Aenderung (Drehen, neue Daten, Auswahl), nie dauernd.
// Ohne three.js oder ohne WebGL bleibt die Liste rechts benutzbar.
(function () {
  'use strict';
  const L = window.EtagenLogik;
  const BASIS = '/static/etagen/';
  const BREITE = 760, TIEFE = 330, ABSTAND = 74, MAX_STRAENGE = 150;
  const FENSTER = [[900, 'letzte 15 min'], [3600, 'letzte Stunde'], [21600, 'letzte 6 Stunden'],
                   [0, 'alle geladenen']];
  const BLICKE = { schraeg: [620, 420, 1250], vorn: [0, 0, 1650], seite: [1650, 0, 0.1], oben: [0, 1650, 0.1] };

  let z = null;          // Zustand der offenen Ansicht
  let letzteAntwort = null;
  let stilDa = false;

  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function stil() {
    if (stilDa) return;
    stilDa = true;
    const el = document.createElement('style');
    el.textContent = `
      .etg { position:absolute; inset:0; display:grid; grid-template-columns:1fr 340px; font:12px/1.4 system-ui,"Segoe UI",sans-serif; color:#e3e8ef; }
      .etg-links { position:relative; min-width:0; overflow:hidden; }
      .etg-buehne, .etg-schilder { position:absolute; inset:0; }
      .etg-schilder { pointer-events:none; overflow:hidden; }
      .etg-schild { position:absolute; white-space:nowrap; transform:translate(-50%,-50%); font-size:10px; color:#9fb0c4; text-shadow:0 0 4px #0b0f17,0 0 4px #0b0f17,0 0 6px #0b0f17; }
      .etg-schild.etage { transform:translate(-100%,-50%); font-size:12px; font-weight:600; color:#e3e8ef; padding-right:12px; text-align:right; }
      .etg-schild.etage small { display:block; font-weight:400; font-size:10px; color:#7f8b9b; }
      .etg-schild.bahn { font-size:11px; color:#ffb199; font-weight:600; }
      .etg-schild.wahl { font-size:11px; color:#fff; background:rgba(11,15,23,.8); padding:1px 5px; border-radius:4px; transform:translate(10px,-50%); }
      .etg-leiste { position:absolute; left:10px; top:8px; right:10px; display:flex; flex-wrap:wrap; gap:6px; align-items:center; }
      .etg button, .etg select { background:#17202e; color:#e3e8ef; border:1px solid #2b3a50; border-radius:6px; padding:3px 8px; font:inherit; cursor:pointer; }
      .etg button.an { border-color:currentColor; background:#1e2b3e; }
      .etg button.aus { opacity:.4; }
      .etg-punkt { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:5px; }
      .etg-seite { background:#111824; border-left:1px solid #223044; padding:12px 14px; overflow:auto; min-height:0; }
      .etg-seite h3 { margin:0 0 8px; font-size:13px; }
      .etg-klein { color:#8894a6; font-size:11px; }
      .etg-zeile { padding:6px 8px; border-radius:6px; cursor:pointer; border:1px solid transparent; }
      .etg-zeile:hover { background:#18222f; border-color:#2b3a50; }
      .etg-seite table { width:100%; border-collapse:collapse; margin-top:8px; }
      .etg-seite td { padding:4px; border-top:1px solid #1f2b3b; vertical-align:top; }
      .etg-seite td.r { text-align:right; white-space:nowrap; color:#b7c2d0; }
      .etg-seite a { color:#7fb7ff; cursor:pointer; }
      .etg-fuss { position:absolute; left:10px; bottom:6px; color:#6f7b8c; font-size:10px; }
      .etg-hinweis { position:absolute; top:50%; left:50%; transform:translate(-50%,-50%); color:#8894a6; text-align:center; }
    `;
    document.head.appendChild(el);
  }

  const geladen = {};
  function skript(name) {
    if (!geladen[name]) {
      geladen[name] = new Promise((ja, nein) => {
        const el = document.createElement('script');
        el.src = BASIS + name; el.onload = ja;
        el.onerror = () => { delete geladen[name]; nein(new Error(name + ' nicht geladen')); };
        document.head.appendChild(el);
      });
    }
    return geladen[name];
  }

  // ── Oeffnen und Schliessen ────────────────────────────────────────────
  async function oeffnen(behaelter) {
    if (z || !behaelter || !L) return;
    stil();
    const wurzel = document.createElement('div');
    wurzel.className = 'etg';
    wurzel.innerHTML =
      '<div class="etg-links"><div class="etg-buehne"></div><div class="etg-schilder"></div>' +
      '<div class="etg-hinweis"></div><div class="etg-leiste"></div>' +
      '<div class="etg-fuss">Höhe = Stufe · Breite = Art des Wegs · Tiefe = Bereich · Ziehen dreht, Rad zoomt, Klick wählt</div></div>' +
      '<div class="etg-seite"></div>';
    behaelter.appendChild(wurzel);
    z = {
      behaelter, wurzel, drei: null,
      buehne: wurzel.querySelector('.etg-buehne'), schilder: wurzel.querySelector('.etg-schilder'),
      hinweis: wurzel.querySelector('.etg-hinweis'), seite: wurzel.querySelector('.etg-seite'),
      gebuendelt: true, seitS: 0, arten: new Set(Object.keys(L.ARTEN)), ausgang: '',
      gruppeKey: null, befehlId: null, signatur: null,
      alle: [], befehle: [], gruppen: [], straenge: [], feste: [], wahl: [], bildOffen: false,
    };
    leiste();
    if (letzteAntwort) daten(letzteAntwort);
    const meins = z;
    try {
      await skript('three.min.js');
      await skript('OrbitControls.js');
      if (z !== meins) return;                       // inzwischen geschlossen
      buehneBauen();
    } catch (e) {
      if (z === meins) z.hinweis.textContent = '3D nicht verfügbar (' + e.message + '). Die Liste rechts zeigt dieselben Daten.';
    }
    if (z === meins) neuZeichnen(true);
  }

  function schliessen() {
    if (!z) return;
    const d = z.drei;
    if (d) {
      d.beobachter.disconnect();
      d.steuer.dispose();
      [d.geruest, d.strangGruppe].forEach(leeren);
      d.renderer.dispose();
      if (d.renderer.forceContextLoss) d.renderer.forceContextLoss();
    }
    z.wurzel.remove();
    z = null;
  }

  function leeren(gruppe) {
    while (gruppe.children.length) {
      const c = gruppe.children.pop();
      if (c.geometry) c.geometry.dispose();
      if (c.material) c.material.dispose();
    }
  }

  function buehneBauen() {
    const T = window.THREE;
    let renderer;
    try { renderer = new T.WebGLRenderer({ antialias: true }); }
    catch (e) { z.hinweis.textContent = '3D nicht verfügbar (kein WebGL). Die Liste rechts zeigt dieselben Daten.'; return; }
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    renderer.setClearColor(0x0b0f17);
    z.buehne.appendChild(renderer.domElement);
    const szene = new T.Scene();
    const kamera = new T.PerspectiveCamera(32, 1, 1, 8000);
    const steuer = new T.OrbitControls(kamera, renderer.domElement);
    steuer.addEventListener('change', anfordern);
    szene.add(new T.AmbientLight(0xffffff, 0.8));
    const licht = new T.DirectionalLight(0xffffff, 0.55); licht.position.set(300, 600, 400); szene.add(licht);
    const geruest = new T.Group(), strangGruppe = new T.Group();
    szene.add(geruest); szene.add(strangGruppe);
    const beobachter = new ResizeObserver(groesse);
    z.drei = { T, renderer, szene, kamera, steuer, geruest, strangGruppe, beobachter,
               strahl: new T.Raycaster(), maus: new T.Vector2(), v: new T.Vector3() };
    beobachter.observe(z.buehne);
    blick('schraeg');
    let unten = null;
    renderer.domElement.addEventListener('pointerdown', e => { unten = [e.clientX, e.clientY]; });
    renderer.domElement.addEventListener('pointerup', e => {
      if (!z || !unten || Math.hypot(e.clientX - unten[0], e.clientY - unten[1]) > 4) return;
      const d = z.drei, r = renderer.domElement.getBoundingClientRect();
      d.maus.set((e.clientX - r.left) / r.width * 2 - 1, -(e.clientY - r.top) / r.height * 2 + 1);
      d.strahl.setFromCamera(d.maus, d.kamera);
      const treffer = d.strahl.intersectObjects(d.strangGruppe.children.filter(m => m.material.opacity > 0.1))[0];
      z.gruppeKey = treffer ? treffer.object.userData.gruppe.key : null;
      z.befehlId = treffer && treffer.object.userData.befehl ? treffer.object.userData.befehl.id : null;
      hervorheben(); seiteZeigen();
    });
    groesse();
  }

  function groesse() {
    if (!z || !z.drei) return;
    const b = z.buehne.clientWidth, h = z.buehne.clientHeight;
    if (!b || !h) return;
    z.drei.renderer.setSize(b, h); z.drei.kamera.aspect = b / h; z.drei.kamera.updateProjectionMatrix();
    anfordern();
  }

  function blick(name) {
    if (!z || !z.drei) return;
    const p = BLICKE[name] || BLICKE.schraeg, d = z.drei;
    d.kamera.position.set(p[0] - 60, p[1], p[2]); d.steuer.target.set(-60, 0, 0); d.steuer.update();
    anfordern();
  }

  // ── Zeichnen nur bei Bedarf ───────────────────────────────────────────
  function anfordern() {
    if (!z || !z.drei || z.bildOffen) return;
    z.bildOffen = true;
    requestAnimationFrame(() => {
      if (!z || !z.drei) return;
      z.bildOffen = false;
      const d = z.drei, b = z.buehne.clientWidth, h = z.buehne.clientHeight;
      d.renderer.render(d.szene, d.kamera);
      z.feste.concat(z.wahl).forEach(s => {
        d.v.copy(s.ort).project(d.kamera);
        s.el.style.left = ((d.v.x + 1) / 2 * b) + 'px'; s.el.style.top = ((1 - d.v.y) / 2 * h) + 'px';
        s.el.style.display = d.v.z < 1 ? '' : 'none';
      });
    });
  }

  function schild(html, ort, klasse, liste) {
    const el = document.createElement('div');
    el.className = 'etg-schild ' + (klasse || ''); el.innerHTML = html; z.schilder.appendChild(el);
    liste.push({ el, ort });
  }

  const hoehe = id => ((L.ETAGEN.length - 1) / 2 - L.ETAGEN.findIndex(e => e.id === id)) * ABSTAND;

  // ── Daten ─────────────────────────────────────────────────────────────
  function daten(antwort) {
    letzteAntwort = antwort;
    if (!z) return;
    z.alle = L.befehleAus(antwort);
    z.textAus = !!(antwort && antwort.text_ausgeblendet);
    const signatur = z.alle.map(b => b.id + ':' + b.ok + ':' + b.dauerMs + ':' + b.bereich).join(',');
    if (signatur !== z.signatur || z.seitS) { z.signatur = signatur; neuZeichnen(false); }
  }

  function neuZeichnen(erzwingen) {
    if (!z) return;
    z.befehle = L.filtern(z.alle, { seitS: z.seitS, arten: z.arten, ausgang: z.ausgang });
    z.gruppen = L.buendeln(z.befehle);
    if (z.gruppeKey && !z.gruppen.some(g => g.key === z.gruppeKey)) z.gruppeKey = null;
    if (z.befehlId && !z.befehle.some(b => b.id === z.befehlId)) z.befehlId = null;
    const zahl = z.wurzel.querySelector('.etg-zahl');
    if (zahl) zahl.textContent = z.befehle.length + ' Befehle · ' + z.gruppen.length + ' verschiedene Wege';
    z.hinweis.textContent = z.drei && !z.befehle.length ? 'Keine Befehle in dieser Auswahl.' : (z.drei ? '' : z.hinweis.textContent);
    if (z.drei) szeneBauen();
    seiteZeigen();
  }

  function szeneBauen() {
    const d = z.drei, T = d.T;
    leeren(d.geruest); leeren(d.strangGruppe);
    z.feste.splice(0).forEach(s => s.el.remove());
    z.straenge = [];
    const spur = L.spuren(z.befehle), bahn = L.bahnen(z.befehle);
    const X = k => (spur[k] || 0) * BREITE * 0.86, Z = k => (bahn[k] || 0) * TIEFE * 0.8;

    const linie = (a, b, farbe, deck) => d.geruest.add(new T.Line(new T.BufferGeometry().setFromPoints([a, b]),
      new T.LineBasicMaterial({ color: farbe, transparent: true, opacity: deck })));
    L.ETAGEN.forEach(e => {
      const y = hoehe(e.id);
      const platte = new T.Mesh(new T.PlaneGeometry(BREITE, TIEFE),
        new T.MeshBasicMaterial({ color: 0x4f6b8f, transparent: true, opacity: 0.06, side: T.DoubleSide, depthWrite: false }));
      platte.rotation.x = -Math.PI / 2; platte.position.y = y; d.geruest.add(platte);
      const rand = new T.LineSegments(new T.EdgesGeometry(new T.PlaneGeometry(BREITE, TIEFE)),
        new T.LineBasicMaterial({ color: 0x3a4d68 }));
      rand.rotation.x = -Math.PI / 2; rand.position.y = y; d.geruest.add(rand);
      schild(esc(e.name) + '<small>' + esc(e.hinweis) + '</small>', new T.Vector3(-BREITE / 2, y, TIEFE / 2), 'etage', z.feste);
    });
    // Spuren: Linie und Name auf der Etage, die entscheidet.
    // Nachbarn stehen abwechselnd hoeher und tiefer, damit sich die Namen nicht ueberdecken.
    Object.keys(spur).sort((a, b) => spur[a] - spur[b]).forEach((k, i) => {
      const teile = k.split('|'), art = teile[0], y = hoehe(art === 'unbekannt' ? 'einordnung' : art);
      linie(new T.Vector3(X(k), y, -TIEFE / 2), new T.Vector3(X(k), y, TIEFE / 2), 0x5d7696, 0.5);
      schild(esc(teile[1]), new T.Vector3(X(k), y - (i % 2 ? 22 : 9), TIEFE / 2 + 6), '', z.feste);
    });
    // Bahnen: Linie und Name auf Eingang und Ergebnis.
    Object.keys(bahn).forEach(k => ['eingang', 'ergebnis'].forEach(id => {
      linie(new T.Vector3(-BREITE / 2, hoehe(id), Z(k)), new T.Vector3(BREITE / 2, hoehe(id), Z(k)), 0xff8a65, 0.22);
      schild(esc(k), new T.Vector3(BREITE / 2 + 34, hoehe(id), Z(k)), 'bahn', z.feste);
    }));

    const punkte = (g, dx, dz) => {
      const x = X(g.art + '|' + g.station) + dx;
      const ziel = g.bereich || g.reiter || L.OHNE_BEREICH, start = g.reiter || L.OHNE_BEREICH;
      return g.weg.map((p, i) => ({ p, ort: new T.Vector3(x, hoehe(p.etage), Z(i === 0 ? start : ziel) + dz) }));
    };
    const kurve = pkt => {
      const liste = [];
      pkt.forEach((q, i) => {
        if (i > 0) { const vor = liste[liste.length - 1];
          liste.push(new T.Vector3(vor.x, vor.y - 14, vor.z)); liste.push(new T.Vector3(q.ort.x, q.ort.y + 14, q.ort.z)); }
        liste.push(q.ort);
      });
      return new T.CatmullRomCurve3(liste, false, 'catmullrom', 0.15);
    };
    const strang = (g, b, radius, dx, dz) => {
      const pkt = punkte(g, dx, dz);
      const rohr = new T.Mesh(new T.TubeGeometry(kurve(pkt), 60, radius, 8, false),
        new T.MeshLambertMaterial({ color: L.ARTEN[g.art].farbe, transparent: true, opacity: 0.9 }));
      rohr.userData = { gruppe: g, befehl: b, pkt, marken: [] };
      pkt.forEach(q => {
        // Etage nur durchlaufen (geprueft, kein Treffer): kleiner dunkler Punkt statt einer Marke.
        const klein = !!q.p.ohneTreffer;
        const m = new T.Mesh(new T.SphereGeometry(klein ? Math.max(1.4, radius * 0.8) : radius + (b ? 1.2 : 3), 12, 10),
          new T.MeshLambertMaterial({ color: klein ? '#5d7696' : (q.p.farbe || '#e6edf5'), transparent: true }));
        m.position.copy(q.ort); m.userData = rohr.userData; d.strangGruppe.add(m); rohr.userData.marken.push(m);
      });
      d.strangGruppe.add(rohr); z.straenge.push(rohr);
    };

    // Wege, die Spur und Zielbahn teilen, ruecken nebeneinander.
    const gezeigt = z.gruppen.slice(0, MAX_STRAENGE), fach = {};
    gezeigt.forEach(g => { const k = g.art + '|' + g.station + '|' + (g.bereich || g.reiter); (fach[k] = fach[k] || []).push(g); });
    Object.keys(fach).forEach(k => fach[k].forEach((g, i) => {
      const n = fach[k].length, schritt = Math.min(15, 70 / n), dx = (i - (n - 1) / 2) * schritt;
      if (z.gebuendelt) strang(g, null, 1.3 + 1.7 * Math.log2(1 + g.befehle.length), dx, 0);
      else g.befehle.forEach((b, j) => strang(g, b, 0.8, dx + Math.cos(j * 2.4) * (2 + j * 0.9), Math.sin(j * 2.4) * (2 + j * 0.9)));
    }));
    hervorheben();
  }

  function gewaehlteGruppe() { return z.gruppeKey ? z.gruppen.find(g => g.key === z.gruppeKey) || null : null; }
  function gewaehlterBefehl() { return z.befehlId ? z.befehle.find(b => b.id === z.befehlId) || null : null; }

  function hervorheben() {
    if (!z || !z.drei) return;
    z.wahl.splice(0).forEach(s => s.el.remove());
    const gruppe = gewaehlteGruppe(), befehl = gewaehlterBefehl();
    let erster = null;
    z.straenge.forEach(m => {
      let an = true;
      if (befehl) an = z.gebuendelt ? m.userData.gruppe.befehle.indexOf(befehl) >= 0 : m.userData.befehl === befehl;
      else if (gruppe) an = m.userData.gruppe === gruppe;
      m.material.opacity = an ? 0.95 : 0.06;
      m.userData.marken.forEach(k => { k.material.opacity = an ? 1 : 0.06; });
      if (an && (befehl || gruppe) && !erster) erster = m;
    });
    if (erster) {
      const weg = befehl ? befehl.weg : erster.userData.gruppe.weg;
      erster.userData.pkt.forEach((q, i) => {
        const p = weg[i] || q.p;
        if (p.ohneTreffer) return;                   // durchlaufene Etagen bleiben ohne Schild
        schild(esc(p.station) + (typeof p.ms === 'number' ? ' · ' + esc(L.dauerText(p.ms)) : ''), q.ort, 'wahl', z.wahl);
      });
    }
    anfordern();
  }

  // ── Leiste oben ───────────────────────────────────────────────────────
  function leiste() {
    const box = z.wurzel.querySelector('.etg-leiste');
    const modus = document.createElement('button');
    const modusText = () => { modus.textContent = z.gebuendelt ? 'gebündelt ✓' : 'jeder Befehl einzeln ✓'; };
    modus.title = 'Gleiche Wege zu einem Strang zusammenlegen oder jeden Befehl einzeln zeigen';
    modus.onclick = () => { z.gebuendelt = !z.gebuendelt; modusText(); neuZeichnen(true); };
    modusText(); box.appendChild(modus);

    const wahl = (eintraege, wert, beiWechsel, titel) => {
      const s = document.createElement('select');
      s.title = titel;
      s.innerHTML = eintraege.map(e => '<option value="' + esc(e[0]) + '">' + esc(e[1]) + '</option>').join('');
      s.value = String(wert); s.onchange = () => beiWechsel(s.value); box.appendChild(s);
    };
    wahl(FENSTER, 0, v => { z.seitS = Number(v); neuZeichnen(true); }, 'Zeitfenster');
    wahl([['schraeg', 'Blick: schräg'], ['vorn', 'Blick: von vorn (Stufen)'], ['seite', 'Blick: von der Seite (Bereiche)'],
          ['oben', 'Blick: von oben (Grundriss)']], 'schraeg', blick, 'Fester Blick');
    wahl([['', 'Ausgang: alle']].concat(Object.keys(L.AUSGAENGE).map(k => [k, 'Ausgang: ' + L.AUSGAENGE[k].name])), '',
         v => { z.ausgang = v; neuZeichnen(true); }, 'Nach Ausgang filtern');

    Object.keys(L.ARTEN).forEach(id => {
      const a = L.ARTEN[id], k = document.createElement('button');
      k.className = 'an'; k.style.color = a.farbe;
      k.innerHTML = '<span class="etg-punkt" style="background:' + a.farbe + '"></span>' + esc(a.name);
      k.onclick = () => { if (z.arten.has(id)) z.arten.delete(id); else z.arten.add(id);
        k.className = z.arten.has(id) ? 'an' : 'aus'; neuZeichnen(true); };
      box.appendChild(k);
    });
    const zahl = document.createElement('span'); zahl.className = 'etg-zahl etg-klein'; box.appendChild(zahl);
  }

  // ── Liste rechts ──────────────────────────────────────────────────────
  function satzText(b) { return b.text ? '„' + esc(b.text) + '“' : '<span class="etg-klein">(Satztext ausgeblendet)</span>'; }
  function vonNach(x) {
    const ziel = x.bereich || '';
    if (!ziel) return x.reiter ? 'aus ' + esc(x.reiter) : 'ohne Bereich';
    return x.reiter && x.reiter !== ziel ? 'von ' + esc(x.reiter) + ' nach ' + esc(ziel) : 'in ' + esc(ziel);
  }

  // Tabelle eines Wegs: je Etage Station, Dauer und ein Balken im Verhaeltnis zur laengsten Stufe.
  function wegTabelle(weg, farbe) {
    const max = Math.max(1, ...weg.filter(p => typeof p.ms === 'number').map(p => p.ms));
    return '<table>' + weg.map(p => {
      const e = L.ETAGEN.find(x => x.id === p.etage);
      const balken = typeof p.ms === 'number'
        ? '<div style="height:4px;border-radius:2px;margin-top:3px;background:' + (p.ohneTreffer ? '#5d7696' : farbe) +
          ';width:' + Math.max(2, Math.round(p.ms / max * 100)) + '%"></div>' : '';
      return '<tr><td' + (p.ohneTreffer ? ' style="opacity:.6"' : '') + '>' + esc(e.name) + '<div class="etg-klein">' +
        esc(p.ohneTreffer ? 'geprüft, kein Treffer' : p.station) + '</div>' + balken + '</td><td class="r">' +
        (typeof p.ms === 'number' ? esc(L.dauerText(p.ms)) : '') + '</td></tr>';
    }).join('') + '</table>';
  }

  function seiteZeigen() {
    if (!z) return;
    const jetzt = Date.now() / 1000, befehl = gewaehlterBefehl(), gruppe = gewaehlteGruppe();
    if (befehl) {
      const farbe = L.ARTEN[befehl.art].farbe;
      z.seite.innerHTML = '<a data-zurueck>← zurück</a><h3 style="margin-top:10px">' + satzText(befehl) + '</h3>' +
        '<div class="etg-klein">' + esc(L.alterText(befehl.ts, jetzt)) + ' · ' + vonNach(befehl) + ' · gesamt ' +
        esc(L.dauerText(befehl.dauerMs)) + ' · <span style="color:' + farbe + '">' + esc(L.ARTEN[befehl.art].name) +
        '</span> · Ausgang: ' + esc(L.AUSGAENGE[befehl.ausgang].name) + '</div>' + wegTabelle(befehl.weg, farbe) +
        (befehl.faehigkeit ? '<p class="etg-klein">Fähigkeit: ' + esc(befehl.faehigkeit) + '</p>' : '') +
        (befehl.nurReiter ? '<p class="etg-klein">Der Bereich ist nur der Reiter; die Antwort nannte kein Ziel.</p>' : '') +
        '<p class="etg-klein">' + (befehl.gemessen
          ? 'Dauer je Stufe vom Gehirn gemessen. Die Prüfung läuft innerhalb der Ausführung und hat keine eigene Zeit.'
          : 'Älterer Satz ohne Stufen-Protokoll: nur die entscheidende Etage ist bekannt, ohne Dauer je Stufe.') + '</p>';
      z.seite.querySelector('[data-zurueck]').onclick = () => { z.befehlId = null; hervorheben(); seiteZeigen(); };
      return;
    }
    const zeigen = gruppe ? [gruppe] : z.gruppen;
    const mehr = !gruppe && z.gruppen.length > MAX_STRAENGE
      ? '<p class="etg-klein">Im Bild stehen die häufigsten ' + MAX_STRAENGE + ' Wege.</p>' : '';
    z.seite.innerHTML = (gruppe ? '<a data-alle>← alle Wege</a>' : '<h3>Wege, häufigste zuerst</h3>') + mehr +
      (zeigen.length ? '' : '<div class="etg-klein">Keine Befehle in dieser Auswahl.</div>') +
      zeigen.map(g => '<div class="etg-zeile" data-g="' + z.gruppen.indexOf(g) + '"><span class="etg-punkt" style="background:' +
        L.ARTEN[g.art].farbe + '"></span><b>' + g.befehle.length + '×</b> ' + g.weg.slice(1).filter(p => !p.ohneTreffer).map(p => esc(p.station)).join(' → ') +
        '<div class="etg-klein">' + esc(L.ARTEN[g.art].name) + ' · ' + vonNach(g) + ' · ' + esc(L.AUSGAENGE[g.ausgang].name) +
        ' · im Mittel ' + esc(L.dauerText(g.mittelMs)) + '</div></div>' +
        (gruppe && g.befehle.some(b => b.gemessen)
          ? '<div class="etg-klein" style="margin-top:8px">Mittlere Dauer je Stufe</div>' + wegTabelle(g.weg, L.ARTEN[g.art].farbe) +
            '<div class="etg-klein" style="margin:10px 0 4px">Befehle in diesem Weg</div>' : '') +
        (gruppe ? g.befehle.map(b => '<div class="etg-zeile" data-b="' + esc(b.id) + '">' + satzText(b) + '<div class="etg-klein">' +
          esc(L.alterText(b.ts, jetzt)) + ' · ' + esc(L.dauerText(b.dauerMs)) + (b.faehigkeit ? ' · ' + esc(b.faehigkeit) : '') +
          '</div></div>').join('') : '')).join('');
    z.seite.querySelectorAll('[data-g]').forEach(el => { el.onclick = () => {
      z.gruppeKey = z.gruppen[Number(el.dataset.g)].key; hervorheben(); seiteZeigen(); }; });
    z.seite.querySelectorAll('[data-b]').forEach(el => { el.onclick = () => { z.befehlId = el.dataset.b; hervorheben(); seiteZeigen(); }; });
    const alle = z.seite.querySelector('[data-alle]');
    if (alle) alle.onclick = () => { z.gruppeKey = null; hervorheben(); seiteZeigen(); };
  }

  window.EtagenAnsicht = { oeffnen, schliessen, daten, offen: () => !!z };
})();
