"""Tests for protocol/events.py - FactoryEvent and EventMetadata"""

import pytest
from datetime import datetime, timezone

from omnicompany.protocol.events import EventMetadata, FactoryEvent


class TestEventMetadata:
    """Test EventMetadata model."""

    def test_metadata_empty(self):
        """Test creating empty metadata."""
        metadata = EventMetadata()
        assert metadata.prompt_tokens is None
        assert metadata.completion_tokens is None
        assert metadata.model is None
        assert metadata.cost_usd is None
        assert metadata.latency_ms is None
        assert metadata.tool_name is None
        assert metadata.duration_ms is None
        assert metadata.extra == {}

    def test_metadata_llm_fields(self):
        """Test metadata with LLM-related fields."""
        metadata = EventMetadata(
            prompt_tokens=100,
            completion_tokens=50,
            model="gpt-4",
            cost_usd=0.002,
            latency_ms=150.5,
        )
        assert metadata.prompt_tokens == 100
        assert metadata.completion_tokens == 50
        assert metadata.model == "gpt-4"
        assert metadata.cost_usd == 0.002
        assert metadata.latency_ms == 150.5

    def test_metadata_tool_fields(self):
        """Test metadata with tool-related fields."""
        metadata = EventMetadata(
            tool_name="bash",
            duration_ms=25.3,
        )
        assert metadata.tool_name == "bash"
        assert metadata.duration_ms == 25.3

    def test_metadata_extra_dict(self):
        """Test metadata extra field."""
        metadata = EventMetadata(extra={"custom_key": "custom_value", "count": 42})
        assert metadata.extra["custom_key"] == "custom_value"
        assert metadata.extra["count"] == 42

    def test_metadata_mixed_fields(self):
        """Test metadata with mixed LLM and tool fields."""
        metadata = EventMetadata(
            prompt_tokens=200,
            model="claude-3",
            tool_name="str_replace_editor",
            duration_ms=10.0,
            extra={"retry_count": 2},
        )
        assert metadata.prompt_tokens == 200
        assert metadata.model == "claude-3"
        assert metadata.tool_name == "str_replace_editor"
        assert metadata.duration_ms == 10.0
        assert metadata.extra["retry_count"] == 2


class TestFactoryEvent:
    """Test FactoryEvent model."""

    def test_event_minimal(self):
        """Test creating event with minimal required fields."""
        event = FactoryEvent(
            event_type="test.event",
            trace_id="trace123",
            source="test.source",
        )
        assert event.event_type == "test.event"
        assert event.trace_id == "trace123"
        assert event.source == "test.source"
        assert event.parent_id is None
        assert event.payload == {}
        assert event.contract_id is None
        assert event.tags == []
        assert event.metadata is None
        assert isinstance(event.timestamp, datetime)

    def test_event_with_parent(self):
        """Test event with parent_id for causal chain."""
        event = FactoryEvent(
            event_type="agent.tool.call",
            trace_id="trace123",
            parent_id="event456",
            source="agent.coder",
        )
        assert event.parent_id == "event456"

    def test_event_with_payload(self):
        """Test event with payload data."""
        payload = {"instruction": "Write a function", "constraints": {"max_lines": 50}}
        event = FactoryEvent(
            event_type="task.intent",
            trace_id="trace123",
            source="user",
            payload=payload,
        )
        assert event.payload == payload
        assert event.payload["instruction"] == "Write a function"

    def test_event_with_contract_id(self):
        """Test event with contract_id for LGF association."""
        event = FactoryEvent(
            event_type="agent.thought",
            trace_id="trace123",
            source="agent.planner",
            contract_id="node_42",
        )
        assert event.contract_id == "node_42"

    def test_event_with_tags(self):
        """Test event with semantic tags."""
        tags = ["demogame.benchmark.battle", "unity.lua", "hero.greed"]
        event = FactoryEvent(
            event_type="agent.action",
            trace_id="trace123",
            source="agent.executor",
            tags=tags,
        )
        assert event.tags == tags
        assert "demogame.benchmark.battle" in event.tags

    def test_event_with_metadata(self):
        """Test event with metadata."""
        metadata = EventMetadata(
            prompt_tokens=150,
            completion_tokens=75,
            model="gpt-4",
            cost_usd=0.003,
            latency_ms=200.0,
        )
        event = FactoryEvent(
            event_type="agent.llm.response",
            trace_id="trace123",
            source="agent.coder",
            metadata=metadata,
        )
        assert event.metadata is not None
        assert event.metadata.prompt_tokens == 150
        assert event.metadata.model == "gpt-4"

    def test_event_timestamp_default(self):
        """Test that timestamp is set to current UTC time by default."""
        event = FactoryEvent(
            event_type="test.event",
            trace_id="trace123",
            source="test",
        )
        assert event.timestamp.tzinfo == timezone.utc
        # Should be very recent (within last second)
        now = datetime.now(timezone.utc)
        diff = abs((now - event.timestamp).total_seconds())
        assert diff < 1.0

    def test_event_id_generation(self):
        """Test that event ID is generated."""
        event = FactoryEvent(
            event_type="test.event",
            trace_id="trace123",
            source="test",
        )
        assert event.id is not None
        assert len(event.id) > 0  # ULID format

    def test_event_full_featured(self):
        """Test event with all fields populated."""
        metadata = EventMetadata(
            prompt_tokens=100,
            completion_tokens=50,
            model="gpt-4",
            cost_usd=0.002,
            latency_ms=150.0,
            tool_name="bash",
            duration_ms=25.0,
            extra={"attempt": 1},
        )
        event = FactoryEvent(
            id="custom_id_123",
            event_type="agent.tool.complete",
            trace_id="trace_main",
            parent_id="event_parent",
            source="agent.worker",
            payload={"tool": "bash", "output": "success"},
            contract_id="contract_789",
            tags=["tool.bash", "status.success"],
            metadata=metadata,
        )
        assert event.id == "custom_id_123"
        assert event.event_type == "agent.tool.complete"
        assert event.trace_id == "trace_main"
        assert event.parent_id == "event_parent"
        assert event.source == "agent.worker"
        assert event.payload["tool"] == "bash"
        assert event.contract_id == "contract_789"
        assert "tool.bash" in event.tags
        assert event.metadata.cost_usd == 0.002


class TestEventSerialization:
    """Test event serialization and deserialization."""

    def test_event_to_stream_dict(self):
        """Test serialization to Redis Streams format."""
        event = FactoryEvent(
            event_type="test.event",
            trace_id="trace123",
            source="test.source",
            payload={"key": "value"},
            tags=["tag1", "tag2"],
        )
        stream_dict = event.to_stream_dict()
        assert "data" in stream_dict
        assert isinstance(stream_dict["data"], str)

    def test_event_from_stream_dict(self):
        """Test deserialization from Redis Streams format."""
        original = FactoryEvent(
            event_type="test.event",
            trace_id="trace123",
            source="test.source",
            payload={"key": "value", "number": 42},
            tags=["tag1"],
        )
        stream_dict = original.to_stream_dict()
        # Simulate Redis returning bytes
        bytes_dict = {k.encode(): v.encode() for k, v in stream_dict.items()}
        restored = FactoryEvent.from_stream_dict(bytes_dict)
        assert restored.event_type == original.event_type
        assert restored.trace_id == original.trace_id
        assert restored.source == original.source
        assert restored.payload == original.payload
        assert restored.tags == original.tags

    def test_event_json_round_trip(self):
        """Test JSON serialization and deserialization."""
        original = FactoryEvent(
            event_type="agent.thought",
            trace_id="trace_complex",
            parent_id="event_001",
            source="agent.planner",
            payload={"thought": "Let me think about this...", "step": 3},
            contract_id="node_15",
            tags=["reasoning", "planning"],
            metadata=EventMetadata(
                prompt_tokens=500,
                completion_tokens=200,
                model="gpt-4",
                cost_usd=0.01,
                latency_ms=350.0,
            ),
        )
        json_str = original.model_dump_json()
        restored = FactoryEvent.model_validate_json(json_str)
        assert restored.event_type == original.event_type
        assert restored.trace_id == original.trace_id
        assert restored.payload == original.payload
        assert restored.contract_id == original.contract_id
        assert restored.tags == original.tags
        assert restored.metadata.prompt_tokens == original.metadata.prompt_tokens


class TestEventCausalChain:
    """Test causal chain tracking in events."""

    def test_causal_chain_creation(self):
        """Test creating a chain of causally-related events."""
        root = FactoryEvent(
            event_type="task.intent",
            trace_id="trace_main",
            source="user",
            payload={"instruction": "Solve the problem"},
        )

        thought1 = FactoryEvent(
            event_type="agent.thought",
            trace_id="trace_main",
            parent_id=root.id,
            source="agent.planner",
            payload={"thought": "First, I need to understand the problem"},
        )

        tool_call = FactoryEvent(
            event_type="agent.tool.call",
            trace_id="trace_main",
            parent_id=thought1.id,
            source="agent.executor",
            payload={"tool": "bash", "command": "ls -la"},
        )

        # Verify causal chain
        assert thought1.parent_id == root.id
        assert tool_call.parent_id == thought1.id
        assert tool_call.trace_id == root.trace_id

    def test_same_trace_different_branches(self):
        """Test multiple branches within same trace."""
        root = FactoryEvent(
            event_type="task.intent",
            trace_id="trace_parallel",
            source="user",
        )

        branch1_step1 = FactoryEvent(
            event_type="agent.action",
            trace_id="trace_parallel",
            parent_id=root.id,
            source="agent.worker1",
        )

        branch2_step1 = FactoryEvent(
            event_type="agent.action",
            trace_id="trace_parallel",
            parent_id=root.id,
            source="agent.worker2",
        )

        # Both branches have same root parent
        assert branch1_step1.parent_id == root.id
        assert branch2_step1.parent_id == root.id
        assert branch1_step1.trace_id == branch2_step1.trace_id


class TestEventTags:
    """Test semantic tag functionality."""

    def test_empty_tags_default(self):
        """Test that tags default to empty list."""
        event = FactoryEvent(
            event_type="test.event",
            trace_id="trace123",
            source="test",
        )
        assert event.tags == []

    def test_tag_hierarchy(self):
        """Test hierarchical tag naming."""
        event = FactoryEvent(
            event_type="benchmark.run",
            trace_id="trace123",
            source="benchmark.runner",
            tags=["demogame.benchmark.battle", "unity.lua", "hero.greed"],
        )
        # Tags use dot notation for hierarchy
        assert "demogame.benchmark.battle" in event.tags
        assert "unity.lua" in event.tags
        assert "hero.greed" in event.tags

    def test_tag_filtering_simulation(self):
        """Test tag-based filtering logic."""
        events = [
            FactoryEvent(
                event_type="agent.action",
                trace_id=f"trace{i}",
                source="agent",
                tags=["tool.bash", "status.success"],
            )
            for i in range(3)
        ]
        events.append(
            FactoryEvent(
                event_type="agent.action",
                trace_id="trace3",
                source="agent",
                tags=["tool.str_replace", "status.success"],
            )
        )

        # Filter by tag
        bash_events = [e for e in events if "tool.bash" in e.tags]
        assert len(bash_events) == 3

        success_events = [e for e in events if "status.success" in e.tags]
        assert len(success_events) == 4
