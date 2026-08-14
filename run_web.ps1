param(
    [int]$Port = 18082,
    [string]$ListenAddress = "0.0.0.0"
)

$WorkspaceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonPath = Join-Path $WorkspaceRoot ".venv-gpt-sovits\Scripts\python.exe"
$WebRoot = Join-Path $WorkspaceRoot "web"

if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "找不到 GPT-SoVITS Python 环境: $PythonPath"
}

$env:PYTHONDONTWRITEBYTECODE = "1"
Set-Location -LiteralPath $WorkspaceRoot
& $PythonPath -m uvicorn app.main:app --app-dir $WebRoot --host $ListenAddress --port $Port --workers 1
