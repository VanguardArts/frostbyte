# FrostByte - build & packaging helpers
#   make deb      -> dist/frostbyte_<ver>-1_all.deb   (Debian/Kubuntu/Ubuntu)
#   make tarball  -> dist/frostbyte-<ver>.tar.gz      (source, any distro)
#   make dist     -> both
#   make clean    -> remove dist/

VERSION := $(shell grep -Po 'FROSTBYTE_VERSION = "\K[^"]+' common/frostbyte_common.py)

.PHONY: deb tarball dist clean

deb:
	./packaging/build-deb.sh

tarball:
	mkdir -p dist
	git archive --format=tar.gz --prefix=frostbyte-$(VERSION)/ \
		-o dist/frostbyte-$(VERSION).tar.gz HEAD
	@echo "dist/frostbyte-$(VERSION).tar.gz"

dist: deb tarball

clean:
	rm -rf dist
