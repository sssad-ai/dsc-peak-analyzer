from __future__ import annotations
import numpy as np
import pandas as pd
from scipy.signal import find_peaks, peak_widths, savgol_filter

def _odd_window(requested: int, n: int) -> int:
    if n < 5:
        return 0
    w = min(int(requested), n if n % 2 else n - 1)
    w = max(5, w)
    if w % 2 == 0:
        w -= 1
    return w if w >= 5 else 0

def analyze_peaks(
    temperature,
    heat_flow,
    direction: str = "both",
    prominence: float | None = None,
    smoothing_window: int = 11,
) -> tuple[pd.DataFrame, np.ndarray]:
    x = pd.to_numeric(pd.Series(temperature), errors="coerce").to_numpy(float)
    y = pd.to_numeric(pd.Series(heat_flow), errors="coerce").to_numpy(float)
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]

    if len(x) < 5:
        raise ValueError("At least 5 valid data points are required.")

    order = np.argsort(x)
    x, y = x[order], y[order]

    window = _odd_window(smoothing_window, len(y))
    smooth = savgol_filter(y, window_length=window, polyorder=min(3, window-2)) if window else y.copy()

    scale = float(np.nanpercentile(smooth, 95) - np.nanpercentile(smooth, 5))
    if prominence is None or prominence <= 0:
        prominence = max(scale * 0.05, np.finfo(float).eps)

    searches = []
    if direction in ("up", "both"):
        searches.append(("Up", smooth))
    if direction in ("down", "both"):
        searches.append(("Down", -smooth))

    rows = []
    for peak_type, signal in searches:
        idx, props = find_peaks(signal, prominence=prominence)
        if not len(idx):
            continue
        widths, _, left_ips, right_ips = peak_widths(signal, idx, rel_height=0.5)
        for j, p in enumerate(idx):
            li = int(max(0, np.floor(left_ips[j])))
            ri = int(min(len(x)-1, np.ceil(right_ips[j])))
            # Simple v0.1 local linear baseline between FWHM bounds.
            baseline = np.interp(x[li:ri+1], [x[li], x[ri]], [smooth[li], smooth[ri]]) if ri > li else np.array([smooth[p]])
            area = float(np.trapezoid(smooth[li:ri+1] - baseline, x[li:ri+1])) if ri > li else 0.0
            rows.append({
                "Type": peak_type,
                "T_left_FWHM": float(x[li]),
                "T_peak": float(x[p]),
                "T_right_FWHM": float(x[ri]),
                "Peak_height": float(smooth[p]),
                "Prominence": float(props["prominences"][j]),
                "Area_FWHM": area,
                "_peak_index": int(p),
            })

    columns = ["Peak", "Type", "T_left_FWHM", "T_peak", "T_right_FWHM", "Peak_height", "Prominence", "Area_FWHM", "_peak_index"]
    if not rows:
        return pd.DataFrame(columns=columns), smooth

    result = pd.DataFrame(rows).sort_values("T_peak").reset_index(drop=True)
    result.insert(0, "Peak", np.arange(1, len(result)+1))
    return result, smooth
