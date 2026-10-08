# Releasing epubx

Version lives in exactly one place: `epubx/__init__.py`. `pyproject.toml` reads
it back with `[tool.setuptools.dynamic]`, so a release cannot ship a version
that disagrees with the code.

## One-time setup

1. **There is no separate "register a name" step on PyPI.** A project is created
   by its first successful upload, or by the first use of a *pending publisher*.
   Per the PyPI docs: "A 'pending' publisher does not create a project or reserve
   a project's name until it is actually used to publish."

2. **`epubx` is already registered on PyPI and has no releases.** Verified:
   `pypi.org/simple/epubx/` answers 200 with `project-status: active` and zero
   files, while a control name 404s. PyPI's *search* shows nothing because search
   indexes released projects only — absence from search proves nothing.

   Three ways forward, in order of cost:

   | Situation | Action |
   |---|---|
   | You already own it | Nothing to register. Check <https://pypi.org/manage/projects/> and the pending publishers at <https://pypi.org/manage/account/publishing/>, then publish. |
   | You do not own it, and want to ship now | Rename the *distribution*: `name = "epubx-py"` in `pyproject.toml`. The import name stays `epubx` (`packages = ["epubx"]`), the repository stays `epubx`, and no code changes. Measured free on PyPI: `epubx-py`, `python-epubx`, `epubx-lib`, `pyepubx`, `epub-x`. Prefer one that is not a near-twin of the taken name — `epub-x` differs from `epubx` by a single hyphen and only invites confusion. |
   | You do not own it, and want that exact name | PEP 541 name request. Per the Name Retention policy a project is *abandoned* only when **all** of these hold: the owner is unreachable (PyPI attempts contact three times over six weeks), there have been **no releases in the past twelve months**, and there is no owner activity on the project's home page. Reusing a name for a *different* project has stricter criteria than continuing maintenance, and "name squatting (package has no functionality or is empty)" is grounds for removal. Process: contact the owner first, search existing requests, then open an issue at <https://github.com/pypi/support/issues>. Expect weeks to months; refusal is possible. |

   A first attempt at a *pending publisher* for `epubx` is the cheapest way to
   learn which case you are in: accepted means the name is yours to publish to,
   rejected means it is taken.

3. **Rehearse on TestPyPI meanwhile.** `epubx` is free there
   (`test.pypi.org/simple/epubx/` answers 404), and `publish.yml` publishes to
   TestPyPI on demand — enough to prove the whole pipeline end to end without
   touching the disputed name.

4. **Enable Trusted Publishing.** On <https://pypi.org/manage/account/publishing/>
   add a pending publisher (and a second one on <https://test.pypi.org>):

   | Field | Value |
   |---|---|
   | PyPI Project Name | `epubx` |
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
uv run --isolated --no-project --with /path/to/epubx/dist/epubx-0.1.1-py3-none-any.whl \
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
    https://pypi.org/simple/ epubx
```

## Rules the package enforces on itself

- **Versions are immutable.** PyPI never lets the same version be re-uploaded;
  a mistake means a new patch version, not a re-upload. `0.1.0` is spent once
  it is up.
- **No yanked releases without a reason.** Yanking hides a version from
  resolvers without deleting it, so say why in the release notes.
- **`build/` and `*.egg-info/` are build residue**, already in `.gitignore`.
  `dist/` is ignored too.
