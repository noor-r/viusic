import unittest
from unittest.mock import Mock, patch
import os

from app.music_recommender import (
    build_music_search_queries,
    build_recommended_music_candidates,
    download_media_with_downloader,
    fallback_search_queries,
    normalize_downloader_results,
)


class NormalizeDownloaderResultsTests(unittest.TestCase):
    def test_normalizes_downloader_search_payload(self):
        payload = [
            {
                "title": "Test Track",
                "author": "Test Artist",
                "url": "https://youtube.com/watch?v=abc123",
                "durationSeconds": 180,
                "thumbnail": "https://i.ytimg.com/vi/abc123/default.jpg",
            }
        ]

        normalized = normalize_downloader_results(payload)

        self.assertEqual(normalized[0]["title"], "Test Track")
        self.assertEqual(normalized[0]["channel"], "Test Artist")
        self.assertEqual(normalized[0]["url"], "https://youtube.com/watch?v=abc123")
        self.assertEqual(normalized[0]["duration"], "3:00")
        self.assertEqual(normalized[0]["thumbnail"], "https://i.ytimg.com/vi/abc123/default.jpg")

    def test_builds_fallback_queries_when_model_queries_missing(self):
        overall = {"dominant_mood": "melancholic"}
        rec = {
            "song_style_descriptors": ["piano", "ambient", "minimal"],
            "tempo_bpm_recommendation": "90-100",
        }

        queries = fallback_search_queries(overall, rec)

        self.assertGreaterEqual(len(queries), 3)
        self.assertIn("melancholic", queries[0].lower())

    def test_builds_music_queries_with_duration_and_royalty_terms(self):
        overall = {"dominant_mood": "uplifting"}
        rec = {
            "song_style_descriptors": ["synthwave", "modern", "energetic"],
            "tempo_bpm_recommendation": "120-130",
            "trending_directions": "Hyper-compressed pop synths are trending.",
        }

        queries = build_music_search_queries(overall, rec, video_duration_seconds=70)

        self.assertGreaterEqual(len(queries), 3)
        self.assertTrue(any("royalty free" in q.lower() or "no copyright" in q.lower() for q in queries))
        self.assertTrue(any("short" in q.lower() or "1 minute" in q.lower() for q in queries))
        self.assertFalse(any("2 hour" in q.lower() or "1 hour" in q.lower() for q in queries))

    def test_builds_best_match_shortlist_from_deduped_results(self):
        overall = {"dominant_mood": "energetic"}
        recommendation = {"song_style_descriptors": ["synthwave", "bright", "drive"]}
        grouped_results = {
            "query 1": {
                "status": "ok",
                "results": [
                    {"title": "Neon Drive", "channel": "Artist A", "url": "https://example.com/1", "duration": "3:12", "duration_seconds": 192},
                    {"title": "Neon Drive", "channel": "Artist A", "url": "https://example.com/1", "duration": "3:12", "duration_seconds": 192},
                ],
            },
            "query 2": {
                "status": "ok",
                "results": [
                    {"title": "Midnight Loop", "channel": "Artist B", "url": "https://example.com/2", "duration": "45:00", "duration_seconds": 2700},
                    {"title": "Bright Pulse", "channel": "Artist C", "url": "https://example.com/3", "duration": "1:41", "duration_seconds": 101},
                ],
            },
        }

        best_matches = build_recommended_music_candidates(
            overall,
            recommendation,
            grouped_results,
            video_duration_seconds=90,
        )

        self.assertGreaterEqual(len(best_matches), 2)
        self.assertEqual(best_matches[0]["title"], "Bright Pulse")
        self.assertTrue(all(item.get("duration_seconds") is None or item.get("duration_seconds", 0) <= 1800 for item in best_matches))


class DownloadFallbackTests(unittest.TestCase):
    @patch("app.music_recommender.subprocess.run")
    @patch("app.music_recommender._ensure_downloader_path")
    @patch("app.music_recommender.Path.exists")
    def test_uses_ytdlp_when_downloader_is_missing(self, exists_mock, ensure_mock, run_mock):
        ensure_mock.side_effect = RuntimeError("downloader missing")
        exists_mock.return_value = True
        run_mock.return_value = Mock(returncode=0, stderr="", stdout="")

        output = download_media_with_downloader("https://youtube.com/watch?v=abc123", "outputs/test_track.mp3")

        self.assertEqual(output, os.path.normpath("outputs/test_track.mp3"))
        self.assertEqual(run_mock.call_count, 1)
        self.assertIn("yt-dlp", run_mock.call_args.args[0][0])

    @patch("app.music_recommender.subprocess.run")
    @patch("app.music_recommender._ensure_downloader_path")
    @patch("app.music_recommender.Path.exists")
    def test_uses_ytdlp_when_downloader_command_fails(self, exists_mock, ensure_mock, run_mock):
        ensure_mock.return_value = "SoundMind.Downloader.exe"
        exists_mock.return_value = True
        run_mock.side_effect = [
            Mock(returncode=1, stderr="downloader failed", stdout=""),
            Mock(returncode=0, stderr="", stdout=""),
        ]

        output = download_media_with_downloader("https://youtube.com/watch?v=abc123", "outputs/test_track.mp4")

        self.assertEqual(output, os.path.normpath("outputs/test_track.mp4"))
        self.assertEqual(run_mock.call_count, 2)
        self.assertIn("SoundMind.Downloader.exe", run_mock.call_args_list[0].args[0][0])
        self.assertIn("yt-dlp", run_mock.call_args_list[1].args[0][0])


if __name__ == "__main__":
    unittest.main()
