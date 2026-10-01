"""Simple local DSC workbench: import, inspect, optionally calculate crystallinity."""
from dataclasses import asdict, replace
from io import BytesIO
from pathlib import Path
from hashlib import sha256
import json
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dsc_analyzer.parser import parse_record, order_records, data_files
from dsc_analyzer.analysis import EventSettings, analyze_event, default_settings
from dsc_analyzer.materials import crystallinity
from dsc_analyzer.tg import analyze_tg
from dsc_analyzer.plots import overlay, event_plot
from dsc_analyzer.export import build_package, html_report

PROJECT = Path(__file__).resolve().parent
STAGE_NAMES = {'H1': '首次升温', 'C1': '首次降温', 'H2': '二次升温', 'C2': '二次降温'}
st.set_page_config(page_title='DSC 分析', page_icon='📈', layout='wide')
st.markdown('''<style>.block-container{max-width:1150px;padding-top:2rem}h1{font-size:2rem!important}
[data-testid="stMetric"]{background:#f4f6f9;padding:12px 16px;border-radius:10px}
[data-testid="stSidebar"]{background:#f7f8fa}</style>''', unsafe_allow_html=True)
st.title('DSC 分析')
st.caption('导入数据，查看曲线、峰温和焓。当前默认峰温区适用于 PP，可自行调整。')


@st.cache_data(show_spinner=False)
def cached_local(paths, options):
    return [parse_record(Path(p), **options) for p, _ in paths]


@st.cache_data(show_spinner=False)
def cached_upload(files, options):
    records = []
    for name, data in files:
        source = BytesIO(data)
        source.name = name
        records.append(parse_record(source, **options))
    return records


def display_value(value, unit=''):
    return '—' if value is None else f'{value:.2f}{unit}'


def stage_label(stage):
    return stage + ' · ' + STAGE_NAMES.get(stage, stage)


with st.sidebar:
    st.header('导入数据')
    modes = ['本机样品文件夹', '上传 Excel / 文本', '合成演示数据']
    mode = st.radio('选择来源', modes, index=2)
    with st.expander('导入设置'):
        exo_choice = st.selectbox('放热方向', ['从仪器识别', '放热向下', '放热向上'])
        sheet = st.number_input('工作表序号（从 0 开始）', min_value=0, value=0, step=1)
        temp_name = st.text_input('温度列名（空白自动识别）')
        flow_name = st.text_input('热流列名（空白自动识别）')
        time_name = st.text_input('时间列名（空白自动识别）')
        unit = st.selectbox('热流单位', ['从表头识别', 'W/g', 'mW/mg', 'mW/g', 'mW', 'W'])
        time_unit = st.selectbox('时间单位', ['从表头识别', 'min', 's'])
        mass = st.number_input('样品质量 mg（0 使用仪器值）', min_value=0., value=0., step=.1)
        rate = st.number_input('无时间列时的速率 K/min（0 不指定）', min_value=0., value=0., step=1.)
options = {'sheet': int(sheet), 'temperature_col': temp_name or None, 'flow_col': flow_name or None,
           'time_col': time_name or None, 'flow_unit': None if unit == '从表头识别' else unit,
           'time_unit': None if time_unit == '从表头识别' else time_unit,
           'mass_mg': mass or None, 'rate_k_min': rate or None}
folder = None
try:
    if mode == modes[0]:
        with st.sidebar:
            with st.expander('更换数据目录'):
                root_text = st.text_input('样品根目录', value='', placeholder='填写你的本机样品目录')
            if not root_text.strip():
                st.info('请填写样品目录，或选择上传 / 合成演示数据。')
                st.stop()
            root = Path(root_text)
            folders = sorted(p for p in root.iterdir() if p.is_dir() and data_files(p))
            if not folders:
                st.info('该目录没有含 DSC 表格的样品子文件夹。')
                st.stop()
            sample = st.selectbox('样品', [p.name for p in folders])
            folder = next(p for p in folders if p.name == sample)
            paths = data_files(folder)
            st.caption(f'当前样品：{len(paths)} 份数据')
        records = cached_local(tuple((str(p), p.stat().st_mtime_ns) for p in paths), options)
    elif mode == modes[1]:
        files = st.file_uploader('选择 DSC 数据（可同时上传四个阶段）',
                                type=['xlsx', 'xls', 'csv', 'txt', 'tsv', 'dat'], accept_multiple_files=True)
        if not files:
            st.info('上传后会自动绘图和分析。')
            st.stop()
        records = cached_upload(tuple((f.name, f.getvalue()) for f in files if not f.name.startswith('~$')), options)
        if not records:
            st.info('请选择实际数据文件，Excel 的 ~$ 临时文件不用于分析。')
            st.stop()
        groups = sorted(set(r.sample for r in records))
        sample = st.selectbox('样品组', groups) if len(groups) > 1 else groups[0]
        records = [r for r in records if r.sample == sample]
    else:
        paths = data_files(PROJECT / 'examples' / 'synthetic_pp_gf')
        records = cached_local(tuple((str(p), p.stat().st_mtime_ns) for p in paths), options)
        sample = '合成演示数据（非实验数据）'
except Exception as exc:
    st.error(f'读取失败：{exc}')
    st.caption('可在左侧“导入设置”指定工作表、列名和单位。')
    st.stop()
if exo_choice != '从仪器识别':
    records = [replace(r, exo_sign=-1 if exo_choice == '放热向下' else 1) for r in records]
if any(r.exo_sign is None for r in records):
    st.info('请在左侧“导入设置”选择放热方向。')
    st.stop()
ordered = order_records(records)
if [stage for stage, _ in ordered] != ['H1', 'C1', 'H2', 'C2']:
    st.caption('已按当前导入数据编号；H1/H2 表示当前导入的第几次升温。')
st.subheader(sample)

results = {}
for stage, record in ordered:
    # Separate import overrides so changing the EXO convention updates defaults.
    skey = 'settings:' + record.source_sha256 + ':' + str(record.exo_sign)
    if skey not in st.session_state:
        try:
            initial = default_settings(record)
        except ValueError:
            initial = EventSettings(float(record.frame.Temperature_C.min()), float(record.frame.Temperature_C.max()),
                                    -record.exo_sign if record.direction == 'heating' else record.exo_sign,
                                    'melting' if record.direction == 'heating' else 'crystallization')
        st.session_state[skey] = asdict(initial)
    try:
        results[stage] = analyze_event(record, EventSettings(**st.session_state[skey]))
    except ValueError as exc:
        st.error(f'{stage} 分析失败：{exc}')
        if st.button('恢复默认设置 ' + stage):
            del st.session_state[skey]
            st.rerun()
        st.stop()

peak_tab, tg_tab, xc_tab = st.tabs(['曲线与峰', '玻璃化转变 Tg', '结晶度（可选）'])
with peak_tab:
    active = st.selectbox('阶段', [s for s, _ in ordered], format_func=stage_label)
    record = dict(ordered)[active]
    result = results[active]
    skey = 'settings:' + record.source_sha256 + ':' + str(record.exo_sign)
    settings = EventSettings(**st.session_state[skey])
    a, b, c = st.columns(3)
    a.metric('主峰温度', display_value(result.summary.get('T_peak_C'), ' °C'))
    b.metric('熔融焓' if record.direction == 'heating' else '结晶焓', display_value(result.summary.get('DeltaH_event_J_g'), ' J/g'))
    c.metric('检出峰数', str(result.summary.get('peak_count', 0)))
    st.plotly_chart(event_plot(record, result, active), width='stretch', key='main_curve')
    if result.warnings:
        st.caption('；'.join(result.warnings))
    with st.expander('调整温区与积分'):
        with st.form('event_settings:' + skey):
            a, b = st.columns(2)
            low = a.number_input('分析温区下界 °C', value=float(settings.low_c))
            high = b.number_input('分析温区上界 °C', value=float(settings.high_c))
            method = st.selectbox('基线', ['edge_fit', 'endpoints'], index=['edge_fit', 'endpoints'].index(settings.baseline_method),
                                 format_func=lambda m: '两端区间均值连线' if m == 'edge_fit' else '两端点连线')
            manual = st.checkbox('手动积分边界', value=settings.boundary_mode == 'manual')
            a, b = st.columns(2)
            ilow = a.number_input('积分下界 °C', value=float(settings.integration_low_c if settings.integration_low_c is not None else result.summary.get('integration_low_C') or settings.low_c))
            ihigh = b.number_input('积分上界 °C', value=float(settings.integration_high_c if settings.integration_high_c is not None else result.summary.get('integration_high_C') or settings.high_c))
            if st.form_submit_button('应用设置', type='primary'):
                candidate = replace(settings, low_c=low, high_c=high, baseline_method=method,
                                    boundary_mode='manual' if manual else 'auto', integration_low_c=ilow if manual else None,
                                    integration_high_c=ihigh if manual else None)
                try:
                    analyze_event(record, candidate)
                    st.session_state[skey] = asdict(candidate)
                    # Render later input widgets before rerunning so Streamlit
                    # does not discard the user's crystallinity inputs.
                    st.session_state['_rerun_requested'] = True
                except ValueError as exc:
                    st.error(str(exc))
        st.caption('分析温区两端应落在峰外的基线区；积分边界控制计算面积。')
    with st.expander('峰参数'):
        if result.peaks.empty:
            st.write('当前温区未检出峰。')
        else:
            names = {'Peak': '编号', 'T_peak_C': '峰温 °C', 'Height_above_baseline_W_g': '峰高 W/g',
                     'Width_half_prominence_C': '半显著性宽度 °C'}
            st.dataframe(result.peaks[list(names)].rename(columns=names).round(3), width='stretch', hide_index=True)
        a, b = st.columns(2)
        a.metric('Onset 估算', display_value(result.summary.get('T_onset_est_C'), ' °C'))
        b.metric('Endset 估算', display_value(result.summary.get('T_endset_est_C'), ' °C'))
    with st.expander('四阶段对比'):
        st.plotly_chart(overlay(ordered), width='stretch', key='overview')
        st.dataframe(pd.DataFrame([{'阶段': stage_label(s), '峰温 °C': r.summary.get('T_peak_C'),
                                   '焓 J/g': r.summary.get('DeltaH_event_J_g')} for s, r in results.items()]).round(3),
                     width='stretch', hide_index=True)
    if folder:
        images = sorted(list(folder.glob('*.jpg')) + list(folder.glob('*.png')))
        if images:
            with st.expander('原仪器图'):
                for image in images:
                    st.image(str(image), caption=image.name)

heating = [(s, r) for s, r in ordered if r.direction == 'heating']
tg_results, tg_fits = {}, {}
with tg_tab:
    if heating:
        chosen_tg = st.selectbox('升温阶段', [s for s, _ in heating], index=len(heating) - 1, format_func=stage_label)
        with st.expander('调整 Tg 温区'):
            a, b = st.columns(2)
            tg_low = a.number_input('Tg 温区下界 °C', value=-30., step=1., key='tg_low:' + sample)
            tg_high = b.number_input('Tg 温区上界 °C', value=20., step=1., key='tg_high:' + sample)
        for stage, record in heating:
            try:
                fit = analyze_tg(record, tg_low, tg_high)
                tg_results[stage], tg_fits[stage] = fit.summary, fit
            except ValueError as exc:
                tg_results[stage] = {'Tg_midpoint_C': None, 'status': 'invalid_settings', 'reason': str(exc)}
        info = tg_results[chosen_tg]
        if info['Tg_midpoint_C'] is not None:
            st.metric('Tg 中点估算', display_value(info['Tg_midpoint_C'], ' °C'))
            st.caption(info['reason'])
        else:
            st.info('当前无法确定 Tg。' + info['reason'])
        fit = tg_fits.get(chosen_tg)
        if fit is not None and not fit.curve.empty:
            fig = go.Figure()
            for col, label in [('HeatFlow_W_g', '热流'), ('Before_baseline_W_g', '转变前基线'),
                               ('After_baseline_W_g', '转变后基线'), ('Step_fit_W_g', '台阶拟合')]:
                if col in fit.curve:
                    fig.add_trace(go.Scatter(x=fit.curve.Temperature_C, y=fit.curve[col], name=label))
            if info['Tg_midpoint_C'] is not None:
                fig.add_vline(x=info['Tg_midpoint_C'], line_dash='dash', annotation_text='Tg')
            fig.update_layout(template='plotly_white', height=380, xaxis_title='温度 (°C)', yaxis_title='热流 (W/g)', legend=dict(orientation='h'))
            st.plotly_chart(fig, width='stretch', key='tg_curve')
        if info.get('checks'):
            with st.expander('分析说明'):
                st.dataframe(pd.DataFrame([{'检查项': key, '结果': '通过' if value else '未通过'}
                                           for key, value in info['checks'].items()]), hide_index=True, width='stretch')
        st.caption('Tg 使用热容台阶拟合。熔融峰和结晶峰的位置不能用来代替 Tg。')
    else:
        st.info('请导入升温数据后分析 Tg。')

material = {}
xc = {}
with xc_tab:
    enabled = st.checkbox('计算结晶度', key='xc_enabled:' + sample)
    if enabled:
        a, b = st.columns(2)
        with a:
            matrix = st.text_input('基体材料', placeholder='例如 PP', key='matrix:' + sample)
            pp_percent = st.number_input('基体质量分数 wt%', min_value=.1, max_value=100., value=None,
                                         placeholder='例如 60，须扣除所有非基体成分', step=1., key='fraction:' + sample)
        with b:
            reference = st.number_input('完全结晶基体参考熔融焓 J/g', min_value=.1, value=None,
                                        placeholder='根据基体类型和参考文献填写', step=1., key='reference:' + sample)
            source = st.text_input('组成与参考焓来源（可选）', key='source:' + sample)
        material = {'matrix': matrix.strip(), 'matrix_mass_fraction': pp_percent / 100 if pp_percent is not None else None,
                    'reference_J_g': reference, 'source': source}
        st.caption('焓按整个试样质量归一化；基体质量分数应扣除玻纤、填料和其他非基体成分。')
        for stage, record in heating:
            st.markdown('**' + stage_label(stage) + '**')
            choice = st.selectbox('冷结晶修正 ' + stage, ['请选择', '无冷结晶，采用 0', '输入同段冷结晶焓'], key='cold:' + sample + ':' + stage)
            cold_h = None
            status = 'unspecified'
            if choice == '无冷结晶，采用 0':
                cold_h, status = 0., 'absent'
            elif choice == '输入同段冷结晶焓':
                cold_h = st.number_input('冷结晶焓 J/g ' + stage, min_value=0., value=None, step=.1, key='cold_h:' + sample + ':' + stage)
                status = 'measured'
            melt_h = results[stage].summary.get('DeltaH_event_J_g')
            estimate = crystallinity(melt_h, matrix_mass_fraction=material['matrix_mass_fraction'],
                                     cold_j_g=cold_h, cold_status=status, reference_j_g=reference)
            if not matrix.strip():
                estimate.update(Xc_percent=None, status='unavailable', reason='请输入基体材料名称。')
            xc[stage] = estimate
            if estimate['Xc_percent'] is None:
                st.caption(estimate['reason'])
            else:
                st.metric(stage + ' 表观结晶度', f"{estimate['Xc_percent']:.2f}%")
        if not heating:
            st.info('结晶度需要升温熔融焓，请导入升温数据。')
        with st.expander('计算公式'):
            st.latex(r'X_c = \frac{\Delta H_m-\Delta H_{cc}}{w_{matrix}\,\Delta H_m^0}\times100\%')
            st.write('冷结晶焓来自同一升温段。C1/C2 的冷却结晶焓不用于这里的修正。参考焓由用户自行选取。')
    else:
        st.caption('需要时开启，并填写基体材料、质量分数与参考熔融焓。')

with st.expander('下载结果'):
    include_raw = st.checkbox('附带归一化原始数据')
    package, export_summary = build_package(sample, ordered, results, material=material, crystallinities=xc,
                                            tg_results=tg_results, include_raw=include_raw)
    a, b = st.columns(2)
    a.download_button('下载结果 ZIP', package, 'dsc_analysis.zip', 'application/zip', type='primary')
    b.download_button('下载汇总 CSV', export_summary.to_csv(index=False).encode('utf-8-sig'), 'dsc_summary.csv', 'text/csv')
    # ZIP contains an export timestamp: use the actual content/conditions for report invalidation.
    html_key = sha256(json.dumps({'sample': sample, 'source': [r.source_sha256 for _, r in ordered],
                                 'material': material, 'summary': export_summary.to_dict('records'),
                                 'settings': {s: r.settings for s, r in results.items()}, 'tg': tg_results,
                                 'xc': xc}, sort_keys=True, default=str).encode()).hexdigest()
    if st.button('生成离线 HTML 报告'):
        figures = {stage: event_plot(record, results[stage], stage) for stage, record in ordered}
        st.session_state['generated_html'] = (html_key, html_report(sample, export_summary, figures, material))
    saved_html = st.session_state.get('generated_html')
    if saved_html and saved_html[0] == html_key:
        st.download_button('下载离线报告', saved_html[1], 'dsc_report.html', 'text/html')
    st.caption('导出保留文件摘要、分析参数、基线和峰表。原文件不会被修改。')

if st.session_state.pop('_rerun_requested', False):
    st.rerun()
