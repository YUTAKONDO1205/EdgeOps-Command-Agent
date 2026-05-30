# EdgeOps Command Agent — dev shortcuts.
#
# Works on macOS/Linux/Git-Bash. PowerShell users can run the underlying
# commands directly or `make`-equivalent via `nmake`.

PYTHON ?= python
VENV   ?= .venv
ACT    := source $(VENV)/bin/activate

.PHONY: help venv install install-frontend test test-fast lint streamlit backend frontend dev clean docker-build compose-up compose-down sim-warning sim-critical

help:
	@echo "Targets:"
	@echo "  venv            create local python venv"
	@echo "  install         install python + backend deps into venv"
	@echo "  install-frontend  npm install for the Next.js frontend"
	@echo "  test            run pytest"
	@echo "  test-fast       pytest -x --ff (stop on first failure, last-failed first)"
	@echo "  streamlit       run Streamlit (port 8501)"
	@echo "  backend         run FastAPI backend (uvicorn, port 8000)"
	@echo "  frontend        run Next.js frontend (port 3000)"
	@echo "  dev             run backend + frontend + simulator concurrently (needs tmux or split shells)"
	@echo "  sim-warning     stream 5s of 'warning'-grade data via the Spresense simulator"
	@echo "  sim-critical    stream 5s of 'critical'-grade data via the simulator"
	@echo "  docker-build    docker build all three images"
	@echo "  compose-up      docker-compose up --build"
	@echo "  compose-down    docker-compose down"
	@echo "  clean           remove caches + side files"

venv:
	$(PYTHON) -m venv $(VENV)

install: venv
	$(VENV)/bin/pip install -r requirements.txt
	$(VENV)/bin/pip install -r backend/requirements.txt
	$(VENV)/bin/pip install pytest httpx

install-frontend:
	cd frontend && npm install

test:
	$(PYTHON) -m pytest tests/

test-fast:
	$(PYTHON) -m pytest tests/ -x --ff

streamlit:
	$(PYTHON) -m streamlit run app.py --server.port 8501

backend:
	$(PYTHON) -m uvicorn backend.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

sim-warning:
	$(PYTHON) data/spresense_simulator.py --equipment-id Pump-03 --intensity warning --duration 5

sim-critical:
	$(PYTHON) data/spresense_simulator.py --equipment-id Pump-03 --intensity critical --duration 5

docker-build:
	docker build -t edgeops-streamlit .
	docker build -t edgeops-backend  -f backend/Dockerfile .
	docker build -t edgeops-frontend -f frontend/Dockerfile ./frontend

compose-up:
	docker-compose up --build

compose-down:
	docker-compose down

clean:
	rm -rf .pytest_cache __pycache__ src/__pycache__ tests/__pycache__ backend/__pycache__
	rm -f _spresense_stream.jsonl _local_cosmos.jsonl _uploaded_image.*
	rm -rf _uploaded
