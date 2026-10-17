"""打包 macOS 应用「AI投标系统.app」（Apple 芯片 arm64）。

在任何能联网的电脑上运行（Linux / macOS 都可以，不需要 Xcode）：

    pip install zstandard pillow
    python packaging/macos/build_app.py

产物：dist/AI投标系统.app 与 dist/AI投标系统-Mac版.zip

做法：
- Python 运行环境取自 conda-forge 的 osx-arm64 包（可重定位：主程序静态链接，动态库用 @rpath/@loader_path，自带签名）；
- 依赖用 pip 按 macosx_11_0_arm64 / cp312 平台下载二进制包装进 site 目录；jieba 是纯 Python 包单独安装；
- 应用本体是一个 bash 启动器（launcher.sh），首次运行把运行环境复制到「~/Library/Application Support/AI投标系统」。
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BUILD = ROOT / "build" / "macos"
DIST = ROOT / "dist"
APP_NAME = "AI投标系统"
CHANNEL = "https://conda.anaconda.org/conda-forge/osx-arm64/"
PY_VERSION = "3.12"
SKIP = {"tk", "tzdata", "ca-certificates", "python_abi", "pip", "setuptools", "wheel"}

REQUIREMENTS = [
    "fastapi>=0.115,<1",
    "uvicorn>=0.30",
    "httpx>=0.27",
    "beautifulsoup4>=4.12",
    "lxml>=5.0",
    "python-docx>=1.1",
    "pypdf>=4.0",
    "python-multipart>=0.0.9",
    "APScheduler>=3.10,<4",
    "tzdata>=2024.1",
]


def app_version() -> str:
    for line in (ROOT / "app" / "__init__.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split("=")[1].strip().strip("\"'")
    return "0.0.0"


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=300) as r:
        return r.read()


def extract_conda(data: bytes, filename: str, dest: Path) -> dict:
    import zstandard

    if filename.endswith(".conda"):
        z = zipfile.ZipFile(io.BytesIO(data))
        info_name = next(n for n in z.namelist() if n.startswith("info-"))
        pkg_name = next(n for n in z.namelist() if n.startswith("pkg-"))
        meta = {}
        it = tarfile.open(fileobj=zstandard.ZstdDecompressor().stream_reader(io.BytesIO(z.read(info_name))), mode="r|")
        for m in it:
            if m.name == "info/index.json":
                meta = json.load(it.extractfile(m))
                break
        tarfile.open(fileobj=zstandard.ZstdDecompressor().stream_reader(io.BytesIO(z.read(pkg_name))), mode="r|").extractall(dest, filter="tar")
        return meta
    tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:bz2")
    meta = json.load(tf.extractfile("info/index.json"))
    tf.extractall(dest, filter="tar")
    return meta


def build_python(prefix: Path) -> None:
    print("下载 conda-forge 软件索引…")
    repo = json.loads(fetch(CHANNEL + "current_repodata.json"))
    pk = {**repo.get("packages", {}), **repo.get("packages.conda", {})}

    def best(name: str):
        c = [(fn, m) for fn, m in pk.items() if m["name"] == name]
        if name == "python":
            c = [x for x in c if x[1]["version"].startswith(PY_VERSION) and "cpython" in x[1]["build"]]
        c.sort(key=lambda x: x[1].get("timestamp", 0))
        return c[-1] if c else None

    todo, done, extra = ["python"], {}, []
    while todo:
        n = todo.pop()
        if n in done or n in SKIP or n.startswith("__"):
            continue
        b = best(n)
        if not b:
            continue
        done[n] = b
        for dep in b[1].get("depends", []):
            parts = dep.split()
            if parts[0] == "libpython" and len(parts) >= 3:
                # libpython 要和 python 精确匹配，索引里只有最新版，按文件名直接下载
                extra.append(f"libpython-{parts[1]}-{parts[2]}.conda")
                continue
            todo.append(parts[0])
    prefix.mkdir(parents=True, exist_ok=True)
    for name, (fn, m) in sorted(done.items()):
        print(f"  {name} {m['version']}")
        extract_conda(fetch(CHANNEL + fn), fn, prefix)
    for fn in extra:
        print(f"  {fn}")
        extract_conda(fetch(CHANNEL + fn), fn, prefix)
    # 精简：只保留运行需要的部分
    for rel in ("include", "share", "ssl/misc", "lib/pkgconfig", f"lib/python{PY_VERSION}/test", f"lib/python{PY_VERSION}/idlelib",
                f"lib/python{PY_VERSION}/tkinter", f"lib/python{PY_VERSION}/turtledemo", f"lib/python{PY_VERSION}/lib2to3",
                f"lib/python{PY_VERSION}/ensurepip", f"lib/python{PY_VERSION}/config-{PY_VERSION}-darwin"):
        shutil.rmtree(prefix / rel, ignore_errors=True)
    for p in prefix.rglob("*.a"):
        p.unlink()
    for p in (prefix / f"lib/python{PY_VERSION}/lib-dynload").glob("_tkinter*.so"):
        p.unlink()
    for p in (prefix / "bin").iterdir():
        if p.name not in ("python3", f"python{PY_VERSION}"):
            p.unlink()
    for d in list(prefix.rglob("__pycache__")):
        shutil.rmtree(d, ignore_errors=True)


def trim_python(prefix: Path) -> None:
    """去掉用不到的部分：静态链接的 python 不需要 libpython 动态库；不用终端交互库与测试模块。"""
    lib = prefix / "lib"
    for pat in ("libpython3*.dylib", "libform*", "libmenu*", "libpanel*", "libncurses*", "libtinfo*", "libreadline*", "libhistory*"):
        for p in lib.glob(pat):
            p.unlink()
    dyn = lib / f"python{PY_VERSION}" / "lib-dynload"
    for pat in ("_test*", "_ctypes_test*", "_xxtestfuzz*", "_curses*", "readline*", "_dbm*", "_tkinter*"):
        for p in dyn.glob(pat):
            p.unlink()
    for rel in ("pydoc_data", "curses"):
        shutil.rmtree(lib / f"python{PY_VERSION}" / rel, ignore_errors=True)


def trim_site(site: Path) -> None:
    """jieba 只用基础分词：去掉深度学习模型、词性标注、关键词提取；lxml 去掉 C 源码与头文件。"""
    for rel in ("jieba/lac_small", "jieba/posseg", "jieba/analyse", "lxml/includes"):
        shutil.rmtree(site / rel, ignore_errors=True)
    for pat in ("*.pyx", "*.pxi", "*.pxd", "*.h"):
        for p in (site / "lxml").rglob(pat):
            p.unlink()


def build_site(site: Path) -> None:
    print("下载 macOS arm64 依赖包…")
    site.mkdir(parents=True, exist_ok=True)
    pip = [sys.executable, "-m", "pip", "install", "-q", "--target", str(site)]
    subprocess.check_call(pip + ["--platform", "macosx_11_0_arm64", "--python-version", PY_VERSION, "--implementation", "cp",
                                 "--abi", "cp" + PY_VERSION.replace(".", ""), "--only-binary=:all:"] + REQUIREMENTS)
    subprocess.check_call(pip + ["--no-deps", "jieba>=0.42"])
    shutil.rmtree(site / "bin", ignore_errors=True)
    for d in list(site.rglob("__pycache__")):
        shutil.rmtree(d, ignore_errors=True)


def build_app(cache: bool = True) -> Path:
    version = app_version()
    runtime_cache = BUILD / "runtime-cache"
    py, site = runtime_cache / "python", runtime_cache / "site"
    if not (cache and (py / "bin" / "python3").exists()):
        shutil.rmtree(py, ignore_errors=True)
        build_python(py)
    if not (cache and (site / "fastapi").exists()):
        shutil.rmtree(site, ignore_errors=True)
        build_site(site)
    trim_python(py)
    trim_site(site)

    app = DIST / f"{APP_NAME}.app"
    shutil.rmtree(app, ignore_errors=True)
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    res = contents / "Resources"
    rt = res / "runtime"
    rt.mkdir(parents=True)
    shutil.copytree(py, rt / "python", symlinks=True)
    shutil.copytree(site, rt / "site", symlinks=True)
    system = rt / "system"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "app", system / "app", ignore=ignore)
    shutil.copytree(ROOT / "static", system / "static", ignore=ignore)
    shutil.copytree(ROOT / "tests" / "fixtures", system / "tests" / "fixtures")
    (res / "VERSION").write_text(version, encoding="utf-8")
    shutil.copy(HERE / "AppIcon.icns", res / "AppIcon.icns")
    plist = (HERE / "Info.plist").read_text(encoding="utf-8").replace("__VERSION__", version)
    (contents / "Info.plist").write_text(plist, encoding="utf-8")
    (contents / "PkgInfo").write_text("APPL????", encoding="ascii")
    launcher = contents / "MacOS" / "AIBidding"
    shutil.copy(HERE / "launcher.sh", launcher)
    os.chmod(launcher, 0o755)
    print("已生成", app)
    return app


def make_zip(app: Path) -> Path:
    """用 zip 打包：保留符号链接（-y）与可执行权限，macOS 的「归档实用工具」能正确还原。"""
    out = DIST / f"{APP_NAME}-Mac版.zip"
    out.unlink(missing_ok=True)
    readme = HERE / "Mac版使用说明.md"
    staging = DIST / "_zip"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    shutil.move(str(app), staging / app.name)
    shutil.copy(readme, staging / readme.name)
    subprocess.check_call(["zip", "-9qry", str(out), app.name, readme.name], cwd=staging)
    shutil.move(str(staging / app.name), app)
    shutil.rmtree(staging)
    print("已生成", out, f"{out.stat().st_size / 1024 / 1024:.1f} MB")
    return out


if __name__ == "__main__":
    make_zip(build_app(cache="--fresh" not in sys.argv))
