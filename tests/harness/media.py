"""Media vero della targa finta: H.264 generato da ffmpeg (+ PCMU) in RTP o SRTP a ritmo
reale, e ffprobe per contare i fotogrammi decodificabili che escono da /av."""
from __future__ import annotations

import asyncio
import json
import os
import random
import shutil
import socket
import struct
import subprocess
import tempfile
import time

from custom_components.vimar_intercom.srtp import SRTPContext

# FFMPEG=<cartella con ffmpeg e ffprobe> (o il percorso di ffmpeg): messa in testa al
# PATH, anche per l'integrazione. Serve dove il primo «ffmpeg» in PATH è uno shim che
# lancia il vero ffmpeg come figlio (Chocolatey su Windows): kill() uccide lo shim, il
# vero ffmpeg resta vivo e la pipe di /av non chiude mai. Vedi CONTRIBUTING.md.
if _ff := os.environ.get("FFMPEG"):
    os.environ["PATH"] = (os.path.dirname(_ff) if os.path.isfile(_ff) else _ff) + os.pathsep + os.environ["PATH"]
FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")

def access_units(gop: int = 15) -> list[list[bytes]]:
    """20 s di testsrc 320x240@15, baseline, SPS/PPS a ogni IDR (come la targa); `gop`
    fotogrammi fra un IDR e l'altro (45 = ogni 3 s, come la targa)."""
    clip = os.path.join(tempfile.gettempdir(), f"vimar_test_testsrc{'' if gop == 15 else f'_g{gop}'}.h264")
    if not os.path.exists(clip):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc=size=320x240:rate=15", "-t", "20", "-threads", "1",
                        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-profile:v", "baseline",
                        "-g", str(gop), "-keyint_min", str(gop), "-x264-params", "repeat-headers=1",
                        "-f", "h264", clip + ".part"], check=True)
        os.replace(clip + ".part", clip)
    with open(clip, "rb") as f:
        data = f.read()
    nals = [n.lstrip(b"\x00") for n in data.split(b"\x00\x00\x01") if n.strip(b"\x00")]
    nals = [n[:-1] if n.endswith(b"\x00") else n for n in nals]
    aus, cur = [], []
    for n in nals:
        cur.append(n)
        if n[0] & 0x1F in (1, 5):
            aus.append(cur)
            cur = []
    return aus


class PanelMedia:
    """RTP della targa: video H.264 (FU-A oltre 1100 B) + PCMU a 15 fps, opzionalmente
    SRTP. `start()` di nuovo = encoder riavviato: SSRC, seq e timestamp nuovi."""

    def __init__(self, key: str | None = None, seq0: int | None = None, pt: int = 96, gop: int = 15,
                 stray_ahead: int = 0, stray_at: int = 1, stray_count: int = 1):
        self.key, self.seq0, self.pt = key, seq0, pt
        # After the `stray_at`-th video packet, `stray_count` more in a row (filler
        # NALs) numbered from `stray_ahead` ahead on the same SSRC, as a relay or a
        # panel resending old packets does.
        self.stray_ahead, self.stray_at, self.stray_count = stray_ahead, stray_at, stray_count
        self.aus = access_units(gop)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.task: asyncio.Task | None = None
        self.seq = 0
        self.idr_at: float | None = None  # epoca (time.time) del primo IDR mandato
        self.sent = 0                     # pacchetti video mandati (o persi)
        self.drop: set[int] = set()       # indici (in `sent`) dei pacchetti persi per strada

    def start(self, vaddr, aaddr) -> PanelMedia:
        self.stop()
        self.task = asyncio.create_task(self._run(vaddr, aaddr))
        return self

    def stop(self):
        if self.task:
            self.task.cancel()
            self.task = None

    async def _run(self, vaddr, aaddr):
        srtp_v = SRTPContext(self.key) if self.key else None
        srtp_a = SRTPContext(self.key) if self.key else None
        self.seq = self.seq0 if self.seq0 is not None else random.randint(0, 65535)
        aseq, ssrc, assrc = random.randint(0, 65535), random.getrandbits(32), random.getrandbits(32)
        ts, ats = random.getrandbits(32), random.getrandbits(32)
        loop = asyncio.get_running_loop()
        t0, i = loop.time(), 0
        self.idr_at = None
        try:
            while True:
                pkts = []
                au = self.aus[i % len(self.aus)]
                if self.idr_at is None and au[-1][0] & 0x1F == 5:
                    self.idr_at = time.time()
                for n in au:
                    if len(n) <= 1100:
                        pkts.append(n)
                        continue
                    hdr, body = n[0], n[1:]
                    for off in range(0, len(body), 1100):
                        s, e = off == 0, off + 1100 >= len(body)
                        pkts.append(bytes([(hdr & 0xE0) | 28, (0x80 if s else 0) | (0x40 if e else 0)
                                           | (hdr & 0x1F)]) + body[off:off + 1100])
                for k, p in enumerate(pkts):
                    m = 0x80 if k == len(pkts) - 1 else 0
                    rtp = struct.pack("!BBHII", 0x80, m | self.pt, self.seq, ts, ssrc) + p
                    self.seq = (self.seq + 1) & 0xFFFF
                    self.sent += 1
                    if self.sent - 1 not in self.drop:
                        self.sock.sendto(srtp_v.protect(rtp) if srtp_v else rtp, vaddr)
                    if self.sent == self.stray_at and self.stray_ahead:
                        for j in range(self.stray_count):
                            seq = (self.seq + self.stray_ahead + j) & 0xFFFF
                            rtp = struct.pack("!BBHII", 0x80, self.pt, seq, ts, ssrc) + b"\x0c\x00"
                            self.sock.sendto(srtp_v.protect(rtp) if srtp_v else rtp, vaddr)
                for _ in range(3):  # ~67 ms di PCMU a pacchetti da 20 ms (circa)
                    rtp = struct.pack("!BBHII", 0x80, 0, aseq, ats, assrc) + b"\xff" * 160
                    aseq, ats = (aseq + 1) & 0xFFFF, (ats + 160) & 0xFFFFFFFF
                    self.sock.sendto(srtp_a.protect(rtp) if srtp_a else rtp, aaddr)
                ts = (ts + 6000) & 0xFFFFFFFF
                i += 1
                await asyncio.sleep(max(0.0, t0 + i / 15 - loop.time()))
        except (asyncio.CancelledError, OSError):
            pass


def decodable_frames(ts: bytes) -> int:
    """Fotogrammi video che ffprobe riesce a decodificare da un MPEG-TS."""
    if len(ts) < 188:
        return 0
    fd, path = tempfile.mkstemp(suffix=".ts")
    os.write(fd, bytes(ts))
    os.close(fd)
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                              "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", path],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        first = out.split()[0] if out else ""
        return int(first) if first.isdigit() else 0
    finally:
        os.unlink(path)


def audio_info(ts: bytes) -> dict:
    """Traccia audio di un MPEG-TS vista da ffprobe (codec_name, sample_rate, channels) e
    nb_read_frames decodificati: ciò che lo stream worker di HA vede con PyAV. Vuoto se
    ffprobe non riconosce un audio (PCMU in TS = bin_data)."""
    fd, path = tempfile.mkstemp(suffix=".ts")
    os.write(fd, bytes(ts))
    os.close(fd)
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "a:0",
                              "-show_entries", "stream=codec_name,sample_rate,channels,nb_read_frames",
                              "-of", "json", path], capture_output=True, text=True, timeout=60).stdout
        streams = json.loads(out or "{}").get("streams") or [{}]
        return streams[0]
    finally:
        os.unlink(path)


def clip_info(path: str) -> tuple[str, float, int]:
    """(codec video, durata in s, fotogrammi decodificabili) di un file secondo ffprobe."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                          "-show_entries", "stream=codec_name,nb_read_frames:format=duration",
                          "-of", "default=nw=1:nk=1", path], capture_output=True, text=True, timeout=60).stdout.split()
    codec, frames, duration = (out + ["", "0", "0"])[:3]
    return codec, float(duration or 0), int(frames) if frames.isdigit() else 0


def clip_audio_codec(path: str) -> str:
    """Codec audio (o "" se non c'è traccia audio) di un file secondo ffprobe."""
    return subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                           "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
                          capture_output=True, text=True, timeout=60).stdout.strip()


def luma_means(ts: bytes) -> list[float]:
    """Luminanza media di ogni fotogramma di un MPEG-TS (per distinguere lo standby
    scuro dal testsrc della targa), nell'ordine in cui escono dal decoder."""
    out = subprocess.run(["ffmpeg", "-v", "error", "-f", "mpegts", "-i", "pipe:0", "-an",
                          "-vf", "scale=16:16", "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"],
                         input=bytes(ts), capture_output=True, timeout=60).stdout
    return [sum(out[i:i + 256]) / 256 for i in range(0, len(out) - 255, 256)]


def frame_sizes(ts: bytes) -> set[str]:
    """«larghezza,altezza» di ogni fotogramma video: un solo valore = niente cambio di
    parametri a metà stream."""
    out = subprocess.run(["ffprobe", "-v", "error", "-f", "mpegts", "-select_streams", "v:0",
                          "-show_entries", "frame=width,height", "-of", "csv=p=0", "-i", "pipe:0"],
                         input=bytes(ts), capture_output=True, timeout=60).stdout.decode()
    return {line.strip().rstrip(",") for line in out.splitlines() if line.strip()}


def free_even_port_pair() -> int:
    """Porta pari con la successiva libera (ffmpeg apre RTP e RTCP = RTP+1). Fuori dal
    range effimero: Windows riassegna subito una porta appena liberata al primo socket
    che fa sendto senza bind, e ffmpeg la troverebbe presa."""
    for _ in range(200):
        p = random.randrange(20000, 30000, 2)
        socks = []
        try:
            for q in (p, p + 1):
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                socks.append(s)
                s.bind(("0.0.0.0", q))
            return p
        except OSError:
            continue
        finally:
            for s in socks:
                s.close()
    raise RuntimeError("nessuna coppia di porte libera")
