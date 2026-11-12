# Seed Program, `experiment.yaml`, and `evaluator.py` Templates

## Phase 2: `EVOLVE-BLOCK` Seed Program Examples

### Single-File Python Seed (`main.py`)

Markers sit at column `0` with no inline comments on the marker lines and no
blank line or trailing whitespace immediately after `# EVOLVE-BLOCK-START`.
Keep any benchmark or verification helpers outside the mutable block:

```python
def compute_filter_weights(samples: list[float]) -> list[float]:
# EVOLVE-BLOCK-START
    window = min(len(samples), 8)
    return [
        sum(samples[max(0, i - window + 1) : i + 1]) / window
        for i in range(len(samples))
    ]
# EVOLVE-BLOCK-END
```

### Subdirectory C++ Seed (`src/kernel.hpp`)

When seed files live inside subdirectories (listed as `"src/kernel.hpp"` in
`experiment.yaml`), place `// EVOLVE-BLOCK-START` and `// EVOLVE-BLOCK-END` at
column `0` around only the mutable kernel logic, keeping verification and
timing harnesses outside the block:

```cpp
#pragma once
#include <vector>

inline void run_kernel(const double* input, double* output, int n) {
// EVOLVE-BLOCK-START
    for (int i = 0; i < n; ++i) {
        output[i] = 0.5 * input[i];
    }
// EVOLVE-BLOCK-END
}
```

## Step 3.1: Declarative `experiment.yaml` Templates

Populate all 7 required top-level keys (`title`, `problem_description`,
`program_language`, `primary_metric`, `initial_score`, `seed_files`, and
`evaluator`):

```yaml
title: "Adaptive Signal Processing"
problem_description: "Evolve a low-latency causal filter minimizing tracking lag and slope reversals..."
program_language: "python"
primary_metric: "overall_score"
initial_score: -12.5
seed_files:
  - "main.py"
evaluator:
  script: "evaluator.py"
  method: "signal_processing_evaluation"
```

### Multi-File `seed_files` With Read-Only Context (`adaptive_sort_cpp` Pattern)

Not every file in `seed_files` requires an `EVOLVE-BLOCK`: **at least one** file
across `seed_files` must contain an `EVOLVE-BLOCK` with non-comment editable
code, while additional entries (such as shared type headers or benchmark
harnesses) may omit `EVOLVE-BLOCK` markers so they remain immutable. Enable
`generation_settings.include_full_program_in_prompt: true` when the LLM needs
to read those immutable context files (and boilerplate outside `EVOLVE-BLOCK`)
during candidate generation:

```yaml
title: "Adaptive Sort C++"
problem_description: "Evolve a C++ sorting algorithm adaptive to random, sorted, reverse, and duplicate inputs."
program_language: "cpp"
primary_metric: "score"
initial_score: 0.0
seed_files:
  - "src/sort.hpp"       # Contains // EVOLVE-BLOCK-START ... // EVOLVE-BLOCK-END
  - "src/sort_impl.hpp"  # Contains // EVOLVE-BLOCK-START ... // EVOLVE-BLOCK-END
  - "src/benchmark.hpp"  # Immutable context file (no EVOLVE-BLOCK markers)
  - "src/benchmark.cpp"  # Immutable context file (no EVOLVE-BLOCK markers)
generation_settings:
  include_full_program_in_prompt: true
evaluator:
  script: "evaluator.py"
  method: "adaptive_sort_evaluation"
```

## Step 3.2: Timeout-Guarded `evaluator.py` Template

Guard candidate execution with a strict wall-clock timeout, negate minimization
metrics so higher scores represent better candidates, set `score=None` (never
`0.0` or numeric sentinels) on crashes/timeouts/constraint failures, populate
diagnostic `insights`, and return `.model_dump()` on
`AlphaEvolveProgramEvaluation`:

```python
import signal
from alpha_evolve.models import (
    AlphaEvolveEvaluationInsight,
    AlphaEvolveEvaluationInsights,
    AlphaEvolveEvaluationScore,
    AlphaEvolveEvaluationScores,
    AlphaEvolveProgramEvaluation,
)


class EvaluationTimeoutError(TimeoutError):
    pass


def _alarm_handler(signum, frame):
    raise EvaluationTimeoutError("Candidate evaluation timed out")


def evaluate_candidate() -> dict:
    old_handler = signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(30)
    try:
        latency_ms = run_verified_benchmark()
        return AlphaEvolveProgramEvaluation(
            scores=AlphaEvolveEvaluationScores(
                scores=[
                    AlphaEvolveEvaluationScore(
                        metric="neg_latency_ms",
                        score=-float(latency_ms),
                    )
                ]
            ),
            insights=AlphaEvolveEvaluationInsights(
                insights=[
                    AlphaEvolveEvaluationInsight(
                        label="benchmark_summary",
                        text=f"Passed all checks in {latency_ms:.2f} ms",
                    )
                ]
            ),
        ).model_dump()
    except Exception as exc:
        return AlphaEvolveProgramEvaluation(
            scores=AlphaEvolveEvaluationScores(
                scores=[
                    AlphaEvolveEvaluationScore(
                        metric="neg_latency_ms",
                        score=None,
                    )
                ]
            ),
            insights=AlphaEvolveEvaluationInsights(
                insights=[
                    AlphaEvolveEvaluationInsight(
                        label="evaluation_error",
                        text=str(exc)[:1000],
                    )
                ]
            ),
        ).model_dump()
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)
```

### Optional `postprocess(top_programs)` Hook

Define `postprocess(top_programs)` in `evaluator.py` when the experiment needs
end-of-run domain plots, polar sweeps, or summary tables uploaded to GCS once
evolution finishes:

```python
def postprocess(top_programs: list[dict]) -> None:
    # Generate summary plots or artifacts under /mnt/disks/share/<exp>/results/
    pass
```
