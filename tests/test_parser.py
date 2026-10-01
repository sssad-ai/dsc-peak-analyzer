import pandas as pd
from dsc_analyzer.parser import detect_columns

def test_detect_columns():
    df = pd.DataFrame({"Temperature (°C)": [20, 30, 40], "Heat Flow (mW/mg)": [0.1, 0.2, 0.1]})
    t, h = detect_columns(df)
    assert t == "Temperature (°C)"
    assert h == "Heat Flow (mW/mg)"
