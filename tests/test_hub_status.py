"""hub.status while a REGISTER renewal lapses during a call or a ring."""

from __future__ import annotations

import asyncio

import pytest
from harness.peer import answer_200, is_
from harness.rig import Rig, run, wait_until

from custom_components.vimar_intercom import media_handler as media
from custom_components.vimar_intercom import sip_client as sip


def test_in_call_wins_over_a_lapsed_registration(hub, monkeypatch):
    monkeypatch.setattr(sip, "in_call", True)
    monkeypatch.setattr(sip, "registered", False)
    assert hub.status == "in_call"


def test_a_ring_wins_over_a_lapsed_registration(hub, monkeypatch):
    monkeypatch.setattr(sip, "registered", False)
    monkeypatch.setitem(sip.pending_incoming, "active", True)
    assert hub.status == "ringing"


@pytest.mark.slow
def test_a_failed_renewal_during_a_call_does_not_turn_the_status_offline(monkeypatch):
    """The registrar refuses a renewal (5xx) mid-call: the call stays in_call."""

    async def s():
        async with Rig(monkeypatch) as rig:
            await rig.register()
            rig.peer.on_invite = answer_200
            assert (await rig.hub.async_call())[0]
            rig.peer.register_code = 503
            await rig.hub._keepalive_tick()
            await asyncio.sleep(0.2)
            assert rig.hub.in_call and media.audio_proto.remote_addr, "the call itself is fine"
            assert rig.hub.status == "in_call", f"status {rig.hub.status!r} with the call up"
            rig.peer.register_code = None
            await rig.hub.async_hangup()

    run(s())


@pytest.mark.slow
def test_a_ring_after_a_failed_renewal_is_still_a_ring(monkeypatch):
    """A ring after a failed renewal still reads "ringing", not "offline"."""

    async def s():
        async with Rig(monkeypatch, "tls") as rig:
            await rig.register()
            # What do_register does on a refused or unanswered renewal.
            sip._set_registered(False)
            rig.ring()
            await rig.peer.wait_for(is_(code=183))
            await wait_until(lambda: rig.rings == 1, 2, "doorbell event")
            assert rig.hub.video_active, "the preview is on"
            assert rig.hub.status == "ringing", f"status {rig.hub.status!r} while the panel rings us"
            await rig.hub.async_decline()

    run(s())
