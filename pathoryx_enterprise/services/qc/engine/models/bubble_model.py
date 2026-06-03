import torch.nn as nn
from torchvision import models


class BubbleModel(nn.Module):
    def __init__(self):
        super().__init__()
        self.model = models.convnext_tiny(weights=None)
        in_features = self.model.classifier[2].in_features
        self.model.classifier[2] = nn.Linear(in_features, 2)

    def forward(self, x):
        return self.model(x)
