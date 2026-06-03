#!/usr/bin/env python3
from __future__ import annotations
import argparse, logging, math, os, sys, tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import cv2, numpy as np, pandas as pd, yaml

__version__ = "0.2.0"

def setup_logging(level:str="INFO") -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(message)s", handlers=[logging.StreamHandler(sys.stdout)], force=True)

@dataclass(frozen=True)
class AppConfig:
    label_crops_dir: Path
    output_run_dir: Path
    output_excel: Path
    min_area_ratio: float
    max_area_ratio: float
    min_circularity: float
    min_saturation: int
    min_value: int
    morph_kernel: int
    colors: Dict[str, List[Tuple[Tuple[int,int,int], Tuple[int,int,int]]]]
    log_level: str

    @staticmethod
    def from_yaml(path: Path) -> "AppConfig":
        cfg = yaml.safe_load(path.read_text()) or {}
        ts = str(cfg.get("run_timestamp", "run"))
        run_output_dir = Path(cfg["run_output_dir"])
        output_run_dir = Path(cfg.get("output_run_dir", run_output_dir / ts))
        block = cfg.get("color_label_routing", {}) or {}
        detect = block.get("detection", {}) or {}
        out_excel = Path(str(cfg.get("color_marker_output_excel") or (output_run_dir / str(block.get("output_excel_name", "color_marker_results.xlsx")))))
        colors_raw = (block.get("colors", {}) or {})
        default_map = {
            "red": [((0,70,50),(12,255,255)), ((170,70,50),(179,255,255))],
            "blue": [((90,60,50),(135,255,255))],
            "green": [((35,60,50),(90,255,255))],
            "yellow": [((15,60,70),(40,255,255))],
        }
        colors = {}
        for cname, ccfg in colors_raw.items():
            ranges=[]
            for item in (ccfg.get("hsv_ranges",[]) or []):
                if isinstance(item,(list,tuple)) and len(item)==2 and len(item[0])==3 and len(item[1])==3:
                    ranges.append((tuple(map(int,item[0])), tuple(map(int,item[1]))))
            if ranges:
                colors[str(cname).lower()] = ranges
        if not colors:
            colors=default_map
        return AppConfig(label_crops_dir=Path(cfg["label_crops_dir"]), output_run_dir=output_run_dir, output_excel=out_excel,
            min_area_ratio=float(detect.get("min_area_ratio",0.001)), max_area_ratio=float(detect.get("max_area_ratio",0.2)),
            min_circularity=float(detect.get("min_circularity",0.35)), min_saturation=int(detect.get("min_saturation",60)),
            min_value=int(detect.get("min_value",40)), morph_kernel=int(detect.get("morph_kernel",5)), colors=colors,
            log_level=str(cfg.get("log_level","INFO")))

def refine(mask: np.ndarray, k:int) -> np.ndarray:
    kk=max(3,int(k)); kernel=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(kk,kk));
    mask=cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel); mask=cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel); return mask

def circ(cnt)->float:
    a=cv2.contourArea(cnt); p=cv2.arcLength(cnt, True)
    return 0.0 if a<=0 or p<=0 else float((4.0*math.pi*a)/(p*p))

def center(cnt):
    m=cv2.moments(cnt); return (0.0,0.0) if m['m00']==0 else (float(m['m10']/m['m00']), float(m['m01']/m['m00']))

def detect(img_bgr: np.ndarray, cfg: AppConfig) -> Dict[str, Any]:
    h,w = img_bgr.shape[:2]; area_img=float(h*w); hsv=cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV); best=None
    sat_mask = cv2.inRange(hsv[:,:,1], cfg.min_saturation, 255); val_mask = cv2.inRange(hsv[:,:,2], cfg.min_value, 255)
    for color_name, ranges in cfg.colors.items():
        mask=np.zeros(hsv.shape[:2], dtype=np.uint8)
        for lo,hi in ranges:
            mask=cv2.bitwise_or(mask, cv2.inRange(hsv, np.array(lo,dtype=np.uint8), np.array(hi,dtype=np.uint8)))
        mask=cv2.bitwise_and(mask, sat_mask); mask=cv2.bitwise_and(mask,val_mask); mask=refine(mask,cfg.morph_kernel)
        contours,_=cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            a=float(cv2.contourArea(cnt)); ar=a/area_img if area_img else 0.0
            if a<=0 or ar<cfg.min_area_ratio or ar>cfg.max_area_ratio: continue
            ci=circ(cnt)
            if ci<cfg.min_circularity: continue
            x,y,bw,bh=cv2.boundingRect(cnt); cx,cy=center(cnt); (_, _), radius = cv2.minEnclosingCircle(cnt)
            mean_h, mean_s, mean_v, _ = cv2.mean(hsv, mask=cv2.drawContours(np.zeros(mask.shape,dtype=np.uint8), [cnt], -1, 255, -1))
            aspect=max(bw,bh)/max(1,min(bw,bh)); score=min(1.0,ci)*0.45 + min(1.0, ar/0.02)*0.2 + min(1.0, mean_s/255.0)*0.2 + min(1.0, mean_v/255.0)*0.1 - min(0.3,max(0.0,aspect-1.6)*0.1)
            cand={"DetectedColor":color_name,"Confidence":round(float(max(0.0,min(1.0,score))),4),"MarkerArea":round(a,2),"AreaRatio":round(ar,6),"Circularity":round(ci,4),"CenterX":round(cx,2),"CenterY":round(cy,2),"Radius":round(float(radius),2),"BoundingBoxX":int(x),"BoundingBoxY":int(y),"BoundingBoxW":int(bw),"BoundingBoxH":int(bh),"MeanHue":round(float(mean_h),2),"MeanSaturation":round(float(mean_s),2),"MeanValue":round(float(mean_v),2)}
            if best is None or cand['Confidence']>best['Confidence']: best=cand
    if best is None:
        return {"DetectedColor":"none","Confidence":0.0,"MarkerArea":0.0,"AreaRatio":0.0,"Circularity":0.0,"CenterX":0.0,"CenterY":0.0,"Radius":0.0,"BoundingBoxX":0,"BoundingBoxY":0,"BoundingBoxW":0,"BoundingBoxH":0,"MeanHue":0.0,"MeanSaturation":0.0,"MeanValue":0.0}
    return best

def run(cfg_path: Path) -> int:
    cfg=AppConfig.from_yaml(cfg_path); setup_logging(cfg.log_level); rows=[]
    if not cfg.label_crops_dir.exists():
        logging.error(f"[ERROR] label_crops_dir not found: {cfg.label_crops_dir}"); return 2
    for p in sorted(cfg.label_crops_dir.glob('*')):
        if not p.is_file() or p.suffix.lower() not in {'.png','.jpg','.jpeg','.tif','.tiff','.bmp','.webp'}: continue
        img=cv2.imread(str(p))
        if img is None:
            rows.append({"FileName":p.name,"SlideStem":p.stem,"DetectedColor":"none","Confidence":0.0,"Status":"load_failed"})
            continue
        rec=detect(img,cfg); rec.update({"FileName":p.name,"SlideStem":p.stem,"Status":"ok"}); rows.append(rec)
    df=pd.DataFrame(rows)
    cfg.output_excel.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=str(cfg.output_excel.parent), suffix='.xlsx', delete=False) as tmp:
        tmp_path=Path(tmp.name)
    try:
        with pd.ExcelWriter(tmp_path, engine='openpyxl') as xw: df.to_excel(xw,index=False)
        os.replace(tmp_path, cfg.output_excel)
    finally:
        if tmp_path.exists():
            try: tmp_path.unlink()
            except Exception: pass
    logging.info(f"[OK] Wrote color marker results: {cfg.output_excel}")
    return 0

def main(argv=None)->int:
    parser=argparse.ArgumentParser(); sub=parser.add_subparsers(dest='command', required=True)
    p=sub.add_parser('run'); p.add_argument('--config', required=True); p.add_argument('--log-level', default='INFO')
    v=sub.add_parser('validate'); v.add_argument('--config', required=True); sub.add_parser('version')
    args=parser.parse_args(argv)
    if args.command=='version': print(__version__); return 0
    if args.command=='validate':
        cfg=AppConfig.from_yaml(Path(args.config)); print(cfg.output_excel); return 0
    return run(Path(args.config))

if __name__=='__main__':
    raise SystemExit(main())
