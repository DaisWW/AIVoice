param(
    [string]$Proxy = 'http://127.0.0.1:7897'
)

$ErrorActionPreference = 'Stop'
$codeRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $codeRoot
$modelRoot = Join-Path $projectRoot 'tools\GPT-SoVITS\GPT_SoVITS'
$g2pwTarget = Join-Path $modelRoot 'text\G2PWModel'
$modelScopeBase = 'https://www.modelscope.cn/models/XXXXRT/GPT-SoVITS-Pretrained/resolve/master'
$huggingFaceBase = 'https://huggingface.co/XXXXRT/GPT-SoVITS-Pretrained/resolve/main'

$files = @(
    @{ Relative = 'pretrained_models/chinese-hubert-base/config.json'; Bytes = 1449 },
    @{ Relative = 'pretrained_models/chinese-hubert-base/preprocessor_config.json'; Bytes = 212 },
    @{ Relative = 'pretrained_models/chinese-hubert-base/pytorch_model.bin'; Bytes = 188811417 },
    @{ Relative = 'pretrained_models/chinese-roberta-wwm-ext-large/config.json'; Bytes = 963 },
    @{ Relative = 'pretrained_models/chinese-roberta-wwm-ext-large/tokenizer.json'; Bytes = 268962 },
    @{ Relative = 'pretrained_models/chinese-roberta-wwm-ext-large/pytorch_model.bin'; Bytes = 651225145 },
    @{ Relative = 'pretrained_models/fast_langdetect/lid.176.bin'; Bytes = 131266198 },
    @{ Relative = 'pretrained_models/fast_langdetect/lid.176.ftz'; Bytes = 938013 },
    @{ Relative = 'pretrained_models/gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt'; Bytes = 155315150 },
    @{ Relative = 'pretrained_models/gsv-v2final-pretrained/s2G2333k.pth'; Bytes = 106035259 },
    @{ Relative = 'G2PWModel.zip'; Bytes = 588856634 }
)

function Invoke-Download {
    param(
        [string]$Url,
        [string]$Destination,
        [string]$ProxyUrl = ''
    )

    $arguments = @(
        '-L', '--fail', '--show-error',
        '--connect-timeout', '30',
        '--speed-time', '60',
        '--speed-limit', '1024',
        '--retry', '10',
        '--retry-delay', '3',
        '--retry-all-errors',
        '--continue-at', '-',
        '--output', $Destination
    )
    if ($ProxyUrl) {
        $arguments += @('--proxy', $ProxyUrl)
    }
    $arguments += $Url
    & curl.exe @arguments
    return $LASTEXITCODE
}

foreach ($item in $files) {
    if ($item.Relative -eq 'G2PWModel.zip') {
        $installedG2pw = Get-ChildItem -LiteralPath $g2pwTarget -Recurse -Filter '*.onnx' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($installedG2pw) {
            Write-Host '已安装: GPT_SoVITS/text/G2PWModel'
            continue
        }
    }
    $relativeWindows = $item.Relative.Replace('/', '\')
    $destination = Join-Path $modelRoot $relativeWindows
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null

    if (Test-Path -LiteralPath $destination) {
        $length = (Get-Item -LiteralPath $destination).Length
        if ($length -eq $item.Bytes) {
            Write-Host "已存在: $($item.Relative)"
            continue
        }
        if ($length -gt $item.Bytes) {
            throw "文件大于官方大小，请人工检查: $destination"
        }
        Write-Host "断点续传: $($item.Relative) ($length/$($item.Bytes))"
    }
    else {
        Write-Host "下载: $($item.Relative)"
    }

    $modelScopeUrl = "$modelScopeBase/$($item.Relative)"
    $exitCode = Invoke-Download -Url $modelScopeUrl -Destination $destination
    if ($exitCode -ne 0 -and $Proxy) {
        Write-Warning "ModelScope 下载失败，切换 Hugging Face 代理"
        $huggingFaceUrl = "$huggingFaceBase/$($item.Relative)?download=true"
        $exitCode = Invoke-Download -Url $huggingFaceUrl -Destination $destination -ProxyUrl $Proxy
    }
    if ($exitCode -ne 0) {
        throw "下载失败: $($item.Relative)，退出码 $exitCode"
    }

    $actualBytes = (Get-Item -LiteralPath $destination).Length
    if ($actualBytes -ne $item.Bytes) {
        throw "文件大小校验失败: $destination，实际 $actualBytes，预期 $($item.Bytes)"
    }
}

$g2pwZip = Join-Path $modelRoot 'G2PWModel.zip'
$existingOnnx = Get-ChildItem -LiteralPath $g2pwTarget -Recurse -Filter '*.onnx' -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $existingOnnx) {
    $downloadsRoot = Join-Path $projectRoot 'tools\downloads'
    New-Item -ItemType Directory -Force -Path $downloadsRoot | Out-Null
    $temporaryRoot = Join-Path $downloadsRoot "g2pw_extract_$PID"
    New-Item -ItemType Directory -Force -Path $temporaryRoot | Out-Null
    Expand-Archive -LiteralPath $g2pwZip -DestinationPath $temporaryRoot -Force

    $onnx = Get-ChildItem -LiteralPath $temporaryRoot -Recurse -Filter '*.onnx' | Select-Object -First 1
    if (-not $onnx) {
        throw "G2PW 压缩包中没有找到 ONNX 模型: $g2pwZip"
    }
    New-Item -ItemType Directory -Force -Path $g2pwTarget | Out-Null
    Get-ChildItem -LiteralPath $onnx.Directory.FullName -Force |
        Copy-Item -Destination $g2pwTarget -Recurse -Force

    $resolvedTemporary = (Resolve-Path -LiteralPath $temporaryRoot).Path
    $resolvedDownloads = (Resolve-Path -LiteralPath $downloadsRoot).Path
    if (-not $resolvedTemporary.StartsWith($resolvedDownloads, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理工作目录外的临时目录: $resolvedTemporary"
    }
    Remove-Item -LiteralPath $resolvedTemporary -Recurse -Force

    $installedOnnx = Get-ChildItem -LiteralPath $g2pwTarget -Recurse -Filter '*.onnx' | Select-Object -First 1
    if (-not $installedOnnx) {
        throw "G2PW 解压后校验失败: $g2pwTarget"
    }
    $resolvedZip = (Resolve-Path -LiteralPath $g2pwZip).Path
    $resolvedModelRoot = (Resolve-Path -LiteralPath $modelRoot).Path
    if (-not $resolvedZip.StartsWith($resolvedModelRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理模型目录外的压缩包: $resolvedZip"
    }
    Remove-Item -LiteralPath $resolvedZip -Force
}

Write-Host "GPT-SoVITS v2 最小推理模型已准备完成: $modelRoot"
