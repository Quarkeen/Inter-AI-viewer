import json
import sqlite3
from datetime import timedelta
from unittest.mock import Mock, patch

from test_monitoring import MonitoringFixture
from database import init_database
from interview_agent import _advance
from interview_reports import TOPIC_DIMENSIONS, answer_sentences, generate_report, get_report, report_source, source_hash
from interview_store import get_session_data, get_turns, save_plan, save_question
from interview_templates import list_templates, read_config, save_template, snapshot, validate_content


def question(identity, text=None, **values):
    return dict(id=identity, kind='must_ask', question=text or f'Explain Python topic {identity}.', topic='Python',
                expected_points='Demonstrate the relevant mechanism and a valid alternative.',
                example_answer='Private benchmark example.', followup_guidance='Probe their own implementation.', **values)


class CustomTemplateTests(MonitoringFixture):
    def configure(self, questions, *, mode='custom', main=None, follow=0, topics=None):
        main = len(questions) if main is None else main
        saved = save_template('Custom technical', main, follow, self.db, mode=mode, topics=topics, questions=questions)
        with sqlite3.connect(self.db) as c:
            frozen = snapshot(c, saved['template_id'], saved['version'])
            c.execute('UPDATE interviews SET template_json=?', (frozen,))
            c.execute('UPDATE interview_sessions SET template_json=?', (frozen,))
        return saved

    def answer(self, text='I use Python generators for lazy iteration and explain how yield suspends execution.'):
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE interview_turns SET answer_text=?,answered_at=CURRENT_TIMESTAMP WHERE session_id=? AND answer_text IS NULL", (text, self.session))

    def advance(self, model=None, retrieve=None):
        with patch('interview_agent.utc_now', return_value=self.start):
            return _advance(self.session, model, self.db, self.db.with_name('graph.db'), retrieve or Mock(side_effect=AssertionError('No retrieval expected')))

    def test_validation_rejects_invalid_modes_duplicates_and_incomplete_mandatory_guides(self):
        base = question('q1')
        cases = [
            (1, dict(mode='unknown')),
            (1, dict(mode='hybrid')),
            (1, dict(mode='ai', questions=[base])),
            (2, dict(mode='custom', questions=[base])),
            (1, dict(mode='hybrid', questions=[base, question('q2')])),
            (1, dict(mode='custom', questions=[base | {'expected_points': ' '} ])),
            (2, dict(mode='custom', questions=[base, question('q1', 'Another question.')])),
            (2, dict(mode='custom', questions=[base, question('q2', base['question'].upper())])),
            (1, dict(mode='custom', questions=[base | {'question': 'word ' * 56}])),
            (1, dict(mode='custom', questions=[base | {'question': '???'}])),
            (1, dict(mode='custom', questions=[base | {'extra': True}])),
            (1, dict(mode='custom', questions=[base | {'id': 'bad:id'}])),
            (1, dict(mode='custom', questions=[base | {'example_answer': 'x' * 2001}])),
            (1, dict(topics=['SQL', 'sql'])),
            (1, dict(topics=[' '])),
            (1, dict(questions='not a list')),
        ]
        for main, content in cases:
            with self.subTest(content=content), self.assertRaises(ValueError):
                validate_content(main, **content)

    def test_snapshot_freezes_all_content_and_legacy_snapshots_keep_their_shape(self):
        first = self.configure([question('q1')])
        frozen = get_session_data(self.session, self.db)['template_json']
        save_template('Changed', 1, 0, self.db, template_id=first['template_id'], expected_version=1,
                      mode='custom', questions=[question('q1') | {'question': 'A changed question.', 'expected_points': 'Changed guide.'}])
        self.assertEqual(get_session_data(self.session, self.db)['template_json'], frozen)
        self.assertEqual(read_config(frozen)['questions'][0]['expected_points'], question('q1')['expected_points'])
        self.assertEqual([item['version'] for item in list_templates(self.db, first['template_id'])], [2, 1])
        legacy = dict(template_id='legacy', version=1, name='Legacy', main_questions=6, max_followups=4)
        self.assertEqual(read_config(json.dumps(legacy)), legacy)
        init_database(self.db)
        self.assertEqual(get_session_data(self.session, self.db)['template_json'], frozen)

    def test_custom_graph_asks_exact_questions_in_order_without_a_model_or_retrieval(self):
        approved = [question('one', 'Explain Python generators.'), question('two', 'Explain transactions.')]
        self.configure(approved)
        model = Mock(side_effect=AssertionError('No model expected'))
        self.advance(model)
        self.assertEqual(get_turns(self.session, self.db)[-1]['question'], approved[0]['question'])
        self.answer(); self.advance(model)
        self.assertEqual(get_turns(self.session, self.db)[-1]['question'], approved[1]['question'])
        self.answer(); self.advance(model)
        self.assertEqual(get_session_data(self.session, self.db)['status'], 'completed')
        model.assert_not_called()

    def test_all_twenty_custom_questions_fit_and_can_be_saved(self):
        approved = [question(f'q{number}') for number in range(1, 21)]
        self.configure(approved)
        for number in range(20):
            self.advance()
            turns = get_turns(self.session, self.db)
            self.assertEqual(turns[-1]['question'], approved[number]['question'])
            self.answer()
        self.advance()
        self.assertEqual(len(get_turns(self.session, self.db)), 20)
        self.assertEqual(get_session_data(self.session, self.db)['status'], 'completed')

    def test_hybrid_plan_keeps_mandatory_questions_and_sends_no_answer_guides_to_generator(self):
        sample = question('sample', 'Explain database indexing.') | {'kind': 'sample'}
        approved = [question('one'), question('two'), sample]
        self.configure(approved, mode='hybrid', main=4, topics=['SQL'])
        retrieve = Mock(return_value=[{'id': 'R1', 'source': 'resume', 'text': 'Built Python APIs'}])
        captured = []
        def planner(_generate, _system, payload, _schema):
            captured.append(payload)
            return {'questions': [{'question': 'Explain database indexing.', 'source_ids': ['R1']}, {'question': 'Explain unit testing.', 'source_ids': ['R1']}]}
        with patch('interview_agent._call_model', side_effect=planner):
            self.advance(retrieve=retrieve)
            self.answer(); self.advance(retrieve=retrieve)
        plan = json.loads(get_session_data(self.session, self.db)['plan_json'])
        self.assertEqual([item['question'] for item in plan[:2]], [item['question'] for item in approved[:2]])
        self.assertEqual(plan[2]['template_question_id'], 'sample')
        self.assertEqual(len(captured), 1)  # Follow-ups are disabled for this template.
        self.assertEqual(captured[0]['topics_to_cover'], ['SQL'])
        self.assertNotIn('expected_points', json.dumps(captured))
        self.assertNotIn('Private benchmark', json.dumps(captured))
        self.assertEqual(get_turns(self.session, self.db)[-1]['main_number'], 2)

    def test_generated_questions_cannot_duplicate_a_mandatory_question(self):
        approved = question('one')
        self.configure([approved], mode='hybrid', main=2)
        retrieve = lambda *args: [{'id': 'R1', 'source': 'resume', 'text': 'Built Python APIs'}]
        with patch('interview_agent._call_model', return_value={'questions': [{'question': approved['question'], 'source_ids': ['R1']}]}) as model:
            with self.assertRaisesRegex(ValueError, 'repeated'):
                self.advance(retrieve=retrieve)
        self.assertEqual(get_turns(self.session, self.db), [])
        self.assertIsNone(get_session_data(self.session, self.db)['plan_json'])

    def test_store_rejects_rewritten_plan_wrong_order_and_early_followups(self):
        approved = [question('one'), question('two')]
        self.configure(approved, follow=2)
        wrong = [{'question': 'Rewritten.', 'source_ids': ['HR:one'], 'template_question_id': 'one'},
                 {'question': approved[1]['question'], 'source_ids': ['HR:two'], 'template_question_id': 'two'}]
        with self.assertRaisesRegex(ValueError, 'wording'):
            save_plan(self.session, wrong, self.db)
        self.advance()
        self.answer()
        with self.assertRaisesRegex(ValueError, 'order'):
            save_question(self.session, dict(kind='main', main_number=2, question='Replacement.', source_ids=['HR:two']), self.db)
        with sqlite3.connect(self.db) as c:
            c.execute('UPDATE interview_sessions SET slot_end=?', ((self.start + timedelta(seconds=150)).isoformat(timespec='seconds'),))
        with self.assertRaisesRegex(ValueError, 'Reserve time'):
            save_question(self.session, dict(kind='follow_up', main_number=1, question='Early follow-up?', source_ids=['HR:one']), self.db)

    def test_custom_followups_use_guidance_without_receiving_expected_answers_and_keep_the_cap(self):
        self.configure([question('one')], follow=2)
        self.advance(); self.answer()
        retrieve = lambda *args: [{'id': 'R1', 'source': 'resume', 'text': 'Built Python APIs'}]
        captures = []
        def probe(_generate, _system, payload, _schema):
            captures.append(payload)
            return dict(action='follow_up', question=f'How did you test implementation {len(captures)}?', source_ids=['HR:one'], summary='The candidate described their implementation.')
        with patch('interview_agent._call_model', side_effect=probe):
            self.advance(retrieve=retrieve); self.answer()
            self.advance(retrieve=retrieve); self.answer()
            self.advance(retrieve=retrieve)
        self.assertEqual([item['kind'] for item in get_turns(self.session, self.db)], ['main', 'follow_up', 'follow_up'])
        self.assertEqual(get_session_data(self.session, self.db)['status'], 'completed')
        self.assertEqual(captures[0]['followup_guidance'], question('one')['followup_guidance'])
        self.assertNotIn('expected_points', json.dumps(captures))
        self.assertNotIn('Private benchmark', json.dumps(captures))

    def test_followup_budget_can_be_used_across_multiple_mandatory_questions(self):
        self.configure([question('one'), question('two')], follow=4)
        self.advance()
        retrieve = lambda *args: [{'id': 'R1', 'source': 'resume', 'text': 'Built Python APIs'}]
        counter = []
        def probe(_generate, _system, payload, _schema):
            counter.append(payload)
            return dict(action='follow_up', question=f'Explain implementation detail {len(counter)}.', source_ids=['R1'], summary='The candidate explained the implementation.')
        with patch('interview_agent._call_model', side_effect=probe):
            for _ in range(6):
                self.answer(); self.advance(retrieve=retrieve)
        self.assertEqual([turn['kind'] for turn in get_turns(self.session, self.db)], ['main', 'follow_up', 'follow_up', 'main', 'follow_up', 'follow_up'])
        self.assertEqual(get_session_data(self.session, self.db)['status'], 'completed')
        self.assertEqual(len(counter), 4)

    def test_short_remaining_time_skips_probes_and_advances_to_the_next_mandatory_question(self):
        self.configure([question('one'), question('two')], follow=2)
        self.advance(); self.answer()
        with sqlite3.connect(self.db) as c:
            c.execute('UPDATE interview_sessions SET slot_end=?', ((self.start + timedelta(seconds=150)).isoformat(timespec='seconds'),))
        self.advance()
        self.assertEqual([turn['kind'] for turn in get_turns(self.session, self.db)], ['main', 'main'])
        self.assertEqual(get_turns(self.session, self.db)[-1]['question'], question('two')['question'])

    def test_candidate_state_before_and_after_start_excludes_plans_guides_and_future_questions(self):
        import api
        from fastapi.testclient import TestClient
        self.configure([question('one'), question('two')])
        self.advance()
        client = TestClient(api.app)
        with patch('api.get_session', side_effect=lambda token: __import__('interview_store').get_session(token, self.db)), \
             patch('api.get_turns', side_effect=lambda session: get_turns(session, self.db)), \
             patch('api.get_invitation_access', return_value={'state': 'available'}):
            response = client.get('/api/interview/state', params={'token': self.token})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        serialized = json.dumps(body)
        for private in ('template_json', 'plan_json', 'decision_json', 'summary', 'expected_points', 'example_answer', 'followup_guidance', question('two')['question']):
            self.assertNotIn(private, serialized)
        self.assertEqual(body['config']['main_questions'], 2)
        with patch('api.get_session', return_value=None), patch('api.get_invitation_access', return_value={'state': 'available'}), patch('database.DATABASE_PATH', self.db):
            response = client.get('/api/interview/state', params={'token': self.token})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('questions', response.json()['config'])
        self.assertNotIn('Private benchmark', response.text)

    def test_guided_report_preserves_formula_and_marks_unasked_mandatory_questions_unassessed(self):
        self.configure([question('one'), question('two')])
        self.advance(); self.answer()
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE interview_sessions SET status='expired',ended_at=CURRENT_TIMESTAMP WHERE session_id=?", (self.session,))
        captures = []
        def judge(system, documents, schema):
            payload = json.loads(documents); captures.append(payload)
            ref = payload['topics'][0]['turns'][0]['sentences'][0]['id']
            rating = dict(rating=3, explanation='A valid implementation is explained.', sentence_ids=[ref])
            if 'topics' in schema['properties']:
                self.assertIn('Accept technically valid alternative', system)
                self.assertEqual(payload['topics'][0]['assessment_guidance']['expected_points'], question('one')['expected_points'])
                return json.dumps({'topics': {'topic_1': {'dimensions': {key: rating for key in TOPIC_DIMENSIONS}}}})
            return json.dumps(dict(role_alignment=rating, summary='One topic assessed.', strengths=[], gaps=[], next_round_questions=[]))
        with patch('interview_reports._input_tokens', return_value=100):
            identity = generate_report(self.session, judge, 'fixture', self.db)
        saved = get_report(identity, database_path=self.db)
        self.assertEqual(saved['assessment']['overall_score'], 75)
        self.assertEqual(saved['assessment']['mandatory_coverage'][1]['status'], 'not_assessed')
        self.assertEqual(saved['assessment']['coverage'], 1)
        self.assertEqual(saved['assessment']['main_question_count'], 2)
        self.assertEqual(saved['source']['question_guides']['1']['id'], 'one')
        sentences, topics = answer_sentences(saved['source'])
        self.assertFalse(any('Private benchmark' in item['text'] for item in sentences.values()))
        self.assertNotIn(2, topics)
        old_hash = source_hash(saved['source'])
        changed = json.loads(json.dumps(saved['source']))
        changed['question_guides']['1']['expected_points'] = 'Altered guide'
        self.assertNotEqual(source_hash(changed), old_hash)

    def test_sample_guide_is_applied_only_when_that_exact_question_was_selected(self):
        sample = question('sample', 'Explain generators.') | {'kind': 'sample'}
        self.configure([sample], mode='ai', main=2)
        retrieve = lambda *args: [{'id': 'R1', 'source': 'resume', 'text': 'Built Python APIs'}]
        with patch('interview_agent._call_model', return_value={'questions': [{'question': sample['question'], 'source_ids': ['R1']}, {'question': 'Explain testing.', 'source_ids': ['R1']}] }):
            self.advance(retrieve=retrieve)
        self.answer()
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE interview_sessions SET status='expired',ended_at=CURRENT_TIMESTAMP WHERE session_id=?", (self.session,))
        source = report_source(self.session, self.db)
        self.assertEqual(set(source['question_guides']), {'1'})
        self.assertEqual(source['question_guides']['1']['id'], 'sample')

    def test_http_rejects_bad_content_and_persists_valid_question_guidance(self):
        import api
        from fastapi.testclient import TestClient
        client = TestClient(api.app)
        body = dict(name='Custom', mode='custom', main_questions=1, max_followups=0, topics=[], questions=[question('one')])
        with patch('api.save_template', side_effect=lambda *args, **kwargs: save_template(*args, database_path=self.db, **kwargs)):
            for change in ({'questions': [question('one') | {'expected_points': ''}]}, {'mode': 'ai'}, {'extra': True}, {'questions': [question('one') | {'kind': 'invalid'}]}):
                with self.subTest(change=change):
                    self.assertIn(client.post('/api/interview-templates', json=body | change).status_code, (400, 422))
            response = client.post('/api/interview-templates', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['questions'][0]['expected_points'], question('one')['expected_points'])
        self.assertEqual(list_templates(self.db)[0]['questions'][0]['question'], question('one')['question'])
