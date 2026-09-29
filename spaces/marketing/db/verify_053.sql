-- verify_053.sql — Proben fuer 053_newsletter_bloecke.sql. Aendert nichts.
BEGIN;
CREATE TEMP TABLE _gut AS SELECT '{
 "root":{"type":"EmailLayout","data":{"backdropColor":"#0f2422","canvasColor":"#1d3b39","textColor":"#cfe3df","fontFamily":"MODERN_SANS","childrenIds":["b1","b2","b3","b4"]}},
 "b1":{"type":"Heading","data":{"style":{"padding":{"top":24,"bottom":8,"left":24,"right":24}},"props":{"text":"Neuigkeiten","level":"h1"}}},
 "b2":{"type":"Text","data":{"style":{},"props":{"text":"Hallo **Welt** [mehr](https://vibemind.space)","markdown":true}}},
 "b3":{"type":"Image","data":{"style":{},"props":{"url":"medien:logo.png","alt":"Logo","linkHref":"https://vibemind.space"}}},
 "b4":{"type":"ColumnsContainer","data":{"style":{},"props":{"columnsCount":2,"columns":[{"childrenIds":["b5"]},{"childrenIds":["b6"]},{"childrenIds":[]}]}}},
 "b5":{"type":"Button","data":{"style":{},"props":{"text":"Jetzt","url":"https://vibemind.space","buttonBackgroundColor":"#5eead4"}}},
 "b6":{"type":"Spacer","data":{"style":{},"props":{"height":16}}}
}'::jsonb AS d;
DO $$ DECLARE v text; BEGIN
  v := marketing.pult_bloecke_fehler((SELECT d FROM _gut));
  IF v IS NOT NULL THEN RAISE EXCEPTION 'PROBE: gueltiges Dokument abgelehnt: %', v; END IF;
END $$;
-- Text ohne Markdown-Link (Klammern, eckige Klammern) und null-Kinderlisten wie im Editor bleiben gueltig
DO $$ DECLARE v text; BEGIN
  v := marketing.pult_bloecke_fehler(jsonb_set((SELECT d FROM _gut),'{b2,data,props,text}','"Preis [netto] (ab 1.10.) - siehe unten"')
       || '{"c0":{"type":"Container","data":{"style":{"padding":null},"props":{"childrenIds":null}}}}'
       || jsonb_build_object('root', jsonb_set((SELECT d->'root' FROM _gut),'{data,childrenIds}','["b1","b2","b3","b4","c0"]')));
  IF v IS NOT NULL THEN RAISE EXCEPTION 'PROBE: Text ohne Link oder leerer Container abgelehnt: %', v; END IF;
END $$;
DO $$ DECLARE d jsonb; BEGIN
  d := (SELECT g.d FROM _gut g);
  IF marketing.pult_bloecke_fehler(d || '{"b9":{"type":"Html","data":{"props":{"contents":"<b>x</b>"}}}}'
       || jsonb_build_object('root', jsonb_set(d->'root','{data,childrenIds}', (d#>'{root,data,childrenIds}') || '"b9"'))) IS NULL
    THEN RAISE EXCEPTION 'PROBE: Html-Block angenommen'; END IF;
  IF marketing.pult_bloecke_fehler(jsonb_set(d,'{b5,data,props,url}','"javascript:alert(1)"')) IS NULL
    THEN RAISE EXCEPTION 'PROBE: javascript-Link angenommen'; END IF;
  IF marketing.pult_bloecke_fehler(jsonb_set(d,'{b3,data,props,url}','"https://boese.de/x.png"')) IS NULL
    THEN RAISE EXCEPTION 'PROBE: fremde Bildadresse angenommen'; END IF;
  IF marketing.pult_bloecke_fehler(jsonb_set(d,'{b3,data,props,url}','"medien:../geheim.png"')) IS NULL
    THEN RAISE EXCEPTION 'PROBE: Pfad im Bildnamen angenommen'; END IF;
  IF marketing.pult_bloecke_fehler(jsonb_set(d,'{b2,data,props,text}','"[x](http://boese.de)"')) IS NULL
    THEN RAISE EXCEPTION 'PROBE: http-Markdown-Link angenommen'; END IF;
  IF marketing.pult_bloecke_fehler(d || '{"waise":{"type":"Spacer","data":{"props":{"height":4}}}}') IS NULL
    THEN RAISE EXCEPTION 'PROBE: Waisen-Block angenommen'; END IF;
  IF marketing.pult_bloecke_fehler(jsonb_set(d,'{root,data,childrenIds}','["b1","b1","b2","b3","b4"]')) IS NULL
    THEN RAISE EXCEPTION 'PROBE: Mehrfachverweis angenommen'; END IF;
  IF marketing.pult_bloecke_fehler(jsonb_set(d,'{root,data,childrenIds}','["b1","b2","b3","b4","fehlt"]')) IS NULL
    THEN RAISE EXCEPTION 'PROBE: Verweis auf fehlenden Block angenommen'; END IF;
  IF marketing.pult_bloecke_fehler(jsonb_set(d,'{b5,data,props,buttonBackgroundColor}','"red"')) IS NULL
    THEN RAISE EXCEPTION 'PROBE: Farbe red angenommen'; END IF;
  IF marketing.pult_bloecke_fehler('{"root":{"type":"Text","data":{}}}') IS NULL
    THEN RAISE EXCEPTION 'PROBE: Wurzel ohne EmailLayout angenommen'; END IF;
END $$;
-- Tiefe > 4: Container-Kette
DO $$ DECLARE d jsonb := '{"root":{"type":"EmailLayout","data":{"childrenIds":["c1"]}},
 "c1":{"type":"Container","data":{"props":{"childrenIds":["c2"]}}},
 "c2":{"type":"Container","data":{"props":{"childrenIds":["c3"]}}},
 "c3":{"type":"Container","data":{"props":{"childrenIds":["c4"]}}},
 "c4":{"type":"Container","data":{"props":{"childrenIds":["c5"]}}},
 "c5":{"type":"Container","data":{"props":{"childrenIds":[]}}}}';
BEGIN
  IF marketing.pult_bloecke_fehler(d) IS NULL THEN RAISE EXCEPTION 'PROBE: Tiefe 5 angenommen'; END IF;
END $$;
-- Speichern mit Grundlage
DO $$ DECLARE v_i uuid; v_n int; BEGIN
  v_i := marketing.pult_inhalt_aus_vorlage(
           (SELECT name FROM marketing.newsletter_vorlagen WHERE status='freigegeben' ORDER BY name LIMIT 1),
           'Probe', 'vibemind');
  IF (SELECT format FROM marketing.inhalt_fassungen WHERE inhalt=v_i AND fassung=1) <> 'bloecke' THEN
    RAISE EXCEPTION 'PROBE: aus Vorlage nicht im Blockformat'; END IF;
  v_n := marketing.pult_bloecke_speichern(v_i, 1, 'Betreff', 'Vorab', (SELECT d FROM _gut), 'betreiber', false);
  IF v_n <> 2 THEN RAISE EXCEPTION 'PROBE: erwartet Fassung 2, war %', v_n; END IF;
  BEGIN
    PERFORM marketing.pult_bloecke_speichern(v_i, 1, 'B', '', (SELECT d FROM _gut), 'agent', false);
    RAISE EXCEPTION 'PROBE: veraltete Grundlage angenommen';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
    IF SQLERRM NOT LIKE 'Inzwischen gibt es Fassung 2%' THEN RAISE EXCEPTION 'PROBE: falscher Grund: %', SQLERRM; END IF;
  END;
  v_n := marketing.pult_bloecke_speichern(v_i, 1, 'B', '', (SELECT d FROM _gut), 'betreiber', true);
  IF v_n <> 3 THEN RAISE EXCEPTION 'PROBE: als Kopie nicht Fassung 3'; END IF;
  IF (SELECT felder->>'betreff' FROM marketing.inhalt_fassungen WHERE inhalt=v_i AND fassung=2) <> 'Betreff' THEN
    RAISE EXCEPTION 'PROBE: Betreff nicht in felder gespiegelt'; END IF;
  BEGIN
    PERFORM marketing.pult_bloecke_speichern(v_i, 3, 'B', '', jsonb_set((SELECT d FROM _gut),'{b3,data,props,url}','"https://x.de/a.png"'), 'agent', false);
    RAISE EXCEPTION 'PROBE: ungueltiges Dokument gespeichert';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
  END;
END $$;
-- Posts bekommen keine Bloecke
DO $$ DECLARE v_i uuid; BEGIN
  SELECT id INTO v_i FROM marketing.inhalte WHERE art='post' AND status='entwurf' LIMIT 1;
  PERFORM marketing.pult_bloecke_speichern(v_i,
    (SELECT max(fassung) FROM marketing.inhalt_fassungen WHERE inhalt=v_i), 'x', '', (SELECT d FROM _gut), 'betreiber', false);
  RAISE EXCEPTION 'PROBE: Bloecke fuer einen Post angenommen';
EXCEPTION WHEN raise_exception THEN
  IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
END $$;
-- Uebernahme: jeder Newsletter-Entwurf hat als neueste Fassung eine Block-Fassung
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM marketing.inhalte i WHERE i.art='newsletter' AND i.status='entwurf'
             AND (SELECT format FROM marketing.inhalt_fassungen f WHERE f.inhalt=i.id
                  ORDER BY fassung DESC LIMIT 1) <> 'bloecke') THEN
    RAISE EXCEPTION 'PROBE: Newsletter-Entwurf nicht uebernommen'; END IF;
  IF EXISTS (SELECT 1 FROM marketing.inhalt_fassungen WHERE format='bloecke'
             AND marketing.pult_bloecke_fehler(bloecke) IS NOT NULL) THEN
    RAISE EXCEPTION 'PROBE: uebernommenes Dokument ungueltig'; END IF;
END $$;
ROLLBACK;
