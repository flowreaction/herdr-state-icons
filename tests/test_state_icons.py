import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import state_icons


class SettingsTests(unittest.TestCase):
    def test_defaults_use_braille_and_nerd_font_glyphs(self) -> None:
        settings = state_icons.Settings()

        self.assertEqual(settings.glyph("working", 0), "⣷")
        self.assertEqual(settings.glyph("working", 8), "⣷")
        self.assertEqual(settings.glyph("done", 0), "󰄬")
        self.assertEqual(settings.glyph("blocked", 0), "󰅖")
        self.assertEqual(settings.glyph("missing", 0), "󰋗")

    def test_loads_custom_icons_and_animation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                '[icons]\nworking = ["a", "b"]\ndone = "D"\n'
                '[animation]\nframe-seconds = 0.25\ndone-hold-seconds = 2\n'
            )

            settings = state_icons.load_settings(path)

        self.assertEqual(settings.glyph("working", 3), "b")
        self.assertEqual(settings.glyph("done", 0), "D")
        self.assertEqual(settings.frame_seconds, 0.25)
        self.assertEqual(settings.done_hold_seconds, 2)

    def test_invalid_values_fall_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                '[icons]\nworking = []\ndone = 1\n'
                '[animation]\nframe-seconds = -1\ndone-hold-seconds = "long"\n'
            )

            settings = state_icons.load_settings(path)

        self.assertEqual(settings.frames, state_icons.DEFAULT_FRAMES)
        self.assertEqual(settings.glyph("done", 0), "󰄬")
        self.assertEqual(settings.frame_seconds, 0.15)
        self.assertEqual(settings.done_hold_seconds, 6.0)


class LifecycleTests(unittest.TestCase):
    def test_holds_done_after_working_then_expires(self) -> None:
        status, deadline = state_icons.resolve_status("idle", "working", 0.0, 10.0, 6.0)
        self.assertEqual((status, deadline), ("done", 16.0))

        status, deadline = state_icons.resolve_status("idle", "idle", deadline, 16.0, 6.0)
        self.assertEqual((status, deadline), ("idle", 0.0))


class MetadataTests(unittest.TestCase):
    def test_reports_custom_state_token(self) -> None:
        with patch("state_icons.herdr") as herdr:
            state_icons.report("plugin:test", "pane-1", "󰄬")

        herdr.assert_called_once_with(
            "pane",
            "report-metadata",
            "pane-1",
            "--source",
            "plugin:test",
            "--token",
            "state_icon_custom=󰄬",
        )

    def test_clears_custom_state_token(self) -> None:
        with patch("state_icons.herdr") as herdr:
            state_icons.report("plugin:test", "pane-1", None)

        herdr.assert_called_once_with(
            "pane",
            "report-metadata",
            "pane-1",
            "--source",
            "plugin:test",
            "--clear-token",
            "state_icon_custom",
        )


if __name__ == "__main__":
    unittest.main()
