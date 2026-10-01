from pathlib import Path
from streamlit.testing.v1 import AppTest


def find(widgets, label):
    return next(w for w in widgets if w.label == label)


def demo():
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'), default_timeout=60).run()
    find(app.radio, '选择来源').set_value('合成演示数据').run()
    return app


def test_simple_ui_has_no_review_or_presets_and_blank_crystallinity_inputs():
    app = demo()
    assert not app.exception
    assert [tab.label for tab in app.tabs] == ['曲线与峰', '玻璃化转变 Tg', '结晶度（可选）']
    assert not any('复核' in widget.label or '牌号' in widget.label for widget in list(app.text_input) + list(app.button) + list(app.selectbox))
    assert not app.get('json')
    assert find(app.selectbox, '升温阶段').value == 'H2'
    assert not any('结晶度' in metric.label for metric in app.metric)
    find(app.checkbox, '计算结晶度').check().run()
    assert find(app.text_input, '基体材料').value == ''
    assert find(app.number_input, '基体质量分数 wt%').value is None
    assert find(app.number_input, '完全结晶基体参考熔融焓 J/g').value is None
    assert not any('结晶度' in metric.label for metric in app.metric)


def test_demo_manual_integration_and_composition_recalculate():
    app = demo()
    find(app.checkbox, '计算结晶度').check().run()
    find(app.text_input, '基体材料').set_value('PP')
    find(app.number_input, '基体质量分数 wt%').set_value(60.)
    find(app.number_input, '完全结晶基体参考熔融焓 J/g').set_value(209.)
    find(app.selectbox, '冷结晶修正 H1').set_value('无冷结晶，采用 0').run()
    value1 = float(next(m.value.rstrip('%') for m in app.metric if m.label == 'H1 表观结晶度'))
    find(app.number_input, '基体质量分数 wt%').set_value(80.).run()
    value2 = float(next(m.value.rstrip('%') for m in app.metric if m.label == 'H1 表观结晶度'))
    assert abs(value2 / value1 - .75) < .001
    find(app.checkbox, '手动积分边界').check()
    find(app.number_input, '积分下界 °C').set_value(158.)
    find(app.number_input, '积分上界 °C').set_value(172.)
    find(app.button, '应用设置').click().run()
    value3 = float(next(m.value.rstrip('%') for m in app.metric if m.label == 'H1 表观结晶度'))
    assert value3 < value2
    find(app.checkbox, '计算结晶度').uncheck().run()
    assert not any('结晶度' in metric.label for metric in app.metric)
    assert not app.exception


def test_tg_invalid_window_shows_readable_message_without_exception():
    app = demo()
    find(app.number_input, 'Tg 温区上界 °C').set_value(-29.).run()
    assert not app.exception
    assert any('至少需要 5°C' in info.value for info in app.info)
