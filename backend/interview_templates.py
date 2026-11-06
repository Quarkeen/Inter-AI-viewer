"""Validated, immutable interview templates and per-interview snapshots."""
import json
import re
import sqlite3
from contextlib import closing
from uuid import uuid4

from database import DATABASE_PATH
from admissions_preset import TEMPLATE_ID as ADMISSIONS_TEMPLATE_ID, preset_metadata


DEFAULT_CONFIG = {'template_id': None, 'version': 1, 'name': 'Standard technical interview', 'main_questions': 6, 'max_followups': 4}
CONTENT_KEYS = ('mode', 'topics', 'questions')
QUESTION_KEYS = {'id', 'kind', 'question', 'topic', 'expected_points', 'example_answer', 'followup_guidance'}


def validate_counts(main_questions, max_followups):
    if type(main_questions) is not int or not 1 <= main_questions <= 20:
        raise ValueError('Choose between 1 and 20 main questions.')
    if type(max_followups) is not int or not 0 <= max_followups <= 20:
        raise ValueError('Choose between 0 and 20 follow-up questions.')
    if main_questions + max_followups > 20:
        raise ValueError('Keep the total question budget at 20 or fewer.')
    if max_followups > 2 * main_questions:
        raise ValueError('Allow at most two follow-ups per main question.')


def question_key(text):
    return re.sub(r'\W', '', text).casefold()


def _text(value, label, limit, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f'{label} must contain {"1–" if required else "at most "}{limit} characters.')
    return value.strip()


def validate_content(main_questions, *, mode='ai', topics=None, questions=None):
    if mode not in ('ai', 'custom', 'hybrid'):
        raise ValueError('Choose AI-generated, custom, or hybrid interview mode.')
    topics = [] if topics is None else topics
    questions = [] if questions is None else questions
    if not isinstance(topics, list) or len(topics) > 20:
        raise ValueError('Choose at most 20 topics.')
    clean_topics = [_text(topic, 'Each topic', 80, True) for topic in topics]
    if len({topic.casefold() for topic in clean_topics}) != len(clean_topics):
        raise ValueError('Remove duplicate topics.')
    if not isinstance(questions, list) or len(questions) > 20:
        raise ValueError('Add at most 20 question entries.')
    clean_questions = []
    for index, item in enumerate(questions, 1):
        if not isinstance(item, dict) or set(item) - QUESTION_KEYS:
            raise ValueError(f'Question {index} has unsupported fields.')
        identity = item.get('id', uuid4().hex)
        if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', identity):
            raise ValueError(f'Question {index} has an invalid identifier.')
        kind = item.get('kind')
        if kind not in ('must_ask', 'sample'):
            raise ValueError(f'Choose Must ask or Sample for question {index}.')
        question = _text(item.get('question'), f'Question {index}', 500, True)
        if len(question.split()) > 55 or not question_key(question):
            raise ValueError(f'Keep question {index} to 55 words with meaningful question text.')
        clean_questions.append({
            'id': identity, 'kind': kind, 'question': question,
            'topic': _text(item.get('topic', ''), f'Question {index} topic', 80),
            'expected_points': _text(item.get('expected_points', ''), f'Question {index} expected key points', 1200, kind == 'must_ask'),
            'example_answer': _text(item.get('example_answer', ''), f'Question {index} example answer', 2000),
            'followup_guidance': _text(item.get('followup_guidance', ''), f'Question {index} follow-up guidance', 500),
        })
    if len({item['id'] for item in clean_questions}) != len(clean_questions):
        raise ValueError('Question identifiers must be unique.')
    if len({question_key(item['question']) for item in clean_questions}) != len(clean_questions):
        raise ValueError('Remove duplicate questions.')
    must_ask = [item for item in clean_questions if item['kind'] == 'must_ask']
    if len(must_ask) > main_questions:
        raise ValueError('Must-ask questions cannot exceed the main-question count.')
    if mode == 'ai' and must_ask:
        raise ValueError('Choose hybrid or custom mode to include must-ask questions.')
    if mode == 'custom' and (len(must_ask) != main_questions or len(clean_questions) != main_questions):
        raise ValueError('Custom mode needs exactly one must-ask entry for every main question, without sample questions.')
    if mode == 'hybrid' and not must_ask:
        raise ValueError('Add at least one must-ask question for a hybrid interview.')
    return {'mode': mode, 'topics': clean_topics, 'questions': clean_questions}


def read_config(raw=None):
    if raw is None:
        return dict(DEFAULT_CONFIG)
    try:
        value = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (TypeError, ValueError):
        raise ValueError('This interview template is unreadable.') from None
    if not isinstance(value, dict):
        raise ValueError('This interview template is unreadable.')
    validate_counts(value.get('main_questions'), value.get('max_followups'))
    content = validate_content(value['main_questions'], **{key: value[key] for key in CONTENT_KEYS if key in value})
    # Preserve legacy snapshot dictionaries and their existing report fingerprints.
    if any(key in value for key in CONTENT_KEYS):
        value.update(content)
    return value


def must_ask_questions(config):
    return [item for item in config.get('questions', []) if item['kind'] == 'must_ask']


def validate_required_plan(config, plan):
    required = must_ask_questions(config)
    if not required:
        return
    if not isinstance(plan, list) or len(plan) != config['main_questions']:
        raise ValueError('The interview plan does not match its assigned template.')
    for approved, planned in zip(required, plan):
        if (not isinstance(planned, dict) or planned.get('template_question_id') != approved['id'] or planned.get('question') != approved['question']
                or planned.get('source_ids') != [f"HR:{approved['id']}"]):
            raise ValueError('Must-ask questions must retain their approved wording and order.')


def public_config(config):
    """Candidate-safe counts only; never publish a question bank or answer guide."""
    return {key: config[key] for key in ('template_id', 'version', 'name', 'main_questions', 'max_followups') if key in config}


def _from_row(row):
    value = {key: row[key] for key in ('template_id', 'version', 'name', 'main_questions', 'max_followups')}
    if row['config_json']:
        value.update(json.loads(row['config_json']))
    if 'created_at' in row.keys():
        value['created_at'] = row['created_at']
    return read_config(value)


def snapshot(connection, template_id=None, version=None):
    if template_id is None:
        if version is not None:
            raise ValueError('Select a template with that version.')
        return json.dumps(DEFAULT_CONFIG)
    if type(version) is not int or version < 1:
        raise ValueError('Select a valid template version.')
    cursor = connection.execute('SELECT template_id,version,name,main_questions,max_followups,config_json FROM interview_templates WHERE template_id=? AND version=?', (template_id, version))
    row = cursor.fetchone()
    if row is None:
        raise ValueError('This interview template version is unavailable.')
    return json.dumps(_from_row(dict(zip([column[0] for column in cursor.description], row))))


def list_templates(database_path=DATABASE_PATH, template_id=None):
    with closing(sqlite3.connect(database_path)) as c:
        c.row_factory = sqlite3.Row
        if template_id:
            rows = c.execute('SELECT * FROM interview_templates WHERE template_id=? ORDER BY version DESC', (template_id,)).fetchall()
        else:
            rows = c.execute('SELECT t.* FROM interview_templates t WHERE version=(SELECT max(version) FROM interview_templates WHERE template_id=t.template_id) ORDER BY CASE WHEN t.template_id=? THEN 0 ELSE 1 END,name,template_id', (ADMISSIONS_TEMPLATE_ID,)).fetchall()
        return [_from_row(row) | preset_metadata(row['template_id']) for row in rows]


def save_template(name, main_questions, max_followups, database_path=DATABASE_PATH, *, template_id=None, expected_version=None,
                  mode='ai', topics=None, questions=None):
    name = _text(name, 'Template name', 80, True)
    validate_counts(main_questions, max_followups)
    content = validate_content(main_questions, mode=mode, topics=topics, questions=questions)
    with closing(sqlite3.connect(database_path)) as c, c:
        c.execute('BEGIN IMMEDIATE')
        version = 1
        if template_id:
            latest = c.execute('SELECT max(version) FROM interview_templates WHERE template_id=?', (template_id,)).fetchone()[0]
            if latest is None:
                raise ValueError('This template no longer exists.')
            if type(expected_version) is not int or latest != expected_version:
                raise ValueError('This template changed. Refresh before saving a new version.')
            version = latest + 1
        else:
            template_id = uuid4().hex
        c.execute('INSERT INTO interview_templates(template_id,version,name,main_questions,max_followups,config_json) VALUES(?,?,?,?,?,?)',
                  (template_id, version, name, main_questions, max_followups, json.dumps(content)))
    return dict(template_id=template_id, version=version, name=name, main_questions=main_questions, max_followups=max_followups, **content, **preset_metadata(template_id))
