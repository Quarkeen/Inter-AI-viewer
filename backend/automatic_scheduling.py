"""Optional LangGraph scheduling workflow; manual scheduling stays independent.

The agent plans with deterministic tools rather than an LLM. A preview never
creates interviews. Commit recalculates under SQLite's write lock, requires the
reviewed plan to match, and atomically saves all interviews plus a retry receipt.
Invitation sending remains the existing, separate HR action.
"""
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from database import DATABASE_PATH
from interview_templates import snapshot
from scheduling import JOIN_BUFFER, _check_slot_conflicts, _timestamp, _verify_approval, reserved_window, utc_now


class SchedulingWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    starts_at: str
    ends_at: str


class AutomaticScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(min_length=1, max_length=64)
    windows: list[SchedulingWindow] = Field(min_length=1, max_length=31)
    application_ids: list[StrictInt] | None = Field(default=None, min_length=1, max_length=500)
    duration_minutes: int = Field(default=30, strict=True, ge=5, le=60)
    gap_minutes: int = Field(default=0, strict=True, ge=0, le=60)
    terminate_on_interruption: bool = Field(default=False, strict=True)
    warning_limit: int = Field(default=3, strict=True, ge=0, le=20)
    template_id: str | None = None
    template_version: int | None = Field(default=None, strict=True, ge=1)


class AutomaticScheduleCommit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch_id: str = Field(pattern=r"^[A-Za-z0-9_-]{16,80}$")
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    schedule: AutomaticScheduleRequest


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _windows(config):
    result = []
    now = utc_now()
    for window in config["windows"]:
        try:
            start, end = (datetime.fromisoformat(_timestamp(datetime.fromisoformat(window[key])))
                          for key in ("starts_at", "ends_at"))
        except (TypeError, ValueError):
            raise ValueError("Choose valid scheduling windows with explicit timezones.") from None
        if start <= now or end <= start or end - start > timedelta(days=1):
            raise ValueError("Each window must start in the future, end after its start, and last at most 24 hours.")
        result.append((start, end))
    result.sort()
    if any(start < previous_end for (_, previous_end), (start, _) in zip(result, result[1:])):
        raise ValueError("Scheduling windows must not overlap.")
    return result


def _plan(connection, config):
    windows = _windows(config)
    run_id = config["run_id"]
    run = connection.execute("SELECT job_id,status FROM shortlist_runs WHERE run_id=?", (run_id,)).fetchone()
    if not run or run["status"] != "completed":
        raise ValueError("Complete HR approval in the latest shortlist before automatic scheduling.")
    latest = connection.execute("SELECT run_id FROM shortlist_runs WHERE job_id=? ORDER BY rowid DESC LIMIT 1", (run["job_id"],)).fetchone()
    if latest[0] != run_id:
        raise ValueError("This shortlist was replaced. Refresh before scheduling.")
    candidates = [dict(row) for row in connection.execute(
        """SELECT a.application_id,i.evaluation_id,c.full_name FROM shortlist_items i
           JOIN applications a ON a.application_id=i.application_id
           JOIN candidates c ON c.candidate_id=a.candidate_id
           WHERE i.run_id=? AND i.approved=1 AND a.job_id=? ORDER BY a.application_id""",
        (run_id, run["job_id"]),
    )]
    selected = config["application_ids"]
    if selected is not None:
        if any(type(value) is not int or value <= 0 for value in selected) or len(set(selected)) != len(selected):
            raise ValueError("Select distinct, valid application IDs.")
        if set(selected) - {row["application_id"] for row in candidates}:
            raise ValueError("Select only approved candidates from this job's latest shortlist.")
        candidates = [row for row in candidates if row["application_id"] in set(selected)]
    if not candidates or len(candidates) > 500:
        raise ValueError("Choose between 1 and 500 approved candidates.")

    existing = connection.execute("SELECT application_id,starts_at,ends_at FROM interviews").fetchall()
    scheduled_ids = {row["application_id"] for row in existing}
    skipped = [row["application_id"] for row in candidates if row["application_id"] in scheduled_ids]
    candidates = [row for row in candidates if row["application_id"] not in scheduled_ids]
    for candidate in candidates:
        _verify_approval(connection, run_id, candidate["application_id"], candidate["evaluation_id"])
    template_json = snapshot(connection, config["template_id"], config["template_version"])
    occupied = sorted(reserved_window(row["starts_at"], row["ends_at"]) for row in existing)
    duration = timedelta(minutes=config["duration_minutes"])
    gap = timedelta(minutes=config["gap_minutes"])
    assignments, unassigned = [], []
    # Existing joining semantics reserve the early start through the latest full finish.
    reservation_duration = 2 * duration + 2 * JOIN_BUFFER
    window_index, cursor = 0, windows[0][0]
    for candidate in candidates:
        allocated = None
        while window_index < len(windows):
            start, end = windows[window_index]
            cursor = max(cursor, start)
            for busy_start, busy_end in occupied:
                if cursor < busy_end + gap and cursor + reservation_duration > busy_start - gap:
                    cursor = busy_end + gap
            if cursor + reservation_duration <= end:
                interview_start = cursor + JOIN_BUFFER
                interview_end = interview_start + duration
                busy_start, busy_end = reserved_window(_timestamp(interview_start), _timestamp(interview_end))
                allocated = candidate | {
                    "starts_at": _timestamp(interview_start), "ends_at": _timestamp(interview_end),
                    "reserved_from": _timestamp(busy_start), "reserved_until": _timestamp(busy_end),
                }
                occupied.append((busy_start, busy_end))
                occupied.sort()
                cursor = busy_end + gap
                break
            window_index += 1
            if window_index < len(windows):
                cursor = windows[window_index][0]
        (assignments if allocated else unassigned).append(allocated or candidate)

    result = {
        "job_id": run["job_id"], "run_id": run_id, "assignments": assignments,
        "unassigned": unassigned, "skipped_application_ids": skipped,
        "reservation_minutes": int(reservation_duration.total_seconds() // 60),
        "gap_minutes": config["gap_minutes"], "can_schedule": bool(assignments) and not unassigned,
        "template_json": template_json,
    }
    result["preview_hash"] = _digest({"request": config, "plan": result})
    # Answer guidance belongs only in saved snapshots, not in preview responses.
    del result["template_json"]
    return result, template_json


class SchedulingState(TypedDict, total=False):
    action: str
    config: dict
    batch_id: str
    preview_hash: str
    result: dict


def build_scheduling_workflow(database_path=DATABASE_PATH):
    def plan(state):
        with closing(sqlite3.connect(database_path, timeout=15)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN")
            if state["action"] == "commit":
                cached = connection.execute("SELECT request_sha256,result_json FROM scheduling_batches WHERE batch_id=?", (state["batch_id"],)).fetchone()
                if cached:
                    if cached["request_sha256"] != _digest({"config": state["config"], "preview_hash": state["preview_hash"]}):
                        raise ValueError("This scheduling request ID was already used. Generate a new preview.")
                    return {"result": json.loads(cached["result_json"])}
            result, _ = _plan(connection, state["config"])
            return {"result": result}

    def reserve(state):
        config = state["config"]
        request_hash = _digest({"config": config, "preview_hash": state["preview_hash"]})
        with closing(sqlite3.connect(database_path, timeout=15)) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            cached = connection.execute("SELECT request_sha256,result_json FROM scheduling_batches WHERE batch_id=?", (state["batch_id"],)).fetchone()
            if cached:
                if cached["request_sha256"] != request_hash:
                    raise ValueError("This scheduling request ID was already used. Generate a new preview.")
                return {"result": json.loads(cached["result_json"])}
            result, template_json = _plan(connection, config)
            if result["preview_hash"] != state["preview_hash"]:
                raise ValueError("Availability or approvals changed since preview. Generate a new preview; no interviews were saved.")
            if not result["can_schedule"]:
                raise ValueError("There is not enough space for all selected candidates. Add a window and preview again; no interviews were saved.")
            for candidate in result["assignments"]:
                _check_slot_conflicts(connection, candidate["starts_at"], candidate["ends_at"])
                candidate["interview_id"] = connection.execute(
                    """INSERT INTO interviews
                       (application_id,run_id,evaluation_id,starts_at,ends_at,terminate_on_interruption,template_json,warning_limit)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (candidate["application_id"], config["run_id"], candidate["evaluation_id"], candidate["starts_at"],
                     candidate["ends_at"], int(config["terminate_on_interruption"]), template_json, config["warning_limit"]),
                ).lastrowid
            result["batch_id"] = state["batch_id"]
            connection.execute("INSERT INTO scheduling_batches(batch_id,run_id,request_sha256,result_json) VALUES(?,?,?,?)",
                               (state["batch_id"], config["run_id"], request_hash, json.dumps(result)))
            return {"result": result}

    graph = StateGraph(SchedulingState)
    graph.add_node("plan", plan)
    graph.add_node("reserve", reserve)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", lambda state: "reserve" if state["action"] == "commit" and "batch_id" not in state["result"] else END)
    graph.add_edge("reserve", END)
    return graph.compile()


def preview_automatic_schedule(request, database_path=DATABASE_PATH):
    config = AutomaticScheduleRequest.model_validate(request).model_dump()
    return build_scheduling_workflow(database_path).invoke({"action": "preview", "config": config})["result"]


def commit_automatic_schedule(request, database_path=DATABASE_PATH):
    validated = AutomaticScheduleCommit.model_validate(request)
    return build_scheduling_workflow(database_path).invoke({
        "action": "commit", "config": validated.schedule.model_dump(),
        "batch_id": validated.batch_id, "preview_hash": validated.preview_hash,
    })["result"]
