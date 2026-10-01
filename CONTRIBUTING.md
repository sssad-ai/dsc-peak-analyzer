# Contributing

Keep the scientific engine separate from the interface. Include units, mass basis, assumptions, and unavailable-result behavior with any new quantity. Do not replace missing data with a plausible number.

Validate consequential changes with an independent calculation or analytic example, then exercise the affected interface. Run `python -m pytest -q` before submitting a change.

Experimental files, material PDFs and local outputs are excluded by default. Use synthetic or explicitly authorized public datasets for examples. Do not add private measurements to a public repository without confirming ownership and disclosure scope.

For an issue, describe the import format, columns and units, scanning program, chosen baseline, integration limits, expected result, actual result and package versions. Provide a minimal shareable example.

Before a v0.1 release, read `docs/VALIDATION.zh-CN.md`, retain its current limitations, and confirm the source archive contains no private data or outputs. A local source archive is not a published GitHub repository.
