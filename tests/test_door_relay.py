"""The door command over the cloud relay, as #14 saw it.

The relay challenges the MESSAGE with a 407, then answers the authenticated one
late: 202 after ~15.2 s on an address with no device behind it. A timeout there
does not mean "not sent": the relay has the MESSAGE and may still deliver it, so
a retry is a second door open. Time runs 10x faster: the timeout the hub asks
for is scaled, and so is the relay's delay.
"""

from __future__ import annotations

import asyncio

import pytest
from harness.peer import DOMAIN, Msg, is_, response
from harness.rig import Rig, run

from custom_components.vimar_intercom import hub as hub_mod
from custom_components.vimar_intercom import sip_client as sip

pytestmark = pytest.mark.slow

SCALE = 0.1  # 1 s here = 10 s on the real relay
RELAY_DELAY = 15.2  # #14: the relay's 202 came ~15.2 s after the MESSAGE


def _fast_clock(monkeypatch):
    real = sip.send_message

    async def scaled(uri, body, extra_headers=None, timeout=15):
        return await real(uri, body, extra_headers, timeout=timeout * SCALE)

    monkeypatch.setattr(sip, "send_message", scaled)


def _slow_relay(peer, code, reason):
    """407 to the bare MESSAGE, then `code` RELAY_DELAY later to the signed one
    (code None: never answers)."""
    orig = peer._on_raw

    def on_raw(raw):
        m = Msg(raw)
        if m.kind != "MESSAGE":
            return orig(raw)
        peer.log.append(m)
        peer._new.set()
        if "proxy-authorization" not in m.hdrs:
            peer.send(
                response(
                    m,
                    407,
                    "Proxy Authentication Required",
                    extra=(f'Proxy-Authenticate: Digest realm="{DOMAIN}", nonce="n407", qop="auth"\r\n'),
                )
            )
        elif code:
            asyncio.get_running_loop().call_later(RELAY_DELAY * SCALE, peer.send, response(m, code, reason))

    peer._on_raw = on_raw


async def _open(monkeypatch, code, reason):
    async with Rig(monkeypatch, "tls") as rig:
        await rig.register()
        _fast_clock(monkeypatch)
        _slow_relay(rig.peer, code, reason)
        ok, msg = await rig.hub.async_door(target="55001")
        await asyncio.sleep(1)  # room for a retry to show up
        signed = rig.peer.got(lambda m: is_("MESSAGE")(m) and "proxy-authorization" in m.hdrs)
        return ok, msg, len(signed), rig.hub.stats["door_count"]


def test_a_late_200_from_the_relay_is_an_open_door(monkeypatch):
    ok, msg, sent, count = run(_open(monkeypatch, 200, "OK"))
    assert (ok, msg) == (True, "OK (200)")
    assert sent == 1, f"{sent} door commands sent: the door opens {sent} times"
    assert count == 1


def test_a_late_202_is_not_an_open_door_and_is_not_retried(monkeypatch):
    ok, msg, sent, count = run(_open(monkeypatch, 202, "Accepted"))
    assert (ok, msg) == (False, hub_mod.DOOR_QUEUED)
    assert sent == 1, f"{sent} copies queued at the relay: the door may open that many times later"
    assert count == 0


def test_a_timeout_over_the_cloud_is_not_retried(monkeypatch):
    ok, msg, sent, count = run(_open(monkeypatch, None, ""))
    assert (ok, msg) == (False, hub_mod.DOOR_UNCONFIRMED)
    assert sent == 1, "the relay has the first MESSAGE: a retry may open the door twice"
    assert count == 0


@pytest.mark.parametrize("code, reason", [(408, "Request Timeout"), (504, "Server Time-out")])
def test_a_408_or_504_from_the_relay_is_not_retried(monkeypatch, code, reason):
    """The relay forwarded it and the door did not answer in time: it may still open."""
    ok, msg, sent, count = run(_open(monkeypatch, code, reason))
    assert (ok, msg) == (False, hub_mod.DOOR_UNCONFIRMED)
    assert sent == 1, "a retry may open the door twice"
    assert count == 0


def _send_error(monkeypatch, signed):
    """The connection resets on the first (un)signed MESSAGE. Signed: after its
    bytes left (reset while draining). Unsigned: before anything left."""
    real_send = sip.send
    hit = []

    async def send(msg):
        if msg.startswith("MESSAGE") and ("Proxy-Authorization" in msg) == signed and not hit:
            hit.append(msg)
            if signed:
                await real_send(msg)
            raise ConnectionResetError("reset")
        await real_send(msg)

    monkeypatch.setattr(sip, "send", send)


async def _open_with_send_error(monkeypatch, signed):
    async with Rig(monkeypatch, "tls") as rig:
        await rig.register()
        _fast_clock(monkeypatch)
        _slow_relay(rig.peer, None if signed else 200, "OK")
        _send_error(monkeypatch, signed)
        ok, msg = await rig.hub.async_door(target="55001")
        await asyncio.sleep(1)
        signed_msgs = rig.peer.got(lambda m: is_("MESSAGE")(m) and "proxy-authorization" in m.hdrs)
        return ok, msg, len(signed_msgs)


def test_a_send_error_on_the_signed_message_over_the_cloud_is_not_retried(monkeypatch):
    """The signed MESSAGE may already be at the relay: no second copy."""
    ok, msg, sent = run(_open_with_send_error(monkeypatch, signed=True))
    assert (ok, msg) == (False, hub_mod.DOOR_UNCONFIRMED)
    assert sent == 1, "a retry after a reset may open the door twice"


def test_a_send_error_on_the_unsigned_message_is_still_retried(monkeypatch):
    """Nothing reached the relay yet: re-register and send it, as before."""
    ok, msg, sent = run(_open_with_send_error(monkeypatch, signed=False))
    assert (ok, msg) == (True, "OK (200)")
    assert sent == 1
