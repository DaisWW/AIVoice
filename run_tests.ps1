$WorkspaceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonPath = Join-Path $WorkspaceRoot ".venv-gpt-sovits\Scripts\python.exe"
$RuffPath = Join-Path $WorkspaceRoot ".venv-gpt-sovits\Scripts\ruff.exe"

if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "找不到测试 Python 环境: $PythonPath"
}

$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
$env:PYTHONDONTWRITEBYTECODE = "1"
Set-Location -LiteralPath $WorkspaceRoot

if (-not (Test-Path -LiteralPath $RuffPath)) {
    $RuffPath = (Get-Command ruff -ErrorAction Stop).Source
}

$CodeFiles = @(
    "code\convert_gpt_sovits_transformers.py",
    "code\download_optional_models.py",
    "code\gpt_sovits_clone.py",
    "code\gpt_sovits_audio.py"
)

& $PythonPath -m black --check web\app web\tests @CodeFiles voice_core
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& $RuffPath check web\app web\tests @CodeFiles voice_core
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& $PythonPath -m pytest -p no:cacheprovider web\tests -q
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$JavaScriptFiles = Get-ChildItem web\static\js -Recurse -Filter *.js
foreach ($JavaScriptFile in $JavaScriptFiles) {
    node --check $JavaScriptFile.FullName
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

$JavaScriptTests = Get-ChildItem web\tests\js -Filter *.test.mjs
if ($JavaScriptTests) {
    node --test $JavaScriptTests.FullName
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

exit 0
