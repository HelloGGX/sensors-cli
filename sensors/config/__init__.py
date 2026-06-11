"""Configuration module for sensors."""

from .loader import ConfigLoadError, load_config, load_config_sync
from .result_types import (
    Finding,
    Formatted,
    GuidanceBlock,
    Metric,
    RunnerResult,
    ScoreInfo,
    SensorReading,
)
from .schema import RunnerConfig, RunnerMode, SensorsConfig

__all__ = [
    "ConfigLoadError",
    "Finding",
    "Formatted",
    "GuidanceBlock",
    "load_config",
    "load_config_sync",
    "Metric",
    "SensorReading",
    "RunnerResult",
    "ScoreInfo",
    "SensorsConfig",
    "RunnerConfig",
    "RunnerMode",
]
