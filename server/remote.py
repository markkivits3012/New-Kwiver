"""The handheld remote's link: a UDP listener on the Pi's hotspot, and a mock
remote for testing on a PC. The wire format and the session rules are in
remote/PROTOCOL.md; keep the two in step.

The remote sends a signed heartbeat with its GPS fix every second, and STOP.
The listener checks every datagram against the pairing key (remote.key in the
state folder, made by pi/hotspot.sh), keeps the newest status for the
Settings screen, and passes STOP to the trolley gate.
"""
import hashlib
import hmac
import ipaddress
import json
import math
import os
import secrets
import socket
import threading
import time

PORT = 8091
HOTSPOT_IP = "10.42.0.1"          # NetworkManager shared mode; pi/hotspot.sh sets it
HOTSPOT_NET = ipaddress.ip_network("10.42.0.0/24")
LINK_TIMEOUT_S = 3.0              # same as the trolley's screen heartbeat
MAX_PENDING = 4
MAX_JSON = 400
KEY_FILE = "remote.key"


def sign(key, obj):
    body = json.dumps(obj, separators=(",", ":")).encode()
    return hmac.new(key, body, hashlib.sha256).hexdigest()[:32].encode() + b" " + body


def verify(key, data):
    """-> the message dict, or None if it isn't one signed with key."""
    mac, _, body = data.partition(b" ")
    if len(mac) != 32 or not 0 < len(body) <= MAX_JSON:
        return None
    good = hmac.new(key, body, hashlib.sha256).hexdigest()[:32].encode()
    if not hmac.compare_digest(mac, good):
        return None
    try:
        msg = json.loads(body)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None


def read_key(path):
    try:
        with open(path, encoding="ascii") as f:
            k = bytes.fromhex(f.read().strip())
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return k if len(k) == 16 else None


def _num(v, lo, hi):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and lo <= v <= hi


def clean_fix(f):
    """The remote's fix if every field is in range, else None."""
    if not (isinstance(f, dict) and _num(f.get("lat"), -90, 90) and _num(f.get("lon"), -180, 180)):
        return None
    acc, q, sats = f.get("acc"), f.get("q"), f.get("sats")
    return {"lat": f["lat"], "lon": f["lon"],
            "acc": acc if _num(acc, 0, 10000) else None,
            "q": q if q in (1, 2, 3, 4, 5) else None,
            "sats": sats if _num(sats, 0, 99) else None}


class RemoteLink:
    """Listens for the remote. status() is what the Settings card shows."""

    def __init__(self, key_path, bind=HOTSPOT_IP, port=PORT, on_stop=None, trolley_state=None,
                 any_source=False, key=None):
        self.key_path, self.bind, self.port = key_path, bind, port
        self.on_stop = on_stop or (lambda: None)
        self.trolley_state = trolley_state or (lambda: "none")
        self.any_source = any_source      # the PC mock talks over 127.0.0.1
        self.fixed_key = key
        self.lock = threading.Lock()
        self.sid, self.last_seq, self.pending = None, 0, []
        self.st = {"state": "off", "seen": None, "addr": None, "fix": None, "bat": None,
                   "stop_t": None, "mock": key is not None}
        threading.Thread(target=self._run, daemon=True).start()

    def status(self):
        with self.lock:
            s = dict(self.st)
        if s["state"] == "linked" and time.time() - s["seen"] > LINK_TIMEOUT_S:
            s["state"] = "lost"  # follow mode, once built, must stop here
        s["t"] = time.time()
        return s

    def _set(self, **kw):
        with self.lock:
            self.st.update(kw)

    def _run(self):
        """Wait for a key and the hotspot address, then serve forever."""
        while True:
            key = self.fixed_key or read_key(self.key_path)
            if not key:
                self._set(state="off")      # not paired: run pi/hotspot.sh
                time.sleep(5)
                continue
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind((self.bind, self.port))
            except OSError:
                sock.close()
                self._set(state="nohotspot")  # hotspot not up (yet)
                time.sleep(5)
                continue
            if self.st["state"] in ("off", "nohotspot"):
                self._set(state="waiting")
            try:
                self._serve(sock, key)
            except OSError:
                pass
            sock.close()
            time.sleep(1)

    def _serve(self, sock, key):
        while True:
            data, addr = sock.recvfrom(1024)
            if not (self.any_source or ipaddress.ip_address(addr[0]) in HOTSPOT_NET):
                continue
            msg = verify(key, data)
            if msg is None:
                continue
            reply = self._handle(msg, addr)
            if reply:
                sock.sendto(sign(key, reply), addr)

    def _handle(self, msg, addr):
        k = msg.get("k")
        if k == "stop":
            # always obeyed, even replayed or without a session: stopping is safe
            self.on_stop()
            self._set(stop_t=time.time())
            return {"k": "ack", "seq": msg.get("seq"), "trolley": self.trolley_state()}
        if k == "hello":
            n = msg.get("n")
            if not (isinstance(n, str) and len(n) == 8):
                return None
            sid = secrets.token_hex(8)
            with self.lock:
                self.pending = (self.pending + [sid])[-MAX_PENDING:]
            return {"k": "welcome", "n": n, "sid": sid}
        if k == "hb":
            sid, seq = msg.get("sid"), msg.get("seq")
            if not _num(seq, 1, 2 ** 53):
                return None
            with self.lock:
                if sid in self.pending:
                    self.sid, self.last_seq, self.pending = sid, 0, []
                if sid != self.sid:
                    return {"k": "rehello"}
                if seq <= self.last_seq:
                    return None  # replayed or out of order
                self.last_seq = seq
                bat = msg.get("bat")
                self.st.update(state="linked", seen=time.time(), addr=addr[0],
                               fix=clean_fix(msg.get("fix")),
                               bat=round(bat) if _num(bat, 0, 100) else None)
            return {"k": "ack", "seq": seq, "trolley": self.trolley_state()}
        return None


class MockRemote:
    """Pretend remote for the PC: speaks the real protocol to the listener on
    127.0.0.1, walking a few metres beside the cart with a slowly draining
    battery."""

    def __init__(self, key, port, cart_fix):
        self.key, self.port, self.cart_fix = key, port, cart_fix
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.5)
        to = ("127.0.0.1", self.port)
        sid, seq, bat, t0 = None, 0, 96.0, time.time()
        while True:
            try:
                if sid is None:
                    n = secrets.token_hex(4)
                    sock.sendto(sign(self.key, {"k": "hello", "n": n}), to)
                else:
                    seq += 1
                    sock.sendto(sign(self.key, {"k": "hb", "sid": sid, "seq": seq,
                                                "fix": self._fix(time.time() - t0), "bat": round(bat)}), to)
                msg = verify(self.key, sock.recvfrom(1024)[0])
                if msg and msg.get("k") == "welcome" and sid is None and msg.get("n") == n:
                    sid, seq = msg["sid"], 0
                elif msg and msg.get("k") == "rehello":
                    sid = None
            except OSError:  # listener not up yet, or no reply
                pass
            bat = max(5.0, bat - 0.01)
            time.sleep(1)

    def _fix(self, t):
        f = self.cart_fix()
        if not f:
            return None
        # ~6 m from the cart, circling slowly, like a golfer walking beside it
        a = t / 20
        return {"lat": round(f["lat"] + 6 * math.cos(a) / 111320, 7),
                "lon": round(f["lon"] + 6 * math.sin(a) / (111320 * math.cos(math.radians(f["lat"]))), 7),
                "acc": 3.8, "q": 1, "sats": 9}


def mock_key():
    return secrets.token_bytes(16)
