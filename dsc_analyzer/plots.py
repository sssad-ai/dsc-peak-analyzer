import numpy as np
import plotly.graph_objects as go

COLORS = {"H1": "#e07a35", "C1": "#2368b4", "H2": "#b34689", "C2": "#238879"}


def overlay(records):
    fig = go.Figure()
    for stage, record in records:
        f = record.frame
        step = max(1, len(f) // 4000)
        f = f.iloc[::step]
        fig.add_trace(go.Scatter(x=f.Temperature_C, y=f.HeatFlow_W_g, name=stage,
                                line=dict(color=COLORS.get(stage, "#59657b"), width=2)))
    fig.update_layout(template="plotly_white", height=440, xaxis_title="导出温度 (°C)", yaxis_title="热流 (W/g)",
                      legend=dict(orientation="h", y=1.12), margin=dict(l=20, r=20, t=30, b=20), hovermode="x unified")
    return fig


def event_plot(record, result, stage):
    f, c, s = record.frame, result.curve, result.summary
    fig = go.Figure()
    sample = f.iloc[::max(1, len(f) // 4000)]
    fig.add_trace(go.Scatter(x=sample.Temperature_C, y=sample.HeatFlow_W_g, name="原始热流", line=dict(color="#aab5c4", width=1)))
    fig.add_trace(go.Scatter(x=c.Temperature_C, y=c.Smoothed_W_g, name="用于找峰的平滑曲线", line=dict(color=COLORS.get(stage, "#2368b4"), width=2)))
    fig.add_trace(go.Scatter(x=c.Temperature_C, y=c.Baseline_W_g, name="积分基线", line=dict(color="#333f53", dash="dash")))
    if s.get("integration_low_C") is not None:
        mask = c.Temperature_C.between(s["integration_low_C"], s["integration_high_C"])
        z = c.loc[mask]
        fig.add_trace(go.Scatter(x=z.Temperature_C, y=z.Baseline_W_g, mode="lines", line=dict(width=0), showlegend=False))
        fig.add_trace(go.Scatter(x=z.Temperature_C, y=z.Raw_interpolated_W_g, fill="tonexty", fillcolor="rgba(70,125,195,0.17)",
                                line=dict(width=0), name="积分区间"))
        for name, value in (("积分下界", s["integration_low_C"]), ("积分上界", s["integration_high_C"]),
                            ("Onset 估算", s.get("T_onset_est_C")), ("Endset 估算", s.get("T_endset_est_C"))):
            if value is not None:
                fig.add_vline(x=value, line_width=1, line_dash="dot", annotation_text=name, annotation_position="top")
    if not result.peaks.empty:
        px = result.peaks.T_peak_C.to_numpy()
        fig.add_trace(go.Scatter(x=px, y=np.interp(px, c.Temperature_C, c.Smoothed_W_g), mode="markers+text",
                                text=[f"P{i}" for i in result.peaks.Peak], textposition="top center", name="候选峰", marker=dict(size=9, color="#273b59")))
    fig.update_layout(template="plotly_white", height=490, xaxis_title="导出温度 (°C)", yaxis_title="热流 (W/g)",
                      xaxis_range=[result.settings["low_c"] - 8, result.settings["high_c"] + 8],
                      margin=dict(l=20, r=20, t=35, b=20), legend=dict(orientation="h", y=-.18))
    return fig
