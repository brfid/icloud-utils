SWIFT_SDK ?= $(shell xcrun --sdk macosx --show-sdk-path)
SWIFT_SCRATCH ?= /tmp/icloud-utils-swift-build
PYTHON ?= python3
SWIFT_ENV = SDKROOT="$(SWIFT_SDK)" CLANG_MODULE_CACHE_PATH=/tmp/icloud-utils-clang-cache SWIFTPM_MODULECACHE_OVERRIDE=/tmp/icloud-utils-swiftpm-cache
SWIFT = $(SWIFT_ENV) swift

.PHONY: build test release checksum fixture-helper install-materializer uninstall-materializer check-python test-mail test-backup test-installers test-materializer install-mail

build:
	$(SWIFT) build --disable-sandbox --sdk "$(SWIFT_SDK)" --scratch-path "$(SWIFT_SCRATCH)"

test: check-python build
	PYTHONDONTWRITEBYTECODE=1 ICMAT_TEST_BINARY="$(SWIFT_SCRATCH)/debug/icloud-materialize" "$(PYTHON)" -m unittest discover -s tests -v

release:
	$(SWIFT) build --disable-sandbox --sdk "$(SWIFT_SDK)" --scratch-path "$(SWIFT_SCRATCH)" -c release

checksum: release
	shasum -a 256 "$(SWIFT_SCRATCH)/release/icloud-materialize"

fixture-helper:
	$(SWIFT_ENV) swiftc -sdk "$(SWIFT_SDK)" -module-cache-path /tmp/icloud-utils-fixture-module-cache tests/support/icloud_fixture.swift -o /tmp/icloud-fixture-helper

install-materializer: release
	scripts/install-materializer --binary "$(SWIFT_SCRATCH)/release/icloud-materialize"

uninstall-materializer:
	scripts/install-materializer --uninstall

check-python:
	@PYTHONPATH=python "$(PYTHON)" -B -c 'from installer_support import require_python; require_python()'

test-mail: check-python
	PYTHONDONTWRITEBYTECODE=1 "$(PYTHON)" -m unittest discover -s tests -p 'test_mail*.py' -v

test-backup: check-python
	PYTHONDONTWRITEBYTECODE=1 "$(PYTHON)" -m unittest discover -s tests -p 'test_backup*.py' -v
	PYTHONDONTWRITEBYTECODE=1 "$(PYTHON)" -m unittest discover -s tests -p 'test_retention.py' -v

test-installers: check-python
	PYTHONDONTWRITEBYTECODE=1 "$(PYTHON)" -m unittest discover -s tests -p 'test_installer*.py' -v
	PYTHONDONTWRITEBYTECODE=1 "$(PYTHON)" -m unittest discover -s tests -p 'test_*installation.py' -v

test-materializer: check-python build
	PYTHONDONTWRITEBYTECODE=1 ICMAT_TEST_BINARY="$(SWIFT_SCRATCH)/debug/icloud-materialize" "$(PYTHON)" -m unittest discover -s tests -p 'test_materializer.py' -v

install-mail: check-python
	PYTHON="$(PYTHON)" scripts/install-mail
