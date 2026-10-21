from unittest.mock import MagicMock, patch

from core.discourse_engine import (
    AGENTS_RETRY_S, RESPONSE_TICK_INTERVAL_S, TICK_INTERVAL_S, DiscourseEngine,
)


def _antwort(agenten):
    r = MagicMock()
    r.json.return_value = agenten
    r.raise_for_status.return_value = None
    return r


def test_ohne_phi3_spricht_engine_niemanden_an():
    eng = DiscourseEngine(kg=MagicMock())
    with patch("core.discourse_engine.requests.get",
               return_value=_antwort([{"name": "brain-planner", "state": "Running"}])):
        assert eng._ensure_agents() is False
    assert eng._agents == []
    assert eng.stats["last_error"] == "keine Discourse-Agenten (-phi3)"


def test_leere_liste_wird_nicht_bei_jedem_tick_neu_geholt():
    eng = DiscourseEngine(kg=MagicMock())
    uhr = [1000.0]
    with patch("core.discourse_engine.time.time", side_effect=lambda: uhr[0]), \
         patch("core.discourse_engine.requests.get",
               return_value=_antwort([{"name": "brain-planner"}])) as get:
        assert eng._ensure_agents() is False
        uhr[0] += 30
        assert eng._ensure_agents() is False
        assert get.call_count == 1
        uhr[0] += AGENTS_RETRY_S
        assert eng._ensure_agents() is False
        assert get.call_count == 2


def test_fehler_beim_holen_wird_ebenfalls_gebremst():
    eng = DiscourseEngine(kg=MagicMock())
    uhr = [1000.0]
    with patch("core.discourse_engine.time.time", side_effect=lambda: uhr[0]), \
         patch("core.discourse_engine.requests.get", side_effect=OSError("down")) as get:
        assert eng._ensure_agents() is False
        uhr[0] += 30
        assert eng._ensure_agents() is False
        assert get.call_count == 1


class _UhrEvent:
    """Event-Ersatz: wait() laesst eine Scheinuhr vorlaufen statt zu schlafen."""

    def __init__(self, uhr, beim_warten=None):
        self.uhr = uhr
        self.beim_warten = beim_warten

    def wait(self, t):
        self.uhr[0] += t
        if self.beim_warten:
            self.beim_warten()
        return False


def test_langer_schlaf_ohne_pause_dauert_die_volle_zeit():
    eng = DiscourseEngine(kg=MagicMock())
    uhr = [1000.0]
    eng._stop = _UhrEvent(uhr)
    with patch("core.discourse_engine.time.time", side_effect=lambda: uhr[0]):
        eng._sleep_interruptible(TICK_INTERVAL_S)
    assert uhr[0] - 1000.0 >= TICK_INTERVAL_S


def test_response_schlaf_ohne_pause_dauert_die_volle_zeit():
    eng = DiscourseEngine(kg=MagicMock())
    uhr = [1000.0]
    eng._stop_response = _UhrEvent(uhr)
    with patch("core.discourse_engine.time.time", side_effect=lambda: uhr[0]):
        eng._sleep_interruptible(RESPONSE_TICK_INTERVAL_S, response=True)
    assert uhr[0] - 1000.0 >= RESPONSE_TICK_INTERVAL_S


def test_ende_der_pause_weckt_langen_schlaf_frueh():
    eng = DiscourseEngine(kg=MagicMock())
    uhr = [1000.0]
    eng.pause()
    eng._stop = _UhrEvent(uhr, beim_warten=eng.resume)
    with patch("core.discourse_engine.time.time", side_effect=lambda: uhr[0]):
        eng._sleep_interruptible(TICK_INTERVAL_S)
    assert uhr[0] - 1000.0 < 1.0


def test_mit_phi3_nur_diese():
    eng = DiscourseEngine(kg=MagicMock())
    with patch("core.discourse_engine.requests.get", return_value=_antwort(
            [{"name": "a-phi3", "state": "Running"}, {"name": "brain-planner", "state": "Running"}])):
        assert eng._ensure_agents() is True
    assert [a["name"] for a in eng._agents] == ["a-phi3"]
