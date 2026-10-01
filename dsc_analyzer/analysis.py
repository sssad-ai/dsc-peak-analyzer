"""Visible linear baselines, raw-time integrals and tangent estimates."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
import numpy as np
import pandas as pd
from scipy.signal import find_peaks, peak_widths, savgol_filter
from .parser import DSCRecord


@dataclass
class EventSettings:
    low_c: float
    high_c: float
    sign: int
    kind: str
    anchor_width_c: float = 4.
    smoothing_points: int = 31
    prominence_w_g: float | None = None
    boundary_mode: str = "auto"
    baseline_method: str = "edge_fit"
    integration_low_c: float | None = None
    integration_high_c: float | None = None
    excluded_peaks_c: list[float] = field(default_factory=list)


@dataclass
class EventResult:
    summary: dict
    peaks: pd.DataFrame
    curve: pd.DataFrame
    settings: dict
    warnings: list[str] = field(default_factory=list)


def smooth_signal(y, window):
    n = len(y)
    if window <= 1 or n < 5:
        return np.array(y, copy=True)
    w = min(int(window), n if n % 2 else n - 1)
    w -= (w % 2 == 0)
    return savgol_filter(y, max(5, w), 3)


def _fit_baseline(x, y, low, high, width, method):
    if method == "endpoints":
        a, b = np.interp([low, high], x, y)
        slope = (b - a) / (high - low)
        return np.array([slope, a - slope * low])
    left = (x >= low) & (x <= low + width)
    right = (x <= high) & (x >= high - width)
    if left.sum() < 3 or right.sum() < 3 or low + width >= high - width:
        raise ValueError("基线左右区间各需至少 3 点且不能重叠，请调整窗口或基线宽度。")
    xl, xr = np.mean(x[left]), np.mean(x[right])
    yl, yr = np.mean(y[left]), np.mean(y[right])
    slope = (yr - yl) / (xr - xl)
    return np.array([slope, yl - slope * xl])


def _tangent(x, smooth, coef, peak_i, a, b, flank):
    indices = np.arange(a + 2, peak_i - 2) if flank == "left" else np.arange(peak_i + 3, b - 1)
    if len(indices) < 5:
        return None
    gradient = np.gradient(smooth - np.polyval(coef, x), x)
    idx = indices[np.argmax(np.abs(gradient[indices]))]
    mask = (np.abs(x - x[idx]) <= .5) & (np.arange(len(x)) >= a) & (np.arange(len(x)) <= b)
    if mask.sum() < 3:
        return None
    tangent = np.polyfit(x[mask], smooth[mask], 1)
    den = tangent[0] - coef[0]
    if abs(den) < 1e-10:
        return None
    value = float((coef[1] - tangent[1]) / den)
    valid = x[a] - 5 <= value < x[peak_i] if flank == "left" else x[peak_i] < value <= x[b] + 5
    return value if valid else None


def analyze_event(record: DSCRecord, settings: EventSettings) -> EventResult:
    if settings.kind not in {"melting", "crystallization", "cold_crystallization", "other"} or settings.sign not in {-1, 1}:
        raise ValueError("热事件类型或峰方向无效。")
    if settings.boundary_mode not in {"auto", "manual"} or settings.baseline_method not in {"edge_fit", "endpoints"}:
        raise ValueError("未知边界或基线方法。")
    low, high = float(settings.low_c), float(settings.high_c)
    if not np.isfinite([low, high, settings.anchor_width_c]).all() or high <= low or settings.anchor_width_c <= 0:
        raise ValueError("温度上下界及基线宽度无效。")
    if settings.prominence_w_g is not None and (not np.isfinite(settings.prominence_w_g) or settings.prominence_w_g <= 0):
        raise ValueError("手动峰显著性必须为正数。")
    frame = record.frame
    if low < frame.Temperature_C.min() or high > frame.Temperature_C.max():
        raise ValueError("分析窗口必须完全位于实测温度范围内。")
    region = frame.loc[frame.Temperature_C.between(low, high)].copy()
    if len(region) < 15:
        raise ValueError("分析窗口至少需要 15 个有效点。")
    raw = region.sort_values("Temperature_C").groupby("Temperature_C", as_index=False).HeatFlow_W_g.mean()
    x = np.linspace(raw.Temperature_C.min(), raw.Temperature_C.max(), min(len(raw), 6000))
    y = np.interp(x, raw.Temperature_C, raw.HeatFlow_W_g)
    smooth = smooth_signal(y, settings.smoothing_points)
    coef = _fit_baseline(x, y, low, high, settings.anchor_width_c, settings.baseline_method)
    baseline = np.polyval(coef, x)
    signal = settings.sign * (smooth - baseline)
    noise = float(1.4826 * np.median(np.abs((y - smooth) - np.median(y - smooth))))
    prom = settings.prominence_w_g or max(.005, 5 * noise, .04 * max(float(signal.max()), 0.))
    dx = float(np.median(np.diff(x)))
    indices, props = find_peaks(signal, prominence=prom, distance=max(1, int(1.0 / dx)), width=max(1., .3 / dx))
    if settings.excluded_peaks_c:
        keep = np.array([not any(abs(x[i] - v) <= max(2 * dx, .15) for v in settings.excluded_peaks_c) for i in indices])
        indices = indices[keep]
        props = {k: v[keep] for k, v in props.items()}
    warnings = []
    if settings.excluded_peaks_c:
        warnings.append("人工排除仅改变候选峰和事件组边界；区间内部的重叠峰面积不会单独扣除。")
    curve = pd.DataFrame({"Temperature_C": x, "Raw_interpolated_W_g": y, "Smoothed_W_g": smooth,
                          "Baseline_W_g": baseline, "Corrected_W_g": smooth - baseline})
    empty = {"kind": settings.kind, "status": "no_peak", "T_peak_C": None, "DeltaH_signed_J_g": None,
             "DeltaH_event_J_g": None, "integration_low_C": None, "integration_high_C": None,
             "T_onset_est_C": None, "T_endset_est_C": None, "peak_count": 0}
    if not len(indices):
        return EventResult(empty, pd.DataFrame(), curve, asdict(settings), ["未检出达到阈值的峰；未检出不能视为零焓。"])
    widths, _, left_ips, right_ips = peak_widths(signal, indices, rel_height=.5)
    if settings.boundary_mode == "auto":
        threshold = max(3 * noise, .005 * float(signal[indices].max()))
        a, b = int(indices.min()), int(indices.max())
        while a > 0 and signal[a] > threshold:
            a -= 1
        while b < len(x) - 1 and signal[b] > threshold:
            b += 1
        guard = max(2, int(settings.anchor_width_c / dx))
        if a <= guard or b >= len(x) - 1 - guard:
            warnings.append("峰尾接近锚点或窗口边缘，请检查边界截断及基线漂移。")
        ilow, ihigh = float(x[a]), float(x[b])
    else:
        ilow = settings.integration_low_c if settings.integration_low_c is not None else low
        ihigh = settings.integration_high_c if settings.integration_high_c is not None else high
        if not np.isfinite([ilow, ihigh]).all() or not low <= ilow < ihigh <= high:
            raise ValueError("积分边界必须位于基线分析窗口内，且下界小于上界。")
        a, b = int(np.searchsorted(x, ilow)), min(len(x) - 1, int(np.searchsorted(x, ihigh)))
        keep = (x[indices] >= ilow) & (x[indices] <= ihigh)
        indices, left_ips, right_ips, widths = indices[keep], left_ips[keep], right_ips[keep], widths[keep]
        props = {k: v[keep] for k, v in props.items()}
        if not len(indices):
            return EventResult(empty, pd.DataFrame(), curve, asdict(settings), ["手动积分区间内未检出峰。"])
    signed_h = None
    if "Time_s" in frame:
        chronological = frame.sort_values("Time_s")
        positions = np.flatnonzero(chronological.Temperature_C.between(ilow, ihigh).to_numpy())
        if len(positions) < 3 or np.any(np.diff(positions) != 1):
            raise ValueError("积分窗口中的时间点不足或在时间轴上不连续，请分段。")
        part = chronological.iloc[max(0, positions[0] - 1):min(len(chronological), positions[-1] + 2)]
        temps, times, flows = (part[c].to_numpy() for c in ("Temperature_C", "Time_s", "HeatFlow_W_g"))
        if record.direction == "cooling":
            start_t, end_t = np.interp([ihigh, ilow], temps[::-1], times[::-1])
        else:
            start_t, end_t = np.interp([ilow, ihigh], temps, times)
        keep = (times > start_t) & (times < end_t)
        it = np.r_[start_t, times[keep], end_t]
        ft, tt = np.interp(it, times, flows), np.interp(it, times, temps)
        signed_h = float(np.trapezoid(ft - np.polyval(coef, tt), it))
    else:
        warnings.append("缺少时间或扫描速率，焓不可计算。")
    oriented_h = settings.sign * signed_h if signed_h is not None else None
    if oriented_h is not None and oriented_h <= 0:
        warnings.append("积分与事件方向不一致，焓不用于结晶度。")
        oriented_h = None
    peaks = []
    for j, idx in enumerate(indices):
        peaks.append({"Peak": j + 1, "T_peak_C": float(x[idx]), "Height_above_baseline_W_g": float(signal[idx]),
                      "Prominence_W_g": float(props["prominences"][j]),
                      "Half_prominence_low_C": float(np.interp(left_ips[j], np.arange(len(x)), x)),
                      "Half_prominence_high_C": float(np.interp(right_ips[j], np.arange(len(x)), x)),
                      "Width_half_prominence_C": float(widths[j] * dx)})
    dominant = int(indices[np.argmax(signal[indices])])
    tl, tr = (_tangent(x, smooth, coef, dominant, a, b, f) for f in ("left", "right"))
    onset, endset = (tl, tr) if record.direction == "heating" else (tr, tl)
    if len(indices) > 1:
        warnings.append("多峰的焓为整个事件组的一次积分；切线温度针对主峰，未进行分峰拟合。")
    summary = {"kind": settings.kind, "status": "estimate", "T_peak_C": float(x[dominant]),
               "T_onset_est_C": onset, "T_endset_est_C": endset, "integration_low_C": ilow, "integration_high_C": ihigh,
               "DeltaH_signed_J_g": signed_h, "DeltaH_event_J_g": oriented_h, "peak_count": len(indices),
               "prominence_threshold_W_g": prom, "noise_MAD_W_g": noise,
               "baseline_slope_W_g_K": float(coef[0]), "baseline_intercept_W_g": float(coef[1]),
               "enthalpy_basis": "total_sample_mass", "integration_method": "raw_heat_flow_over_time" if "Time_s" in frame else "unavailable"}
    return EventResult(summary, pd.DataFrame(peaks), curve, asdict(settings), warnings)


def default_settings(record, kind=None):
    kind = kind or ("melting" if record.direction == "heating" else "crystallization")
    bounds = {"melting": (110., 195.), "crystallization": (60., 155.), "cold_crystallization": (20., 130.), "other": (60., 195.)}
    low, high = bounds[kind]
    low, high = max(low, float(record.frame.Temperature_C.min())), min(high, float(record.frame.Temperature_C.max()))
    if high - low < 10:
        raise ValueError("实测温区未覆盖所选 PP 热事件，需手动指定范围。")
    sign = -record.exo_sign if kind == "melting" and record.exo_sign else record.exo_sign
    if sign is None:
        raise ValueError("未识别放热方向，请在界面确认。")
    return EventSettings(low, high, sign, kind)
