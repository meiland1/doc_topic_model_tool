"""Transcript scaffolding stays in source evidence but out of model inputs."""
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import unittest
from qualitative_themes.analysis.transcript_text import clean_model_text
from qualitative_themes.analysis.topics import prepare_passages, analyze, TopicOutput, AnalysisSettings
from qualitative_themes.analysis.refinement import load_source
from qualitative_themes.storage.database import create_project
from qualitative_themes.storage.documents import import_document, get_document

class TranscriptTextTests(unittest.TestCase):
    def test_common_turn_markers_and_timestamp_formats(self):
        examples = [
            ('SPEAKER_00: Helpful support.', 'Helpful support.'),
            ('[00:01:02.345 --> 00:01:05.600] [SPEAKER_01]: Helpful support.', 'Helpful support.'),
            ('00:12,345 - 00:17,000 speaker_02 Helpful support.', 'Helpful support.'),
            ('(00:12) Speaker 03: Helpful support.', 'Helpful support.'),
            ('Interviewer: How was it?\nParticipant 2: Helpful support.', 'How was it? Helpful support.'),
            ('The speaker discussed 2026 and 00 codes at 12:30.', 'The speaker discussed 2026 and 00 codes at 12:30.'),
            ('speaker_00x and speakerphone remain.', 'speaker_00x and speakerphone remain.'),
        ]
        for source, expected in examples:
            with self.subTest(source=source): self.assertEqual(clean_model_text(source), expected)

    def test_new_analysis_and_fixed_partition_refresh_preserve_exact_evidence(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = create_project(root/'project', 'Interview').directory
            text = '\r\n\r\n'.join(f'[00:00:0{i}] SPEAKER_00: Café 😀 useful experience {i}.' for i in range(6))
            text += '\r\n\r\nSPEAKER_01: [00:00:09]'
            path = root/'interview.txt'; path.write_bytes(text.encode())
            doc = import_document(directory, path).document_id
            passages = prepare_passages(directory, 'Paragraphs', [doc])
            self.assertEqual(len(passages), 6)
            self.assertEqual(get_document(directory, doc)[1], text)
            for passage in passages:
                self.assertNotIn('SPEAKER', passage.text)
                self.assertNotIn('00:', passage.text)
                self.assertIn('SPEAKER_00', text[passage.start:passage.end])
                self.assertEqual(passage.source_hash, hashlib.sha256(text[passage.start:passage.end].encode()).hexdigest())
            def fake(passages, *args):
                self.assertTrue(all('SPEAKER' not in p.text for p in passages))
                return TopicOutput([0]*len(passages), {0: [('experience', .8)]})
            run, _ = analyze(directory, 'Paragraphs', [doc], AnalysisSettings(min_topic_size=2), backend=fake)
            refreshed = load_source(directory, run)
            self.assertEqual([p.text for p in refreshed['passages']], [p.text for p in passages])
            self.assertEqual(refreshed['model_info']['model_text_filter'], 'transcript-markers-v1')
            self.assertEqual(get_document(directory, doc)[1], text)
