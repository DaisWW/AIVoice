param(
    [switch]$List,
    [string]$DeleteFileId = "",
    [switch]$Yes
)

$WorkspaceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonPath = Join-Path $WorkspaceRoot ".venv-gpt-sovits\Scripts\python.exe"
$WebRoot = Join-Path $WorkspaceRoot "web"
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONPATH = $WebRoot

if ($List) {
    & $PythonPath -m app.admin --list
    exit $LASTEXITCODE
}

if (-not $DeleteFileId) {
    throw "请使用 -List，或使用 -DeleteFileId 指定文件 ID"
}

$AdminArgs = @("-m", "app.admin", "--delete-file", $DeleteFileId)
if ($Yes) {
    $AdminArgs += "--yes"
}
& $PythonPath @AdminArgs
exit $LASTEXITCODE
