"""Read NETZSCH exports and plain tables without losing time or provenance."""
from __future__ import annotations
from dataclasses import dataclass, field
from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import Path
import csv
import re
import numpy as np
import pandas as pd

TEMP_HINTS = ('temperature', 'temp', '温度')
FLOW_HINTS = ('heat flow', 'heatflow', 'heat_flow', 'dsc', '热流')


def data_files(folder):
    """List actual tables, ignoring Excel lock files created by an open workbook."""
    extensions = {'.xlsx', '.xls', '.csv', '.txt', '.tsv', '.dat'}
    return sorted(p for p in Path(folder).iterdir()
                  if p.is_file() and p.suffix.lower() in extensions and not p.name.startswith('~$'))


def _clean(value):
    return re.sub(r'\s+', ' ', str(value).strip().lower())


def _score(value, hints):
    return sum(3 if _clean(value) == h else 1 for h in hints if h in _clean(value))


def detect_columns(df):
    numeric = [c for c in df.columns if pd.to_numeric(df[c], errors='coerce').notna().sum() >= 3]
    if len(numeric) < 2:
        raise ValueError('需要至少两列数值：温度和热流。')
    temp = max(numeric, key=lambda c: _score(c, TEMP_HINTS))
    if not _score(temp, TEMP_HINTS):
        temp = numeric[0]
    remaining = [c for c in numeric if c != temp]
    flow = max(remaining, key=lambda c: _score(c, FLOW_HINTS))
    return str(temp), str(flow)


def _source_bytes(source):
    if isinstance(source, (str, Path)):
        p = Path(source)
        return p.name, p.read_bytes()
    if hasattr(source, 'getvalue'):
        return getattr(source, 'name', 'data.xlsx'), source.getvalue()
    source.seek(0)
    return getattr(source, 'name', 'data.xlsx'), source.read()


def read_table(source, sheet=0):
    name, data = _source_bytes(source)
    suffix = Path(name).suffix.lower()
    if suffix in {'.xlsx', '.xls'}:
        raw = pd.read_excel(BytesIO(data), sheet_name=sheet, header=None)
        rows = raw.where(raw.notna(), None).values.tolist()
    elif suffix in {'.csv', '.txt', '.tsv', '.dat'}:
        for encoding in ('utf-8-sig', 'utf-16', 'gb18030'):
            try:
                content = data.decode(encoding)
                break
            except UnicodeError:
                continue
        else:
            raise ValueError('无法识别文本编码，请保存为 UTF-8 CSV。')
        lines = content.splitlines()
        header_line = next((s for s in lines if '##' in s and ('temp' in s.lower() or '温度' in s)), None)
        probe = header_line or next((s for s in lines if s.strip() and not s.startswith('#')), '')
        try:
            delimiter = csv.Sniffer().sniff(probe, delimiters=',;\t').delimiter
        except csv.Error:
            delimiter = '\t' if '\t' in probe else ','
        rows = list(csv.reader(StringIO(content), delimiter=delimiter))
    else:
        raise ValueError(f'不支持的文件格式：{suffix}')
    metadata = {}
    header_index = None
    for i, row in enumerate(rows[:200]):
        if not row or row[0] is None or pd.isna(row[0]):
            continue
        first = str(row[0]).strip()
        if first.startswith('#') and not first.startswith('##'):
            metadata[first.lstrip('#').rstrip(':').strip()] = row[1] if len(row) > 1 and pd.notna(row[1]) else None
        if first.startswith('##') or (any(_score(c, TEMP_HINTS) for c in row) and any(_score(c, FLOW_HINTS) for c in row)):
            header_index = i
            break
    if header_index is None:
        header_index = next((i for i, row in enumerate(rows) if any(c is not None and pd.notna(c) and str(c).strip() for c in row)), None)
    if header_index is None:
        raise ValueError('表格为空。')
    header = [str(c).strip().lstrip('#') if c is not None and pd.notna(c) else f'Unnamed_{i}' for i, c in enumerate(rows[header_index])]
    if len(set(header)) != len(header):
        raise ValueError('表头包含重复列名，请先重命名。')
    body = [list(r[:len(header)]) + [None] * max(0, len(header) - len(r)) for r in rows[header_index + 1:]]
    df = pd.DataFrame(body, columns=header).dropna(how='all')
    df.attrs.update(metadata=metadata, source_name=name, source_sha256=sha256(data).hexdigest(), header_row=header_index + 1)
    return df


def load_dsc_file(uploaded):
    return read_table(uploaded)


def infer_flow_unit(column):
    c = _clean(column).replace(' ', '')
    if 'mw/mg' in c or ('w/g' in c and 'mw/g' not in c):
        return 'W/g'
    if 'mw/g' in c:
        return 'mW/g'
    if 'mw' in c:
        return 'mW'
    if re.search(r'(?:/|\(|\[)w(?:\)|\]|$)', c):
        return 'W'
    return None


@dataclass
class DSCRecord:
    source_name: str
    sample: str
    frame: pd.DataFrame
    metadata: dict
    source_sha256: str
    direction: str
    segment: int | None
    rate_k_min: float | None
    exo_sign: int | None
    mass_mg: float | None
    issues: list[str] = field(default_factory=list)


def parse_record(source, *, sheet=0, temperature_col=None, flow_col=None, time_col=None,
                 flow_unit=None, mass_mg=None, rate_k_min=None, time_unit=None):
    df = read_table(source, sheet)
    metadata = df.attrs['metadata']
    temp, flow = detect_columns(df)
    temp, flow = temperature_col or temp, flow_col or flow
    if temp == flow:
        raise ValueError('温度和热流不能选择同一列。')
    if any(token in _clean(temp) for token in ('°f', 'fahrenheit', '/k', '(k)', '[k]')):
        raise ValueError('本版温度需为摄氏度 °C，请先转换 Kelvin 或 Fahrenheit。')
    time_col = time_col or next((str(c) for c in df if 'time' in _clean(c) or '时间' in str(c)), None)
    mass = mass_mg if mass_mg is not None else metadata.get('SAMPLE MASS /mg')
    mass = float(mass) if mass is not None and pd.notna(mass) else None
    unit = flow_unit or infer_flow_unit(flow)
    if unit is None:
        raise ValueError('未识别热流单位，请在导入设置中明确选择 W/g、mW/mg、mW/g、mW 或 W。')
    if unit in {'mW', 'W'} and (mass is None or not np.isfinite(mass) or mass <= 0):
        raise ValueError('未按质量归一化的热流需要有效的样品质量（mg）。')
    factors = {'W/g': 1., 'mW/mg': 1., 'mW/g': .001, 'mW': 1. / mass if mass else 1., 'W': 1000. / mass if mass else 1.}
    if unit not in factors:
        raise ValueError('不支持该热流单位。')
    frame = pd.DataFrame({'Temperature_C': pd.to_numeric(df[temp], errors='coerce'),
                          'HeatFlow_W_g': pd.to_numeric(df[flow], errors='coerce') * factors[unit]})
    frame['Source_row'] = df.index.to_numpy() + df.attrs['header_row'] + 1
    issues = []
    if time_col:
        col = _clean(time_col)
        tu = time_unit or ('min' if 'min' in col or '分钟' in col else 's' if re.search(r'[/\[(]s(?:ec)?[\])]?$' , col) or '秒' in col else None)
        if tu not in {'min', 's'}:
            raise ValueError('时间列单位不明确，请指定 min 或 s。')
        frame['Time_s'] = pd.to_numeric(df[time_col], errors='coerce') * (60. if tu == 'min' else 1.)
    mask = np.isfinite(frame[['Temperature_C', 'HeatFlow_W_g']]).all(axis=1)
    if time_col:
        mask &= np.isfinite(frame['Time_s'])
    dropped = int((~mask).sum())
    if dropped:
        issues.append(f'剔除 {dropped} 行无效温度、热流或时间，源行号保留。')
    frame = frame.loc[mask].reset_index(drop=True)
    if len(frame) < 10:
        raise ValueError('至少需要 10 个有效数据点。')
    program = str(metadata.get('RANGE', ''))
    match = re.search(r'([+-]?\d+(?:\.\d+)?)\s*°?C/([\d.]+)\(K/min\)/([+-]?\d+(?:\.\d+)?)', program)
    program_direction = None
    rate = rate_k_min
    if match:
        start, r, end = map(float, match.groups())
        program_direction = 'heating' if end > start else 'cooling'
        rate = rate if rate is not None else r
    if 'Time_s' in frame:
        if frame['Time_s'].duplicated().any():
            raise ValueError('存在重复时间：可能将多个扫描段放在同一张表，请分段导入。')
        if not frame['Time_s'].is_monotonic_increasing:
            issues.append('源表时间非升序，已按时间恢复采集顺序。')
        frame = frame.sort_values('Time_s', kind='stable').reset_index(drop=True)
    elif rate is not None and (not np.isfinite(rate) or rate <= 0):
        raise ValueError('扫描速率必须是正数 K/min。')
    direction = program_direction or ('heating' if frame.Temperature_C.iloc[-1] > frame.Temperature_C.iloc[0] else 'cooling')
    t = frame.Temperature_C.to_numpy()
    backwards = (np.maximum.accumulate(t) - t) if direction == 'heating' else (t - np.minimum.accumulate(t))
    if np.max(backwards) > 15:
        raise ValueError('检测到超过 15°C 的反向扫描，请将升温、降温或多个循环分开导入。')
    if 'Time_s' not in frame:
        if rate is None:
            issues.append('无时间列和扫描速率：只能找峰，不能计算 J/g 焓。')
        else:
            travel = np.r_[0., np.cumsum(np.abs(np.diff(t)))]
            frame['Time_s'] = travel / (rate / 60.)
            issues.append('无时间列：按给定恒定扫描速率重建时间，焓为该假设下的估算。')
    if rate is None and 'Time_s' in frame:
        speed = np.abs(np.diff(t) / np.diff(frame.Time_s)) * 60.
        rate = float(np.median(speed[np.isfinite(speed)]))
    segment_match = re.search(r'S(\d+)', str(metadata.get('SEGMENT', '')))
    segment = int(segment_match.group(1)) if segment_match else None
    exo = metadata.get('EXO')
    exo = int(float(exo)) if exo is not None and pd.notna(exo) else None
    exo = exo if exo in {-1, 1} else None
    sample = Path(str(metadata.get('FILE') or re.sub(r'-\d+$', '', Path(df.attrs['source_name']).stem))).stem
    metadata = {**metadata, 'temperature_column': temp, 'heat_flow_column': flow, 'time_column': time_col,
                'input_heat_flow_unit': unit, 'header_row': df.attrs['header_row'], 'sheet': sheet}
    return DSCRecord(df.attrs['source_name'], sample, frame, metadata, df.attrs['source_sha256'], direction, segment, rate, exo, mass, issues)


def order_records(records):
    ordered = sorted(records, key=lambda r: (r.segment is None, r.segment if r.segment is not None else
                     float(r.frame.Time_s.min()) if 'Time_s' in r.frame else 0., r.source_name))
    counters = {'heating': 0, 'cooling': 0}
    result = []
    for record in ordered:
        counters[record.direction] += 1
        code = ('H' if record.direction == 'heating' else 'C') + str(counters[record.direction])
        result.append((code, record))
    return result
