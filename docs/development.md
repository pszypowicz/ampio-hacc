# Development

Every quality gate lives in `.pre-commit-config.yaml`. The same hooks run on each local commit and in the `pre-commit` job of the CI workflow. A commit that passes locally passes the gate on the pull request.

## Build the gate venv

The type check and the test suite run from a virtual environment at `.venv`. Two files pin what goes into it. `requirements_test.txt` holds the Home Assistant release, the test plugin that pins it, and mypy. `custom_components/ampio/manifest.json` holds the `ampio-mqtt` release that HACS installs.

1. Install [uv](https://docs.astral.sh/uv/) and `jq`.
2. Run these commands in the repository root:

   ```sh
   uv venv .venv --python 3.14
   uv pip install -p .venv -r requirements_test.txt "$(jq -r '.requirements[0]' custom_components/ampio/manifest.json)"
   ```

Python 3.14 is required. On an older Python the resolver falls back to a years-old Home Assistant release with no error.

The hooks start mypy and pytest with `uv run`. It finds `.venv` in the repository root or in a parent directory. If your checkout has no venv of its own, for example a git worktree, set `VIRTUAL_ENV` to the venv path.

## Install the hooks

1. Install [pre-commit](https://pre-commit.com/), for example with `uv tool install pre-commit` or `brew install pre-commit`.
2. Run `pre-commit install` in the repository root.

Each commit now runs the hooks on the staged files. The first run builds the hook environments and takes a few minutes. The type check and the test suite run on every commit, about 10 s each.

## Run the gate by hand

```sh
pre-commit run --all-files
```

The CI workflow also runs `hassfest` and the HACS validation. Neither one is a pre-commit hook. To run hassfest locally:

```sh
docker run --rm -v "$PWD":/github/workspace ghcr.io/home-assistant/hassfest
```

## The secrets hook

`betterleaks` scans the staged changes for hardcoded secrets. The config-flow strings in `custom_components/ampio/translations/en.json` contain the word `password`, and a rewrite of that file trips the scan. Edit the file in place and keep its indentation. Do not regenerate it.
