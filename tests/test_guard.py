from pathlib import Path

from elen.guard import Guard
from elen.plugins import ToolSpec


async def _noop(**_):
    return None


def spec(recipients=("to",)):
    return ToolSpec("email", "send_email", "", {"properties": {}}, "write", ("to",), recipients, _noop)


def guard(tmp_path):
    return Guard({"guard": {}}, Path(tmp_path) / "audit.log")


def test_recipient_status(tmp_path):
    g = guard(tmp_path)
    report, warnings = g.check_recipients(
        spec(),
        {"to": "dana@example.com, typed@me.com, seen@data.com, ghost@nowhere.com"},
        known={"Dana@Example.com"},
        user_texts=["send it to typed@me.com"],
        tool_texts=['{"from": "seen@data.com"}'],
    )
    status = {r["value"]: r["status"] for r in report}
    assert status == {
        "dana@example.com": "contact",
        "typed@me.com": "typed_by_you",
        "seen@data.com": "from_data",
        "ghost@nowhere.com": "unverified",
    }
    assert len(warnings) == 1 and "ghost@nowhere.com" in warnings[0]


def test_name_instead_of_address_is_invalid(tmp_path):
    report, warnings = guard(tmp_path).check_recipients(spec(), {"to": "Dana"}, set(), [], [])
    assert report[0]["status"] == "invalid" and warnings


def test_action_claim(tmp_path):
    g = guard(tmp_path)
    assert g.action_claim_warning("I have sent the email.", [])
    assert g.action_claim_warning("I've scheduled the meeting", [{"ok": False, "risk": "write"}])
    assert not g.action_claim_warning("I have sent it.", [{"ok": True, "risk": "write"}])
    assert not g.action_claim_warning("Here is your calendar.", [])


def test_overrides(tmp_path):
    g = Guard({"guard": {"overrides": {"email__send_email": "dangerous"}}}, Path(tmp_path) / "a.log")
    assert g.effective_risk(spec()) == "dangerous"
