import cv2
import numpy as np
from skimage.filters.rank import entropy
from skimage.morphology import disk
from sklearn.cluster import KMeans

LEVEL = 2
TILE_SIZE = 128
EDGE_MARGIN = 1
BLUR_TILE_TISSUE_FRAC = 0.5
MIN_TISSUE_AREA = 1000
LAP_THRESH = 29
TEN_THRESH = 1700
ENT_THRESH = 4.4
SLIDE_BLUR_PERCENT = 0.10


def remove_small_objects(mask, min_size=5000):
    mask_u8 = (mask > 0).astype(np.uint8)
    nb_components, output, stats, _ = cv2.connectedComponentsWithStats(mask_u8, connectivity=8)
    cleaned_mask = np.zeros(mask.shape, dtype=np.uint8)
    for i in range(1, nb_components):
        if stats[i, -1] >= min_size:
            cleaned_mask[output == i] = 1
    return cleaned_mask


def tissue_mask_lab(img_rgb, pen_mask=None, n_clusters=3, min_area=MIN_TISSUE_AREA):
    lab = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2LAB)
    ab_values = lab.reshape((-1, 3))
    kmeans = KMeans(n_clusters=n_clusters, random_state=42).fit(ab_values)
    labels = kmeans.labels_.reshape(img_rgb.shape[:2])
    L_values = lab[:, :, 0]
    cluster_avg_L = [np.mean(L_values[labels == i]) for i in range(n_clusters)]
    sorted_idx = np.argsort(cluster_avg_L)
    dark_cluster, middle_cluster = sorted_idx[0], sorted_idx[1]
    selected_clusters = [middle_cluster] if pen_mask is not None and np.sum(pen_mask) > 0 else [dark_cluster, middle_cluster]
    mask = np.zeros_like(labels, dtype=np.uint8)
    for c in selected_clusters:
        mask[labels == c] = 1
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    mask = remove_small_objects(mask.astype(bool), min_size=5000)
    return np.zeros_like(mask) if np.sum(mask) < min_area else mask


def extract_focus_features(tile_gray):
    lap_var = cv2.Laplacian(tile_gray, cv2.CV_64F).var()
    gx = cv2.Sobel(tile_gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(tile_gray, cv2.CV_64F, 0, 1, ksize=3)
    tenengrad = np.mean(gx * gx + gy * gy)
    ent = np.mean(entropy(tile_gray.astype(np.uint8), disk(5)))
    return lap_var, tenengrad, ent


def is_blur_tile(lap, ten, ent):
    return (lap < LAP_THRESH) and (ten < TEN_THRESH) and (ent < ENT_THRESH)


def overlay_blur(img_rgb, blur_mask, alpha=0.4):
    overlay = img_rgb.copy()
    red_layer = np.zeros_like(img_rgb)
    red_layer[..., 0] = 255
    overlay[blur_mask == 1] = (alpha * red_layer[blur_mask == 1] + (1 - alpha) * overlay[blur_mask == 1]).astype(np.uint8)
    return overlay


def detect_blur(img_rgb, pen_mask=None, debug=False):
    tissue_mask = tissue_mask_lab(img_rgb, pen_mask=pen_mask)
    gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    blur_mask = np.zeros((h, w), dtype=np.uint8)
    total_tiles = blur_tiles = 0
    n_tiles_y, n_tiles_x = h // TILE_SIZE, w // TILE_SIZE
    for j in range(n_tiles_y):
        for i in range(n_tiles_x):
            if i < EDGE_MARGIN or j < EDGE_MARGIN or i >= n_tiles_x - EDGE_MARGIN or j >= n_tiles_y - EDGE_MARGIN:
                continue
            y, x = j * TILE_SIZE, i * TILE_SIZE
            tile_gray = gray[y:y + TILE_SIZE, x:x + TILE_SIZE]
            tmask_tile = tissue_mask[y:y + TILE_SIZE, x:x + TILE_SIZE]
            if np.sum(tmask_tile) < BLUR_TILE_TISSUE_FRAC * TILE_SIZE * TILE_SIZE:
                continue
            lap, ten, ent = extract_focus_features(tile_gray)
            total_tiles += 1
            if is_blur_tile(lap, ten, ent):
                blur_tiles += 1
                blur_mask[y:y + TILE_SIZE, x:x + TILE_SIZE] = 1
    blur_ratio = blur_tiles / total_tiles if total_tiles > 0 else 0
    blur_flag = 1 if blur_ratio >= SLIDE_BLUR_PERCENT else 0
    debug_images = None
    if debug:
        debug_images = {"overlay": overlay_blur(img_rgb, blur_mask)}
    results = {"blur_flag": blur_flag, "blur_ratio": blur_ratio, "total_tiles": total_tiles, "blur_tiles": blur_tiles, "tissue_mask": tissue_mask, "blur_mask": blur_mask}
    return results, debug_images


def run_blur_detection(pil_image):
    img_rgb = np.array(pil_image)
    return detect_blur(img_rgb, debug=True)
