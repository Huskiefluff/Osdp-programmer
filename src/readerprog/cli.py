"""Command line interface: ``readerprog <command> ...``"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional

from . import APP_NAME, __version__
from . import programmer as P
from .commands import CommandLibrary, all_definitions, find_definition
from .osdp.decode import describe_reply
from .osdp import constants as C
from .osdp.cp import SCAN_BAUD_ORDER, Bus, OsdpError, Reader, scan
from .osdp.secure import SecureChannelError, parse_key
from .osdp.transport import SerialTransport, list_ports


def _open_bus(args) -> Bus:
    log = (lambda kind, text: print(f"  [{kind}] {text}", file=sys.stderr)) if args.verbose else None
    if args.simulate:
        from .sim import SimulatedReader, SimulatedTransport

        transport = SimulatedTransport([SimulatedReader()], baud=args.baud)
    else:
        if not args.port:
            ports = list_ports()
            hint = ", ".join(p.device for p in ports) or "none found"
            raise SystemExit(f"--port is required (available: {hint})")
        transport = SerialTransport(args.port, args.baud)
    return Bus(transport, log=log)


def _key(text: Optional[str]) -> Optional[bytes]:
    if text is None:
        return None
    return parse_key(text)


def cmd_ports(args) -> int:
    ports = list_ports()
    if not ports:
        print("No serial ports found. Plug in the USB-RS485 adapter and check Device Manager > Ports.")
        return 0
    for p in ports:
        flag = "  <- likely USB-RS485 adapter" if p.likely_rs485_adapter else ""
        print(f"{p.label()}{flag}")
    return 0


def cmd_scan(args) -> int:
    bus = _open_bus(args)
    found = scan(bus, bauds=tuple(args.scan_baud) if args.scan_baud else SCAN_BAUD_ORDER,
                 stop_at_first=not args.all,
                 progress=(lambda text, frac: print(f"  {int(frac * 100):3d}% {text}", file=sys.stderr))
                 if args.verbose else None)
    if not found:
        print("No reader answered. Check 12 VDC power, RS-485 A/B polarity and the COM port.")
        return 1
    for f in found:
        print(f.describe())
        print()
    return 0


def cmd_info(args) -> int:
    bus = _open_bus(args)
    info = P.read_info(bus, args.addr, args.baud, _key(args.key), use_secure=args.key is not None)
    print(info.describe())
    return 0


def cmd_program(args) -> int:
    bus = _open_bus(args)
    for w in P.comset_warnings(args.addr, args.new_addr, args.new_baud):
        print(f"WARNING: {w}")
    result = P.program_reader(bus, args.addr, args.baud, args.new_addr, args.new_baud,
                              key=_key(args.key), use_secure=args.key is not None)
    print(result.describe())
    if not args.no_log:
        dev = result.device_id
        path = P.append_log({
            "port": args.port or "SIMULATOR", "action": "program",
            "serial_number": dev.serial_number if dev else "", "vendor_code": dev.vendor_hex if dev else "",
            "model": dev.model if dev else "", "firmware": dev.firmware if dev else "",
            "old_address": args.addr, "old_baud": args.baud, "new_address": result.new_address,
            "new_baud": result.new_baud, "verified": result.verified,
            "notes": " | ".join(result.warnings)})
        print(f"Logged to {path}")
    print()
    print(P.onguard_checklist(result.new_address, result.new_baud, device_id=result.device_id))
    return 0 if result.ok else 1


def cmd_verify(args) -> int:
    bus = _open_bus(args)
    res = P.verify_after_power_cycle(bus, args.addr, args.baud, args.serial)
    print(res.text)
    return 0 if res.ok else 1


def cmd_set_key(args) -> int:
    bus = _open_bus(args)
    new_key = os.urandom(16) if args.new_key == "random" else parse_key(args.new_key)
    msg = P.install_key(bus, args.addr, args.baud, new_key, _key(args.key))
    print(msg)
    print(f"KEY: {new_key.hex().upper()}")
    return 0


def cmd_test_sc(args) -> int:
    bus = _open_bus(args)
    print(P.test_secure_channel(bus, args.addr, args.baud, _key(args.key)))
    return 0


def _reader(args) -> Reader:
    bus = _open_bus(args)
    reader = Reader(bus, args.addr)
    if args.key is not None:
        reader.start_secure_channel(_key(args.key))
    return reader


def cmd_led(args) -> int:
    reader = _reader(args)
    color = C.LED_COLOR_BY_NAME[args.color]
    if args.permanent:
        reader.led(perm_on_color=color, cancel_temp=True)
    else:
        reader.led(temp_on_color=color, temp_on_time=args.on, temp_off_time=args.off, temp_timer=args.seconds * 10)
    print(f"LED set to {args.color}")
    return 0


def cmd_beep(args) -> int:
    reader = _reader(args)
    reader.buzzer(on_time=args.on, off_time=args.off, count=args.count)
    print("Beep sent")
    return 0


def cmd_watch(args) -> int:
    reader = _reader(args)
    print("Present a card (Ctrl+C to stop)...")
    end = time.monotonic() + args.seconds if args.seconds else None
    try:
        while end is None or time.monotonic() < end:
            for ev in reader.poll():
                print(ev.text)
                if ev.kind == "card":
                    reader.buzzer(count=1)
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_send(args) -> int:
    reader = _reader(args)
    code = int(args.code, 0)
    data = bytes.fromhex(args.data.replace(" ", "")) if args.data else b""
    reply = reader.command(code, data, raise_nak=False)
    print(f"{reply.name}: {describe_reply(reply.code, reply.data)}")
    return 0


def _library() -> CommandLibrary:
    try:
        return CommandLibrary.load()
    except Exception as exc:
        print(f"WARNING: could not read your command library: {exc}", file=sys.stderr)
        return CommandLibrary()


def cmd_commands(args) -> int:
    group = None
    for d in all_definitions(_library()):
        if d.group != group:
            group = d.group
            print(f"\n[{group}]")
        flag = "  (changes settings)" if d.changes_settings else ""
        print(f"  {d.name}{flag}")
        for prm in d.params:
            choices = f" one of: {', '.join(prm.choices)}" if prm.choices else ""
            print(f"      --set {prm.key}=...   {prm.label} (default {prm.default_display()}){choices}")
    return 0


def cmd_run(args) -> int:
    d = find_definition(args.name, _library())
    values = d.defaults()
    for item in args.set or []:
        key, sep, value = item.partition("=")
        if not sep or key not in values:
            raise ValueError(f"--set {item}: '{d.name}' takes {', '.join(values) or 'no settings'}")
        values[key] = value
    data = d.payload(values)
    print(f"Sending {d.preview(values)}")
    if d.changes_settings and not args.yes:
        print("This command can change the reader's configuration. Add --yes to send it.")
        return 2
    if d.secure and args.key is None:
        raise ValueError("This command needs a secure channel: add --key (32 hex digits or 'default')")
    reply = _reader(args).command(d.code, data, raise_nak=False)
    print(f"{reply.name}: {describe_reply(reply.code, reply.data)}")
    return 0 if reply.code != 0x41 else 1


def cmd_probe(args) -> int:
    from .osdp.probe import probe_commands, report

    reader = Reader(_open_bus(args), args.addr)
    header = f"Command support for the reader at address {args.addr} @ {args.baud} baud"
    try:
        dev = reader.identify()
        header += (f"\nSerial {dev.serial_number}, firmware {dev.firmware}, "
                   f"vendor code {dev.vendor_hex} ({dev.vendor_name})")
    except OsdpError as exc:
        raise OsdpError(f"Reader did not answer: {exc}") from exc
    print(report(probe_commands(reader), header + "\n"))
    return 0


def cmd_vendor(args) -> int:
    from .osdp.decode import parse_hex
    from .osdp.vendors import vendor_name

    code = parse_hex(args.code, name="vendor code", length=3)
    print(f"{'-'.join(f'{b:02X}' for b in code)}: {vendor_name(code)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="readerprog", description=f"{APP_NAME} {__version__} (command line)")
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p, addr=True):
        p.add_argument("--port", help="serial port of the USB-RS485 adapter, e.g. COM3")
        p.add_argument("--baud", type=int, default=9600, help="current reader baud rate (default 9600)")
        if addr:
            p.add_argument("--addr", type=int, default=1, help="current reader OSDP address (default 1)")
        p.add_argument("--simulate", action="store_true", help="use a simulated reader instead of hardware")
        p.add_argument("-v", "--verbose", action="store_true", help="print packet trace")
        return p

    sub.add_parser("ports", help="list serial ports").set_defaults(func=cmd_ports)

    p = common(sub.add_parser("scan", help="find a reader (address and baud rate)"), addr=False)
    p.add_argument("--all", action="store_true", help="keep scanning after the first reader")
    p.add_argument("--scan-baud", type=int, action="append", help="limit scan to this baud rate (repeatable)")
    p.set_defaults(func=cmd_scan)

    p = common(sub.add_parser("info", help="show reader identity, capabilities and status"))
    p.add_argument("--key", help="open a secure channel first (32 hex digits or 'default')")
    p.set_defaults(func=cmd_info)

    p = common(sub.add_parser("program", help="set the reader's OSDP address and baud rate"))
    p.add_argument("--new-addr", type=int, required=True)
    p.add_argument("--new-baud", type=int, default=9600, choices=C.BAUD_RATES)
    p.add_argument("--key", help="send the change over secure channel with this key ('default' = SCBK-D)")
    p.add_argument("--no-log", action="store_true", help="do not append to the programming log CSV")
    p.set_defaults(func=cmd_program)

    p = common(sub.add_parser("verify", help="after a power cycle, confirm the reader kept its settings"))
    p.add_argument("--serial", type=int, help="expected OSDP serial number")
    p.set_defaults(func=cmd_verify)

    p = common(sub.add_parser("set-key", help="install a secure channel base key (SCBK)"))
    p.add_argument("--new-key", required=True, help="32 hex digits, or 'random'")
    p.add_argument("--key", help="current key (omit for the default install key)")
    p.set_defaults(func=cmd_set_key)

    p = common(sub.add_parser("test-sc", help="test a secure channel session"))
    p.add_argument("--key", help="32 hex digits; omit for the default install key")
    p.set_defaults(func=cmd_test_sc)

    p = common(sub.add_parser("led", help="set the reader LED"))
    p.add_argument("color", choices=sorted(C.LED_COLOR_BY_NAME))
    p.add_argument("--permanent", action="store_true", help="set the steady (permanent) color")
    p.add_argument("--seconds", type=int, default=3)
    p.add_argument("--on", type=int, default=5, help="on time, 100 ms units")
    p.add_argument("--off", type=int, default=5, help="off time, 100 ms units")
    p.add_argument("--key")
    p.set_defaults(func=cmd_led)

    p = common(sub.add_parser("beep", help="sound the reader buzzer"))
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--on", type=int, default=3)
    p.add_argument("--off", type=int, default=3)
    p.add_argument("--key")
    p.set_defaults(func=cmd_beep)

    p = common(sub.add_parser("watch", help="poll the reader and print card reads"))
    p.add_argument("--seconds", type=int, default=0, help="stop after N seconds (default: until Ctrl+C)")
    p.add_argument("--key")
    p.set_defaults(func=cmd_watch)

    sub.add_parser("commands", help="list built-in and saved commands").set_defaults(func=cmd_commands)

    common(sub.add_parser("probe", help="check which OSDP commands the reader implements")).set_defaults(
        func=cmd_probe)

    p = sub.add_parser("vendor", help="look up who owns an OSDP vendor code, e.g. vendor 00-11-12")
    p.add_argument("code", help="3-byte vendor code as shown by 'info'")
    p.set_defaults(func=cmd_vendor)

    p = common(sub.add_parser("run", help="send a command from the library, e.g. run Buzzer --set count=3"))
    p.add_argument("name", help="command name as shown by 'readerprog commands'")
    p.add_argument("--set", action="append", metavar="KEY=VALUE", help="command setting (repeatable)")
    p.add_argument("--yes", action="store_true", help="confirm commands that change reader settings")
    p.add_argument("--key")
    p.set_defaults(func=cmd_run)

    p = common(sub.add_parser("send", help="send a raw OSDP command (advanced)"))
    p.add_argument("code", help="command code, e.g. 0x61")
    p.add_argument("--data", default="", help="payload in hex")
    p.add_argument("--key")
    p.set_defaults(func=cmd_send)
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (OsdpError, SecureChannelError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"ERROR opening port: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
