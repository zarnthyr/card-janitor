.PHONY: all build check clean dev-install dev-uninstall format inspect lint sync test

all: check build

build:
	uv run package

check:
	uv run ruff format --check .
	uv run ruff check .
	uv run pytest

clean:
	rm -rf build card-janitor.ankiaddon

dev-install:
	uv run python -c "from package import install_development_addon; install_development_addon()"

dev-uninstall:
	uv run python -c "from package import uninstall_development_addon; uninstall_development_addon()"

format:
	uv run ruff format . $(ARGS)

inspect:
	uv run python -c "from package import validate_package; validate_package()"
	unzip -l card-janitor.ankiaddon

lint:
	uv run ruff check . $(ARGS)

sync:
	uv sync

test:
	uv run pytest
