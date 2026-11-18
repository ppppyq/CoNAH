# CoNAH Documentation

CoNAH's documentation is built with Sphinx (`pydata-sphinx-theme` + `sphinx-design`) with parallel English (`source-en/`) and Simplified Chinese (`source-zh/`) trees ready for Read the Docs.

## Build & Live Preview

```bash
pip install -r docs/requirements.txt
cd docs

# Live-reloading Chinese documentation at http://127.0.0.1:8001
bash autobuild.sh zh 8001

# Live-reloading English documentation at http://127.0.0.1:8002
bash autobuild.sh en 8002
```

## Static Build (CI / Read the Docs)

```bash
cd docs
make zh
make en
```
