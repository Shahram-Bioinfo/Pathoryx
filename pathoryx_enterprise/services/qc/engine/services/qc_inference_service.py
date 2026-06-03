from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image

from pathoryx_enterprise.services.qc.engine.config import AppConfig
from pathoryx_enterprise.services.qc.engine.domain.enums import SlideQCStatus
from pathoryx_enterprise.services.qc.engine.domain.results import QCModuleResult, SlideQCResult
from pathoryx_enterprise.services.qc.engine.modules.sharpness_model import run_blur_detection
from pathoryx_enterprise.services.qc.engine.services.model_registry import ModelRegistry
from pathoryx_enterprise.services.qc.engine.services.thumbnail_service import ThumbnailService
from pathoryx_enterprise.services.qc.engine.services.visualization_service import VisualizationService


class GradCAM:
    def __init__(self, model, target_layer):
        self.model = model
        self.target_layer = target_layer
        self.gradients = None
        self.activations = None
        self._register_hooks()

    def _register_hooks(self):
        def forward_hook(module, _input, output):
            self.activations = output

        def backward_hook(module, _grad_input, grad_output):
            self.gradients = grad_output[0]

        self.target_layer.register_forward_hook(forward_hook)
        self.target_layer.register_full_backward_hook(backward_hook)

    def __call__(self, x, class_idx):
        self.model.zero_grad()
        output = self.model(x)
        target = output[:, class_idx]
        target.backward(retain_graph=True)

        gradients = self.gradients
        activations = self.activations

        weights = gradients.mean(dim=(2, 3), keepdim=True)
        cam = (weights * activations).sum(dim=1, keepdim=True)
        cam = torch.relu(cam)
        cam = cam.squeeze().detach().cpu().numpy()

        return (cam - cam.min()) / (cam.max() - cam.min() + 1e-8)


class SlideQcInferenceService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.models = ModelRegistry(config)
        self.thumbs = ThumbnailService(config)
        self.viz = VisualizationService()
        self._penmark_gradcam = None

    def _preprocess(self, thumbnail_np: np.ndarray) -> torch.Tensor:
        img = Image.fromarray(thumbnail_np).resize((224, 224))
        tensor = self.models.normalize(img).unsqueeze(0).to(self.models.device)
        return tensor

    def _penmark_gradcam_instance(self):
        if self._penmark_gradcam is None and self.models.penmark_model is not None:
            self._penmark_gradcam = GradCAM(
                self.models.penmark_model,
                self.models.penmark_model.model.features[-1],
            )
        return self._penmark_gradcam

    def process_slide(self, source_path: str | Path) -> SlideQCResult:
        source = Path(source_path).resolve()
        checksum = None
        return self.run(
            wsi_path=source,
            output_root=self.config.paths.output_root,
            checksum=checksum,
        )

    def run(
        self,
        wsi_path: str | Path,
        output_root: Path,
        checksum: str | None,
    ) -> SlideQCResult:
        start_total = time.time()
        source = Path(wsi_path).resolve()

        slide = self.thumbs.open_slide(source)
        thumbnail = self.thumbs.get_thumbnail(slide)
        thumbnail_2048 = self.thumbs.get_thumbnail_2048(slide)

        per_slide_out = output_root / source.stem / source.stem
        per_slide_out.mkdir(parents=True, exist_ok=True)

        stain_result = self._run_stain(thumbnail, per_slide_out)
        penmark_result = self._run_penmark(thumbnail, per_slide_out)
        bubble_result = self._run_bubble(thumbnail, per_slide_out)
        blur_result = self._run_blur(thumbnail_2048, per_slide_out)

        summary = {
            "wsi_name": source.name,
            "stain_type": stain_result.values.get("label") if stain_result else None,
            "penmark_flag": penmark_result.values.get("flag") if penmark_result else None,
            "bubble_flag": bubble_result.values.get("flag") if bubble_result else None,
            "blur_flag": blur_result.values.get("blur_flag") if blur_result else None,
            "blur_ratio": blur_result.values.get("blur_ratio") if blur_result else None,
        }

        return SlideQCResult(
            status=SlideQCStatus.completed,
            source_path=source,
            source_checksum=checksum,
            total_duration_seconds=time.time() - start_total,
            stain_result=stain_result,
            penmark_result=penmark_result,
            bubble_result=bubble_result,
            blur_result=blur_result,
            summary=summary,
        )

    def _run_stain(self, thumbnail_np: np.ndarray, out_dir: Path) -> QCModuleResult | None:
        if not self.config.modules.enable_stain or self.models.stain_model is None:
            return None

        t0 = time.time()
        tensor = self._preprocess(thumbnail_np)

        with torch.no_grad():
            outputs = self.models.stain_model(tensor)
            probs = torch.softmax(outputs, dim=1)
            pred = torch.argmax(probs, dim=1).item()
            prob = probs[0, pred].item()

        label = "Hist" if pred == 0 else "IHC"
        artifacts: list[str] = []

        if self.config.artifacts.save_visualizations:
            stain_dir = out_dir / "stain" / label
            stain_dir.mkdir(parents=True, exist_ok=True)
            artifacts.append(
                self.viz.save_pil_jpeg(
                    stain_dir / f"{out_dir.name}_thumb.jpg",
                    thumbnail_np,
                )
            )

        return QCModuleResult(
            "stain",
            True,
            {"label": label, "probability": prob},
            time.time() - t0,
            artifacts,
        )

    def _run_penmark(self, thumbnail_np: np.ndarray, out_dir: Path) -> QCModuleResult | None:
        if not self.config.modules.enable_penmark or self.models.penmark_model is None:
            return None

        t0 = time.time()
        tensor = self._preprocess(thumbnail_np)
        tensor.requires_grad = True

        logits = self.models.penmark_model(tensor)
        probs = torch.softmax(logits, dim=1)
        prob = probs[0, 1].item()
        flag = 1 if prob >= self.config.thresholds.penmark_threshold else 0

        artifacts: list[str] = []

        if self.config.artifacts.save_visualizations:
            pen_dir = out_dir / "penmark"
            pen_dir.mkdir(parents=True, exist_ok=True)

            artifacts.append(self.viz.save_rgb(pen_dir / "thumbnail.png", thumbnail_np))

            gradcam = self._penmark_gradcam_instance()
            if gradcam is not None:
                cam = gradcam(tensor, class_idx=1)
                h, w, _ = thumbnail_np.shape
                cam_resized = cv2.resize(cam, (w, h))
                heatmap = cv2.applyColorMap((cam_resized * 255).astype(np.uint8), cv2.COLORMAP_JET)
                overlay = cv2.addWeighted(thumbnail_np, 0.6, heatmap, 0.4, 0)
                artifacts.append(self.viz.save_rgb(pen_dir / "gradcam_overlay.png", overlay))

        return QCModuleResult(
            "penmark",
            True,
            {"flag": flag, "probability": prob},
            time.time() - t0,
            artifacts,
        )

    def _run_bubble(self, thumbnail_np: np.ndarray, out_dir: Path) -> QCModuleResult | None:
        if not self.config.modules.enable_bubble or self.models.bubble_model is None:
            return None

        t0 = time.time()
        patches: list[tuple[int, int, np.ndarray]] = []
        h, w, _ = thumbnail_np.shape

        for y in range(0, h - self.config.parameters.patch_size, self.config.parameters.stride):
            for x in range(0, w - self.config.parameters.patch_size, self.config.parameters.stride):
                patch = thumbnail_np[
                    y:y + self.config.parameters.patch_size,
                    x:x + self.config.parameters.patch_size,
                ]
                patches.append((x, y, patch))

        bubble_count = 0
        artifacts: list[str] = []

        bubble_dir = out_dir / "bubble"
        if self.config.artifacts.save_visualizations:
            bubble_dir.mkdir(parents=True, exist_ok=True)

        for i, (_x, _y, patch) in enumerate(patches):
            img = Image.fromarray(patch).resize((224, 224))
            tensor = torch.tensor(np.array(img)).permute(2, 0, 1).float().unsqueeze(0) / 255.0
            tensor = tensor.to(self.models.device)

            with torch.no_grad():
                logits = self.models.bubble_model(tensor)
                probs = torch.softmax(logits, dim=1)
                prob = probs[0, 1].item()

            if prob >= self.config.thresholds.bubble_threshold:
                bubble_count += 1
                if self.config.artifacts.save_visualizations:
                    artifacts.append(
                        self.viz.save_rgb(
                            bubble_dir / f"patch_{i}_prob_{prob:.3f}.png",
                            patch,
                        )
                    )

        flag = 1 if bubble_count > 0 else 0

        return QCModuleResult(
            "bubble",
            True,
            {"flag": flag, "bubble_count": bubble_count},
            time.time() - t0,
            artifacts,
        )

    def _run_blur(self, thumbnail_2048: np.ndarray, out_dir: Path) -> QCModuleResult | None:
        if not self.config.modules.enable_blur:
            return None

        t0 = time.time()
        results, debug = run_blur_detection(Image.fromarray(thumbnail_2048))
        artifacts: list[str] = []

        if self.config.artifacts.save_visualizations and debug is not None:
            blur_dir = out_dir / "blur"
            blur_dir.mkdir(parents=True, exist_ok=True)
            artifacts.append(self.viz.save_rgb(blur_dir / "blur_overlay.png", debug["overlay"]))

        values = {k: v for k, v in results.items() if k not in {"tissue_mask", "blur_mask"}}

        return QCModuleResult(
            "blur",
            True,
            values,
            time.time() - t0,
            artifacts,
        )


QCInferenceService = SlideQcInferenceService
