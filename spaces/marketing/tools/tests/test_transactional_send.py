import asyncio
from spaces.marketing.tools import transactional_send as ts

def test_dry_run_never_sends_and_is_ok():
    res = asyncio.run(ts.send_one(
        to="lead@example.com", subject="hi", body_html="<p>hi</p>",
        mode="dry_run", source="OutreachAgent"))
    assert res["ok"] is True
    assert res["mode"] == "dry_run"
    assert "message_id" not in res or res["message_id"] is None
