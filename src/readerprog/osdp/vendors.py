"""Identify the company behind an OSDP vendor code (an IEEE OUI).

The full IEEE registry is used when the optional ``netaddr`` package is
installed. A small built-in table of access-control vendors is the fallback;
every entry below was checked against the IEEE MA-L registry (November 2026).
Veridt and IDFactors have no OUI of their own in that registry, so their
readers report another company's code (Veridt Stealth readers seen in the
field report 00-11-12, registered to Honeywell CMSS).
"""

from __future__ import annotations

from typing import Optional

KNOWN_OUIS = {
    "00-06-8E": "HID Corporation",
    "00-06-33": "Crossmatch Technologies/HID Global",
    "00-30-8E": "Crossmatch Technologies/HID Global",
    "00-11-12": "Honeywell CMSS",
    "00-23-0F": "Hirsch Electronics Corporation",
    "00-0F-E5": "Mercury Security Corporation",
    "58-FC-C8": "LenelS2 Carrier",
    "5C-26-23": "WaveLynx Technologies Corporation",
    "68-AB-8A": "RF IDeas",
    "2C-17-E0": "STid (Systemes et Technologies Identification)",
    "00-17-7A": "ASSA ABLOY AB",
}


def _fmt(code: bytes) -> str:
    return "-".join(f"{b:02X}" for b in code[:3])


def _registry_lookup(oui: str) -> Optional[str]:
    try:
        from netaddr import EUI
        from netaddr.core import NotRegisteredError
    except ImportError:
        return None
    try:
        return EUI(oui + "-00-00-00").oui.registration().org
    except (NotRegisteredError, IndexError, ValueError, OSError):
        return None
    except Exception:
        return None


def lookup(oui: str) -> Optional[str]:
    oui = oui.upper()
    return _registry_lookup(oui) or KNOWN_OUIS.get(oui)


def vendor_name(code: bytes) -> str:
    """Human readable owner of a 3-byte vendor code as reported by osdp_PDID.

    Some devices send the OUI least-significant byte first, so the reversed
    order is tried as well.
    """
    if len(code) < 3:
        return "unknown"
    forward = _fmt(code)
    name = lookup(forward)
    if name:
        return name
    reverse = _fmt(bytes(reversed(code[:3])))
    name = lookup(reverse)
    if name:
        return f"{name} (bytes reversed: {reverse})"
    return "not in the IEEE registry"
