"""Tests for bus/sqlite.py - SQLiteBus implementation"""

import pytest
import tempfile
import os
from pathlib import Path

from omnicompany.bus.sqlite import SQLiteBus
from omnicompany.core.config import resolve_unified_db_path
from omnicompany.protocol.events import FactoryEvent, EventMetadata


@pytest.fixture(autouse=True)
def isolated_omni_db_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNICOMPANY_DB_DIR", str(tmp_path))


class TestSQLiteBusBasic:
    """Test basic SQLiteBus operations."""

    def test_sqlite_bus_init_default(self):
        """Test SQLiteBus initialization with default path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            bus = SQLiteBus(db_path)
            assert bus.db_path == resolve_unified_db_path("events.db")
            with pytest.raises(RuntimeError):
                _ = bus.conn

    def test_sqlite_bus_init_with_path(self):
        """Test SQLiteBus initialization with explicit path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "events.db"
            bus = SQLiteBus(db_path)
            assert bus.db_path == resolve_unified_db_path("events.db")

    @pytest.mark.asyncio
    async def test_sqlite_bus_connect(self):
        """Test SQLiteBus connect."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            bus = SQLiteBus(db_path)
            await bus.connect()
            # Connection should be established

    @pytest.mark.asyncio
    async def test_sqlite_bus_close(self):
        """Test SQLiteBus close."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            bus = SQLiteBus(db_path)
            await bus.connect()
            await bus.close()
            # Connection should be closed

    @pytest.mark.asyncio
    async def test_sqlite_bus_context_manager(self):
        """Test SQLiteBus as async context manager."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            async with SQLiteBus(db_path) as bus:
                assert bus.conn is not None
            # Should close automatically


class TestSQLiteBusPublish:
    """Test SQLiteBus publish operations."""

    @pytest.mark.asyncio
    async def test_sqlite_bus_publish(self):
        """Test publishing an event."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            async with SQLiteBus(db_path) as bus:
                event = FactoryEvent(
                    event_type="test.event",
                    trace_id="trace123",
                    source="test.source",
                    payload={"key": "value"},
                )
                event_id = await bus.publish(event)
                assert event_id == event.id

    @pytest.mark.asyncio
    async def test_sqlite_bus_publish_persists(self):
        """Test that published events are persisted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            # Publish event
            async with SQLiteBus(db_path) as bus:
                event = FactoryEvent(
                    event_type="test.event",
                    trace_id="trace123",
                    source="test.source",
                    payload={"key": "value"},
                )
                await bus.publish(event)

            # Reopen and verify
            async with SQLiteBus(db_path) as bus:
                events = await bus.read_trace("trace123")
                assert len(events) == 1
                assert events[0].payload["key"] == "value"

    @pytest.mark.asyncio
    async def test_sqlite_bus_publish_multiple(self):
        """Test publishing multiple events."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            async with SQLiteBus(db_path) as bus:
                for i in range(10):
                    await bus.publish(
                        FactoryEvent(
                            event_type="test.event",
                            trace_id="trace123",
                            source="test.source",
                            payload={"index": i},
                        )
                    )

                count = await bus.count()
                assert count == 10


class TestSQLiteBusReadTrace:
    """Test SQLiteBus read_trace operations."""

    @pytest.mark.asyncio
    async def test_sqlite_bus_read_trace(self):
        """Test reading events by trace_id."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            async with SQLiteBus(db_path) as bus:
                # Publish events
                for i in range(5):
                    await bus.publish(
                        FactoryEvent(
                            event_type="test.event",
                            trace_id="trace_main",
                            source="test.source",
                            payload={"index": i},
                        )
                    )

                # Read trace
                events = await bus.read_trace("trace_main")
                assert len(events) == 5
                for i, event in enumerate(events):
                    assert event.payload["index"] == i

    @pytest.mark.asyncio
    async def test_sqlite_bus_read_trace_empty(self):
        """Test reading non-existent trace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            async with SQLiteBus(db_path) as bus:
                events = await bus.read_trace("nonexistent")
                assert len(events) == 0

    @pytest.mark.asyncio
    async def test_sqlite_bus_read_trace_ordered(self):
        """Test that events are returned in timestamp order."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            async with SQLiteBus(db_path) as bus:
                # Publish in order
                for i in range(5):
                    await bus.publish(
                        FactoryEvent(
                            event_type="test.event",
                            trace_id="trace123",
                            source="test.source",
                            payload={"index": i},
                        )
                    )

                events = await bus.read_trace("trace123")
                # Should be in insertion order (which correlates with timestamp)
                for i, event in enumerate(events):
                    assert event.payload["index"] == i


class TestSQLiteBusReplay:
    """Test SQLiteBus replay operations."""

    @pytest.mark.asyncio
    async def test_sqlite_bus_replay(self):
        """Test replaying events with filters."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            async with SQLiteBus(db_path) as bus:
                # Publish different event types
                for i in range(3):
                    await bus.publish(
                        FactoryEvent(
                            event_type="agent.thought",
                            trace_id="trace123",
                            source="agent.planner",
                            payload={"index": i},
                        )
                    )
                    await bus.publish(
                        FactoryEvent(
                            event_type="agent.action",
                            trace_id="trace123",
                            source="agent.executor",
                            payload={"index": i},
                        )
                    )

                # Replay all
                events = await bus.replay(trace_id="trace123")
                assert len(events) == 6

                # Replay by event type
                thoughts = await bus.replay(
                    trace_id="trace123",
                    event_type="agent.thought",
                )
                assert len(thoughts) == 3
                assert all(e.event_type == "agent.thought" for e in thoughts)

                # Replay by source
                planner_events = await bus.replay(
                    trace_id="trace123",
                    source="agent.planner",
                )
                assert len(planner_events) == 3
                assert all(e.source == "agent.planner" for e in planner_events)

    @pytest.mark.asyncio
    async def test_sqlite_bus_replay_limit(self):
        """Test replaying with limit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            async with SQLiteBus(db_path) as bus:
                for i in range(10):
                    await bus.publish(
                        FactoryEvent(
                            event_type="test.event",
                            trace_id="trace123",
                            source="test.source",
                            payload={"index": i},
                        )
                    )

                # Replay with limit
                events = await bus.replay(trace_id="trace123", limit=5)
                assert len(events) == 5

    @pytest.mark.asyncio
    async def test_sqlite_bus_replay_tags(self):
        """Test replaying with tag filtering."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            async with SQLiteBus(db_path) as bus:
                # Publish events with different tags
                await bus.publish(
                    FactoryEvent(
                        event_type="test.event",
                        trace_id="trace123",
                        source="test.source",
                        tags=["tag_a", "tag_b"],
                    )
                )
                await bus.publish(
                    FactoryEvent(
                        event_type="test.event",
                        trace_id="trace123",
                        source="test.source",
                        tags=["tag_b", "tag_c"],
                    )
                )
                await bus.publish(
                    FactoryEvent(
                        event_type="test.event",
                        trace_id="trace123",
                        source="test.source",
                        tags=["tag_a", "tag_c"],
                    )
                )

                # Filter by single tag
                events_a = await bus.replay(trace_id="trace123", tags=["tag_a"])
                assert len(events_a) == 2

                # Filter by multiple tags (AND semantics)
                events_ab = await bus.replay(trace_id="trace123", tags=["tag_a", "tag_b"])
                assert len(events_ab) == 1


class TestSQLiteBusCount:
    """Test SQLiteBus count operations."""

    @pytest.mark.asyncio
    async def test_sqlite_bus_count_all(self):
        """Test counting all events."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            async with SQLiteBus(db_path) as bus:
                # Initially empty
                assert await bus.count() == 0

                # Add events
                for i in range(7):
                    await bus.publish(
                        FactoryEvent(
                            event_type="test.event",
                            trace_id=f"trace{i}",
                            source="test.source",
                        )
                    )

                assert await bus.count() == 7

    @pytest.mark.asyncio
    async def test_sqlite_bus_count_by_trace(self):
        """Test counting events by trace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            async with SQLiteBus(db_path) as bus:
                # Add events to different traces
                for i in range(5):
                    await bus.publish(
                        FactoryEvent(
                            event_type="test.event",
                            trace_id="trace_A",
                            source="test.source",
                        )
                    )
                for i in range(3):
                    await bus.publish(
                        FactoryEvent(
                            event_type="test.event",
                            trace_id="trace_B",
                            source="test.source",
                        )
                    )

                assert await bus.count(trace_id="trace_A") == 5
                assert await bus.count(trace_id="trace_B") == 3
                assert await bus.count() == 8


class TestSQLiteBusAck:
    """Test SQLiteBus acknowledgment operations."""

    @pytest.mark.asyncio
    async def test_sqlite_bus_ack(self):
        """Test acknowledging an event (no-op in SQLiteBus)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            async with SQLiteBus(db_path) as bus:
                event = FactoryEvent(
                    event_type="test.event",
                    trace_id="trace123",
                    source="test.source",
                )
                await bus.publish(event)
                await bus.ack(event)
                # Should not raise


class TestSQLiteBusTail:
    """Test SQLiteBus tail operations."""

    @pytest.mark.asyncio
    async def test_sqlite_bus_tail(self):
        """Test tailing all events."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            async with SQLiteBus(db_path) as bus:
                # Publish events
                for i in range(4):
                    await bus.publish(
                        FactoryEvent(
                            event_type="test.event",
                            trace_id="trace123",
                            source="test.source",
                            payload={"index": i},
                        )
                    )

                # Tail and collect
                received = []
                async for event in bus.tail():
                    received.append(event)
                    if len(received) == 4:
                        break

                assert len(received) == 4


class TestSQLiteBusIntegration:
    """Integration tests for SQLiteBus."""

    @pytest.mark.asyncio
    async def test_sqlite_bus_full_workflow(self):
        """Test complete workflow with persistence."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            # Session 1: Create and publish
            async with SQLiteBus(db_path) as bus1:
                root_event = FactoryEvent(
                    event_type="task.intent",
                    trace_id="workflow_trace",
                    source="user",
                    payload={"instruction": "Do something"},
                )
                root_id = await bus1.publish(root_event)

                action_event = FactoryEvent(
                    event_type="agent.action",
                    trace_id="workflow_trace",
                    parent_id=root_id,
                    source="agent.executor",
                    payload={"action": "executing"},
                    metadata=EventMetadata(tool_name="bash", duration_ms=100.0),
                )
                await bus1.publish(action_event)

            # Session 2: Read and verify (simulates restart)
            async with SQLiteBus(db_path) as bus2:
                events = await bus2.read_trace("workflow_trace")
                assert len(events) == 2
                assert events[1].parent_id == root_id
                assert events[1].metadata.tool_name == "bash"

    @pytest.mark.asyncio
    async def test_sqlite_bus_persistence_after_close(self):
        """Test that data persists after bus is closed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            # Write events
            async with SQLiteBus(db_path) as bus:
                for i in range(5):
                    await bus.publish(
                        FactoryEvent(
                            event_type="test.event",
                            trace_id="persist_trace",
                            source="test.source",
                            payload={"index": i},
                        )
                    )

            # Verify file exists
            assert resolve_unified_db_path("events.db").exists()

            # Read events in new session
            async with SQLiteBus(db_path) as bus:
                events = await bus.read_trace("persist_trace")
                assert len(events) == 5
                for i, event in enumerate(events):
                    assert event.payload["index"] == i
