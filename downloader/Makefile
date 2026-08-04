VENV  := $(HOME)/.local/share/dl/venv
PY    := $(VENV)/bin/python
SHIM  := $(HOME)/.local/bin/dl

.PHONY: install test uninstall

install: $(PY)
	$(PY) -m pip install -q -e .
	@mkdir -p $(dir $(SHIM))
	@printf '#!/bin/sh\nexec %s -m dl "$$@"\n' "$(PY)" > $(SHIM)
	@chmod 755 $(SHIM)
	@echo "installed: $(SHIM)"

$(PY):
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -q --upgrade pip

test: $(PY)
	$(PY) -m pip install -q -e ".[dev]"
	$(PY) -m pytest

uninstall:
	rm -rf $(VENV) $(SHIM)
