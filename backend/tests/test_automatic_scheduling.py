import json
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from automatic_scheduling import preview_automatic_schedule, commit_automatic_schedule
from database import init_database, save_application, save_job, save_resume_evaluation
from interview_templates import save_template
from scheduling import schedule_interview, reserved_window
from screening import WEIGHTS
from shortlisting import start_run, save_proposals, save_decisions


class AutomaticSchedulingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'test.db'
        init_database(self.db)
        self.now = datetime(2030, 5, 6, tzinfo=timezone.utc)
        for module in ('automatic_scheduling', 'scheduling'):
            clock = patch(module + '.utc_now', return_value=self.now)
            clock.start()
            self.addCleanup(clock.stop)
        self.job, self.run, self.apps = self.make_job('Python', 4)
        self.other_job, self.other_run, self.other_apps = self.make_job('Other', 1)

    def make_job(self, title, count):
        job = save_job(title, 'Python APIs', self.db)
        evaluations, apps = [], []
        for index in range(count):
            resume = 'Built Python APIs'
            app = save_application(job, f'Candidate {job} {index}', f'candidate{job}-{index}@example.com',
                                   'resume.pdf', b'%PDF-test', resume, resume, self.db)
            assessment = {'components': {key: {'applicable': True, 'score': 80,
                'explanation': 'Relevant experience.', 'evidence': [{'resume_quote': resume, 'jd_quote': 'Python APIs'}]}
                for key in WEIGHTS}, 'gaps': []}
            evaluation = save_resume_evaluation(app, resume, title, 'Python APIs', assessment, 'fixture', self.db)
            evaluations.append({'application_id': app, 'evaluation_id': evaluation})
            apps.append(app)
        run = start_run(job, 70, self.db)
        save_proposals(run, evaluations, self.db)
        save_decisions(run, [{'application_id': app, 'approved': True} for app in apps], self.db)
        return job, run, apps

    def timestamp(self, hour, minute=0, day=6):
        return self.now.replace(day=day, hour=hour, minute=minute).isoformat()

    def request(self, **changes):
        return {'run_id': self.run, 'application_ids': self.apps[:2],
                'windows': [{'starts_at': self.timestamp(10), 'ends_at': self.timestamp(14)}],
                'duration_minutes': 15, 'gap_minutes': 5, **changes}

    def commit_request(self, request, preview=None, **changes):
        preview = preview or preview_automatic_schedule(request, self.db)
        return {'batch_id': uuid4().hex, 'preview_hash': preview['preview_hash'], 'schedule': request, **changes}

    def interviews(self):
        with sqlite3.connect(self.db) as c:
            return c.execute('SELECT * FROM interviews ORDER BY interview_id').fetchall()

    def test_preview_is_read_only_and_full_reservations_fit_with_gap(self):
        plan = preview_automatic_schedule(self.request(), self.db)
        self.assertTrue(plan['can_schedule'])
        self.assertEqual(plan['reservation_minutes'], 50)
        first, second = plan['assignments']
        self.assertEqual(first['reserved_from'], self.timestamp(10))
        self.assertEqual(first['starts_at'], self.timestamp(10, 10))
        self.assertEqual(first['reserved_until'], self.timestamp(10, 50))
        self.assertEqual(second['reserved_from'], self.timestamp(10, 55))
        self.assertEqual(self.interviews(), [])
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM scheduling_batches').fetchone()[0], 0)

    def test_skips_other_jobs_reservations_and_preserves_existing_bookings(self):
        schedule_interview(self.other_run, self.other_apps[0], datetime.fromisoformat(self.timestamp(10, 10)), 15, self.db)
        before = self.interviews()[0]
        request = self.request()
        plan = preview_automatic_schedule(request, self.db)
        self.assertEqual(plan['assignments'][0]['reserved_from'], self.timestamp(10, 55))
        result = commit_automatic_schedule(self.commit_request(request, plan), self.db)
        self.assertEqual(len(result['assignments']), 2)
        self.assertEqual(self.interviews()[0], before)
        with sqlite3.connect(self.db) as c:
            slots = c.execute('SELECT starts_at,ends_at FROM interviews').fetchall()
            self.assertEqual(c.execute('SELECT count(*) FROM interviews WHERE status="sent"').fetchone()[0], 0)
        windows = sorted(reserved_window(*slot) for slot in slots)
        self.assertTrue(all(a[1] <= b[0] for a, b in zip(windows, windows[1:])))

    def test_existing_same_job_candidate_is_skipped_without_being_rescheduled(self):
        schedule_interview(self.run, self.apps[0], datetime.fromisoformat(self.timestamp(9)), 15, self.db)
        before = self.interviews()[0]
        request = self.request()
        plan = preview_automatic_schedule(request, self.db)
        self.assertEqual(plan['skipped_application_ids'], [self.apps[0]])
        self.assertEqual([item['application_id'] for item in plan['assignments']], [self.apps[1]])
        commit_automatic_schedule(self.commit_request(request, plan), self.db)
        self.assertEqual(self.interviews()[0], before)

    def test_manual_scheduling_still_rejects_an_automatically_reserved_slot(self):
        result = commit_automatic_schedule(self.commit_request(self.request()), self.db)
        before = self.interviews()
        with self.assertRaisesRegex(ValueError, 'already scheduled during'):
            schedule_interview(self.run, self.apps[2], datetime.fromisoformat(result['assignments'][0]['starts_at']), 15, self.db)
        self.assertEqual(self.interviews(), before)

    def test_insufficient_capacity_never_saves_a_partial_batch(self):
        request = self.request(windows=[{'starts_at': self.timestamp(10), 'ends_at': self.timestamp(11)}])
        plan = preview_automatic_schedule(request, self.db)
        self.assertFalse(plan['can_schedule'])
        self.assertEqual(len(plan['assignments']), 1)
        self.assertEqual(len(plan['unassigned']), 1)
        with self.assertRaisesRegex(ValueError, 'not enough space'):
            commit_automatic_schedule(self.commit_request(request, plan), self.db)
        self.assertEqual(self.interviews(), [])

    def test_exact_window_boundary_and_multiple_days(self):
        request = self.request(gap_minutes=0, windows=[
            {'starts_at': self.timestamp(10), 'ends_at': self.timestamp(10, 50)},
            {'starts_at': self.timestamp(10, day=7), 'ends_at': self.timestamp(10, 50, day=7)},
        ])
        plan = preview_automatic_schedule(request, self.db)
        self.assertTrue(plan['can_schedule'])
        self.assertEqual(plan['assignments'][1]['reserved_until'], self.timestamp(10, 50, day=7))

    def test_conflict_created_after_preview_rejects_commit_without_overwriting(self):
        request = self.request()
        command = self.commit_request(request)
        schedule_interview(self.other_run, self.other_apps[0], datetime.fromisoformat(self.timestamp(10, 10)), 15, self.db)
        before = self.interviews()
        with self.assertRaisesRegex(ValueError, 'changed since preview'):
            commit_automatic_schedule(command, self.db)
        self.assertEqual(self.interviews(), before)

    def test_new_manual_booking_for_selected_candidate_invalidates_preview(self):
        request = self.request()
        command = self.commit_request(request)
        schedule_interview(self.run, self.apps[0], datetime.fromisoformat(self.timestamp(16)), 15, self.db)
        before = self.interviews()
        with self.assertRaisesRegex(ValueError, 'changed since preview'):
            commit_automatic_schedule(command, self.db)
        self.assertEqual(self.interviews(), before)

    def test_replaced_shortlist_invalidates_preview(self):
        command = self.commit_request(self.request())
        start_run(self.job, 70, self.db)
        with self.assertRaisesRegex(ValueError, 'replaced'):
            commit_automatic_schedule(command, self.db)
        self.assertEqual(self.interviews(), [])

    def test_cannot_include_candidate_from_another_job_or_unapproved_candidate(self):
        with self.assertRaisesRegex(ValueError, 'only approved'):
            preview_automatic_schedule(self.request(application_ids=self.other_apps), self.db)
        with sqlite3.connect(self.db) as c:
            c.execute('UPDATE shortlist_items SET approved=0 WHERE run_id=? AND application_id=?', (self.run, self.apps[0]))
        with self.assertRaisesRegex(ValueError, 'only approved'):
            preview_automatic_schedule(self.request(), self.db)

    def test_pins_template_version_and_proctoring_policy(self):
        template = save_template('Python basics', 2, 0, self.db)
        request = self.request(template_id=template['template_id'], template_version=1,
                               terminate_on_interruption=True, warning_limit=2)
        command = self.commit_request(request)
        save_template('Python expanded', 4, 2, self.db, template_id=template['template_id'], expected_version=1)
        commit_automatic_schedule(command, self.db)
        with sqlite3.connect(self.db) as c:
            for raw, terminate, limit in c.execute('SELECT template_json,terminate_on_interruption,warning_limit FROM interviews'):
                self.assertEqual(json.loads(raw)['version'], 1)
                self.assertEqual((terminate, limit), (1, 2))

    def test_commit_retries_are_idempotent_even_after_shortlist_changes(self):
        command = self.commit_request(self.request())
        first = commit_automatic_schedule(command, self.db)
        start_run(self.job, 70, self.db)
        self.assertEqual(commit_automatic_schedule(command, self.db), first)
        self.assertEqual(len(self.interviews()), 2)
        altered = dict(command, preview_hash='0' * 64)
        with self.assertRaisesRegex(ValueError, 'already used'):
            commit_automatic_schedule(altered, self.db)

    def test_concurrent_identical_commits_create_only_one_batch(self):
        command = self.commit_request(self.request())
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(commit_automatic_schedule, command, self.db) for _ in range(2)]
            results = [future.result(timeout=10) for future in futures]
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(self.interviews()), 2)
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM scheduling_batches').fetchone()[0], 1)

    def test_concurrent_distinct_batches_cannot_double_book(self):
        first = self.commit_request(self.request())
        second = self.commit_request(self.request(run_id=self.other_run, application_ids=self.other_apps))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(commit_automatic_schedule, command, self.db) for command in (first, second)]
            outcomes = []
            for future in futures:
                try:
                    outcomes.append(future.result(timeout=10))
                except ValueError:
                    outcomes.append(None)
        self.assertEqual(sum(outcome is not None for outcome in outcomes), 1)
        with sqlite3.connect(self.db) as c:
            windows = sorted(reserved_window(*row) for row in c.execute('SELECT starts_at,ends_at FROM interviews'))
        self.assertTrue(all(a[1] <= b[0] for a, b in zip(windows, windows[1:])))

    def test_failure_mid_batch_rolls_back_every_insert_and_receipt(self):
        command = self.commit_request(self.request())
        with sqlite3.connect(self.db) as c:
            c.execute(f"CREATE TRIGGER test_failure BEFORE INSERT ON interviews WHEN NEW.application_id={self.apps[1]} BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            commit_automatic_schedule(command, self.db)
        self.assertEqual(self.interviews(), [])
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM scheduling_batches').fetchone()[0], 0)

    def test_invalid_windows_ids_and_counts_are_rejected(self):
        invalid = [
            {'windows': []}, {'windows': [{'starts_at': self.timestamp(12), 'ends_at': self.timestamp(10)}]},
            {'windows': [{'starts_at': self.timestamp(10)[:-6], 'ends_at': self.timestamp(14)}]},
            {'windows': [{'starts_at': self.timestamp(10), 'ends_at': self.timestamp(14)}, {'starts_at': self.timestamp(13), 'ends_at': self.timestamp(15)}]},
            {'windows': [{'starts_at': self.now.isoformat(), 'ends_at': self.timestamp(14)}]},
            {'application_ids': [True]}, {'application_ids': [self.apps[0], self.apps[0]]},
            {'duration_minutes': 4}, {'gap_minutes': -1}, {'warning_limit': 21},
        ]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                preview_automatic_schedule(self.request(**changes), self.db)

    def test_api_validation_and_preview_do_not_mutate_database(self):
        import api
        from fastapi.testclient import TestClient
        with patch('api.preview_automatic_schedule', side_effect=lambda request: preview_automatic_schedule(request, self.db)), \
             patch('api.commit_automatic_schedule', side_effect=lambda request: commit_automatic_schedule(request, self.db)):
            client = TestClient(api.app)
            response = client.post('/api/scheduling/automatic/preview', json=self.request())
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.interviews(), [])
            self.assertEqual(client.post('/api/scheduling/automatic/preview', json=self.request(duration_minutes=True)).status_code, 422)
            saved = client.post('/api/scheduling/automatic/commit', json=self.commit_request(self.request(), response.json()))
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(len(self.interviews()), 2)


if __name__ == '__main__':
    unittest.main()
