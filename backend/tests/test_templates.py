import json
import sqlite3
from contextlib import ExitStack
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch
from PIL import Image
from test_monitoring import MonitoringFixture, audio
from interview_templates import save_template, list_templates, read_config, validate_counts, snapshot
from scheduling import schedule_interview, claim_invitation, finish_invitation, reschedule_interview
from interview_store import start_session, get_session_data, save_question
from preflight import issue_receipt
from database import init_database


class TemplateTests(MonitoringFixture):
    def test_versions_are_immutable_and_stale_edit_rejected(self):
        one=save_template('Python',3,0,self.db)
        two=save_template('Python technical',4,2,self.db,template_id=one['template_id'],expected_version=1)
        self.assertEqual(two['version'],2)
        self.assertEqual([v['main_questions'] for v in list_templates(self.db,one['template_id'])],[4,3])
        self.assertEqual(list_templates(self.db)[0]['version'],2)
        with self.assertRaisesRegex(ValueError,'changed'):
            save_template('Stale edit',2,0,self.db,template_id=one['template_id'],expected_version=1)

    def test_previous_session_rename_migration_repairs_child_references_without_data_loss(self):
        from interview_store import save_events, get_events
        save_events(self.token,[self.event()],self.db)
        before=get_events(self.session,self.db)
        with sqlite3.connect(self.db) as c:
            c.execute('PRAGMA foreign_keys=OFF')
            c.execute('PRAGMA legacy_alter_table=OFF')
            c.execute('ALTER TABLE interview_sessions RENAME TO interview_sessions_legacy')
            c.execute('PRAGMA legacy_alter_table=ON')
            c.execute('ALTER TABLE interview_sessions_legacy RENAME TO interview_sessions')
            self.assertTrue(c.execute('PRAGMA foreign_key_check').fetchall())
        init_database(self.db)
        init_database(self.db)
        self.assertEqual(get_events(self.session,self.db),before)
        with sqlite3.connect(self.db) as c:self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())

    def test_twenty_question_budget_and_migration_preserve_versions(self):
        saved=save_template('Twenty questions',20,0,self.db)
        self.assertEqual(saved['main_questions'],20)
        self.assertEqual(save_template('Mixed',10,10,self.db)['max_followups'],10)
        with sqlite3.connect(self.db) as c:
            c.execute("DELETE FROM interview_templates WHERE main_questions+max_followups>10")
            c.execute('DROP TABLE interview_templates')
            c.execute("CREATE TABLE interview_templates(template_id TEXT,version INTEGER,name TEXT,main_questions INTEGER CHECK(main_questions BETWEEN 1 AND 10),max_followups INTEGER CHECK(max_followups BETWEEN 0 AND 8),created_at TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(template_id,version),CHECK(main_questions+max_followups<=10),CHECK(max_followups<=2*main_questions))")
            c.execute("INSERT INTO interview_templates(template_id,version,name,main_questions,max_followups) VALUES('existing',1,'Existing',6,4)")
        old={'template_id':'existing'}
        init_database(self.db)
        init_database(self.db)
        self.assertEqual(list_templates(self.db,old['template_id'])[0]['version'],1)
        save_template('New budget',20,0,self.db)

    def test_limits_and_unknown_versions_are_rejected(self):
        for counts in ((0,0),(21,0),(15,6),(1,3),(True,0),(1,False),(2.5,0),(3,-1)):
            with self.subTest(counts=counts),self.assertRaises(ValueError):validate_counts(*counts)
        with sqlite3.connect(self.db) as c:
            with self.assertRaises(ValueError):snapshot(c,'missing',1)
        self.assertEqual(read_config()['main_questions'],6)
        self.assertEqual(read_config()['max_followups'],4)

    def configured_session(self,main=1,follow=0,**content):
        template=save_template('Short technical',main,follow,self.db,**content)
        # Replace only this fixture's original interview; no real data is used.
        with sqlite3.connect(self.db) as c:
            for table in ('interview_events','interview_preflight','interview_sessions','interviews'):c.execute('DELETE FROM '+table)
        slot=self.start+timedelta(hours=1)
        interview=schedule_interview(self.run,self.app1,slot,30,self.db,template_id=template['template_id'],template_version=1)
        save_template('Long technical',6,4,self.db,template_id=template['template_id'],expected_version=1)
        invitation=claim_invitation(interview,'https://example.com',self.db)
        self.assertEqual(read_config(invitation['template_json'])['version'],1)
        self.assertEqual(read_config(invitation['template_json'])['main_questions'],main)
        finish_invitation(interview,'sent',database_path=self.db)
        reschedule_interview(interview,slot+timedelta(hours=1),30,self.db)
        with sqlite3.connect(self.db) as c:self.assertEqual(read_config(c.execute('SELECT template_json FROM interviews').fetchone()[0])['version'],1)
        invitation=claim_invitation(interview,'https://example.com',self.db)
        finish_invitation(interview,'sent',database_path=self.db)
        token=invitation['interview_url'].split('token=')[1]
        frame=BytesIO();Image.new('RGB',(320,240),'white').save(frame,format='JPEG')
        checks={'policy_version':'setup-v1','face_count':1,'screen_surface':'monitor','speaker_confirmed':True,'playback_confirmed':True,'rules_accepted':True}
        with ExitStack() as stack:
            for module in ('scheduling','interview_store','preflight'):stack.enter_context(patch(module+'.utc_now',return_value=slot+timedelta(hours=1)))
            receipt=issue_receipt(token,checks,frame.getvalue(),audio(),self.db)['receipt']
            session=start_session(token,self.db,preflight_receipt=receipt)
        return session

    def test_snapshot_survives_edits_send_reschedule_and_session_start(self):
        session=self.configured_session(3,0)
        self.assertEqual(read_config(get_session_data(session,self.db)['template_json'])['main_questions'],3)
        init_database(self.db)
        self.assertEqual(read_config(get_session_data(session,self.db)['template_json'])['main_questions'],3)

    def test_custom_content_is_pinned_through_invitation_and_rescheduling(self):
        item=dict(id='required-python',kind='must_ask',question='Explain Python generators.',topic='Python',expected_points='Lazy iteration and yield.',example_answer='A private example.',followup_guidance='Probe iterator state.')
        session=self.configured_session(1,0,mode='custom',questions=[item])
        frozen=read_config(get_session_data(session,self.db)['template_json'])
        self.assertEqual(frozen['mode'],'custom')
        self.assertEqual(frozen['questions'],[item])
        self.assertEqual(frozen['version'],1)
        init_database(self.db)
        self.assertEqual(read_config(get_session_data(session,self.db)['template_json']),frozen)

    def test_one_main_no_followups_graph_finishes_and_report_uses_one_topic(self):
        from interview_agent import _advance
        from interview_reports import generate_report,get_report
        from report_pdf import export_report_pdf
        from pypdf import PdfReader
        session=self.configured_session()
        checkpoint=self.db.with_name('graph.db')
        calls=[]
        def question_model(_generate,_system,payload,_schema):
            calls.append(payload)
            return {'questions':[{'question':'Explain Python APIs.','source_ids':['R1']}]}
        retrieve=lambda *args:[{'id':'R1','source':'resume','text':'Built Python APIs'}]
        with patch('interview_agent._call_model',side_effect=question_model):
            _advance(session,None,self.db,checkpoint,retrieve)
            with sqlite3.connect(self.db) as c:c.execute("UPDATE interview_turns SET answer_text='I built Python API routes and tested them.' WHERE session_id=?",(session,))
            with self.assertRaises(ValueError):save_question(session,{'kind':'main','main_number':2,'question':'Second','source_ids':['R1']},self.db)
            with self.assertRaises(ValueError):save_question(session,{'kind':'follow_up','main_number':1,'question':'Follow up','source_ids':['R1']},self.db)
            _advance(session,None,self.db,checkpoint,retrieve)
        self.assertEqual(len(calls),1)
        self.assertEqual(get_session_data(session,self.db)['status'],'completed')
        def judge(system,documents,schema):
            payload=json.loads(documents)
            ref=payload['topics'][0]['turns'][0]['sentences'][0]['id']
            rating={'rating':3,'explanation':'Relevant fixture evidence.','sentence_ids':[ref]}
            if 'topics' in schema['properties']:
                from interview_reports import TOPIC_DIMENSIONS
                return json.dumps({'topics':{'topic_1':{'dimensions':{key:rating for key in TOPIC_DIMENSIONS}}}})
            return json.dumps({'role_alignment':rating,'summary':'Fixture summary.','strengths':[],'gaps':[],'next_round_questions':[]})
        with patch('interview_reports._input_tokens',return_value=100):
            report_id=generate_report(session,judge,'fixture',self.db)
        report=get_report(report_id,database_path=self.db)
        self.assertEqual(report['assessment']['main_question_count'],1)
        self.assertEqual(report['assessment']['coverage'],1)
        pdf=PdfReader(BytesIO(export_report_pdf(report_id,self.db)))
        self.assertEqual(len(pdf.pages),3)
        self.assertIn('1/1 topics assessed',pdf.pages[0].extract_text())

    def test_twenty_answer_assessment_includes_all_topics(self):
        from interview_reports import generate_report,get_report,TOPIC_DIMENSIONS
        session=self.configured_session(20,0)
        with sqlite3.connect(self.db) as c:
            for number in range(1,21):
                c.execute("INSERT INTO interview_turns(session_id,sequence,kind,main_number,question,source_ids_json,answer_text) VALUES(?,?,'main',?,?,'[]',?)",(session,number,number,f'Explain API topic {number}.',f'I validate API request {number} before writing database records.'))
            c.execute("UPDATE interview_sessions SET status='completed',ended_at=CURRENT_TIMESTAMP WHERE session_id=?",(session,))
        calls=[]
        def estimate(prompt,payload,schema):
            return 7000 if 'Assess role alignment ONCE' in prompt and 'context_note' not in payload else 100
        def judge(prompt,documents,schema):
            payload=json.loads(documents);calls.append(payload)
            def rate(topic):
                return {'rating':3,'explanation':'Supported implementation.','sentence_ids':[topic['turns'][0]['sentences'][0]['id']]}
            if 'topics' in schema['properties']:
                return json.dumps({'topics':{f"topic_{topic['main_number']}":{'dimensions':{name:rate(topic) for name in TOPIC_DIMENSIONS}} for topic in payload['topics']}})
            self.assertEqual(len(payload['topics']),20)
            return json.dumps({'role_alignment':rate(payload['topics'][0]),'summary':'Fixture summary.','strengths':[],'gaps':[],'next_round_questions':[]})
        with patch('interview_reports._input_tokens',side_effect=estimate):
            result=generate_report(session,judge,'fixture',self.db)
        saved=get_report(result,database_path=self.db)
        self.assertEqual(saved['assessment']['coverage'],20)
        self.assertEqual(saved['assessment']['main_question_count'],20)

    def test_larger_plans_receive_sufficient_output_budget(self):
        import api
        from interview_agent import PLAN_SCHEMA
        with patch('api._groq',return_value=('fixture-key','fixture-model')), patch('api.get_session',return_value={'session_id':'fixture','template_json':json.dumps(dict(main_questions=20,max_followups=0))}), patch('api.generate',return_value='fixture') as generate:
            api._interview_model('token')('system','documents',PLAN_SCHEMA)
            self.assertEqual(generate.call_args.kwargs['max_completion_tokens'],3600)

    def test_http_templates_reject_fractional_counts_and_stale_edits(self):
        from fastapi.testclient import TestClient
        import api
        client=TestClient(api.app)
        with patch('api.save_template',side_effect=lambda *a,**kw:save_template(*a,database_path=self.db,**kw)):
            self.assertEqual(client.post('/api/interview-templates',json={'name':'Bad','main_questions':1.5,'max_followups':0}).status_code,422)
            result=client.post('/api/interview-templates',json={'name':'Test','main_questions':2,'max_followups':0})
            self.assertEqual(result.status_code,200,result.text)
            identity=result.json()['template_id']
            body={'name':'Test v2','main_questions':3,'max_followups':0,'expected_version':1}
            self.assertEqual(client.post(f'/api/interview-templates/{identity}/versions',json=body).status_code,200)
            self.assertEqual(client.post(f'/api/interview-templates/{identity}/versions',json=body).status_code,400)
