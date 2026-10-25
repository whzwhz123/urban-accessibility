"""
collect_tci_data.py
===================
采集东西城区公交竞争力（TCI）出行时间数据
- 261个东西城网格 × 17个目的地 × 2种方式 × 3时段
- 支持断点续跑
- 输出：tci_raw_data.json

运行：
  python collect_tci_data.py
"""

import os, json, time, math, requests
import numpy as np
import geopandas as gpd
from datetime import datetime

# ── 配置 ─────────────────────────────────────
BASE     = "/Users/aaa/Desktop/MajorThesis/System_build"
GRID_FILE = BASE + "/urban-accessibility-rebuild/Back_end/data_process/output/demand_grid_beijing.geojson"
OUT_DIR  = BASE + "/urban-accessibility-rebuild/Back_end/data_process/output"
CACHE_FILE = OUT_DIR + "/tci_cache.json"
OUT_FILE   = OUT_DIR + "/tci_raw_data.json"

API_KEY = os.environ.get("AMAP_API_KEY", "")

# 东西城边界
EW_LON_MIN, EW_LON_MAX = 116.30, 116.47
EW_LAT_MIN, EW_LAT_MAX = 39.84,  39.98

# 三个时段（2026-05-06 工作日）
SESSIONS = {
    "morning":   {"label": "早高峰08:00", "timestamp": 1746489600},
    "midday":    {"label": "平峰15:00",   "timestamp": 1746514800},
    "evening":   {"label": "晚高峰18:00", "timestamp": 1746525600},
}

# 17个目的地
DESTINATIONS = [
    {"id":"D01","name":"天安门",      "lon":116.3975,"lat":39.9093},
    {"id":"D02","name":"西单",        "lon":116.3744,"lat":39.9136},
    {"id":"D03","name":"王府井",      "lon":116.4074,"lat":39.9145},
    {"id":"D04","name":"北京站",      "lon":116.4277,"lat":39.9027},
    {"id":"D05","name":"鼓楼",        "lon":116.3951,"lat":39.9390},
    {"id":"D06","name":"清琴路",      "lon":116.2200,"lat":39.9800},
    {"id":"D07","name":"荷塘月舍",    "lon":116.2800,"lat":40.0200},
    {"id":"D08","name":"北卫家园",    "lon":116.4200,"lat":40.0200},
    {"id":"D09","name":"红廷别墅",    "lon":116.5200,"lat":40.0000},
    {"id":"D10","name":"三间房",      "lon":116.5600,"lat":39.9300},
    {"id":"D11","name":"化工路",      "lon":116.5200,"lat":39.8600},
    {"id":"D12","name":"万源西里",    "lon":116.4200,"lat":39.8000},
    {"id":"D13","name":"白盆窑",      "lon":116.3200,"lat":39.8000},
    {"id":"D14","name":"卢沟桥北路",  "lon":116.2200,"lat":39.8600},
    {"id":"D15","name":"柴家坟",      "lon":116.2600,"lat":39.9400},
    {"id":"D16","name":"丝竹园",      "lon":116.3800,"lat":39.9900},
    {"id":"D17","name":"金嘉大厦",    "lon":116.3600,"lat":39.9200},
]

# ── WGS-84 → GCJ-02 ──────────────────────────
def wgs2gcj(lon, lat):
    a=6378245.0; ee=0.00669342162296594323; pi=math.pi
    def tL(x,y):
        r=-100+2*x+3*y+0.2*y*y+0.1*x*y+0.2*math.sqrt(abs(x))
        r+=(20*math.sin(6*x*pi)+20*math.sin(2*x*pi))*2/3
        r+=(20*math.sin(y*pi)+40*math.sin(y/3*pi))*2/3
        r+=(160*math.sin(y/12*pi)+320*math.sin(y*pi/30))*2/3; return r
    def tO(x,y):
        r=300+x+2*y+0.1*x*x+0.1*x*y+0.1*math.sqrt(abs(x))
        r+=(20*math.sin(6*x*pi)+20*math.sin(2*x*pi))*2/3
        r+=(20*math.sin(x*pi)+40*math.sin(x/3*pi))*2/3
        r+=(150*math.sin(x/12*pi)+300*math.sin(x/30*pi))*2/3; return r
    rl=lat/180*pi; mg=math.sin(rl); mg=1-ee*mg*mg; sq=math.sqrt(mg)
    dlat=tL(lon-105,lat-35); dlon=tO(lon-105,lat-35)
    dlat=(dlat*180)/((a*(1-ee))/(mg*sq)*pi)
    dlon=(dlon*180)/(a/sq*math.cos(rl)*pi)
    return lon+dlon, lat+dlat

# ── API调用 ───────────────────────────────────
def get_transit_time(o_lon, o_lat, d_lon, d_lat, timestamp):
    """公交路径规划，返回(时间分钟, 换乘次数)"""
    url = "https://restapi.amap.com/v3/direction/transit/integrated"
    params = {
        "key":        API_KEY,
        "origin":     f"{o_lon:.6f},{o_lat:.6f}",
        "destination":f"{d_lon:.6f},{d_lat:.6f}",
        "city":       "010",
        "strategy":   "0",
        "departure_time": str(timestamp),
        "nightflag":  "0",
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        if data.get("status") != "1" or not data.get("route"):
            return None, None
        transits = data["route"].get("transits", [])
        if not transits:
            return None, None
        best = transits[0]
        duration = float(best.get("duration", 0)) / 60.0
        # 换乘次数：有效乘车段数-1
        segs = best.get("segments", [])
        valid_legs = sum(1 for s in segs
                        if s.get("bus") and s["bus"].get("buslines"))
        transfer = max(0, valid_legs - 1)
        return round(duration, 2), transfer
    except Exception as e:
        return None, None

def get_driving_time(o_lon, o_lat, d_lon, d_lat, timestamp):
    """小汽车路径规划，返回时间分钟"""
    url = "https://restapi.amap.com/v3/direction/driving"
    params = {
        "key":        API_KEY,
        "origin":     f"{o_lon:.6f},{o_lat:.6f}",
        "destination":f"{d_lon:.6f},{d_lat:.6f}",
        "strategy":   "0",
        "departure_time": str(timestamp),
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        if data.get("status") != "1" or not data.get("route"):
            return None
        paths = data["route"].get("paths", [])
        if not paths:
            return None
        duration = float(paths[0].get("duration", 0)) / 60.0
        return round(duration, 2)
    except:
        return None

# ── 主程序 ────────────────────────────────────
def main():
    raise RuntimeError('此旧脚本的历史时段与高德调用参数不可靠，已禁用；请从本地系统的“导航抽样”运行即时采集。')
    # 读取东西城网格
    print("→ 读取东西城网格...")
    grid = gpd.read_file(GRID_FILE)
    cx = grid.geometry.centroid.x.values
    cy = grid.geometry.centroid.y.values
    mask = ((cx >= EW_LON_MIN) & (cx <= EW_LON_MAX) &
            (cy >= EW_LAT_MIN) & (cy <= EW_LAT_MAX))
    ew_grid = grid[mask].copy()
    ew_grid["cx"] = cx[mask]
    ew_grid["cy"] = cy[mask]
    print(f"  东西城网格数: {len(ew_grid)}")

    # 读取缓存
    cache = {}
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
        print(f"  已有缓存记录: {len(cache)} 条")

    # 统计总任务量
    total = len(ew_grid) * len(DESTINATIONS) * len(SESSIONS)
    done  = len(cache)
    print(f"\n  总任务: {total} 条（公交+驾车各一次）")
    print(f"  已完成: {done} 条")
    print(f"  剩余:   {total - done} 条")
    print(f"  预计时间: {(total - done) * 0.35 / 60:.0f} 分钟\n")

    count = 0
    errors = 0

    for sess_id, sess in SESSIONS.items():
        print(f"\n{'='*45}")
        print(f"  时段: {sess['label']}")
        print(f"{'='*45}")
        ts = sess["timestamp"]

        for gi, row in ew_grid.iterrows():
            grid_lon, grid_lat = row["cx"], row["cy"]
            # WGS-84 → GCJ-02
            g_lon, g_lat = wgs2gcj(grid_lon, grid_lat)

            for dest in DESTINATIONS:
                d_lon, d_lat = wgs2gcj(dest["lon"], dest["lat"])
                cache_key = f"{sess_id}_{gi}_{dest['id']}"

                if cache_key in cache:
                    continue

                # 公交
                bus_t, transfer = get_transit_time(g_lon, g_lat, d_lon, d_lat, ts)
                time.sleep(0.18)
                # 驾车
                car_t = get_driving_time(g_lon, g_lat, d_lon, d_lat, ts)
                time.sleep(0.18)

                record = {
                    "session":    sess_id,
                    "grid_idx":   int(gi),
                    "grid_lon":   round(grid_lon, 6),
                    "grid_lat":   round(grid_lat, 6),
                    "dest_id":    dest["id"],
                    "dest_name":  dest["name"],
                    "bus_time":   bus_t,
                    "transfer":   transfer,
                    "car_time":   car_t,
                    "timestamp":  ts,
                }
                cache[cache_key] = record
                count += 1

                if bus_t is None or car_t is None:
                    errors += 1

                # 每100条保存缓存
                if count % 100 == 0:
                    with open(CACHE_FILE, "w", encoding="utf-8") as f:
                        json.dump(cache, f, ensure_ascii=False)
                    done_total = len(cache)
                    pct = done_total / total * 100
                    print(f"  [{pct:.1f}%] {done_total}/{total}  错误:{errors}  "
                          f"当前: 网格{gi} {dest['id']} {sess['label']}")

    # 最终保存
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(list(cache.values()), f, ensure_ascii=False, indent=2)

    print(f"\n{'='*45}")
    print(f"✓ 采集完成！")
    print(f"  总记录: {len(cache)} 条")
    print(f"  错误数: {errors} 条")
    print(f"  输出:   {OUT_FILE}")
    print(f"{'='*45}")

if __name__ == "__main__":
    main()