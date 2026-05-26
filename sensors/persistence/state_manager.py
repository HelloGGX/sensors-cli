"""State manager for persisting sensors state to disk."""

import json
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

import aiofiles

from sensors.time_util import to_utc_iso, utc_now

from .models import (
    CheckHistoryEntry,
    QueryLogEntry,
    RunnerCheckSummary,
    RunnerState,
    SensorsState,
    Snapshot,
)


def _parse_dt(s: str) -> datetime:
    """Parse ISO datetime string, stripping timezone to get naive local datetime."""
    return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)


def _hydrate_runner_states(runners: dict) -> None:
    for runner_state in runners.values():
        if "lastRun" in runner_state:
            runner_state["lastRun"] = _parse_dt(runner_state["lastRun"])
        if "result" in runner_state and "timestamp" in runner_state["result"]:
            runner_state["result"]["timestamp"] = _parse_dt(
                runner_state["result"]["timestamp"]
            )


def _hydrate_query_log(entries: list) -> None:
    for entry in entries:
        if "timestamp" in entry:
            entry["timestamp"] = _parse_dt(entry["timestamp"])


def _hydrate_snapshot(snapshot_data: dict | None) -> None:
    if not snapshot_data:
        return
    if "timestamp" in snapshot_data:
        snapshot_data["timestamp"] = _parse_dt(snapshot_data["timestamp"])
    _hydrate_runner_states(snapshot_data.get("runners", {}))


class StateManager:
    """Manages reading and writing sensors state to JSON files."""

    def __init__(self, state_file: Path | None = None, history_file: Path | None = None):
        """Initialize the state manager.

        Args:
            state_file: Path to the state JSON file. Defaults to sensors/state/current.json
        """
        if state_file is None:
            # Default to sensors/state/current.json relative to this file
            sensors_root = Path(__file__).parent.parent.parent
            state_file = sensors_root / "state" / "current.json"

        self.state_file = Path(state_file)
        # Ensure state directory exists
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        # History file sits alongside the state file when not overridden
        if history_file is None:
            history_file = self.state_file.parent / "history.jsonl"
        self.history_file = Path(history_file)

    async def reset(self) -> None:
        """Delete the state file so the sensors starts fresh."""
        if self.state_file.exists():
            self.state_file.unlink()

    async def read_state(self) -> SensorsState:
        """Read the current state from the JSON file.

        Returns:
            SensorsState object. If the file doesn't exist or is invalid,
            returns an empty state with current timestamp.
        """
        if not self.state_file.exists():
            # Return empty state if file doesn't exist
            return SensorsState(
                lastUpdated=datetime.now(),
                runners={},
                queryLog=[]
            )

        try:
            async with aiofiles.open(self.state_file) as f:
                content = await f.read()
            data = json.loads(content)

            if "lastUpdated" in data:
                data["lastUpdated"] = _parse_dt(data["lastUpdated"])
            _hydrate_runner_states(data.get("runners", {}))
            _hydrate_query_log(data.get("queryLog", []))
            _hydrate_snapshot(data.get("snapshot"))

            return SensorsState(**data)

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            # If file is corrupted, return empty state
            print(f"Warning: Failed to parse state file: {e}. Returning empty state.")
            return SensorsState(
                lastUpdated=datetime.now(),
                runners={},
                queryLog=[]
            )

    async def update_state(
        self,
        runner_name: str,
        runner_state: RunnerState
    ) -> None:
        """Update the state for a specific runner atomically.

        This method:
        1. Reads the current state
        2. Updates the specified runner's state
        3. Writes atomically using tempfile + os.replace()

        Args:
            runner_name: Name of the runner to update
            runner_state: New state for the runner
        """
        # Read current state
        current_state = await self.read_state()

        # Update the specific runner
        current_state.runners[runner_name] = runner_state
        current_state.lastUpdated = datetime.now()

        # Write atomically
        await self._write_state_atomic(current_state)

    async def save_snapshot(self) -> None:
        """Save a snapshot of the current runner states for later comparison.

        Copies the current runners dict into the snapshot field with a timestamp.
        """
        current_state = await self.read_state()

        now_local = datetime.now()
        current_state.snapshot = Snapshot(
            snapshot_id=uuid.uuid4().hex[:8],
            timestamp=now_local,
            runners=current_state.runners.copy(),
        )
        current_state.lastUpdated = now_local

        await self._write_state_atomic(current_state)

        snapshot_entry = CheckHistoryEntry(
            timestamp=utc_now(),
            runner_filter=None,
            snapshot_id=current_state.snapshot.snapshot_id,
            runners={
                name: RunnerCheckSummary(status=rs.status, score=rs.score)
                for name, rs in current_state.runners.items()
            },
        )
        await self.append_check_history(snapshot_entry)

    async def log_query(
        self,
        command: str,
        runner: str | None = None
    ) -> None:
        """Log a query to the state file.

        This method:
        1. Reads the current state
        2. Adds a new query log entry
        3. Keeps only the latest 5 entries
        4. Writes atomically

        Args:
            command: The command that was executed (status, failures, check)
            runner: Runner filter if specified
        """
        # Read current state
        current_state = await self.read_state()

        # Create new log entry
        entry = QueryLogEntry(
            timestamp=datetime.now(),
            command=command,
            runner=runner
        )

        # Add to the front of the list
        current_state.queryLog.insert(0, entry)

        # Keep only latest 5 entries
        current_state.queryLog = current_state.queryLog[:5]

        # Write atomically
        await self._write_state_atomic(current_state)

    async def append_check_history(self, entry: CheckHistoryEntry) -> None:
        """Append one check history record to the history JSONL file.

        Each line is a compact JSON object representing the state of all
        runners at the moment a 'check' command was executed.

        Args:
            entry: The CheckHistoryEntry to persist.
        """
        current_state = await self.read_state()
        snapshot_id = (
            current_state.snapshot.snapshot_id if current_state.snapshot else entry.snapshot_id
        )
        record = entry.model_dump(mode="json")
        record["timestamp"] = to_utc_iso(entry.timestamp)
        if snapshot_id is not None:
            record["snapshot_id"] = snapshot_id
        else:
            record.pop("snapshot_id", None)
        line = json.dumps(record, separators=(",", ":")) + "\n"
        async with aiofiles.open(self.history_file, "a") as f:
            await f.write(line)

    async def _write_state_atomic(self, state: SensorsState) -> None:
        """Write state to disk atomically using tempfile + os.replace().

        Args:
            state: The SensorsState to write
        """
        # Convert to JSON with proper datetime serialization
        state_dict = state.model_dump(mode="json")

        # Convert datetime objects to ISO format strings (no Z suffix for local/naive datetimes)
        def to_iso_z(dt: datetime) -> str:
            s = dt.isoformat()
            if dt.tzinfo is None:
                return s  # local naive datetime, no timezone suffix
            return s.replace("+00:00", "Z")

        state_dict["lastUpdated"] = to_iso_z(state.lastUpdated)
        for runner_name, runner_state in state_dict["runners"].items():
            runner_state["lastRun"] = to_iso_z(state.runners[runner_name].lastRun)

        # Convert query log timestamps
        for i, entry in enumerate(state_dict["queryLog"]):
            entry["timestamp"] = to_iso_z(state.queryLog[i].timestamp)

        # Convert snapshot timestamps
        if state.snapshot and state_dict.get("snapshot"):
            state_dict["snapshot"]["timestamp"] = to_iso_z(state.snapshot.timestamp)
            for runner_name, runner_state in state_dict["snapshot"]["runners"].items():
                runner_state["lastRun"] = to_iso_z(state.snapshot.runners[runner_name].lastRun)

        json_content = json.dumps(state_dict, indent=2)

        # Write to temporary file in the same directory
        # This ensures atomic replacement on the same filesystem
        temp_fd, temp_path = tempfile.mkstemp(
            dir=self.state_file.parent,
            prefix=".current.json.",
            suffix=".tmp"
        )

        try:
            # Write content to temp file using aiofiles
            async with aiofiles.open(temp_path, "w") as f:
                await f.write(json_content)

            # Ensure data is flushed to disk
            os.fsync(temp_fd)

            # Atomically replace the old file with the new one
            os.replace(temp_path, self.state_file)

        finally:
            # Close the file descriptor
            os.close(temp_fd)
            # Clean up temp file if it still exists (e.g., if os.replace failed)
            if os.path.exists(temp_path):
                os.unlink(temp_path)
