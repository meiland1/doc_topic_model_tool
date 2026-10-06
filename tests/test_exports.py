"""Exports share workspace precedence and preserve exact Unicode source ranges."""
import csv
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from qualitative_themes.analysis.topics import AnalysisSettings, TopicOutput, analyze, AnalysisCancelled
from qualitative_themes.export.research import build_export, write_codebook, write_coded_passages, _write_atomic
from qualitative_themes.storage.database import connect, create_project
from qualitative_themes.storage.documents import import_document, get_document, remove_documents
from qualitative_themes.storage.coding import create_theme, add_coding, add_note, remove_coding, reject_model_suggestion, merge_themes
from qualitative_themes.storage.history import rename_analysis

class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = create_project(self.root/'project', 'Research café').directory
        self.docs = []
        for n in range(2):
            path = self.root/f'reviews{n}.txt'
            path.write_text('\n\n'.join(f'Café 😀 review {i}, "quoted".\nSecond line {n}.' for i in range(6)), encoding='utf-8')
            self.docs.append(import_document(self.directory, path).document_id)
        def fake(passages, *args):
            return TopicOutput([i % 2 for i in range(len(passages))], {0: [('café', .9)], 1: [('review', .8)]})
        self.run, _ = analyze(self.directory, 'Paragraphs', [self.docs[0]], AnalysisSettings(min_topic_size=2), backend=fake)
        self.manual = create_theme(self.directory, 'Manual, "café" 😀')
        self.text = get_document(self.directory, self.docs[0])[1]

    def snapshot(self):
        with connect(self.directory) as conn:
            return '\n'.join(conn.iterdump())

    def test_multitheme_exact_ranges_overlaps_and_utf8_csv(self):
        other = create_theme(self.directory, 'Second theme')
        end = re.search(r'\r?\n\r?\n', self.text).start()
        cid = add_coding(self.directory, self.docs[0], 0, end, self.manual)
        add_coding(self.directory, self.docs[0], 0, end, other)
        add_coding(self.directory, self.docs[0], 5, end, other)
        add_note(self.directory, 'Memo, "quoted" 😀\nSecond line', coding_id=cid)
        add_note(self.directory, 'Document memo', document_id=self.docs[0])
        before = self.snapshot()
        target = self.root/'passages.csv'
        self.assertEqual(write_coded_passages(self.directory, None, target), (2, 3))
        self.assertTrue(target.read_bytes().startswith(b'\xef\xbb\xbf'))
        with target.open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]['quote'], self.text[:end])
        self.assertEqual(len(json.loads(rows[0]['themes'])), 2)
        self.assertIn('😀\nSecond line', rows[0]['passage_notes'])
        self.assertEqual(rows[0]['assignment_source'], 'researcher')
        self.assertEqual(self.snapshot(), before)

    def test_rejections_removals_and_reacceptance(self):
        initial = build_export(self.directory, self.run)
        passage = initial['passages'][0]; assignment = passage['assignments'][0]
        tid, start, end = assignment['theme_id'], passage['start_char'], passage['end_char']
        reject_model_suggestion(self.directory, self.docs[0], start, end, tid)
        self.assertEqual(build_export(self.directory, self.run)['metadata']['unique_ranges'], 5)
        cid = add_coding(self.directory, self.docs[0], start, end, tid)
        add_note(self.directory, 'Retained memo', coding_id=cid)
        remove_coding(self.directory, cid)
        self.assertEqual(build_export(self.directory, self.run)['metadata']['unique_ranges'], 5)
        add_coding(self.directory, self.docs[0], start, end, tid)
        restored = build_export(self.directory, self.run)['passages'][0]['assignments'][0]
        self.assertEqual(restored['source'], 'researcher')
        self.assertEqual(restored['notes'][0]['body'], 'Retained memo')
        self.assertEqual(restored['notes'][0]['coding_state'], 'removed')

    def test_merge_aggregates_keywords_notes_and_deduplicates(self):
        data = build_export(self.directory, self.run)
        themes = [row['theme_id'] for row in data['themes'] if row['keywords']]
        add_note(self.directory, 'Source theme memo', theme_id=themes[0])
        for tid in themes:
            add_coding(self.directory, self.docs[0], 0, 10, tid)
        merge_themes(self.directory, themes[0], themes[1])
        merged = build_export(self.directory, self.run)
        target = next(row for row in merged['themes'] if row['theme_id'] == themes[1])
        self.assertEqual(set(target['keywords']), {'café', 'review'})
        self.assertEqual(target['unique_coded_ranges'], 7)
        self.assertEqual(target['theme_notes'][0]['body'], 'Source theme memo')
        self.assertNotIn(themes[0], {row['theme_id'] for row in merged['themes']})

    def test_scope_removed_document_and_persistent_name(self):
        add_coding(self.directory, self.docs[1], 0, 10, self.manual)
        rename_analysis(self.directory, self.run, 'Application reviews')
        remove_documents(self.directory, [self.docs[0]])
        data = build_export(self.directory, self.run)
        self.assertEqual(data['metadata']['analysis'], 'Application reviews')
        self.assertEqual({row['document_id'] for row in data['passages']}, {self.docs[0]})
        self.assertTrue(all(row['document_status'] == 'removed' for row in data['passages']))
        manual = build_export(self.directory)
        self.assertEqual({row['document_id'] for row in manual['passages']}, {self.docs[1]})
        self.assertEqual(len(manual['themes']), 1)

    def test_codebook_formats_and_read_only_provenance(self):
        add_note(self.directory, 'Theme description memo', theme_id=self.manual)
        before = self.snapshot()
        target = self.root/'codebook.json'
        write_codebook(self.directory, self.run, target)
        data = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(data['metadata']['analysis_id'], self.run)
        self.assertEqual(data['metadata']['unique_ranges'], 6)
        self.assertIn('model_info', data['metadata'])
        self.assertTrue(data['themes'][0]['representative_evidence'] or data['themes'][1]['representative_evidence'])
        target = self.root/'codebook.csv'
        write_codebook(self.directory, self.run, target)
        with target.open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(sum(int(r['unique_coded_ranges']) for r in rows), 6)
        self.assertEqual(self.snapshot(), before)

    def test_empty_manual_export_has_headers_and_metadata(self):
        target = self.root/'empty.csv'
        self.assertEqual(write_coded_passages(self.directory, None, target), (0, 0))
        with target.open(encoding='utf-8-sig', newline='') as stream:
            self.assertEqual(list(csv.DictReader(stream)), [])
        data = build_export(self.directory)
        self.assertEqual(data['metadata']['unique_ranges'], 0)
        self.assertEqual(data['themes'][0]['unique_coded_ranges'], 0)

    def test_protected_destinations_and_failed_or_cancelled_writes(self):
        for path in [self.directory/'project.sqlite', self.directory/'sources'/'bad.csv', self.directory/'models'/'bad.json']:
            with self.assertRaises(ValueError):
                write_codebook(self.directory, self.run, path)
        target = self.root/'keep.csv'; target.write_text('Keep existing file')
        with self.assertRaises(AnalysisCancelled):
            _write_atomic(target, lambda stream: stream.write('New'), 'utf-8', lambda: True)
        def fail(stream):
            stream.write('Partial'); raise OSError('Disk failure')
        with self.assertRaises(OSError):
            _write_atomic(target, fail, 'utf-8')
        self.assertEqual(target.read_text(), 'Keep existing file')
        self.assertEqual(list(self.root.glob('.*.tmp')), [])
        with self.assertRaises(AnalysisCancelled):
            write_coded_passages(self.directory, self.run, target, should_cancel=lambda: True)

    def test_page_paragraph_locations_follow_overlapping_source_spans(self):
        with connect(self.directory) as conn:
            conn.execute('DELETE FROM document_pages WHERE document_id=?', (self.docs[0],))
            conn.execute('INSERT INTO document_pages VALUES (?,?,?,?,?,?)', ('span1', self.docs[0], 1, 2, 0, 10))
            conn.execute('INSERT INTO document_pages VALUES (?,?,?,?,?,?)', ('span2', self.docs[0], 2, 3, 10, len(self.text)))
        add_coding(self.directory, self.docs[0], 5, 15, self.manual)
        passage = build_export(self.directory)['passages'][0]
        self.assertEqual(passage['pages'], [1, 2])
        self.assertEqual(passage['paragraphs'], [2, 3])
        self.assertEqual(passage['quote'], self.text[5:15])

    def test_accepted_earlier_theme_keeps_keywords_in_later_run(self):
        tid = next(row['theme_id'] for row in build_export(self.directory, self.run)['themes'] if row['keywords'] == ['café'])
        add_coding(self.directory, self.docs[0], 0, 10, tid)
        def fake(passages, *args): return TopicOutput([0]*len(passages), {0: [('later', .6)]})
        later, _ = analyze(self.directory, 'Paragraphs', [self.docs[0]], AnalysisSettings(min_topic_size=2), backend=fake)
        theme = next(row for row in build_export(self.directory, later)['themes'] if row['theme_id'] == tid)
        self.assertEqual(theme['keywords'], ['café'])
        self.assertEqual(theme['unique_coded_ranges'], 1)


    def test_lf_and_windows_crlf_quotes_round_trip_without_normalization(self):
        for separator in ["\n", "\r\n"]:
            with self.subTest(separator=repr(separator)):
                text = separator.join(['Café 😀, "quoted".', 'Second line.', '', 'Next review.'])
                source = self.root / ('crlf.txt' if separator == "\r\n" else 'lf.txt')
                source.write_bytes(text.encode('utf-8'))
                doc = import_document(self.directory, source).document_id
                self.assertEqual(get_document(self.directory, doc)[1], text)
                end = re.search(r'\r?\n\r?\n', text).start()
                add_coding(self.directory, doc, 0, end, self.manual)
                target = self.root / 'line_endings.csv'
                write_coded_passages(self.directory, None, target)
                with target.open(encoding='utf-8-sig', newline='') as stream:
                    row = next(r for r in csv.DictReader(stream) if r['document_id'] == doc)
                self.assertEqual(row['quote'], text[:end])
                self.assertEqual(int(row['end_char']), end)

    def test_workspace_keyword_query_matches_codebook_after_rename_and_merge(self):
        from qualitative_themes.storage.theme_keywords import theme_keywords
        from qualitative_themes.storage.coding import rename_theme
        data = build_export(self.directory, self.run)
        generated = [row for row in data['themes'] if row['keywords']]
        tid = generated[0]['theme_id']
        expected = generated[0]['keywords']
        rename_theme(self.directory, tid, 'Researcher label')
        self.assertEqual(theme_keywords(self.directory, tid, self.run), expected)
        self.assertEqual(theme_keywords(self.directory, self.manual, self.run), [])
        merge_themes(self.directory, tid, generated[1]['theme_id'])
        target = generated[1]['theme_id']
        entry = next(row for row in build_export(self.directory, self.run)['themes'] if row['theme_id'] == target)
        self.assertEqual(theme_keywords(self.directory, target, self.run), entry['keywords'])
        self.assertEqual(set(entry['keywords']), {'café', 'review'})


    def test_keyword_scores_survive_rename_and_merge_uses_highest_saved_weight(self):
        from qualitative_themes.storage.theme_keywords import theme_keyword_scores
        from qualitative_themes.storage.coding import rename_theme
        themes = [row['theme_id'] for row in build_export(self.directory, self.run)['themes'] if row['keywords']]
        with connect(self.directory) as conn:
            info = json.loads(conn.execute('SELECT model_info_json FROM analysis_runs WHERE id=?', (self.run,)).fetchone()[0])
            info['keywords'] = {'0': [['shared', .00001234], ['first', .9]], '1': [['shared', .7], ['second', .8]]}
            conn.execute('UPDATE analysis_runs SET model_info_json=? WHERE id=?', (json.dumps(info), self.run))
            target = conn.execute('SELECT theme_id FROM model_suggestions WHERE run_id=? AND bertopic_id=0 LIMIT 1', (self.run,)).fetchone()[0]
        before = theme_keyword_scores(self.directory, target, self.run)
        self.assertEqual(before, [('first', .9), ('shared', .00001234)])
        rename_theme(self.directory, target, 'Renamed label')
        self.assertEqual(theme_keyword_scores(self.directory, target, self.run), before)
        source = next(tid for tid in themes if tid != target)
        merge_themes(self.directory, source, target)
        self.assertEqual(theme_keyword_scores(self.directory, target, self.run), [('first', .9), ('second', .8), ('shared', .7)])
        self.assertEqual(theme_keyword_scores(self.directory, self.manual, self.run), [])
