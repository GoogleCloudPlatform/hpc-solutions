# Google Cloud HPC Solutions

This repository provides a collection of High Performance Computing (HPC),
AI/ML, and scientific computing solutions on Google Cloud Platform (GCP). The
solutions cover a wide range of workloads, from automated evolutionary algorithm
frameworks on Google Cloud Batch and Vertex AI to specialized deployment
blueprints and supporting Google Cloud Cluster Toolkit modules.

## Repository Structure

The repository is organized into the following top-level directories:

- `blueprints`: Cluster Toolkit deployment blueprints and scripts for provisioning infrastructure and running workloads (e.g., [`blueprints/alphaevolve`](blueprints/alphaevolve)).
- `community`: Supporting Terraform modules for Google Cloud Cluster Toolkit deployments (e.g., `community/modules`).
- `solutions`: End-to-end domain solutions, frameworks, and workload runners:
  - `solutions/alphaevolve`: An open-source, scalable platform for running Google's AlphaEvolve evolutionary algorithm framework on Google Cloud Batch and Vertex AI.

## Getting Started

To get started with the solutions and examples in this repository, you will need a Google Cloud Platform account with billing enabled. You will also need to have the following tools installed and configured:

- [Google Cloud SDK](https://cloud.google.com/sdk/docs/install)
- [Terraform](https://learn.hashicorp.com/tutorials/terraform/install-cli)
- [Git](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git)

Most of the solutions use the [Google Cloud Cluster Toolkit](https://cloud.google.com/cluster-toolkit) (`gcluster`) to deploy the necessary infrastructure. Please follow the [installation instructions](https://cloud.google.com/cluster-toolkit/docs/setup/install-cluster-toolkit) to set it up.

## Examples

Here is a curated list of solutions and examples available in this repository:

- **AlphaEvolve:**
  - [Automated Evolutionary Algorithm Code Discovery & Optimization on Google Cloud Batch](blueprints/alphaevolve/README.md)
  - [AlphaEvolve Framework & Creating Custom Experiments](solutions/alphaevolve/README.md)

## Contributing

We welcome contributions to this repository. Please see the [CONTRIBUTING.md](CONTRIBUTING.md) file for more information.

## License

This repository is licensed under the Apache 2.0 License. See the [LICENSE](LICENSE) file for more information.