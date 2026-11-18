SPHINXBUILD ?= sphinx-build
LANG ?= zh

.PHONY: html zh en clean

html:
	$(SPHINXBUILD) -W --keep-going source-$(LANG) build/html-$(LANG)

zh:
	$(SPHINXBUILD) -W --keep-going source-zh build/html-zh

en:
	$(SPHINXBUILD) -W --keep-going source-en build/html-en

clean:
	rm -rf build/
