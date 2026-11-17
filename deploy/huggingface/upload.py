"""Upload the prepared Space folder to Hugging Face Spaces.

Usage:
    set HF_TOKEN=hf_xxx
    python upload.py <username>/<space-name>

The token is read from the HF_TOKEN environment variable and is never printed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from huggingface_hub import HfApi
from huggingface_hub.utils import HfHubHTTPError


def main() -> int:
    if len(sys.argv) != 2 or "/" not in sys.argv[1]:
        print("用法: python upload.py <HF用户名>/<Space名称>")
        print("例如: python upload.py zhangsan/EcoPolice")
        return 2

    repo_id = sys.argv[1].strip().strip("/")
    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        print("错误: 没有设置 HF_TOKEN。请先创建 Hugging Face Access Token（Write 权限）。")
        return 2

    folder = Path(__file__).resolve().parent / "space"
    if not (folder / "Dockerfile").exists() or not (folder / "README.md").exists():
        print(f"错误: 部署目录不完整: {folder}")
        return 2

    api = HfApi(token=token)
    try:
        who = api.whoami()
        print(f"已登录 Hugging Face: {who.get('name', '?')}")
    except Exception as exc:  # noqa: BLE001
        print(f"错误: Token 无效或网络不可达: {exc}")
        return 1

    try:
        url = api.create_repo(
            repo_id=repo_id,
            repo_type="space",
            space_sdk="docker",
            private=False,
            exist_ok=True,
        )
        print(f"Space 已创建/已存在: {url}")
    except HfHubHTTPError as exc:
        print(f"错误: 创建 Space 失败: {exc}")
        print("请确认用户名、Space 名称正确，且 Token 有 write 权限。")
        return 1

    print("正在上传文件（约 15 MB，首次可能需要 1~3 分钟）...")
    try:
        api.upload_folder(
            folder_path=str(folder),
            repo_id=repo_id,
            repo_type="space",
            commit_message="Deploy 警务遥感 from local bundle",
        )
    except HfHubHTTPError as exc:
        print(f"错误: 上传失败: {exc}")
        return 1

    user, space = repo_id.split("/", 1)
    direct = f"https://{user.lower()}-{space.lower().replace('_', '-')}.hf.space"
    print("\n上传完成。Hugging Face 会自动构建 Docker 镜像，通常 3~10 分钟。")
    print(f"Space 管理页: https://huggingface.co/spaces/{repo_id}")
    print(f"构建日志:     https://huggingface.co/spaces/{repo_id}/logs")
    print(f"公网网址:     {direct}")
    print("\n如果网址暂时 404/503，是镜像还在构建；等日志出现 Running 后再打开。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
