SHELL := /bin/bash

ENV ?= ctf

.PHONY: help install dry-run update verify uninstall smoke sync-skills remove-skills

help:
	@echo "Targets:"
	@echo "  make install ENV=ctf"
	@echo "  make dry-run ENV=ctf"
	@echo "  make update ENV=ctf"
	@echo "  make verify ENV=ctf"
	@echo "  make uninstall ENV=ctf"
	@echo "  make smoke"

install:
	bash scripts/install.sh --env "$(ENV)"

dry-run:
	bash scripts/install_headless.sh --env "$(ENV)" --dry-run

update:
	bash scripts/update_headless.sh --env "$(ENV)" --all-toolboxes --profile headless

verify:
	bash scripts/verify.sh --env "$(ENV)"

uninstall:
	bash scripts/uninstall.sh --env "$(ENV)"

sync-skills:
	bash scripts/sync_skills.sh

remove-skills:
	bash scripts/remove_skills.sh

smoke:
	bash -n scripts/_installer_bootstrap.sh
	bash -n scripts/install.sh
	bash -n scripts/install_headless.sh
	bash -n scripts/update_headless.sh
	bash -n scripts/verify.sh
	bash -n scripts/uninstall.sh
	bash -n scripts/sync_skills.sh
	bash -n scripts/remove_skills.sh
	python3 -m py_compile scripts/tool_catalog.py
	python3 -m py_compile scripts/install_cli.py
	bash scripts/install_headless.sh --env "$(ENV)" --dry-run
