"""Experimental Tg midpoint fitting with explicit, readable rejection reasons."""
from dataclasses import dataclass
import numpy as np
import pandas as pd
from scipy.optimize import least_squares
from scipy.special import expit


@dataclass
class TgResult:
    summary: dict
    curve: pd.DataFrame


def analyze_tg(record, low_c=-30., high_c=20., baseline_fraction=.2):
    if not np.isfinite([low_c, high_c, baseline_fraction]).all() or high_c - low_c < 5 or not .1 <= baseline_fraction <= .3:
        raise ValueError("Tg 温区至少需要 5°C，基线比例应在 10%–30% 之间。")
    f = record.frame
    empty = {"Tg_midpoint_C": None, "status": "unavailable", "reason": "", "reason_code": "",
             "method": "two_baselines_sigmoid_midpoint", "low_C": low_c, "high_C": high_c,
             "measured_low_C": float(f.Temperature_C.min()), "measured_high_C": float(f.Temperature_C.max()),
             "baseline_fraction": baseline_fraction, "checks": {}}
    if record.direction != "heating":
        return TgResult({**empty, "reason_code": "not_heating", "reason": "请使用升温段分析 Tg。"}, pd.DataFrame())
    if f.Temperature_C.min() > low_c or f.Temperature_C.max() < high_c:
        reason = (f"此段实测范围为 {f.Temperature_C.min():.1f}–{f.Temperature_C.max():.1f}°C，"
                  f"未覆盖所选的 {low_c:g}–{high_c:g}°C。请选择覆盖该温区的升温段，或调整温区。")
        return TgResult({**empty, "reason_code": "not_covered", "reason": reason}, pd.DataFrame())
    raw = f.loc[f.Temperature_C.between(low_c, high_c)].groupby("Temperature_C", as_index=False).HeatFlow_W_g.mean()
    if len(raw) < 50:
        return TgResult({**empty, "reason_code": "too_few_points", "reason": "所选温区少于 50 个温度点，无法稳定拟合。"}, pd.DataFrame())
    x = np.linspace(raw.Temperature_C.min(), raw.Temperature_C.max(), min(len(raw), 3000))
    y = np.interp(x, raw.Temperature_C, raw.HeatFlow_W_g)
    span = high_c - low_c
    left = x <= low_c + span * baseline_fraction
    right = x >= high_c - span * baseline_fraction
    before = np.polyval(np.polyfit(x[left], y[left], 1), x)
    after = np.polyval(np.polyfit(x[right], y[right], 1), x)
    separation = after - before
    curve = pd.DataFrame({"Temperature_C": x, "HeatFlow_W_g": y, "Before_baseline_W_g": before,
                          "After_baseline_W_g": after})
    center = len(x) // 2
    edge_residual = np.r_[y[left] - before[left], y[right] - after[right]]
    noise = max(1e-6, float(np.sqrt(np.mean(edge_residual ** 2))))
    central = (x >= low_c + span * .2) & (x <= high_c - span * .2)
    baseline_ok = bool(not np.any(separation[central] * separation[center] <= 0))
    noise_ratio = abs(float(separation[center])) / noise
    checks = {"两侧基线在转变区保持分离": baseline_ok, "台阶大于 5 倍边缘噪声": noise_ratio >= 5}
    if not baseline_ok or noise_ratio < 5:
        code = "crossing_baselines" if not baseline_ok else "weak_step"
        reason = ("两侧外推基线在转变区交叉，当前温区不适合台阶拟合。请调整温区，使两端落在平稳基线区。"
                  if not baseline_ok else "台阶信号低于噪声门槛，当前数据无法分辨 Tg。可调整温区或使用信噪比更高的数据。")
        return TgResult({**empty, "reason_code": code, "reason": reason, "checks": checks,
                         "step_noise_ratio": noise_ratio}, curve)

    def model(params):
        midpoint, width = params
        return before + separation * expit((x - midpoint) / width)

    width_max = min(12., span / 4)
    # Clip the initial width to the valid bounds, including narrow user windows.
    fits = [least_squares(lambda p: (model(p) - y) / noise,
                          [low_c + span * fraction, min(3., width_max * .8)],
                          bounds=([low_c + .2 * span, .3], [high_c - .2 * span, width_max]), loss="soft_l1")
            for fraction in (.35, .5, .65)]
    fit = min(fits, key=lambda result: result.cost)
    midpoint, width = map(float, fit.x)
    predicted = model(fit.x)
    straight = np.polyval(np.polyfit(x, y, 1), x)
    improvement = 1. - float(np.sum((y - predicted) ** 2)) / max(float(np.sum((y - straight) ** 2)), 1e-15)
    residual_rms = float(np.sqrt(np.mean((y - predicted) ** 2)))
    bounded = midpoint <= low_c + span * .201 or midpoint >= high_c - span * .201 or width >= width_max * .99 or width <= .303
    step = float(np.interp(midpoint, x, separation))
    ratio = abs(step) / max(noise, residual_rms)
    direction_ok = bool(record.exo_sign is not None and step * (-record.exo_sign) > 0)
    checks.update({"拟合收敛": bool(fit.success), "拟合未触及参数边界": not bounded,
                   "相对直线的误差改善超过 35%": improvement > .35, "台阶与残差之比至少为 5": ratio >= 5,
                   "台阶方向符合升温热容增加": direction_ok})
    failures = []
    if not fit.success:
        failures.append(("not_converged", "台阶拟合未收敛"))
    if bounded:
        failures.append(("fit_at_boundary", "拟合触及参数边界，两侧基线可能不足"))
    if improvement <= .35:
        failures.append(("linear_drift", "台阶模型相对直线的改善不足，可能主要是基线漂移"))
    if ratio < 5:
        failures.append(("poor_fit", "台阶相对拟合残差太小"))
    if not direction_ok:
        failures.append(("wrong_step_direction", "拟合台阶与当前放热方向下的升温热容增加方向相反，不能据此判为 Tg；请检查放热方向和基线温区"))
    candidate = not failures
    curve["Step_fit_W_g"] = predicted
    cp = step * (-record.exo_sign) / (record.rate_k_min / 60) if record.exo_sign and record.rate_k_min and record.rate_k_min > 0 else None
    summary = {**empty, "Tg_midpoint_C": midpoint if candidate else None,
               "status": "estimate" if candidate else "unavailable",
               "reason_code": "step_detected" if candidate else failures[0][0],
               "reason": "由两侧基线之间的台阶中点估算。" if candidate else "；".join(message for _, message in failures) + "。",
               "checks": checks, "failed_checks": [code for code, _ in failures],
               "fit_midpoint_C": midpoint, "step_W_g": step, "step_noise_ratio": noise_ratio,
               "DeltaCp_est_J_g_K": cp if candidate else None, "step_fit_width_K": width,
               "step_residual_ratio": ratio, "improvement_over_linear": improvement,
               "fit_RMS_W_g": residual_rms}
    return TgResult(summary, curve)
