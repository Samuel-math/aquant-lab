.PHONY: test demo check
PYTHON ?= python3
test:
	$(PYTHON) -m unittest discover -s tests -v
demo:
	$(PYTHON) -m aquant demo
check:
	$(PYTHON) -m compileall -q aquant tests
	$(PYTHON) -m unittest discover -s tests -v
