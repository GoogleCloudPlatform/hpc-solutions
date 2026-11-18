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

"""7nm ASAP7 RISC-V + NPU Tile macro floorplanning and PPA optimizer.

AlphaEvolve evolves `compute_macro_floorplan` to place and orient 8 hard SRAM
macros around a 45,000-gate standard-cell sea, eliminating central core routing
blockages, orienting macro signal pins toward the interior logic channels, and
closing multi-corner (`ss_0p63v_100c`, `tt_0p70v_25c`, `ff_0p77v_m40c`) setup
timing (`WNS >= 0 ps`, `TNS = 0 ps`) while minimizing wirelength and power.
"""

import math
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union


# EVOLVE-BLOCK-START
def compute_macro_floorplan(netlist_graph: Dict[str, Any]) -> Dict[str, Any]:
  """Computes `(x_um, y_um, orientation, halo_um)` for all hard SRAM macros.

  Baseline implementation: places the 8 SRAM macros in a uniform 2-column grid
  near the center-left of the die with uniform `R0` orientation, obstructing
  the central standard-cell core and clock spine.

  Args:
    netlist_graph: Dictionary loaded from `models/asap7_riscv_npu_tile.json`
      with the following schema:
      - `core_bbox_um`: `[xmin, ymin, xmax, ymax]` list of 4 floats.
      - `die_bbox_um`: `[xmin, ymin, xmax, ymax]` list of 4 floats.
      - `clock_period_ps`, `min_channel_spacing_um`, `min_pin_clearance_um`:
        floats.
      - `io_pads`: Dict mapping pad ID string (e.g. `"IO_CLK_PLL"`) to
        `{"x_um": float, "y_um": float, "side": str}` (NOTE: dict, not list).
      - `macros`: List of dicts, each with `{"id": str, "group": str,
        "width_um": float, "height_um": float, "default_pin_side": str,
        "clock_load_ff": float}`.
      - `stdcell_clusters`: List of dicts, each with `{"id": str,
        "area_um2": float, "gate_count": int,
        "anchor_io": Dict[str, float]}`.
      - `hypergraph_nets`: List of dicts, each with `{"src": str, "dst": str,
        "weight": float, "activity": float}`.
      - `timing_paths`: List of dicts, each with `{"name": str,
        "stages": List[str], "logic_delay_ps": float,
        "setup_uncertainty_ps": float}`.
      - `pvt_corners`: Dict mapping corner name to `{"logic_scale": float,
        "wire_rc_scale": float, "clock_skew_scale": float,
        "leakage_mw": float}`.

  Returns:
    Dictionary with:
      - `macros`: Mapping from macro ID to `{"x_um": float, "y_um": float,
        "orientation": str, "halo_um": [float, float]}` (`orientation` in
        `{"R0", "R180", "MY", "MX"}`).
      - `routing_config`: `{"target_density": float, "layer_derating": float}`.
  """
  macros_out: Dict[str, Dict[str, Any]] = {}
  x_cols = [120.0, 520.0]
  y_rows = [160.0, 390.0, 620.0, 850.0]

  for idx, macro in enumerate(netlist_graph["macros"]):
    col = idx % 2
    row = idx // 2
    macros_out[macro["id"]] = {
        "x_um": x_cols[col],
        "y_um": y_rows[row],
        "orientation": "R0",
        "halo_um": [10.0, 10.0],
    }

  return {
      "macros": macros_out,
      "routing_config": {
          "target_density": 0.65,
          "layer_derating": 0.15,
      },
  }
# EVOLVE-BLOCK-END
