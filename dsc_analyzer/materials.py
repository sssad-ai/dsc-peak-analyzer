"""Crystallinity from user-supplied matrix composition and reference enthalpy."""
import math

def crystallinity(melting_j_g, *, matrix_mass_fraction=None, cold_j_g=None, cold_status="unspecified",
                 reference_j_g=None, enthalpy_basis="total_sample_mass"):
    out = {"Xc_percent": None, "status": "unavailable", "reason": "", "matrix_mass_fraction": matrix_mass_fraction,
           "reference_J_g": reference_j_g, "melting_J_g": melting_j_g, "cold_crystallization_J_g": cold_j_g,
           "cold_status": cold_status, "enthalpy_basis": enthalpy_basis}
    if melting_j_g is None or not math.isfinite(melting_j_g) or melting_j_g <= 0:
        out["reason"] = "缺少有效熔融焓。"
    elif reference_j_g is None or not math.isfinite(reference_j_g) or reference_j_g <= 0:
        out["reason"] = "请输入完全结晶基体的参考熔融焓。"
    elif enthalpy_basis not in {"total_sample_mass", "matrix_mass"}:
        out["reason"] = "焓质量基准未知。"
    elif enthalpy_basis == "total_sample_mass" and (matrix_mass_fraction is None or not math.isfinite(matrix_mass_fraction) or not 0 < matrix_mass_fraction <= 1):
        out["reason"] = "请输入基体占整个试样的质量分数。"
    elif cold_status not in {"absent", "measured"}:
        out["reason"] = "请选择冷结晶修正方式。"
    elif cold_status == "measured" and (cold_j_g is None or not math.isfinite(cold_j_g) or cold_j_g < 0):
        out["reason"] = "请输入同一升温段的冷结晶焓（非负数）。"
    else:
        cold = 0. if cold_status == "absent" else cold_j_g
        w = matrix_mass_fraction if enthalpy_basis == "total_sample_mass" else 1.
        xc = (melting_j_g - cold) / (w * reference_j_g) * 100.
        if not 0 <= xc <= 100:
            out["reason"] = "结晶度超出 0–100%，请检查单位、基线、质量分数和参考焓。"
        else:
            out.update(Xc_percent=xc, status="estimate", reason="由输入的组成、参考焓和冷结晶修正计算。")
    return out
