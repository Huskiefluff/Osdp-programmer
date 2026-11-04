"""Byte transports: a real serial port (USB-RS485 adapter) and helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Protocol

# USB vendor IDs of the bridge chips found in common USB-RS485 adapters.
KNOWN_USB_SERIAL_VIDS = {
    0x0403: "FTDI",
    0x10C4: "Silicon Labs CP210x",
    0x1A86: "WCH CH340/CH341",
    0x067B: "Prolific PL2303",
    0x0557: "ATEN",
    0x04E2: "Exar",
}


class Transport(Protocol):
    baud: int

    def write(self, data: bytes) -> None: ...

    def read(self, timeout: float) -> bytes: ...

    def set_baud(self, baud: int) -> None: ...

    def flush_input(self) -> None: ...

    def close(self) -> None: ...


@dataclass
class PortInfo:
    device: str
    description: str
    hwid: str
    vid: Optional[int] = None
    pid: Optional[int] = None

    @property
    def chip(self) -> str:
        return KNOWN_USB_SERIAL_VIDS.get(self.vid or -1, "")

    @property
    def likely_rs485_adapter(self) -> bool:
        return self.vid in KNOWN_USB_SERIAL_VIDS

    def label(self) -> str:
        extra = f" [{self.chip}]" if self.chip else ""
        return f"{self.device} - {self.description}{extra}"


def list_ports() -> list[PortInfo]:
    from serial.tools import list_ports as lp

    ports = []
    for p in lp.comports():
        ports.append(PortInfo(p.device, p.description or "", p.hwid or "", p.vid, p.pid))
    # Show likely USB-RS485 adapters first, then by name.
    ports.sort(key=lambda p: (not p.likely_rs485_adapter, p.device))
    return ports


class SerialTransport:
    """OSDP over a serial port: 8 data bits, no parity, 1 stop bit."""

    def __init__(self, port: str, baud: int = 9600):
        import serial

        self.port_name = port
        self.baud = baud
        self._ser = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0,
            write_timeout=2,
        )

    def write(self, data: bytes) -> None:
        self._ser.write(data)
        self._ser.flush()

    def read(self, timeout: float) -> bytes:
        deadline = time.monotonic() + max(timeout, 0)
        out = bytearray()
        while True:
            waiting = self._ser.in_waiting
            if waiting:
                out.extend(self._ser.read(waiting))
                # Give the rest of a frame a moment to arrive.
                time.sleep(0.002)
                if not self._ser.in_waiting:
                    return bytes(out)
                continue
            if time.monotonic() >= deadline:
                return bytes(out)
            time.sleep(0.002)

    def set_baud(self, baud: int) -> None:
        self._ser.baudrate = baud
        self.baud = baud

    def flush_input(self) -> None:
        self._ser.reset_input_buffer()

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:
            pass
