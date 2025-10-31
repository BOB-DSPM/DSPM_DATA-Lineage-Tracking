# modules/connectors/git_fetch.py
from __future__ import annotations
import os, shutil, subprocess, tempfile
from pathlib import Path
from typing import Optional

def _build_auth_url(git_url: str, token: Optional[str]) -> str:
    # https URL만 토큰 주입(ssh 미지원). 토큰에 특수문자 있으면 URL 인코딩 고려.
    if token and git_url.startswith("https://"):
        return git_url.replace("https://", f"https://{token}@")
    return git_url

def shallow_clone(git_url: str, branch: str = "main",
                  subdir: Optional[str] = None, token: Optional[str] = None) -> Path:
    """깃 얕은 클론(depth=1) 후 subdir만 반환(있으면)"""
    tmp_root = Path(tempfile.mkdtemp(prefix="dspm-git-"))
    try:
        auth_url = _build_auth_url(git_url, token)
        cmd = ["git", "clone", "--depth", "1", "--branch", branch,
               "--filter=blob:none", auth_url, str(tmp_root)]
        subprocess.run(cmd, check=True, capture_output=True)
    except Exception:
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise

    if subdir:
        target = (tmp_root / subdir).resolve()
        if not target.is_dir() or not str(target).startswith(str(tmp_root)):
            shutil.rmtree(tmp_root, ignore_errors=True)
            raise ValueError(f"subdir not found or invalid: {subdir}")
        return target
    return tmp_root
