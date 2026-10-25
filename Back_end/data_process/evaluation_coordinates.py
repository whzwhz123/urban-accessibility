import math
import numpy as np

# Vectorized coordinate conversion from the original preprocessing implementation.
def gcj02_to_wgs84_arr(lons, lats):
    a = 6378245.0
    ee = 0.006693421622965943
    pi = math.pi
    lons = np.array(lons, dtype=float)
    lats = np.array(lats, dtype=float)

    def tLat(x, y):
        r = -100 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * np.sqrt(np.abs(x))
        r += (20 * np.sin(6 * x * pi) + 20 * np.sin(2 * x * pi)) * 2 / 3
        r += (20 * np.sin(y * pi) + 40 * np.sin(y / 3 * pi)) * 2 / 3
        r += (160 * np.sin(y / 12 * pi) + 320 * np.sin(y * pi / 30)) * 2 / 3
        return r

    def tLon(x, y):
        r = 300 + x + 2 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * np.sqrt(np.abs(x))
        r += (20 * np.sin(6 * x * pi) + 20 * np.sin(2 * x * pi)) * 2 / 3
        r += (20 * np.sin(x * pi) + 40 * np.sin(x / 3 * pi)) * 2 / 3
        r += (150 * np.sin(x / 12 * pi) + 300 * np.sin(x / 30 * pi)) * 2 / 3
        return r
    wl, wt = (lons.copy(), lats.copy())
    for _ in range(10):
        dlat = tLat(wl - 105, wt - 35)
        dlon = tLon(wl - 105, wt - 35)
        rl = wt / 180 * pi
        mg = np.sin(rl)
        mg = 1 - ee * mg * mg
        sq = np.sqrt(mg)
        wl -= wl + dlon * 180 / (a / sq * np.cos(rl) * pi) - lons
        wt -= wt + dlat * 180 / (a * (1 - ee) / (mg * sq) * pi) - lats
    return (wl, wt)
