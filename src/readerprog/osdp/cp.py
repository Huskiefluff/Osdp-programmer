"""OSDP control panel (CP) side: talk to readers on one RS-485 line."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import constants as C
from . import decode as D
from .packet import Packet, PacketReader, build_packet
from .secure import SecureChannelError, SecureSession
from .transport import Transport

LogFunc = Callable[[str, str], None]  # (kind, text) kind in {"tx","rx","info","error"}


class OsdpError(Exception):
    pass


class NoResponse(OsdpError):
    pass


class NakError(OsdpError):
    def __init__(self, code: int, data: bytes, command: int):
        self.code = code
        self.data = data
        self.command = command
        super().__init__(f"{C.command_name(command)} rejected: {D.decode_nak(data)}")


class ProtocolError(OsdpError):
    pass


@dataclass
class Reply:
    code: int
    data: bytes
    packet: Packet

    @property
    def name(self) -> str:
        return C.reply_name(self.code)


@dataclass
class ReaderEvent:
    kind: str           # "card", "keypad", "status", "other"
    text: str
    card: Optional[D.CardRead] = None


class Bus:
    """One serial line. Handles framing, timing and echo suppression."""

    def __init__(self, transport: Transport, *, use_mark: bool = True, log: Optional[LogFunc] = None):
        self.transport = transport
        self.use_mark = use_mark
        self.log: LogFunc = log or (lambda kind, text: None)
        self._reader = PacketReader()

    @property
    def baud(self) -> int:
        return self.transport.baud

    def set_baud(self, baud: int) -> None:
        if baud != self.transport.baud:
            self.transport.set_baud(baud)
            self.log("info", f"Port baud rate set to {baud}")

    def exchange(self, raw: bytes, address: int, timeout: float) -> Optional[Packet]:
        """Send one packet and wait for the matching reply packet."""
        self._reader.reset()
        try:
            self.transport.flush_input()
        except Exception:
            pass
        frame = (bytes([C.MARK]) if self.use_mark else b"") + raw
        self.log("tx", raw.hex(" ").upper())
        self.transport.write(frame)
        # Allow for the time it takes to clock our own bytes out of the adapter.
        tx_time = len(frame) * 10.0 / max(self.transport.baud, 1)
        deadline = time.monotonic() + timeout + tx_time
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                for err in self._reader.errors:
                    self.log("error", f"Discarded bad frame: {err}")
                self._reader.errors.clear()
                return None
            chunk = self.transport.read(min(remaining, 0.05))
            if not chunk:
                if getattr(self.transport, "synchronous", False):
                    return None  # simulator answers instantly or never
                continue
            for pkt in self._reader.feed(chunk):
                if not pkt.is_reply:
                    continue  # our own echo on a 2-wire adapter
                if address != C.BROADCAST_ADDR and pkt.address != address:
                    self.log("info", f"Ignoring reply from address {pkt.address}")
                    continue
                self.log("rx", pkt.raw.hex(" ").upper())
                return pkt


class Reader:
    """One OSDP peripheral device (reader) at a given address on a Bus."""

    def __init__(self, bus: Bus, address: int, *, use_crc: bool = True,
                 timeout: float = 0.3, retries: int = 2):
        self.bus = bus
        self.address = address
        self.use_crc = use_crc
        self.timeout = timeout
        self.retries = retries
        self._sqn = 0
        self.secure: Optional[SecureSession] = None

    # ------------------------------------------------------------------
    # Low level
    # ------------------------------------------------------------------
    def reset_link(self) -> None:
        """Next command uses sequence 0, which also resets any secure session."""
        self._sqn = 0
        self.secure = None

    @property
    def secure_active(self) -> bool:
        return bool(self.secure and self.secure.active)

    def _advance_sqn(self) -> None:
        self._sqn = 1 if self._sqn >= 3 else self._sqn + 1

    def _build(self, code: int, data: bytes, scb: Optional[bytes]) -> bytes:
        sess = self.secure if self.secure_active else None
        if sess is None:
            return build_packet(self.address, code, data, sqn=self._sqn, use_crc=self.use_crc, scb=scb)
        if data:
            payload = sess.encrypt_command_data(data)
            scb = bytes([2, C.SCS_17])
        else:
            payload = b""
            scb = bytes([2, C.SCS_15])
        return build_packet(self.address, code, payload, sqn=self._sqn, use_crc=self.use_crc,
                            scb=scb, mac_func=sess.mac_command)

    def _unwrap(self, pkt: Packet, command: int) -> Reply:
        sess = self.secure if self.secure_active else None
        if sess is None or pkt.scb is None or pkt.scb_type not in (C.SCS_16, C.SCS_18):
            if sess is not None and pkt.code == C.REPLY_NAK:
                # Reader dropped out of secure mode (e.g. it was power cycled).
                self.secure = None
                return Reply(pkt.code, pkt.data, pkt)
            if sess is not None and pkt.code not in (C.REPLY_CCRYPT, C.REPLY_RMAC_I):
                self.secure = None
                raise ProtocolError("Reader replied without secure channel protection; session closed")
            return Reply(pkt.code, pkt.data, pkt)
        expected = sess.mac_reply(pkt.mac_input)[:4]
        if pkt.mac != expected:
            self.secure = None
            raise ProtocolError("Secure channel MAC check failed; session closed")
        data = pkt.data
        if pkt.scb_type == C.SCS_18 and data:
            data = sess.decrypt_reply_data(data)
        return Reply(pkt.code, data, pkt)

    def command(self, code: int, data: bytes = b"", *, scb: Optional[bytes] = None,
                timeout: Optional[float] = None, raise_nak: bool = True) -> Reply:
        raw = self._build(code, data, scb)
        tmo = self.timeout if timeout is None else timeout
        pkt = None
        for attempt in range(self.retries + 1):
            if attempt:
                self.bus.log("info", f"No reply, retry {attempt} of {self.retries}")
            pkt = self.bus.exchange(raw, self.address, tmo)
            if pkt is not None:
                break
        if pkt is None:
            raise NoResponse(
                f"No response from address {self.address} at {self.bus.baud} baud "
                f"to {C.command_name(code)}")
        if pkt.sqn != self._sqn and self.address != C.BROADCAST_ADDR:
            self.bus.log("error", f"Reply sequence {pkt.sqn} != expected {self._sqn}")
        self._advance_sqn()
        reply = self._unwrap(pkt, code)
        if reply.code == C.REPLY_BUSY:
            time.sleep(0.2)
            return self.command(code, data, scb=scb, timeout=timeout, raise_nak=raise_nak)
        if reply.code == C.REPLY_NAK and raise_nak:
            raise NakError(reply.data[0] if reply.data else 0, reply.data, code)
        return reply

    # ------------------------------------------------------------------
    # Standard commands
    # ------------------------------------------------------------------
    def poll(self) -> list[ReaderEvent]:
        reply = self.command(C.CMD_POLL)
        return self._events_from(reply)

    def _events_from(self, reply: Reply) -> list[ReaderEvent]:
        if reply.code == C.REPLY_ACK:
            return []
        if reply.code == C.REPLY_RAW:
            card = D.decode_raw(reply.data)
            return [ReaderEvent("card", card.describe(), card)]
        if reply.code == C.REPLY_FMT:
            card = D.decode_fmt(reply.data)
            return [ReaderEvent("card", card.describe(), card)]
        if reply.code == C.REPLY_KEYPAD:
            return [ReaderEvent("keypad", D.decode_keypad(reply.data))]
        if reply.code == C.REPLY_LSTATR:
            return [ReaderEvent("status", D.decode_lstatr(reply.data).describe())]
        if reply.code == C.REPLY_RSTATR:
            return [ReaderEvent("status", "Reader status: " + ", ".join(D.decode_rstatr(reply.data)))]
        return [ReaderEvent("other", f"{reply.name}: {reply.data.hex(' ').upper()}")]

    def identify(self) -> D.DeviceId:
        reply = self.command(C.CMD_ID, b"\x00")
        self._expect(reply, C.REPLY_PDID)
        return D.decode_pdid(reply.data)

    def capabilities(self) -> list[D.Capability]:
        reply = self.command(C.CMD_CAP, b"\x00")
        self._expect(reply, C.REPLY_PDCAP)
        return D.decode_pdcap(reply.data)

    def local_status(self) -> D.LocalStatus:
        reply = self.command(C.CMD_LSTAT)
        self._expect(reply, C.REPLY_LSTATR)
        return D.decode_lstatr(reply.data)

    def reader_status(self) -> list[str]:
        reply = self.command(C.CMD_RSTAT)
        self._expect(reply, C.REPLY_RSTATR)
        return D.decode_rstatr(reply.data)

    def led(self, **kwargs) -> None:
        self._expect(self.command(C.CMD_LED, D.encode_led(**kwargs)), C.REPLY_ACK)

    def buzzer(self, **kwargs) -> None:
        self._expect(self.command(C.CMD_BUZ, D.encode_buzzer(**kwargs)), C.REPLY_ACK)

    def comset(self, address: int, baud: int) -> D.ComSettings:
        """Send osdp_COMSET. The reader switches to the new settings after replying."""
        reply = self.command(C.CMD_COMSET, D.encode_comset(address, baud))
        self._expect(reply, C.REPLY_COM)
        return D.decode_com(reply.data)

    def keyset(self, key: bytes) -> None:
        if not self.secure_active:
            raise OsdpError("A secure channel session is required before installing a key")
        self._expect(self.command(C.CMD_KEYSET, D.encode_keyset(key)), C.REPLY_ACK)

    def manufacturer(self, oui: bytes, payload: bytes) -> Reply:
        return self.command(C.CMD_MFG, bytes(oui[:3]) + payload, raise_nak=False)

    @staticmethod
    def _expect(reply: Reply, code: int) -> None:
        if reply.code != code:
            raise ProtocolError(f"Expected {C.reply_name(code)} but got {reply.name} "
                                f"{reply.data.hex(' ').upper()}")

    # ------------------------------------------------------------------
    # Secure channel
    # ------------------------------------------------------------------
    def start_secure_channel(self, scbk: Optional[bytes] = None) -> None:
        """Open a secure channel. ``None`` means the default install key SCBK-D."""
        key = scbk if scbk is not None else C.SCBK_DEFAULT
        use_default = key == C.SCBK_DEFAULT
        self.secure = None
        session = SecureSession(key, os.urandom(8))
        flag = 0x00 if use_default else 0x01

        reply = self.command(C.CMD_CHLNG, session.rnd_a, scb=bytes([3, C.SCS_11, flag]), raise_nak=False)
        if reply.code == C.REPLY_NAK:
            raise SecureChannelError(
                f"Reader refused the secure channel challenge ({D.decode_nak(reply.data)}). "
                + ("It is probably not in install mode / already has a custom key."
                   if use_default else "It may not support secure channel or may need the default key."))
        self._expect(reply, C.REPLY_CCRYPT)
        if len(reply.data) < 32:
            raise SecureChannelError("osdp_CCRYPT reply too short")
        rnd_b = reply.data[8:16]
        if reply.data[16:32] != session.client_cryptogram(rnd_b):
            raise SecureChannelError(
                "Key mismatch: the reader's cryptogram does not match the "
                + ("default install key (SCBK-D)." if use_default else "key you entered."))

        server_cryptogram = session.server_cryptogram(rnd_b)
        reply = self.command(C.CMD_SCRYPT, server_cryptogram, scb=bytes([3, C.SCS_13, flag]), raise_nak=False)
        if reply.code == C.REPLY_NAK:
            raise SecureChannelError(f"Reader rejected our cryptogram ({D.decode_nak(reply.data)})")
        self._expect(reply, C.REPLY_RMAC_I)
        pkt = reply.packet
        if pkt.scb_data[:1] not in (b"\x01",):
            raise SecureChannelError("Reader did not accept the secure channel cryptogram")
        if reply.data[:16] != session.initial_rmac(server_cryptogram):
            raise SecureChannelError("Initial R-MAC mismatch")
        session.establish(rnd_b, server_cryptogram)
        self.secure = session
        self.bus.log("info", "Secure channel established ("
                     + ("default install key" if use_default else "custom key") + ")")


# ======================================================================
# Discovery helpers
# ======================================================================
SCAN_BAUD_ORDER = (9600, 115200, 38400, 19200, 57600, 230400)


@dataclass
class FoundReader:
    address: int
    baud: int
    device_id: Optional[D.DeviceId] = None
    notes: list[str] = field(default_factory=list)

    def describe(self) -> str:
        text = f"Reader found at address {self.address}, {self.baud} baud"
        if self.device_id:
            text += f"\n{self.device_id.describe()}"
        for note in self.notes:
            text += f"\n{note}"
        return text


ProgressFunc = Callable[[str, float], None]
CancelFunc = Callable[[], bool]


def probe(bus: Bus, address: int, baud: int, timeout: float = 0.25) -> Optional[Packet]:
    """Single POLL at address/baud with sequence 0. Returns the reply packet or None."""
    bus.set_baud(baud)
    raw = build_packet(address, C.CMD_POLL, b"", sqn=0, use_crc=True)
    return bus.exchange(raw, address, timeout)


def scan(
    bus: Bus,
    *,
    bauds: tuple[int, ...] = SCAN_BAUD_ORDER,
    addresses: Optional[range] = None,
    timeout: float = 0.2,
    use_broadcast: bool = True,
    stop_at_first: bool = True,
    progress: Optional[ProgressFunc] = None,
    cancelled: Optional[CancelFunc] = None,
) -> list[FoundReader]:
    """Find readers on the line.

    First tries the broadcast address at every baud rate (fast; needs only one
    reader connected). Then walks addresses 0..126 at the baud rate(s) that
    answered, or at every baud rate if nothing answered the broadcast.
    """
    progress = progress or (lambda text, frac: None)
    cancelled = cancelled or (lambda: False)
    addresses = addresses if addresses is not None else range(0, C.MAX_ADDR + 1)
    found: list[FoundReader] = []
    live_bauds: list[int] = []

    if use_broadcast:
        for i, baud in enumerate(bauds):
            if cancelled():
                return found
            progress(f"Broadcast check at {baud} baud", i / (len(bauds) * 4))
            if probe(bus, C.BROADCAST_ADDR, baud, timeout) is not None:
                live_bauds.append(baud)
                if stop_at_first:
                    break

    search_bauds = live_bauds or list(bauds)
    total = len(search_bauds) * len(addresses)
    step = 0
    for baud in search_bauds:
        for addr in addresses:
            if cancelled():
                return found
            step += 1
            if step % 4 == 1:
                progress(f"Checking address {addr} at {baud} baud", 0.25 + 0.75 * step / total)
            pkt = probe(bus, addr, baud, timeout)
            if pkt is None:
                continue
            reader = FoundReader(addr, baud)
            r = Reader(bus, addr, timeout=max(timeout, 0.3))
            try:
                reader.device_id = r.identify()
            except OsdpError as exc:
                reader.notes.append(f"(identify failed: {exc})")
            found.append(reader)
            if stop_at_first:
                progress("Reader found", 1.0)
                return found
    progress("Scan complete", 1.0)
    return found
