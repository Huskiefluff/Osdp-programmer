"""Find out which OSDP commands a reader implements.

OSDP has no "list your commands" request. osdp_CAP reports capabilities by
category, not command by command. But the specification gives an
unimplemented command its own NAK reason (0x03, "unknown command code"),
while an implemented command sent with a missing payload gets a length error
(NAK 0x02) or a normal reply. So each command is sent once with an EMPTY
payload, which a reader cannot act on, and the reply is classified.

Commands that could change a reader's identity or keys if a reader
mishandled an empty payload (COMSET, KEYSET, FILETRANSFER) are never sent.
Manufacturer sub-commands are never guessed: only whether osdp_MFG itself is
implemented is checked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from . import constants as C
from .cp import OsdpError, Reader
from .decode import describe_reply

SUPPORTED = "supported"
NOT_SUPPORTED = "not supported"
NEEDS_SECURE = "supported - needs secure channel"
NO_REPLY = "no reply"
UNCLEAR = "unclear"
SKIPPED = "not tested"

# (code, payload to send, what it is for)
PROBES: list[tuple[int, bytes, str]] = [
    (C.CMD_POLL, b"", "report card reads and events"),
    (C.CMD_ID, b"\x00", "identity report"),
    (C.CMD_CAP, b"\x00", "capability report"),
    (C.CMD_LSTAT, b"", "tamper and power status"),
    (C.CMD_ISTAT, b"", "input status"),
    (C.CMD_OSTAT, b"", "output status"),
    (C.CMD_RSTAT, b"", "sub-reader status"),
    (C.CMD_OUT, b"", "output control"),
    (C.CMD_LED, b"", "LED control"),
    (C.CMD_BUZ, b"", "buzzer control"),
    (C.CMD_TEXT, b"", "text display"),
    (C.CMD_BIOREAD, b"", "biometric read"),
    (C.CMD_BIOMATCH, b"", "biometric match"),
    (C.CMD_ACURXSIZE, b"", "panel receive-size notice"),
    (C.CMD_MFG, b"", "manufacturer-specific commands (sub-commands not tested)"),
    (C.CMD_XWR, b"", "transparent smart-card mode (extended write)"),
    (C.CMD_ABORT, b"", "abort pending operation"),
    (C.CMD_PIVDATA, b"", "read PIV data object"),
    (C.CMD_GENAUTH, b"", "PIV general authenticate"),
    (C.CMD_CRAUTH, b"", "PIV challenge/response authentication"),
    (C.CMD_KEEPACTIVE, b"", "keep smart-card session open"),
]

NOT_PROBED = {
    C.CMD_COMSET: "changes address/baud - use the Program tab",
    C.CMD_KEYSET: "changes the secure channel key - use the Secure channel tab",
    C.CMD_FILETRANSFER: "file/firmware upload - never sent blind",
    C.CMD_CHLNG: "secure channel handshake - use Test secure channel",
    C.CMD_SCRYPT: "secure channel handshake - use Test secure channel",
}


@dataclass
class ProbeResult:
    code: int
    status: str
    detail: str
    purpose: str = ""

    @property
    def name(self) -> str:
        return C.command_name(self.code)

    def line(self) -> str:
        return f"0x{self.code:02X} {self.name:<18} {self.status:<32} {self.detail}"


def classify(code: int, data: bytes) -> str:
    if code == C.REPLY_NAK:
        reason = data[0] if data else None
        if reason == 0x03:
            return NOT_SUPPORTED
        if reason in (0x05, 0x06):
            return NEEDS_SECURE
        if reason in (0x02, 0x07, 0x08, 0x09):
            return SUPPORTED
        return UNCLEAR
    return SUPPORTED


def probe_commands(reader: Reader, *, progress: Optional[Callable[[str, float], None]] = None,
                   cancelled: Optional[Callable[[], bool]] = None) -> list[ProbeResult]:
    progress = progress or (lambda text, frac: None)
    cancelled = cancelled or (lambda: False)
    results: list[ProbeResult] = []
    reader.reset_link()
    for i, (code, payload, purpose) in enumerate(PROBES):
        if cancelled():
            break
        progress(f"Testing {C.command_name(code)}", i / len(PROBES))
        try:
            reply = reader.command(code, payload, raise_nak=False)
        except OsdpError as exc:
            results.append(ProbeResult(code, NO_REPLY, str(exc).split(" to ")[0], purpose))
            reader.reset_link()
            continue
        detail = describe_reply(reply.code, reply.data).splitlines()[0]
        results.append(ProbeResult(code, classify(reply.code, reply.data), detail, purpose))
    for code, why in NOT_PROBED.items():
        results.append(ProbeResult(code, SKIPPED, why))
    progress("Command check complete", 1.0)
    return results


def report(results: list[ProbeResult], header: str = "") -> str:
    lines = [header] if header else []
    lines.append(f"{'Code':<4} {'Command':<18} {'Result':<32} Reader's answer")
    lines += [r.line() for r in results]
    supported = [r.name for r in results if r.status in (SUPPORTED, NEEDS_SECURE)]
    lines += ["", f"Supported: {', '.join(supported) or 'none'}",
              "Method: each command is sent once with an empty payload; NAK 03 (unknown command) means "
              "not implemented. Readers that don't follow the OSDP spec closely can give misleading answers."]
    return "\n".join(lines)
