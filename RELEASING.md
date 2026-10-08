# Releasing epubx

Version lives in exactly one place: `epubx/__init__.py`. `pyproject.toml` reads
it back with `[tool.setuptools.dynamic]`, so a release cannot ship a version
that disagrees with the code.

## One-time setup

1. **There is no separate "register a name" step on PyPI.** A project is created
   by its first successful upload, or by the first use of a *pending publisher*.
   Per the PyPI docs: "A 'pending' publisher does not create a project or reserve
   a project's name until it is actually used to publish."

2. **The PyPI project name is `epub-extended`.** `epubx` is already registered
   on PyPI and has no releases — verified: `pypi.org/simple/epubx/` answers 200
   with `project-status: active` and zero files, while a control name 404s.
   PyPI's *search* shows nothing because search indexes released projects only —
   absence from search proves nothing.

   Decision recorded: the *distribution* is renamed, not the project. The name
   `epub-extended` was reserved by the first use of a *pending publisher*
   (<https://pypi.org/manage/account/publishing/>); per the PyPI docs, a pending
   publisher does not create a project or reserve a project's name until it is
   actually used to publish. The import name stays `epubx` (`packages =
   ["epubx"]` in `pyproject.toml`), the repository stays `epubx`, and no code
   changes.

   The pending publisher details (as recorded): pending project name
   `epub-extended`, publisher GitHub, repository `taylorren/epubx`, workflow
   `publish.yml`, environment `pypi`. Trusted publishing matches these fields
   **exactly** — the workflow file on GitHub is `publish.yml`; if the pending
   publisher was added as `publish.yaml`, remove and re-add it with
   `publish.yml`, or the upload is rejected.

3. **Rehearse on TestPyPI meanwhile.** `epub-extended` is free there
   (`test.pypi.org/simple/epub-extended/` answers 404 until the first publish),
   and `publish.yml` publishes to TestPyPI on demand — enough to prove the whole
   pipeline end to end without touching the production name. TestPyPI needs its
   own pending publisher (step 4).

4. **Enable Trusted Publishing.** On <https://pypi.org/manage/account/publishing/>
   add a pending publisher (and a second one on <https://test.pypi.org>):

   | Field | Value |
   |---|---|
   | PyPI Project Name | `epub-extended` |
   | Owner | `taylorren` |
   | Repository name | `epubx` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` (TestPyPI: `testpypi`) |

   Then create the matching GitHub environments `pypi` and `testpypi` under
   *Settings → Environments*. No API token is stored anywhere.

## Every release

```
# 1. Commit everything — a release must be a clean tree.
git status --porcelain          # must print nothing

# 2. Bump the version, in one file.
$EDITOR epubx/__init__.py       # __version__ = "0.1.1"

# 3. Confirm the suite passes.
uv run pytest -q

# 4. Build and inspect.
uv build                        # writes dist/*.whl and dist/*.tar.gz
uvx twine check --strict dist/*

# 5. Smoke-test the wheel in a throwaway environment — from OUTSIDE the repo.
#    Run it from the repo root and Python imports the local source tree
#    instead of the wheel, so the test proves nothing.
cd /tmp
uv run --isolated --no-project --with /path/to/epubx/dist/epub-extended-0.1.1-py3-none-any.whl \
    python -c "import epubx; print(epubx.__version__, epubx.__file__)"

# 6. Commit, tag, push.
git commit -am "Release 0.1.1"
git tag -a v0.1.1 -m "epubx 0.1.1"
git push origin main v0.1.1

# 7. Publish. Either publish a GitHub Release from the v0.1.1 tag, which
#    triggers the `pypi` job, or run the workflow by hand against TestPyPI
#    first (*Actions → Publish → Run workflow → testpypi*).
```

## Publishing without GitHub Actions

```
uv build
uv publish                      # reads UV_PUBLISH_TOKEN; --publish-url for TestPyPI
# or: python -m twine upload --repository testpypi dist/*   # then: dist/*
```

Rehearse on TestPyPI first:

```
uv publish --publish-url https://test.pypi.org/legacy/ \
    --token pypi-AgEIcHlwaS5vcmc...
python -m pip install -i https://test.pypi.org/simple/ --extra-index-url \
    https://pypi.org/simple/ epub-extended
```

## Rules the package enforces on itself

- **Versions are immutable.** PyPI never lets the same version be re-uploaded;
  a mistake means a new patch version, not a re-upload. `0.1.0` is spent once
  it is up.
- **No yanked releases without a reason.** Yanking hides a version from
  resolvers without deleting it, so say why in the release notes.
- **`build/` and `*.egg-info/` are build residue**, already in `.gitignore`.
  `dist/` is ignored too.
