import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import TypedDict
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from database import (
    DATABASE_PATH, get_evaluation_input, get_latest_resume_evaluation, save_resume_evaluation,
)
from screening import RUBRIC_VERSION, WEIGHTS, assess_resume, document_hashes, validate_assessment


CHECKPOINT_PATH = DATABASE_PATH.with_name("shortlist-workflows.db")


def _validate_threshold(threshold):
    if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 <= threshold <= 100:
        raise ValueError("Enter a threshold between 0 and 100.")


def meets_threshold(assessment, threshold):
    _validate_threshold(threshold)
    # A component's weighted points meet its weighted minimum iff its raw
    # 0–100 score meets the threshold. Weights remain private to the backend.
    return all(
        assessment["components"][name]["score"] >= threshold
        for name in WEIGHTS if assessment["components"][name]["applicable"]
    )


def start_run(job_id, threshold, database_path=DATABASE_PATH):
    _validate_threshold(threshold)
    run_id = uuid4().hex
    with closing(sqlite3.connect(database_path)) as connection, connection: 
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        ids = [row[0] for row in connection.execute(
            "SELECT application_id FROM applications WHERE job_id = ? ORDER BY application_id", (job_id,)
        )]
        if not ids:
            raise ValueError("There are no applications for this job yet.")
        connection.execute(
            """UPDATE shortlist_runs SET status = 'superseded'
               WHERE job_id = ? AND status IN ('evaluating', 'awaiting_approval')""", (job_id,)
        )
        connection.execute(
            """INSERT INTO shortlist_runs (run_id, job_id, threshold, application_ids_json, status)
               VALUES (?, ?, ?, ?, 'evaluating')""", (run_id, job_id, threshold, json.dumps(ids))
        )
    return run_id


def get_run(run_id=None, job_id=None, database_path=DATABASE_PATH):
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        if run_id is not None:
            row = connection.execute("SELECT * FROM shortlist_runs WHERE run_id = ?", (run_id,)).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM shortlist_runs WHERE job_id = ? ORDER BY rowid DESC LIMIT 1", (job_id,)
            ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["application_ids"] = json.loads(result.pop("application_ids_json"))
    return result


def get_run_items(run_id, database_path=DATABASE_PATH):
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """SELECT i.*, c.full_name, c.email, a.resume_filename, e.score,
                      e.assessment_json, e.evaluated_at
               FROM shortlist_items i
               JOIN applications a ON a.application_id = i.application_id
               JOIN candidates c ON c.candidate_id = a.candidate_id
               JOIN resume_evaluations e ON e.evaluation_id = i.evaluation_id
               WHERE i.run_id = ? ORDER BY i.proposed DESC, e.score DESC, i.application_id""", (run_id,)
        ).fetchall()
    items = [dict(row) for row in rows]
    for item in items:
        item["assessment"] = json.loads(item.pop("assessment_json"))
    return items


def _checked_evaluation(connection, application_id, evaluation_id, job_id):
    row = connection.execute(
        """SELECT e.resume_sha256, e.jd_sha256, e.rubric_version, e.assessment_json,
                  a.resume_text, a.job_title_at_application, a.jd_at_application,
                  (SELECT max(evaluation_id) FROM resume_evaluations WHERE application_id = a.application_id)
           FROM resume_evaluations e JOIN applications a ON a.application_id = e.application_id
           WHERE e.evaluation_id = ? AND a.application_id = ? AND a.job_id = ?""",
        (evaluation_id, application_id, job_id),
    ).fetchone()
    if not row or row[7] != evaluation_id or row[2] != RUBRIC_VERSION or (
        row[0], row[1]
    ) != document_hashes(row[4], row[5], row[6]):
        raise ValueError("An application or evaluation changed. Generate a new shortlist before approving it.")
    assessment = json.loads(row[3])
    validate_assessment(assessment, row[4], row[6])
    return assessment


def save_proposals(run_id, evaluations, database_path=DATABASE_PATH):
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        run = connection.execute(
            "SELECT job_id, threshold, application_ids_json, status FROM shortlist_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if not run or run[3] not in ("evaluating", "awaiting_approval"):
            raise ValueError("This shortlist is no longer active. Open the latest review.")
        ids = [item["application_id"] for item in evaluations]
        if len(ids) != len(set(ids)) or set(ids) != set(json.loads(run[2])):
            raise ValueError("The evaluations do not match this job's application list.")
        for item in evaluations:
            assessment = _checked_evaluation(connection, item["application_id"], item["evaluation_id"], run[0])
            connection.execute(
                """INSERT OR IGNORE INTO shortlist_items
                   (run_id, application_id, evaluation_id, proposed) VALUES (?, ?, ?, ?)""",
                (run_id, item["application_id"], item["evaluation_id"], int(meets_threshold(assessment, run[1]))),
            )
        connection.execute("UPDATE shortlist_runs SET status = 'awaiting_approval' WHERE run_id = ?", (run_id,))


def save_decisions(run_id, decisions, database_path=DATABASE_PATH):
    if not isinstance(decisions, list) or any(
        not isinstance(item, dict) or set(item) != {"application_id", "approved"}
        or type(item["application_id"]) is not int or type(item["approved"]) is not bool
        for item in decisions
    ):
        raise ValueError("Submit a decision for each application in this review.")
    choices = {item["application_id"]: item["approved"] for item in decisions}
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        run = connection.execute("SELECT job_id, status FROM shortlist_runs WHERE run_id = ?", (run_id,)).fetchone()
        if not run or run[1] not in ("awaiting_approval", "completed"):
            raise ValueError("This review was replaced. Open the latest shortlist.")
        items = connection.execute(
            "SELECT application_id, evaluation_id, approved FROM shortlist_items WHERE run_id = ?", (run_id,)
        ).fetchall()
        if len(choices) != len(decisions) or set(choices) != {item[0] for item in items}:
            raise ValueError("Submit exactly one decision for every application in this review.")
        if run[1] == "completed":
            if all(item[2] == int(choices[item[0]]) for item in items):
                return
            raise ValueError("These decisions are already saved. Generate a new shortlist to review again.")
        for application_id, evaluation_id, _ in items:
            _checked_evaluation(connection, application_id, evaluation_id, run[0])
        for application_id, approved in choices.items():
            connection.execute(
                """UPDATE shortlist_items SET approved = ?, decided_at = CURRENT_TIMESTAMP
                   WHERE run_id = ? AND application_id = ?""", (int(approved), run_id, application_id)
            )
        connection.execute(
            "UPDATE shortlist_runs SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE run_id = ?", (run_id,)
        )


class ShortlistState(TypedDict):
    run_id: str
    evaluations: list[dict]


def build_workflow(checkpointer, generate, model_name, database_path=DATABASE_PATH):
    def evaluate(state):
        run = get_run(state["run_id"], database_path=database_path)
        if not run or run["status"] != "evaluating":
            raise ValueError("This shortlist is no longer active.")
        evaluations = []
        for application_id in run["application_ids"]:
            source = get_evaluation_input(application_id, database_path)
            previous = get_latest_resume_evaluation(application_id, database_path)
            hashes = document_hashes(source["resume_text"], source["job_title_at_application"], source["jd_at_application"])
            if previous and (previous["resume_sha256"], previous["jd_sha256"]) == hashes and previous["rubric_version"] == RUBRIC_VERSION:
                validate_assessment(previous["assessment"], source["resume_text"], source["jd_at_application"])
                evaluation_id = previous["evaluation_id"]
            else:
                result = assess_resume(source["resume_text"], source["job_title_at_application"], source["jd_at_application"], generate)
                evaluation_id = save_resume_evaluation(
                    application_id, source["resume_text"], source["job_title_at_application"], source["jd_at_application"],
                    result["assessment"], model_name, database_path,
                )
            evaluations.append({"application_id": application_id, "evaluation_id": evaluation_id})
        return {"evaluations": evaluations}

    def propose(state):
        save_proposals(state["run_id"], state["evaluations"], database_path)
        return {}

    def review(state):
        decisions = interrupt({"run_id": state["run_id"], "message": "Awaiting HR decisions"})
        save_decisions(state["run_id"], decisions, database_path)
        return {}

    graph = StateGraph(ShortlistState)
    graph.add_node("evaluate", evaluate)
    graph.add_node("propose", propose)
    graph.add_node("review", review)
    graph.add_edge(START, "evaluate")
    graph.add_edge("evaluate", "propose")
    graph.add_edge("propose", "review")
    graph.add_edge("review", END)
    return graph.compile(checkpointer=checkpointer)


def advance_workflow(run_id, generate, model_name, decisions=None,
                     database_path=DATABASE_PATH, checkpoint_path=CHECKPOINT_PATH):
    Path(checkpoint_path).parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        graph = build_workflow(checkpointer, generate, model_name, database_path)
        config = {"configurable": {"thread_id": run_id}}
        snapshot = graph.get_state(config)
        if decisions is not None:
            if not snapshot.values or not snapshot.next:
                raise ValueError("This workflow is not waiting for review. Refresh the shortlist.")
            return graph.invoke(Command(resume=decisions), config)
        if snapshot.values:
            return graph.invoke(None, config)
        return graph.invoke({"run_id": run_id}, config)
