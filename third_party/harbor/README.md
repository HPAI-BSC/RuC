# Harbor for RuC

RuC runs agents with [Harbor](https://github.com/harbor-framework/harbor) (Apache-2.0),
release `0.20.1.dev202607310211`, plus the small patch in `ruc.patch`. Build it with:

```
third_party/harbor/setup_harbor.sh
```

This downloads the release, applies the patch and installs it into
`third_party/harbor/.venv/`, which is where the agentic slurm scripts expect it.

The patch makes Harbor work on HPC clusters and with a model served by vLLM:

- Singularity can run without `--fakeroot`, on read-only images and with no internet.
- OpenCode gets a `vllm-local` provider, and the model's context and output limits.
- An agent already installed in the image is not reinstalled.
- Timeouts are reported more clearly.
