from conftest import UEBERWACHT, eine


def test_umgebung_steht(db_roh):
    for tabelle in UEBERWACHT:
        schema, name = tabelle.split(".")
        vorhanden = eine(
            db_roh,
            "SELECT count(*) AS n FROM information_schema.tables WHERE table_schema=%s AND table_name=%s",
            (schema, name),
        )
        assert vorhanden["n"] == 1, tabelle
        assert eine(db_roh, f"SELECT count(*) AS n FROM {tabelle}")["n"] == 0
