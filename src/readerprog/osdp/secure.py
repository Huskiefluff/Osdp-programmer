"""OSDP Secure Channel (AES-128) primitives.

Implements the session key derivation, cryptograms, MAC chaining and data
encryption described in Annex D of the OSDP specification. The same class is
used by the control-panel side (this tool) and by the built-in reader
simulator.
"""

from __future__ import annotations

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .constants import SCBK_DEFAULT


class SecureChannelError(Exception):
    pass


def _ecb(key: bytes, block: bytes) -> bytes:
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return enc.update(block) + enc.finalize()


def _cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return enc.update(data) + enc.finalize()


def _cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return dec.update(data) + dec.finalize()


def _invert(block: bytes) -> bytes:
    return bytes((~b) & 0xFF for b in block)


def parse_key(text: str) -> bytes:
    """Parse a 16-byte key given as 32 hex digits (spaces/colons allowed)."""
    cleaned = "".join(ch for ch in text if ch not in " :-_\t").strip()
    if cleaned.lower() in ("default", "scbk-d", "scbkd"):
        return SCBK_DEFAULT
    try:
        key = bytes.fromhex(cleaned)
    except ValueError as exc:
        raise ValueError("key must be 32 hexadecimal digits") from exc
    if len(key) != 16:
        raise ValueError(f"key must be 16 bytes (32 hex digits), got {len(key)} bytes")
    return key


class SecureSession:
    """State for one secure channel session between CP and one PD."""

    def __init__(self, scbk: bytes, rnd_a: bytes):
        if len(scbk) != 16:
            raise ValueError("SCBK must be 16 bytes")
        if len(rnd_a) != 8:
            raise ValueError("RND.A must be 8 bytes")
        self.scbk = bytes(scbk)
        self.rnd_a = bytes(rnd_a)
        self.rnd_b = b""
        self.s_enc = _ecb(scbk, bytes([0x01, 0x82]) + rnd_a[:6] + bytes(8))
        self.s_mac1 = _ecb(scbk, bytes([0x01, 0x01]) + rnd_a[:6] + bytes(8))
        self.s_mac2 = _ecb(scbk, bytes([0x01, 0x02]) + rnd_a[:6] + bytes(8))
        self.r_mac = bytes(16)
        self.c_mac = bytes(16)
        self.active = False

    @property
    def uses_default_key(self) -> bool:
        return self.scbk == SCBK_DEFAULT

    # ---- handshake -------------------------------------------------------
    def client_cryptogram(self, rnd_b: bytes) -> bytes:
        """Cryptogram the PD sends in osdp_CCRYPT."""
        return _ecb(self.s_enc, self.rnd_a + rnd_b)

    def server_cryptogram(self, rnd_b: bytes) -> bytes:
        """Cryptogram the CP sends in osdp_SCRYPT."""
        return _ecb(self.s_enc, rnd_b + self.rnd_a)

    def initial_rmac(self, server_cryptogram: bytes) -> bytes:
        return _ecb(self.s_mac2, _ecb(self.s_mac1, server_cryptogram))

    def establish(self, rnd_b: bytes, server_cryptogram: bytes) -> None:
        self.rnd_b = bytes(rnd_b)
        self.r_mac = self.initial_rmac(server_cryptogram)
        self.active = True

    # ---- MAC -----------------------------------------------------------
    def _mac(self, iv: bytes, msg: bytes) -> bytes:
        buf = bytearray(msg)
        if len(buf) % 16:
            buf.append(0x80)
            while len(buf) % 16:
                buf.append(0)
        if len(buf) > 16:
            chained = _cbc_encrypt(self.s_mac1, iv, bytes(buf[:-16]))
            iv = chained[-16:]
        return _cbc_encrypt(self.s_mac2, iv, bytes(buf[-16:]))

    def mac_command(self, msg: bytes) -> bytes:
        """MAC for a CP->PD message; becomes the new C-MAC."""
        self.c_mac = self._mac(self.r_mac, msg)
        return self.c_mac

    def mac_reply(self, msg: bytes) -> bytes:
        """MAC for a PD->CP message; becomes the new R-MAC."""
        self.r_mac = self._mac(self.c_mac, msg)
        return self.r_mac

    # ---- data encryption ---------------------------------------------
    @staticmethod
    def _pad(data: bytes) -> bytes:
        buf = bytearray(data)
        buf.append(0x80)
        while len(buf) % 16:
            buf.append(0)
        return bytes(buf)

    @staticmethod
    def _unpad(data: bytes) -> bytes:
        stripped = data.rstrip(b"\x00")
        if not stripped or stripped[-1] != 0x80:
            raise SecureChannelError("bad padding in decrypted data (wrong key?)")
        return stripped[:-1]

    def encrypt_command_data(self, data: bytes) -> bytes:
        return _cbc_encrypt(self.s_enc, _invert(self.r_mac), self._pad(data))

    def decrypt_command_data(self, data: bytes) -> bytes:
        if len(data) % 16:
            raise SecureChannelError("encrypted data is not a multiple of 16 bytes")
        return self._unpad(_cbc_decrypt(self.s_enc, _invert(self.r_mac), data))

    def encrypt_reply_data(self, data: bytes) -> bytes:
        return _cbc_encrypt(self.s_enc, _invert(self.c_mac), self._pad(data))

    def decrypt_reply_data(self, data: bytes) -> bytes:
        if len(data) % 16:
            raise SecureChannelError("encrypted data is not a multiple of 16 bytes")
        return self._unpad(_cbc_decrypt(self.s_enc, _invert(self.c_mac), data))
