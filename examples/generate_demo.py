"""Generate one entirely mathematical DSC sample (four stages, no experimental input)."""
from pathlib import Path
import numpy as np


def generate(destination=None):
    destination = Path(destination) if destination else Path(__file__).parent / 'synthetic_pp_gf'
    destination.mkdir(parents=True, exist_ok=True)
    stages = [('H1', 1, 30., 250., 10.), ('C1', 3, 250., -50., 20.),
              ('H2', 5, -50., 250., 10.), ('C2', 6, 250., 30., 20.)]
    for stage, segment, start, end, rate in stages:
        temperature = np.linspace(start, end, 3001)
        minutes = np.abs(temperature - start) / rate
        baseline = .2 + .0007 * temperature
        if stage.startswith('H'):
            heat_flow = baseline + .06 / (1. + np.exp(-(temperature + 8.) / 1.7))
            peak, amplitude = (166., 1.2) if stage == 'H1' else (163., .95)
            heat_flow += amplitude * np.exp(-.5 * ((temperature - peak) / 5.) ** 2)
        else:
            heat_flow = baseline - 1.4 * np.exp(-.5 * ((temperature - 118.) / 6.) ** 2)
        header = (f'#FILE:,synthetic.ngb-sdh\n#EXO:,-1\n#SAMPLE MASS /mg:,3\n#SEGMENT:,S{segment}/6\n'
                  f'#RANGE:,{start:g}°C/{rate:.1f}(K/min)/{end:g}°C\n'
                  '##Temp./°C,Time/min,DSC/(mW/mg)')
        np.savetxt(destination / f'synthetic-{stage}.csv', np.c_[temperature, minutes, heat_flow],
                   delimiter=',', header=header, comments='', fmt='%.10f', encoding='utf-8')


if __name__ == '__main__':
    generate()
