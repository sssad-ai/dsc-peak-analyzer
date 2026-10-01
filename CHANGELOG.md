# Changelog

## 0.1.0 — 2026-10-01

- Replace the prototype's temperature/FWHM area with normalized raw-time enthalpy integration.
- Read NETZSCH metadata and restore the four-stage scanning order.
- Provide visible baselines and editable integration windows.
- Simplify the UI to curves/peaks, Tg, and optional crystallinity; remove review workflows and preset material grades.
- Require user-supplied matrix identity, mass fraction, reference enthalpy and cold-crystallization treatment.
- Explain individual Tg rejection checks and preserve diagnostic curves; fix narrow-window initialization and EXO-up heat-capacity sign.
- Ignore Excel lock files when reading local samples.
- Export parameters, source digests, curves, CSV/ZIP and offline reports.
- Include synthetic examples, scientific regression tests and validation limitations.
