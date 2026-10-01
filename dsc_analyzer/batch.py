"""Read-only batch analysis. Private measurements remain outside the public repo."""
from pathlib import Path
import argparse
import json
import pandas as pd
from .parser import parse_record, order_records, data_files
from .analysis import analyze_event, default_settings
from .tg import analyze_tg


def analyze_directory(root):
    root = Path(root)
    rows, metadata, errors, tg_rows = [], [], [], []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        files = data_files(folder)
        if not files:
            continue
        records = []
        for file in files:
            try:
                records.append(parse_record(file))
            except Exception as exc:
                errors.append({"sample": folder.name, "file": file.name, "error": str(exc)})
        ordered = order_records(records)
        completeness = "complete" if [s for s, _ in ordered] == ["H1", "C1", "H2", "C2"] else "incomplete_or_unexpected"
        for stage, record in ordered:
            try:
                result = analyze_event(record, default_settings(record))
                rows.append({"sample": folder.name, "stage": stage, "source_file": record.source_name,
                             "source_sha256": record.source_sha256, "instrument_segment": record.segment,
                             "scan_rate_K_min": record.rate_k_min, "sample_mass_mg": record.mass_mg,
                             "stage_completeness": completeness, **result.summary,
                             "warnings": "；".join(record.issues + result.warnings)})
                metadata.append({"sample": folder.name, "stage": stage, "source_file": record.source_name,
                                 "metadata": record.metadata, "settings": result.settings, "peak_table": result.peaks.to_dict("records")})
                if stage.startswith("H"):
                    tg_rows.append({"sample": folder.name, "stage": stage, **analyze_tg(record).summary})
            except Exception as exc:
                errors.append({"sample": folder.name, "file": record.source_name, "stage": stage, "error": str(exc)})
    return pd.DataFrame(rows), metadata, errors, pd.DataFrame(tg_rows)


def main():
    parser = argparse.ArgumentParser(description="PP/GF DSC batch analysis without modifying source data")
    parser.add_argument("data_root", type=Path)
    parser.add_argument("--output", type=Path, default=Path("outputs/batch"))
    args = parser.parse_args()
    rows, metadata, errors, tg = analyze_directory(args.data_root)
    args.output.mkdir(parents=True, exist_ok=True)
    rows.to_csv(args.output / "stage_results.csv", index=False, encoding="utf-8-sig")
    tg.to_csv(args.output / "tg_candidates.csv", index=False, encoding="utf-8-sig")
    (args.output / "analysis_details.json").write_text(json.dumps({"metadata": metadata, "errors": errors}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"samples": int(rows['sample'].nunique()) if len(rows) else 0, "stages": len(rows), "errors": len(errors), "output": str(args.output)}, ensure_ascii=False))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
