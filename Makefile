VERSION := 1.0.16
IMAGE := ninjatec/rsyslog:$(VERSION)

.PHONY: build test publish

build:
	DOCKER_BUILDKIT=1 docker build -t $(IMAGE) .

test: build
	python3 -m unittest discover -s tests -v
	RSYSLOG_IMAGE=$(IMAGE) python3 tests/smoke.py

publish: test
	docker push $(IMAGE)
