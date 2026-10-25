.PHONY: install lint typecheck test fetch-kalshi fetch-polymarket build analyze all

install:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy

test:
	uv run pytest -q

fetch-kalshi:
	uv run pmcal fetch-kalshi

fetch-polymarket:
	uv run pmcal fetch-polymarket

build:
	uv run pmcal build

analyze:
	uv run pmcal analyze

all: fetch-kalshi fetch-polymarket build analyze
