COMPONENT := custom_components/argocd
VERSION := $(shell python3 -c "import json; print(json.load(open('$(COMPONENT)/manifest.json'))['version'])")

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

.PHONY: version
version: ## Print the manifest version
	@echo $(VERSION)

.PHONY: check
check: ## Validate JSON manifests and byte-compile the component
	@python3 -m json.tool $(COMPONENT)/manifest.json >/dev/null && echo "manifest.json ok"
	@python3 -m json.tool hacs.json >/dev/null && echo "hacs.json ok"
	@python3 -m compileall -q $(COMPONENT) && echo "compile ok"

.PHONY: lint
lint: ## Run ruff lint + format check
	ruff check $(COMPONENT) tests
	ruff format --check $(COMPONENT) tests

.PHONY: test
test: ## Run the test suite
	pytest -q

.PHONY: release
release: check ## Tag the manifest version and publish a GitHub release (gh)
	@git diff --quiet HEAD || { echo "working tree dirty — commit first"; exit 1; }
	@git rev-parse "v$(VERSION)" >/dev/null 2>&1 && { echo "tag v$(VERSION) already exists"; exit 1; } || true
	git push
	gh release create "v$(VERSION)" --title "v$(VERSION)" --generate-notes
	@echo "released v$(VERSION)"
