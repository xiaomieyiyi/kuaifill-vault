from __future__ import annotations

import unittest

from vault import theme


class ThemeTests(unittest.TestCase):
    def test_skins_have_required_colors(self):
        self.assertGreaterEqual(len(theme.skin_names()), 4)
        for name in theme.skin_names():
            colors = theme.skin_colors(name)
            for key in ("accent", "accent_dark", "accent_soft", "sidebar", "bg", "swatch"):
                self.assertIn(key, colors)
                self.assertTrue(colors[key].startswith("#"))

    def test_apply_skin_changes_accent(self):
        original = theme.CURRENT_SKIN
        try:
            applied = theme.apply_skin("森林绿")
            self.assertEqual(applied, "森林绿")
            self.assertEqual(theme.CURRENT_SKIN, "森林绿")
            self.assertEqual(theme.ACCENT, theme.SKINS["森林绿"]["accent"])
            self.assertEqual(theme.BG, theme.SKINS["森林绿"]["bg"])
        finally:
            theme.apply_skin(original)

    def test_unknown_skin_falls_back(self):
        original = theme.CURRENT_SKIN
        try:
            self.assertEqual(theme.apply_skin("不存在的皮肤"), theme.DEFAULT_SKIN)
            self.assertEqual(theme.CURRENT_SKIN, theme.DEFAULT_SKIN)
        finally:
            theme.apply_skin(original)

    def test_default_skin_exists(self):
        self.assertIn(theme.DEFAULT_SKIN, theme.SKINS)


if __name__ == "__main__":
    unittest.main()
