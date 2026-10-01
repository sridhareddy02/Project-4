PY ?= python
.PHONY: setup test eval serve ui demo
setup:
	$(PY) -m pip install -r requirements.txt && cd frontend && npm ci
test:
	PYTHONPATH=src $(PY) -m pytest -q && cd frontend && npm run typecheck && npm test
eval:
	PYTHONPATH=src $(PY) -m mktg_copilot eval --check
serve:
	cd frontend && npm run build && cd .. && PYTHONPATH=src $(PY) -m mktg_copilot serve
ui:
	cd frontend && npm run dev          # http://localhost:5173, proxies /api to :8000
demo:
	PYTHONPATH=src $(PY) -m mktg_copilot export-demo && cd frontend && npm run build:demo
