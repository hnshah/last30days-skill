"""Tests for watchlist.py command functions."""

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "last30days" / "scripts"))

import store
import watchlist
from lib import schema


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    
    # Override the database path
    original_override = store._db_override
    store._db_override = db_path
    
    # Initialize fresh database
    store.init_db()
    
    yield db_path
    
    # Cleanup
    store._db_override = original_override
    if db_path.exists():
        db_path.unlink()


# === Tests for cmd_add() ===

def test_cmd_add_basic(temp_db, capsys):
    """Test adding a topic with default schedule."""
    args = Mock()
    args.topic = "Test Topic"
    args.weekly = False
    args.schedule = None
    args.queries = None
    
    watchlist.cmd_add(args)
    
    # Verify output
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    
    assert output["action"] == "added"
    assert output["topic"] == "Test Topic"
    assert "daily" in output["schedule"]


def test_cmd_add_with_custom_schedule(temp_db, capsys):
    """Test adding a topic with custom schedule."""
    args = Mock()
    args.topic = "Test Topic"
    args.weekly = False
    args.schedule = "0 12 * * *"
    args.queries = None
    
    watchlist.cmd_add(args)
    
    # Verify in database
    topic = store.get_topic("Test Topic")
    assert topic["schedule"] == "0 12 * * *"


def test_cmd_add_weekly(temp_db, capsys):
    """Test adding a topic with weekly schedule."""
    args = Mock()
    args.topic = "Test Topic"
    args.weekly = True
    args.schedule = None
    args.queries = None
    
    watchlist.cmd_add(args)
    
    # Verify weekly schedule
    topic = store.get_topic("Test Topic")
    assert topic["schedule"] == "0 8 * * 1"  # Monday 8am


def test_cmd_add_with_search_queries(temp_db, capsys):
    """Test adding a topic with custom search queries."""
    args = Mock()
    args.topic = "Test Topic"
    args.weekly = False
    args.schedule = None
    args.queries = "query1, query2, query3"
    
    watchlist.cmd_add(args)
    
    # Verify queries stored
    topic = store.get_topic("Test Topic")
    queries = json.loads(topic["search_queries"])
    assert queries == ["query1", "query2", "query3"]


# === Tests for cmd_remove() ===

def test_cmd_remove_existing_topic(temp_db, capsys):
    """Test removing an existing topic."""
    # Add a topic first
    store.add_topic("Test Topic")
    
    args = Mock()
    args.topic = "Test Topic"
    
    watchlist.cmd_remove(args)
    
    # Verify output
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    
    assert output["action"] == "removed"
    assert output["topic"] == "Test Topic"


def test_cmd_remove_nonexistent_topic(temp_db, capsys):
    """Test removing a topic that doesn't exist."""
    args = Mock()
    args.topic = "Nonexistent Topic"
    
    watchlist.cmd_remove(args)
    
    # Verify output
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    
    assert output["action"] == "not_found"
    assert output["topic"] == "Nonexistent Topic"


# === Tests for cmd_list() ===

def test_cmd_list_empty(temp_db, capsys):
    """Test listing when no topics exist."""
    args = Mock()
    
    watchlist.cmd_list(args)
    
    # Verify output
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    
    assert output["topics"] == []
    assert output["budget_used"] == 0.0
    assert output["budget_limit"] == 5.0


def test_cmd_list_with_topics(temp_db, capsys):
    """Test listing with multiple topics."""
    # Add topics
    store.add_topic("Topic 1")
    store.add_topic("Topic 2")
    store.add_topic("Topic 3")
    
    args = Mock()
    
    watchlist.cmd_list(args)
    
    # Verify output
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    
    assert len(output["topics"]) == 3
    topic_names = {t["name"] for t in output["topics"]}
    assert topic_names == {"Topic 1", "Topic 2", "Topic 3"}


# === Tests for cmd_due() / cmd_run_due() ===

def test_cmd_due_lists_due_topics(temp_db, capsys):
    """Test listing topics that should be run now."""
    store.add_topic("Due Topic")

    args = Mock()

    watchlist.cmd_due(args)

    output = json.loads(capsys.readouterr().out)
    assert output["action"] == "due"
    assert output["count"] == 1
    assert output["topics"][0]["name"] == "Due Topic"


@patch('watchlist._run_topic')
def test_cmd_run_due_runs_only_due_topics(mock_run, temp_db, capsys):
    """Test running only due topics."""
    due_topic = store.add_topic("Due Topic")
    fresh_topic = store.add_topic("Fresh Topic")
    run_id = store.record_run(fresh_topic["id"], source_mode="v3", status="completed")
    conn = sqlite3.connect(str(temp_db))
    conn.execute(
        "UPDATE research_runs SET run_date = datetime('now') WHERE id = ?",
        (run_id,),
    )
    conn.commit()
    conn.close()
    mock_run.return_value = {
        "topic": "Due Topic",
        "status": "completed",
        "new": 1,
        "updated": 0,
        "duration": 1.0,
    }

    args = Mock()

    watchlist.cmd_run_due(args)

    mock_run.assert_called_once()
    assert mock_run.call_args[0][0]["id"] == due_topic["id"]
    output = json.loads(capsys.readouterr().out)
    assert output["action"] == "run_due"
    assert output["ran"] == 1
    assert output["results"][0]["topic"] == "Due Topic"


@patch('watchlist._run_topic')
def test_cmd_run_due_respects_budget(mock_run, temp_db, capsys):
    """Test run-due skips work when the daily budget is already spent."""
    store.add_topic("Due Topic")
    store.set_setting("daily_budget", "0.00")

    args = Mock()

    watchlist.cmd_run_due(args)

    mock_run.assert_not_called()
    output = json.loads(capsys.readouterr().out)
    assert output["ran"] == 0
    assert output["results"][0]["status"] == "skipped"


# === Tests for cmd_delta() ===

def test_cmd_delta_outputs_topic_delta(temp_db, capsys):
    """Test printing the latest watchlist delta as JSON."""
    topic = store.add_topic("Test Topic")
    previous_run_id = store.record_run(topic["id"], source_mode="v3", status="completed")
    store.store_findings(previous_run_id, topic["id"], [
        {
            "source": "reddit",
            "source_url": "https://reddit.com/continued",
            "source_title": "Continued",
            "content": "Still present",
        }
    ])
    current_run_id = store.record_run(topic["id"], source_mode="v3", status="completed")
    store.store_findings(current_run_id, topic["id"], [
        {
            "source": "reddit",
            "source_url": "https://reddit.com/continued",
            "source_title": "Continued",
            "content": "Still present",
        },
        {
            "source": "github",
            "source_url": "https://github.com/example/new",
            "source_title": "New",
            "content": "New this run",
        },
    ])

    args = Mock()
    args.topic = "Test Topic"
    args.emit = "json"

    watchlist.cmd_delta(args)

    captured = capsys.readouterr()
    output = json.loads(captured.out)

    assert output["topic"] == "Test Topic"
    assert output["status"] == "ok"
    assert output["current_run_id"] == current_run_id
    assert output["previous_run_id"] == previous_run_id
    assert output["new"] == 1
    assert output["continued"] == 1


def test_cmd_delta_unknown_topic_exits(temp_db):
    """Test delta for an unknown topic exits with an error."""
    args = Mock()
    args.topic = "Missing Topic"
    args.emit = "json"

    with pytest.raises(SystemExit):
        watchlist.cmd_delta(args)


def test_cmd_delta_compact_output(temp_db, capsys):
    """Test printing a human-readable watchlist delta brief."""
    topic = store.add_topic("Test Topic")
    previous_run_id = store.record_run(topic["id"], source_mode="v3", status="completed")
    store.store_findings(previous_run_id, topic["id"], [
        {
            "source": "reddit",
            "source_url": "https://reddit.com/continued",
            "source_title": "Continued",
            "content": "Still present",
        }
    ])
    current_run_id = store.record_run(topic["id"], source_mode="v3", status="completed")
    store.store_findings(current_run_id, topic["id"], [
        {
            "source": "github",
            "source_url": "https://github.com/example/new",
            "source_title": "New",
            "content": "New this run",
        },
    ])

    args = Mock()
    args.topic = "Test Topic"
    args.emit = "compact"

    watchlist.cmd_delta(args)

    output = capsys.readouterr().out
    assert "# Watchlist delta: Test Topic" in output
    assert f"- Current run: {current_run_id}" in output
    assert f"- Previous run: {previous_run_id}" in output
    assert "- New: 1" in output
    assert "- Continued: 0" in output
    assert "- Dropped: 1" in output
    assert "## What changed" in output
    assert "New" in output


# === Tests for cmd_dossier() ===

def test_cmd_dossier_outputs_json(temp_db, capsys):
    """Test printing a topic dossier as JSON."""
    topic = store.add_topic("Test Topic")
    run_id = store.record_run(topic["id"], source_mode="v3", status="completed")
    store.store_findings(run_id, topic["id"], [
        {
            "source": "github",
            "source_url": "https://github.com/example/new",
            "source_title": "New",
            "content": "New this run",
        }
    ])

    args = Mock()
    args.topic = "Test Topic"
    args.emit = "json"

    watchlist.cmd_dossier(args)

    output = json.loads(capsys.readouterr().out)
    assert output["topic"] == "Test Topic"
    assert output["finding_count"] == 1
    assert output["recent_runs"][0]["id"] == run_id


def test_cmd_dossier_compact_output(temp_db, capsys):
    """Test printing a human-readable topic dossier."""
    topic = store.add_topic("Test Topic")
    run_id = store.record_run(topic["id"], source_mode="v3", status="completed")
    store.store_findings(run_id, topic["id"], [
        {
            "source": "github",
            "source_url": "https://github.com/example/new",
            "source_title": "New",
            "content": "New this run",
        }
    ])

    args = Mock()
    args.topic = "Test Topic"
    args.emit = "md"

    watchlist.cmd_dossier(args)

    output = capsys.readouterr().out
    assert "# Watchlist dossier: Test Topic" in output
    assert "## Recent runs" in output
    assert f"run {run_id}" in output
    assert "## Recent findings" in output
    assert "New" in output


def test_cmd_dossier_compact_output_includes_escalation(temp_db, capsys):
    """Test dossier briefs include quiet/escalate recommendation."""
    topic = store.add_topic("Test Topic")
    first_run_id = store.record_run(topic["id"], source_mode="v3", status="completed")
    store.store_findings(first_run_id, topic["id"], [
        {
            "source": "reddit",
            "source_url": "https://reddit.com/old",
            "source_title": "Old",
            "content": "Old item",
        }
    ])
    second_run_id = store.record_run(topic["id"], source_mode="v3", status="completed")
    store.store_findings(second_run_id, topic["id"], [
        {
            "source": "github",
            "source_url": "https://github.com/example/new-1",
            "source_title": "New 1",
            "content": "New item",
        },
        {
            "source": "reddit",
            "source_url": "https://reddit.com/new-2",
            "source_title": "New 2",
            "content": "New item",
        },
    ])

    args = Mock()
    args.topic = "Test Topic"
    args.emit = "compact"

    watchlist.cmd_dossier(args)

    output = capsys.readouterr().out
    assert "## Escalation" in output
    assert "- Decision: escalate" in output
    assert "- Recommended action: review_delta" in output


# === Tests for cmd_handoff() ===

def test_cmd_handoff_outputs_json_bundle(temp_db, capsys):
    """Test printing an agent handoff bundle."""
    topic = store.add_topic("Test Topic")
    first_run_id = store.record_run(topic["id"], source_mode="v3", status="completed")
    store.store_findings(first_run_id, topic["id"], [
        {
            "source": "reddit",
            "source_url": "https://reddit.com/old",
            "source_title": "Old",
            "content": "Old item",
        }
    ])
    second_run_id = store.record_run(topic["id"], source_mode="v3", status="completed")
    store.store_findings(second_run_id, topic["id"], [
        {
            "source": "github",
            "source_url": "https://github.com/example/new",
            "source_title": "New",
            "content": "New item",
        },
    ])

    args = Mock()
    args.topic = "Test Topic"
    args.agent = "openclaw"
    args.emit = "json"

    watchlist.cmd_handoff(args)

    output = json.loads(capsys.readouterr().out)
    assert output["agent"] == "openclaw"
    assert output["topic"] == "Test Topic"
    assert "task" in output
    assert output["delta"]["new"] == 1


def test_cmd_handoff_compact_output(temp_db, capsys):
    """Test printing a readable agent handoff bundle."""
    topic = store.add_topic("Test Topic")
    run_id = store.record_run(topic["id"], source_mode="v3", status="completed")
    store.store_findings(run_id, topic["id"], [
        {
            "source": "github",
            "source_url": "https://github.com/example/new",
            "source_title": "New",
            "content": "New item",
        }
    ])

    args = Mock()
    args.topic = "Test Topic"
    args.agent = "hermes"
    args.emit = "compact"

    watchlist.cmd_handoff(args)

    output = capsys.readouterr().out
    assert "# Watchlist handoff: Test Topic" in output
    assert "- Agent: hermes" in output
    assert "## Task" in output
    assert "## Context" in output


# === Tests for cmd_config() ===

def test_cmd_config_delivery(temp_db, capsys):
    """Test configuring delivery channel."""
    args = Mock()
    args.key = "delivery"
    args.value = "https://hooks.slack.com/services/TEST"
    
    watchlist.cmd_config(args)
    
    # Verify setting stored
    channel = store.get_setting("delivery_channel")
    assert channel == "https://hooks.slack.com/services/TEST"
    
    # Verify output
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    
    assert output["action"] == "config"
    assert output["key"] == "delivery_channel"


def test_cmd_config_budget(temp_db, capsys):
    """Test configuring daily budget."""
    args = Mock()
    args.key = "budget"
    args.value = 10.0
    
    watchlist.cmd_config(args)
    
    # Verify setting stored
    budget = store.get_setting("daily_budget")
    assert budget == "10.0"


def test_cmd_config_unknown_key(temp_db):
    """Test that unknown config key raises error."""
    args = Mock()
    args.key = "unknown_key"
    args.value = "value"
    
    with pytest.raises(SystemExit):
        watchlist.cmd_config(args)


# === Tests for _run_topic() ===

@patch('watchlist.subprocess.run')
def test_run_topic_success(mock_subprocess, temp_db):
    """Test successful topic run."""
    topic = store.add_topic("Test Topic")
    
    # Mock successful subprocess call
    mock_result = Mock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps({
        "topic": "Test Topic",
        "range_from": "2026-01-01",
        "range_to": "2026-04-03",
        "generated_at": "2026-04-03T00:00:00Z",
        "provider_runtime": {
            "reasoning_provider": "gemini",
            "planner_model": "gemini-2.0-flash-exp",
            "rerank_model": "gemini-2.0-flash-exp",
        },
        "query_plan": {
            "intent": "test",
            "freshness_mode": "recent",
            "cluster_mode": "standard",
            "raw_topic": "test",
            "subqueries": [],
            "source_weights": {},
        },
        "clusters": [],
        "ranked_candidates": [
            {
                "candidate_id": "c-r1",
                "item_id": "R1",
                "source": "reddit",
                "title": "Test",
                "url": "https://reddit.com/1",
                "snippet": "Snippet",
                "subquery_labels": ["primary"],
                "native_ranks": {"reddit": 1},
                "local_relevance": 0.8,
                "freshness": 100,
                "engagement": 50.0,
                "source_quality": 0.8,
                "rrf_score": 1.0,
                "final_score": 0.8,
                "explanation": "Snippet",
                "source_items": [
                    {
                        "item_id": "R1",
                        "source": "reddit",
                        "title": "Test",
                        "body": "Content",
                        "url": "https://reddit.com/1",
                        "author": "user",
                        "engagement_score": 50.0,
                        "local_relevance": 0.8,
                        "snippet": "Snippet",
                    }
                ],
            }
        ],
        "items_by_source": {
            "reddit": [
                {
                    "item_id": "R1",
                    "source": "reddit",
                    "title": "Test",
                    "body": "Content",
                    "url": "https://reddit.com/1",
                    "author": "user",
                    "engagement_score": 50.0,
                    "local_relevance": 0.8,
                    "snippet": "Snippet",
                }
            ],
        },
        "errors_by_source": {},
        "warnings": [],
    })
    mock_subprocess.return_value = mock_result
    
    result = watchlist._run_topic(topic)
    
    assert result["status"] == "completed"
    assert result["new"] == 1
    assert result["topic"] == "Test Topic"


@patch('watchlist.subprocess.run')
def test_run_topic_failure(mock_subprocess, temp_db):
    """Test topic run failure."""
    topic = store.add_topic("Test Topic")
    
    # Mock failed subprocess call
    mock_result = Mock()
    mock_result.returncode = 1
    mock_result.stderr = "Error message"
    mock_subprocess.return_value = mock_result
    
    result = watchlist._run_topic(topic)
    
    assert result["status"] == "failed"
    assert "Error message" in result["error"]


@patch('watchlist.subprocess.run')
def test_run_topic_timeout(mock_subprocess, temp_db):
    """Test topic run timeout."""
    topic = store.add_topic("Test Topic")
    
    # Mock timeout
    mock_subprocess.side_effect = subprocess.TimeoutExpired("cmd", 300)
    
    result = watchlist._run_topic(topic)
    
    assert result["status"] == "failed"
    assert result["error"] == "timeout"


@patch('watchlist.subprocess.run')
@patch('watchlist._deliver_findings')
def test_run_topic_calls_delivery(mock_deliver, mock_subprocess, temp_db):
    """Test that successful run calls delivery."""
    topic = store.add_topic("Test Topic")
    
    # Mock successful subprocess call with findings
    mock_result = Mock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps({
        "topic": "Test Topic",
        "range_from": "2026-01-01",
        "range_to": "2026-04-03",
        "generated_at": "2026-04-03T00:00:00Z",
        "provider_runtime": {
            "reasoning_provider": "gemini",
            "planner_model": "gemini-2.0-flash-exp",
            "rerank_model": "gemini-2.0-flash-exp",
        },
        "query_plan": {
            "intent": "test",
            "freshness_mode": "recent",
            "cluster_mode": "standard",
            "raw_topic": "test",
            "subqueries": [],
            "source_weights": {},
        },
        "clusters": [],
        "ranked_candidates": [
            {
                "candidate_id": "c-r1",
                "item_id": "R1",
                "source": "reddit",
                "title": "Test",
                "url": "https://reddit.com/1",
                "snippet": "Snippet",
                "subquery_labels": ["primary"],
                "native_ranks": {"reddit": 1},
                "local_relevance": 0.8,
                "freshness": 100,
                "engagement": 50.0,
                "source_quality": 0.8,
                "rrf_score": 1.0,
                "final_score": 0.8,
                "explanation": "Snippet",
                "source_items": [
                    {
                        "item_id": "R1",
                        "source": "reddit",
                        "title": "Test",
                        "body": "Content",
                        "url": "https://reddit.com/1",
                        "author": "user",
                        "engagement_score": 50.0,
                        "local_relevance": 0.8,
                        "snippet": "Snippet",
                    }
                ],
            }
        ],
        "items_by_source": {
            "reddit": [
                {
                    "item_id": "R1",
                    "source": "reddit",
                    "title": "Test",
                    "body": "Content",
                    "url": "https://reddit.com/1",
                    "author": "user",
                    "engagement_score": 50.0,
                    "local_relevance": 0.8,
                    "snippet": "Snippet",
                }
            ],
        },
        "errors_by_source": {},
        "warnings": [],
    })
    mock_subprocess.return_value = mock_result
    
    watchlist._run_topic(topic)
    
    # Verify delivery was called
    assert mock_deliver.called
    call_args = mock_deliver.call_args[0]
    assert call_args[0] == "Test Topic"
    assert call_args[1]["new"] == 1


# === Tests for cmd_run_one() ===

@patch('watchlist._run_topic')
def test_cmd_run_one(mock_run, temp_db, capsys):
    """Test running a single topic."""
    topic = store.add_topic("Test Topic")
    
    mock_run.return_value = {
        "topic": "Test Topic",
        "status": "completed",
        "new": 5,
        "updated": 2,
        "duration": 60.0,
    }
    
    args = Mock()
    args.topic = "Test Topic"
    
    watchlist.cmd_run_one(args)
    
    # Verify output
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    
    assert output["status"] == "completed"
    assert output["new"] == 5


def test_cmd_run_one_nonexistent_topic(temp_db, capsys):
    """Test running a nonexistent topic."""
    args = Mock()
    args.topic = "Nonexistent Topic"
    
    with pytest.raises(SystemExit):
        watchlist.cmd_run_one(args)


# === Tests for cmd_run_all() ===

@patch('watchlist._run_topic')
def test_cmd_run_all_no_topics(mock_run, temp_db, capsys):
    """Test running all topics when none exist."""
    args = Mock()
    
    watchlist.cmd_run_all(args)
    
    # Verify output
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    
    assert "No enabled topics" in output["message"]


@patch('watchlist._run_topic')
def test_cmd_run_all_multiple_topics(mock_run, temp_db, capsys):
    """Test running multiple topics."""
    # Add topics
    store.add_topic("Topic 1")
    store.add_topic("Topic 2")
    
    mock_run.return_value = {
        "topic": "Test",
        "status": "completed",
        "new": 5,
        "updated": 2,
        "duration": 60.0,
    }
    
    args = Mock()
    
    watchlist.cmd_run_all(args)
    
    # Verify output
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    
    assert output["action"] == "run_all"
    assert len(output["results"]) == 2


@patch('watchlist._run_topic')
@patch('watchlist.store.get_daily_cost')
def test_cmd_run_all_respects_budget(mock_cost, mock_run, temp_db, capsys):
    """Test that run-all respects daily budget."""
    # Add topics
    store.add_topic("Topic 1")
    store.add_topic("Topic 2")
    store.add_topic("Topic 3")
    
    # Mock budget exceeded (budget limit is 5.0)
    mock_cost.return_value = 6.0  # Over budget
    
    mock_run.return_value = {
        "topic": "Test",
        "status": "completed",
        "new": 5,
        "updated": 2,
        "duration": 60.0,
    }
    
    args = Mock()
    
    watchlist.cmd_run_all(args)
    
    # Verify output
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    
    # All topics should be skipped due to budget
    results = output["results"]
    skipped = [r for r in results if r["status"] == "skipped"]
    
    assert len(skipped) == 3  # All 3 topics skipped


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
