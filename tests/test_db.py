from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from vault.db import (
    DuplicateEntry,
    Entry,
    VaultDB,
    VaultLocked,
    ValidationError,
    WrongPassword,
)

TEST_ROOT = Path(__file__).resolve().parents[1] / "test-data" / "tests"


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        self.dir = TEST_ROOT / uuid.uuid4().hex
        self.dir.mkdir()
        self.path = self.dir / "vault.db"

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_create_crud_search_and_reopen(self):
        db = VaultDB.create(self.path, "Master!2345")
        self.assertTrue(db.is_unlocked)
        self.assertEqual(db.count(), 0)
        a = db.add_entry(
            Entry(
                title="GitHub",
                account="alice@example.com",
                password="S3cret!pass",
                url="https://github.com",
                category="工作",
                tags=["dev"],
            )
        )
        b = db.add_entry(Entry(title="GitHub", account="bob@example.com", password="Other!pass", category="工作"))
        self.assertEqual(db.count(), 2)
        self.assertEqual(len(db.search("github")), 2)
        self.assertEqual([e.account for e in db.search("bob")], ["bob@example.com"])
        self.assertEqual(db.categories(), ["工作"])

        a.notes = "2FA enabled"
        a.favorite = True
        db.update_entry(a)
        self.assertTrue(db.get_entry(a.id).favorite)
        self.assertEqual(db.get_entry(a.id).notes, "2FA enabled")

        db.lock()
        self.assertFalse(db.is_unlocked)
        with self.assertRaises(VaultLocked):
            db.list_entries()

        reopened = VaultDB.open(self.path, "Master!2345")
        self.assertEqual(reopened.count(), 2)
        self.assertEqual(reopened.get_entry(a.id).password, "S3cret!pass")
        self.assertEqual(reopened.get_entry(b.id).tags, [])
        reopened.close()

    def test_duplicate_title_account_is_blocked(self):
        db = VaultDB.create(self.path, "Master!2345")
        db.add_entry(Entry(title="GitHub", account="alice@example.com", password="a"))
        with self.assertRaises(DuplicateEntry):
            db.add_entry(Entry(title=" github ", account="ALICE@example.com", password="b"))
        db.close()

    def test_wrong_password_and_validation(self):
        VaultDB.create(self.path, "Master!2345").close()
        with self.assertRaises(WrongPassword):
            VaultDB.open(self.path, "wrong-password")
        db = VaultDB.open(self.path, "Master!2345")
        with self.assertRaises(ValidationError):
            db.add_entry(Entry(title="", account="a", password="b"))
        with self.assertRaises(ValidationError):
            db.add_entry(Entry(title="Site", account="", password="b"))
        db.close()

    def test_change_master_password_reencrypts(self):
        db = VaultDB.create(self.path, "OldMaster!23")
        entry = db.add_entry(Entry(title="Mail", account="me@example.com", password="Mail!5678", tags=["mail"]))
        db.change_master_password("OldMaster!23", "NewMaster!89")
        self.assertEqual(db.count(), 1)
        self.assertEqual(db.get_entry(entry.id).password, "Mail!5678")
        db.close()
        with self.assertRaises(WrongPassword):
            VaultDB.open(self.path, "OldMaster!23")
        reopened = VaultDB.open(self.path, "NewMaster!89")
        self.assertEqual(reopened.get_entry(entry.id).tags, ["mail"])
        reopened.close()

    def test_backup_and_settings(self):
        db = VaultDB.create(self.path, "Master!2345")
        db.add_entry(Entry(title="Site", account="user", password="pass"))
        db.update_settings(auto_lock_minutes=9, hotkey="Ctrl+Alt+K")
        backed = db.backup(self.dir / "backups")
        self.assertTrue(backed.exists())
        self.assertGreater(backed.stat().st_size, 0)
        db.close()
        copy = VaultDB.open(backed, "Master!2345")
        self.assertEqual(copy.count(), 1)
        self.assertEqual(copy.settings["auto_lock_minutes"], 9)
        copy.close()

    def test_plaintext_is_not_in_database(self):
        db = VaultDB.create(self.path, "Master!2345")
        db.add_entry(
            Entry(
                title="SensitiveSite",
                account="secret-account@example.com",
                password="SuperSecretPassword!",
                notes="private note",
            )
        )
        db.close()
        raw = self.path.read_bytes()
        self.assertNotIn(b"SensitiveSite", raw)
        self.assertNotIn(b"secret-account@example.com", raw)
        self.assertNotIn(b"SuperSecretPassword!", raw)
        self.assertNotIn(b"private note", raw)


if __name__ == "__main__":
    unittest.main()
