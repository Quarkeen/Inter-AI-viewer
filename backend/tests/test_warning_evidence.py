import base64
import json
import sqlite3
from io import BytesIO
from unittest.mock import Mock, patch

from PIL import Image
from pypdf import PdfReader
from test_monitoring import MonitoringFixture
from interview_store import claim_operation, get_events, get_session, resume_session, save_events, start_session
from interview_reports import finished_interviews, generate_report, get_report, report_source, source_hash
from warning_evidence import gaze_monitoring_summary, list_warnings, warning_snapshot
from report_pdf import export_report_pdf


class WarningEvidenceTests(MonitoringFixture):
    flagged = True

    def event_warning(self, number, *, kind='warn.started', signal='face', photo=True):
        identity=f"event-{number}-{kind.replace('.', '-')}"
        event = dict(id=identity, kind=kind, at=self.start.isoformat(), detail=dict(signal=signal, warning_id=f'warning-{number}'))
        if photo and kind == 'warn.started':
            data = BytesIO(); Image.new('RGB', (320,240), '#237d78').save(data, format='JPEG')
            event.update(snapshot='data:image/jpeg;base64,' + base64.b64encode(data.getvalue()).decode(), snapshot_at=self.start.isoformat())
        return event

    def record_episode(self, number, signal='face', photo=True):
        save_events(self.token, [self.event_warning(number, signal=signal, photo=photo), self.event_warning(number, kind='warn.cleared', signal=signal, photo=False)], self.db)

    def finish_report(self):
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE interview_sessions SET status='completed',ended_at=started_at WHERE session_id=? AND status='active'", (self.session,))
        model = Mock(side_effect=AssertionError('An empty interview must not receive invented AI scores'))
        identity = generate_report(self.session, model, 'fixture', self.db)
        model.assert_not_called()
        return identity

    def test_recovered_episodes_accumulate_on_server_and_fourth_warning_terminates(self):
        for number in range(1,4):
            self.record_episode(number)
            self.assertEqual(get_session(self.token,self.db)['status'], 'active')
            self.assertEqual(get_session(self.token,self.db)['proctoring']['warning_count'], number)
        # An acknowledged request can be retried without adding a strike or log row.
        before = len(get_events(self.session,self.db))
        self.record_episode(3)
        self.assertEqual(len(get_events(self.session,self.db)), before)
        self.record_episode(4)
        saved = get_session(self.token,self.db)
        self.assertEqual(saved['status'], 'terminated')
        self.assertEqual(saved['pause_reason'], 'warning-limit')
        self.assertEqual(saved['proctoring']['warning_count'], 4)
        self.assertEqual(start_session(self.token,self.db), self.session)
        with self.assertRaisesRegex(ValueError, 'not active'):
            resume_session(self.token,self.db)
        with self.assertRaisesRegex(ValueError, 'not active'):
            claim_operation(self.token,self.db)

    def test_zero_allowance_ends_on_first_warning_and_off_setting_never_uses_count_to_end(self):
        with sqlite3.connect(self.db) as c:
            c.execute('UPDATE interviews SET warning_limit=0')
        self.record_episode(1)
        self.assertEqual(get_session(self.token,self.db)['status'],'terminated')
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE interview_sessions SET status='active',pause_reason=NULL,ended_at=NULL")
            c.execute('UPDATE interviews SET terminate_on_interruption=0')
        for number in range(2,7):self.record_episode(number)
        self.assertEqual(get_session(self.token,self.db)['status'],'active')
        self.assertEqual(get_session(self.token,self.db)['proctoring']['warning_count'],6)

    def test_camera_failures_have_evidence_rows_but_do_not_consume_warning_allowance(self):
        with sqlite3.connect(self.db) as c:c.execute('UPDATE interviews SET warning_limit=0')
        for number in range(1,5):self.record_episode(number,'camera',photo=False)
        saved=get_session(self.token,self.db)
        self.assertEqual(saved['status'],'active')
        self.assertEqual(saved['proctoring']['warning_count'],0)
        warnings=list_warnings(self.session,self.db)
        self.assertEqual(len(warnings),4)
        self.assertTrue(all(not row['has_snapshot'] for row in warnings))

    def test_one_open_warning_episode_is_not_counted_again_after_reload(self):
        first=self.event_warning(1)
        save_events(self.token,[first],self.db)
        self.assertEqual(get_session(self.token,self.db)['proctoring']['active_warnings'],{'face':'warning-1'})
        save_events(self.token,[self.event_warning(2)],self.db)
        self.assertEqual(get_session(self.token,self.db)['proctoring']['warning_count'],1)
        self.assertEqual(len(list_warnings(self.session,self.db)),1)
        save_events(self.token,[self.event_warning(1,kind='warn.cleared',photo=False)],self.db)
        self.record_episode(3)
        self.assertEqual(get_session(self.token,self.db)['proctoring']['warning_count'],2)

    def test_snapshot_is_decoded_and_timestamps_are_retained_without_becoming_score_evidence(self):
        event=self.event_warning(1)
        save_events(self.token,[event],self.db)
        rows=list_warnings(self.session,self.db)
        self.assertEqual(rows[0]['client_at'],self.start.isoformat())
        self.assertEqual(rows[0]['recorded_at'],self.start.isoformat(timespec='seconds'))
        self.assertTrue(rows[0]['has_snapshot'])
        with Image.open(BytesIO(warning_snapshot(self.session,'warning-1',self.db))) as image:
            self.assertEqual(image.size,(320,240))
            self.assertEqual(image.format,'JPEG')
        with self.assertRaises(ValueError):warning_snapshot('other-session','warning-1',self.db)
        self.finish_report()
        self.assertNotIn('snapshot',json.dumps(report_source(self.session,self.db)))

    def test_invalid_image_or_warning_metadata_rejects_batch_without_partial_records(self):
        bads=[self.event_warning(1)|{'snapshot':'data:image/jpeg;base64,invalid'},
              self.event_warning(1)|{'snapshot_at':'invalid'},
              self.event_warning(1)|{'detail':dict(signal='unknown-signal',warning_id='warning-1')},
              self.event_warning(1)|{'id':'invalid/id'}]
        for bad in bads:
            with self.subTest(bad=bad.get('id')),self.assertRaises(ValueError):save_events(self.token,[bad],self.db)
        self.assertEqual(get_events(self.session,self.db),[])
        self.assertEqual(list_warnings(self.session,self.db),[])

    def test_gaze_warnings_retain_snapshots_without_scores_or_termination_strikes(self):
        with sqlite3.connect(self.db) as c:c.execute('UPDATE interviews SET warning_limit=0')
        for number in range(1,5):self.record_episode(number,'gaze')
        saved=get_session(self.token,self.db)
        self.assertEqual(saved['status'],'active')
        self.assertEqual(saved['proctoring']['warning_count'],0)
        rows=list_warnings(self.session,self.db)
        self.assertEqual(len(rows),4)
        self.assertTrue(all(row['has_snapshot'] and 'review only' in row['label'] for row in rows))
        identity=self.finish_report()
        self.assertNotIn('gaze',json.dumps(report_source(self.session,self.db)))
        pdf=PdfReader(BytesIO(export_report_pdf(identity,self.db)))
        text=pdf.pages[2].extract_text()
        self.assertIn('Gaze estimate for human review only',text)
        self.assertTrue(list(pdf.pages[2].images))

    def test_gaze_schema_migration_preserves_existing_evidence_and_is_idempotent(self):
        from database import init_database
        self.record_episode(1)
        before=list_warnings(self.session,self.db,include_images=True)
        with sqlite3.connect(self.db) as c:
            c.execute('ALTER TABLE interview_warnings RENAME TO warning_copy')
            c.execute("""CREATE TABLE interview_warnings (
                session_id TEXT NOT NULL REFERENCES interview_sessions(session_id), warning_id TEXT NOT NULL,
                signal TEXT NOT NULL CHECK(signal IN ('face','fullscreen','camera')),
                recorded_at TEXT NOT NULL,client_at TEXT NOT NULL,cleared_at TEXT,
                snapshot_jpeg BLOB,snapshot_at TEXT,snapshot_note TEXT NOT NULL DEFAULT '',PRIMARY KEY(session_id,warning_id))""")
            c.execute('INSERT INTO interview_warnings SELECT * FROM warning_copy')
            c.execute('DROP TABLE warning_copy')
        init_database(self.db);init_database(self.db)
        self.assertEqual(list_warnings(self.session,self.db,include_images=True),before)
        self.record_episode(2,'gaze')
        self.assertEqual(len(list_warnings(self.session,self.db)),2)

    def test_gaze_coverage_discloses_missing_calibration_and_tracking_gaps(self):
        self.assertIn('No gaze tracking record',gaze_monitoring_summary(self.session,self.db))
        for state in ('not-calibrated','enabled','uncertain','enabled'):
            save_events(self.token,[self.event(kind='gaze.state',detail={'state':state,'review_only':True})],self.db)
        summary=gaze_monitoring_summary(self.session,self.db)
        self.assertIn('Calibrated gaze tracking was used',summary)
        self.assertIn('1 tracking interruption',summary)

    def test_historical_warnings_are_kept_with_explicit_missing_snapshot(self):
        save_events(self.token,[self.event(kind='warn.started',detail={'signal':'fullscreen'}),self.event(kind='warn.started',detail={'signal':'face'})],self.db)
        rows=list_warnings(self.session,self.db)
        self.assertEqual(len(rows),2)
        self.assertTrue(all('historical' in row['snapshot_note'] for row in rows))
        self.assertEqual(get_session(self.token,self.db)['proctoring']['warning_count'],0)

    def test_terminated_before_any_answer_still_has_an_integrity_report_without_ai_scores(self):
        with sqlite3.connect(self.db) as c:c.execute('UPDATE interviews SET warning_limit=0')
        self.record_episode(1)
        self.assertIn(self.session,[row['session_id'] for row in finished_interviews(self.db)])
        identity=self.finish_report()
        saved=get_report(identity,database_path=self.db)
        self.assertIsNone(saved['assessment']['overall_score'])
        self.assertEqual(saved['assessment']['coverage'],0)
        pdf=PdfReader(BytesIO(export_report_pdf(identity,self.db)))
        self.assertEqual(len(pdf.pages),3)
        self.assertIn('Warning 1',pdf.pages[2].extract_text())
        self.assertTrue(list(pdf.pages[2].images))

    def test_pdf_contains_all_warnings_with_continuations_and_late_evidence_keeps_score_cache(self):
        with sqlite3.connect(self.db) as c:c.execute('UPDATE interviews SET terminate_on_interruption=0')
        identity=self.finish_report()
        fingerprint=source_hash(report_source(self.session,self.db))
        for number in range(1,8):self.record_episode(number,photo=number%2==1)
        self.assertEqual(source_hash(report_source(self.session,self.db)),fingerprint)
        pdf=PdfReader(BytesIO(export_report_pdf(identity,self.db)))
        self.assertEqual(len(pdf.pages),4)
        text=' '.join(page.extract_text() for page in pdf.pages[2:])
        for number in range(1,8):self.assertIn(f'Warning {number}',text)
        self.assertIn('Snapshot unavailable',text)
        self.assertIn('4 / 4',pdf.pages[3].extract_text())

    def test_http_warning_acknowledges_server_termination_and_snapshot_is_only_for_finished_interview(self):
        import api
        from fastapi.testclient import TestClient
        from interview_store import get_session as session_state
        client=TestClient(api.app)
        with sqlite3.connect(self.db) as c:c.execute('UPDATE interviews SET warning_limit=0')
        with patch('api.save_events',side_effect=lambda token,events:save_events(token,events,self.db)),patch('api.get_session',side_effect=lambda token:session_state(token,self.db)):
            response=client.post('/api/interview/events',data={'token':self.token,'events':json.dumps([self.event_warning(1)])})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['proctoring']['terminated'])
        with patch('api.report_source',side_effect=lambda sid:report_source(sid,self.db)),patch('api.warning_snapshot',side_effect=lambda sid,wid:warning_snapshot(sid,wid,self.db)):
            image=client.get('/api/reports/warning-snapshot',params={'session_id':self.session,'warning_id':'warning-1'})
            self.assertEqual(image.status_code,200)
            self.assertEqual(image.headers['content-type'],'image/jpeg')
            self.assertEqual(image.headers['cache-control'],'private, no-store')
            missing=client.get('/api/reports/warning-snapshot',params={'session_id':self.session,'warning_id':'missing'})
            self.assertEqual(missing.status_code,400)
