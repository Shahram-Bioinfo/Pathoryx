import torch.nn as nn
from torchvision import models


class StainModel(nn.Module):
    def __init__(self):
        super().__init__()
        self.model = models.mobilenet_v3_large(weights=None)
        in_features = self.model.classifier[3].in_features
        self.model.classifier[3] = nn.Linear(in_features, 2)

    def forward(self, x):
        return self.model(x)
