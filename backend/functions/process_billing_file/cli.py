"""Local command-line entry point for the billing analysis pipeline.

This module is intended for development and testing. AWS Lambda uses
handler.py instead; cli.py provides a convenient way to run the same
parser and analytics logic against a local CSV file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import process_csv_file


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and analyze a cloud billing CSV file."
    )
    parser.add_argument(
        "--file",
        required=True,
        help="Path to the billing CSV file.",
    )
    parser.add_argument(
        "--output",
        default="analysis_output.json",
        help="Path for the generated analysis JSON response.",
    )
    args = parser.parse_args()

    analysis = process_csv_file(Path(args.file))
    output_path = Path(args.output)
    output_path.write_text(json.dumps(analysis, indent=2), encoding="utf-8")

    summary = analysis["summary"]
    print(f"Analysis ID: {analysis['analysisId']}")
    print(f"Organization: {analysis['organizationId']}")
    print(f"Total cost: ${summary['totalCost']:.2f}")
    print(f"Estimated savings: ${summary['estimatedSavings']:.2f}")
    print(f"Recommendations: {summary['recommendationCount']}")
    print(f"Anomalies: {summary['anomalyCount']}")
    print(f"Saved complete response to: {output_path}")


if __name__ == "__main__":
    main()
