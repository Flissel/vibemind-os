-- Nachweise fuer 068, laeuft nur ueber migration_probe (eine Transaktion + ROLLBACK).

DO $$ DECLARE
  basis jsonb := '{"root":{"type":"EmailLayout","data":{"childrenIds":["t"],"schriften":{"anzeige":"playfair","text":"poppins"}}},'
                 '"t":{"type":"Text","data":{"style":{"fontFamily":"HAND"},"props":{"text":"Deine Lena"}}}}';
BEGIN
  ASSERT to_regprocedure('marketing._pult_bloecke_fehler_060(jsonb)') IS NOT NULL, 'Huelle 060 umbenannt';
  ASSERT (SELECT provolatile FROM pg_proc WHERE oid = 'marketing.pult_bloecke_fehler(jsonb)'::regprocedure) = 'i',
         'neue Huelle ist IMMUTABLE';
  ASSERT (SELECT count(*) FROM pg_constraint
           WHERE conname IN ('inhalt_fassungen_bloecke_gueltig','newsletter_vorlagen_bloecke_gueltig',
                             'newsletter_vorlagen_fassungen_bloecke_gueltig')
             AND pg_get_constraintdef(oid) LIKE '%marketing.pult_bloecke_fehler(bloecke)%') = 3,
         'CHECK-Regeln nutzen die neue Huelle';
  -- HAND ist erlaubt, fremde Rollen weiter nicht
  ASSERT marketing.pult_bloecke_fehler(basis) IS NULL, 'HAND im Textblock erlaubt';
  ASSERT marketing.pult_bloecke_fehler(jsonb_set(basis, '{t,data,style,fontFamily}', '"KRITZEL"')) IS NOT NULL,
         'unbekannte Rolle bleibt verboten';
  -- caveat bleibt aus dem Vorlagen-Schriftpaar draussen
  ASSERT marketing.pult_bloecke_fehler(jsonb_set(basis, '{root,data,schriften,anzeige}', '"caveat"')) IS NOT NULL,
         'caveat nicht als Schriftpaar';
  -- ein Text, der das Muster enthaelt, wird nicht umgedeutet (Anfuehrungszeichen sind escaped)
  ASSERT marketing.pult_bloecke_fehler(jsonb_set(basis, '{t,data,props,text}', '"\"fontFamily\": \"HAND\""')) IS NULL,
         'Muster im Text ist harmlos';
  -- Bestand bleibt gueltig: jede neueste Bloecke-Fassung besteht weiter
  -- Seiten-Vorlagen (082, bloecke NULL) haben keine Bloecke; vor 082 gibt es die Spalte format noch nicht
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.newsletter_vorlagen v WHERE coalesce(to_jsonb(v)->>'format', 'bloecke') = 'bloecke'
                      AND marketing.pult_bloecke_fehler(bloecke) IS NOT NULL),
         'alle Vorlagen weiter gueltig';
END $$;
