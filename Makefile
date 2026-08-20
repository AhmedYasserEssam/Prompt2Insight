.PHONY: up down logs test lint format

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f

test:
	cd backend && uv run pytest

lint:
	cd backend && uv run ruff check . && uv run mypy app

format:
	cd backend && uv run ruff format .
