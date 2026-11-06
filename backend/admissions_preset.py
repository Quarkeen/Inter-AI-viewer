"""Version-one admissions question bank, installed once without replacing edits."""
import json
import sqlite3
from contextlib import closing

from database import DATABASE_PATH

TEMPLATE_ID = 'iiit-dharwad-mtech-dsai-foundations'
TEMPLATE_NAME = 'M.Tech DSAI Admissions - Foundations'
RECOMMENDED_DURATION_MINUTES = 45
DESCRIPTION = ('Proposed readiness interview for IIIT Dharwad\'s two-year online M.Tech DSAI programme. '
               'Assess foundations and reasoning; faculty should approve this draft before live admissions use.')

# The questions are proposed assessment content, not an official institute syllabus.
QUESTIONS = [
    dict(id='dsai-background', topic='Background and motivation',
         question='Briefly introduce your academic or professional background. What interests you about studying Data Science and AI, and what would you like to learn through this programme?',
         expected_points='Relevant academic or professional background; a specific learning interest; a reasonable connection to future goals. Accept fresh graduates and independent learners. Do not infer technical ability from a polished introduction, accent, confidence or institution prestige.',
         example_answer='My coursework introduced me to programming and statistics. A small data-analysis assignment made me interested in how models generalise. I want to strengthen my mathematical understanding and learn to investigate problems through structured experiments.',
         followup_guidance='Ask about one learning interest only if the answer is unclear. Do not turn personal background or professional employment into an eligibility gate.'),
    dict(id='dsai-project', topic='Project understanding',
         question='Describe a project, assignment or problem you worked on. What was your own contribution, how did you check your results, and what would you improve?',
         expected_points='A clear problem, personal contribution, understandable approach, some validation, and limitations or lessons. Coursework, independent study and non-ML projects are acceptable. Do not require employment, publications or impressive numerical results.',
         example_answer='For a coursework analysis I cleaned a dataset, wrote summaries and checked calculations against small hand-worked examples. I contributed the cleaning and validation code. I would improve the treatment of missing data and test whether the findings hold on another dataset.',
         followup_guidance='Probe one stated contribution or validation step. Ask for an explanation rather than treating a resume claim as verified authorship.'),
    dict(id='dsai-programming', topic='Programming fundamentals',
         question='How would you calculate the average of numerical readings when some entries are missing? Explain your algorithm using any programming language or pseudocode.',
         expected_points='Identify valid values; retain legitimate zeros; accumulate sum and valid count; divide by the valid count; explicitly handle an empty valid dataset. Exact language syntax is not required. For [0, 2, missing, 4], the average is 2.',
         example_answer='I would scan the readings, skip only entries defined as missing, and add each valid reading to a sum while increasing a count. Zero is valid. If the count is zero, I return a documented missing result or error; otherwise I return sum divided by count.',
         followup_guidance='If necessary, ask what happens with a zero reading or no valid readings. Do not supply the algorithm or insist on Python syntax.'),
    dict(id='dsai-algorithms', topic='Algorithms and efficiency',
         question='How would you find the largest number in a very large list without sorting it? Explain your approach and the important edge cases.',
         expected_points='A one-pass running maximum; linear time and constant additional space. Handle empty input and negative values sensibly. Initialising a maximum to zero is incorrect for an all-negative list unless separately handled.',
         example_answer='After checking for an empty list, I initialise the maximum from its first value. I compare each remaining value and replace the maximum when necessary. This uses one pass and one stored maximum, so time is O(n) and additional space is O(1).',
         followup_guidance='Ask about an all-negative list or why sorting is unnecessary. Accept correct explanations without formal complexity notation.'),
    dict(id='dsai-linear-algebra', topic='Linear algebra',
         question='A simple prediction is calculated as a dot product between a feature vector and a weight vector. What does this calculation mean, and why must their dimensions match?',
         expected_points='Multiply corresponding feature and weight entries and sum the products; each weight controls a feature contribution; vectors need matching dimensions; the dot product is a scalar. A correct numerical example is sufficient. Do not require advanced matrix decompositions.',
         example_answer='For features [2, 3] and weights [0.5, 1], the dot product is 2 times 0.5 plus 3 times 1, which is 4. Each feature has a corresponding weight, so their lengths must match. The result is one weighted score.',
         followup_guidance='Ask for a small numerical example or clarification of feature and weight dimensions. Do not demand unasked advanced linear algebra.'),
    dict(id='dsai-probability', topic='Conditional probability',
         question='Among 100 students, 40 know Python, 30 know SQL, and 10 know both. If a randomly selected student knows SQL, what is the probability that they also know Python? Explain your reasoning.',
         expected_points='Restrict the relevant group to the 30 SQL users. Ten also know Python, so the conditional probability is 10/30 = 1/3. Distinguish this from the joint probability 10/100. Assess reasoning separately from a minor arithmetic slip.',
         example_answer='We are choosing from students who know SQL, so there are 30 relevant students. Of those, 10 also know Python. The probability is therefore 10 divided by 30, or one third. The total of 100 is not the conditional denominator.',
         followup_guidance='Ask the candidate to explain the denominator if needed. Do not require a memorised formula or suggest the correct denominator.'),
    dict(id='dsai-statistics', topic='Statistics and outliers',
         question='For the values 10, 10, 10, 10 and 100, what are the mean and median? Which would you use to describe a typical observation, and why?',
         expected_points='The mean is 28 and the median is 10. The large observation pulls the mean upward. The median describes the main cluster reasonably; the appropriate statistic depends on the purpose. Do not insist that the median is always preferable.',
         example_answer='The sum is 140, so the mean is 28. The middle sorted value is 10, so the median is 10. I would use the median to describe the cluster here, while still reporting the large value and checking whether it is valid.',
         followup_guidance='Ask how a larger outlier would affect each measure, or whether it should automatically be removed. Accept context-sensitive reasoning.'),
    dict(id='dsai-optimisation', topic='Calculus and optimisation',
         question='Consider the loss function L(w) = (w - 3)^2. Where is its minimum? Explain how a gradient-based method could move towards it when starting at w = 0.',
         expected_points='The minimum is at w = 3. The derivative is 2(w - 3), which is negative at zero. Subtracting a positive learning rate times that gradient increases w towards the minimum. Accept an accurate conceptual or graphical explanation; avoid rewarding formula recitation without understanding.',
         example_answer='The squared term is smallest when w equals 3. At zero the derivative is -6, so a gradient-descent update w minus learning-rate times derivative moves w to a positive value. A suitable step size lets repeated updates approach the minimum.',
         followup_guidance='If the core reasoning is sound, ask how an excessively large learning rate could affect convergence. Do not require optimiser implementation details.'),
    dict(id='dsai-generalisation', topic='Learning and generalisation',
         question='A predictive model works very well on its training data but poorly on new data. What might explain this, and how would you investigate it?',
         expected_points='Recognise possible overfitting; evaluate on genuinely unseen data; consider leakage or a change in data distribution; suggest a simpler baseline, better validation or another justified improvement. Introductory conceptual reasoning is sufficient; specific libraries and advanced architectures are not required.',
         example_answer='The model may have learned training-specific patterns that do not generalise. I would check the split and preprocessing for leakage, compare training and held-out performance, and try a simpler baseline. I would also check whether the new data differ substantially from the training data.',
         followup_guidance='Probe one proposed diagnostic or validation step. Do not demand advanced ML vocabulary if the candidate explains the underlying idea correctly.'),
    dict(id='dsai-responsible-research', topic='Responsible research and data use',
         question='You receive a dataset containing personal information for a research project. What would you check before using it, and how would you make your results trustworthy?',
         expected_points='Check permission and appropriate purpose; protect identities and minimise unnecessary personal data; document methods; check data quality; report limitations honestly; avoid fabricated or unsupported conclusions. Accept practical, sensible reasoning without legal terminology or publication experience.',
         example_answer='I would confirm that I have permission to use the data for this purpose, restrict access and avoid retaining unnecessary identifiers. I would document cleaning and analysis decisions, check the results and clearly report limitations instead of inventing findings or unsupported causal claims.',
         followup_guidance='Ask for one concrete privacy measure or one way to make the analysis reproducible. Do not request personal or sensitive information from the candidate.'),
]


def preset_metadata(template_id):
    if template_id != TEMPLATE_ID:
        return {}
    return dict(is_preset=True, recommended_duration_minutes=RECOMMENDED_DURATION_MINUTES, preset_description=DESCRIPTION)


def ensure_admissions_template(database_path=DATABASE_PATH):
    """Insert once; an existing preset or HR-created revision is never overwritten."""
    from interview_templates import validate_content, validate_counts
    validate_counts(10, 4)
    content = validate_content(10, mode='custom', topics=[], questions=[dict(item, kind='must_ask') for item in QUESTIONS])
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        if connection.execute('SELECT 1 FROM interview_templates WHERE template_id=? LIMIT 1', (TEMPLATE_ID,)).fetchone():
            return False
        connection.execute('''INSERT INTO interview_templates(template_id,version,name,main_questions,max_followups,config_json)
            VALUES(?,1,?,10,4,?)''', (TEMPLATE_ID, TEMPLATE_NAME, json.dumps(content)))
    return True
