from __future__ import annotations

import ctypes
import os
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

    def test_empty_window_helpers(self):
        self.assertEqual(autotype.window_title(0), "")
        self.assertEqual(autotype.window_class(0), "")
        self.assertEqual(autotype.window_pid(0), 0)
        self.assertEqual(autotype.window_rect(0), (0, 0, 0, 0))
        self.assertFalse(autotype.TargetWindow().valid)

    def test_click_point_prefers_caret_then_cursor(self):
        target = autotype.TargetWindow(
            hwnd=0,
            rect=(100, 100, 400, 300),
            caret=(150, 160),
            cursor=(200, 200),
        )
        self.assertEqual(autotype.click_point(target), (150, 160))
        target.caret = None
        self.assertEqual(autotype.click_point(target), (200, 200))
        target.cursor = (10, 10)  # 窗口外
        self.assertIsNone(autotype.click_point(target))

    def test_click_point_uses_focus_control_center(self):
        target = autotype.TargetWindow(hwnd=0, rect=(100, 100, 400, 300), focus=0)
        self.assertIsNone(autotype.click_point(target))

    def test_focus_level_outside_process(self):
        target = autotype.TargetWindow(hwnd=0, pid=os.getpid())
        self.assertEqual(autotype.focus_level(target), 0)

    def test_target_describe(self):
        target = autotype.TargetWindow(hwnd=1, pid=42, title="登录窗口", class_name="LoginWnd")
        self.assertIn("登录窗口", target.describe())

    def test_process_integrity(self):
        level = autotype.process_integrity(os.getpid())
        self.assertIn(level, (0, 0x1000, 0x2000, 0x3000, 0x4000))
        # 自己和自己权限一致，不应判定为被拦截
        self.assertEqual(autotype.integrity_blocked(os.getpid()), "")

    def test_process_integrity_invalid_pid(self):
        self.assertEqual(autotype.process_integrity(0), 0)
        self.assertEqual(autotype.integrity_blocked(0), "")


if __name__ == "__main__":
    unittest.main()
