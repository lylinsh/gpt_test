PYTHON ?= python3
.PHONY: test model-test throughput-test lint vectors demo
model-test:
	$(PYTHON) -m unittest discover -s tests -v
throughput-test:
	$(PYTHON) -m tools.throughput
lint:
	$(PYTHON) -m ruff check rs_codec tests tools
vectors:
	$(PYTHON) -m tools.generate_vectors
demo:
	$(PYTHON) -m rs_codec demo --errors 8
test: model-test throughput-test
