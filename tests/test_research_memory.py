"""Tests for the Engram-pattern research memory store."""

import pytest
import pytest_asyncio

from research_mcp_server.store.research_memory import ResearchMemory


@pytest_asyncio.fixture
async def memory(tmp_path):
    """Create a ResearchMemory instance with a temporary database."""
    db_path = tmp_path / "test_research_memory.db"
    mem = ResearchMemory(db_path=db_path)
    await mem._ensure_initialized()
    return mem


@pytest.mark.asyncio
async def test_create_session(memory: ResearchMemory):
    session_id = await memory.create_session(
        name="LLM Reasoning Survey",
        goal="Survey chain-of-thought prompting techniques",
    )
    assert session_id is not None
    assert len(session_id) == 36  # UUID format

    session = await memory.get_session(session_id)
    assert session is not None
    assert session["name"] == "LLM Reasoning Survey"
    assert session["goal"] == "Survey chain-of-thought prompting techniques"
    assert session["status"] == "active"

    # List sessions
    sessions = await memory.list_sessions(status="active")
    assert len(sessions) == 1
    assert sessions[0]["id"] == session_id

    # Close session
    await memory.close_session(session_id)
    session = await memory.get_session(session_id)
    assert session["status"] == "closed"

    # Active filter should return empty
    active = await memory.list_sessions(status="active")
    assert len(active) == 0


@pytest.mark.asyncio
async def test_add_thesis(memory: ResearchMemory):
    thesis_id = await memory.add_thesis(
        statement="Chain-of-thought prompting improves reasoning in LLMs >7B params",
        category="confirmatory",
        confidence=0.7,
    )
    assert thesis_id is not None
    assert len(thesis_id) == 36

    thesis = await memory.get_thesis(thesis_id)
    assert thesis is not None
    assert thesis["statement"] == "Chain-of-thought prompting improves reasoning in LLMs >7B params"
    assert thesis["category"] == "confirmatory"
    assert thesis["confidence"] == 0.7
    assert thesis["status"] == "active"
    assert thesis["evidence"] == []


@pytest.mark.asyncio
async def test_update_thesis_confidence(memory: ResearchMemory):
    thesis_id = await memory.add_thesis(
        statement="Scaling laws hold for sparse models",
        confidence=0.5,
    )

    # Add first evidence
    await memory.update_thesis(
        thesis_id,
        confidence=0.6,
        evidence="Paper 2401.00001 confirms scaling for MoE models",
    )

    thesis = await memory.get_thesis(thesis_id)
    assert thesis["confidence"] == 0.6
    assert len(thesis["evidence"]) == 1
    assert "MoE models" in thesis["evidence"][0]

    # Add second evidence — should append, not replace
    await memory.update_thesis(
        thesis_id,
        confidence=0.8,
        evidence="Paper 2401.00002 extends to vision transformers",
    )

    thesis = await memory.get_thesis(thesis_id)
    assert thesis["confidence"] == 0.8
    assert len(thesis["evidence"]) == 2
    assert "vision transformers" in thesis["evidence"][1]


@pytest.mark.asyncio
async def test_save_and_get_digest(memory: ResearchMemory):
    session_id = await memory.create_session(
        name="Digest Test Session",
        goal="Test digest saving",
    )

    digest_id = await memory.save_digest(
        content="This session explored scaling laws across architectures.",
        session_id=session_id,
        validated_theses=["thesis-1"],
        invalidated_theses=["thesis-2"],
        emerging_patterns=["MoE models follow different scaling curves"],
        active_opportunities=["Investigate sparse-dense hybrid scaling"],
        meta={"paper_count": 12},
    )
    assert digest_id is not None
    assert len(digest_id) == 36

    digest = await memory.get_latest_digest()
    assert digest is not None
    assert digest["id"] == digest_id
    assert digest["session_id"] == session_id
    assert digest["content"] == "This session explored scaling laws across architectures."
    assert digest["validated_theses"] == ["thesis-1"]
    assert digest["invalidated_theses"] == ["thesis-2"]
    assert digest["emerging_patterns"] == ["MoE models follow different scaling curves"]
    assert digest["active_opportunities"] == ["Investigate sparse-dense hybrid scaling"]
    assert digest["meta"] == {"paper_count": 12}


@pytest.mark.asyncio
async def test_get_active_theses(memory: ResearchMemory):
    t1 = await memory.add_thesis(statement="Thesis A", confidence=0.5)
    t2 = await memory.add_thesis(statement="Thesis B", confidence=0.6)
    t3 = await memory.add_thesis(statement="Thesis C", confidence=0.7)

    # Invalidate one
    await memory.update_thesis(t2, status="invalidated")

    active = await memory.get_active_theses()
    assert len(active) == 2
    active_ids = {t["id"] for t in active}
    assert t1 in active_ids
    assert t3 in active_ids
    assert t2 not in active_ids


@pytest.mark.asyncio
async def test_session_papers_tracking(memory: ResearchMemory):
    session_id = await memory.create_session(
        name="Paper Tracking Test",
        goal="Test paper tracking",
    )

    await memory.add_session_paper(
        session_id=session_id,
        paper_id="2401.00001",
        action="read",
        notes="Foundational scaling paper",
    )
    await memory.add_session_paper(
        session_id=session_id,
        paper_id="2401.00002",
        action="cited",
        notes="Confirms MoE scaling hypothesis",
    )

    papers = await memory.get_session_papers(session_id)
    assert len(papers) == 2
    paper_ids = {p["paper_id"] for p in papers}
    assert "2401.00001" in paper_ids
    assert "2401.00002" in paper_ids

    # Check details of one paper
    p1 = next(p for p in papers if p["paper_id"] == "2401.00001")
    assert p1["action"] == "read"
    assert p1["notes"] == "Foundational scaling paper"

    # Upsert: update existing paper
    await memory.add_session_paper(
        session_id=session_id,
        paper_id="2401.00001",
        action="compared",
        notes="Used in comparison with newer work",
    )
    papers = await memory.get_session_papers(session_id)
    assert len(papers) == 2  # Still 2, not 3
    p1_updated = next(p for p in papers if p["paper_id"] == "2401.00001")
    assert p1_updated["action"] == "compared"


@pytest.mark.asyncio
async def test_get_warm_context(memory: ResearchMemory):
    # Empty state
    ctx = await memory.get_warm_context()
    assert ctx["total_prior_runs"] == 0
    assert ctx["latest_digest"] is None
    assert ctx["active_theses"] == []

    # Add data
    await memory.create_session(name="S1", goal="G1")
    await memory.add_thesis(statement="T1")
    await memory.save_digest(content="Digest content")

    ctx = await memory.get_warm_context()
    assert ctx["total_prior_runs"] == 1
    assert ctx["latest_digest"] is not None
    assert ctx["latest_digest"]["content"] == "Digest content"
    assert len(ctx["active_theses"]) == 1
