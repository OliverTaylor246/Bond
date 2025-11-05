# Contributing to Bond Broker

Thanks for helping improve the Bond Broker service! This repository now only
contains the WebSocket broker, so the workflow is intentionally lightweight.

## Development Setup

```bash
git clone <repo-url>
cd bond
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run the broker locally:

```bash
uvicorn apps.broker.broker:app --host 0.0.0.0 --port 8080
```

Need Redis? Start the local stack:

```bash
docker compose up -d
```

## Coding Standards

- Use 2-space indentation and include type hints for public functions.
- Keep the broker event loop non-blocking; prefer `async` + `await`.
- When touching authentication logic in `apps/broker/auth.py`, get a security
  review before merging.
- Add concise comments only where the flow is non-obvious.

## Quality Checks

```bash
make lint  # flake8 + mypy on apps/ and engine/
```

There are currently no automated tests in this repo; please test changes
manually when altering broker behaviour.

## Submitting Changes

1. Create a feature branch: `git checkout -b feature/my-change`
2. Make your edits and run `make lint`
3. Commit with a descriptive message
4. Push and open a pull request describing:
   - What changed
   - How it was tested
   - Any follow-up work needed

We appreciate clean diffs and focused pull requests. Thanks again!
