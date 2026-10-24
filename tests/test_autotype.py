from __future__ import annotations

import ctypes
import unittest

from vault import autotype


class AutoTypeTests(unittest.TestCase):
    def test_input_struct_size(self):
        self.assertEqual(ctypes.sizeof(autotype.INPUT), 40)
        self.assertEqual(ctypes.sizeof(autotype.KEYBDINPUT), 24)

    def test_parse_and_format_hotkey(self):
        self.assertEqual(autotype.parse_hotkey("Ctrl+Alt+K"), (autotype.MOD_CONTROL | autotype.MOD_ALT, ord("K")))
        self.assertEqual(autotype.parse_hotkey("ctrl+shift+p"), (autotype.MOD_CONTROL | autotype.MOD_SHIFT, ord("P")))
        self.assertEqual(autotype.format_hotkey("ctrl+alt+k"), "Ctrl+Alt+K")
        self.assertEqual(autotype.format_hotkey("Win+Alt+1"), "Alt+Win+1")
        with self.assertRaises(ValueError):
            autotype.parse_hotkey("K")
        with self.assertRaises(ValueError):
            autotype.parse_hotkey("Ctrl+")
        with self.assertRaises(ValueError):
            autotype.parse_hotkey("Ctrl+Alt+Nope")

    def test_key_event(self):
        event = autotype._key_event(vk=0x41)
        self.assertEqual(event.type, autotype.INPUT_KEYBOARD)
        self.assertEqual(event.ki.wVk, 0x41)
        self.assertEqual(event.ki.dwFlags, 0)


if __name__ == "__main__":
    unittest.main()
