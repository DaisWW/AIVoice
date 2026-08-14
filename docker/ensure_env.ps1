param(
    [Parameter(Mandatory = $true)]
    [string]$Path
)

$resolved = [System.IO.Path]::GetFullPath($Path)
if (Test-Path -LiteralPath $resolved) {
    exit 0
}

$parent = Split-Path -Parent $resolved
New-Item -ItemType Directory -Path $parent -Force | Out-Null
$bytes = New-Object byte[] 32
$random = [Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $random.GetBytes($bytes)
}
finally {
    $random.Dispose()
}
$token = [Convert]::ToBase64String($bytes).TrimEnd('=') -replace '\+', '-' -replace '/', '_'
@(
    "VOICE_LAB_ADMIN_TOKEN=$token"
    "VOICE_LAB_IMAGE=voice-lab:local"
    "VOICE_LAB_BASE_IMAGE=docker.m.daocloud.io/pytorch/pytorch:2.5.1-cuda12.1-cudnn9-runtime"
    "VOICE_LAB_TORCH_VERSION=2.5.1"
    "VOICE_LAB_TORCH_CUDA_INDEX=cu121"
    "VOICE_LAB_BUILD_PROXY="
    "VOICE_LAB_DATA_DIR=./docker-data"
    "VOICE_LAB_INPUT_DIR=./input"
    "VOICE_LAB_OUTPUT_DIR=./output"
) | Set-Content -LiteralPath $resolved -Encoding ascii
