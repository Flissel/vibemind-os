from spaces.marketing.scripts import migration_probe


def test_klammern_raus_eine_transaktion_rollback(tmp_path):
    a = tmp_path / "a.sql"
    a.write_text("BEGIN;\nCREATE TABLE x();\nCREATE FUNCTION f() RETURNS int LANGUAGE plpgsql AS $$\nBEGIN\n RETURN 1;\nEND $$;\nCOMMIT;\n", encoding="utf-8")
    b = tmp_path / "b.sql"
    b.write_text("begin;\nSELECT 1;\nROLLBACK;\n", encoding="utf-8")
    sql = migration_probe.zusammensetzen([str(a), str(b)])
    assert sql.startswith("BEGIN;\n") and sql.rstrip().endswith("ROLLBACK;")
    assert sql.count("COMMIT") == 0 and sql.upper().count("BEGIN;") == 1
    assert "BEGIN\n RETURN 1;" in sql                  # plpgsql-BEGIN ohne Semikolon bleibt


def test_main_schickt_streng_an_psql(monkeypatch, tmp_path):
    a = tmp_path / "a.sql"
    a.write_text("SELECT 1;\n", encoding="utf-8")
    gesehen = {}

    def falsch(sql, container, streng=False):
        gesehen.update(sql=sql, streng=streng)
        return "ok"
    monkeypatch.setattr(migration_probe._db, "_run_psql", falsch)
    assert migration_probe.main([str(a)]) == 0
    assert gesehen["streng"] is True and "ROLLBACK;" in gesehen["sql"]
