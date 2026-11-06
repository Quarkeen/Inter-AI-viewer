import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from admissions_preset import TEMPLATE_ID, QUESTIONS, ensure_admissions_template
from database import init_database, save_application, save_job
from interview_templates import list_templates, read_config, save_template, snapshot


class AdmissionsPresetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'test.db'
        init_database(self.db)

    def test_installs_once_with_ten_fixed_questions_and_preserves_other_records(self):
        job = save_job('Existing admissions opening', 'Existing description', self.db)
        save_application(job, 'Test applicant', 'test@example.com', 'resume.pdf', b'%PDF-test', 'Existing resume', 'Existing resume', self.db)
        other = save_template('A different interview', 3, 0, self.db)
        with sqlite3.connect(self.db) as c:
            preserved = {table: c.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall() for table in ('jobs', 'candidates', 'applications')}
        self.assertTrue(ensure_admissions_template(self.db))
        self.assertFalse(ensure_admissions_template(self.db))
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM interview_templates WHERE template_id=?', (TEMPLATE_ID,)).fetchone()[0], 1)
            for table, rows in preserved.items():
                self.assertEqual(c.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall(), rows)
            config = read_config(snapshot(c, TEMPLATE_ID, 1))
            self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())
        self.assertEqual((config['mode'], config['main_questions'], config['max_followups']), ('custom', 10, 4))
        self.assertEqual(len(config['questions']), 10)
        self.assertTrue(all(item['kind'] == 'must_ask' and item['expected_points'] and item['example_answer'] and item['followup_guidance'] for item in config['questions']))
        self.assertEqual([item['question'] for item in config['questions']], [item['question'] for item in QUESTIONS])
        templates = list_templates(self.db)
        self.assertEqual(templates[0]['template_id'], TEMPLATE_ID)
        self.assertEqual(templates[0]['recommended_duration_minutes'], 45)
        self.assertTrue(templates[0]['is_preset'])
        self.assertEqual(templates[1]['template_id'], other['template_id'])

    def test_startup_does_not_overwrite_hr_revisions_or_assigned_versions(self):
        ensure_admissions_template(self.db)
        with sqlite3.connect(self.db) as c:
            frozen = snapshot(c, TEMPLATE_ID, 1)
        initial = read_config(frozen)
        edited = [dict(item) for item in initial['questions']]
        edited[0]['expected_points'] = 'Faculty-reviewed learning goals and relevant background.'
        revision = save_template('Faculty-reviewed admissions interview', 10, 4, self.db,
                                 template_id=TEMPLATE_ID, expected_version=1, mode='custom', questions=edited)
        self.assertTrue(revision['is_preset'])
        self.assertFalse(ensure_admissions_template(self.db))
        versions = list_templates(self.db, TEMPLATE_ID)
        self.assertEqual([item['version'] for item in versions], [2, 1])
        self.assertEqual(versions[0]['questions'][0]['expected_points'], edited[0]['expected_points'])
        with sqlite3.connect(self.db) as c:
            self.assertEqual(snapshot(c, TEMPLATE_ID, 1), frozen)

    def test_api_startup_initialises_schema_before_installing_the_preset(self):
        import api
        order = []
        with patch('api._warn_unless_launcher_env'), patch('api.init_database', side_effect=lambda: order.append('schema')), patch('api.ensure_admissions_template', side_effect=lambda: order.append('preset')):
            api._startup()
        self.assertEqual(order, ['schema', 'preset'])
