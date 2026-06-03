from __future__ import annotations

import shutil
from pathlib import Path

from pathoryx_enterprise.services.qc.engine.config import AppConfig
from pathoryx_enterprise.services.qc.engine.domain.results import SlideQCResult


class SlideQcDecisionService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def decide(self, inference_result: SlideQCResult, source_path: str | Path) -> dict:
        source = Path(source_path).resolve()

        blur_values = inference_result.blur_result.values if inference_result.blur_result else {}
        blur_flag = int(blur_values.get("blur_flag", 0) or 0)
        blur_ratio = float(blur_values.get("blur_ratio", 0.0) or 0.0)

        threshold = float(self.config.decision.blur_fail_threshold)

        if blur_flag == 1 and blur_ratio >= threshold:
            decision_status = "failed"
            decision_reason = "blur_ratio_above_threshold"
            routed_path = self._route_failed(source)
        elif blur_flag == 1 and blur_ratio < threshold:
            decision_status = "passed"
            decision_reason = "blur_within_tolerance"
            routed_path = self._route_passed(source)
        else:
            decision_status = "passed"
            decision_reason = "no_blur"
            routed_path = self._route_passed(source)

        return {
            "decision_status": decision_status,
            "decision_reason": decision_reason,
            "decision_threshold_json": {
                "blur_fail_threshold": threshold,
                "blur_flag": blur_flag,
                "blur_ratio": blur_ratio,
            },
            "final_routed_path": str(routed_path) if routed_path else None,
        }

    def _route_passed(self, source: Path) -> Path | None:
        if not self.config.decision.route_passed_to_final:
            return None
        return self._copy_or_move(source, self.config.paths.final_root)

    def _route_failed(self, source: Path) -> Path | None:
        if not self.config.decision.route_failed_to_quarantine:
            return None
        return self._copy_or_move(source, self.config.paths.failed_root)

    def _copy_or_move(self, source: Path, target_root: Path | None) -> Path | None:
        if target_root is None:
            return None

        target_root.mkdir(parents=True, exist_ok=True)
        destination = target_root / source.name

        if destination.exists():
            return destination

        if self.config.decision.copy_instead_of_move:
            shutil.copy2(source, destination)
        else:
            shutil.move(str(source), str(destination))

        return destination


QcDecisionService = SlideQcDecisionService