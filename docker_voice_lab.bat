@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Voice Lab Docker

set "ROOT=%~dp0"
set "PROJECT_ROOT=%ROOT:~0,-1%"
set "COMPOSE=%ROOT%docker\compose.yaml"
set "ENV_FILE=%ROOT%docker\voice-lab.env"
set "ACTION=%~1"
if "%ACTION%"=="" set "ACTION=up"

if /I "%ACTION%"=="stop" goto stop
if /I "%ACTION%"=="logs" goto logs
if /I "%ACTION%"=="status" goto status
if /I "%ACTION%"=="build" set "FORCE_BUILD=1"
if /I not "%ACTION%"=="up" if /I not "%ACTION%"=="build" (
  echo 用法: docker_voice_lab.bat [up^|build^|stop^|logs^|status]
  exit /b 2
)

if not exist "%COMPOSE%" (
  echo [ERROR] 找不到 Docker Compose 配置: %COMPOSE%
  exit /b 1
)
if not exist "%ROOT%docker-data\voice_web.sqlite3" (
  echo [ERROR] 找不到 docker-data\voice_web.sqlite3
  echo 请先手工执行: .venv-gpt-sovits\Scripts\python.exe docker\prepare_data.py
  echo 部署脚本不会自动复制或覆盖现有数据。
  exit /b 1
)
if not exist "%ROOT%tools\GPT-SoVITS\GPT_SoVITS\pretrained_models\gsv-v2final-pretrained\s2G2333k.pth" (
  echo [ERROR] GPT-SoVITS 模型不完整，请先准备 pretrained_models。
  exit /b 1
)
if not exist "%ROOT%tools\GPT-SoVITS\GPT_SoVITS\pretrained_models\chinese-roberta-wwm-ext-large\model.safetensors" (
  echo [ERROR] chinese-roberta 模型不完整，请先准备 pretrained_models。
  exit /b 1
)
if not exist "%ROOT%tools\GPT-SoVITS\GPT_SoVITS\pretrained_models\chinese-hubert-base\model.safetensors" (
  echo [ERROR] chinese-hubert 模型不完整，请先准备 pretrained_models。
  exit /b 1
)
if not exist "%ROOT%tools\GPT-SoVITS\GPT_SoVITS\pretrained_models\gsv-v2final-pretrained\s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt" (
  echo [ERROR] s1bert 模型不完整，请先准备 pretrained_models。
  exit /b 1
)
if not exist "%ROOT%tools\GPT-SoVITS\GPT_SoVITS\text\G2PWModel\g2pW.onnx" (
  echo [ERROR] G2PWModel 不完整，请先准备 G2PWModel。
  exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Docker Desktop 未运行，或当前用户没有 Docker 权限。
  exit /b 1
)
docker compose version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] 找不到 Docker Compose v2。
  exit /b 1
)

set "ENV_CREATED=0"
if not exist "%ENV_FILE%" (
  echo 正在生成系统管理员初始密码...
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%ROOT%docker\ensure_env.ps1" -Path "%ENV_FILE%"
  if errorlevel 1 (
    echo [ERROR] 无法生成管理员初始密码。
    exit /b 1
  )
  set "ENV_CREATED=1"
)
for /f "tokens=1,* delims==" %%A in ('findstr /B "VOICE_LAB_PORT=" "%ENV_FILE%"') do set "HOST_PORT=%%B"
if not defined HOST_PORT set "HOST_PORT=18082"
for /f "tokens=1,* delims==" %%A in ('findstr /B "VOICE_LAB_IMAGE=" "%ENV_FILE%"') do set "IMAGE_NAME=%%B"
if not defined IMAGE_NAME set "IMAGE_NAME=voice-lab:local"

set "BUILD_ARG="
if defined FORCE_BUILD set "BUILD_ARG=--build"
if not defined FORCE_BUILD (
  docker image inspect "!IMAGE_NAME!" >nul 2>&1
  if errorlevel 1 set "BUILD_ARG=--build"
)

echo.
echo [Voice Lab] 正在启动 Docker 服务...
docker compose --project-directory "%PROJECT_ROOT%" --env-file "%ENV_FILE%" -f "%COMPOSE%" up -d %BUILD_ARG%
if errorlevel 1 (
  echo [ERROR] Docker 启动失败，最近日志如下:
  docker compose --project-directory "%PROJECT_ROOT%" --env-file "%ENV_FILE%" -f "%COMPOSE%" logs --tail=120 voice-lab
  exit /b 1
)

powershell.exe -NoLogo -NoProfile -Command "$port='!HOST_PORT!'; $deadline=(Get-Date).AddSeconds(90); do { try { $r=Invoke-WebRequest -UseBasicParsing -Uri ('http://127.0.0.1:' + $port + '/api/readyz') -TimeoutSec 3; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; Start-Sleep -Seconds 2 } while ((Get-Date) -lt $deadline); exit 1"
if errorlevel 1 (
  echo [ERROR] 服务未在 90 秒内通过健康检查，最近日志如下:
  docker compose --project-directory "%PROJECT_ROOT%" --env-file "%ENV_FILE%" -f "%COMPOSE%" logs --tail=160 voice-lab
  exit /b 1
)

if "!ENV_CREATED!"=="1" (
  for /f "tokens=1,* delims==" %%A in ('findstr /B "VOICE_LAB_ADMIN_PASSWORD=" "%ENV_FILE%"') do set "ADMIN_PASSWORD=%%B"
  if not defined ADMIN_PASSWORD for /f "tokens=1,* delims==" %%A in ('findstr /B "VOICE_LAB_ADMIN_TOKEN=" "%ENV_FILE%"') do set "ADMIN_PASSWORD=%%B"
)
echo.
echo ==================================================
echo Voice Lab Docker 已就绪
echo 本机工作台: http://127.0.0.1:!HOST_PORT!/
echo 管理后台:   http://127.0.0.1:!HOST_PORT!/admin
echo 初始用户名: admin
if "!ENV_CREATED!"=="1" echo 初始密码:   !ADMIN_PASSWORD!
if not "!ENV_CREATED!"=="1" echo 初始密码:   已存在，请使用已保存的管理员密码。
echo 局域网访问: 使用本机 IPv4 地址加 :!HOST_PORT!
echo 查看日志:   docker_voice_lab.bat logs
echo 停止服务:   docker_voice_lab.bat stop
echo ==================================================
if not "%VOICE_LAB_NO_BROWSER%"=="1" start "" "http://127.0.0.1:!HOST_PORT!/admin"
exit /b 0

:stop
docker info >nul 2>&1
if errorlevel 1 exit /b 1
docker compose --project-directory "%PROJECT_ROOT%" --env-file "%ENV_FILE%" -f "%COMPOSE%" stop voice-lab
exit /b %ERRORLEVEL%

:logs
docker compose --project-directory "%PROJECT_ROOT%" --env-file "%ENV_FILE%" -f "%COMPOSE%" logs -f --tail=120 voice-lab
exit /b %ERRORLEVEL%

:status
docker compose --project-directory "%PROJECT_ROOT%" --env-file "%ENV_FILE%" -f "%COMPOSE%" ps
exit /b %ERRORLEVEL%
