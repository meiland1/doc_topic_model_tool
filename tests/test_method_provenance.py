"""Method/origin reporting survives edits, refinement, history, and exports."""
import csv
import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from qualitative_themes.analysis.settings import AnalysisSettings
from qualitative_themes.analysis.topics import analyze, TopicOutput
from qualitative_themes.analysis.refinement import propose_refinement, apply_proposal, review_proposal
from qualitative_themes.storage.database import create_project, connect, open_project
from qualitative_themes.storage.documents import import_document
from qualitative_themes.storage.coding import list_themes, merge_themes, rename_theme, create_theme, add_coding
from qualitative_themes.storage.history import analysis_details, history_runs
from qualitative_themes.storage.provenance import theme_provenance, method_label
from qualitative_themes.export.research import build_export, write_codebook, write_coded_passages
from qualitative_themes.analysis.labels import label_run

class MethodProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.directory=create_project(self.root/'project','Methods').directory
        source=self.root/'evidence.txt'
        source.write_text('\n\n'.join(['Clinic treatment access.','Medication costs money.','Rockets launch satellites.','Space missions explore planets.']),encoding='utf-8')
        self.doc=import_document(self.directory,source).document_id
        self.settings=AnalysisSettings(analysis_method='zero_shot',min_topic_size=2,
            topic_definitions=(('Care access','Access to medication and healthcare'),('Stress','Emotional distress')))
        def fake(passages,*args):
            return TopicOutput([0,0,2,-1], {0:[('clinic',.4)],2:[('space',.5)]}, model_info={
                'predefined_topic_labels':{'0':'Care access'},
                'zero_shot':{'matched_passage_count':2,'unmatched_before_discovery':2,'unmatched_category_names':['Stress'],
                             'best_similarity_by_segment':{p.id:.8 for p in passages}}})
        self.run,_=analyze(self.directory,'Paragraphs',[self.doc],self.settings,backend=fake)
        with connect(self.directory) as conn:
            self.topics={topic:theme for topic,theme in conn.execute('SELECT DISTINCT bertopic_id,theme_id FROM model_suggestions WHERE run_id=?',(self.run,))}

    def test_history_configuration_and_legacy_method(self):
        text,_=analysis_details(self.directory,self.run)
        self.assertIn('Zero-shot topics + discovery',text)
        self.assertIn('Care access | Access to medication',text)
        self.assertIn('Initially matched passages: 2',text)
        self.assertIn('Categories with no initial matches: Stress',text)
        self.assertIn('not confidence',text)
        self.assertEqual(history_runs(self.directory)[0][0]['method'],'Zero-shot topics + discovery')
        self.assertEqual(method_label({}),'General discovery')
        self.assertEqual(method_label({},manual=True),'Manual coding only')

    def test_renamed_and_merged_theme_keeps_origins(self):
        rename_theme(self.directory,self.topics[0],'Treatment barriers')
        self.assertEqual(theme_provenance(self.directory,self.topics[0],self.run)['supplied_names'],['Care access'])
        merge_themes(self.directory,self.topics[2],self.topics[0])
        origin=theme_provenance(self.directory,self.topics[0],self.run)
        self.assertEqual(origin['origins'],['discovered','predefined'])
        self.assertEqual(origin['supplied_names'],['Care access'])
        open_project(self.directory)
        entry=next(t for t in build_export(self.directory,self.run)['themes'] if t['theme_id']==self.topics[0])
        self.assertEqual(entry['theme'],'Treatment barriers')
        self.assertEqual(entry['topic_origins'],['discovered','predefined'])
        self.assertEqual(entry['label_source'],'researcher')

    def test_csv_json_and_manual_exports(self):
        data=build_export(self.directory,self.run)
        self.assertEqual(data['metadata']['format_version'],2)
        self.assertEqual(data['metadata']['analysis_method'],'zero_shot')
        entry=next(t for t in data['themes'] if t['theme_id']==self.topics[0])
        self.assertEqual(entry['label_source'],'predefined')
        for kind in ['csv','json']:
            path=self.root/f'codebook.{kind}'
            write_codebook(self.directory,self.run,path)
            if kind=='csv':
                rows=list(csv.DictReader(path.open(encoding='utf-8-sig')))
                row=next(t for t in rows if t['theme_id']==self.topics[0])
                self.assertEqual(json.loads(row['supplied_category_names']),['Care access'])
                self.assertEqual(json.loads(row['analysis_configuration'])['analysis_method'],'zero_shot')
            else:
                self.assertEqual(json.loads(path.read_text())['metadata']['analysis_method'],'zero_shot')
        path=self.root/'passages.csv';write_coded_passages(self.directory,self.run,path)
        rows=list(csv.DictReader(path.open(encoding='utf-8-sig')))
        self.assertEqual(rows[0]['analysis_method'],'zero_shot')
        self.assertIn('topic_origins',json.loads(rows[0]['assignments_json'])[0])
        theme=create_theme(self.directory,'Manual health code');add_coding(self.directory,self.doc,0,6,theme)
        manual=build_export(self.directory)
        self.assertEqual(manual['metadata']['analysis_method'],'manual')
        self.assertEqual(manual['themes'][0]['topic_origins'],['researcher'])

    def test_refinement_preserves_mixed_origins_and_name_protection(self):
        rename_theme(self.directory,self.topics[0],'Combined evidence')
        merge_themes(self.directory,self.topics[2],self.topics[0])
        def words(passages,assignments,*args):
            return TopicOutput(assignments,{t:[('evidence',.4)] for t in set(assignments)})
        proposal,_=propose_refinement(self.directory,self.run,'words',representation_backend=words)
        review=review_proposal(self.directory,proposal)
        apply_proposal(self.directory,proposal,{entry['theme_id']:entry['recommended'] for entry in review['themes']})
        origin=theme_provenance(self.directory,self.topics[0],proposal)
        self.assertEqual(origin['origins'],['discovered','predefined'])
        self.assertEqual(origin['supplied_names'],['Care access'])
        def unavailable(): self.fail('Only categories/outliers remain; Ollama should be skipped')
        label_run(self.directory,proposal,None,client_factory=unavailable)
        self.assertIn('Combined evidence',[label for _,label,_ in list_themes(self.directory,proposal)])
        text,metadata=analysis_details(self.directory,proposal)
        self.assertIn('initial matching run',text)
        self.assertNotIn('best_similarity_by_segment',metadata['model_info']['zero_shot'])
        self.assertEqual(metadata['model_info']['initial_matching_run_id'],self.run)

    def test_guided_and_general_origins(self):
        for method,expected in [('guided','guided_discovery'),('general','discovered')]:
            settings=replace(self.settings,analysis_method=method)
            run,_=analyze(self.directory,'Paragraphs',[self.doc],settings,
                backend=lambda passages,*args:TopicOutput([0]*len(passages),{}))
            theme=list_themes(self.directory,run)[0][0]
            self.assertEqual(theme_provenance(self.directory,theme,run)['origins'],[expected])

    def test_workspace_and_history_show_method_and_origin(self):
        import os
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        try:
            from PySide6.QtWidgets import QApplication, QDialogButtonBox
        except ImportError:
            self.skipTest('PySide6 not installed')
        from qualitative_themes.gui.coding_workspace import CodingWorkspace
        from qualitative_themes.gui.history import AnalysisHistoryDialog
        from qualitative_themes.gui.refinement import RefinementSetupDialog
        from qualitative_themes.analysis.refinement import load_source
        app=QApplication.instance() or QApplication([])
        workspace=CodingWorkspace();workspace.set_project(self.directory)
        self.assertIn('Zero-shot',workspace.method_status.text())
        self.assertIn('Care access',workspace.method_status.toolTip())
        for i in range(workspace.themes.count()):
            workspace.themes.setCurrentRow(i)
            if workspace.current_theme_id()==self.topics[0]:
                self.assertIn('Predefined category',workspace.theme_origin_label.text())
                self.assertIn('Care access',workspace.theme_origin_label.toolTip())
        workspace.run_choice.setCurrentIndex(workspace.run_choice.findData(None))
        self.assertIn('Manual coding only',workspace.method_status.text())
        history=AnalysisHistoryDialog(self.directory)
        self.assertIn('Zero-shot',history.widgets['runs']['rows'].item(0).text())
        source=load_source(self.directory,self.run)
        refine=RefinementSetupDialog(source,operation='reduce')
        self.assertFalse(refine.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled())
        self.assertIn('protect predefined',refine.consequences.text())
        for widget in [workspace,history,refine]:
            widget.close();widget.deleteLater()
        app.processEvents()
