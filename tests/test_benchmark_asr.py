"""tools/benchmark_asr.py scoring and pins; no models or audio."""

import unittest

from tools import asr_models
from tools import benchmark_asr as asr


class NormalizeTests(unittest.TestCase):
    def test_numbers_are_compared_as_spoken(self):
        same = [("set the quality to 1080p", "Set the quality to ten eighty p."),
                ("jump to 2:30", "Jump to two thirty."),
                ("go back 10 seconds", "Go back ten seconds"),
                ("set the volume to 30", "set the volume to thirty"),
                ("play the second one", "Play the 2nd one."),
                ("open notepad", "Open Note pad"),
                ("search youtube for lofi hip hop", "Search YouTube for lo-fi hip-hop."),
                ("don't open Chrome", "Don't open Chrome."),
                ("minimise Spotify", "Minimize Spotify")]
        for ref, hyp in same:
            self.assertEqual(asr.normalize(ref), asr.normalize(hyp), (ref, hyp))
        self.assertNotEqual(asr.normalize("write hello"), asr.normalize("right hello"))
        self.assertEqual((asr.say(1080), asr.say(30), asr.say(250), asr.say(2000)),
                         ("ten eighty", "thirty", "two hundred fifty", "two thousand"))

    def test_score_counts_write_and_pause(self):
        prompts = [{"id": "a", "text": "write hello I am Aqua"}, {"id": "b", "text": "pause the music"},
                   {"id": "c", "text": "open notepad"}]
        result = asr.score(prompts, {"a": "Right, hello, I am Aqua.", "b": "Paws the music.", "c": "Open Notepad."})
        self.assertEqual((result["write_ok"], result["pause_ok"], result["exact_count"]), (0, 0, 1))
        self.assertAlmostEqual(result["wer"], 2 / 10)
        result = asr.score(prompts, {"a": "Write hello, I am Aqua.", "b": "Pause the music.", "c": "Open notepad"})
        self.assertEqual((result["write_ok"], result["pause_ok"], result["wer"]), (1, 1, 0.0))


class PinTests(unittest.TestCase):
    def test_every_download_is_pinned(self):
        for name, (file, size, sha, licence) in asr_models.ARCHIVES.items():
            self.assertTrue(file.startswith(name) and file.endswith(".tar.bz2"))
            self.assertEqual(len(sha), 64)
            self.assertGreater(size, 1_000_000)
            self.assertTrue(licence)
        for name, (repo, revision, files, licence) in asr_models.HF_MODELS.items():
            self.assertEqual(len(revision), 40, name)
            # No third-party copies; csukuangfj2 was approved as a k2-fsa organisation member.
            self.assertTrue(repo.split("/")[0] in {"csukuangfj", "csukuangfj2", "k2-fsa", "Systran"}, repo)
            self.assertTrue(licence)
            for file, (size, blob, sha) in files.items():
                self.assertTrue(blob or sha, file)
                if file.endswith((".onnx", ".bin")):
                    self.assertEqual(len(sha or ""), 64, file)  # Large files by LFS SHA-256.
        self.assertTrue(str(asr_models.MODELS).endswith(r".cache\asr-models"))

    def test_archives_are_never_extracted_unverified(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            name, (file, size, _sha, _l) = next(iter(asr_models.ARCHIVES.items()))
            (folder / file).write_bytes(b"x" * 10)  # Wrong size.
            with mock.patch.object(asr_models, "MODELS", folder), \
                    mock.patch.object(asr_models, "_safe_extract", side_effect=AssertionError("extracted")):
                lines = []
                missing = asr_models.extract_local({name}, log=lambda *a, **k: lines.append(a[0]))
            self.assertEqual(missing, [name])
            self.assertIn("not extracted", lines[0])
            self.assertTrue((folder / file).exists())  # Left alone, never deleted or re-downloaded.

    def test_every_model_has_a_licence(self):
        for spec in asr.MODELS.values():
            self.assertTrue(spec["licence"] and spec["label"])


if __name__ == "__main__":
    unittest.main()
