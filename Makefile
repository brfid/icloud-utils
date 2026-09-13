SWIFT_SDK ?= /Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk
SWIFT_SCRATCH ?= /tmp/icloud-utils-swift-build
SWIFT_ENV = SDKROOT=$(SWIFT_SDK) CLANG_MODULE_CACHE_PATH=/tmp/icloud-utils-clang-cache SWIFTPM_MODULECACHE_OVERRIDE=/tmp/icloud-utils-swiftpm-cache
SWIFT = $(SWIFT_ENV) swift

.PHONY: build test release checksum install-materializer uninstall-materializer

build:
	$(SWIFT) build --disable-sandbox --sdk $(SWIFT_SDK) --scratch-path $(SWIFT_SCRATCH)

test:
	$(SWIFT) build --disable-sandbox --sdk $(SWIFT_SDK) --scratch-path $(SWIFT_SCRATCH)
	PYTHONDONTWRITEBYTECODE=1 ICMAT_TEST_BINARY=$(SWIFT_SCRATCH)/debug/icloud-materialize python3 -m unittest discover -s tests -v

release:
	$(SWIFT) build --disable-sandbox --sdk $(SWIFT_SDK) --scratch-path $(SWIFT_SCRATCH) -c release

checksum: release
	shasum -a 256 $(SWIFT_SCRATCH)/release/icloud-materialize

install-materializer: release
	scripts/install-materializer --binary $(SWIFT_SCRATCH)/release/icloud-materialize

uninstall-materializer:
	scripts/install-materializer --uninstall
