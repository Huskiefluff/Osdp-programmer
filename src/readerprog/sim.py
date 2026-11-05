"""A simulated OSDP reader for demo mode and automated tests.

It behaves like a Veridt Stealth-series OSDP reader as described in Veridt
Technical Note 123: factory address 1 at 9600 baud, and a baud rate change
sent with osdp_COMSET is only kept across a power cycle when the address is
changed at the same time. It never touches real hardware.
"""

from __future__ import annotations

import os
import threading
from typing import Optional

from .osdp import constants as C
from .osdp import decode as D
from .osdp.packet import Packet, PacketReader, build_packet
from .osdp.secure import SecureChannelError, SecureSession

SIM_VENDOR = bytes([0x5A, 0x5A, 0x5A])  # obviously fake vendor code


def h10301_bits(facility: int, card: int) -> str:
    body = f"{facility:08b}{card:016b}"
    left, right = body[:12], body[12:]
    p_even = "1" if left.count("1") % 2 else "0"
    p_odd = "0" if right.count("1") % 2 else "1"
    return p_even + body + p_odd


def fascn_200_bits(agency: str, system: str, credential: str, cs: str = "1", ici: str = "1",
                   pi: str = "1234567890", oc: str = "1", oi: str = "1234", poa: str = "1") -> str:
    """Encode a 200-bit FASC-N (used to exercise the PIV decoder)."""
    def char(value: int) -> str:
        bits = "".join(str((value >> i) & 1) for i in range(4))
        return bits + ("0" if bits.count("1") % 2 else "1")

    SS, FS, ES = 0x0B, 0x0D, 0x0F
    seq = [SS] + [int(c) for c in agency] + [FS] + [int(c) for c in system] + [FS]
    seq += [int(c) for c in credential] + [FS] + [int(cs)] + [FS] + [int(ici)] + [FS]
    seq += [int(c) for c in pi + oc + oi + poa] + [ES]
    lrc = 0
    for v in seq:
        lrc ^= v
    seq.append(lrc)
    return "".join(char(v) for v in seq)


def bits_to_raw_reply(bits: str, reader: int = 0, fmt: int = 0) -> bytes:
    n = len(bits)
    padded = bits + "0" * (-n % 8)
    data = int(padded, 2).to_bytes(len(padded) // 8, "big") if padded else b""
    return bytes([reader, fmt, n & 0xFF, n >> 8]) + data


class SimulatedReader:
    def __init__(self, address: int = 1, baud: int = 9600, serial_number: int = 12345678,
                 scbk: Optional[bytes] = None, name: str = "Simulated Stealth reader"):
        self.name = name
        self.address = address
        self.baud = baud
        self.saved_address = address
        self.saved_baud = baud
        self.scbk = scbk               # None = factory/install mode (accepts SCBK-D)
        self.serial_number = serial_number
        self.tamper = False
        self.last_led: Optional[bytes] = None
        self.last_text = ""
        self.inputs = [0, 0]
        self.outputs = [0]
        self.mfg_received: list[bytes] = []
        self.beeps = 0
        self.powered = True
        self._session: Optional[SecureSession] = None
        self._pending_session: Optional[SecureSession] = None
        self._last_sqn: Optional[int] = None
        self._last_reply: Optional[bytes] = None
        self._cards: list[bytes] = []
        self._rx = PacketReader()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def present_card(self, bits: str, fmt: int = 0) -> None:
        with self._lock:
            self._cards.append(bits_to_raw_reply(bits, 0, fmt))

    def power_cycle(self) -> None:
        """Reload saved settings, applying the Veridt default-ID behaviour."""
        with self._lock:
            self.address = self.saved_address
            self.baud = 9600 if self.saved_address == 1 else self.saved_baud
            self._session = None
            self._pending_session = None
            self._last_sqn = None
            self._rx.reset()

    @property
    def secure_active(self) -> bool:
        return bool(self._session and self._session.active)

    # ------------------------------------------------------------------
    def receive(self, data: bytes, line_baud: int) -> bytes:
        """Feed bytes seen on the wire; return bytes the reader transmits."""
        with self._lock:
            if not self.powered:
                return b""
            if line_baud != self.baud:
                self._rx.reset()  # framing garbage at the wrong speed
                return b""
            out = b""
            for pkt in self._rx.feed(data):
                if pkt.is_reply:
                    continue
                if pkt.address not in (self.address, C.BROADCAST_ADDR):
                    continue
                reply = self._handle(pkt)
                if reply:
                    out += reply
            return out

    # ------------------------------------------------------------------
    def _reply(self, pkt: Packet, code: int, data: bytes = b"", scb: Optional[bytes] = None) -> bytes:
        addr = C.BROADCAST_ADDR if pkt.address == C.BROADCAST_ADDR else self.address
        sess = self._session if self.secure_active else None
        if sess is not None and scb is None:
            if data:
                data = sess.encrypt_reply_data(data)
                scb = bytes([2, C.SCS_18])
            else:
                scb = bytes([2, C.SCS_16])
            raw = build_packet(addr, code, data, sqn=pkt.sqn, use_crc=pkt.use_crc, scb=scb,
                               is_reply=True, mac_func=sess.mac_reply)
        else:
            raw = build_packet(addr, code, data, sqn=pkt.sqn, use_crc=pkt.use_crc, scb=scb, is_reply=True)
        self._last_reply = raw
        self._last_sqn = pkt.sqn
        return raw

    def _nak(self, pkt: Packet, reason: int) -> bytes:
        self._session = None
        raw = build_packet(self.address, C.REPLY_NAK, bytes([reason]), sqn=pkt.sqn,
                           use_crc=pkt.use_crc, is_reply=True)
        self._last_reply = raw
        self._last_sqn = pkt.sqn
        return raw

    def _handle(self, pkt: Packet) -> bytes:
        if pkt.sqn == 0:
            self._session = None
            self._pending_session = None
        elif pkt.sqn == self._last_sqn and self._last_reply is not None:
            return self._last_reply  # CP retransmission: repeat our last answer

        data = pkt.data
        if self.secure_active:
            if pkt.scb_type in (C.SCS_15, C.SCS_17):
                sess = self._session
                assert sess is not None
                if sess.mac_command(pkt.mac_input)[:4] != pkt.mac:
                    return self._nak(pkt, 0x05)
                if pkt.scb_type == C.SCS_17 and data:
                    try:
                        data = sess.decrypt_command_data(data)
                    except SecureChannelError:
                        return self._nak(pkt, 0x05)
            else:
                self._session = None  # plaintext message ends the session
        elif pkt.scb_type in (C.SCS_15, C.SCS_17):
            return self._nak(pkt, 0x05)

        code = pkt.code
        if code == C.CMD_POLL:
            if self._cards:
                return self._reply(pkt, C.REPLY_RAW, self._cards.pop(0))
            return self._reply(pkt, C.REPLY_ACK)
        if code == C.CMD_ID:
            body = (SIM_VENDOR + bytes([1, 1]) + self.serial_number.to_bytes(4, "little") + bytes([2, 3, 0]))
            return self._reply(pkt, C.REPLY_PDID, body)
        if code == C.CMD_CAP:
            caps = [(1, 1, 2), (2, 1, 1), (3, 1, 0), (4, 4, 1), (5, 1, 1), (8, 1, 0), (9, 1, 1), (10, 0x00, 0x04),
                    (11, 0x00, 0x04), (13, 0, 1), (16, 2, 0)]
            return self._reply(pkt, C.REPLY_PDCAP, b"".join(bytes(c) for c in caps))
        if code == C.CMD_LSTAT:
            return self._reply(pkt, C.REPLY_LSTATR, bytes([int(self.tamper), 0]))
        if code == C.CMD_RSTAT:
            return self._reply(pkt, C.REPLY_RSTATR, bytes([0]))
        if code == C.CMD_LED:
            self.last_led = data
            return self._reply(pkt, C.REPLY_ACK)
        if code == C.CMD_BUZ:
            self.beeps += 1
            return self._reply(pkt, C.REPLY_ACK)
        if code == C.CMD_COMSET:
            if len(data) < 5:
                return self._nak(pkt, 0x02)
            settings = D.decode_com(data)
            if settings.address > C.MAX_ADDR:
                return self._nak(pkt, 0x09)
            reply = self._reply(pkt, C.REPLY_COM, data[:5])
            if settings.address != self.address:
                self.saved_baud = settings.baud  # Veridt: baud only saved with an ID change
            self.saved_address = settings.address
            self.address = settings.address
            self.baud = settings.baud
            self._session = None
            self._last_sqn = None
            return reply
        if code == C.CMD_CHLNG:
            return self._handle_chlng(pkt)
        if code == C.CMD_SCRYPT:
            return self._handle_scrypt(pkt)
        if code == C.CMD_KEYSET:
            if not self.secure_active:
                return self._nak(pkt, 0x06)
            if len(data) != 18 or data[0] != C.KEY_TYPE_SCBK or data[1] != 16:
                return self._nak(pkt, 0x02)
            self.scbk = bytes(data[2:18])
            return self._reply(pkt, C.REPLY_ACK)
        if code == C.CMD_ISTAT:
            return self._reply(pkt, C.REPLY_ISTATR, bytes(self.inputs))
        if code == C.CMD_OSTAT:
            return self._reply(pkt, C.REPLY_OSTATR, bytes(self.outputs))
        if code == C.CMD_OUT:
            if len(data) < 4 or len(data) % 4:
                return self._nak(pkt, 0x02)
            for i in range(0, len(data), 4):
                num, action = data[i], data[i + 1]
                if num >= len(self.outputs):
                    return self._nak(pkt, 0x09)
                if action in (2, 4, 5):
                    self.outputs[num] = 1
                elif action in (1, 3, 6):
                    self.outputs[num] = 0
            return self._reply(pkt, C.REPLY_ACK)
        if code == C.CMD_TEXT:
            if len(data) < 6:
                return self._nak(pkt, 0x02)
            self.last_text = bytes(data[6:6 + data[5]]).decode("ascii", "replace")
            return self._reply(pkt, C.REPLY_ACK)
        if code in (C.CMD_ACURXSIZE, C.CMD_KEEPACTIVE, C.CMD_ABORT):
            return self._reply(pkt, C.REPLY_ACK)
        if code == C.CMD_MFG:
            # The simulator only understands its own (fake) vendor code and simply
            # echoes the payload back, like a manufacturer "ping".
            if len(data) >= 3 and bytes(data[:3]) == SIM_VENDOR:
                self.mfg_received.append(bytes(data[3:]))
                return self._reply(pkt, C.REPLY_MFGREP, SIM_VENDOR + bytes(data[3:]))
            return self._nak(pkt, 0x03)
        return self._nak(pkt, 0x03)

    def _handle_chlng(self, pkt: Packet) -> bytes:
        if pkt.scb_type != C.SCS_11 or len(pkt.data) != 8:
            return self._nak(pkt, 0x05)
        use_default = pkt.scb_data[:1] == b"\x00"
        if use_default and self.scbk is not None:
            return self._nak(pkt, 0x05)  # not in install mode any more
        if not use_default and self.scbk is None:
            return self._nak(pkt, 0x05)
        key = C.SCBK_DEFAULT if use_default else self.scbk
        assert key is not None
        sess = SecureSession(key, pkt.data)
        rnd_b = os.urandom(8)
        sess.rnd_b = rnd_b
        self._pending_session = sess
        cuid = SIM_VENDOR + self.serial_number.to_bytes(4, "little") + b"\x00"
        body = cuid + rnd_b + sess.client_cryptogram(rnd_b)
        return self._reply(pkt, C.REPLY_CCRYPT, body, scb=bytes([3, C.SCS_12, 0 if use_default else 1]))

    def _handle_scrypt(self, pkt: Packet) -> bytes:
        sess = self._pending_session
        if sess is None or pkt.scb_type != C.SCS_13 or len(pkt.data) != 16:
            return self._nak(pkt, 0x05)
        if pkt.data != sess.server_cryptogram(sess.rnd_b):
            self._pending_session = None
            return self._nak(pkt, 0x05)
        sess.establish(sess.rnd_b, pkt.data)
        reply = self._reply(pkt, C.REPLY_RMAC_I, sess.r_mac, scb=bytes([3, C.SCS_14, 0x01]))
        self._session = sess
        self._pending_session = None
        return reply


class SimulatedTransport:
    """In-memory RS-485 line with one or more simulated readers attached."""

    synchronous = True

    def __init__(self, readers: Optional[list[SimulatedReader]] = None, baud: int = 9600, echo: bool = False):
        self.readers = readers if readers is not None else [SimulatedReader()]
        self.baud = baud
        self.echo = echo
        self._pending = bytearray()
        self.port_name = "SIMULATOR"

    def write(self, data: bytes) -> None:
        if self.echo:
            self._pending.extend(data)
        replies = [r.receive(data, self.baud) for r in self.readers]
        replies = [r for r in replies if r]
        if len(replies) == 1:
            self._pending.extend(replies[0])
        elif len(replies) > 1:
            # Two readers answering at once collide on the wire.
            merged = bytes(a | b for a, b in zip(replies[0], replies[1]))
            self._pending.extend(merged)

    def read(self, timeout: float) -> bytes:
        out = bytes(self._pending)
        self._pending.clear()
        return out

    def set_baud(self, baud: int) -> None:
        self.baud = baud

    def flush_input(self) -> None:
        self._pending.clear()

    def close(self) -> None:
        pass
