"""Tests fuer den Vorlagen-Erzeuger (Teil 4 Katalog fuellen)."""
from __future__ import annotations

import importlib.util
import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "katalog_vorlagen.py"
spec = importlib.util.spec_from_file_location("katalog_vorlagen", SCRIPT)
assert spec and spec.loader
kv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kv)


def zeile(name: str = "acme", ergebnis: str = "einrichtbar_oauth", **extra: Any) -> dict[str, Any]:
    z: dict[str, Any] = {
        "name": name,
        "anzeigename": name.title(),
        "server_url": f"https://mcp.{name}.com/mcp",
        "transport": "http",
        "anmeldung": "oauth",
        "header_name": "",
        "header_format": "",
        "schluessel_link": "",
        "quelle": f"https://developers.{name}.com/mcp",
        "offiziell": True,
        "notiz": "",
        "pruefung": {
            "ergebnis": ergebnis,
            "klasse": "x",
            "scopes_supported": [],
            "datum": "2026-11-10",
        },
    }
    scopes = extra.pop("scopes_supported", None)
    z.update(extra)
    if scopes is not None:
        z["pruefung"]["scopes_supported"] = scopes
    return z


def eintrag(name: str, *arten: str) -> dict[str, Any]:
    return {
        "name": name,
        "components": [{"component": {"kind": a, "name": a}} for a in arten],
    }


def katalog(*eintraege: dict[str, Any]) -> dict[str, Any]:
    return {"entries": list(eintraege)}


def lauf(
    tmp_path: Path,
    recherche: list[dict[str, Any]],
    kat: dict[str, Any],
    mit: list[str] | None = None,
) -> tuple[list[dict[str, str]], list[str], Path]:
    r = tmp_path / "recherche.json"
    k = tmp_path / "katalog.json"
    m = tmp_path / "mitgeliefert"
    z = tmp_path / "ziel"
    m.mkdir(exist_ok=True)
    z.mkdir(exist_ok=True)
    for i in mit or []:
        (m / f"{i}.toml").write_text("id = 'x'\n", encoding="utf-8")
    r.write_text(json.dumps(recherche), encoding="utf-8")
    k.write_text(json.dumps(kat), encoding="utf-8")
    zeilen, fehler = kv.lauf(r, k, m, z)
    return zeilen, fehler, z


def test_oauth_vorlage() -> None:
    t = tomllib.loads(kv.vorlage_toml(zeile(scopes_supported=["offline_access"])))
    assert t["auth"] == {"type": "oauth", "scopes": ["offline_access"]}
    assert t["catalog"] == {
        "replaces_openai_plugin": "acme",
        "license": "proprietary-service",
        "admission": "admitted",
        "verified": False,
    }
    assert t["read_only_tools"] == []
    assert t["transport"]["url"] == "https://mcp.acme.com/mcp"
    assert t["category"] in kv.KATEGORIEN


def test_oauth_ohne_offline_access() -> None:
    t = tomllib.loads(kv.vorlage_toml(zeile(scopes_supported=["read"])))
    assert t["auth"]["scopes"] == []


def test_schluessel_vorlage() -> None:
    z = zeile(
        "moody-s",
        "einrichtbar_schluessel",
        header_name="Authorization",
        header_format="Bearer {credential}",
        schluessel_link="https://x.example/key",
    )
    t = tomllib.loads(kv.vorlage_toml(z))
    assert "auth" not in t
    assert t["auth_headers"][0]["credential"] == "INTEGRATION_MOODY_S_KEY"
    assert t["auth_headers"][0]["format"] == "Bearer {credential}"
    env = t["required_env"][0]
    assert env["name"] == "INTEGRATION_MOODY_S_KEY"
    assert env["is_secret"] is True
    assert env["get_url"] == "https://x.example/key"


def test_ohne_anmeldung_vorlage() -> None:
    t = tomllib.loads(kv.vorlage_toml(zeile(ergebnis="einrichtbar_ohne_anmeldung")))
    assert "auth" not in t and "auth_headers" not in t and "required_env" not in t


def test_sonderzeichen_im_namen() -> None:
    name = "Moody's \"Analytics\" & S&P"
    t = tomllib.loads(kv.vorlage_toml(zeile(anzeigename=name)))
    assert t["name"] == name


def test_domain_pruefung(tmp_path: Path) -> None:
    ok = zeile("hubspot", server_url="https://mcp.hubspot.com/", quelle="https://developers.hubspot.com/mcp")
    assert kv.domain_ok(ok)
    fremd = zeile("hubspot", server_url="https://mcp.example-aggregator.io/", quelle="https://hubspot.com/x")
    assert not kv.domain_ok(fremd)
    assert kv.domain_ok(zeile(server_url="https://foo.co.uk/m", quelle="https://bar.foo.co.uk/d"))
    zeilen, _fehler, ziel = lauf(tmp_path, [fremd], katalog(eintrag("hubspot", "app")))
    assert zeilen[0]["ergebnis"] == "domain_pruefen"
    assert not (ziel / "hubspot.toml").exists()


def test_anbieter_domains_ausnahme(monkeypatch: pytest.MonkeyPatch) -> None:
    z = zeile("atl", server_url="https://mcp.atlassian.com/", quelle="https://atlassian.net/doc")
    assert not kv.domain_ok(z)
    monkeypatch.setitem(kv.ANBIETER_DOMAINS, "atl", frozenset({"atlassian.com", "atlassian.net"}))
    assert kv.domain_ok(z)


def test_nie_ueberschreiben(tmp_path: Path) -> None:
    z = tmp_path / "ziel"
    z.mkdir()
    (z / "github.toml").write_bytes(b"irgendwas = 1\n")
    notion = b'id = "notion"\n[catalog]\nreplaces_openai_plugin = "notion"\nverified = true\n'
    (z / "notion.toml").write_bytes(notion)
    zeilen, _fehler, ziel = lauf(
        tmp_path,
        [zeile("notion"), zeile("github")],
        katalog(eintrag("notion", "app"), eintrag("github", "app", "mcp")),
    )
    assert (ziel / "github.toml").read_bytes() == b"irgendwas = 1\n"
    assert (ziel / "notion.toml").read_bytes() == notion
    erg = {r["name"]: r["ergebnis"] for r in zeilen}
    assert erg == {"notion": "vorlage_bestand", "github": "vorlage_bestand"}


def test_ohne_verified_feld_wird_nicht_ueberschrieben(tmp_path: Path) -> None:
    z = tmp_path / "ziel"
    z.mkdir()
    inhalt = b'id = "acme"\n[catalog]\nreplaces_openai_plugin = "acme"\n'
    (z / "acme.toml").write_bytes(inhalt)
    zeilen, _f, ziel = lauf(tmp_path, [zeile("acme")], katalog(eintrag("acme", "app")))
    assert (ziel / "acme.toml").read_bytes() == inhalt
    assert zeilen[0]["ergebnis"] == "vorlage_bestand"


def test_zweiter_lauf_aendert_nichts(tmp_path: Path) -> None:
    rech = [zeile("a1"), zeile("b2", ergebnis="einrichtbar_ohne_anmeldung")]
    kat = katalog(eintrag("a1", "app"), eintrag("b2", "app"))
    _z, _f, ziel = lauf(tmp_path, rech, kat)
    erst = {p.name: p.read_bytes() for p in ziel.rglob("*") if p.is_file()}
    assert "a1.toml" in erst and "b2.toml" in erst and "README.md" in erst
    lauf(tmp_path, rech, kat)
    zweit = {p.name: p.read_bytes() for p in ziel.rglob("*") if p.is_file()}
    assert erst == zweit


def test_mitgeliefert(tmp_path: Path) -> None:
    kat = katalog(eintrag("gmail", "app"))
    zeilen, _f, ziel = lauf(tmp_path, [zeile("gmail", "braucht_registrierung")], kat, mit=["gmail"])
    assert zeilen[0]["ergebnis"] == "vorlage_mitgeliefert"
    assert not (ziel / "gmail.toml").exists()
    zeilen, _f, ziel = lauf(tmp_path, [zeile("gmail", "einrichtbar_oauth")], kat, mit=["gmail"])
    assert zeilen[0]["ergebnis"] == "vorlage"
    assert (ziel / "gmail.toml").exists()


def test_abgleich_vollstaendig() -> None:
    kat = katalog(
        eintrag("app-a", "app", "asset"),
        eintrag("mcp-b", "app", "mcp"),
        eintrag("skill-c", "skill", "asset"),
        eintrag("github", "app", "mcp"),
        eintrag("ohne-server", "app"),
        eintrag("kunde", "app"),
        eintrag("unrecherchiert", "app"),
    )
    rech = [
        zeile("app-a"),
        zeile("mcp-b"),
        zeile("ohne-server", server_url="", transport="", pruefung=None),
        zeile("kunde", server_url="https://{tenant}.kunde.com/mcp"),
    ]
    zeilen = kv.abgleich(rech, kat, [])
    assert sorted(r["name"] for r in zeilen) == sorted(e["name"] for e in kat["entries"])
    erg = {r["name"]: r["ergebnis"] for r in zeilen}
    assert erg["skill-c"] == "skills_teil_5"
    assert erg["github"] == "vorlage_bestand"
    assert erg["ohne-server"] == "kein_offizieller_server"
    assert erg["kunde"] == "adresse_je_kunde"
    assert erg["unrecherchiert"] == "kein_offizieller_server"
    assert {r["name"]: r["sorte"] for r in zeilen}["mcp-b"] == "mcp"
    assert erg["app-a"] == "vorlage"


def test_id_format(tmp_path: Path) -> None:
    _z, fehler, ziel = lauf(tmp_path, [zeile("Foo_Bar")], katalog(eintrag("Foo_Bar", "app")))
    assert fehler
    assert not list(ziel.glob("*.toml"))


def test_kategorie_vollstaendig() -> None:
    pfad = ROOT / "integrations" / "abgleich" / "recherche.json"
    rech = json.loads(pfad.read_text(encoding="utf-8"))
    for r in rech:
        if (r.get("pruefung") or {}).get("ergebnis", "").startswith("einrichtbar_"):
            assert r["name"] in kv.KATEGORIE, r["name"]
    assert set(kv.KATEGORIE.values()) <= kv.KATEGORIEN
    assert set(kv.ICON) == set(kv.KATEGORIEN)


def test_github_quelle_bindet_an_org(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        kv.ANBIETER_DOMAINS, "acme", frozenset({"acme.com", "github.com/acme"})
    )
    ok = zeile("acme", server_url="https://mcp.acme.com/", quelle="https://github.com/Acme/mcp")
    assert kv.domain_ok(ok)
    fremd = zeile("acme", server_url="https://mcp.acme.com/", quelle="https://github.com/other/mcp")
    assert not kv.domain_ok(fremd)


def test_ausnahme_gilt_nur_fuer_den_eigenen_anbieter() -> None:
    assert "neon-postgres" in kv.ANBIETER_DOMAINS
    z = zeile("andere", server_url="https://mcp.neon.tech/", quelle="https://neon.com/docs")
    assert not kv.domain_ok(z)
    z = zeile("neon-postgres", server_url="https://mcp.neon.tech/", quelle="https://neon.com/docs")
    assert kv.domain_ok(z)


def test_teamwork_ausnahme_nur_eigene_org() -> None:
    z = zeile("teamwork-com", server_url="https://mcp.ai.teamwork.com/", quelle="https://github.com/Teamwork/mcp")
    assert kv.domain_ok(z)
    z = zeile("teamwork-com", server_url="https://mcp.ai.teamwork.com/", quelle="https://github.com/other/mcp")
    assert not kv.domain_ok(z)


def test_stichworte_vollstaendig_und_ohne_id() -> None:
    pfad = ROOT / "integrations" / "abgleich" / "recherche.json"
    rech = json.loads(pfad.read_text(encoding="utf-8"))
    for r in rech:
        if (r.get("pruefung") or {}).get("ergebnis", "").startswith("einrichtbar_"):
            assert kv.STICHWORTE.get(r["name"]), r["name"]
            tags = tomllib.loads(kv.vorlage_toml(r))["tags"]
            assert 1 <= len(tags) <= 3 and r["name"] not in tags
    for n in ("attio", "apollo", "outreach", "carta-crm", "highlevel", "common-room"):
        assert "crm" in kv.STICHWORTE[n]


# --- Final fix wave -------------------------------------------------------

def test_nicht_offiziell_liefert_keine_vorlage(tmp_path: Path) -> None:
    for wert in (False, None, "true", 1):
        z = zeile("acme", offiziell=wert)
        zeilen, _f, ziel = lauf(tmp_path, [z], katalog(eintrag("acme", "app")))
        assert zeilen[0]["ergebnis"] == "domain_pruefen"
        assert zeilen[0]["grund"] == "nicht_offiziell"
        assert not (ziel / "acme.toml").exists()


def test_hosting_plattformen_sind_eigene_domains() -> None:
    assert not kv.domain_ok(
        zeile("x", server_url="https://x.github.io/mcp", quelle="https://y.github.io/doc")
    )
    assert kv.domain_ok(
        zeile("x", server_url="https://a.x.github.io/mcp", quelle="https://x.github.io/doc")
    )
    assert kv.registrierbare_domain("foo.vercel.app") == "foo.vercel.app"
    assert kv.registrierbare_domain("a.b.workers.dev") == "b.workers.dev"
    for endung in (
        "github.io", "vercel.app", "netlify.app", "pages.dev", "herokuapp.com",
        "azurewebsites.net", "cloudfront.net", "onrender.com", "fly.dev",
        "workers.dev", "web.app", "firebaseapp.com", "appspot.com",
    ):
        assert endung in kv.HOSTING_ENDUNGEN


@pytest.mark.parametrize(
    "extra",
    [
        {"header_name": "", "header_format": "Bearer {credential}"},
        {"header_name": "X-Key", "header_format": ""},
        {"header_name": "X-Key", "header_format": "Bearer"},
        {"header_name": "X-Key", "header_format": "{credential}{credential}"},
        {"header_name": "X Key", "header_format": "{credential}"},
        {"header_name": "X-Key:", "header_format": "{credential}"},
    ],
)
def test_schluessel_ohne_gueltigen_header(tmp_path: Path, extra: dict[str, str]) -> None:
    z = zeile("acme", "einrichtbar_schluessel", **extra)
    zeilen, _f, ziel = lauf(tmp_path, [z], katalog(eintrag("acme", "app")))
    assert zeilen[0]["ergebnis"] == "unklar"
    assert zeilen[0]["grund"] == "header_unbekannt"
    assert not (ziel / "acme.toml").exists()


def test_produktivitaet_mit_umlaut() -> None:
    t = tomllib.loads(kv.vorlage_toml(zeile("acme")))
    assert t["tags"][0] == "produktivität"
    assert "produktivitaet" not in kv.TAG_JE_KATEGORIE.values()


def test_beschreibung_mit_umlaut() -> None:
    t = tomllib.loads(kv.vorlage_toml(zeile("acme")))
    assert t["description"].endswith(" über den offiziellen Remote-MCP-Server")
    assert "ueber" not in t["description"]


def test_cloud_symbol_ist_farbig() -> None:
    # Das Windows-Emoji ☁️ ist hellgrau und auf weissen Karten kaum sichtbar.
    assert kv.ICON["cloud"] == "🌐"


def test_readme_nennt_alle_ergebnisse(tmp_path: Path) -> None:
    zeilen, _f, ziel = lauf(tmp_path, [zeile("acme")], katalog(eintrag("acme", "app")))
    md = (ziel / "abgleich" / "README.md").read_text(encoding="utf-8")
    for e in ("nicht_erreichbar", "domain_pruefen", "id_ungueltig", "unklar"):
        assert f"| {e} | 0 |" in md
    assert "| vorlage | 1 |" in md


def test_highlevel_hinweis_in_abgleich() -> None:
    z = zeile("highlevel", server_url="https://services.leadconnectorhq.com/mcp/anthropic/v2",
              quelle="https://marketplace.gohighlevel.com/docs/other/mcp")
    r = kv.abgleich([z], katalog(eintrag("highlevel", "app")), [])
    assert r[0]["ergebnis"] == "vorlage"
    assert "Anthropic-Clients" in r[0]["grund"]


def test_veraltete_vorlagen_werden_geloescht(tmp_path: Path) -> None:
    z = tmp_path / "ziel"
    z.mkdir()
    alt = z / "alt.toml"
    alt.write_text(
        kv.vorlage_toml(zeile("alt")), encoding="utf-8", newline="\n"
    )
    handarbeit = z / "hand.toml"
    handarbeit.write_text('id = "hand"\n[catalog]\nverified = false\n', encoding="utf-8")
    verifiziert = z / "ver.toml"
    verifiziert.write_text(
        kv.vorlage_toml(zeile("ver")).replace("verified = false", "verified = true"),
        encoding="utf-8", newline="\n",
    )
    geschuetzt = z / "github.toml"
    geschuetzt.write_text(kv.vorlage_toml(zeile("github")), encoding="utf-8", newline="\n")
    rech = [zeile("alt", ergebnis="unklar"), zeile("neu")]
    kat = katalog(eintrag("alt", "app"), eintrag("neu", "app"))
    r = tmp_path / "recherche.json"
    k = tmp_path / "katalog.json"
    m = tmp_path / "mit"
    m.mkdir()
    r.write_text(json.dumps(rech), encoding="utf-8")
    k.write_text(json.dumps(kat), encoding="utf-8")
    weg: list[str] = []
    kv.lauf(r, k, m, z, weg)
    assert weg == ["alt.toml"]
    assert not alt.exists()
    assert (z / "neu.toml").exists()
    assert handarbeit.exists() and verifiziert.exists() and geschuetzt.exists()


def test_doppelter_name_bricht_ab(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    r = tmp_path / "recherche.json"
    k = tmp_path / "katalog.json"
    m = tmp_path / "mit"
    m.mkdir()
    r.write_text(json.dumps([zeile("acme"), zeile("acme")]), encoding="utf-8")
    k.write_text(json.dumps(katalog(eintrag("acme", "app"))), encoding="utf-8")
    code = kv.main(
        ["--recherche", str(r), "--katalog", str(k), "--mitgeliefert", str(m),
         "--ziel", str(tmp_path / "ziel")]
    )
    assert code == 1
    assert "doppelter Name" in capsys.readouterr().err
    assert not list((tmp_path / "ziel").glob("*.toml"))


def test_echte_vorlagen_schema() -> None:
    ordner = ROOT / "integrations"
    dateien = sorted(ordner.glob("*.toml"))
    assert dateien
    for f in dateien:
        text = f.read_text(encoding="utf-8")
        t = tomllib.loads(text)
        assert t["category"] in kv.KATEGORIEN, f.name
        assert t["transport"]["type"] in {"http", "sse"}, f.name
        if "catalog" in t:
            for feld in ("replaces_openai_plugin", "license", "admission"):
                assert t["catalog"].get(feld), (f.name, feld)
        if text.startswith(kv.KOPFZEILE):
            assert t["catalog"]["verified"] is False, f.name
            assert t["read_only_tools"] == [], f.name
