"""Enterprise WeChat message encryption/decryption for smart bot callback.

Implements the official WXBizMsgCrypt protocol:
- URL verification (GET callback)
- Message decryption (POST callback)
- Reply encryption

Based on: https://developer.work.weixin.qq.com/document/path/90968
"""

import base64
import hashlib
import json
import struct
import time

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes


class WXBizMsgCrypt:
    def __init__(self, token: str, encoding_aes_key: str, corp_id: str):
        self.token = token
        self.corp_id = corp_id
        self.aes_key = base64.b64decode(encoding_aes_key + "=")
        if len(self.aes_key) != 32:
            raise ValueError(f"EncodingAESKey must decode to 32 bytes, got {len(self.aes_key)}")

    def _sha1(self, *parts: str) -> str:
        raw = "".join(sorted(parts))
        return hashlib.sha1(raw.encode()).hexdigest()

    def _pkcs7_pad(self, data: bytes, block_size: int = 32) -> bytes:
        pad_len = block_size - (len(data) % block_size)
        return data + bytes([pad_len] * pad_len)

    def _pkcs7_unpad(self, data: bytes) -> bytes:
        pad_len = data[-1]
        if pad_len < 1 or pad_len > 32:
            raise ValueError("Invalid PKCS7 padding")
        return data[:-pad_len]

    def _decrypt(self, ciphertext: bytes) -> bytes:
        """AES-256-CBC decrypt. IV is first 16 bytes of AES key."""
        iv = self.aes_key[:16]
        cipher = AES.new(self.aes_key, AES.MODE_CBC, iv)
        return cipher.decrypt(ciphertext)

    def _encrypt(self, plaintext: bytes) -> bytes:
        """AES-256-CBC encrypt. IV is first 16 bytes of AES key."""
        iv = self.aes_key[:16]
        cipher = AES.new(self.aes_key, AES.MODE_CBC, iv)
        return cipher.encrypt(plaintext)

    def verify_url(
        self,
        msg_signature: str,
        timestamp: str,
        nonce: str,
        echostr: str,
    ) -> tuple[int, str]:
        """Verify callback URL (GET). Returns (0, plaintext_echostr) on success."""
        signature = self._sha1(self.token, timestamp, nonce, echostr)
        if signature != msg_signature:
            return (-1, "signature mismatch")

        try:
            plaintext = self._decrypt(base64.b64decode(echostr))
            plaintext = self._pkcs7_unpad(plaintext)
        except Exception as e:
            return (-1, f"decrypt failed: {e}")

        # Parse: 16 bytes random + 4 bytes msg_len + msg + receiveid
        msg_len = struct.unpack("!I", plaintext[16:20])[0]
        msg = plaintext[20:20 + msg_len].decode("utf-8")
        receiveid = plaintext[20 + msg_len:].decode("utf-8")

        # Smart bot: receiveid is empty string
        if receiveid and receiveid != self.corp_id:
            return (-1, f"receiveid mismatch: {receiveid}")

        return (0, msg)

    def decrypt_msg(
        self,
        msg_signature: str,
        timestamp: str,
        nonce: str,
        encrypted_body: str,
    ) -> tuple[int, str]:
        """Decrypt incoming message (POST). Returns (0, json_string) on success."""
        signature = self._sha1(self.token, timestamp, nonce, encrypted_body)
        if signature != msg_signature:
            return (-1, "signature mismatch")

        try:
            plaintext = self._decrypt(base64.b64decode(encrypted_body))
            plaintext = self._pkcs7_unpad(plaintext)
        except Exception as e:
            return (-1, f"decrypt failed: {e}")

        # Parse: 16 bytes random + 4 bytes msg_len + msg + receiveid
        msg_len = struct.unpack("!I", plaintext[16:20])[0]
        msg = plaintext[20:20 + msg_len].decode("utf-8")
        receiveid = plaintext[20 + msg_len:].decode("utf-8")

        if receiveid and receiveid != self.corp_id:
            return (-1, f"receiveid mismatch: {receiveid}")

        return (0, msg)

    def encrypt_msg(self, reply: str, nonce: str) -> str:
        """Encrypt reply message. Returns JSON string with encrypt/msgsignature/timestamp/nonce."""
        timestamp = str(int(time.time()))
        receiveid = ""  # smart bot receiveid is empty
        reply_bytes = reply.encode("utf-8")

        # Build: 16 bytes random + 4 bytes msg_len + msg + receiveid
        random_bytes = get_random_bytes(16)
        msg_len = struct.pack("!I", len(reply_bytes))
        plaintext = random_bytes + msg_len + reply_bytes + receiveid.encode("utf-8")
        plaintext = self._pkcs7_pad(plaintext)

        encrypted = self._encrypt(plaintext)
        encrypt_b64 = base64.b64encode(encrypted).decode("utf-8")

        signature = self._sha1(self.token, timestamp, nonce, encrypt_b64)

        return json.dumps(
            {
                "encrypt": encrypt_b64,
                "msgsignature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
            }
        )
