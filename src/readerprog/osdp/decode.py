"""Decoders for OSDP reply payloads and encoders for command payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import constants as C


class DecodeError(Exception):
    pass


# ==========================================================================
# Reply decoders
# ==========================================================================
@dataclass
class DeviceId:
    vendor_code: bytes
    model: int
    version: int
    serial_number: int
    firmware: str

    @property
    def vendor_hex(self) -> str:
        return "-".join(f"{b:02X}" for b in self.vendor_code)

    @property
    def serial_hex(self) -> str:
        return f"{self.serial_number:08X}"

    @property
    def vendor_name(self) -> str:
        from .vendors import vendor_name

        return vendor_name(self.vendor_code)

    def describe(self) -> str:
        return (
            f"Vendor code (OUI): {self.vendor_hex} ({self.vendor_name})\n"
            f"Model: {self.model}   Version: {self.version}\n"
            f"Serial number: {self.serial_number} (0x{self.serial_hex})\n"
            f"Firmware: {self.firmware}"
        )


def decode_pdid(data: bytes) -> DeviceId:
    if len(data) < 12:
        raise DecodeError(f"osdp_PDID too short ({len(data)} bytes)")
    return DeviceId(
        vendor_code=bytes(data[0:3]),
        model=data[3],
        version=data[4],
        serial_number=int.from_bytes(data[5:9], "little"),
        firmware=f"{data[9]}.{data[10]}.{data[11]}",
    )


@dataclass
class Capability:
    function: int
    compliance: int
    count: int

    @property
    def name(self) -> str:
        return C.CAPABILITY_NAMES.get(self.function, f"Function {self.function}")

    def describe(self) -> str:
        detail = f"compliance {self.compliance}, items {self.count}"
        if self.function == 8:
            detail = "CRC-16 supported" if self.compliance else "checksum only"
        elif self.function == 9:
            parts = []
            parts.append("AES-128 secure channel" if self.compliance & 0x01 else "no secure channel")
            if self.count & 0x01:
                parts.append("default key (install mode) allowed")
            detail = ", ".join(parts)
        elif self.function in (10, 11):
            detail = f"{self.compliance | (self.count << 8)} bytes"
        elif self.function == 13:
            detail = f"{self.count} reader(s)"
        elif self.function == 3:
            detail = {0: "no card data", 1: "binary/raw card data", 2: "ASCII card data", 3: "binary and ASCII"}.get(
                self.compliance, f"compliance {self.compliance}")
        elif self.function == 4:
            detail = f"{self.count} LED(s), compliance level {self.compliance}"
        elif self.function == 5:
            detail = f"{self.count} buzzer(s), compliance level {self.compliance}"
        elif self.function == 16:
            detail = {0: "unspecified", 1: "IEC 60839-11-5", 2: "SIA OSDP 2.2"}.get(
                self.compliance, f"level {self.compliance}")
        return f"{self.name}: {detail}"


def decode_pdcap(data: bytes) -> list[Capability]:
    if len(data) % 3:
        raise DecodeError("osdp_PDCAP length not a multiple of 3")
    return [Capability(data[i], data[i + 1], data[i + 2]) for i in range(0, len(data), 3)]


@dataclass
class LocalStatus:
    tamper: bool
    power_failure: bool

    def describe(self) -> str:
        return (f"Tamper: {'TAMPER' if self.tamper else 'normal'}   "
                f"Power: {'FAILURE' if self.power_failure else 'normal'}")


def decode_lstatr(data: bytes) -> LocalStatus:
    if len(data) < 2:
        raise DecodeError("osdp_LSTATR too short")
    return LocalStatus(tamper=bool(data[0]), power_failure=bool(data[1]))


def decode_rstatr(data: bytes) -> list[str]:
    names = {0: "normal", 1: "not connected", 2: "tamper"}
    return [names.get(b, f"0x{b:02X}") for b in data]


@dataclass
class ComSettings:
    address: int
    baud: int


def decode_com(data: bytes) -> ComSettings:
    if len(data) < 5:
        raise DecodeError("osdp_COM too short")
    return ComSettings(address=data[0], baud=int.from_bytes(data[1:5], "little"))


def decode_nak(data: bytes) -> str:
    if not data:
        return "NAK (no reason given)"
    reason = C.NAK_REASONS.get(data[0], f"error code 0x{data[0]:02X}")
    return f"NAK {data[0]}: {reason}"


@dataclass
class CardRead:
    reader: int
    format_code: int
    bit_count: int
    data: bytes
    ascii: Optional[str] = None
    decoded: dict = field(default_factory=dict)

    @property
    def bits(self) -> str:
        if self.ascii is not None:
            return ""
        return "".join(f"{b:08b}" for b in self.data)[: self.bit_count]

    def describe(self) -> str:
        if self.ascii is not None:
            return f"Reader {self.reader}: ASCII card data '{self.ascii}'"
        fmt = {0: "raw (unspecified)", 1: "Wiegand"}.get(self.format_code, f"format {self.format_code}")
        lines = [f"Reader {self.reader}: {self.bit_count}-bit card, {fmt}",
                 f"  Hex : {self.data.hex(' ').upper()}"]
        if self.bit_count <= 128:
            lines.append(f"  Bits: {self.bits}")
        for key, value in self.decoded.items():
            lines.append(f"  {key}: {value}")
        return "\n".join(lines)


def decode_raw(data: bytes) -> CardRead:
    if len(data) < 4:
        raise DecodeError("osdp_RAW too short")
    bit_count = data[2] | (data[3] << 8)
    payload = bytes(data[4:])
    card = CardRead(reader=data[0], format_code=data[1], bit_count=bit_count, data=payload)
    card.decoded = decode_card_bits(card.bits)
    return card


def decode_fmt(data: bytes) -> CardRead:
    if len(data) < 3:
        raise DecodeError("osdp_FMT too short")
    text = bytes(data[3:3 + data[2]]).decode("ascii", "replace")
    return CardRead(reader=data[0], format_code=0x100, bit_count=0, data=bytes(data[3:]), ascii=text)


def decode_keypad(data: bytes) -> str:
    if len(data) < 2:
        raise DecodeError("osdp_KEYPAD too short")
    keys = []
    for b in data[2:2 + data[1]]:
        keys.append({0x7F: "*", 0x0D: "#"}.get(b, chr(b) if 32 <= b < 127 else f"<{b:02X}>"))
    return f"Reader {data[0]}: keys {''.join(keys)}"


# ==========================================================================
# Card bit decoders (diagnostic only)
# ==========================================================================
def _parity_ok(bits: str, even: bool) -> bool:
    ones = bits.count("1")
    return (ones % 2 == 0) if even else (ones % 2 == 1)


_FASCN_CHARS = {0x0B: "SS", 0x0D: "FS", 0x0F: "ES"}


def decode_fascn_200(bits: str) -> Optional[dict]:
    """Decode a 200-bit FASC-N (40 x 5-bit BCD characters, LSB first, odd parity)."""
    if len(bits) != 200:
        return None
    chars = []
    for i in range(0, 200, 5):
        group = bits[i:i + 5]
        value = int(group[0]) | int(group[1]) << 1 | int(group[2]) << 2 | int(group[3]) << 3
        if group.count("1") % 2 != 1:
            return None
        chars.append(_FASCN_CHARS.get(value, str(value) if value <= 9 else "?"))
    if chars[0] != "SS":
        return None
    try:
        es = chars.index("ES")
    except ValueError:
        return None
    body = chars[1:es]
    fields = []
    current = ""
    for ch in body:
        if ch == "FS":
            fields.append(current)
            current = ""
        else:
            current += ch
    fields.append(current)
    if len(fields) < 6:
        return None
    tail = fields[5]
    return {
        "Format": "FASC-N (200-bit, PIV/CAC/TWIC)",
        "Agency code": fields[0],
        "System code": fields[1],
        "Credential number": fields[2],
        "Credential series": fields[3],
        "Individual credential issue": fields[4],
        "Person identifier": tail[:10],
        "Org category / org id / association": f"{tail[10:11]} / {tail[11:15]} / {tail[15:16]}",
    }


def decode_card_bits(bits: str) -> dict:
    n = len(bits)
    if n == 26:
        fc = int(bits[1:9], 2)
        card = int(bits[9:25], 2)
        ok = _parity_ok(bits[0:13], True) and _parity_ok(bits[13:26], False)
        return {"Format": "26-bit H10301", "Facility code": fc, "Card number": card,
                "Parity": "OK" if ok else "mismatch (may be a different 26-bit format)"}
    if n == 37:
        fc = int(bits[1:17], 2)
        card = int(bits[17:36], 2)
        ok = _parity_ok(bits[0:19], True) and _parity_ok(bits[18:37], False)
        return {"Format": "37-bit H10304 (if parity OK)", "Facility code": fc, "Card number": card,
                "Parity": "OK" if ok else "mismatch (another 37-bit format)"}
    if n == 75:
        return {
            "Format": "75-bit PIV (agency/system/credential/expiry)",
            "Agency code": int(bits[1:15], 2),
            "System code": int(bits[15:29], 2),
            "Credential number": int(bits[29:49], 2),
            "Expiration (YYYYMMDD)": int(bits[49:74], 2),
        }
    if n == 200:
        decoded = decode_fascn_200(bits)
        if decoded:
            return decoded
    return {}


# ==========================================================================
# Command payload encoders
# ==========================================================================
def encode_comset(address: int, baud: int) -> bytes:
    if not 0 <= address <= C.MAX_ADDR:
        raise ValueError("OSDP address must be 0..126")
    if baud <= 0:
        raise ValueError("baud must be positive")
    return bytes([address]) + int(baud).to_bytes(4, "little")


def encode_keyset(key: bytes) -> bytes:
    if len(key) != 16:
        raise ValueError("SCBK must be 16 bytes")
    return bytes([C.KEY_TYPE_SCBK, len(key)]) + key


def encode_led(
    reader: int = 0,
    led: int = 0,
    *,
    temp_on_color: Optional[int] = None,
    temp_off_color: int = 0,
    temp_on_time: int = 5,
    temp_off_time: int = 5,
    temp_timer: int = 30,
    perm_on_color: Optional[int] = None,
    perm_off_color: int = 0,
    perm_on_time: int = 0,
    perm_off_time: int = 0,
    cancel_temp: bool = False,
) -> bytes:
    """Build one 14-byte osdp_LED record. Times are in 100 ms units."""
    if temp_on_color is not None:
        temp = [2, temp_on_time, temp_off_time, temp_on_color, temp_off_color,
                temp_timer & 0xFF, (temp_timer >> 8) & 0xFF]
    elif cancel_temp:
        temp = [1, 0, 0, 0, 0, 0, 0]
    else:
        temp = [0, 0, 0, 0, 0, 0, 0]
    if perm_on_color is not None:
        if perm_on_time == 0 and perm_off_time == 0:
            perm_on_time = 1  # steady on
        perm = [1, perm_on_time, perm_off_time, perm_on_color, perm_off_color]
    else:
        perm = [0, 0, 0, 0, 0]
    return bytes([reader, led] + temp + perm)


def encode_buzzer(reader: int = 0, tone: int = 2, on_time: int = 5, off_time: int = 5, count: int = 1) -> bytes:
    """osdp_BUZ: tone 0=none, 1=off, 2=default tone. Times in 100 ms units."""
    return bytes([reader, tone, on_time, off_time, count])


def encode_text(text: str, reader: int = 0, row: int = 1, col: int = 1, temp_time: int = 0,
                mode: Optional[int] = None) -> bytes:
    """osdp_TEXT. mode: 1=permanent no wrap, 2=permanent wrap, 3=temporary no wrap, 4=temporary wrap."""
    raw = text.encode("ascii", "replace")[:32]
    if mode is None:
        mode = 3 if temp_time else 1
    if mode not in (1, 2, 3, 4):
        raise ValueError("text mode must be 1-4")
    return bytes([reader, mode, temp_time, row, col, len(raw)]) + raw


OUTPUT_ACTIONS = {
    0: "No change",
    1: "Permanent OFF (cancel any timed action)",
    2: "Permanent ON (cancel any timed action)",
    3: "Permanent OFF (let a timed action finish)",
    4: "Permanent ON (let a timed action finish)",
    5: "Temporary ON for the timer, then OFF",
    6: "Temporary OFF for the timer, then ON",
}


def encode_output(output: int = 0, action: int = 2, timer: int = 0) -> bytes:
    """osdp_OUT record: output number, control code, timer (100 ms units, LSB first)."""
    if action not in OUTPUT_ACTIONS:
        raise ValueError("output action must be 0-6")
    if not 0 <= timer <= 0xFFFF:
        raise ValueError("timer must be 0-65535")
    return bytes([output, action, timer & 0xFF, timer >> 8])


def encode_u16(value: int) -> bytes:
    if not 0 <= value <= 0xFFFF:
        raise ValueError("value must be 0-65535")
    return int(value).to_bytes(2, "little")


def parse_hex(text: str, *, name: str = "data", length: Optional[int] = None) -> bytes:
    """Parse hex typed by a person: spaces, dashes, colons and 0x prefixes are allowed."""
    cleaned = text.replace("0x", "").replace("0X", "")
    cleaned = "".join(ch for ch in cleaned if ch not in " -:_,\t\n")
    if len(cleaned) % 2:
        raise ValueError(f"{name}: odd number of hex digits")
    try:
        value = bytes.fromhex(cleaned)
    except ValueError as exc:
        raise ValueError(f"{name}: not valid hexadecimal") from exc
    if length is not None and len(value) != length:
        raise ValueError(f"{name}: must be exactly {length} bytes")
    return value


def encode_mfg(vendor_code: bytes, payload: bytes) -> bytes:
    """osdp_MFG: the 3-byte vendor code followed by the manufacturer's own payload."""
    if len(vendor_code) != 3:
        raise ValueError("vendor code must be 3 bytes, e.g. 00 11 12")
    return bytes(vendor_code) + bytes(payload)


# ==========================================================================
# Generic reply description (used by the command console)
# ==========================================================================
def _io_states(data: bytes, what: str) -> str:
    if not data:
        return f"no {what}s reported"
    names = {0: "inactive", 1: "active"}
    return ", ".join(f"{what} {i}: {names.get(b, f'0x{b:02X}')}" for i, b in enumerate(data))


def describe_reply(code: int, data: bytes) -> str:
    """One readable description of any reply a reader can send."""
    try:
        if code == C.REPLY_ACK:
            return "ACK - command accepted"
        if code == C.REPLY_NAK:
            return decode_nak(data)
        if code == C.REPLY_PDID:
            return decode_pdid(data).describe()
        if code == C.REPLY_PDCAP:
            return "\n".join(c.describe() for c in decode_pdcap(data))
        if code == C.REPLY_LSTATR:
            return decode_lstatr(data).describe()
        if code == C.REPLY_ISTATR:
            return _io_states(data, "Input")
        if code == C.REPLY_OSTATR:
            return _io_states(data, "Output")
        if code == C.REPLY_RSTATR:
            return "Reader status: " + ", ".join(decode_rstatr(data))
        if code == C.REPLY_COM:
            com = decode_com(data)
            return f"Communication settings now: address {com.address}, {com.baud} baud"
        if code == C.REPLY_RAW:
            return decode_raw(data).describe()
        if code == C.REPLY_FMT:
            return decode_fmt(data).describe()
        if code == C.REPLY_KEYPAD:
            return decode_keypad(data)
        if code == C.REPLY_BUSY:
            return "BUSY - reader is busy, try again"
        if code == C.REPLY_MFGREP and len(data) >= 3:
            from .vendors import vendor_name

            vendor = "-".join(f"{b:02X}" for b in data[:3])
            body = data[3:].hex(" ").upper() or "(no data)"
            return f"Manufacturer reply from vendor {vendor} ({vendor_name(data[:3])}): {body}"
    except DecodeError as exc:
        return f"{C.reply_name(code)} (could not decode: {exc}) {data.hex(' ').upper()}"
    body = data.hex(" ").upper()
    return f"{C.reply_name(code)} {body}".strip()
