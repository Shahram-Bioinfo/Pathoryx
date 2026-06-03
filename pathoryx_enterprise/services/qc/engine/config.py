from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


@dataclass(slots=True)
class PathsConfig:
    output_root: Path
    quarantine_root: Path | None = None
    final_root: Path | None = None
    failed_root: Path | None = None


@dataclass(slots=True)
class PipelineConfig:
    pipeline_name: str = "slide_qc_service"
    target_system: str = "babelshark_precheck"
    service_name: str = "qc"


@dataclass(slots=True)
class WatcherConfig:
    input_dirs: list[str] = field(default_factory=list)
    poll_interval_seconds: int = 10
    recursive: bool = True
    stable_file_wait_seconds: int = 20
    allowed_extensions: list[str] = field(
        default_factory=lambda: [".svs", ".tif", ".tiff", ".ndpi", ".mrxs", ".vms"]
    )


@dataclass(slots=True)
class ModelConfig:
    penmark_weights: str
    bubble_weights: str
    stain_weights: str
    blur_weights: str


@dataclass(slots=True)
class ModuleConfig:
    enable_stain: bool = True
    enable_penmark: bool = True
    enable_bubble: bool = False
    enable_blur: bool = True
    enable_sharpness: bool = True


@dataclass(slots=True)
class ParameterConfig:
    thumb_size: int = 1024
    patch_size: int = 224
    stride: int = 112
    batch_size: int = 16


@dataclass(slots=True)
class ThresholdConfig:
    penmark_threshold: float = 0.01
    bubble_threshold: float = 0.02
    min_tissue_ratio: float = 0.03
    sat_threshold: float = 0.01


@dataclass(slots=True)
class ArtifactConfig:
    save_csv: bool = True
    save_visualizations: bool = True


@dataclass(slots=True)
class ProcessingConfig:
    force_reprocess: bool = False
    quarantine_failed_inputs: bool = False


@dataclass(slots=True)
class LoggingConfig:
    level: str = "INFO"


@dataclass(slots=True)
class PostgresConfig:
    url: str


@dataclass(slots=True)
class DecisionConfig:
    blur_fail_threshold: float = 0.10
    route_passed_to_final: bool = True
    route_failed_to_quarantine: bool = True
    copy_instead_of_move: bool = True
    force_fail_for_testing: bool = False


@dataclass(slots=True)
class AppConfig:
    paths: PathsConfig
    pipeline: PipelineConfig
    watcher: WatcherConfig
    models: ModelConfig
    modules: ModuleConfig
    parameters: ParameterConfig
    thresholds: ThresholdConfig
    artifacts: ArtifactConfig
    processing: ProcessingConfig
    logging: LoggingConfig
    postgres: PostgresConfig
    decision: DecisionConfig


def load_config(config_path: str | os.PathLike[str]) -> AppConfig:
    with open(config_path, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    raw = _expand_env(raw)

    paths_raw = raw.get("paths", {})
    paths = PathsConfig(
        output_root=Path(paths_raw["output_root"]).resolve(),
        quarantine_root=Path(paths_raw["quarantine_root"]).resolve() if paths_raw.get("quarantine_root") else None,
        final_root=Path(paths_raw["final_root"]).resolve() if paths_raw.get("final_root") else None,
        failed_root=Path(paths_raw["failed_root"]).resolve() if paths_raw.get("failed_root") else None,
    )

    pipeline = PipelineConfig(**raw.get("pipeline", {}))
    watcher = WatcherConfig(**raw.get("watcher", {}))
    models = ModelConfig(**raw.get("models", {}))
    modules = ModuleConfig(**raw.get("modules", {}))
    parameters = ParameterConfig(**raw.get("parameters", {}))
    thresholds = ThresholdConfig(**raw.get("thresholds", {}))
    artifacts = ArtifactConfig(**raw.get("artifacts", {}))
    processing = ProcessingConfig(**raw.get("processing", {}))
    logging_cfg = LoggingConfig(**raw.get("logging", {}))
    decision = DecisionConfig(**raw.get("decision", {}))

    postgres_url = raw.get("postgres", {}).get("url") or os.environ.get("DATABASE_URL")
    if not postgres_url:
        raise ValueError("postgres.url or DATABASE_URL is required")

    postgres = PostgresConfig(url=postgres_url)

    return AppConfig(
        paths=paths,
        pipeline=pipeline,
        watcher=watcher,
        models=models,
        modules=modules,
        parameters=parameters,
        thresholds=thresholds,
        artifacts=artifacts,
        processing=processing,
        logging=logging_cfg,
        postgres=postgres,
        decision=decision,
    )