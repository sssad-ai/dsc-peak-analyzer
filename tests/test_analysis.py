from dataclasses import replace
from io import BytesIO
import math
import numpy as np
import pandas as pd
import pytest
from dsc_analyzer.parser import DSCRecord, parse_record, order_records, data_files
from dsc_analyzer.analysis import analyze_event, EventSettings
from dsc_analyzer.materials import crystallinity
from dsc_analyzer.tg import analyze_tg
from dsc_analyzer.export import build_package
import zipfile
import json


def record(cooling=False, peaks=1, time=True):
    x = np.linspace(70, 210, 4201)
    baseline = .2 + .001 * x
    y = baseline + np.exp(-.5 * ((x - 160) / 5) ** 2)
    if peaks == 2:
        y += .7 * np.exp(-.5 * ((x - 175) / 3) ** 2)
    if cooling:
        x = x[::-1]
        y = 2 * (.2 + .001 * x) - y[::-1]
    frame = pd.DataFrame({'Temperature_C': x, 'HeatFlow_W_g': y})
    if time:
        frame['Time_s'] = np.arange(len(x)) * .2  # 10 K/min, independent of direction
    return DSCRecord('synthetic.csv', 'synthetic', frame, {}, 'abc123', 'cooling' if cooling else 'heating', 1, 10., -1, 3.)


def settings(cooling=False):
    return EventSettings(110., 200., -1 if cooling else 1, 'crystallization' if cooling else 'melting',
                         boundary_mode='manual', integration_low_c=125., integration_high_c=195.)


@pytest.mark.parametrize('cooling', [False, True])
def test_gaussian_enthalpy_in_j_per_g_and_cooling_sign(cooling):
    result = analyze_event(record(cooling), settings(cooling))
    # Analytic integral A*sigma*sqrt(2*pi) / beta; beta=10/60 K/s.
    expected = 5 * math.sqrt(2 * math.pi) / (10 / 60)
    assert result.summary['DeltaH_event_J_g'] == pytest.approx(expected, rel=.001)
    assert result.summary['DeltaH_signed_J_g'] * (-1 if cooling else 1) > 0
    assert abs(result.summary['T_peak_C'] - 160) < .1
    onset, endset = result.summary['T_onset_est_C'], result.summary['T_endset_est_C']
    assert onset > endset if cooling else onset < endset


def test_overlapping_peaks_integrated_once():
    r = analyze_event(record(peaks=2), settings())
    expected = (5 + .7 * 3) * math.sqrt(2 * math.pi) / (10 / 60)
    assert r.summary['peak_count'] == 2
    assert r.summary['DeltaH_event_J_g'] == pytest.approx(expected, rel=.002)


def test_missing_time_does_not_produce_fake_enthalpy():
    r = analyze_event(record(time=False), settings())
    assert r.summary['T_peak_C'] is not None
    assert r.summary['DeltaH_event_J_g'] is None


def csv_source(text):
    f = BytesIO(text.encode('utf-8'))
    f.name = 'test.csv'
    return f


@pytest.mark.parametrize('unit,mass,factor', [('mW/mg', 2., 1.), ('W/g', 2., 1.), ('mW/g', 2., .001), ('mW', 2., .5), ('W', 2., 500.)])
def test_heat_flow_unit_normalization(unit, mass, factor):
    text = 'Temperature (°C),Time/s,Heat Flow (' + unit + ')\n' + '\n'.join(f'{i},{i},2' for i in range(20))
    r = parse_record(csv_source(text), mass_mg=mass)
    assert np.allclose(r.frame.HeatFlow_W_g, 2 * factor)


def test_netzsch_metadata_and_reversed_cooling_time():
    text = '#EXO:,-1\n#SEGMENT:,S3/6\n#RANGE:,250°C/20.0(K/min)/-50°C\n#SAMPLE MASS /mg:,3.2\n##Temp./°C,Time/min,DSC/(mW/mg)\n'
    text += '\n'.join(f'{100+i},{20-i/60},-1' for i in range(20))
    r = parse_record(csv_source(text))
    assert r.direction == 'cooling' and r.segment == 3 and r.exo_sign == -1
    assert r.frame.Time_s.is_monotonic_increasing
    assert r.frame.Temperature_C.iloc[0] > r.frame.Temperature_C.iloc[-1]
    assert r.mass_mg == 3.2 and r.rate_k_min == 20


def test_stage_order_uses_instrument_not_filename():
    records = [replace(record(), source_name='sample-1.csv', segment=5), replace(record(), source_name='sample-2.csv', segment=1),
               replace(record(True), source_name='sample-3.csv', segment=6), replace(record(True), source_name='sample-4.csv', segment=3)]
    ordered = order_records(records)
    assert [s for s, _ in ordered] == ['H1', 'C1', 'H2', 'C2']
    assert [r.source_name for _, r in ordered] == ['sample-2.csv', 'sample-4.csv', 'sample-1.csv', 'sample-3.csv']


def test_duplicate_time_rejected():
    with pytest.raises(ValueError, match='重复时间'):
        parse_record(csv_source('Temperature,Time/s,HeatFlow (W/g)\n' + '\n'.join(f'{i},0,1' for i in range(20))))


def test_no_time_rate_reconstructed_with_units():
    r = parse_record(csv_source('Temperature,HeatFlow (W/g)\n' + '\n'.join(f'{i},1' for i in range(20))), rate_k_min=10)
    assert r.frame.Time_s.iloc[-1] == pytest.approx(19 * 6)


def test_unknown_unit_rejected():
    with pytest.raises(ValueError, match='未识别热流单位'):
        parse_record(csv_source('Temperature,HeatFlow\n' + '\n'.join(f'{i},1' for i in range(20))))


def test_crystallinity_mass_and_cold_correction():
    r = crystallinity(62.7, matrix_mass_fraction=.6, cold_j_g=12.54, cold_status='measured', reference_j_g=209.)
    assert r['Xc_percent'] == pytest.approx(40.)
    # A matrix-normalized enthalpy must not be divided by .6 again.
    r = crystallinity(83.6, matrix_mass_fraction=.6, cold_status='absent', enthalpy_basis='matrix_mass', reference_j_g=209.)
    assert r['Xc_percent'] == pytest.approx(40.)


@pytest.mark.parametrize('kwargs', [{}, {'matrix_mass_fraction': .6}, {'matrix_mass_fraction': .6, 'cold_status': 'measured', 'reference_j_g': 209.},
                                  {'matrix_mass_fraction': 0., 'cold_status': 'absent', 'reference_j_g': 209.},
                                  {'matrix_mass_fraction': .1, 'cold_status': 'absent', 'reference_j_g': 209.}])
def test_missing_or_impossible_crystallinity_unavailable(kwargs):
    assert crystallinity(62.7, **kwargs)['Xc_percent'] is None


def test_no_reference_enthalpy_is_inferred_from_material():
    assert crystallinity(62.7, matrix_mass_fraction=.6, cold_status='absent')['Xc_percent'] is None


def test_excel_temporary_lock_files_are_ignored(tmp_path):
    for name in ['data.xlsx', '~$data.xlsx', 'DATA.CSV', 'instrument.jpg']:
        (tmp_path / name).touch()
    assert {p.name for p in data_files(tmp_path)} == {'data.xlsx', 'DATA.CSV'}


def test_tg_logistic_midpoint_and_no_linear_false_positive():
    r = record()
    x = np.linspace(-40, 30, 2101)
    y = .1 + .0002 * x + .07 / (1 + np.exp(-(x + 8) / 1.7))
    r.frame = pd.DataFrame({'Temperature_C': x, 'HeatFlow_W_g': y, 'Time_s': np.arange(len(x)) * .2})
    tg = analyze_tg(r)
    assert tg.summary['Tg_midpoint_C'] == pytest.approx(-8, abs=.5)
    r.frame['HeatFlow_W_g'] = .1 + .0002 * x
    assert analyze_tg(r).summary['Tg_midpoint_C'] is None


def test_first_heating_missing_low_temperature_has_no_tg():
    result = analyze_tg(record()).summary
    assert result['Tg_midpoint_C'] is None
    assert result['reason_code'] == 'not_covered'
    assert '70.0' in result['reason']


def test_reversed_step_reports_actual_reason_and_keeps_plot():
    r = record()
    x = np.linspace(-40, 30, 2101)
    r.frame = pd.DataFrame({'Temperature_C': x, 'HeatFlow_W_g': .1 + .0002 * x - .07 / (1 + np.exp(-(x + 8) / 1.7))})
    result = analyze_tg(r)
    assert result.summary['Tg_midpoint_C'] is None
    assert 'wrong_step_direction' in result.summary['failed_checks']
    assert not result.summary['checks']['台阶方向符合升温热容增加']
    assert 'Step_fit_W_g' in result.curve


def test_exo_up_has_positive_heat_capacity_estimate():
    r = replace(record(), exo_sign=1)
    x = np.linspace(-40, 30, 2101)
    r.frame = pd.DataFrame({'Temperature_C': x, 'HeatFlow_W_g': .1 - .07 / (1 + np.exp(-(x + 8) / 1.7))})
    result = analyze_tg(r).summary
    assert result['Tg_midpoint_C'] == pytest.approx(-8, abs=.5)
    assert result['DeltaCp_est_J_g_K'] > 0


def test_narrow_tg_window_initial_width_within_bounds():
    r = record()
    x = np.linspace(-12, -4, 1601)
    r.frame = pd.DataFrame({'Temperature_C': x, 'HeatFlow_W_g': .1 + .07 / (1 + np.exp(-(x + 8) / .4))})
    result = analyze_tg(r, -11, -5)
    assert result.summary['Tg_midpoint_C'] == pytest.approx(-8, abs=.15)


def test_export_preserves_parameters_and_raw_opt_in():
    r = record()
    a = analyze_event(r, settings())
    payload, summary = build_package('sample', [('H1', r)], {'H1': a}, material={}, crystallinities={}, tg_results={})
    with zipfile.ZipFile(BytesIO(payload)) as z:
        assert 'H1_normalized_raw.csv' not in z.namelist()
        data = json.loads(z.read('analysis.json'))
        assert data['stages'][0]['source_sha256'] == r.source_sha256
        assert data['stages'][0]['settings']['integration_low_c'] == 125.
        assert 'review_receipt' not in data['stages'][0]
        assert 'review_status' not in summary.columns


def test_source_not_mutated_by_analysis():
    r = record(True)
    before = r.frame.copy(deep=True)
    analyze_event(r, settings(True))
    pd.testing.assert_frame_equal(r.frame, before)
