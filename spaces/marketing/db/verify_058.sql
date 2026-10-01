-- Nachweise fuer 058, laeuft nur ueber migration_probe (eine Transaktion + ROLLBACK).
DO $$ BEGIN
  ASSERT marketing.pult_bloecke_fehler('{"root":{"type":"EmailLayout","data":{"childrenIds":["h"],"schriften":{"anzeige":"playfair","text":"poppins"}}},"h":{"type":"Heading","data":{"style":{"fontFamily":"ANZEIGE","letterSpacing":3,"textTransform":"uppercase","lineHeight":1.1},"props":{"text":"x"}}}}'::jsonb) IS NULL, 'neue Felder gueltig';
  ASSERT marketing.pult_bloecke_fehler('{"root":{"type":"EmailLayout","data":{"childrenIds":["h"]}},"h":{"type":"Heading","data":{"style":{"letterSpacing":9},"props":{"text":"x"}}}}'::jsonb) IS NOT NULL, 'Laufweite begrenzt';
  ASSERT marketing.pult_bloecke_fehler('{"root":{"type":"EmailLayout","data":{"childrenIds":["k"]}},"k":{"type":"Container","data":{"style":{"overlay":{"farbe":"#2f4858","deckkraft":80}},"props":{"url":"medien:platzhalter-2x1.png","width":600,"height":300,"childrenIds":[]}}}}'::jsonb) IS NULL, 'Container-Hintergrund gueltig';
  ASSERT marketing._bild_ist_platz('{"type":"Container","data":{"props":{"url":"medien:a.png","width":600,"height":300}}}'::jsonb), 'Container ist Platz';
  ASSERT NOT marketing._bild_ist_platz('{"type":"Image","data":{"props":{"url":"medien:a.png","width":600,"height":300,"grafik":true}}}'::jsonb), 'Grafik ist kein Platz';
  ASSERT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='marketing'
                 AND table_name='newsletter_vorlagen' AND column_name='fuer_alle'), 'fuer_alle da';
END $$;
SELECT 'verify_058 ok' AS ergebnis;
