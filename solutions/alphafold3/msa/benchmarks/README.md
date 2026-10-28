# AlphaFold 3 MSA Throughput Benchmarks

This directory contains the benchmark dataset, input staging tooling, and profiling
utilities used to validate the performance of the AlphaFold 3 Multiple Sequence
Alignment (MSA) solution.

In production benchmarking across 50 `c4d-standard-96` Spot VMs on Google Cloud Batch,
the solution achieved an observed throughput of **300.3 alignments per hour**
(5.01 tasks/min; **615.3 alignments/hr** steady-state) across 300 heterogeneous targets
averaging 1,052 residues each (100.1% of the 300 alignments/hour design target across
50 active Spot VMs).

---

## 1. Overview

The benchmark suite consists of two core dataset files:

- **[`benchmark_sequences.jsonl`](benchmark_sequences.jsonl)**: Contains **300 benchmark inputs**
  representative of heterogeneous, multi-chain AlphaFold 3 workloads. In the default
  `heterogeneous_mix` configuration, inputs are partitioned evenly across four biological
  entity categories (75 inputs per type):

  1. **Protein Monomers (`protein_monomer`)**: Single-chain reviewed proteins from
     UniProtKB (200–1,800 residues), exercising `jackhmmer` (`uniref90`, `mgnify`,
     `small_bfd`, `uniprot_cluster_annot`) and `hmmsearch` (`pdb_seqres`) with
     structural mmCIF template lookups.
  2. **RNA Monomers (`rna_monomer`)**: Single-chain non-coding RNAs from RNAcentral
     and Rfam (30–500 nucleotides), exercising `nhmmer` (`rfam`, `rnacentral`,
     `nt_rna`) alongside `hmmbuild` and `hmmalign`.
  3. **Protein Multimers (`protein_multimer`)**: Multi-chain protein complexes
     (heterodimers), exercising multi-chain parallel genetic search and cross-chain
     UniProt taxonomic pairing (`msa_pairing.py`).
  4. **Protein-RNA Complexes (`rnp_complex`)**: Ribonucleoprotein complexes
     combining protein and RNA chains, exercising both protein and RNA search
     pipelines within the same Cloud Batch task.

  Execution order across all inputs is deterministically randomized (`--seed 42`)
  so VM task scheduling experiences an interleaved mix of entity types and lengths,
  preventing artificial batching of light or heavy jobs.

- **[`reference_rnas.jsonl`](reference_rnas.jsonl)**: Contains **222 curated reference RNA sequences**
  (34–463 nt) from Rfam and RNAcentral, including ribozymes, riboswitches, aptamers, and
  non-coding RNAs. While `setup.py` queries UniProtKB dynamically for proteins, RNA candidates
  are maintained in this local curated pool rather than queried live. This design decision
  avoids external API rate limits and downtime, guarantees canonical base validation (preventing
  IUPAC degenerate codes that fail AlphaFold 3 schema checks), and ensures biologically diverse,
  structured targets rather than uncharacterized genomic fragments. This pool is used by
  [`setup.py`](setup.py) to synthesize RNA monomers and protein-RNA (RNP) complexes when
  assembling benchmark suites.


---

## 2. Usage

### Input Preparation (`setup.py`)

[`setup.py`](setup.py) is a standalone script (with PEP 723 inline dependency
metadata, e.g. for `uv run`) that prepares and uploads the benchmark suite:

1. **Assemble, randomize & save**: Assembles the benchmark dataset across the
   requested category (`heterogeneous_mix`, `protein_monomer`, `rna_monomer`,
   `protein_multimer`, or `rnp_complex`), splits the total input count
   as evenly as possible across subtypes, shuffles input order deterministically
   (`--seed 42`), and saves to `benchmark_sequences.jsonl` (or reuses the
   cached file if `--cached` is passed).
2. **Generate temporary AlphaFold 3 inputs in `/tmp`**: Expands each line into
   sequential input files (`0.json` .. `<N-1>.json`) inside a temporary staging
   directory under `/tmp/af3-msa-benchmarks.XXXXXX/msa-inputs/`. Non-dialect
   keys are stripped to ensure strict AlphaFold 3 schema compatibility.
3. **Upload & clean up**: Concurrently uploads `0.json` .. `<N-1>.json` and
   `manifest.jsonl` to `gs://<bucket>/msa-inputs/` using the Google Cloud
   Storage Python API (`google-cloud-storage`), and automatically cleans up the
   temporary staging directory on exit.
4. **Characterize**: Prints a Rich table summarizing input counts by category,
   mean length, range, and standard deviation.

#### Arguments & Environment Variables

| Option / Variable | Default | Description |
| :--- | :--- | :--- |
| `gs://<bucket>` / `GCS_BUCKET` | Required (unless `--no-upload`) | Destination Cloud Storage bucket (or `gs://<bucket>/msa-inputs`). |
| `-n`, `--count` / `INPUT_COUNT` | `300` | Total number of benchmark inputs to generate. |
| `-t`, `--type` / `INPUT_TYPE` | `heterogeneous_mix` | Benchmark input category: `heterogeneous_mix`, `protein_monomer`, `rna_monomer`, `protein_multimer`, or `rnp_complex`. |
| `--min-len` / `MIN_LEN` | `200` | Minimum sequence length in residues for protein monomers. |
| `--max-len` / `MAX_LEN` | `1800` | Maximum sequence length in residues for protein monomers. |
| `--seed` / `SHUFFLE_SEED` | `42` | Random seed for deterministic input ordering. |
| `--cached` | `false` | Skip assembly if `benchmark_sequences.jsonl` already exists with matching count. |
| `--no-upload` | `false` | Generate and verify temporary `{n}.json` files locally without uploading to Cloud Storage. |

#### Usage Examples

Generate and upload 300 heterogeneous benchmark inputs to a specific bucket:

```bash
./setup.py gs://<your-alphafold-msa-bucket>
```

Generate a customized benchmark of 50 inputs:

```bash
./setup.py --count 50 gs://<your-alphafold-msa-bucket>
```

Generate RNA-only benchmark inputs without uploading:

```bash
./setup.py --count 50 --type rna_monomer --no-upload
```

Verify the 300 cached heterogeneous inputs locally:

```bash
./setup.py --cached --no-upload
```

### Running the 300-Alignment Throughput Benchmark

Follow these steps to reproduce or validate the 300-alignment benchmark run:

1. **Configure and deploy [`../af3-msa.yaml`](../af3-msa.yaml)**:
   Set `msa_task_count: 300` and `msa_parallelism: 50`. Deploy the infrastructure using Cluster Toolkit:

   ```bash
   cd ..
   gcluster deploy af3-msa.yaml --vars project_id=YOUR_PROJECT_ID
   ```

2. **Prepare the reference databases**:
   Populate and shard the genetic databases in Cloud Storage (one-time job):

   ```bash
   # within your gcluster deployment dir - see deployment instructions
   ./submit-db-prep-job.sh
   ```

3. **Assemble and upload the benchmark inputs**:

   ```bash
   cd /path/to/hpc-solutions/solutions/alphafold3/msa/benchmarks
   ./setup.py gs://<your-alphafold-msa-bucket>
   ```

4. **Submit the MSA array job**:

   ```bash
   # from gcluster deployment dir - see deployment instructions for more info
   ./submit-msa-array-job.sh
   ```

5. **Profile and export results using [`profile.py`](profile.py)**:

   Once the job is executing or complete, run [`profile.py`](profile.py) to inspect
   live progress and export structured metrics:

   ```bash
   ./profile.py --project-id YOUR_PROJECT_ID --job-id YOUR_JOB_ID --with-logs --export
   ```

   `profile.py` queries the Cloud Batch API and Cloud Logging to generate:
   - **Throughput Summary**: Overall run duration, completed tasks, failure counts,
     average tasks/min, and design target comparison against 300 alignments/hr.
   - **Throughput & Duration Breakdown by Entity Type**: Succeeded tasks, average sequence
     length, mean duration, median duration, and single-VM processing
     capacity (**Alignments / VM Hour**) across entity categories.
   - **Task Failure Analysis**: Error diagnostics, exit codes, and timestamps.
   - **Preemption & Retry Tracking (`--with-logs`)**: Spot VM preemption interrupts
     (Cloud Batch exit code `50001` and Compute Engine audit events).
   - **Database Transfer Performance (`--with-logs`)**: Download counts and transfer
     durations from Cloud Storage to Hyperdisk Balanced.
   - **Per-Database Search Breakdown (`--with-logs`)**: Individual tool/database search
     execution timings across `jackhmmer`, `nhmmer`, and `hmmsearch`.
   - **Structured Data Export (`--export [DIR]`)**: Exports `overview.md`,
     `job_summary.tsv`, `tasks.tsv`, `throughput_by_entity_type.tsv`,
     `benchmark_sequences.tsv`, `database_search_summary.tsv`, `database_calls_raw.tsv`,
     `vm_summary.tsv`, and `telemetry_summary.tsv`.

---

## 3. Results

The performance results below were captured from benchmark job `msa-array-job-230996eb`
deployed in region `europe-west4` across 50 `c4d-standard-96` Spot worker VMs.

### Input Characterization

The benchmark suite consists of 300 targets structured to represent real-world
structural biology workloads across monomeric and multimeric entities:

| Entity Type | Inputs | Sequence Length Mean | Sequence Length Range | Target Characteristics |
| :--- | :---: | :---: | :---: | :--- |
| **Protein Monomer** | 75 | 1,054.7 aa | 237 – 1,765 aa | Reviewed single-chain UniProtKB targets |
| **Protein Multimer** | 75 | 1,958.3 aa | 667 – 3,378 aa | Multi-chain heterodimers with taxonomic pairing |
| **RNA Monomer** | 75 | 117.4 nt | 38 – 463 nt | Non-coding RNAs from RNAcentral & Rfam |
| **RNP Complex** | 75 | 1,079.3 residues | 303 – 1,868 residues | Mixed protein-RNA complexes |
| **Overall** | **300** | **1,052.4 residues** | **38 – 3,378 residues** | **Heterogeneous mix across all 4 entity types** |

### Observed Throughput

#### Job Execution Summary

- **Total Job Duration**: **59.93 minutes** (59m 56s / 3,595.86 seconds)
- **Task Success Rate**: **100%** (300 / 300 tasks succeeded, 0 failed)
- **Observed Fleet Throughput**: **5.01 tasks/min (300.3 alignments/hour)**
- **Steady-State Fleet Throughput (excl. cold-start staging)**: **10.26 tasks/min (615.3 alignments/hour)**
- **Design Target**: 300 alignments/hour (5.00 tasks/min)
- **Performance vs Target**: **100.1% of design target** (300.3 alignments/hour across 50 active Spot VMs; +0.3 alignments/hour vs target)
- **Effective Fleet Processing Rate**: **9.5 alignments / VM-hour**

#### Throughput & Duration Breakdown by Entity Type

| Entity Type | Completed | Avg Length | Mean Duration | Median Duration | Min – Max Duration | Alignments / VM-Hr |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Protein Monomer** | 75 / 75 | 1,055 aa | 6.32 min | 3.95 min | 0.79 – 23.95 min | 9.49 / VM-hr |
| **Protein Multimer** | 75 / 75 | 1,958 aa | 9.73 min | 6.61 min | 1.76 – 38.89 min | 6.17 / VM-hr |
| **RNA Monomer** | 75 / 75 | 117 nt | 2.21 min | 1.03 min | 0.81 – 10.91 min | 27.12 / VM-hr |
| **RNP Complex** | 75 / 75 | 1,079 res | 7.13 min | 4.79 min | 1.87 – 32.90 min | 8.41 / VM-hr |
| **Combined Overall** | **300 / 300** | **1,052 res** | **6.35 min** | **4.40 min** | **0.79 – 38.89 min** | **9.45 / VM-hr** |

*Note: Task duration includes input download, JSON parsing, genetic search, mmCIF template extraction, MSA pairing, result generation, and output upload to Cloud Storage. In this run, 2 transient Spot preemption interrupts occurred across the job and were automatically retried to success by Cloud Batch without user intervention.*

#### Infrastructure & Telemetry Observations

- **Worker VM Concurrency**: 50 concurrent `c4d-standard-96` Spot instances observed (Batch reached the target 50 instances).
- **Task Distribution**: Worker VMs completed an average of **6.00 tasks per VM** (median 6.0, ranging from 1 to 10 tasks depending on target length and VM lifetime).
- **Rapid Database Cold Starts**: 50 database downloads occurred across the 50 observed VMs (1 per worker VM). Cold-start download durations averaged **2.83 minutes** (median 2.85 min; min 2.56 min, max 3.38 min).
- **Warm Cache Reuse**: After the initial ~2.8-minute database staging on each VM, subsequent tasks bypassed all data transfers (**251 tasks reused the cache**), reading directly from Hyperdisk Balanced and Linux kernel memory page cache. Mean task execution time excluding initial staging was **4.88 minutes** (median 3.38 minutes; min 0.79 min, max 29.51 min).
- **Spot Preemption Resilience**: Cloud Batch handled 2 transient Spot preemptions (Batch exit code `50001`: 2, GCE audit: 1) seamlessly with automatic task retries via lifecycle policy codes. All 300 tasks succeeded to 100% completion (0 failed tasks).

### Relative CPU Utilization & Search Optimization

High throughput in AlphaFold 3 MSA requires saturating all available CPU cores without
bottlenecking on single long-running database searches or leaving cores idle during
multi-stage search pipelines.

#### Database Search Durations (Sampled across 10 Tasks / 10 Chains)

The table below summarizes execution timings across the reference databases:

| Tool | Database | Shards | Total Calls | Mean Duration | Median Duration | Min – Max Duration | Total Core-Time |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `jackhmmer` | **`mgy`** (MGnify) | 1 (unsharded) | 10 | 370.21 s | 182.39 s | 32.37 – 1408.35 s | 32.91 core-hrs |
| `jackhmmer` | **`uniprot`** (UniProt Annot) | 1 (unsharded) | 10 | 369.33 s | 171.56 s | 22.25 – 1358.28 s | 32.83 core-hrs |
| `jackhmmer` | **`uniref`** (UniRef90) | 1 (unsharded) | 10 | 251.58 s | 128.02 s | 15.85 – 833.07 s | 22.36 core-hrs |
| `jackhmmer` | **`bfd`** (Small BFD) | 1 (unsharded) | 10 | 50.62 s | 41.38 s | 9.19 – 131.64 s | 4.50 core-hrs |
| `hmmsearch` | **`pdb`** (PDB seqres) | 1 (unsharded) | 10 | 4.43 s | 3.90 s | 0.75 – 9.12 s | 0.39 core-hrs (23.62 min) |
| `nhmmer` | **`ntrna`** (NT-RNA) | 384 | 1,920 | 3.10 s | 3.06 s | 2.63 – 4.67 s | 1.65 core-hrs |
| `nhmmer` | **`rnacentral`** | 64 | 320 | 4.44 s | 4.38 s | 3.66 – 5.94 s | 0.39 core-hrs (23.68 min) |
| `nhmmer` | **`rfam`** | 16 | 80 | 0.64 s | 0.69 s | 0.18 – 1.02 s | 0.01 core-hrs (51.00 s) |

*Note: The 10-task sample included long protein targets with extensive homologous hits, reaching up to 1,408.4s on `mgy` and 1,358.3s on `uniprot`. Median durations reflect standard per-chain search performance.*

#### Hybrid Search Architecture Analysis

The `c4d-standard-96` worker VM provides **96 vCPUs**. Rather than treating all sequence
tools identically, the solution adopts a specialized hybrid configuration tailored to the
concurrency and scaling properties of each search tool:

1. **Unsharded Multi-Threaded JackHMMER (32 vCPUs per search)**:
   - Upstream JackHMMER scaling improvements allow multi-threaded searches on monolithic
     FASTA files to scale efficiently up to 32 vCPUs without the overhead of file sharding.
   - During protein MSA search, AlphaFold 3 runs three searches concurrently: `uniref90`,
     `uniprot_cluster_annot`, and `mgnify`. Allocating 32 cores to each (`--jackhmmer_n_cpu=32`)
     perfectly occupies the machine's 96 vCPUs (`3 × 32 = 96 vCPUs`) with zero CPU oversubscription.
   - When the longest database search (`mgnify`) continues past `uniref90` and `uniprot`,
     it retains all 32 cores, cutting tail completion time by 33% compared to 24-core setups.
   - Median search times were **128.0s** (`uniref90`), **171.6s** (`uniprot`), and
     **182.4s** (`mgnify`), while eliminating the management of hundreds of shard files.

2. **Sharded Single-Threaded Nhmmer for RNA**:
   - Unlike JackHMMER, `nhmmer` multi-threading suffers from internal reader-thread lock contention
     when scanning large monolithic files. Running unsharded `nhmmer` with high thread counts
     results in low CPU utilization and extended wall-clock times.
   - Sharding RNA databases into single-threaded workers (`--nhmmer_n_cpu=1`) with up to 24
     parallel shard tasks (`--nhmmer_max_parallel_shards=24`) bypasses thread locking completely:
     - `nt_rna` (384 shards) executes across 24 parallel workers in 16 waves, completing in
       **~49 seconds wall-clock** (down from >200 seconds unsharded).
     - `rnacentral` (64 shards) completes across 24 workers in **~13 seconds wall-clock**.
     - `rfam` (16 shards) completes in a single wave in **~0.69 seconds**.
   - As a result, **RNA Monomer** median task duration dropped to **1.03 minutes**, delivering
     **27.12 alignments / VM-hour**.

3. **Streamlined Database Staging & Page Cache Retention**:
   - Staging uncompressed sharded RNA files and monolithic compressed protein archives directly
     to local Hyperdisk Balanced achieved an average VM cold-start download time of **~2.83 minutes** across 50 concurrent VMs.
   - Once staged, the ~250 GB protein working set remains resident in the Linux kernel page cache,
     ensuring that subsequent tasks run with zero storage transfer overhead.

Through this hybrid configuration, worker VMs sustain high instruction throughput across both
protein and RNA workloads, delivering an observed throughput of **300.3 alignments per hour**
(100.1% of the 300 alignments/hr design target with 50 active Spot VMs) and a steady-state fleet throughput of **615.3 alignments per hour**.
