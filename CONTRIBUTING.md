# Contributing

Set up and validate exactly as in the README's Development section:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,fabric]"
npm ci --ignore-scripts

pytest -q --cov=mquery_toolkit --cov-fail-under=80
mypy src
ruff check .
ruff format --check .
npm test
npm run bundle && git diff --exit-code -- src/mquery_toolkit/_bridge.cjs
python -m build && twine check dist/*
```

Run the whole block twice before opening a pull request.
