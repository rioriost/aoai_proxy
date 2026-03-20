SHELL := /bin/sh

REPO_ROOT := $(CURDIR)
UV ?= uv
PYTHON ?= python3

DIST_DIR := $(REPO_ROOT)/dist
FORMULA_DIR := $(REPO_ROOT)/Formula
FORMULA_FILE := $(FORMULA_DIR)/aoai_proxy.rb

.PHONY: release-artifacts sync build formula

release-artifacts: sync build formula

sync:
	$(UV) sync --extra test --group dev

build:
	$(UV) build

formula:
	mkdir -p "$(FORMULA_DIR)"
	genformula \
		--install-mode advanced \
		--source-subdir .
