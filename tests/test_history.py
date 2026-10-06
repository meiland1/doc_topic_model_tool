"""Saved titles and readable, paged history preserve existing analysis data."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from qualitative_themes.storage.database import connect, create_project, open_project
from qualitative_themes.storage.documents import import_document
from qualitative_themes.storage.coding import create_theme, add_coding, rename_theme, merge_themes
from qualitative_themes.storage.history import analysis_title, rename_analysis, history_runs, history_events, analysis_details
from qualitative_themes.analysis.topics import analyze, AnalysisSettings, TopicOutput

class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.directory = create_project(root/'project', 'History').directory
        path = root/'Evidence.txt'
        path.write_text('\n\n'.join(f'Café 😀 evidence paragraph {i}.' for i in range(6)), encoding='utf-8')
        self.doc = import_document(self.directory, path).document_id
        def fake(passages, *args): return TopicOutput([0]*len(passages), {0: [('evidence', .8)]})
        self.run, _ = analyze(self.directory, 'Paragraphs', [self.doc], AnalysisSettings(min_topic_size=2), backend=fake)

    def stable_data(self):
        with connect(self.directory) as conn:
            return {table: conn.execute(f'SELECT * FROM {table}').fetchall() for table in ['segments', 'themes', 'codings', 'notes', 'model_suggestions']} | {'info': conn.execute('SELECT model_info_json FROM analysis_runs WHERE id=?', (self.run,)).fetchone()}

    def test_name_persists_without_rewriting_research(self):
        before = self.stable_data()
        rename_analysis(self.directory, self.run, '  Reviews — café 😀  ')
        open_project(self.directory)
        rows, total = history_runs(self.directory)
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]['title'], 'Reviews — café 😀')
        self.assertEqual(self.stable_data(), before)
        text, details = analysis_details(self.directory, self.run)
        self.assertIn('Evidence.txt', text)
        self.assertIn('Passages: 6', text)
        self.assertIn('Reviews — café 😀', text)
        self.assertEqual(details['settings']['display_name'], 'Reviews — café 😀')
        with connect(self.directory) as conn:
            count = conn.execute('SELECT count(*) FROM history_events').fetchone()[0]
        rename_analysis(self.directory, self.run, 'Reviews — café 😀')
        with connect(self.directory) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM history_events').fetchone()[0], count)
        rename_analysis(self.directory, self.run, '')
        rows, _ = history_runs(self.directory)
        self.assertNotIn('display_name', rows[0]['settings'])
        self.assertNotIn(self.run, rows[0]['title'])

    def test_invalid_and_duplicate_names_leave_data_unchanged(self):
        for name in ['a'*121, 'two\nlines', 'Manual coding only', 'bad\x00name']:
            with self.assertRaises(ValueError): rename_analysis(self.directory, self.run, name)
        with self.assertRaises(ValueError): rename_analysis(self.directory, 'missing', 'New')
        rename_analysis(self.directory, self.run, 'Reviews')
        with connect(self.directory) as conn:
            conn.execute("INSERT INTO analysis_runs SELECT 'other',started_at,completed_at,status,settings_json,model_info_json FROM analysis_runs WHERE id=?", (self.run,))
        with self.assertRaises(ValueError): rename_analysis(self.directory, 'other', 'reviews')

    def test_readable_events_and_pagination(self):
        tid = create_theme(self.directory, 'Initial name')
        add_coding(self.directory, self.doc, 0, 10, tid)
        rename_theme(self.directory, tid, 'Useful evidence')
        target = create_theme(self.directory, 'Merged evidence')
        merge_themes(self.directory, tid, target)
        all_events, total = history_events(self.directory)
        page, _ = history_events(self.directory, 0, 2)
        second, _ = history_events(self.directory, 2, 2)
        self.assertEqual(page + second, all_events[:4])
        self.assertEqual(total, len(all_events))
        text = '\n'.join(row['summary'] for row in all_events)
        self.assertIn('Evidence.txt', text)
        self.assertIn('Initial name → Useful evidence', text)
        self.assertIn('Useful evidence → Merged evidence', text)
        self.assertNotIn(self.doc, text)
        self.assertNotIn(self.run, text)
        self.assertTrue(all(row['timestamp'] for row in all_events))
        self.assertEqual(history_runs(self.directory, 1, 1)[0], [])

    def test_legacy_automatic_title(self):
        title = analysis_title(json.dumps({'analysis_unit': 'Paragraphs'}), '2026-10-01T12:30:40+00:00')
        self.assertEqual(title, 'Analysis 2026-10-01 12:30:40 · Paragraphs')
