# Model Weights

These files are not tracked by git due to their size (up to 107 MB each).

## Files required

| File | Size | Purpose |
|---|---|---|
| `bubble_detection_ConvNeXtTiny_model.pth` | 107 MB | Bubble artefact detection |
| `blur_detection_resnet18_old.pth` | 43 MB | Blur detection |
| `stain_model_MobileNetV3.pth` | 17 MB | Stain classification |
| `penmark_detection_MobileNetV3.pth` | 17 MB | Penmark detection |

## How to obtain

Copy these files from the shared network drive or model registry to this folder,
or contact the Pathoryx project maintainer for access.

To use Git LFS (recommended for teams):
```bash
git lfs install
git lfs track "*.pth" "*.pt" "*.pkl"
git add .gitattributes
```
