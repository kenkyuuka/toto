.PHONY: test test-nonfree cov lint fmt typecheck check reinstall container dev-deps dev up down bash

## Testing
test:                ## Run all tests
	hatch run test

test-nonfree:        ## Run nonfree tests only
	hatch run test-nonfree

cov:                 ## Run tests with coverage report
	hatch run cov

## Linting & Formatting
lint:                ## Run ruff + black check
	hatch run lint:style

fmt:                 ## Auto-format (black + ruff --fix)
	hatch run lint:fmt

typecheck:           ## Run mypy type checking
	hatch run lint:typing

check:               ## Run all lint + type checks
	hatch run lint:all

## Environment
reinstall:           ## Recreate hatch env (picks up new entry points)
	hatch env prune && hatch env create

## Docker
container:           ## Build the toto CLI image (toto:latest)
	@(DOCKER_BUILDKIT=1 docker build -t toto:latest --target final -f Dockerfile .)

dev-deps:            ## Build the dev toolchain image (toto:dev-deps)
	@(DOCKER_BUILDKIT=1 docker build -t toto:dev-deps --target dev-deps -f Dockerfile .)

dev:                 ## Build the self-contained dev image (toto:dev)
	@(DOCKER_BUILDKIT=1 docker build -t toto:dev --target dev -f Dockerfile .)

up: dev-deps         ## Build and start the dev container
	HOST_UID=$$(id -u) HOST_GID=$$(id -g) docker compose up -d --build

down:                ## Stop the dev container
	docker compose down

bash:                ## Open a shell in the dev container
	@docker compose exec app bash

## Help
help:                ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
