import torch.nn as nn
from torchvision import models


class PenmarkModel(nn.Module):
    def __init__(self):
        super().__init__()
        self.model = models.mobilenet_v3_large(weights=None)
        self.model.classifier[3] = nn.Linear(self.model.classifier[3].in_features, 2)

    def forward(self, x):
        return self.model(x)
