import array
import hashlib
from io import BytesIO
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from database import init_database, save_job, save_application, save_resume_evaluation
from screening import WEIGHTS
from shortlisting import start_run, save_proposals, save_decisions
from scheduling import utc_now, schedule_interview, claim_invitation, finish_invitation
from interview_store import start_session, get_session
from preflight import issue_receipt


def audio(silent=False):
    data = BytesIO()
    with wave.open(data, 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000)
        f.writeframes(array.array('h', [0 if silent else 3000, 0 if silent else -3000] * 16000).tobytes())
    return data.getvalue()


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'test.db'
        init_database(self.db)
        init_database(self.db)  # Migration is safe on existing databases.
        jd, resume, title = 'Python APIs', 'Built Python APIs', 'Test role'
        job = save_job(title, jd, self.db)
        app = save_application(job, 'Test Candidate', 'candidate@example.com', 'resume.pdf', b'%PDF-test', resume, resume, self.db)
        assessment = {'components': {key: {'applicable': True, 'score': 80,
            'explanation': 'Relevant experience.', 'evidence': [{'resume_quote': resume, 'jd_quote': jd}]} for key in WEIGHTS}, 'gaps': []}
        evaluation = save_resume_evaluation(app, resume, title, jd, assessment, 'fixture', self.db)
        run = start_run(job, 70, self.db)
        save_proposals(run, [{'application_id': app, 'evaluation_id': evaluation}], self.db)
        save_decisions(run, [{'application_id': app, 'approved': True}], self.db)
        self.start = utc_now() + timedelta(days=1)
        interview = schedule_interview(run, app, self.start, 30, self.db)
        invitation = claim_invitation(interview, 'https://example.com', self.db)
        self.token = invitation['interview_url'].split('token=')[1]
        finish_invitation(interview, 'sent', database_path=self.db)
        for module in ('scheduling', 'interview_store', 'preflight'):
            clock = patch(module + '.utc_now', return_value=self.start)
            clock.start(); self.addCleanup(clock.stop)
        self.checks = {'policy_version': 'setup-v1', 'face_count': 1, 'screen_surface': 'monitor',
                       'speaker_confirmed': True, 'playback_confirmed': True, 'rules_accepted': True}
        frame = BytesIO(); Image.new('RGB', (320,240), 'white').save(frame, format='JPEG')
        self.frame = frame.getvalue()

    def receipt(self, **changes):
        return issue_receipt(self.token, self.checks | changes, self.frame, audio(), self.db)['receipt']

    def test_start_requires_receipt_and_refresh_preserves_deadline(self):
        with self.assertRaisesRegex(ValueError, 'system check'):
            start_session(self.token, self.db)
        receipt = self.receipt()
        session = start_session(self.token, self.db, preflight_receipt=receipt)
        saved = get_session(self.token, self.db)
        self.assertEqual(saved['slot_end'], (self.start+timedelta(minutes=30)).isoformat(timespec='seconds'))
        self.assertEqual(start_session(self.token, self.db), session)

    def test_expired_and_unbound_receipts_rejected(self):
        receipt = self.receipt()
        with patch('preflight.utc_now', return_value=self.start+timedelta(minutes=11)):
            with self.assertRaisesRegex(ValueError, 'expired'):
                start_session(self.token, self.db, preflight_receipt=receipt)
        with self.assertRaises(ValueError):
            start_session(self.token, self.db, preflight_receipt='x'*43)

    def test_required_checks_and_sample_validation(self):
        for changes in ({'face_count':0},{'face_count':2},{'face_count':True},{'screen_surface':'window'},
                        {'rules_accepted':False},{'playback_confirmed':False},{'policy_version':'old'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError): self.receipt(**changes)
        with self.assertRaisesRegex(ValueError,'silent'):
            issue_receipt(self.token,self.checks,self.frame,audio(True),self.db)
        dark=BytesIO(); Image.new('RGB',(320,240),'black').save(dark,format='JPEG')
        with self.assertRaisesRegex(ValueError,'dark'):
            issue_receipt(self.token,self.checks,dark.getvalue(),audio(),self.db)
        with self.assertRaises(ValueError):
            issue_receipt(self.token,self.checks,b'not-an-image',audio(),self.db)
        with self.assertRaises(ValueError):
            issue_receipt('invalid-token',self.checks,self.frame,audio(),self.db)

    def test_http_gate_rejects_bypass_and_accepts_valid_samples(self):
        from fastapi.testclient import TestClient
        import api
        client = TestClient(api.app)
        with patch('api.start_session', side_effect=lambda token, **kw: start_session(token, self.db, **kw)), \
             patch('api.issue_receipt', side_effect=lambda *args: issue_receipt(*args, database_path=self.db)):
            self.assertEqual(client.post('/api/interview/sessions', data={'token': self.token}).status_code, 422)
            import json
            response = client.post('/api/interview/preflight', data={'token': self.token, 'checks': json.dumps(self.checks)},
                files={'camera': ('camera.jpg', self.frame, 'image/jpeg'), 'audio': ('sample.wav', audio(), 'audio/wav')})
            self.assertEqual(response.status_code, 200, response.text)
            started = client.post('/api/interview/sessions', data={'token': self.token, 'preflight_receipt': response.json()['receipt']})
            self.assertEqual(started.status_code, 200, started.text)

    def test_receipt_is_invitation_bound_and_consumed_atomically(self):
        import sqlite3
        receipt=self.receipt()
        with sqlite3.connect(self.db) as c:
            c.execute('UPDATE interview_preflight SET invitation_sha256=?', ('different-invitation',))
        with self.assertRaises(ValueError):start_session(self.token,self.db,preflight_receipt=receipt)
        with sqlite3.connect(self.db) as c:
            c.execute('UPDATE interview_preflight SET invitation_sha256=?',(hashlib.sha256(self.token.encode()).hexdigest(),))
        start_session(self.token,self.db,preflight_receipt=receipt)
        with sqlite3.connect(self.db) as c:
            self.assertIsNotNone(c.execute('SELECT consumed_at FROM interview_preflight').fetchone()[0])
            self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())


if __name__ == '__main__': unittest.main()
