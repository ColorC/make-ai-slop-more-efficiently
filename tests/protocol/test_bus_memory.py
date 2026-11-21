"""Tests for bus/memory.py - MemoryBus implementation"""

import pytest
import asyncio

from omnicompany.bus.memory import MemoryBus
from omnicompany.protocol.events import FactoryEvent, EventMetadata


class TestMemoryBusBasic:
    """Test basic MemoryBus operations."""

    def test_memory_bus_init(self):
        """Test MemoryBus initialization."""
        bus = MemoryBus()
        assert bus._events == []
        assert bus._connected is False

    @pytest.mark.asyncio
    async def test_memory_bus_connect(self):
        """Test MemoryBus connect."""
        bus = MemoryBus()
        await bus.connect()
        assert bus._connected is True

    @pytest.mark.asyncio
    async def test_memory_bus_close(self):
        """Test MemoryBus close."""
        bus = MemoryBus()
        await bus.connect()
        await bus.close()
        assert bus._connected is False

    @pytest.mark.asyncio
    async def test_memory_bus_context_manager(self):
        """Test MemoryBus as async context manager."""
        bus = MemoryBus()
        async with bus:
            assert bus._connected is True
        assert bus._connected is False


class TestMemoryBusPublish:
    """Test MemoryBus publish operations."""

    @pytest.mark.asyncio
    async def test_memory_bus_publish(self):
        """Test publishing an event."""
        bus = MemoryBus()
        await bus.connect()

        event = FactoryEvent(
            event_type="test.event",
            trace_id="trace123",
            source="test.source",
            payload={"key": "value"},
        )

        event_id = await bus.publish(event)
        assert event_id == event.id
        assert len(bus._events) == 1
        assert bus._events[0] == event

    @pytest.mark.asyncio
    async def test_memory_bus_publish_multiple(self):
        """Test publishing multiple events."""
        bus = MemoryBus()
        await bus.connect()

        events = []
        for i in range(5):
            event = FactoryEvent(
                event_type="test.event",
                trace_id=f"trace{i}",
                source="test.source",
                payload={"index": i},
            )
            events.append(event)
            await bus.publish(event)

        assert len(bus._events) == 5
        for i, event in enumerate(bus._events):
            assert event.payload["index"] == i


class TestMemoryBusSubscribe:
    """Test MemoryBus subscribe operations."""

    @pytest.mark.asyncio
    async def test_memory_bus_subscribe(self):
        """Test subscribing to events."""
        bus = MemoryBus()
        await bus.connect()

        # Publish some events
        for i in range(3):
            event = FactoryEvent(
                event_type="test.event",
                trace_id="trace123",
                source="test.source",
                payload={"index": i},
            )
            await bus.publish(event)

        # Subscribe and collect
        received = []
        async for event in bus.subscribe("test_group", "consumer1"):
            received.append(event)

        assert len(received) == 3
        for i, event in enumerate(received):
            assert event.payload["index"] == i

    @pytest.mark.asyncio
    async def test_memory_bus_subscribe_empty(self):
        """Test subscribing when no events exist."""
        bus = MemoryBus()
        await bus.connect()

        received = []
        async for event in bus.subscribe("test_group", "consumer1"):
            received.append(event)

        assert len(received) == 0


class TestMemoryBusAck:
    """Test MemoryBus acknowledgment operations."""

    @pytest.mark.asyncio
    async def test_memory_bus_ack(self):
        """Test acknowledging an event (no-op in MemoryBus)."""
        bus = MemoryBus()
        await bus.connect()

        event = FactoryEvent(
            event_type="test.event",
            trace_id="trace123",
            source="test.source",
        )
        await bus.publish(event)

        # ack should not raise
        await bus.ack(event)


class TestMemoryBusReadTrace:
    """Test MemoryBus trace reading operations."""

    @pytest.mark.asyncio
    async def test_memory_bus_read_trace(self):
        """Test reading events by trace_id."""
        bus = MemoryBus()
        await bus.connect()

        # Publish events with different traces
        for i in range(3):
            await bus.publish(
                FactoryEvent(
                    event_type="test.event",
                    trace_id="trace_main",
                    source="test.source",
                    payload={"index": i},
                )
            )

        for i in range(2):
            await bus.publish(
                FactoryEvent(
                    event_type="test.event",
                    trace_id="trace_other",
                    source="test.source",
                    payload={"index": i},
                )
            )

        # Read trace_main
        events = await bus.read_trace("trace_main")
        assert len(events) == 3
        for i, event in enumerate(events):
            assert event.payload["index"] == i

        # Read trace_other
        events = await bus.read_trace("trace_other")
        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_memory_bus_read_trace_empty(self):
        """Test reading non-existent trace."""
        bus = MemoryBus()
        await bus.connect()

        events = await bus.read_trace("nonexistent")
        assert len(events) == 0


class TestMemoryBusTail:
    """Test MemoryBus tail operations."""

    @pytest.mark.asyncio
    async def test_memory_bus_tail(self):
        """Test tailing all events."""
        bus = MemoryBus()
        await bus.connect()

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

        assert len(received) == 4
        for i, event in enumerate(received):
            assert event.payload["index"] == i


class TestMemoryBusIntegration:
    """Integration tests for MemoryBus."""

    @pytest.mark.asyncio
    async def test_memory_bus_full_workflow(self):
        """Test complete publish-subscribe workflow."""
        bus = MemoryBus()
        await bus.connect()

        # Create a trace with multiple related events
        root_event = FactoryEvent(
            event_type="task.intent",
            trace_id="workflow_trace",
            source="user",
            payload={"instruction": "Do something"},
        )
        root_id = await bus.publish(root_event)

        thought_event = FactoryEvent(
            event_type="agent.thought",
            trace_id="workflow_trace",
            parent_id=root_id,
            source="agent.planner",
            payload={"thought": "Thinking..."},
        )
        thought_id = await bus.publish(thought_event)

        action_event = FactoryEvent(
            event_type="agent.action",
            trace_id="workflow_trace",
            parent_id=thought_id,
            source="agent.executor",
            payload={"action": "executing"},
            metadata=EventMetadata(tool_name="bash", duration_ms=100.0),
        )
        await bus.publish(action_event)

        # Verify all events in trace
        trace_events = await bus.read_trace("workflow_trace")
        assert len(trace_events) == 3

        # Verify causal chain
        assert trace_events[1].parent_id == root_id
        assert trace_events[2].parent_id == thought_id

        # Verify metadata preserved
        assert trace_events[2].metadata.tool_name == "bash"
        assert trace_events[2].metadata.duration_ms == 100.0

    @pytest.mark.asyncio
    async def test_memory_bus_multiple_traces(self):
        """Test managing multiple independent traces."""
        bus = MemoryBus()
        await bus.connect()

        # Create two independent traces
        for trace_id in ["trace_A", "trace_B"]:
            for i in range(3):
                await bus.publish(
                    FactoryEvent(
                        event_type="test.event",
                        trace_id=trace_id,
                        source="test.source",
                        payload={"trace": trace_id, "index": i},
                    )
                )

        # Verify separation
        trace_a = await bus.read_trace("trace_A")
        trace_b = await bus.read_trace("trace_B")

        assert len(trace_a) == 3
        assert len(trace_b) == 3

        assert all(e.payload["trace"] == "trace_A" for e in trace_a)
        assert all(e.payload["trace"] == "trace_B" for e in trace_b)

    @pytest.mark.asyncio
    async def test_memory_bus_lifecycle(self):
        """Test bus lifecycle management."""
        bus = MemoryBus()

        # Not connected initially
        assert bus._connected is False

        # Connect
        await bus.connect()
        assert bus._connected is True

        # Publish while connected
        event = FactoryEvent(
            event_type="test.event",
            trace_id="trace123",
            source="test.source",
        )
        await bus.publish(event)
        assert len(bus._events) == 1

        # Close
        await bus.close()
        assert bus._connected is False

        # Events still in memory (not cleared on close)
        assert len(bus._events) == 1
