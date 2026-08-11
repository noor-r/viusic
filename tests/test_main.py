import os
import tempfile
import unittest
from pathlib import Path

from app.content_analyzer import resolve_model as resolve_vision_model
from app.main import (
    build_attach_ffmpeg_command,
    is_supported_video_file,
    load_environment,
    normalize_api_base_url,
    resolve_api_base_url,
)
from app.music_recommender import resolve_model as resolve_text_model


class EnvironmentLoadingTests(unittest.TestCase):
    def test_load_environment_overrides_existing_values_from_dotenv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("KIMI_API_KEY=from-dotenv\n", encoding="utf-8")
            os.environ["KIMI_API_KEY"] = "stale-value"

            try:
                load_environment(env_path)
                self.assertEqual(os.environ["KIMI_API_KEY"], "from-dotenv")
            finally:
                os.environ.pop("KIMI_API_KEY", None)


class ModelSelectionTests(unittest.TestCase):
    def test_uses_openai_default_model_when_openai_key_is_present(self):
        os.environ["OPENAI_API_KEY"] = "test-openai-key"
        os.environ.pop("KIMI_API_KEY", None)
        os.environ.pop("MISTRAL_API_KEY", None)
        os.environ.pop("KIMI_MODEL", None)
        os.environ.pop("KIMI_VISION_MODEL", None)
        os.environ.pop("MISTRAL_MODEL", None)
        os.environ.pop("MISTRAL_VISION_MODEL", None)
        try:
            self.assertEqual(resolve_vision_model(), "gpt-4o-mini")
            self.assertEqual(resolve_text_model(), "gpt-4o-mini")
        finally:
            os.environ.pop("OPENAI_API_KEY", None)

    def test_uses_mistral_default_model_when_mistral_key_is_present(self):
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("KIMI_API_KEY", None)
        os.environ["MISTRAL_API_KEY"] = "test-mistral-key"
        os.environ.pop("KIMI_MODEL", None)
        os.environ.pop("KIMI_VISION_MODEL", None)
        os.environ.pop("MISTRAL_MODEL", None)
        os.environ.pop("MISTRAL_VISION_MODEL", None)
        try:
            self.assertEqual(resolve_vision_model(), "mistral-small-latest")
            self.assertEqual(resolve_text_model(), "mistral-small-latest")
        finally:
            os.environ.pop("MISTRAL_API_KEY", None)

    def test_prefers_mistral_base_url_when_mistral_key_is_present(self):
        os.environ["MISTRAL_API_KEY"] = "test-mistral-key"
        os.environ["KIMI_API_BASE_URL"] = "https://api.moonshot.cn/v1"
        os.environ["MISTRAL_API_BASE_URL"] = "https://api.mistral.ai/v1"
        try:
            self.assertEqual(resolve_api_base_url(), "https://api.mistral.ai/v1")
        finally:
            os.environ.pop("MISTRAL_API_KEY", None)
            os.environ.pop("KIMI_API_BASE_URL", None)
            os.environ.pop("MISTRAL_API_BASE_URL", None)


class AttachVideoCommandTests(unittest.TestCase):
    def test_uses_attached_track_when_source_has_audio(self):
        cmd = build_attach_ffmpeg_command(
            Path("/tmp/source.mp4"),
            Path("/tmp/track.mp3"),
            Path("/tmp/out.mp4"),
            has_source_audio=True,
        )
        self.assertNotIn("-filter_complex", cmd)
        self.assertIn("-map", cmd)
        self.assertIn("1:a:0", cmd)

    def test_builds_simple_command_when_source_has_no_audio(self):
        cmd = build_attach_ffmpeg_command(
            Path("/tmp/source.mp4"),
            Path("/tmp/track.mp3"),
            Path("/tmp/out.mp4"),
            has_source_audio=False,
        )
        self.assertNotIn("-filter_complex", cmd)
        self.assertIn("-map", cmd)
        self.assertIn("1:a:0", cmd)

    def test_preserves_original_audio_with_longest_mix_when_requested(self):
        cmd = build_attach_ffmpeg_command(
            Path("/tmp/source.mp4"),
            Path("/tmp/track.mp3"),
            Path("/tmp/out.mp4"),
            has_source_audio=True,
            keep_original_audio=True,
            original_audio_role="background",
        )
        joined_cmd = " ".join(cmd)
        self.assertIn("-filter_complex", cmd)
        self.assertIn("amix=inputs=2:duration=longest:dropout_transition=0", joined_cmd)
        self.assertNotIn("-shortest", cmd)
        self.assertIn("-map", cmd)
        self.assertIn("[out]", joined_cmd)


class NormalizeApiBaseUrlTests(unittest.TestCase):
    def test_accepts_video_files_by_extension_when_mime_type_is_missing(self):
        self.assertTrue(is_supported_video_file("clip.mp4", ""))
        self.assertTrue(is_supported_video_file("clip.MOV", "application/octet-stream"))

    def test_rejects_non_video_files(self):
        self.assertFalse(is_supported_video_file("photo.png", "image/png"))
        self.assertFalse(is_supported_video_file("notes.txt", ""))

    def test_defaults_empty_url_to_moonshot_v1(self):
        self.assertEqual(normalize_api_base_url(""), "https://api.moonshot.cn/v1")

    def test_rewrites_kimi_host_to_moonshot_root(self):
        self.assertEqual(
            normalize_api_base_url("https://api.kimi.com"),
            "https://api.moonshot.cn/v1",
        )

    def test_trims_chat_completions_suffix(self):
        self.assertEqual(
            normalize_api_base_url("https://api.moonshot.cn/v1/chat/completions"),
            "https://api.moonshot.cn/v1",
        )

    def test_rejects_non_v1_api_root(self):
        with self.assertRaisesRegex(ValueError, "ending in /v1"):
            normalize_api_base_url("https://api.moonshot.cn/chat")


if __name__ == "__main__":
    unittest.main()