"""Building and parsing OSDP packets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from . import constants as C
from .crc import checksum8, crc16

MAX_PACKET_LEN = 2048  # generous upper bound used only for resync sanity
MAC_LEN = 4

MacFunc = Callable[[bytes], bytes]


class PacketError(Exception):
    pass


@dataclass
class Packet:
    address: int
    is_reply: bool
    sqn: int
    use_crc: bool
    code: int
    data: bytes = b""
    scb: Optional[bytes] = None   # whole security control block (len, type, data...)
    mac: Optional[bytes] = None   # 4 MAC bytes when present
    raw: bytes = field(default=b"", repr=False)

    @property
    def scb_type(self) -> Optional[int]:
        return self.scb[1] if self.scb else None

    @property
    def scb_data(self) -> bytes:
        return self.scb[2:] if self.scb else b""

    @property
    def mac_input(self) -> bytes:
        """Bytes covered by the MAC: SOM through the end of the data field."""
        end = len(self.raw) - (2 if self.use_crc else 1) - (MAC_LEN if self.mac else 0)
        return self.raw[:end]

    def name(self) -> str:
        return C.reply_name(self.code) if self.is_reply else C.command_name(self.code)


def scb_has_mac(scb_type: Optional[int]) -> bool:
    return scb_type is not None and C.SCS_15 <= scb_type <= C.SCS_18


def build_packet(
    address: int,
    code: int,
    data: bytes = b"",
    sqn: int = 0,
    use_crc: bool = True,
    scb: Optional[bytes] = None,
    is_reply: bool = False,
    mac_func: Optional[MacFunc] = None,
) -> bytes:
    """Return a complete packet (without the optional leading 0xFF mark).

    ``mac_func`` receives the bytes from SOM through the end of data and must
    return the MAC bytes to append (first 4 bytes are used).
    """
    if not 0 <= address <= C.BROADCAST_ADDR:
        raise ValueError(f"address {address} out of range 0..127")
    if not 0 <= sqn <= 3:
        raise ValueError("sqn must be 0..3")
    if mac_func is not None and not (scb and scb_has_mac(scb[1])):
        raise ValueError("mac_func requires an SCB of type 0x15..0x18")

    ctrl = (sqn & C.CTRL_SQN_MASK) | (C.CTRL_CRC if use_crc else 0) | (C.CTRL_SCB if scb else 0)
    body = bytes(scb or b"") + bytes([code]) + bytes(data)
    total = 5 + len(body) + (MAC_LEN if mac_func else 0) + (2 if use_crc else 1)
    addr_byte = (address | C.REPLY_FLAG) if is_reply else address
    pkt = bytes([C.SOM, addr_byte, total & 0xFF, (total >> 8) & 0xFF, ctrl]) + body
    if mac_func:
        pkt += bytes(mac_func(pkt)[:MAC_LEN])
    if use_crc:
        crc = crc16(pkt)
        pkt += bytes([crc & 0xFF, crc >> 8])
    else:
        pkt += bytes([checksum8(pkt)])
    assert len(pkt) == total
    return pkt


def parse_packet(raw: bytes) -> Packet:
    """Parse one complete packet (must start at SOM). Raises PacketError."""
    if len(raw) < 7 or raw[0] != C.SOM:
        raise PacketError("too short or missing SOM")
    total = raw[2] | (raw[3] << 8)
    if total != len(raw):
        raise PacketError(f"length field {total} != {len(raw)} bytes")
    ctrl = raw[4]
    use_crc = bool(ctrl & C.CTRL_CRC)
    if use_crc:
        expect = crc16(raw[:-2])
        got = raw[-2] | (raw[-1] << 8)
        if expect != got:
            raise PacketError(f"CRC mismatch (got {got:04X}, expected {expect:04X})")
        end = len(raw) - 2
    else:
        if checksum8(raw[:-1]) != raw[-1]:
            raise PacketError("checksum mismatch")
        end = len(raw) - 1

    pos = 5
    scb = None
    if ctrl & C.CTRL_SCB:
        scb_len = raw[pos]
        if scb_len < 2 or pos + scb_len >= end:
            raise PacketError("bad security block length")
        scb = raw[pos:pos + scb_len]
        pos += scb_len
    mac = None
    if scb is not None and scb_has_mac(scb[1]):
        if end - MAC_LEN <= pos:
            raise PacketError("packet too short for MAC")
        mac = raw[end - MAC_LEN:end]
        end -= MAC_LEN
    if pos >= end:
        raise PacketError("missing command/reply code")
    code = raw[pos]
    data = raw[pos + 1:end]
    return Packet(
        address=raw[1] & 0x7F,
        is_reply=bool(raw[1] & C.REPLY_FLAG),
        sqn=ctrl & C.CTRL_SQN_MASK,
        use_crc=use_crc,
        code=code,
        data=bytes(data),
        scb=bytes(scb) if scb is not None else None,
        mac=bytes(mac) if mac is not None else None,
        raw=bytes(raw),
    )


class PacketReader:
    """Incremental stream parser that tolerates noise, echoes and bad frames."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.errors: list[str] = []

    def reset(self) -> None:
        self._buf.clear()

    def feed(self, chunk: bytes) -> list[Packet]:
        self._buf.extend(chunk)
        out: list[Packet] = []
        while True:
            # Skip to the next SOM (drops 0xFF marks and line noise).
            idx = self._buf.find(bytes([C.SOM]))
            if idx < 0:
                self._buf.clear()
                return out
            if idx:
                del self._buf[:idx]
            if len(self._buf) < 5:
                return out
            total = self._buf[2] | (self._buf[3] << 8)
            if total < 7 or total > MAX_PACKET_LEN:
                del self._buf[0]
                continue
            if len(self._buf) < total:
                return out
            raw = bytes(self._buf[:total])
            try:
                pkt = parse_packet(raw)
            except PacketError as exc:
                self.errors.append(f"{exc}: {raw.hex(' ')}")
                del self._buf[0]
                continue
            del self._buf[:total]
            out.append(pkt)
