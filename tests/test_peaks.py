import numpy as np
from dsc_analyzer.peaks import analyze_peaks

def test_detects_synthetic_peak():
    x = np.linspace(20, 250, 1000)
    y = 0.0005 * (x - 20) + 1.8 * np.exp(-0.5 * ((x - 125) / 7) ** 2)
    peaks, _ = analyze_peaks(x, y, direction="up", prominence=0.2, smoothing_window=11)
    assert len(peaks) >= 1
    closest = peaks.iloc[(peaks["T_peak"] - 125).abs().argmin()]
    assert abs(closest["T_peak"] - 125) < 2
