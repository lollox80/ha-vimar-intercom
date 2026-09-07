"""_update_stats del hub: parsing degli annunci VOICEMAIL/DND e (futuro) GET_INIT_STATUS_REPLY."""
import pytest

hub_mod = pytest.importorskip("custom_components.vimar_intercom.hub")


@pytest.fixture
def hub(monkeypatch):
    h = hub_mod.VimarIntercomHub()
    # evita callback/side effects
    monkeypatch.setattr(h, "_touch", lambda: None)
    return h


def test_voicemail_on_off(hub):
    hub._update_stats("message", "VOICEMAIL;ON")
    assert hub.stats["voicemail"] is True
    hub._update_stats("message", "VOICEMAIL;OFF")
    assert hub.stats["voicemail"] is False


def test_dnd_on_off(hub):
    hub._update_stats("message", "DND;ON")
    assert hub.stats["dnd"] is True
    hub._update_stats("message", "DND;OFF")
    assert hub.stats["dnd"] is False


def test_get_init_status_reply(hub):
    body = ('GET_INIT_STATUS_REPLY;[{"PARAM":"dnd","VALUE":"1"},{"PARAM":"voicemail","VALUE":"0"},'
            '{"PARAM":"token","VALUE":"abc123"},{"PARAM":"rubrica_ver","VALUE":"7"}]')
    hub._update_stats("message", body)
    assert hub.stats["dnd"] is True
    assert hub.stats["voicemail"] is False
    assert hub.stats["init_status"].get("token") == "abc123"
    assert str(hub.stats.get("rubrica_ver")) == "7"


def test_get_init_status_reply_real_impianto(hub):
    """Reply 'corta' verificata sul campo 20/08: rubrica_ver/vm_ver/vm_level/dnd/voicemail."""
    body = ('GET_INIT_STATUS_REPLY;[{"PARAM":"rubrica_ver","VALUE":"deadbeef"},'
            '{"PARAM":"vm_ver","VALUE":"cafe1234"},{"PARAM":"vm_level","VALUE":"0/100"},'
            '{"PARAM":"dnd","VALUE":"0"},{"PARAM":"voicemail","VALUE":"1"}]')
    hub._update_stats("message", body)
    assert hub.stats["voicemail"] is True
    assert hub.stats["dnd"] is False
    assert hub.stats["vm_level"] == "0/100"
    assert hub.stats["vm_ver"] == "cafe1234"
    assert hub.stats["rubrica_ver"] == "deadbeef"


def test_get_init_status_reply_truncated(hub):
    """Body troncato (sip_client tronca a 200 char): il parser regex estrae comunque
    le coppie complete presenti senza crashare."""
    body = ('GET_INIT_STATUS_REPLY;[{"PARAM":"dnd","VALUE":"1"},{"PARAM":"voicemail","VALUE":"0"},'
            '{"PARAM":"rubrica_ver","VALUE":"ab')  # troncato a metà
    hub._update_stats("message", body)
    assert hub.stats["dnd"] is True
    assert hub.stats["voicemail"] is False


def test_rubrica_ver_change_fires_event(hub):
    events = []
    hub.register_event_callback(lambda et, data: events.append((et, data)))
    from custom_components.vimar_intercom import const as C
    hub._update_stats("message",
                      'GET_INIT_STATUS_REPLY;[{"PARAM":"rubrica_ver","VALUE":"v1"}]')
    assert not events  # primo valore: nessun evento
    hub._update_stats("message",
                      'GET_INIT_STATUS_REPLY;[{"PARAM":"rubrica_ver","VALUE":"v2"}]')
    assert events and events[-1][0] == C.EVENT_PHONEBOOK_CHANGED
    assert events[-1][1]["rubrica_ver"] == "v2"


def test_missed_call_event(hub):
    from custom_components.vimar_intercom import const as C
    events = []
    hub.register_event_callback(lambda et, data: events.append((et, data)))
    hub._update_stats("message", 'MISSED_CALL;{"SIP_ID":"55100","TS":1692000000}')
    assert hub.stats["missed_call_count"] == 1
    assert hub.stats["last_missed_call"]["sip_id"] == "55100"
    assert events[-1][0] == C.EVENT_MISSED_CALL
    assert events[-1][1]["ts"] == 1692000000


def test_videomessage_event(hub):
    from custom_components.vimar_intercom import const as C
    events = []
    hub.register_event_callback(lambda et, data: events.append((et, data)))
    hub._update_stats("message", "VM;VIDEO_MESSAGE_CHANGE;NEW;1")
    assert hub.stats["new_videomessage"] is True
    assert events[-1][0] == C.EVENT_VIDEOMESSAGE
    assert events[-1][1]["change"] == "NEW"
    hub._update_stats("message", "VM;VIDEO_MESSAGE_CHANGE;UPDATE")
    assert hub.stats["new_videomessage"] is False


def test_fuoriporta_and_call_info(hub):
    from custom_components.vimar_intercom import const as C
    events = []
    hub.register_event_callback(lambda et, data: events.append((et, data)))
    hub._update_stats("message", 'FP;{"SIP_ID":"55100","MSG":"OPEN"}')
    assert events[-1][0] == C.EVENT_FUORIPORTA
    assert hub.stats["last_fuoriporta"]["msg"] == "OPEN"
    hub._update_stats("message", 'CALL_INFO;{"SIP_ID":"55100","REASON":"ring","MEDIA_TYPE":2,"VIDEO_SRC":"55100"}')
    assert events[-1][0] == C.EVENT_CALL_INFO
    assert hub.stats["last_call_info"]["media_type"] == 2


def test_new_phonebook_event(hub):
    from custom_components.vimar_intercom import const as C
    events = []
    hub.register_event_callback(lambda et, data: events.append((et, data)))
    hub._update_stats("message", "NEW_PHONEBOOK;101;v9")
    assert events[-1][0] == C.EVENT_PHONEBOOK_CHANGED
    assert events[-1][1] == {"gid": "101", "rubrica_ver": "v9"}
    assert hub.stats["rubrica_ver"] == "v9"


def test_unmapped_message_no_crash(hub):
    hub._update_stats("message", "SOMETHING_UNKNOWN;foo;bar")
    hub._update_stats("message", "")
