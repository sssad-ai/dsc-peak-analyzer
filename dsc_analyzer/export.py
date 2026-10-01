from datetime import datetime, timezone
from io import BytesIO
import html
import json
import zipfile
import pandas as pd


def build_package(sample, ordered, results, *, material, crystallinities, tg_results, include_raw=False):
    summaries, details = [], []
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for stage, record in ordered:
            result = results[stage]
            summaries.append({"sample": sample, "stage": stage, **result.summary,
                              "Xc_percent": crystallinities.get(stage, {}).get("Xc_percent"),
                              "Tg_midpoint_C": tg_results.get(stage, {}).get("Tg_midpoint_C"),
                              "Tg_status": tg_results.get(stage, {}).get("status"),
                              "Tg_reason": tg_results.get(stage, {}).get("reason")})
            details.append({"stage": stage, "source_file": record.source_name, "source_sha256": record.source_sha256,
                            "metadata": record.metadata, "settings": result.settings, "summary": result.summary,
                            "warnings": record.issues + result.warnings, "crystallinity": crystallinities.get(stage),
                            "tg": tg_results.get(stage)})
            archive.writestr(f"{stage}_peaks.csv", result.peaks.to_csv(index=False).encode("utf-8-sig"))
            archive.writestr(f"{stage}_baseline_curve.csv", result.curve.to_csv(index=False).encode("utf-8-sig"))
            if include_raw:
                archive.writestr(f"{stage}_normalized_raw.csv", record.frame.to_csv(index=False).encode("utf-8-sig"))
        archive.writestr("summary.csv", pd.DataFrame(summaries).to_csv(index=False).encode("utf-8-sig"))
        payload = {"application_version": "0.1.0", "exported_at_UTC": datetime.now(timezone.utc).isoformat(),
                   "sample": sample, "material": material, "stages": details,
                   "method_note": "Raw time integration; linear baseline; tangent temperatures and Tg are estimates; matrix composition and reference enthalpy are user inputs."}
        archive.writestr("analysis.json", json.dumps(payload, ensure_ascii=False, indent=2, default=str, allow_nan=False).encode("utf-8"))
    return buffer.getvalue(), pd.DataFrame(summaries)


def html_report(sample, summary, figures, material):
    fragments = []
    for i, (stage, figure) in enumerate(figures.items()):
        fragments.append(f"<h2>{html.escape(stage)}</h2>" + figure.to_html(full_html=False, include_plotlyjs=True if i == 0 else False))
    columns = {"stage": "阶段", "T_peak_C": "峰温 °C", "DeltaH_event_J_g": "事件焓 J/g试样",
               "T_onset_est_C": "Onset 估算 °C", "T_endset_est_C": "Endset 估算 °C",
               "integration_low_C": "积分下界 °C", "integration_high_C": "积分上界 °C",
               "Xc_percent": "表观结晶度 %", "Tg_midpoint_C": "Tg 估算 °C"}
    compact = summary[[c for c in columns if c in summary]].rename(columns=columns)
    table = compact.to_html(index=False, float_format=lambda v: f"{v:.4f}", escape=True, na_rep="—")
    fraction = material.get("matrix_mass_fraction")
    material_note = (f"基体：{material.get('matrix', '未填写')}；"
                     f"质量分数：{fraction * 100:g}%" if fraction is not None else
                     f"基体：{material.get('matrix', '未填写')}；质量分数：未填写")
    reference = material.get("reference_J_g")
    material_note += f"；参考熔融焓：{reference:g} J/g" if reference is not None else "；参考熔融焓：未填写"
    if material.get("source"):
        material_note += "；来源：" + material["source"]
    tg_notes = ""
    if "Tg_reason" in summary:
        tg_notes = "<h2>Tg 分析说明</h2>" + "".join(
            f"<p>{html.escape(str(row['stage']))}：{html.escape(str(row['Tg_reason']))}</p>"
            for _, row in summary.iterrows() if pd.notna(row.get("Tg_reason")))
    return ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>DSC analysis</title>"
            "<style>body{font-family:Arial,'Microsoft YaHei',sans-serif;margin:40px;color:#20304a}table{border-collapse:collapse;font-size:13px}"
            "td,th{padding:8px;border-bottom:1px solid #dde4ee}section{overflow:auto}h1{font-size:28px}</style>"
            f"<h1>{html.escape(sample)}</h1><p>DSC v0.1 · 分析结果</p>"
            "<p>焓按原始热流和时间计算，质量基准为整个试样；切线温度与 Tg 是方法相关估算。结晶度依赖用户填写的组成与参考焓。</p>"
            f"<p>{html.escape(material_note)}</p><section>{table}</section>{tg_notes}"
            + "".join(fragments) + "</html>").encode("utf-8")
