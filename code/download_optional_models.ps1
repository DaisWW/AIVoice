param(
    [ValidateSet("all", "qwen", "qwen17", "cosy", "voxcpm15", "voxcpm2")]
    [string]$Model = "all",
    [string]$Proxy = "http://127.0.0.1:7897"
)

$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$PythonPath = Join-Path $WorkspaceRoot ".venv-gpt-sovits\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "找不到 Python 环境: $PythonPath"
}

if ($Proxy) {
    $env:HTTP_PROXY = $Proxy
    $env:HTTPS_PROXY = $Proxy
}

function Ensure-ModelSource {
    param(
        [string]$Name,
        [string]$Url,
        [string]$Commit,
        [string]$Path,
        [switch]$Recursive
    )

    $repositoryExists = Test-Path -LiteralPath (Join-Path $Path ".git")
    if ($repositoryExists) {
        $currentCommit = [string](& git -C $Path rev-parse HEAD 2>$null)
        if ($LASTEXITCODE -ne 0 -or $currentCommit.Trim() -ne $Commit) {
            throw "$Name 源码目录不是指定提交 $Commit`: $Path"
        }
    }
    else {
        if (Test-Path -LiteralPath $Path) {
            throw "$Name 源码目录已存在但不是 Git 仓库: $Path"
        }
        Write-Host "[$Name] 下载官方源码"
        $steps = @(
            @("init", "--quiet", $Path),
            @("-C", $Path, "remote", "add", "origin", $Url),
            @("-C", $Path, "fetch", "--depth", "1", "origin", $Commit),
            @("-C", $Path, "checkout", "--quiet", "--detach", "FETCH_HEAD")
        )
        foreach ($arguments in $steps) {
            & git @arguments
            if ($LASTEXITCODE -ne 0) {
                throw "$Name 源码下载失败"
            }
        }
    }
    if ($Recursive) {
        & git -C $Path submodule update --init --recursive --depth 1
        if ($LASTEXITCODE -ne 0) {
            throw "$Name 子模块下载失败"
        }
    }
}

if ($Model -in @("all", "qwen", "qwen17")) {
    Ensure-ModelSource `
        -Name "Qwen3-TTS" `
        -Url "https://github.com/QwenLM/Qwen3-TTS.git" `
        -Commit "022e286b98fbec7e1e916cb940cdf532cd9f488e" `
        -Path (Join-Path $WorkspaceRoot "tools\Qwen3-TTS")
}
if ($Model -in @("all", "cosy")) {
    Ensure-ModelSource `
        -Name "CosyVoice" `
        -Url "https://github.com/FunAudioLLM/CosyVoice.git" `
        -Commit "074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc" `
        -Path (Join-Path $WorkspaceRoot "tools\CosyVoice") `
        -Recursive
}

$arguments = @((Join-Path $WorkspaceRoot "code\download_optional_models.py"))
if ($Model -ne "all") {
    $arguments += $Model
}
& $PythonPath @arguments
exit $LASTEXITCODE
