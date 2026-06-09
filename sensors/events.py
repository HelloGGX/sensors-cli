"""Shared asyncio event types for cross-component signaling."""

import asyncio
from dataclasses import dataclass, field


@dataclass
class DisplayEvents:
    """Events used to signal lifecycle actions between orchestration and display."""

    clear: asyncio.Event = field(default_factory=asyncio.Event)
    shutdown: asyncio.Event = field(default_factory=asyncio.Event)
    snapshot: asyncio.Event = field(default_factory=asyncio.Event)
    #: Populated by orchestrator after runners are built; keyed by runner name.
    #: Control server uses this to trigger remote re-runs without touching the main loop.
    rerun_events: dict[str, asyncio.Event] = field(default_factory=dict)
