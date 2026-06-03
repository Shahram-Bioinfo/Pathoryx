from __future__ import annotations

from functools import cached_property

import torch
import torchvision.transforms as T

from pathoryx_enterprise.services.qc.engine.config import AppConfig
from pathoryx_enterprise.services.qc.engine.models.blur_model import BlurModel
from pathoryx_enterprise.services.qc.engine.models.bubble_model import BubbleModel
from pathoryx_enterprise.services.qc.engine.models.penmark_model import PenmarkModel
from pathoryx_enterprise.services.qc.engine.models.stain_model import StainModel


class ModelRegistry:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.normalize = T.Compose([
            T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])

    def _load_model(self, path: str, model_class):
        model = model_class().to(self.device)
        state_dict = torch.load(path, map_location=self.device)
        new_state_dict = {}
        for k, v in state_dict.items():
            new_key = "model." + k if not k.startswith("model.") else k
            new_state_dict[new_key] = v
        model.load_state_dict(new_state_dict, strict=True)
        model.eval()
        return model

    @cached_property
    def penmark_model(self):
        return self._load_model(self.config.models.penmark_weights, PenmarkModel) if self.config.modules.enable_penmark else None

    @cached_property
    def bubble_model(self):
        return self._load_model(self.config.models.bubble_weights, BubbleModel) if self.config.modules.enable_bubble else None

    @cached_property
    def stain_model(self):
        return self._load_model(self.config.models.stain_weights, StainModel) if self.config.modules.enable_stain else None

    @cached_property
    def blur_model(self):
        return self._load_model(self.config.models.blur_weights, BlurModel) if self.config.modules.enable_blur else None
