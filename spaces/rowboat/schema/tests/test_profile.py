"""Checks for ehl_schema.profile."""
import pytest

from ehl_schema.model import SchemaError, load_schema
from ehl_schema.profile import PROFILES_DIR, compile_pattern, default_profile, load_profile


@pytest.fixture
def schema():
    return load_schema()


def fits(pattern, path):
    return compile_pattern(pattern).fullmatch(path) is not None


def test_star_stays_inside_one_segment():
    assert fits("Projects/*", "Projects/E-Ticketing")
    assert not fits("Projects/*", "Projects/A/_overview")
    assert fits("Notes/Traction Metrics*", "Notes/Traction Metrics 2026-Q2")


def test_double_star_spans_one_or_more_segments():
    assert fits("Diary/**", "Diary/Blue Rose Diary")
    assert fits("vibemind-discourse/**", "vibemind-discourse/2026-04-30/04-07")
    assert not fits("Diary/**", "Diary")


def test_captures_and_special_characters():
    m = compile_pattern("Projects/{p}/_overview").fullmatch("Projects/VibeMind - LinkedIn Launch — Beta (c1c67f)/_overview")
    assert m.group("p") == "VibeMind - LinkedIn Launch — Beta (c1c67f)"
    assert fits("Bubbles/**", "Bubbles/Grüne Idee (fa8d41)")
    assert fits("Projects/VibeMind - TODO */_overview", "Projects/VibeMind - TODO Tech-Stack (z. B. OAuth2)/_overview")
    assert not fits("Notes/a.b", "Notes/aXb")             # the dot is literal


def test_bad_patterns_are_rejected():
    for bad in ("Projects/{p", "Projects/a**b", "Projects/{p}/{p}"):
        with pytest.raises(SchemaError):
            compile_pattern(bad)


def test_vibemind_profile_loads_and_first_rule_wins(schema):
    profile = load_profile("vibemind", schema)
    assert profile.company == "VibeMind"
    assert profile.name == "vibemind"
    rule, caps = profile.match("Projects/VibeMind - TODO Tests/_overview")
    assert rule.type == "Tasks"
    rule, caps = profile.match("Projects/VibeMind - Shop/Payment Flow")
    assert (rule.type, rule.parent, caps) == ("Requirements", "Projects/{p}/_overview", {"p": "VibeMind - Shop"})
    assert profile.match("Agents/abo-probe (183dc8)") is None   # left to the default rule
    assert profile.excludes("Diary/Blue Rose Diary")
    assert profile.excludes("Welcome")
    assert not profile.excludes("Topics/AI")
    assert profile.has_rules
    assert profile.exclude_raw == ("Diary/**", "Bewerbung/**", "Welcome")


def test_default_profile_has_no_rules():
    profile = default_profile("EHL")
    assert (profile.company, profile.rules, profile.exclude, profile.has_rules) == ("EHL", (), (), False)


def write_profile(tmp_path, text):
    path = tmp_path / "p.yaml"
    path.write_text(text, encoding="utf-8", newline="\n")
    return str(path)


def test_profile_errors(tmp_path, schema):
    cases = {
        "unknown type": "company: X\nrules:\n  - {match: 'A/**', type: Nope}\n",
        "structure type": "company: X\nrules:\n  - {match: 'A/**', type: Areas}\n",
        "unknown capture": "company: X\nrules:\n  - {match: 'A/*', type: Topics, parent: 'B/{q}'}\n",
        "no company": "rules: []\n",
    }
    for name, text in cases.items():
        with pytest.raises(SchemaError):
            load_profile(write_profile(tmp_path, text), schema)
    with pytest.raises(SchemaError, match="no profile"):
        load_profile("does-not-exist", schema)
    assert (PROFILES_DIR / "vibemind.yaml").is_file()
