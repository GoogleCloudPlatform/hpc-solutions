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

"""Target-conditioned kinase/GTPase pocket ligand generator and ranker.

AlphaEvolve evolves `propose_and_rank_ligands` to generate valid, synthetically
accessible drug-like SMILES scaffolds tailored to 3D binding pocket coordinates,
electrostatic charges, and pharmacophore interaction anchors.
"""

import math
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union


# EVOLVE-BLOCK-START
def propose_and_rank_ligands(
    pocket_spec: Dict[str, Any],
    seed_scaffolds: List[str],
    top_k: int = 8,
) -> List[str]:
  """Proposes and ranks candidate SMILES strings for a 3D receptor pocket.

  Baseline implementation: applies generic unconditioned amide/methyl/halide
  decorations to the seed scaffolds and ranks them by string length without
  inspecting the 3D pocket geometry (`pocket_spec['residues']`) or
  `pocket_spec['pharmacophore_anchors']`.

  Args:
    pocket_spec: Dictionary containing:
      - `target_id`: Receptor identifier (e.g., 'KRAS_G12D_7RPZ').
      - `pocket_center`: [x, y, z] center of the binding site in Angstroms.
      - `residues`: List of pocket heavy atoms with `res_name`, `atom_name`,
        `coords` ([x, y, z]), `charge`, `vdw_radius`, and `vina_type`.
      - `pharmacophore_anchors`: Key 3D interaction features (`type`, `coords`,
        `tolerance_radius`, `weight`).
    seed_scaffolds: Initial list of valid heterocyclic fragment SMILES.
    top_k: Number of top candidate SMILES to return.

  Returns:
    List of up to `top_k` candidate SMILES strings ordered from best to worst.
  """
  candidates: List[str] = []
  seen = set()

  decorations = [
      "C(=O)NC",
      "c1ccc(F)cc1",
      "C1CCNCC1",
      "OC",
      "C(F)(F)F",
      "CN(C)C",
  ]

  for base in seed_scaffolds:
    if base not in seen:
      seen.add(base)
      candidates.append(base)
    for dec in decorations:
      variant = f"{base}{dec}"
      if variant not in seen:
        seen.add(variant)
        candidates.append(variant)

  # Baseline naive ranking: sort by moderate SMILES length
  candidates.sort(key=lambda s: abs(len(s) - 36))
  return candidates[:top_k]
# EVOLVE-BLOCK-END
