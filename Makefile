.PHONY: install dev test demo run docker-build

install:
	pip install -e .

dev:
	pip install -e ".[dev]"

test:
	pytest -q

demo:
	python examples/scheduling_agent_demo.py

run:
	mes-mcp

docker-build:
	docker build -t mes-scheduling-mcp .