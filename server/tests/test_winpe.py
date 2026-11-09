from __future__ import annotations

import re

import pytest

from app.models import IsoImage
from app.winpe import DIAG_FIRST, RUN_COPY, render_netdispatch_cmd


@pytest.fixture
def script(settings) -> str:
    return render_netdispatch_cmd(settings, IsoImage(id=7, setup_path="setup.exe"), True, "52:54:00:ab:cd:01")


def _lines(script: str) -> list[str]:
    return script.split("\r\n")


def test_script_reruns_itself_from_a_ram_disk_copy(script):
    # WinPE reads the wimboot-injected file back wrong after a few seconds, which
    # ended the script (and so rebooted WinPE) in the middle of the retry loop.
    lines = _lines(script)
    assert RUN_COPY == "X:\\netdispatch.cmd"
    assert lines[:4] == [
        "@echo off",
        f'if /i not "%~f0"=="{RUN_COPY}" (',
        f'  copy /y "%~f0" {RUN_COPY} >nul && {RUN_COPY}',
        ")",
    ]
    # started without CALL, so the copy replaces this script instead of returning to it
    assert f"call {RUN_COPY}" not in script
    # the copy is made before anything that runs long enough for reads to go wrong
    assert script.index(RUN_COPY) < script.index("wpeinit") < script.index("net use")


def test_retry_loop_waits_and_goes_back_to_connect(script):
    lines = _lines(script)
    wait = lines.index(":wait")
    assert lines[wait + 1 : wait + 4] == [
        "echo netdispatch: retrying in 10 seconds",
        "ping -n 11 127.0.0.1 >nul",
        "goto connect",
    ]
    assert f"if %ND_TRIES%=={DIAG_FIRST} call :diag" in lines


def test_diagnostics_only_use_tools_in_setup_boot_wim(script):
    # Windows Setup's boot.wim has find, ping and ipconfig, but no findstr.
    assert "findstr" not in script
    lines = _lines(script)
    diag = lines[lines.index(":diag") :]
    assert "ipconfig" in diag
    assert "if not exist %SystemRoot%\\System32\\find.exe goto diagping" in diag
    assert any(line.startswith('ipconfig | find /i "adapter" >nul || echo ') for line in diag)
    assert any(line.startswith('ipconfig | find "IPv4 Address" >nul || echo ') for line in diag)
    assert diag.index(":diagping") < diag.index("ping -n 1 -w 2000 10.0.0.5")


def test_every_jump_target_exists(script):
    lines = _lines(script)
    labels = {line[1:].lower() for line in lines if line.startswith(":")}
    targets = re.findall(r"\b(?:goto|call) :?([A-Za-z]\w*)", script)
    assert targets
    for target in targets:
        assert target.lower() == "eof" or target.lower() in labels, target
