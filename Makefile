.PHONY: test test-nonfree cov lint fmt typecheck check reinstall

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

## Help
help:                ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
