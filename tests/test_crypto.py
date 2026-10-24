from __future__ import annotations

import unittest

from vault.crypto import (
    AesGcmKey,
    TamperedData,
    WrongPassword,
    derive_master_keys,
    make_verifier,
    new_salt,
    open_sealed,
    password_strength,
    random_password,
    seal,
    verify_master_password,
)


class CryptoTests(unittest.TestCase):
    def test_aes_256_gcm_nist_vector(self):
        key = bytes(32)
        with AesGcmKey(key) as aes:
            body = aes.encrypt(bytes(16), b"", nonce=bytes(12))
        self.assertEqual(body[:16].hex(), "cea7403d4d606b6e074ec5d3baf39d18")
        self.assertEqual(body[16:].hex(), "d0d1c8a799996bf0265b98b5d48ab919")

    def test_seal_roundtrip_and_aad_binding(self):
        with AesGcmKey(bytes(range(32))) as aes:
            blob = seal(aes, "账号 abc 密码".encode("utf-8"), b"record-1")
            self.assertEqual(open_sealed(aes, blob, b"record-1").decode("utf-8"), "账号 abc 密码")
            with self.assertRaises(TamperedData):
                open_sealed(aes, blob, b"record-2")

    def test_tamper_is_rejected(self):
        with AesGcmKey(bytes(32)) as aes:
            blob = bytearray(seal(aes, b"secret", b"r"))
            blob[-1] ^= 0x01
            with self.assertRaises(TamperedData):
                open_sealed(aes, bytes(blob), b"r")
            with self.assertRaises(TamperedData):
                open_sealed(aes, b"x", b"r")

    def test_master_key_derivation_and_verifier(self):
        salt = new_salt()
        enc1, mac1 = derive_master_keys("correct horse battery staple", salt)
        enc2, mac2 = derive_master_keys("correct horse battery staple", salt)
        self.assertEqual(enc1, enc2)
        self.assertEqual(mac1, mac2)
        self.assertEqual(len(enc1), 32)
        self.assertEqual(len(mac1), 32)
        verifier = make_verifier(mac1)
        self.assertEqual(verify_master_password("correct horse battery staple", salt, verifier), (enc1, mac1))
        with self.assertRaises(WrongPassword):
            verify_master_password("wrong password", salt, verifier)

    def test_random_password(self):
        pwd = random_password(24)
        self.assertEqual(len(pwd), 24)
        self.assertTrue(any(c.islower() for c in pwd))
        self.assertTrue(any(c.isupper() for c in pwd))
        self.assertTrue(any(c.isdigit() for c in pwd))
        self.assertTrue(any(not c.isalnum() for c in pwd))
        self.assertNotEqual(random_password(24), random_password(24))
        score, label = password_strength(pwd)
        self.assertGreaterEqual(score, 3)
        self.assertIn(label, {"较强", "很强"})


if __name__ == "__main__":
    unittest.main()
