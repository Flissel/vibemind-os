-- verify_049.sql — Proben fuer 049_formular_gestalt_zahlen.sql.
-- Wie verify_045..048: eine Transaktion, am Ende ROLLBACK, nichts bleibt
-- stehen. Jede verletzte Erwartung bricht mit 'PROBE: ...' ab (ON_ERROR_STOP).
BEGIN;

DO $$
DECLARE v_fehler text;
BEGIN
    -- String-Koordinate im Feld: "8" statt 8.
    v_fehler := marketing._formular_gestalt_fehler('{"seite":{"breite_mm":148,"hoehe_mm":105},
        "felder":[{"name":"kunde","beschriftung":"Kunde","art":"text","quelle":"kunde.name",
                   "platz":{"x":"8","y":20,"breite":60,"hoehe":12}}]}'::jsonb);
    IF v_fehler IS NULL THEN RAISE EXCEPTION 'PROBE: String-Koordinate x="8" ANGENOMMEN'; END IF;
    RAISE NOTICE 'PROBE string-x abgelehnt: %', v_fehler;

    -- String-Seitenmass.
    v_fehler := marketing._formular_gestalt_fehler('{"seite":{"breite_mm":"148","hoehe_mm":105},
        "felder":[{"name":"kunde","beschriftung":"Kunde","art":"text","quelle":"kunde.name",
                   "platz":{"x":8,"y":20,"breite":60,"hoehe":12}}]}'::jsonb);
    IF v_fehler IS NULL THEN RAISE EXCEPTION 'PROBE: String-Seitenmass ANGENOMMEN'; END IF;

    -- String-Koordinate in einem festen Text.
    v_fehler := marketing._formular_gestalt_fehler('{"seite":{"breite_mm":148,"hoehe_mm":105},
        "felder":[{"name":"kunde","beschriftung":"Kunde","art":"text","quelle":"kunde.name",
                   "platz":{"x":8,"y":20,"breite":60,"hoehe":12}}],
        "texte":[{"text":"KARTE","platz":{"x":8,"y":"4","breite":60,"hoehe":8}}]}'::jsonb);
    IF v_fehler IS NULL THEN RAISE EXCEPTION 'PROBE: String-Koordinate im festen Text ANGENOMMEN'; END IF;

    -- groesse mit Einheit.
    v_fehler := marketing._formular_gestalt_fehler('{"seite":{"breite_mm":148,"hoehe_mm":105},
        "felder":[{"name":"kunde","beschriftung":"Kunde","art":"text","quelle":"kunde.name",
                   "platz":{"x":8,"y":20,"breite":60,"hoehe":12}}],
        "texte":[{"text":"KARTE","platz":{"x":8,"y":4,"breite":60,"hoehe":8},"groesse":"12pt"}]}'::jsonb);
    IF v_fehler IS NULL THEN RAISE EXCEPTION 'PROBE: groesse "12pt" ANGENOMMEN'; END IF;

    -- Feldhoehe 5 mm.
    v_fehler := marketing._formular_gestalt_fehler('{"seite":{"breite_mm":148,"hoehe_mm":105},
        "felder":[{"name":"kunde","beschriftung":"Kunde","art":"text","quelle":"kunde.name",
                   "platz":{"x":8,"y":20,"breite":60,"hoehe":5}}]}'::jsonb);
    IF v_fehler IS NULL THEN RAISE EXCEPTION 'PROBE: Feldhoehe 5 ANGENOMMEN'; END IF;
    RAISE NOTICE 'PROBE hoehe-5 abgelehnt: %', v_fehler;

    -- Feldhoehe 6 mm: die Grenze selbst geht durch.
    v_fehler := marketing._formular_gestalt_fehler('{"seite":{"breite_mm":148,"hoehe_mm":105},
        "felder":[{"name":"kunde","beschriftung":"Kunde","art":"text","quelle":"kunde.name",
                   "platz":{"x":8,"y":20,"breite":60,"hoehe":6}}]}'::jsonb);
    IF v_fehler IS NOT NULL THEN RAISE EXCEPTION 'PROBE: Feldhoehe 6 ABGELEHNT: %', v_fehler; END IF;

    -- Gueltige Gestalt im Stil von 046 (mit festem Text und Zahl-groesse) geht weiter durch.
    v_fehler := marketing._formular_gestalt_fehler('{"seite":{"breite_mm":148,"hoehe_mm":105},
        "texte":[{"text":"Terminkarte","platz":{"x":8,"y":4,"breite":80,"hoehe":8},"groesse":12}],
        "felder":[
          {"name":"kunde","beschriftung":"Kunde","art":"text","quelle":"kunde.name","platz":{"x":8,"y":20,"breite":60,"hoehe":12}},
          {"name":"wann","beschriftung":"Datum","art":"datum","quelle":"termin.datum","platz":{"x":80,"y":20,"breite":60,"hoehe":12}},
          {"name":"um","beschriftung":"Uhrzeit","art":"uhrzeit","quelle":"termin.uhrzeit","platz":{"x":80,"y":34,"breite":60,"hoehe":8}},
          {"name":"vorinfo","beschriftung":"Vorinfos","art":"mehrzeilig","quelle":"frei","platz":{"x":8,"y":48,"breite":132,"hoehe":30.5}}]}'::jsonb);
    IF v_fehler IS NOT NULL THEN RAISE EXCEPTION 'PROBE: gueltige 046-Gestalt ABGELEHNT: %', v_fehler; END IF;

    -- Eine 046-Regel bleibt: unerlaubte Quelle wird weiter abgelehnt.
    v_fehler := marketing._formular_gestalt_fehler('{"seite":{"breite_mm":148,"hoehe_mm":105},
        "felder":[{"name":"x","beschriftung":"X","art":"text","quelle":"kunde.foo",
                   "platz":{"x":8,"y":20,"breite":30,"hoehe":12}}]}'::jsonb);
    IF v_fehler IS NULL THEN RAISE EXCEPTION 'PROBE: unerlaubte quelle nach 049 ANGENOMMEN'; END IF;
END $$;

ROLLBACK;
\echo verify_049: alle Proben bestanden
