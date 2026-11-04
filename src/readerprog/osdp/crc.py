"""OSDP message check characters.

OSDP uses CRC-16/AUG-CCITT (poly 0x1021, init 0x1D0F, no reflection, no xorout)
when the CRC bit of the control byte is set, otherwise an 8-bit two's
complement checksum.
"""

from __future__ import annotations


def _make_table() -> list[int]:
    table = []
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
        table.append(crc & 0xFFFF)
    return table


_TABLE = _make_table()


def crc16(data: bytes, init: int = 0x1D0F) -> int:
    crc = init
    for b in data:
        crc = ((crc << 8) & 0xFFFF) ^ _TABLE[((crc >> 8) ^ b) & 0xFF]
    return crc


def checksum8(data: bytes) -> int:
    return (-sum(data)) & 0xFF
