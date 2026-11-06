"""Command library for the Commands tab and the ``run`` CLI command.

Two kinds of entries:

* Built-in commands from the public SIA OSDP v2.2 specification, each with a
  small parameter form and an encoder.
* User commands that a person adds (for example a manufacturer-specific
  osdp_MFG command supplied by a reader vendor's support team). They are kept
  in a JSON file that can be exported and shared.

No vendor-specific bytes are built in: HID, Veridt and IDFactors do not
publish their OSDP configuration commands, and sending guessed bytes could
misconfigure a reader.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .osdp import constants as C
from .osdp import decode as D

VENDORS = ("HID", "Veridt", "IDFactors", "Other")

VENDOR_NOTES = {
    "HID": "HID confirms its readers accept HID manufacturer-specific OSDP commands (for example to enter "
           "install mode or change the OSDP version, sent through Mercury panels) and HID configuration "
           "cards, but the command format is only given to HID partners. Signo and iCLASS SE readers can "
           "also be configured with the HID Reader Manager mobile app. Add commands HID support gives you "
           "here.",
    "Veridt": "Veridt command cards (for example XOPTIONS A2004002 or X2OPTIONS 83) are read by the "
              "reader from the card, not sent over OSDP. Ask Veridt support for the osdp_MFG (0x80) "
              "equivalent and add it here. Veridt Stealth readers report vendor code 00-11-12.",
    "IDFactors": "IDFactors readers are configured with the IDFACTORS ReaderConfig application (ask "
                 "customerservice@idfactors.com). Its documented command-line entries are 'SM 1F' (switch to "
                 "1-factor mode) and 'WF xx' (pick a Wiegand format). IDFactors says its readers can also be "
                 "reconfigured over OSDP, but doesn't publish the OSDP form. A jumper between terminals 1 and 2 "
                 "also forces 1-factor mode.",
    "Other": "Commands for any other reader brand.",
}

MAX_DATA = 1024


# ==========================================================================
# Definitions
# ==========================================================================
@dataclass
class Param:
    key: str
    label: str
    kind: str = "int"                 # int | choice | text | hex
    default: Any = 0
    minimum: int = 0
    maximum: int = 255
    choices: Optional[dict[str, int]] = None
    help: str = ""

    def convert(self, raw: Any) -> Any:
        if self.kind == "int":
            try:
                value = int(str(raw).strip(), 0)
            except ValueError as exc:
                raise ValueError(f"{self.label}: enter a whole number") from exc
            if not self.minimum <= value <= self.maximum:
                raise ValueError(f"{self.label}: must be {self.minimum}-{self.maximum}")
            return value
        if self.kind == "choice":
            assert self.choices is not None
            if isinstance(raw, int) and raw in self.choices.values():
                return raw
            if raw not in self.choices:
                raise ValueError(f"{self.label}: choose one of {', '.join(self.choices)}")
            return self.choices[raw]
        if self.kind == "hex":
            return D.parse_hex(str(raw), name=self.label)
        return str(raw)

    def default_display(self) -> str:
        if self.kind == "choice":
            assert self.choices is not None
            for label, value in self.choices.items():
                if value == self.default:
                    return label
            return next(iter(self.choices))
        return str(self.default)


@dataclass
class CommandDef:
    key: str
    name: str
    group: str
    code: int
    description: str
    params: list[Param] = field(default_factory=list)
    build: Optional[Callable[[dict], bytes]] = None
    data_hex: str = ""
    changes_settings: bool = False
    secure: bool = False
    source: str = ""
    notes: str = ""
    user: bool = False

    def defaults(self) -> dict:
        return {p.key: p.default_display() for p in self.params}

    def payload(self, raw_values: Optional[dict] = None) -> bytes:
        raw_values = raw_values if raw_values is not None else self.defaults()
        values = {p.key: p.convert(raw_values.get(p.key, p.default_display())) for p in self.params}
        if self.build is not None:
            data = self.build(values)
        else:
            data = D.parse_hex(self.data_hex)
        if len(data) > MAX_DATA:
            raise ValueError(f"payload is {len(data)} bytes; the limit here is {MAX_DATA}")
        return data

    def preview(self, raw_values: Optional[dict] = None) -> str:
        data = self.payload(raw_values)
        body = data.hex(" ").upper() if data else "(no data)"
        return f"{C.command_name(self.code)} (0x{self.code:02X}): {body}"


# ==========================================================================
# Built-in standard OSDP commands
# ==========================================================================
COLOR_CHOICES = {name.title(): value for name, value in
                 [("off", 0), ("red", 1), ("green", 2), ("amber", 3), ("blue", 4),
                  ("magenta", 5), ("cyan", 6), ("white", 7)]}
SPEC = "SIA OSDP v2.2"
STANDARD_GROUP = "Standard OSDP"
MFG_GROUP = "Manufacturer specific"


def _led(v: dict) -> bytes:
    if v["mode"] == 1:
        return D.encode_led(reader=0, led=v["led"], temp_on_color=v["on_color"], temp_off_color=v["off_color"],
                            temp_on_time=v["on_time"], temp_off_time=v["off_time"], temp_timer=v["timer"])
    return D.encode_led(reader=0, led=v["led"], perm_on_color=v["on_color"], perm_off_color=v["off_color"],
                        perm_on_time=v["on_time"], perm_off_time=v["off_time"], cancel_temp=True)


def _standard() -> list[CommandDef]:
    t100 = "x 100 ms"
    return [
        CommandDef("poll", "Poll", STANDARD_GROUP, C.CMD_POLL,
                   "Ask the reader for anything it has to report (card reads, key presses, status changes).",
                   source=SPEC),
        CommandDef("id", "Identity (osdp_ID)", STANDARD_GROUP, C.CMD_ID,
                   "Vendor code, model, version, serial number and firmware.", build=lambda v: b"\x00", source=SPEC),
        CommandDef("cap", "Capabilities (osdp_CAP)", STANDARD_GROUP, C.CMD_CAP,
                   "What the reader supports: LEDs, buzzer, card formats, secure channel, buffer sizes.",
                   build=lambda v: b"\x00", source=SPEC),
        CommandDef("lstat", "Local status (tamper / power)", STANDARD_GROUP, C.CMD_LSTAT,
                   "Tamper switch and power-supply status.", source=SPEC),
        CommandDef("istat", "Input status", STANDARD_GROUP, C.CMD_ISTAT,
                   "State of the reader's inputs, if it has any.", source=SPEC),
        CommandDef("ostat", "Output status", STANDARD_GROUP, C.CMD_OSTAT,
                   "State of the reader's outputs, if it has any.", source=SPEC),
        CommandDef("rstat", "Reader tamper status", STANDARD_GROUP, C.CMD_RSTAT,
                   "Status of attached sub-readers.", source=SPEC),
        CommandDef("led", "LED control", STANDARD_GROUP, C.CMD_LED,
                   "Set an LED. Temporary flashes for the timer and then goes back; permanent stays.",
                   params=[
                       Param("led", "LED number", default=0, maximum=15),
                       Param("mode", "Mode", "choice", 1, choices={"Temporary": 1, "Permanent": 0}),
                       Param("on_color", "On color", "choice", 2, choices=COLOR_CHOICES),
                       Param("off_color", "Off color", "choice", 0, choices=COLOR_CHOICES),
                       Param("on_time", "On time", default=5, help=t100),
                       Param("off_time", "Off time", default=5, help=t100),
                       Param("timer", "Timer (temporary only)", default=30, maximum=0xFFFF, help=t100),
                   ], build=_led, source=SPEC),
        CommandDef("buzzer", "Buzzer", STANDARD_GROUP, C.CMD_BUZ, "Sound the reader's buzzer.",
                   params=[
                       Param("tone", "Tone", "choice", 2, choices={"Default tone": 2, "Off": 1}),
                       Param("on_time", "On time", default=3, help=t100),
                       Param("off_time", "Off time", default=3, help=t100),
                       Param("count", "Repeat count", default=1, help="0 = until told to stop"),
                   ], build=lambda v: D.encode_buzzer(0, v["tone"], v["on_time"], v["off_time"], v["count"]),
                   source=SPEC),
        CommandDef("output", "Output control", STANDARD_GROUP, C.CMD_OUT,
                   "Switch one of the reader's outputs (on readers that have them).",
                   params=[
                       Param("output", "Output number", default=0),
                       Param("action", "Action", "choice", 2,
                             choices={label: code for code, label in D.OUTPUT_ACTIONS.items()}),
                       Param("timer", "Timer (temporary actions)", default=0, maximum=0xFFFF, help=t100),
                   ], build=lambda v: D.encode_output(v["output"], v["action"], v["timer"]), source=SPEC),
        CommandDef("text", "Text display", STANDARD_GROUP, C.CMD_TEXT,
                   "Show text on readers with a display.",
                   params=[
                       Param("mode", "Mode", "choice", 1, choices={
                           "Permanent, no wrap": 1, "Permanent, wrap": 2,
                           "Temporary, no wrap": 3, "Temporary, wrap": 4}),
                       Param("temp_time", "Temporary time (seconds)", default=0),
                       Param("row", "Row", default=1),
                       Param("col", "Column", default=1),
                       Param("text", "Text", "text", "HELLO"),
                   ], build=lambda v: D.encode_text(v["text"], 0, v["row"], v["col"], v["temp_time"], v["mode"]),
                   source=SPEC),
        CommandDef("acurxsize", "Set CP receive size", STANDARD_GROUP, C.CMD_ACURXSIZE,
                   "Tell the reader the largest reply this tool can accept.",
                   params=[Param("size", "Size (bytes)", default=1024, minimum=64, maximum=0xFFFF)],
                   build=lambda v: D.encode_u16(v["size"]), source=SPEC),
        CommandDef("keepactive", "Keep active", STANDARD_GROUP, C.CMD_KEEPACTIVE,
                   "Keep a smart-card session open for the given time.",
                   params=[Param("ms", "Time (ms)", default=5000, maximum=0xFFFF)],
                   build=lambda v: D.encode_u16(v["ms"]), source=SPEC),
        CommandDef("abort", "Abort", STANDARD_GROUP, C.CMD_ABORT,
                   "Cancel a pending operation (for example a biometric or smart-card action).", source=SPEC),
        CommandDef("comset", "Communication settings (COMSET)", STANDARD_GROUP, C.CMD_COMSET,
                   "Change the reader's OSDP address and baud rate. The '1. Program' tab does this AND "
                   "verifies the result; prefer it.",
                   params=[
                       Param("address", "New address", default=0, maximum=126),
                       Param("baud", "New baud", "choice", 9600, choices={str(b): b for b in C.BAUD_RATES}),
                   ], build=lambda v: D.encode_comset(v["address"], v["baud"]),
                   changes_settings=True, source=SPEC),
        CommandDef("mfg", "Manufacturer command (osdp_MFG)", MFG_GROUP, C.CMD_MFG,
                   "Send a manufacturer-specific command: the reader's 3-byte vendor code followed by the "
                   "manufacturer's payload. Only send payloads the manufacturer gave you.",
                   params=[
                       Param("vendor", "Vendor code (3 bytes hex)", "hex", "00 11 12",
                             help="filled in from the last reader you read"),
                       Param("payload", "Payload (hex)", "hex", ""),
                   ], build=lambda v: D.encode_mfg(v["vendor"], v["payload"]),
                   changes_settings=True, source=SPEC + " (payload format is the manufacturer's)"),
    ]


STANDARD_COMMANDS = _standard()


# ==========================================================================
# User library
# ==========================================================================
@dataclass
class UserCommand:
    name: str
    vendor: str = "Other"
    code: str = "80"
    data: str = ""
    secure: bool = False
    changes_settings: bool = True
    notes: str = ""
    source: str = ""

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("Give the command a name")
        if self.vendor not in VENDORS:
            raise ValueError(f"Vendor must be one of {', '.join(VENDORS)}")
        code = D.parse_hex(self.code, name="Command code", length=1)
        if code[0] < 0x60:
            raise ValueError("Command code must be an OSDP command (0x60 or higher)")
        data = D.parse_hex(self.data, name="Data")
        if len(data) > MAX_DATA:
            raise ValueError(f"Data is limited to {MAX_DATA} bytes")
        if code[0] == C.CMD_MFG and len(data) < 3:
            raise ValueError("osdp_MFG data must start with the 3-byte vendor code")

    def to_def(self) -> CommandDef:
        self.validate()
        code = D.parse_hex(self.code, length=1)[0]
        return CommandDef(
            key=f"user:{self.vendor}:{self.name}", name=self.name, group=self.vendor, code=code,
            description=self.notes or "User-defined command.", data_hex=self.data,
            changes_settings=self.changes_settings, secure=self.secure,
            source=self.source or "added by you", notes=self.notes, user=True)


class CommandLibrary:
    """User commands stored as JSON (one file, easy to share)."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path
        self.commands: list[UserCommand] = []

    @staticmethod
    def default_path() -> Path:
        from .programmer import default_log_dir

        return default_log_dir() / "command_library.json"

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "CommandLibrary":
        lib = cls(path or cls.default_path())
        if lib.path and lib.path.exists():
            lib.commands = cls._read(lib.path)
        return lib

    @staticmethod
    def _read(path: Path) -> list[UserCommand]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw.get("commands", []) if isinstance(raw, dict) else raw
        out = []
        fields = UserCommand.__dataclass_fields__
        for item in items:
            cmd = UserCommand(**{k: v for k, v in item.items() if k in fields})
            cmd.validate()
            out.append(cmd)
        return out

    def save(self) -> Path:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.export(self.path)
        return self.path

    def export(self, path: Path) -> None:
        data = {"format": "osdp-reader-programmer command library", "version": 1,
                "commands": [asdict(c) for c in self.commands]}
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")

    def find(self, vendor: str, name: str) -> Optional[UserCommand]:
        for c in self.commands:
            if c.vendor == vendor and c.name == name:
                return c
        return None

    def upsert(self, cmd: UserCommand, replacing: Optional[UserCommand] = None) -> None:
        cmd.validate()
        if replacing is not None and replacing in self.commands:
            self.commands[self.commands.index(replacing)] = cmd
            return
        existing = self.find(cmd.vendor, cmd.name)
        if existing is not None:
            self.commands[self.commands.index(existing)] = cmd
        else:
            self.commands.append(cmd)

    def remove(self, cmd: UserCommand) -> None:
        self.commands.remove(cmd)

    def import_file(self, path: Path) -> int:
        """Merge commands from another library file. Returns how many were added or updated."""
        incoming = self._read(Path(path))
        for cmd in incoming:
            self.upsert(cmd)
        return len(incoming)

    def definitions(self) -> list[CommandDef]:
        return [c.to_def() for c in self.commands]


def all_definitions(library: Optional[CommandLibrary] = None) -> list[CommandDef]:
    return STANDARD_COMMANDS + (library.definitions() if library else [])


def find_definition(name: str, library: Optional[CommandLibrary] = None) -> CommandDef:
    wanted = name.strip().lower()
    for d in all_definitions(library):
        if wanted in (d.key.lower(), d.name.lower()):
            return d
    raise ValueError(f"No command named '{name}'. Use 'readerprog commands' to list them.")
