"""High level reader programming workflow shared by the GUI and the CLI."""

from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .osdp import constants as C
from .osdp import decode as D
from .osdp.cp import Bus, FoundReader, NoResponse, OsdpError, Reader, probe, scan

# --------------------------------------------------------------------------
# Field notes for Veridt Stealth readers on Lenel OnGuard
# --------------------------------------------------------------------------
VERIDT_DEFAULT_ADDRESS = 1
VERIDT_DEFAULT_BAUD = 9600

VERIDT_NOTES = [
    "Factory default for Veridt OSDP readers: address 1 at 9600 baud (Veridt Tech Note 123).",
    "A baud change sent with osdp_COMSET is only kept after a power cycle if the address changes "
    "at the same time. A reader left at address 1 always restarts at 9600 baud.",
    "Older (pre-2022) Veridt OSDP readers could stay offline after a power loss when the panel "
    "auto-detected the baud rate. LenelS2's fix is a fixed address (0) at 9600 baud on the reader, "
    "with the same baud rate set explicitly in OnGuard (OnGuard 8.1 does not auto-negotiate it).",
    "The tamper wire is not used in OSDP mode; leave it disconnected.",
    "Power the reader from a 12 VDC (+/- 1 V) supply that can deliver at least 300 mA. "
    "The USB adapter does not power the reader.",
]

ONGUARD_RECOMMENDED = (0, 9600)


@dataclass
class Preset:
    label: str
    address: int
    baud: int


PRESETS = [
    Preset("OnGuard: address 0 @ 9600 (LenelS2 fix for Veridt)", 0, 9600),
    Preset("OnGuard multidrop: address 1 @ 9600", 1, 9600),
    Preset("OnGuard multidrop: address 2 @ 9600", 2, 9600),
    Preset("OnGuard multidrop: address 3 @ 9600", 3, 9600),
    Preset("Faster line: address 0 @ 38400", 0, 38400),
    Preset("Veridt factory default: address 1 @ 9600", 1, 9600),
]


def comset_warnings(current_address: int, new_address: int, new_baud: int) -> list[str]:
    warnings = []
    if new_address == current_address and new_baud != VERIDT_DEFAULT_BAUD:
        warnings.append(
            "The address is not changing. Veridt readers only keep a new baud rate when the "
            "address changes too, so this reader may come back at its old baud rate after a power "
            "cycle. Use 'Verify after power cycle' to check.")
    if new_address == VERIDT_DEFAULT_ADDRESS and new_baud != VERIDT_DEFAULT_BAUD:
        warnings.append(
            "Veridt readers at address 1 always restart at 9600 baud. Choose another address "
            "or use 9600 baud.")
    if new_address > 126:
        warnings.append("OSDP addresses must be 0-126.")
    return warnings


def key_fingerprint(key: bytes) -> str:
    """Short, non-reversible identifier for a key (safe to display/log)."""
    return hashlib.sha256(key).hexdigest()[:8].upper()


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------
@dataclass
class ReaderInfo:
    address: int
    baud: int
    device_id: Optional[D.DeviceId] = None
    capabilities: list[D.Capability] = field(default_factory=list)
    local_status: Optional[D.LocalStatus] = None
    errors: list[str] = field(default_factory=list)

    @property
    def supports_secure_channel(self) -> Optional[bool]:
        for cap in self.capabilities:
            if cap.function == 9:
                return bool(cap.compliance & 0x01)
        return None if not self.capabilities else False

    def describe(self) -> str:
        lines = [f"Address {self.address}, {self.baud} baud"]
        if self.device_id:
            lines.append(self.device_id.describe())
        if self.local_status:
            lines.append(self.local_status.describe())
        if self.capabilities:
            lines.append("Capabilities:")
            lines.extend(f"  - {c.describe()}" for c in self.capabilities)
        for err in self.errors:
            lines.append(f"Note: {err}")
        return "\n".join(lines)


@dataclass
class ProgramResult:
    ok: bool
    old_address: int
    old_baud: int
    new_address: int
    new_baud: int
    device_id: Optional[D.DeviceId] = None
    verified: bool = False
    messages: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def describe(self) -> str:
        head = "SUCCESS" if self.ok and self.verified else ("PROGRAMMED (not verified)" if self.ok else "FAILED")
        lines = [f"{head}: address {self.old_address} @ {self.old_baud} -> "
                 f"address {self.new_address} @ {self.new_baud}"]
        lines.extend(self.messages)
        lines.extend(f"WARNING: {w}" for w in self.warnings)
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Operations
# --------------------------------------------------------------------------
def read_info(bus: Bus, address: int, baud: int, key: Optional[bytes] = None,
              use_secure: bool = False) -> ReaderInfo:
    bus.set_baud(baud)
    reader = Reader(bus, address)
    info = ReaderInfo(address, baud)
    if use_secure:
        reader.start_secure_channel(key)
    info.device_id = reader.identify()
    try:
        info.capabilities = reader.capabilities()
    except OsdpError as exc:
        info.errors.append(f"capabilities: {exc}")
    try:
        info.local_status = reader.local_status()
    except OsdpError as exc:
        info.errors.append(f"local status: {exc}")
    return info


def _wait_for_reader(bus: Bus, address: int, baud: int, seconds: float = 3.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if probe(bus, address, baud, 0.25) is not None:
            return True
        if getattr(bus.transport, "synchronous", False):
            return False
        time.sleep(0.1)
    return False


def program_reader(bus: Bus, current_address: int, current_baud: int, new_address: int, new_baud: int,
                   *, key: Optional[bytes] = None, use_secure: bool = False) -> ProgramResult:
    """Change a reader's OSDP address and baud rate and confirm it answers at the new settings."""
    result = ProgramResult(False, current_address, current_baud, new_address, new_baud)
    result.warnings.extend(comset_warnings(current_address, new_address, new_baud))
    if not 0 <= new_address <= C.MAX_ADDR:
        result.messages.append("New address must be between 0 and 126.")
        return result

    bus.set_baud(current_baud)
    reader = Reader(bus, current_address)
    result.device_id = reader.identify()
    if use_secure:
        reader.start_secure_channel(key)
    com = reader.comset(new_address, new_baud)
    result.ok = True
    if (com.address, com.baud) != (new_address, new_baud):
        result.warnings.append(
            f"Reader reported address {com.address} @ {com.baud} instead of the requested values.")
    result.new_address, result.new_baud = com.address, com.baud
    result.messages.append(f"Reader accepted osdp_COMSET (now address {com.address} @ {com.baud}).")

    if not getattr(bus.transport, "synchronous", False):
        time.sleep(0.3)  # let the reader switch its UART
    if _wait_for_reader(bus, com.address, com.baud):
        check = Reader(bus, com.address)
        try:
            dev = check.identify()
            if result.device_id and dev.serial_number != result.device_id.serial_number:
                result.warnings.append("A different serial number answered at the new address!")
            else:
                result.verified = True
                result.messages.append("Reader answers at the new address and baud rate.")
        except OsdpError as exc:
            result.warnings.append(f"Reader answered but identify failed: {exc}")
    else:
        result.warnings.append("Reader did not answer at the new settings. Use 'Find reader' to locate it.")
    return result


@dataclass
class VerifyResult:
    ok: bool
    text: str
    found: Optional[FoundReader] = None


def verify_after_power_cycle(bus: Bus, address: int, baud: int, expected_serial: Optional[int] = None,
                             *, progress=None, cancelled=None) -> VerifyResult:
    """Confirm a reader came back at the programmed settings after power was removed."""
    if _wait_for_reader(bus, address, baud, seconds=5.0):
        reader = Reader(bus, address)
        try:
            dev = reader.identify()
        except OsdpError as exc:
            return VerifyResult(False, f"Reader answered at address {address} @ {baud} but identify failed: {exc}")
        if expected_serial is not None and dev.serial_number != expected_serial:
            return VerifyResult(False, f"A different reader (serial {dev.serial_number}) answered at "
                                       f"address {address} @ {baud}.")
        return VerifyResult(True, f"PASS: reader kept address {address} @ {baud} baud through the power cycle. "
                                  "It is ready for OnGuard.",
                            FoundReader(address, baud, dev))
    found = scan(bus, progress=progress, cancelled=cancelled)
    if found:
        f = found[0]
        return VerifyResult(False, f"FAIL: reader came back at address {f.address} @ {f.baud} baud, not "
                                   f"{address} @ {baud}. The settings were not retained "
                                   "(see the Veridt notes: change the address together with the baud rate).", f)
    return VerifyResult(False, "FAIL: no reader answered at any address or baud rate. Check power and wiring.")


def install_key(bus: Bus, address: int, baud: int, new_key: bytes,
                current_key: Optional[bytes] = None) -> str:
    """Install a new secure channel base key, then prove the reader accepts it."""
    if len(new_key) != 16:
        raise ValueError("new key must be 16 bytes")
    bus.set_baud(baud)
    reader = Reader(bus, address)
    reader.start_secure_channel(current_key)
    reader.keyset(new_key)
    time.sleep(0.05)
    check = Reader(bus, address)
    check.poll()
    check.start_secure_channel(new_key)
    check.poll()
    return (f"New secure channel key installed and verified (fingerprint {key_fingerprint(new_key)}). "
            "Record this key; you need it to manage the reader in the future.")


def test_secure_channel(bus: Bus, address: int, baud: int, key: Optional[bytes]) -> str:
    bus.set_baud(baud)
    reader = Reader(bus, address)
    reader.poll()
    reader.start_secure_channel(key)
    reader.poll()
    which = "default install key (SCBK-D)" if key in (None, C.SCBK_DEFAULT) else \
        f"custom key {key_fingerprint(key)}"
    return f"Secure channel OK using the {which}."


# --------------------------------------------------------------------------
# OnGuard checklist
# --------------------------------------------------------------------------
def onguard_checklist(address: int, baud: int, *, device_id: Optional[D.DeviceId] = None,
                      secure_mode: str = "plain", card_bits: Optional[int] = None, door: str = "") -> str:
    sc_text = {
        "plain": "Plain text, or encrypted with the panel installing its own key",
        "install": "Encrypted, panel installs its own key (reader is still in install mode / default key)",
        "custom": "Encrypted with the custom key installed by this tool (OnGuard must use the same key)",
    }.get(secure_mode, secure_mode)
    lines = [
        "Lenel OnGuard reader settings",
        "=============================",
        "System Administration > Access Control > Readers and Doors > (reader)",
        "(exact menu and field names vary between OnGuard versions)",
        "",
    ]
    if door:
        lines.append(f"  Door / label                 : {door}")
    lines += [
        "  Reader communication / type : OSDP",
        f"  OSDP address                 : {address}",
        f"  Baud rate                    : {baud}  (set explicitly - do not use auto-detect)",
        f"  Secure channel / encryption  : {sc_text}",
        "  Panel / reader port          : ____________ (the RS-485 port this reader is wired to)",
    ]
    if card_bits:
        lines.append(f"  Card format bit length       : {card_bits} bits (from the card test)")
    if device_id:
        lines += ["", f"Reader serial (OSDP): {device_id.serial_number}   Firmware: {device_id.firmware}   "
                      f"Vendor code: {device_id.vendor_hex}"]
    lines += [
        "",
        "Wiring at the panel: RS-485 + (A) and RS-485 - (B), plus 12 VDC and ground.",
        "Leave the tamper wire disconnected in OSDP mode.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Programming log
# --------------------------------------------------------------------------
def default_log_dir() -> Path:
    base = Path(os.environ.get("USERPROFILE") or Path.home())
    docs = base / "Documents"
    return (docs if docs.is_dir() else base) / "OSDP Reader Programmer"


LOG_FIELDS = ["timestamp", "port", "action", "serial_number", "vendor_code", "model", "firmware",
              "old_address", "old_baud", "new_address", "new_baud", "verified", "key", "notes"]


def append_log(row: dict, log_dir: Optional[Path] = None) -> Path:
    log_dir = log_dir or default_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "programming_log.csv"
    new = not path.exists()
    row = {k: row.get(k, "") for k in LOG_FIELDS}
    row["timestamp"] = row["timestamp"] or _dt.datetime.now().isoformat(timespec="seconds")
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        if new:
            writer.writeheader()
        writer.writerow(row)
    return path


__all__ = [
    "VERIDT_NOTES", "PRESETS", "Preset", "ReaderInfo", "ProgramResult", "VerifyResult",
    "read_info", "program_reader", "verify_after_power_cycle", "install_key",
    "test_secure_channel", "onguard_checklist", "append_log", "default_log_dir",
    "comset_warnings", "key_fingerprint", "NoResponse",
]
