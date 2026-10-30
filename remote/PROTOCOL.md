# Handheld remote ↔ Pi protocol

The handheld remote (ESP32-S3 with a 1.69" touch screen and a Beitian BK-880
GPS) tells the cart's Pi where the golfer is and lets them stop the trolley.
Version 1, as built by `remote/src/main.cpp` and `server/remote.py`.

## Network

- The Pi runs its own Wi-Fi hotspot (`pi/hotspot.sh`, NetworkManager shared
  mode). There is no other Wi-Fi on the course. The Pi is always `10.42.0.1`;
  the remote gets an address from the Pi's DHCP.
- UDP, Pi port **8091**. The Pi listens on `10.42.0.1` only, and drops
  datagrams from outside `10.42.0.0/24`.
- UDP, not TCP: a lost packet is simply replaced by the next one a second
  later, and nothing ever blocks waiting for a retransmit.

## Datagram format

```
<mac> <json>
```

- `json`: one JSON object, UTF-8, at most 400 bytes.
- `mac`: the first 16 bytes of HMAC-SHA256(key, json bytes), as 32 lowercase
  hex characters, then one space.
- The **pairing key** is 16 random bytes (32 hex characters), made by
  `pi/hotspot.sh` and stored on the Pi in `/var/lib/golf-map/remote.key`.
  The remote keeps it in flash (see README "Pairing"). A datagram with a
  wrong or missing MAC is dropped without a reply, so a device without the
  key can't drive the cart, fake the golfer's position, or keep a link alive.
- Both directions are signed with the same key.

## Messages

Every message has `"k"` (kind). Remote → Pi:

| k | When | Fields |
|---|---|---|
| `hello` | every second until welcomed | `n`: 8 hex chars, random each time |
| `hb` | every second once welcomed | `sid`, `seq`, `fix`, `bat` |
| `stop` | STOP pressed: 5 copies, 100 ms apart | `sid` (if any), `seq` |

- `hb` is the heartbeat **and** the position report, so a position can never
  arrive without proof the link is alive.
- `seq`: counts up from 1 within a session, one per `hb` or `stop`.
- `fix`: `null` with no fix, else
  `{"lat": 41.2, "lon": -73.2, "acc": 4.5, "q": 1, "sats": 9}`:
  - `acc`: estimated horizontal error in metres. The BK-880's default NMEA has
    no error estimate (no GST sentence), so this is HDOP × 2.5 m, which is
    rough.
  - `q`: the GGA fix quality: 1 = GPS, 2 = DGPS/SBAS. (0 is sent as `null`.)
  - `sats`: satellites used.
- `bat`: remote battery, 0 to 100 percent, or `null` on USB power without a
  battery.

Pi → remote:

| k | Reply to | Fields |
|---|---|---|
| `welcome` | `hello` | `n` (echoed), `sid`: 16 hex chars |
| `ack` | `hb`, `stop` | `seq` (echoed), `trolley`: `none`, `idle`, `driving` or `stopped` |
| `rehello` | `hb` with an unknown `sid` | (nothing) |

## Sessions and replay

A recorded `hb` replayed later must not keep a link (and, later, follow mode)
alive after the real remote has died. So:

1. The remote sends `hello` with a fresh random nonce `n`.
2. The Pi answers `welcome` with that `n` and a new random `sid`. It keeps the
   four newest unused `sid`s pending.
3. The remote checks the MAC and that `n` is its own, then sends `hb`s
   with that `sid` and `seq` 1, 2, 3, ...
4. The Pi accepts an `hb` only if its `sid` is the active session's and its
   `seq` is higher than the last one, or its `sid` is pending (that session
   then becomes the active one and the others are dropped).
5. A `sid` the Pi doesn't know (it restarted, or the session was replaced) gets
   `rehello`, and the remote starts again at step 1.

A replayed `hello` only gets a `welcome` that nobody can use without the key.

`stop` with a valid MAC is always obeyed, with or without a session or a
fresh `seq`: stopping is always safe, so a replayed `stop` can do no harm.

## Link state

- Pi: **linked** while a valid `hb` arrived in the last **3 s**; then **lost**.
  Losing the link must stop any follow mode (not built yet) at once. It is
  the same rule as the screen's trolley heartbeat (`trolley.py`).
- Remote: shows **Linked** while an `ack` arrived in the last 3 s.
- After 3 s without a reply the remote keeps sending `hb`; after 10 s it goes
  back to `hello`.

## STOP

- The remote's STOP button acts on touch-down and sends 5 `stop`s 100 ms apart
  (UDP may drop one; one is enough).
- The Pi stops the trolley (`TrolleyGate.command`, logged in
  `trolley-commands.jsonl` as from the remote) and answers each with `ack`.
- The remote shows "STOP sent" until an `ack` says `stopped`, then "Stopped".

## For follow-me (not built yet)

Two consumer GPS receivers (the cart's and the remote's) can each be several
metres off, in different directions, and drift as satellites change. Their
difference is noisier than either: 5 to 10 m is normal under trees. So
following needs at least:

- a **minimum gap** (say 8 to 10 m) inside which the trolley does not move,
- a **speed cap** at walking pace, and no chasing a sudden jump in the
  remote's position,
- **STOP on link loss** (3 s without an `hb`), on a stale or missing `fix`, or
  on the remote's `acc` getting too large,
- the keep-out map and gate checks every other trolley move goes through.
