"""Der Supabase-Zugang des Ideen-Clients.

Warum es diesen Test gibt: bis 2026-09-24 las `supabase_ideas_client` bei
Modul-Import eine tote LAN-Adresse (SUPABASE_URL-Vorgabe) und den Schluessel
mit dem Platzhalter "anon" (SUPABASE_KEY-Vorgabe). brain-core bekommt den
Schluessel aber als Datei (SUPABASE_ANON_KEY_FILE). Jede `supabase:`-Operation
(bubble_*, idea_*, idea_count ...) bekam deshalb live nach dem Deploy 401 und
fand nichts.
"""
from unittest.mock import patch

import pytest

from core import config as core_config
from core.supabase_ideas_client import SupabaseIdeasClient


@pytest.fixture
def _sauber(monkeypatch, tmp_path):
    for name in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_ANON_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)
    # /run/secrets gibt es lokal nicht; get_secret faellt dann auf env zurueck.
    # get_secret() ruft aber auch _load_env_file() (Repo-.env-Loader) und liest
    # _SECRETS_DIR (/run/secrets) - beides muss stillgelegt werden, sonst
    # messen die Tests diese Maschine statt den Code.
    monkeypatch.setattr(core_config, "_load_env_file", lambda *a, **k: None)
    monkeypatch.setattr(core_config, "_SECRETS_DIR", str(tmp_path))


def test_schluessel_kommt_aus_der_datei(monkeypatch, tmp_path, _sauber):
    datei = tmp_path / "anon"
    datei.write_text("a.b.c\n", encoding="utf-8")
    monkeypatch.setenv("SUPABASE_URL", "http://supabase-kong:8000/")
    monkeypatch.setenv("SUPABASE_ANON_KEY_FILE", str(datei))

    client = SupabaseIdeasClient()

    assert client.key == "a.b.c"
    assert client.url == "http://supabase-kong:8000"


def test_konstruktionszeit_statt_importzeit(monkeypatch, tmp_path, _sauber):
    """Das Modul ist laengst importiert (oben in dieser Datei). Die Umgebung
    wird trotzdem erst jetzt gesetzt - und muss trotzdem gelesen werden, weil
    die Aufloesung in __init__ passiert statt in Modulkonstanten."""
    datei = tmp_path / "anon"
    datei.write_text("nach-dem-import\n", encoding="utf-8")
    monkeypatch.setenv("SUPABASE_URL", "http://spaeter-gesetzt:9999")
    monkeypatch.setenv("SUPABASE_ANON_KEY_FILE", str(datei))

    client = SupabaseIdeasClient()

    assert client.url == "http://spaeter-gesetzt:9999"
    assert client.key == "nach-dem-import"


def test_ohne_url_kein_netzaufruf_und_fehlschlag(monkeypatch, _sauber):
    monkeypatch.setenv("SUPABASE_ANON_KEY", "k")
    client = SupabaseIdeasClient()

    assert client.url == ""

    with patch("httpx.AsyncClient") as async_client_cls:
        import asyncio
        result = asyncio.run(client.get_idea("x"))

    async_client_cls.assert_not_called()
    assert result is None
    assert client.stats["errors"] >= 1
    assert "SUPABASE_URL" in (client.stats["last_error"] or "")


def test_ohne_schluessel_kein_anon_platzhalter_kein_netzaufruf(monkeypatch, _sauber):
    monkeypatch.setenv("SUPABASE_URL", "http://supabase-kong:8000")
    client = SupabaseIdeasClient()

    assert client.key == ""
    assert client._headers().get("apikey") != "anon"

    with patch("httpx.AsyncClient") as async_client_cls:
        import asyncio
        result = asyncio.run(client.get_idea("x"))

    async_client_cls.assert_not_called()
    assert result is None
    assert "Schluessel" in (client.stats["last_error"] or "")


def test_explizite_argumente_gewinnen_ueber_die_umgebung(monkeypatch, _sauber):
    monkeypatch.setenv("SUPABASE_URL", "http://aus-der-umgebung:8000")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "aus-der-umgebung")

    client = SupabaseIdeasClient(url="http://explizit:1234/", anon_key="explizit-schluessel")

    assert client.url == "http://explizit:1234"
    assert client.key == "explizit-schluessel"
