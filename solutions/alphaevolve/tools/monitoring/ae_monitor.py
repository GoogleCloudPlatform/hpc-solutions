#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""AlphaEvolve Experiment Monitoring and Post-Processing CLI Tool.

This script provides a powerful command-line interface to monitor live AlphaEvolve
experiments, inspect status and statistics, retrieve and unflatten generated
programs, compute metric distributions and leaderboards, analyze evolutionary lineage,
and export source code and data summaries locally.

Usage Examples:
  # Check status and run complete post-processing with default env vars:
  python3 tools/monitoring/ae_monitor.py --action all --output-dir ./results

  # Continuously monitor an experiment polling every 30 seconds:
  python3 tools/monitoring/ae_monitor.py --action monitor --poll-interval 30

  # Post-process specific experiment and export top 5 programs by execution_time:
  python3 tools/monitoring/ae_monitor.py --action postprocess -e projects/.../alphaEvolveExperiments/my_exp \
      --target-metric execution_time --top-k 5 --output-dir ./my_exp_results
"""

import argparse
import logging
import sys
from pathlib import Path

logger = logging.getLogger("ae_monitor")

# Add project root and google_framework to sys.path
project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

gf_path = project_root / "google_framework"
if str(gf_path) not in sys.path:
    sys.path.insert(0, str(gf_path))

from alpha_evolve.monitoring import AlphaEvolveMonitor


def parse_args():
    parser = argparse.ArgumentParser(
        description="Monitor and post-process AlphaEvolve experiments.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-p",
        "--project-id",
        type=str,
        default=None,
        help="GCP project ID override (falls back to experiment name prefix or _PROJECT_ID env var).",
    )
    parser.add_argument(
        "-e",
        "--experiment-name",
        type=str,
        default=None,
        help="Full resource name or short ID of the experiment (falls back to _USER_EXPERIMENT_NAME env var).",
    )
    parser.add_argument(
        "-s",
        "--session-name",
        type=str,
        default=None,
        help="Full resource name or short ID of the session (falls back to _SESSION_NAME env var).",
    )
    parser.add_argument(
        "--action",
        type=str,
        choices=["status", "monitor", "postprocess", "all", "list", "list-experiments"],
        default="all",
        help="Action to perform: status (metadata only), monitor (continuous polling loop), postprocess (metrics, lineage, export), all, or list (show project experiments).",
    )
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Force interactive prompt to select an experiment from a numbered list.",
    )
    parser.add_argument(
        "-b",
        "--bucket",
        type=str,
        default=None,
        help="GCS bucket name to load environment config from (falls back to _CLOUD_BUCKET_NAME env var).",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=60,
        help="Polling interval in seconds when action is 'monitor'.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Maximum duration in seconds for monitoring loop (None for unlimited).",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=str,
        default="./ae_postprocess_results",
        help="Local destination directory to export CSV leaderboards, JSON statistics, and program code.",
    )
    parser.add_argument(
        "--target-metric",
        type=str,
        default=None,
        help="Metric name to sort the leaderboard by (defaults to primary metric found or 'score').",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Number of top programs (ranked by target metric) to export unflattened code files for.",
    )
    parser.add_argument(
        "--no-export-code",
        dest="export_code",
        action="store_false",
        default=True,
        help="Disable exporting individual program source code files to output directory.",
    )
    parser.add_argument(
        "--state-filter",
        type=str,
        default=None,
        help="Filter programs by state (e.g. COMPLETED, EVALUATING, GENERATING).",
    )
    parser.add_argument(
        "--order-by",
        type=str,
        default=None,
        help="Order criteria for API list requests (e.g. 'score desc').",
    )
    parser.add_argument(
        "--max-programs",
        type=int,
        default=None,
        help="Maximum number of programs to fetch across pagination requests.",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Set logging verbosity level.",
    )
    return parser.parse_args()


def print_status_summary(status: dict):
    print("\n" + "=" * 60)
    print("=== ALPHAEVOLVE EXPERIMENT STATUS ===")
    print("=" * 60)
    print(f"Experiment Name : {status.get('name')}")
    print(f"Short ID        : {status.get('short_id')}")
    print(f"State           : {status.get('state')}")
    print(f"Created Time    : {status.get('create_time')}")
    stats = status.get("stats", {})
    print(f"Programs Gen    : {stats.get('candidates_count', 0)}")
    print(f"Programs Eval   : {stats.get('evaluated_candidates_count', 0)}")
    config = status.get("config", {})
    print("-" * 60)
    print(f"Title           : {config.get('title')}")
    print(f"Language        : {config.get('program_language')}")
    print(f"Problem Desc    : {config.get('problem_description', '')[:100]}...")
    run_sett = config.get("run_settings", {})
    if run_sett:
        print(
            f"Run Settings    : Max Programs: {run_sett.get('maxPrograms', run_sett.get('max_programs', 'N/A'))} | "
            f"Max Duration (hr): {run_sett.get('maxDuration', run_sett.get('max_duration', 'N/A'))}"
        )
    print("=" * 60 + "\n")


def print_postprocess_summary(res: dict):
    metrics = res.get("metrics_analysis", {})
    lineage = res.get("lineage_analysis", {})
    out_dir = res.get("exported_directory", "")

    print("\n" + "=" * 60)
    print("=== ALPHAEVOLVE POST-PROCESSING SUMMARY ===")
    print("=" * 60)
    print(f"Total Programs Found     : {metrics.get('total_programs', 0)}")
    print(f"Evaluated Programs Count : {metrics.get('evaluated_programs_count', 0)}")
    print(f"State Breakdown          : {metrics.get('state_counts', {})}")
    print(f"Target Ranking Metric    : {metrics.get('target_metric', 'N/A')}")

    print("\n--- Evaluation Metrics Statistics ---")
    stats_map = metrics.get("metrics_stats", {})
    if stats_map:
        print(f"{'Metric':<20} | {'Count':<6} | {'Min':<10} | {'Max':<10} | {'Mean':<10} | {'StDev':<10}")
        print("-" * 76)
        for m_name, m_stat in stats_map.items():
            print(
                f"{m_name:<20} | {m_stat.get('valid_count', 0):<6} | "
                f"{m_stat.get('min', 0.0):<10.4f} | {m_stat.get('max', 0.0):<10.4f} | "
                f"{m_stat.get('mean', 0.0):<10.4f} | {m_stat.get('stdev', 0.0):<10.4f}"
            )
    else:
        print("No numeric metric scores found.")

    print("\n--- Leaderboard (Top 5 Programs) ---")
    leaderboard = metrics.get("leaderboard", [])
    if leaderboard:
        for item in leaderboard[:5]:
            scores_str = ", ".join([f"{k}: {v:.4f}" for k, v in item.get("scores", {}).items()])
            parents_str = " | ".join(item.get("parent_programs", [])) or "SEED"
            print(
                f"Rank #{item['rank']:02d} [{item['program_id']}] | State: {item['state']} | "
                f"Scores: ({scores_str}) | Parents: [{parents_str}]"
            )
    else:
        print("No evaluated programs in leaderboard.")

    print("\n--- Evolutionary Lineage Analysis ---")
    print(f"Total Evolution Nodes    : {lineage.get('total_nodes', 0)}")
    print(f"Seed (Root) Programs     : {len(lineage.get('seed_programs', []))} seeds found")
    print(f"Max Evolution Depth      : {lineage.get('max_evolution_depth', 0)}")

    print("\n--- Top Evaluation Insights ---")
    insights = metrics.get("top_insights", [])
    if insights:
        for idx, ins in enumerate(insights[:5]):
            print(f"{idx+1}. {ins['insight']} (Count: {ins['count']})")
    else:
        print("No evaluation insights recorded.")

    if out_dir:
        print("\n" + "=" * 60)
        print(f"SUCCESS: All results, CSV leaderboard, JSON stats, and code exported to:")
        print(f"  -> {out_dir}")
        print("=" * 60 + "\n")


def print_experiments_table(exps: list):
    print("\n" + "=" * 90)
    print("=== AVAILABLE ALPHAEVOLVE EXPERIMENTS ===")
    print("=" * 90)
    print(f"{'#':<3} | {'Experiment ID':<22} | {'State':<12} | {'Title'}")
    print("-" * 90)
    for idx, exp in enumerate(exps, 1):
        exp_id = exp.get("name", "").split("/")[-1]
        state = exp.get("state", "UNKNOWN")
        title = exp.get("config", {}).get("title", "Untitled")
        print(f"{idx:<3} | {exp_id:<22} | {state:<12} | {title}")
    print("=" * 90 + "\n")


def select_experiment_interactively(monitor: AlphaEvolveMonitor) -> dict:
    logger.info("Scanning GCP project for AlphaEvolve sessions and experiments...")
    exps = monitor.list_all_experiments()
    if not exps:
        raise RuntimeError("No experiments found in the current GCP project/engine.")
    print_experiments_table(exps)
    while True:
        try:
            choice = input("Enter the number [1-N] or Experiment ID of the experiment to monitor (or 'q' to quit): ").strip()
            if choice.lower() in ["q", "quit", "exit"]:
                sys.exit(0)
            if choice.isdigit():
                idx = int(choice)
                if 1 <= idx <= len(exps):
                    return exps[idx - 1]
            for exp in exps:
                if exp.get("name", "").endswith(choice):
                    return exp
            print("Invalid selection. Please enter a valid index number or experiment ID.")
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            sys.exit(0)


def main():
    args = parse_args()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
    )

    monitor = AlphaEvolveMonitor(
        experiment_name=args.experiment_name,
        session_name=args.session_name,
        bucket_name=args.bucket,
        project_id=args.project_id,
    )

    try:
        if args.action in ["list", "list-experiments"]:
            exps = monitor.list_all_experiments()
            print_experiments_table(exps)
            return

        if not monitor.experiment_name:
            if sys.stdin.isatty() or args.interactive:
                selected = select_experiment_interactively(monitor)
                monitor.experiment_name = selected["name"]
                monitor.session_name = selected["session_name"]
                if args.output_dir == "./ae_postprocess_results":
                    exp_id = selected["name"].split("/")[-1]
                    args.output_dir = f"./{exp_id}"
                    logger.info(f"Setting output directory to: {args.output_dir}")
            else:
                logger.error("No experiment specified (-e) and terminal is not interactive.")
                exps = monitor.list_all_experiments()
                print_experiments_table(exps)
                sys.exit(1)

        if args.action == "status":
            status = monitor.get_experiment_status()
            print_status_summary(status)

        elif args.action == "monitor":
            status = monitor.monitor_loop(
                poll_interval_seconds=args.poll_interval,
                timeout_seconds=args.timeout,
            )
            print_status_summary(status)

        elif args.action == "postprocess":
            res = monitor.postprocess(
                output_dir=args.output_dir,
                target_metric=args.target_metric,
                top_k=args.top_k,
                export_code=args.export_code,
                state_filter=args.state_filter,
                order_by=args.order_by,
                max_programs=args.max_programs,
            )
            print_postprocess_summary(res)

        elif args.action == "all":
            status = monitor.get_experiment_status()
            print_status_summary(status)
            res = monitor.postprocess(
                output_dir=args.output_dir,
                target_metric=args.target_metric,
                top_k=args.top_k,
                export_code=args.export_code,
                state_filter=args.state_filter,
                order_by=args.order_by,
                max_programs=args.max_programs,
            )
            print_postprocess_summary(res)

    except Exception as e:
        logging.error(f"Error during execution: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
