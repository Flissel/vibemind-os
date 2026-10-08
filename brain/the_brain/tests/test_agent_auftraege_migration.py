from pathlib import Path

SQL = Path(__file__).resolve().parents[3] / "supabase" / "migrations" / "20261008_brain_agent_auftraege.sql"


def test_migration_hat_alle_spalten_und_rls():
    s = SQL.read_text(encoding="utf-8")
    for spalte in ("trace_id", "plan_id", "hop_id", "capability", "agent", "auftrag", "status", "ergebnis",
                   "fehler", "grund", "angelegt", "begonnen", "beendet", "frist", "versuche", "pruefung",
                   "plan_rest", "uebergabe", "antwortkanal", "zugestellt"):
        assert f"\n    {spalte} " in s, spalte
    assert "enable row level security" in s
    assert "revoke all on public.brain_agent_auftraege from anon, authenticated" in s
    for st in ("offen", "laeuft", "fertig", "fehler", "abgelehnt", "abgelaufen"):
        assert f"'{st}'" in s
