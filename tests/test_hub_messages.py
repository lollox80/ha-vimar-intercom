"""The hub's read side: properties, callbacks, statistics and the SIP MESSAGE
announcements from the panel (voicemail, phonebook, missed calls, ...).

Nothing here sends anything: the incoming messages are only parsed, and a
malformed one must leave the stats alone instead of raising.
"""
from __future__ import annotations

import asyncio
import json

import pytest

hub_mod = pytest.importorskip("custom_components.vimar_intercom.hub")
sip = hub_mod.sip
R = hub_mod.R
S = hub_mod.S
C = hub_mod.C


@pytest.fixture
def plain_hub(monkeypatch):
    """A hub with its real _touch (the conftest `hub` fixture replaces it)."""
    monkeypatch.setattr(sip, "in_call", False)
    monkeypatch.setattr(sip, "calling", False)
    monkeypatch.setattr(sip, "registered", True)
    return hub_mod.VimarIntercomHub()


def _events(h):
    got = []
    h.register_event_callback(lambda t, d: got.append((t, d)))
    return got


# ─── helpers and properties ──────────────────────────────────────────────────

def test_a_command_uri_accepts_only_plant_uris_and_ids(monkeypatch):
    monkeypatch.setattr(R, "SIP_DOMAIN", "plant.example")
    assert hub_mod._command_uri("sip:55001@plant.example") == "sip:55001@plant.example"
    assert hub_mod._command_uri("sip:55001@elsewhere.example") is None
    assert hub_mod._command_uri("55002") == "sip:55002@plant.example"
    assert hub_mod._command_uri("55002\r\nX: y") is None


def test_the_connection_properties_follow_the_transport(plain_hub, monkeypatch):
    monkeypatch.setattr(R, "LOCAL_PROXY", "192.0.2.10")
    monkeypatch.setattr(R, "SIP_PROXY", "sip.example")
    monkeypatch.setattr(R, "SIP_USER", "1234")
    monkeypatch.setattr(R, "SIP_DOMAIN", "plant.example")
    monkeypatch.setattr(sip, "MY_IP", "192.0.2.20")
    monkeypatch.setattr(R, "USE_LOCAL_UDP", True)
    assert (plain_hub.transport, plain_hub.proxy) == ("udp-local", "192.0.2.10")
    monkeypatch.setattr(R, "USE_LOCAL_UDP", False)
    assert (plain_hub.transport, plain_hub.proxy) == ("tls-cloud", "sip.example")
    assert plain_hub.local_ip == "192.0.2.20"
    assert (plain_hub.sip_user, plain_hub.sip_domain) == ("1234", "plant.example")


def test_voicemail_dnd_and_model_read_the_announced_state(plain_hub, monkeypatch):
    assert plain_hub.voicemail is None and plain_hub.dnd is None
    plain_hub.stats.update(voicemail=True, dnd=False)
    assert plain_hub.voicemail is True and plain_hub.dnd is False
    monkeypatch.setattr(S, "DETECTED_MODEL", "Elvox Tab 7S")
    assert plain_hub.detected_model == "Elvox Tab 7S"


def test_calling_and_status_follow_the_sip_state(plain_hub, monkeypatch):
    monkeypatch.setattr(sip, "calling", True)
    assert plain_hub.calling is True and plain_hub.status == "calling"
    monkeypatch.setattr(sip, "registered", False)
    assert plain_hub.status == "calling", "a lapsed registration does not hide a call"
    monkeypatch.setattr(sip, "calling", False)
    assert plain_hub.status == "offline"


def test_the_device_list_is_restored_and_shown_masked(plain_hub):
    plain_hub.restore_devices([
        {"sip_id": "", "device_id": "EMPTY-SIP-ID-0001"},  # no SIP user: skipped
        {"sip_id": "60901", "device_id": "PHONE-ID-0123456789", "name": "Phone"},
    ])
    assert [d["sip_id"] for d in plain_hub.devices] == ["60901"]
    assert plain_hub.devices[0]["device_id"] == "PHONE-ID-0123456789"
    assert plain_hub.devices_public[0]["device_id"] == "***456789"
    assert len(plain_hub.devices_summary) == 1 and "Phone" in plain_hub.devices_summary[0]


# ─── callbacks ───────────────────────────────────────────────────────────────

def test_notify_refreshes_every_entity_even_after_a_failing_one(plain_hub):
    seen = []

    def broken():
        raise RuntimeError("entity gone")

    plain_hub.register_state_callback(broken)
    plain_hub.register_state_callback(lambda: seen.append("ok"))
    plain_hub.notify()
    assert seen == ["ok"]
    plain_hub.unregister_state_callback(broken)
    plain_hub.unregister_state_callback(broken)  # twice: no error
    assert plain_hub._state_callbacks and broken not in plain_hub._state_callbacks


def test_a_failing_ring_callback_does_not_stop_the_others(plain_hub, monkeypatch):
    monkeypatch.setattr(R, "RING_WEBHOOK_URL", "")
    rang = []

    def broken():
        raise RuntimeError("boom")

    def good():
        rang.append(1)

    plain_hub.register_ring_callback(broken)
    plain_hub.register_ring_callback(good)
    plain_hub.fire_ring_callbacks()
    assert rang == [1]
    plain_hub.unregister_ring_callback(good)
    plain_hub.unregister_ring_callback(good)
    plain_hub.fire_ring_callbacks()
    assert rang == [1]


def test_a_failing_video_end_callback_does_not_stop_the_others(plain_hub):
    ended = []

    def broken():
        raise RuntimeError("boom")

    plain_hub.register_video_end_callback(broken)
    cancel = plain_hub.register_video_end_callback(lambda: ended.append(1))
    plain_hub._video_ended()
    cancel()
    plain_hub._video_ended()
    assert ended == [1]


def test_a_failing_event_callback_does_not_stop_the_others(plain_hub):
    def broken(t, d):
        raise RuntimeError("boom")

    plain_hub.register_event_callback(broken)
    got = _events(plain_hub)
    plain_hub._fire_event("x", {"a": 1})
    assert got == [("x", {"a": 1})]
    plain_hub.unregister_event_callback(broken)
    plain_hub.unregister_event_callback(broken)
    assert broken not in plain_hub._event_callbacks


def test_the_detected_model_reaches_every_model_callback(plain_hub):
    got = []

    def broken(*a):
        raise RuntimeError("boom")

    plain_hub.register_model_callback(broken)
    plain_hub.register_model_callback(lambda *a: got.append(a))
    plain_hub._on_model_detected("Elvox Tab 7S", "1.2", "Tab 7S/1.2", 1)
    assert got == [("Elvox Tab 7S", "1.2", "Tab 7S/1.2", 1)]


# ─── statistics ──────────────────────────────────────────────────────────────

def test_registered_and_error_events_are_recorded(plain_hub):
    plain_hub._update_stats("registered", "")
    plain_hub._update_stats("error", "x" * 300)
    assert plain_hub.stats["last_register_time"] is not None
    assert plain_hub.stats["last_error"] == "x" * 200
    assert plain_hub.stats["last_error_time"] is not None


def test_an_answered_ring_keeps_the_call_direction_incoming(plain_hub):
    plain_hub._ring_answered = True
    plain_hub.stats["last_call_direction"] = "in"
    plain_hub._update_stats("call_started", "")
    assert plain_hub.stats["last_call_direction"] == "in"
    assert plain_hub.stats["call_count"] == 1


def test_a_broken_stats_update_is_logged_and_the_entities_still_refresh(plain_hub, caplog):
    touched = []
    plain_hub.register_state_callback(lambda: touched.append(1))
    plain_hub.stats["missed_count"] = None  # corrupt: += 1 raises
    plain_hub.stats["last_ring_time"] = "earlier"
    plain_hub._update_stats("ring_ended", "")
    assert "stats update error" in caplog.text
    assert touched == [1]


def test_a_ring_that_ends_during_our_call_does_not_end_the_video(plain_hub, monkeypatch):
    monkeypatch.setattr(sip, "in_call", True)
    ended = []
    plain_hub.register_video_end_callback(lambda: ended.append(1))
    asyncio.run(plain_hub._handle_broadcast("ring_ended", ""))
    assert ended == []


def test_a_failing_ws_broadcast_is_logged_not_raised(plain_hub, caplog):
    async def broken(payload):
        raise ConnectionResetError("client gone")

    plain_hub.set_ws_broadcast(broken)
    asyncio.run(plain_hub._handle_broadcast("registered", ""))
    assert "WS broadcast error" in caplog.text
    assert plain_hub.stats["last_register_time"] is not None


def test_the_state_broadcast_says_whether_it_rings(plain_hub, monkeypatch):
    # The same fields as the /audio_ws replies (views._state).
    sent = []

    async def collect(payload):
        sent.append(payload)

    plain_hub.set_ws_broadcast(collect)
    monkeypatch.setattr(sip, "ringing", lambda: True)

    async def run():
        plain_hub._on_sip_state_change()
        for _ in range(5):
            await asyncio.sleep(0)

    asyncio.run(run())
    assert {"type": "state", "registered": True, "in_call": False, "ringing": True} in sent


def test_a_clip_without_video_leaves_the_last_clip_alone(plain_hub):
    plain_hub._clip_done(None)
    assert plain_hub.stats["last_clip"] is None
    plain_hub._clip_done("/tmp/x/squillo_1.mp4")
    assert plain_hub.stats["last_clip"] == "squillo_1.mp4"


# ─── incoming MESSAGE parsing ────────────────────────────────────────────────

def test_a_message_body_without_payload_has_no_json():
    assert hub_mod.VimarIntercomHub._split_json_payload("MISSED_CALL", 1) is None
    assert hub_mod.VimarIntercomHub._split_json_payload("MISSED_CALL; {} ", 1) == "{}"


def test_init_status_ignores_what_is_not_a_param_list(plain_hub):
    before = dict(plain_hub.stats)
    plain_hub._parse_init_status_reply('GET_INIT_STATUS_REPLY;{"PARAM":"dnd","VALUE":"1"}')
    plain_hub._parse_init_status_reply('GET_INIT_STATUS_REPLY;[1, {"VALUE": "x"}]')
    plain_hub._parse_init_status_reply("GET_INIT_STATUS_REPLY;garbage")
    plain_hub._parse_init_status_reply("GET_INIT_STATUS_REPLY")
    assert plain_hub.stats["init_status"] == {} and plain_hub._init_seq == 0
    assert plain_hub.stats["dnd"] == before["dnd"]


def test_init_status_mixed_list_keeps_the_valid_items(plain_hub):
    plain_hub._parse_init_status_reply(
        'GET_INIT_STATUS_REPLY;[1, {"PARAM": "dnd", "VALUE": "1"}, "x"]')
    assert plain_hub.stats["dnd"] is True and plain_hub._init_seq == 1


def test_a_truncated_init_status_is_read_by_the_regex_fallback(plain_hub):
    raw = 'GET_INIT_STATUS_REPLY;[{"PARAM":"vm_level","VALUE":"3/100"},{"PARAM":"rubr'
    plain_hub._parse_init_status_reply(raw)
    assert plain_hub.stats["vm_level"] == "3/100"


def test_non_list_timeout_values_are_ignored(plain_hub):
    plain_hub._apply_apt_params({"vm_timeout_values": "15,30", "vm_timeout": "x"})
    assert "vm_timeout_values" not in plain_hub.stats and "vm_timeout" not in plain_hub.stats


def test_apt_params_changed_ignores_what_it_cannot_read(plain_hub):
    plain_hub._handle_incoming_message("APT_PARAMS_CHANGED;not json")
    plain_hub._handle_incoming_message('APT_PARAMS_CHANGED;["vm_timeout", 30]')
    assert "vm_timeout" not in plain_hub.stats
    plain_hub._handle_incoming_message('APT_PARAMS_CHANGED;{"PARAM":"vm_timeout","VALUE":"30"}')
    assert plain_hub.stats["vm_timeout"] == 30


def test_an_unreadable_apt_params_reply_resolves_no_waiter(plain_hub):
    async def run():
        fut = asyncio.get_running_loop().create_future()
        plain_hub._apt_param_waiters["abc"] = fut
        plain_hub._handle_incoming_message("SET_APT_PARAMS_REPLY;not json")
        plain_hub._handle_incoming_message('SET_APT_PARAMS_REPLY;["abc"]')
        assert not fut.done()
        plain_hub._handle_incoming_message('SET_APT_PARAMS_REPLY;{"MSGID":"abc","ERRCODE":"ERR_NONE"}')
        return fut.result()

    assert asyncio.run(run()) == "ERR_NONE"


def test_a_refused_set_apt_param_command_fails_without_waiting(plain_hub, monkeypatch):
    async def refused(uri, body, extra_headers=None, timeout=15):
        return False, "403 Forbidden"

    monkeypatch.setattr(R, "PICG_TARGET", "55002")
    monkeypatch.setattr(sip, "do_system_message", refused)
    ok, msg = asyncio.run(plain_hub.async_set_apt_param("vm_timeout", 30, timeout=0.01))
    assert (ok, msg) == (False, "403 Forbidden")
    assert plain_hub._apt_param_waiters == {}


@pytest.mark.parametrize("raw, expected", [
    ("MISSED_CALL", {"sip_id": None, "ts": None, "name": None}),
    ("MISSED_CALL;not json", {"sip_id": None, "ts": None, "name": None}),
    ("MISSED_CALL;[1]", {"sip_id": None, "ts": None, "name": None}),
    ('MISSED_CALL;{"sip_id":"55001","ts":"t"}', {"sip_id": "55001", "ts": "t", "name": "Targa Esterna"}),
])
def test_missed_calls_are_counted_whatever_the_payload(plain_hub, raw, expected):
    got = _events(plain_hub)
    plain_hub._handle_incoming_message(raw)
    assert plain_hub.stats["last_missed_call"] == expected
    assert plain_hub.stats["missed_call_count"] == 1
    assert got == [(C.EVENT_MISSED_CALL, expected)]


@pytest.mark.parametrize("raw, expected", [
    ("FP;", {"sip_id": None, "msg": None}),
    ("FP;not json", {"sip_id": None, "msg": None}),
    ("FP;[1]", {"sip_id": None, "msg": None}),
    ('FP;{"sip_id":"55002","msg":"hi"}', {"sip_id": "55002", "msg": "hi"}),
])
def test_fuoriporta_is_fired_whatever_the_payload(plain_hub, raw, expected):
    got = _events(plain_hub)
    plain_hub._handle_incoming_message(raw)
    assert plain_hub.stats["last_fuoriporta"] == expected
    assert got == [(C.EVENT_FUORIPORTA, expected)]


@pytest.mark.parametrize("raw", ["CALL_INFO", "CALL_INFO;not json", "CALL_INFO;[1]"])
def test_call_info_without_readable_payload_fires_empty_fields(plain_hub, raw):
    got = _events(plain_hub)
    plain_hub._handle_incoming_message(raw)
    empty = {"sip_id": None, "reason": None, "media_type": None, "video_src": None}
    assert plain_hub.stats["last_call_info"] == empty
    assert got == [(C.EVENT_CALL_INFO, empty)]


def test_new_phonebook_fires_once_per_version(plain_hub, monkeypatch):
    monkeypatch.setattr(R, "SIP_USER", "1234")
    got = _events(plain_hub)
    plain_hub._handle_incoming_message("NEW_PHONEBOOK;v1;g1")
    plain_hub._handle_incoming_message("NEW_PHONEBOOK;v1;g1")  # same version: no event
    plain_hub._handle_incoming_message("NEW_PHONEBOOK")        # no version: event, ver None
    assert got == [
        (C.EVENT_PHONEBOOK_CHANGED, {"gid": "g1", "rubrica_ver": "v1"}),
        (C.EVENT_PHONEBOOK_CHANGED, {"gid": "1234", "rubrica_ver": None}),
    ]


def test_a_nicks_reply_without_entries_is_ignored(plain_hub):
    plain_hub._handle_incoming_message("GET_NICKS_REPLY;[]")
    assert "nicknames" not in plain_hub.stats and plain_hub._nicks_seq == 0


def test_an_unknown_message_changes_nothing(plain_hub):
    before = dict(plain_hub.stats)
    plain_hub._handle_incoming_message("SOMETHING_NEW;{}")
    assert plain_hub.stats == before


@pytest.mark.parametrize("ok, msg, outcome", [
    (True, "200 OK", "exists"),
    (False, "404 Not Found", "absent"),
    (False, "Timeout", "no_response"),
    (False, "500 Server Error", "error"),
    (False, "", "error"),
])
def test_probe_outcomes(ok, msg, outcome):
    assert hub_mod.VimarIntercomHub._probe_outcome(ok, msg) == outcome


def test_the_last_message_is_redacted_and_parsed(plain_hub):
    plain_hub._update_stats("message", json.dumps({"x": 1}))
    assert plain_hub.stats["last_message_in"] == '{"x": 1}'
