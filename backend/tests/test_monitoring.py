import array
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from database import init_database, save_application, save_job, save_resume_evaluation
from interview_store import (
    claim_operation, complete_session, get_events, get_session, pause_session,
    release_operation, resume_session, save_events, start_session, terminate_session,
)
from interview_reports import finished_interviews, report_source, shortlisted_candidates
from invitations import build_email
from preflight import issue_receipt
from scheduling import claim_invitation, finish_invitation, get_interviews, schedule_interview, utc_now
from screening import WEIGHTS
from shortlisting import save_decisions, save_proposals, start_run


def audio():
    data = BytesIO()
    with wave.open(data, 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000)
        f.writeframes(array.array('h', [3000, -3000] * 16000).tobytes())
    return data.getvalue()


class MonitoringFixture(unittest.TestCase):
    flagged = False

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'test.db'
        init_database(self.db)
        jd, resume, title = 'Python APIs', 'Built Python APIs', 'Test role'
        job = save_job(title, jd, self.db)
        app = save_application(job, 'Test Candidate', 'candidate@example.com', 'resume.pdf', b'%PDF-test', resume, resume, self.db)
        assessment = {'components': {key: {'applicable': True, 'score': 80,
            'explanation': 'Relevant experience.', 'evidence': [{'resume_quote': resume, 'jd_quote': jd}]} for key in WEIGHTS}, 'gaps': []}
        evaluation = save_resume_evaluation(app, resume, title, jd, assessment, 'fixture', self.db)
        self.app1, self.eval1 = app, evaluation
        run = start_run(job, 70, self.db)
        save_proposals(run, [{'application_id': app, 'evaluation_id': evaluation}], self.db)
        save_decisions(run, [{'application_id': app, 'approved': True}], self.db)
        self.start = utc_now() + timedelta(days=1)
        interview = schedule_interview(run, app, self.start, 30, self.db, terminate_on_interruption=self.flagged)
        invitation = claim_invitation(interview, 'https://example.com', self.db)
        self.token = invitation['interview_url'].split('token=')[1]
        finish_invitation(interview, 'sent', database_path=self.db)
        for module in ('scheduling', 'interview_store', 'preflight'):
            clock = patch(module + '.utc_now', return_value=self.start)
            clock.start(); self.addCleanup(clock.stop)
        checks = {'policy_version': 'setup-v1', 'face_count': 1, 'screen_surface': 'monitor',
                  'speaker_confirmed': True, 'playback_confirmed': True, 'rules_accepted': True}
        frame = BytesIO(); Image.new('RGB', (320, 240), 'white').save(frame, format='JPEG')
        receipt = issue_receipt(self.token, checks, frame.getvalue(), audio(), self.db)['receipt']
        self.session = start_session(self.token, self.db, preflight_receipt=receipt)
        self.job, self.run = job, run

    def event(self, **changes):
        item = {'kind': 'browser.focus', 'at': '2030-05-06T07:08:09Z', 'detail': {'focused': False}}
        return item | changes


class MonitoringTests(MonitoringFixture):
    def test_saves_batch_with_server_timestamp(self):
        events = [self.event(kind='browser.visibility', detail={'hidden': True}),
                  self.event(kind='browser.clipboard', at='2030-05-06T07:08:10+00:00', detail={'action': 'paste'})]
        self.assertEqual(save_events(self.token, events, self.db), 2)
        rows = get_events(self.session, self.db)
        self.assertEqual([row['kind'] for row in rows], ['browser.visibility', 'browser.clipboard'])
        self.assertEqual(rows[0]['client_at'], '2030-05-06T07:08:09Z')
        self.assertEqual(rows[0]['recorded_at'], self.start.isoformat(timespec='seconds'))
        self.assertEqual(json.loads(rows[0]['detail_json']), {'hidden': True})

    def test_rejects_unknown_or_sessionless_token(self):
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            save_events('x' * 43, [self.event()], self.db)
        app = save_application(self.job, 'Second Candidate', 'second@example.com', 'r.pdf', b'%PDF-2', 'text', 'text', self.db)
        assessment = {'components': {key: {'applicable': True, 'score': 80, 'explanation': 'ok',
            'evidence': [{'resume_quote': 'text', 'jd_quote': 'Python APIs'}]} for key in WEIGHTS}, 'gaps': []}
        evaluation = save_resume_evaluation(app, 'text', 'Test role', 'Python APIs', assessment, 'fixture', self.db)
        run2 = start_run(self.job, 70, self.db)
        save_proposals(run2, [{'application_id': self.app1, 'evaluation_id': self.eval1},
                              {'application_id': app, 'evaluation_id': evaluation}], self.db)
        save_decisions(run2, [{'application_id': self.app1, 'approved': False},
                              {'application_id': app, 'approved': True}], self.db)
        interview = schedule_interview(run2, app, self.start + timedelta(hours=2), 30, self.db)
        other = claim_invitation(interview, 'https://example.com', self.db)['interview_url'].split('token=')[1]
        with self.assertRaisesRegex(ValueError, 'session is unavailable'):
            save_events(other, [self.event()], self.db)

    def test_rejects_invalid_payloads(self):
        bad = ([], [self.event()] * 201, 'not-a-list',
               [self.event(kind='unknown.kind')], [self.event(at='not-a-time')],
               [self.event(at='2030-01-01T00:00:00')], [{'kind': 'browser.focus'}],
               [self.event(detail='not-a-dict')], [self.event(detail={'blob': 'x' * 3000})])
        for payload in bad:
            with self.subTest(payload=str(payload)[:80]), self.assertRaises(ValueError):
                save_events(self.token, payload, self.db)
        self.assertEqual(get_events(self.session, self.db), [])

    def test_accepts_late_events_after_completion(self):
        complete_session(self.session, self.db)
        self.assertEqual(save_events(self.token, [self.event()], self.db), 1)

    def test_http_endpoint_reports_events(self):
        from fastapi.testclient import TestClient
        import api
        client = TestClient(api.app)
        with patch('api.save_events', side_effect=lambda token, events: save_events(token, events, self.db)):
            response = client.post('/api/interview/events', data={'token': self.token, 'events': json.dumps([self.event()])})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {'saved': 1})
            bad = client.post('/api/interview/events', data={'token': self.token, 'events': 'not-json'})
            self.assertEqual(bad.status_code, 400)
            missing = client.post('/api/interview/events', data={'token': self.token})
            self.assertEqual(missing.status_code, 422)


class PauseTests(MonitoringFixture):
    def test_pause_and_resume_extends_deadline(self):
        paused = pause_session(self.token, 'fullscreen-exit', self.db)
        self.assertEqual(paused['pause_reason'], 'fullscreen-exit')
        self.assertIsNone(paused['terminate_at'])
        saved = get_session(self.token, self.db)
        self.assertEqual(saved['paused_at'], self.start.isoformat(timespec='seconds'))
        with patch('interview_store.utc_now', return_value=self.start + timedelta(minutes=5)):
            resumed = resume_session(self.token, self.db)
        self.assertEqual(resumed['credited_seconds'], 300)
        self.assertEqual(resumed['slot_end'], (self.start + timedelta(minutes=35)).isoformat(timespec='seconds'))
        saved = get_session(self.token, self.db)
        self.assertIsNone(saved['paused_at'])
        self.assertIsNone(saved['pause_reason'])
        self.assertEqual(saved['slot_end'], resumed['slot_end'])

    def test_pause_blocks_interview_actions_until_resumed(self):
        pause_session(self.token, 'camera-ended', self.db)
        with self.assertRaisesRegex(ValueError, 'paused'):
            claim_operation(self.token, self.db)
        resume_session(self.token, self.db)
        session_id, operation = claim_operation(self.token, self.db)
        self.assertEqual(session_id, self.session)
        release_operation(session_id, operation, self.db)

    def test_pause_is_idempotent_and_validates_reason(self):
        first = pause_session(self.token, 'fullscreen-required', self.db)
        second = pause_session(self.token, 'camera-ended', self.db)
        self.assertEqual(first, second)
        self.assertEqual(get_session(self.token, self.db)['pause_reason'], 'fullscreen-required')
        with self.assertRaisesRegex(ValueError, 'reason'):
            pause_session(self.token, 'taking-a-break', self.db)
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            pause_session('x' * 43, 'camera-ended', self.db)
        resumed = resume_session(self.token, self.db)
        self.assertEqual(resumed['credited_seconds'], 0)
        again = resume_session(self.token, self.db)
        self.assertEqual(again, resumed)

    def test_pause_credit_is_capped(self):
        pause_session(self.token, 'fullscreen-exit', self.db)
        with patch('interview_store.utc_now', return_value=self.start + timedelta(minutes=20)):
            resumed = resume_session(self.token, self.db)
        self.assertEqual(resumed['credited_seconds'], 600)
        pause_session(self.token, 'fullscreen-exit', self.db)
        with patch('interview_store.utc_now', return_value=self.start + timedelta(minutes=25)):
            capped = resume_session(self.token, self.db)
        self.assertEqual(capped['credited_seconds'], 0)
        self.assertEqual(capped['slot_end'], resumed['slot_end'])

    def test_finished_sessions_cannot_pause_or_resume(self):
        complete_session(self.session, self.db)
        with self.assertRaisesRegex(ValueError, 'not active'):
            pause_session(self.token, 'camera-ended', self.db)
        with self.assertRaisesRegex(ValueError, 'not active'):
            resume_session(self.token, self.db)

    def test_http_pause_endpoints(self):
        from fastapi.testclient import TestClient
        import api
        client = TestClient(api.app)
        with patch('api.pause_session', side_effect=lambda token, reason: pause_session(token, reason, self.db)), \
             patch('api.resume_session', side_effect=lambda token: resume_session(token, self.db)):
            paused = client.post('/api/interview/pause', data={'token': self.token, 'reason': 'camera-ended'})
            self.assertEqual(paused.status_code, 200, paused.text)
            self.assertEqual(paused.json()['pause_reason'], 'camera-ended')
            resumed = client.post('/api/interview/resume', data={'token': self.token})
            self.assertEqual(resumed.status_code, 200, resumed.text)
            self.assertIn('slot_end', resumed.json())
            bad = client.post('/api/interview/pause', data={'token': 'x' * 43, 'reason': 'camera-ended'})
            self.assertEqual(bad.status_code, 400)


class TerminationTests(MonitoringFixture):
    flagged = True

    def test_hr_flag_persists_and_validates(self):
        interviews = get_interviews(self.job, self.db)
        self.assertEqual(len(interviews), 1)
        self.assertEqual(interviews[0]['terminate_on_interruption'], 1)
        with self.assertRaisesRegex(ValueError, 'termination'):
            schedule_interview('x', 1, self.start, 30, self.db, terminate_on_interruption='yes')

    def test_pause_arms_countdown_for_fullscreen_and_absence(self):
        for reason in ('face-absent', 'fullscreen-exit', 'fullscreen-required'):
            with self.subTest(reason=reason):
                paused = pause_session(self.token, reason, self.db)
                self.assertEqual(paused['terminate_at'], (self.start + timedelta(seconds=60)).isoformat(timespec='seconds'))
                self.assertEqual(get_session(self.token, self.db)['terminate_at'], paused['terminate_at'])
                resume_session(self.token, self.db)

    def test_camera_pause_never_arms_countdown(self):
        paused = pause_session(self.token, 'camera-ended', self.db)
        self.assertIsNone(paused['terminate_at'])

    def test_resume_clears_countdown(self):
        pause_session(self.token, 'fullscreen-exit', self.db)
        resume_session(self.token, self.db)
        self.assertIsNone(get_session(self.token, self.db)['terminate_at'])

    def test_terminate_before_and_after_deadline(self):
        pause_session(self.token, 'fullscreen-exit', self.db)
        with patch('interview_store.utc_now', return_value=self.start + timedelta(seconds=30)):
            early = terminate_session(self.token, self.db)
        self.assertEqual(early, {'terminated': False, 'remaining_seconds': 30})
        self.assertEqual(get_session(self.token, self.db)['status'], 'active')
        with patch('interview_store.utc_now', return_value=self.start + timedelta(seconds=61)):
            done = terminate_session(self.token, self.db)
        self.assertEqual(done, {'terminated': True, 'remaining_seconds': 0})
        saved = get_session(self.token, self.db)
        self.assertEqual(saved['status'], 'terminated')
        self.assertIsNotNone(saved['ended_at'])
        self.assertEqual(terminate_session(self.token, self.db)['terminated'], True)

    def test_resume_after_deadline_terminates(self):
        pause_session(self.token, 'face-absent', self.db)
        with patch('interview_store.utc_now', return_value=self.start + timedelta(seconds=61)):
            with self.assertRaisesRegex(ValueError, 'not active'):
                resume_session(self.token, self.db)
        self.assertEqual(get_session(self.token, self.db)['status'], 'terminated')

    def test_terminated_session_is_final(self):
        pause_session(self.token, 'fullscreen-exit', self.db)
        with patch('interview_store.utc_now', return_value=self.start + timedelta(seconds=61)):
            terminate_session(self.token, self.db)
        with self.assertRaisesRegex(ValueError, 'not active'):
            claim_operation(self.token, self.db)
        self.assertEqual(start_session(self.token, self.db), self.session)

    def test_warn_event_kinds_accepted(self):
        events = [self.event(kind='warn.started', detail={'signal': 'face'}),
                  self.event(kind='warn.cleared', detail={'signal': 'face'})]
        self.assertEqual(save_events(self.token, events, self.db), 2)
        self.assertEqual([row['kind'] for row in get_events(self.session, self.db)], ['warn.started', 'warn.cleared'])

    def test_terminated_interview_flows_to_reports(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute(
                "INSERT INTO interview_turns (session_id,sequence,kind,main_number,question,answer_text) VALUES (?,?,?,?,?,?)",
                (self.session, 1, 'main', 1, 'What is an API?', 'An interface.'))
        pause_session(self.token, 'fullscreen-exit', self.db)
        with patch('interview_store.utc_now', return_value=self.start + timedelta(seconds=61)):
            terminate_session(self.token, self.db)
        self.assertIn(self.session, [row['session_id'] for row in finished_interviews(self.db)])
        source = report_source(self.session, self.db)
        self.assertEqual(source['status'], 'terminated')
        self.assertEqual(len(source['turns']), 1)
        rows = shortlisted_candidates(self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['interview_status'], 'Terminated')

    def test_legacy_database_migrates(self):
        for with_pause_columns in (False, True):
            with self.subTest(with_pause_columns=with_pause_columns):
                path = Path(self.temp.name) / f'legacy-{with_pause_columns}.db'
                pause_columns = (", paused_at TEXT, pause_reason TEXT, paused_seconds INTEGER NOT NULL DEFAULT 0"
                                 if with_pause_columns else "")
                with sqlite3.connect(path) as connection:
                    connection.execute(
                        """CREATE TABLE interviews (interview_id INTEGER PRIMARY KEY AUTOINCREMENT,
                           application_id INTEGER NOT NULL UNIQUE, run_id TEXT NOT NULL, evaluation_id INTEGER NOT NULL,
                           starts_at TEXT NOT NULL, ends_at TEXT NOT NULL)""")
                    connection.execute(
                        f"""CREATE TABLE interview_sessions (session_id TEXT PRIMARY KEY, interview_id INTEGER NOT NULL,
                           evaluation_id INTEGER NOT NULL, invitation_sha256 TEXT NOT NULL UNIQUE,
                           status TEXT NOT NULL CHECK (status IN ('active','completed','expired')), slot_end TEXT NOT NULL,
                           plan_json TEXT, summary TEXT NOT NULL DEFAULT '', processing_id TEXT, busy_until TEXT{pause_columns},
                           started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, ended_at TEXT)""")
                    connection.execute(
                        "INSERT INTO interview_sessions (session_id,interview_id,evaluation_id,invitation_sha256,status,slot_end) VALUES (?,?,?,?,?,?)",
                        ('legacy-session', 1, 1, 'hash', 'completed', '2030-01-01T00:00:00+00:00'))
                init_database(path)
                init_database(path)
                with sqlite3.connect(path) as connection:
                    columns = {row[1] for row in connection.execute("PRAGMA table_info(interview_sessions)")}
                    self.assertTrue({'paused_at', 'pause_reason', 'paused_seconds', 'terminate_at'} <= columns)
                    row = connection.execute(
                        "SELECT session_id,status,slot_end,paused_seconds FROM interview_sessions").fetchone()
                    self.assertEqual(tuple(row), ('legacy-session', 'completed', '2030-01-01T00:00:00+00:00', 0))
                    connection.execute("UPDATE interview_sessions SET status='terminated' WHERE session_id='legacy-session'")
                    self.assertEqual(
                        connection.execute("SELECT status FROM interview_sessions").fetchone()[0], 'terminated')
                    flag_columns = {row[1] for row in connection.execute("PRAGMA table_info(interviews)")}
                    self.assertIn('terminate_on_interruption', flag_columns)

    def test_invitation_mentions_auto_end_when_armed(self):
        invitation = {'full_name': 'Test Candidate', 'email': 'candidate@example.com', 'job_title': 'Test role',
                      'starts_at': self.start.isoformat(), 'ends_at': (self.start + timedelta(minutes=30)).isoformat(),
                      'interview_url': 'https://example.com/interview?token=x', 'terminate_on_interruption': 1}
        armed = build_email(invitation, 'sender@example.com')
        self.assertIn('3 fullscreen/face-absence warnings', armed.get_body(('plain',)).get_content())
        invitation['terminate_on_interruption'] = 0
        plain = build_email(invitation, 'sender@example.com')
        self.assertNotIn('fullscreen/face-absence warnings', plain.get_body(('plain',)).get_content())

    def test_http_terminate_endpoint(self):
        from fastapi.testclient import TestClient
        import api
        client = TestClient(api.app)
        with patch('api.terminate_session', side_effect=lambda token: terminate_session(token, self.db)):
            pause_session(self.token, 'fullscreen-exit', self.db)
            response = client.post('/api/interview/terminate', data={'token': self.token})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {'terminated': False, 'remaining_seconds': 60})
            bad = client.post('/api/interview/terminate', data={'token': 'x' * 43})
            self.assertEqual(bad.status_code, 400)


if __name__ == '__main__': unittest.main()
