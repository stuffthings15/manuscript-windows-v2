# Manuscript Windows Version 2 Copy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create an isolated, reproducible copy of the current Windows app named Manuscript Windows Version 2 without changing the original Windows or Android app.

**Architecture:** Copy only source, tests, required assets, local model metadata, and the locked essay corpus into a staging directory. Verify a cryptographic manifest, rename the staging directory atomically, initialize a separate Git repository, then isolate all product and runtime identities so both Windows apps can coexist.

**Tech Stack:** PowerShell, Git, Python 3.12, PySide6, PyInstaller, Inno Setup, pytest.

## Global Constraints

- Source: `C:\Users\stuff\Documents\Echo-Link\Manuscript\Windows` at commit `b2baa9442858a8f20218d50d0f9d1d235ec17e55`.
- Destination: `C:\Users\stuff\Documents\Echo-Link\Manuscript\Windows Version 2`.
- The destination must not already exist. If it exists, stop and inspect it. Never merge or overwrite blindly.
- If Curtis confirms Android was literal, use source `C:\Users\stuff\Documents\Echo-Link\Manuscript\Android` and destination `C:\Users\stuff\Documents\Echo-Link\Manuscript\Android Version 2`; do not copy Python Windows code into Android.
- Preserve the source folder and the shared 20-essay ZIP.
- Exclude caches, virtual environments, build output, local databases, logs, downloaded runtime packs, credentials, private entitlement state, and generated evidence.
- The Version 2 app must use separate settings, database, mutex, tray identity, startup key, browser-host name, executable name, installer `AppId`, and default hotkeys.
- No production publication or production subscription configuration is part of this copy.

---

### Task 1: Lock the copy contract and source manifest

**Files:**
- Create: `tools/create_windows_v2_copy.ps1`
- Create: `tests/test_windows_v2_copy_plan.py`
- Produce: `Windows Version 2/docs/source-copy-manifest.json`

**Interfaces:**
- Consumes: source path, destination path, source commit, corpus ZIP path.
- Produces: a staged copy and SHA-256 manifest with `source_commit`, `copied_files`, `excluded_roots`, `corpus_sha256`, and `created_at`.

- [ ] **Step 1: Write the failing script-contract test**

```python
def test_v2_copy_script_refuses_existing_destination_and_excludes_private_state():
    script = Path("tools/create_windows_v2_copy.ps1").read_text(encoding="utf-8")
    assert "Refusing existing destination" in script
    for value in (".git", ".venv", "dist", "build", "installer-output", ".test-data"):
        assert value in script
    assert "source-copy-manifest.json" in script
```

- [ ] **Step 2: Run the test and confirm it fails because the script is absent**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_windows_v2_copy_plan.py -v
```

Expected: FAIL with `FileNotFoundError`.

- [ ] **Step 3: Implement the guarded copy script**

The script must:

```powershell
param(
    [string]$Source = "C:\Users\stuff\Documents\Echo-Link\Manuscript\Windows",
    [string]$Destination = "C:\Users\stuff\Documents\Echo-Link\Manuscript\Windows Version 2",
    [string]$CorpusZip = "C:\Users\stuff\Documents\Echo-Link\Manuscript\20_Animal_Pet_Essays_800_Percent_More_Errors.zip"
)
$ExpectedCommit = "b2baa9442858a8f20218d50d0f9d1d235ec17e55"
$ExpectedCorpusHash = "C80A6FBA0978596A464E1E9FF3C17D17D6A928FA56445A97C8E045A0EB9FF771"
```

It must resolve absolute paths, reject a destination inside the source, reject any existing destination, verify the source commit and clean Windows subtree, verify the corpus hash, copy into `Windows Version 2.staging-<guid>`, and rename only after all checks pass.

Copy these roots when present: `src`, `tests`, `tools`, `scripts`, `docs`, `resources`, `packaging`, `native`, `browser-extension`, `website`, `shared`, `models`, plus root Python/build/spec/readme files.

Exclude these names recursively: `.git`, `.venv`, `.pytest_cache`, `.test-data`, `.test-tmp`, `.tmp`, `.worktrees`, `__pycache__`, `build`, `dist`, `installer-output`, generated `evidence`, `*.log`, `*.sqlite*`, `*.db*`, `.env*`, `secrets*`, `credentials*`, `entitlement*`, and downloaded GGUF/runtime pack files not required by the checked-in manifest.

- [ ] **Step 4: Run the contract test**

Expected: PASS.

- [ ] **Step 5: Commit the reusable copy tool to the current Windows branch**

```powershell
git add Manuscript/Windows/tools/create_windows_v2_copy.ps1 Manuscript/Windows/tests/test_windows_v2_copy_plan.py
git commit -m "chore: add guarded Manuscript V2 copy tool"
```

### Task 2: Create and verify the Version 2 folder

**Files:**
- Create: `Windows Version 2/docs/source-copy-manifest.json`
- Create: `Windows Version 2/test-corpus/animal-pet-20/source.zip`
- Create: `Windows Version 2/test-corpus/animal-pet-20/README.txt`
- Create: `Windows Version 2/test-corpus/animal-pet-20/01_*.docx` through `20_*.docx`

**Interfaces:**
- Consumes: Task 1 copy script.
- Produces: a complete but unbootstrapped Version 2 source tree.

- [ ] **Step 1: Prove the destination does not exist and record free disk space**

```powershell
Test-Path -LiteralPath 'C:\Users\stuff\Documents\Echo-Link\Manuscript\Windows Version 2'
Get-Volume -DriveLetter C | Select-Object SizeRemaining
```

Expected: `False`; free space exceeds copied source plus 5 GB build headroom.

- [ ] **Step 2: Execute the guarded copy**

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\create_windows_v2_copy.ps1
```

- [ ] **Step 3: Verify the corpus before any build**

```powershell
$root='C:\Users\stuff\Documents\Echo-Link\Manuscript\Windows Version 2\test-corpus\animal-pet-20'
(Get-FileHash "$root\source.zip" -Algorithm SHA256).Hash
(Get-ChildItem $root -Filter '*.docx').Count
```

Expected: hash `C80A6FBA0978596A464E1E9FF3C17D17D6A928FA56445A97C8E045A0EB9FF771`; count `20`.

- [ ] **Step 4: Verify prohibited private/generated files are absent**

Run a filename and content scan for `.env`, tokens, secrets, account state, local databases, logs, build output, and user document history. Any match fails the copy.

- [ ] **Step 5: Initialize independent source control**

```powershell
cd 'C:\Users\stuff\Documents\Echo-Link\Manuscript\Windows Version 2'
git init
git switch -c codex/manuscript-windows-v2-bootstrap
git add .
git commit -m "chore: create isolated Manuscript Windows Version 2 baseline"
```

### Task 3: Isolate product identity and state

**Files:**
- Modify: `src/echo_link_app/branding.py`
- Modify: `src/echo_link_core/paths.py`
- Modify: `src/echo_link_app/main.py`
- Modify: `src/echo_link_app/startup.py`
- Modify: `src/echo_link_app/windows_identity.py`
- Modify: `EchoLink.spec`
- Modify: `packaging/manuscript_version_info.txt`
- Modify: `packaging/Manuscript.iss` or the active installer script
- Modify: `browser-extension/manifest.json`
- Test: `tests/test_branding.py`
- Test: `tests/test_startup.py`
- Test: `tests/test_packaging.py`
- Create: `tests/test_v2_identity.py`

**Interfaces:**
- Produces: `PRODUCT_NAME`, `PRODUCT_SLUG`, `APP_DATA_DIR_NAME`, `STARTUP_VALUE_NAME`, `SINGLE_INSTANCE_ID`, `BROWSER_HOST_NAME`, and installer `AppId` values unique to Version 2.

- [ ] **Step 1: Add failing identity tests**

Require:

```python
assert PRODUCT_NAME == "Manuscript Windows Version 2"
assert PRODUCT_SLUG == "manuscript-windows-v2"
assert APP_DATA_DIR_NAME == "ManuscriptWindowsV2"
assert STARTUP_VALUE_NAME == "ManuscriptWindowsV2"
assert "Version 2" in executable_description
```

Also assert none of these values equals the original app identity.

- [ ] **Step 2: Run the focused tests and confirm failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_v2_identity.py tests\test_branding.py tests\test_startup.py tests\test_packaging.py -v
```

- [ ] **Step 3: Implement identity constants and route every identity consumer through them**

Use `ManuscriptV2.exe`, `%LOCALAPPDATA%\ManuscriptWindowsV2`, a distinct Windows Run value, a distinct single-instance mutex, a distinct browser native-host name, and a new stable Inno `AppId` generated once and committed.

- [ ] **Step 4: Prevent hotkey collisions**

Version 2 must start with global hotkeys disabled until the user enables nonconflicting bindings. A warning must name the conflict and keep dictation available through the main button.

- [ ] **Step 5: Run focused tests and commit**

```powershell
git add src tests packaging EchoLink.spec browser-extension
git commit -m "feat: isolate Manuscript Windows Version 2 identity"
```

### Task 4: Bootstrap and prove the copied baseline

**Files:**
- Modify only if needed: `scripts/bootstrap.ps1`
- Produce: `docs/qa/v2-baseline.json`
- Produce: `dist/ManuscriptV2/ManuscriptV2.exe`
- Produce: `installer-output/ManuscriptV2TestingSetup.exe`

**Interfaces:**
- Consumes: isolated Version 2 source tree.
- Produces: test-only baseline artifacts and a content-free verification report.

- [ ] **Step 1: Bootstrap a fresh virtual environment**

```powershell
.\scripts\bootstrap.ps1
```

- [ ] **Step 2: Run the isolated source suite**

```powershell
.\scripts\verify.ps1
```

- [ ] **Step 3: Launch Version 2 beside the original with hotkeys disabled**

Verify both processes remain responsive, use separate tray labels and data folders, and do not change each other’s settings or history.

- [ ] **Step 4: Build the testing-unlocked package only**

```powershell
.\.venv\Scripts\python.exe .\tools\build.py --clean --installer --testing-unlocked
```

- [ ] **Step 5: Run packaged smoke and installer lifecycle checks**

Do not accept source-only results as packaged proof.

- [ ] **Step 6: Commit the verified baseline report**

```powershell
git add docs/qa/v2-baseline.json
git commit -m "test: record Manuscript V2 isolated baseline"
```

### Task 5: Freeze the handoff boundary

**Files:**
- Create: `docs/V2_SOURCE_AND_IDENTITY.md`
- Create: `docs/qa/v2-copy-receipt.json`

- [ ] **Step 1: Record source commit, corpus hash, destination, exclusions, product IDs, test counts, artifact hashes, and all remaining gaps**
- [ ] **Step 2: Confirm the original Windows and Android folder hashes/status are unchanged from Task 1**
- [ ] **Step 3: Stop before correction work and obtain Curtis’s approval to use Version 2 as the repair workspace**

