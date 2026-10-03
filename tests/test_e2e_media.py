"""End-to-end col media vero: la targa manda H.264 (e PCMU) in RTP/SRTP a ritmo reale,
/av passa dall'ffmpeg vero dietro un aiohttp vero, ffprobe conta i fotogrammi che
escono. Solo i casi in cui il bug era nel media; il resto è in test_e2e_sip.py.

    PYTHONPATH=<aiohttp> python -m pytest -m media
"""
from __future__ import annotations

import asyncio
import time

import pytest
from harness.media import PanelMedia, audio_info, decodable_frames, frame_sizes, luma_means
from harness.peer import is_
from harness.rig import Rig, our_media_addrs, run, wait_until
from harness.web import AvClient

from custom_components.vimar_intercom import av_passive, av_stream, frame_grabber
from custom_components.vimar_intercom import media_handler as media

pytestmark = pytest.mark.media


def test_cloud_srtp_rollover_e_foto_decodificabili(monkeypatch):
    """Cloud TLS+SRTP, la sequenza RTP parte a ridosso di 65535 (rollover SRTP dentro la
    chiamata). "Vedi esterno", la targa chiude dopo 4 s: fin lì video decodificabile, poi
    /av finisce e non si richiama. Foto dal frame grabber durante la chiamata."""
    async def s():
        async with Rig(monkeypatch, "tls", real_av=True, http=True, srtp=True) as rig:
            await rig.register()
            rig.answer(media_on=True, bye_after=4, seq0=65400)
            ok, msg = await rig.hub.async_call()
            assert ok, msg
            av = AvClient(rig.base).start()
            jpeg = await frame_grabber.wait_frame(8)
            assert jpeg and jpeg[:2] == b"\xff\xd8", "nessuna foto dalla chiamata"
            await wait_until(lambda: rig.hub.status == "idle", 10, "BYE della targa")
            srtp_fail = media.video_proto._srtp_fail
            await asyncio.sleep(2)
            await av.close()
            segs = [decodable_frames(x) for x in av.segments]
            assert segs and segs[0] >= 15, f"segmento senza video: {segs}"
            assert srtp_fail == 0, f"SRTP fail {srtp_fail}"
            assert 503 in av.statuses and len(rig.peer.got(is_("INVITE"))) == 1, av.statuses
    run(s())


STRAY_BURST = 5  # strays on the 40515 came alone or 3 at a time; 5 used to pass as a jump


def test_av_survives_a_burst_of_packets_600_ahead_mid_call(monkeypatch):
    """STRAY_BURST packets 600 ahead on the same SSRC, ~2 s into a call with /av
    open. ffmpeg's RTP demuxer accepts a jump under 3000, emits them after
    max_delay as the new head and drops every live packet as "too late" until
    the numbers pass them: ~20 s of frozen camera stream, Frigate and Scrypted
    included."""
    async def s():
        async with Rig(monkeypatch, real_av=True, http=True) as rig:
            await rig.register()

            def start_media(our_sdp, seq0=None):
                if rig.panel_media:
                    rig.panel_media.stop()
                rig.panel_media = PanelMedia(rig.peer.key, 1000, rig.peer.pt, stray_ahead=600,
                                             stray_at=40, stray_count=STRAY_BURST)
                rig.panel_media.start(*our_media_addrs(our_sdp))
            rig.start_media = start_media
            rig.answer(media_on=True)
            assert (await rig.hub.async_call())[0]
            av = AvClient(rig.base, reconnect=False).start()
            await wait_until(lambda: av.bytes > 20000, 10, "video su /av")
            await asyncio.sleep(7)
            await rig.hub.async_hangup()
            await asyncio.wait_for(av.task, 5)
            n = decodable_frames(av.segments[0])
            assert n >= 75, f"/av: {n} decodable frames in ~8 s after the burst (expected ~110)"
    run(s())


def test_cloud_srtp_ring_preview_survives_a_packet_far_ahead(monkeypatch):
    """Real ring on a cloud TLS+SRTP 40515 (2 Oct): the preview stopped after the
    first packets, so no ring photo, a black card and no voice. One authentic
    packet numbered far ahead on the same SSRC moved the replay window there,
    and every packet of the live stream after it was refused as too old."""
    from harness.media import PanelMedia

    async def s():
        async with Rig(monkeypatch, "tls", real_av=True, srtp=True) as rig:
            await rig.register()
            rig.ring()
            r183 = await rig.peer.wait_for(is_(code=183))
            rig.panel_media = PanelMedia(rig.peer.key, 1000, rig.peer.pt, stray_ahead=20000)
            rig.start_media(r183.body)
            jpeg = await frame_grabber.wait_frame(8)
            assert jpeg and jpeg[:2] == b"\xff\xd8", "no ring photo: the preview stopped"
            assert media.video_proto._srtp_fail == 0
    run(s())


def test_anteprima_poi_risposta_senza_riavviare_ffmpeg(monkeypatch):
    """Squillo con anteprima: la targa offre lei (PT 99, non il 96 dell'SDP di ffmpeg).
    /av mostra l'anteprima, si risponde: stesso ffmpeg, video che continua."""
    async def s():
        async with Rig(monkeypatch, real_av=True, http=True) as rig:
            await rig.register()
            rig.peer.pt = 99
            rig.ring()
            r183 = await rig.peer.wait_for(is_(code=183))
            rig.start_media(r183.body)
            av = AvClient(rig.base, reconnect=False).start()
            await wait_until(lambda: av.bytes > 20000, 10, "anteprima su /av")
            proc = av_stream.av_ffmpeg_proc
            assert (await rig.hub.async_answer())[0]
            n = av.bytes
            await asyncio.sleep(3)
            assert av_stream.av_ffmpeg_proc is proc, "ffmpeg di /av riavviato alla risposta"
            assert av.bytes > n + 10000, "/av fermo dopo la risposta"
            await rig.hub.async_hangup()
            await asyncio.wait_for(av.task, 5)
            assert decodable_frames(av.segments[0]) >= 40
            assert not [m for m in rig.peer.got(is_("INVITE")) if m.cid != "ring-1"], "auto-call"
    run(s())


def test_av_audio_aac_per_stream_worker_e_homekit(monkeypatch):
    """L'audio della targa è PCMU: in MPEG-TS con «-c:a copy» finiva come bin_data e lo
    stream worker di HA (HLS, camera.record) e HomeKit non lo vedevano. /av lo
    transcodifica in AAC (48 kHz mono, come la PR #21 ma a 24): ffprobe lo riconosce e ne
    decodifica i fotogrammi, come fa lo stream worker con PyAV. Il video resta «copy»."""
    async def s():
        async with Rig(monkeypatch, real_av=True, http=True) as rig:
            await rig.register()
            rig.answer(media_on=True)
            assert (await rig.hub.async_call())[0]
            av = AvClient(rig.base).start()
            await wait_until(lambda: av.bytes > 20000, 10, "video su /av")
            await asyncio.sleep(2)
            await rig.hub.async_hangup()
            await av.close()
            info = audio_info(av.segments[0])
            assert (info.get("codec_name"), info.get("sample_rate"), info.get("channels")) == ("aac", "48000", 1), info
            assert int(info.get("nb_read_frames", 0)) >= 20, f"audio AAC non decodificabile: {info}"
            assert decodable_frames(av.segments[0]) >= 15
    run(s())


def test_seconda_chiamata_video_e_foto_prima_dell_sps_in_banda(monkeypatch):
    """Dal campo (40515 via cloud): dopo il 200 OK il primo RTP porta PPS+IDR, l'SPS
    solo ogni ~6 s, e la targa chiude dopo ~10 s: ffmpeg di /av («non-existing PPS 0
    referenced») aspettava l'SPS, ~3,5 s di video visti. Seconda chiamata senza mai
    un SPS in banda: /av e la foto devono uscire lo stesso, con SPS/PPS della prima.
    Chiude la targa (come sul campo): il suo media si ferma prima del BYE."""
    async def s():
        async with Rig(monkeypatch, real_av=True, http=True) as rig:
            await rig.register()
            rig.answer(media_on=True, bye_after=3)
            assert (await rig.hub.async_call())[0]
            av = AvClient(rig.base).start()
            await wait_until(lambda: av.bytes > 20000, 10, "video su /av")
            await wait_until(lambda: rig.hub.status == "idle", 5, "BYE della targa")
            await wait_until(lambda: 503 in av.statuses, 5, "/av chiuso a fine chiamata")
            rig.panel_media.aus = [[n for n in au if n[0] & 0x1F != 7] for au in rig.panel_media.aus]
            assert (await rig.hub.async_call())[0]
            jpeg = await frame_grabber.wait_frame(4)
            await wait_until(lambda: rig.hub.status == "idle", 5, "BYE della targa")
            await av.close()
            assert jpeg and jpeg[:2] == b"\xff\xd8", "nessuna foto senza SPS in banda"
            assert len(av.segments) == 2, av.statuses
            n = decodable_frames(av.segments[1])
            assert n >= 15, f"seconda chiamata: {n} fotogrammi decodificabili senza SPS in banda"
    run(s())


def test_av_passivo_come_go2rtc_anteprima_vera_e_mai_un_invite(monkeypatch):
    """go2rtc/Frigate su /av?autocall=0, in ciclo: a riposo 503 dietro l'aiohttp vero;
    allo squillo l'anteprima decodificabile; al CANCEL lo stream finisce e i retry
    tornano 503. Mai un INVITE nostro."""
    async def s():
        async with Rig(monkeypatch, real_av=True, http=True) as rig:
            await rig.register()
            av = AvClient(rig.base, path="/api/vimar_intercom/av?autocall=0").start()
            await wait_until(lambda: av.statuses.count(503) >= 3, 5, "503 a riposo")
            rig.ring()
            r183 = await rig.peer.wait_for(is_(code=183))
            rig.start_media(r183.body)
            await wait_until(lambda: av.bytes > 20000, 10, "anteprima su /av passivo")
            n503 = av.statuses.count(503)
            rig.peer.request("CANCEL", "ring-1", 1, "pnl")
            await wait_until(lambda: av.statuses.count(503) > n503, 10, "/av chiuso al CANCEL")
            await av.close()
            assert len(av.segments) == 1 and decodable_frames(av.segments[0]) >= 15
            assert not [m for m in rig.peer.got(is_("INVITE")) if m.cid != "ring-1"], "auto-call"
            assert rig.hub._stream_viewers == 0
    run(s())


def test_av_passivo_continuo_standby_live_standby_sulla_stessa_connessione(monkeypatch):
    """/av?autocall=0&idle=image (Frigate, Scrypted): un solo MPEG-TS che non finisce mai.
    A riposo lo standby scuro; allo squillo il video della targa entro ~2 s sulla stessa
    connessione; al CANCEL di nuovo lo standby. Parametri video costanti, nessun INVITE
    nostro, nessuno spettatore per l'hub, decoder e ffmpeg di /av fermi a riposo."""
    async def s():
        async with Rig(monkeypatch, real_av=True, http=True) as rig:
            await rig.register()
            av = AvClient(rig.base, reconnect=False,
                          path="/api/vimar_intercom/av?autocall=0&idle=image").start()
            await wait_until(lambda: av.bytes > 0, 10, "standby su /av continuo")
            t_first = time.monotonic()
            await asyncio.sleep(2)
            assert av_stream.av_ffmpeg_proc is None and not av_stream._av_clients, "decoder a riposo"
            rig.ring()
            r183 = await rig.peer.wait_for(is_(code=183))
            rig.start_media(r183.body)
            t_ring = time.monotonic()
            await asyncio.sleep(4)
            rig.peer.request("CANCEL", "ring-1", 1, "pnl")
            await wait_until(lambda: rig.hub.status == "idle", 5, "fine dello squillo")
            await wait_until(lambda: av_stream.av_ffmpeg_proc is None and not av_stream._av_clients,
                             5, "decoder staccato a fine squillo")
            await asyncio.sleep(3)
            assert not av.task.done() and av.statuses == [200], "connessione caduta"
            await av.close()
            await wait_until(lambda: av_passive._task is None, 5, "encoder fermato con l'ultimo client")
            ts = av.segments[0]
            means = luma_means(ts)
            assert len(means) >= 80, f"solo {len(means)} fotogrammi in ~11 s"
            assert frame_sizes(ts) == {f"{av_passive.W},{av_passive.H}"}
            a = audio_info(ts)  # AAC continuo: silenzio a riposo, la targa durante lo squillo
            assert a.get("codec_name") == "aac" and int(a.get("nb_read_frames") or 0) > 300, a
            standby = means[0]
            live = [i for i, m in enumerate(means) if abs(m - standby) > 20]
            assert live, "mai il video della targa"
            first = live[0] / av_passive.FPS - (t_ring - t_first)
            assert first < 3, f"video live dopo {first:.1f} s dallo squillo (live0={live[0]} n={len(means)} dt={t_ring - t_first:.2f})"
            assert all(abs(m - standby) <= 20 for m in means[-15:]), "non torna allo standby"
            assert not [m for m in rig.peer.got(is_("INVITE")) if m.cid != "ring-1"], "auto-call"
            assert rig.hub._stream_viewers == 0
    run(s())


def test_av_passivo_continuo_un_encoder_per_tutti(monkeypatch):
    """Due client (Frigate e Scrypted) condividono l'encoder: gli stessi byte, un solo
    ffmpeg; il primo che se ne va non lo ferma, l'ultimo sì."""
    async def s():
        async with Rig(monkeypatch, real_av=True, http=True) as rig:
            await rig.register()
            path = "/api/vimar_intercom/av?autocall=0&idle=image"
            a = AvClient(rig.base, reconnect=False, path=path).start()
            await wait_until(lambda: a.bytes > 0, 10, "primo client")
            b = AvClient(rig.base, reconnect=False, path=path).start()
            await wait_until(lambda: b.bytes > 0, 10, "secondo client")
            task = av_passive._task
            assert task and not task.done() and len(av_passive._clients) == 2
            await asyncio.sleep(1.5)
            await a.close()
            await asyncio.sleep(0.5)
            assert av_passive._task is task and not task.done() and len(av_passive._clients) == 1
            n = b.bytes
            await asyncio.sleep(1)
            assert b.bytes > n, "il secondo client non riceve più"
            await b.close()
            await wait_until(lambda: av_passive._task is None, 5, "encoder fermato")
            assert not rig.peer.got(is_("INVITE"))
    run(s())


def test_encoder_riavviato_a_meta_chiamata_av_continua(monkeypatch):
    """La targa riparte a metà chiamata (SSRC, seq e timestamp nuovi, seq "indietro"):
    ffmpeg scartava tutto come «received too late» e /av restava fermo."""
    async def s():
        async with Rig(monkeypatch, real_av=True, http=True) as rig:
            await rig.register()
            rig.answer(media_on=True)
            assert (await rig.hub.async_call())[0]
            av = AvClient(rig.base, reconnect=False).start()
            await wait_until(lambda: av.bytes > 20000, 10, "video su /av")
            await asyncio.sleep(1)
            n1 = decodable_frames(av.segments[0])
            inv = rig.peer.got(is_("INVITE"))[0]
            rig.start_media(inv.body, seq0=(rig.panel_media.seq - 1000) & 0xFFFF)
            await asyncio.sleep(4)
            n2 = decodable_frames(av.segments[0])
            assert n2 >= n1 + 40, f"/av fermo dopo il riavvio dell'encoder: {n1} → {n2}"
            await rig.hub.async_hangup()
            await av.close()
    run(s())


def test_av_esce_anche_senza_audio_rtp(monkeypatch):
    """Il maintainer, con un SDP PCMU+H264 e solo video: «copy» ~89 KB, «aac» 0 byte,
    perché l'encoder non parte senza il primo pacchetto audio (183 muta, SRTP audio
    che fallisce). /av deve uscire lo stesso, con video decodificabile."""
    from harness.media import PanelMedia
    start = PanelMedia.start  # l'audio della targa va a una porta chiusa: solo video
    monkeypatch.setattr(PanelMedia, "start", lambda self, v, a: start(self, v, ("127.0.0.1", 9)))

    async def s():
        async with Rig(monkeypatch, real_av=True, http=True) as rig:
            await rig.register()
            rig.answer(media_on=True)
            assert (await rig.hub.async_call())[0]
            av = AvClient(rig.base).start()
            await wait_until(lambda: av.bytes > 20000, 10, "video su /av senza audio")
            await rig.hub.async_hangup()
            await av.close()
            assert decodable_frames(av.segments[0]) >= 10
    run(s())
