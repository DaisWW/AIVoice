from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_docker_launcher_uses_configured_host_port_and_hides_existing_password() -> (
    None
):
    launcher = _text("docker_voice_lab.bat")
    generated_environment = _text("docker/ensure_env.ps1")

    assert 'findstr /B "VOICE_LAB_PORT="' in launcher
    assert '"VOICE_LAB_PORT=18082"' in generated_environment
    assert "$port='!HOST_PORT!'" in launcher
    assert "'http://127.0.0.1:' + $port + '/api/healthz'" in launcher
    assert "http://127.0.0.1:!HOST_PORT!/admin" in launcher
    assert 'if "!ENV_CREATED!"=="1" echo 初始密码:' in launcher
    assert 'if not "!ENV_CREATED!"=="1" echo 初始密码:   已存在' in launcher


def test_compose_passes_text_model_environment_overrides() -> None:
    compose = _text("docker/compose.yaml")

    for name in (
        "VOICE_TEXT_MODEL_BASE_URL",
        "VOICE_TEXT_MODEL_API_KEY",
        "VOICE_TEXT_MODEL",
    ):
        assert f"{name}: ${{{name}:-}}" in compose


def test_container_drops_to_fixed_non_root_user() -> None:
    dockerfile = _text("docker/Dockerfile")
    entrypoint = _text("docker/entrypoint.sh")

    assert "ARG APP_UID=10001" in dockerfile
    assert "ARG APP_GID=10001" in dockerfile
    assert 'useradd --uid "${APP_UID}" --gid "${APP_GID}"' in dockerfile
    assert "chown -R voicelab:voicelab /app/web/data" in entrypoint
    assert "server=(" in entrypoint
    assert (
        'exec setpriv --reuid=voicelab --regid=voicelab --init-groups "${server[@]}"'
        in entrypoint
    )
