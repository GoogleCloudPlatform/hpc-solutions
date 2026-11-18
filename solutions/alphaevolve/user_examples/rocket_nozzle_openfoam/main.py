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

"""Supersonic Rocket Nozzle Contour Evolution Entrypoint (OpenFOAM-14 shockFluid).

Parameterizes a 2D axisymmetric convergent-divergent rocket nozzle contour using
6 throat-to-exit Rao/B-spline bell control parameters and evaluates the steady
compressible Navier-Stokes flowfield in OpenFOAM-14 (`shockFluid`).
"""

import numpy as np
from src.optimization.optimization import funct

# 6-parameter supersonic divergent nozzle contour bounds:
#   x[0]: exit_radius_ratio  (r_e / r_t)      in [1.40, 3.00]  (Area ratio epsilon in [1.96, 9.00])
#   x[1]: length_ratio       (L_div / r_t)    in [3.50, 7.50]  (Divergent bell length / throat radius)
#   x[2]: theta_n_deg        (theta_n [deg])  in [12.0, 36.0]  (Post-throat inflection expansion angle)
#   x[3]: theta_e_deg        (theta_e [deg])  in [2.0,  18.0]  (Nozzle exit lip wall angle)
#   x[4]: throat_rc_ratio    (R_c,t / r_t)    in [0.35, 1.40]  (Downstream throat fillet radius ratio)
#   x[5]: bell_camber        (c_bell)         in [-0.20, 0.25] (Quartic bell contour camber modifier)
BOUNDS = [
    (1.40, 3.00),
    (3.50, 7.50),
    (12.0, 36.0),
    (2.0, 18.0),
    (0.35, 1.40),
    (-0.20, 0.25),
]

# Baseline 15-degree conical nozzle (under-expanded at p0 = 3.0 MPa, p_amb = 101.325 kPa):
#   r_e / r_t = 1.50 (epsilon = 2.25), L_div / r_t = 4.00,
#   theta_n = 15.0 deg, theta_e = 15.0 deg (straight 15-deg cone),
#   R_c,t / r_t = 0.40, bell_camber = 0.00
BASELINE_X = [1.50, 4.00, 15.0, 15.0, 0.40, 0.00]


# EVOLVE-BLOCK-START
def design_nozzle(parameters):
    """Computes the 6D supersonic rocket nozzle contour parameter vector x.

    Operating conditions in `parameters`:
      - p0_pa = 3.0e6 Pa (30 bar chamber stagnation pressure)
      - t0_k = 1200.0 K (chamber stagnation temperature)
      - p_amb_pa = 101325.0 Pa (sea-level ambient back-pressure)
      - gamma = 1.4, r_throat_m = 0.020 m

    IMPORTANT: Do NOT call `funct()` inside `design_nozzle()`. OpenFOAM-14 CFD
    is executed once by `evaluate()` on the returned 6-parameter vector `x`.
    Use compressible gas-dynamics relations (e.g., isentropic pressure-Mach
    and area-Mach equations, Prandtl-Meyer expansion waves, Method of
    Characteristics / Rao optimum thrust bell equations, divergence loss
    models, and boundary-layer skin-friction surrogates) to synthesize the
    optimal 6D nozzle contour vector `x`.
    """
    return np.array(BASELINE_X, dtype=float)
# EVOLVE-BLOCK-END


def evaluate(parameters):
    """Evaluates the candidate rocket nozzle contour with a single OpenFOAM-14 run."""
    x = np.asarray(design_nozzle(parameters), dtype=float).flatten()
    if x.size != len(BOUNDS) or not np.all(np.isfinite(x)):
        raise ValueError(f"Invalid nozzle parameter vector from design_nozzle: {x}")
    out_of_bounds = [
        f"x[{i}]={val:.4f} (allowed: [{low}, {high}])"
        for i, (val, (low, high)) in enumerate(zip(x, BOUNDS))
        if not (low - 1e-5 <= val <= high + 1e-5)
    ]
    if out_of_bounds:
        raise ValueError(
            f"Nozzle contour parameters violated BOUNDS: {', '.join(out_of_bounds)}"
        )
    f, g1, g2, g3, g4 = funct(x, parameters)
    return f, g1, g2, g3, g4, x.tolist()
