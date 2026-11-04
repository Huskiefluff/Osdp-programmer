"""OSDP (SIA Open Supervised Device Protocol) constants.

Values follow the public SIA OSDP v2.2 specification (IEC 60839-11-5).
"""

from __future__ import annotations

SOM = 0x53            # Start of message
MARK = 0xFF           # Optional line-idle marker sent before SOM (OSDP 2.2)
BROADCAST_ADDR = 0x7F
REPLY_FLAG = 0x80     # Set on the address byte of every PD reply
MAX_ADDR = 126        # 0..126 are valid PD addresses, 127 is broadcast

# Control byte bits
CTRL_SQN_MASK = 0x03
CTRL_CRC = 0x04
CTRL_SCB = 0x08

# Standard baud rates a reader may be configured to use.
BAUD_RATES = (9600, 19200, 38400, 57600, 115200, 230400)

# Default secure channel base key (SCBK-D) used in "install mode".
SCBK_DEFAULT = bytes(range(0x30, 0x40))

# --------------------------------------------------------------------------
# Commands (CP -> PD)
# --------------------------------------------------------------------------
CMD_POLL = 0x60
CMD_ID = 0x61
CMD_CAP = 0x62
CMD_LSTAT = 0x64
CMD_ISTAT = 0x65
CMD_OSTAT = 0x66
CMD_RSTAT = 0x67
CMD_OUT = 0x68
CMD_LED = 0x69
CMD_BUZ = 0x6A
CMD_TEXT = 0x6B
CMD_COMSET = 0x6E
CMD_BIOREAD = 0x73
CMD_BIOMATCH = 0x74
CMD_KEYSET = 0x75
CMD_CHLNG = 0x76
CMD_SCRYPT = 0x77
CMD_ACURXSIZE = 0x7B
CMD_FILETRANSFER = 0x7C
CMD_MFG = 0x80
CMD_XWR = 0xA1
CMD_ABORT = 0xA2
CMD_PIVDATA = 0xA3
CMD_GENAUTH = 0xA4
CMD_CRAUTH = 0xA5
CMD_KEEPACTIVE = 0xA7

COMMAND_NAMES = {
    CMD_POLL: "osdp_POLL",
    CMD_ID: "osdp_ID",
    CMD_CAP: "osdp_CAP",
    CMD_LSTAT: "osdp_LSTAT",
    CMD_ISTAT: "osdp_ISTAT",
    CMD_OSTAT: "osdp_OSTAT",
    CMD_RSTAT: "osdp_RSTAT",
    CMD_OUT: "osdp_OUT",
    CMD_LED: "osdp_LED",
    CMD_BUZ: "osdp_BUZ",
    CMD_TEXT: "osdp_TEXT",
    CMD_COMSET: "osdp_COMSET",
    CMD_BIOREAD: "osdp_BIOREAD",
    CMD_BIOMATCH: "osdp_BIOMATCH",
    CMD_KEYSET: "osdp_KEYSET",
    CMD_CHLNG: "osdp_CHLNG",
    CMD_SCRYPT: "osdp_SCRYPT",
    CMD_ACURXSIZE: "osdp_ACURXSIZE",
    CMD_FILETRANSFER: "osdp_FILETRANSFER",
    CMD_MFG: "osdp_MFG",
    CMD_XWR: "osdp_XWR",
    CMD_ABORT: "osdp_ABORT",
    CMD_PIVDATA: "osdp_PIVDATA",
    CMD_GENAUTH: "osdp_GENAUTH",
    CMD_CRAUTH: "osdp_CRAUTH",
    CMD_KEEPACTIVE: "osdp_KEEPACTIVE",
}

# --------------------------------------------------------------------------
# Replies (PD -> CP)
# --------------------------------------------------------------------------
REPLY_ACK = 0x40
REPLY_NAK = 0x41
REPLY_PDID = 0x45
REPLY_PDCAP = 0x46
REPLY_LSTATR = 0x48
REPLY_ISTATR = 0x49
REPLY_OSTATR = 0x4A
REPLY_RSTATR = 0x4B
REPLY_RAW = 0x50
REPLY_FMT = 0x51
REPLY_KEYPAD = 0x53
REPLY_COM = 0x54
REPLY_BIOREADR = 0x57
REPLY_BIOMATCHR = 0x58
REPLY_CCRYPT = 0x76
REPLY_RMAC_I = 0x78
REPLY_BUSY = 0x79
REPLY_FTSTAT = 0x7A
REPLY_PIVDATAR = 0x80
REPLY_GENAUTHR = 0x81
REPLY_CRAUTHR = 0x82
REPLY_MFGSTATR = 0x83
REPLY_MFGERRR = 0x84
REPLY_MFGREP = 0x90
REPLY_XRD = 0xB1

REPLY_NAMES = {
    REPLY_ACK: "osdp_ACK",
    REPLY_NAK: "osdp_NAK",
    REPLY_PDID: "osdp_PDID",
    REPLY_PDCAP: "osdp_PDCAP",
    REPLY_LSTATR: "osdp_LSTATR",
    REPLY_ISTATR: "osdp_ISTATR",
    REPLY_OSTATR: "osdp_OSTATR",
    REPLY_RSTATR: "osdp_RSTATR",
    REPLY_RAW: "osdp_RAW",
    REPLY_FMT: "osdp_FMT",
    REPLY_KEYPAD: "osdp_KEYPAD",
    REPLY_COM: "osdp_COM",
    REPLY_BIOREADR: "osdp_BIOREADR",
    REPLY_BIOMATCHR: "osdp_BIOMATCHR",
    REPLY_CCRYPT: "osdp_CCRYPT",
    REPLY_RMAC_I: "osdp_RMAC_I",
    REPLY_BUSY: "osdp_BUSY",
    REPLY_FTSTAT: "osdp_FTSTAT",
    REPLY_PIVDATAR: "osdp_PIVDATAR",
    REPLY_GENAUTHR: "osdp_GENAUTHR",
    REPLY_CRAUTHR: "osdp_CRAUTHR",
    REPLY_MFGSTATR: "osdp_MFGSTATR",
    REPLY_MFGERRR: "osdp_MFGERRR",
    REPLY_MFGREP: "osdp_MFGREP",
    REPLY_XRD: "osdp_XRD",
}

NAK_REASONS = {
    0x00: "No error",
    0x01: "Message check character(s) error (bad CRC/checksum)",
    0x02: "Command length error",
    0x03: "Unknown command code (not supported by this reader)",
    0x04: "Unexpected sequence number",
    0x05: "Unsupported security block or security conditions not met",
    0x06: "Encrypted communication required to process this command",
    0x07: "Biometric type not supported",
    0x08: "Biometric format not supported",
    0x09: "Unable to process command record",
}

# --------------------------------------------------------------------------
# Secure channel block types
# --------------------------------------------------------------------------
SCS_11 = 0x11  # CHLNG     (CP->PD)
SCS_12 = 0x12  # CCRYPT    (PD->CP)
SCS_13 = 0x13  # SCRYPT    (CP->PD)
SCS_14 = 0x14  # RMAC_I    (PD->CP)
SCS_15 = 0x15  # CP->PD, MAC only, data not encrypted
SCS_16 = 0x16  # PD->CP, MAC only, data not encrypted
SCS_17 = 0x17  # CP->PD, MAC + encrypted data
SCS_18 = 0x18  # PD->CP, MAC + encrypted data

# --------------------------------------------------------------------------
# PD capability function codes (osdp_PDCAP)
# --------------------------------------------------------------------------
CAPABILITY_NAMES = {
    1: "Contact status monitoring (inputs)",
    2: "Output control",
    3: "Card data format",
    4: "Reader LED control",
    5: "Reader audible output",
    6: "Reader text output",
    7: "Time keeping (deprecated)",
    8: "Check character support (CRC-16)",
    9: "Communication security (secure channel)",
    10: "Receive buffer size",
    11: "Largest combined message size",
    12: "Smart card support",
    13: "Readers",
    14: "Biometrics",
    15: "Secure PIN entry",
    16: "OSDP version",
}

LED_COLORS = {
    0: "Black/off",
    1: "Red",
    2: "Green",
    3: "Amber",
    4: "Blue",
    5: "Magenta",
    6: "Cyan",
    7: "White",
}
LED_COLOR_BY_NAME = {
    "off": 0, "black": 0, "red": 1, "green": 2, "amber": 3,
    "blue": 4, "magenta": 5, "cyan": 6, "white": 7,
}

KEY_TYPE_SCBK = 0x01


def command_name(code: int) -> str:
    return COMMAND_NAMES.get(code, f"cmd 0x{code:02X}")


def reply_name(code: int) -> str:
    return REPLY_NAMES.get(code, f"reply 0x{code:02X}")
