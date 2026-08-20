"""从 ModelScope（魔搭，国内直连）下载模型文件并组织成 HF 缓存结构。

用途：whisper / GPT-SoVITS 基础模型的一键安装。
hf-mirror 在国内不可达，魔搭是国内可直连的模型托管站。

用法：
  python download-from-modelscope.py <repo_id> <local_dir> <snapshot_key> <file1> [file2 ...]

local_dir 下会生成 models--<repo>--<snapshot_key>/snapshots/main/<file...>，
与 huggingface_hub snapshot_download 的目录结构一致，方便后端直接识别。
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request

BASE_URL = "https://modelscope.cn/models/{repo}/resolve/master/{path}"

RETRY = 3
TIMEOUT = 60
STATUS_FILE = os.environ.get("MIO_STATUS_FILE", "").strip()
DEPENDENCY_ID = os.environ.get("MIO_DEP_ID", "").strip()
STAGE = os.environ.get("MIO_DOWNLOAD_STAGE", "model").strip() or "model"
START_PERCENT = int(os.environ.get("MIO_DOWNLOAD_START_PERCENT", "0") or 0)
END_PERCENT = int(os.environ.get("MIO_DOWNLOAD_END_PERCENT", "98") or 98)


def write_status(
    *,
    percent: int,
    message: str,
    file_name: str = "",
    downloaded_bytes: int = 0,
    total_bytes: int = 0,
    download_percent: int = 0,
    target_path: str = "",
    speed_mb_s: float = 0.0,
) -> None:
    if not STATUS_FILE:
        return
    payload = {
        "id": DEPENDENCY_ID,
        "stage": STAGE,
        "percent": max(0, min(100, int(percent))),
        "message": message,
        "file_name": file_name,
        "downloaded_bytes": max(0, int(downloaded_bytes)),
        "total_bytes": max(0, int(total_bytes)),
        "download_percent": max(0, min(100, int(download_percent))),
        "target_path": target_path,
        "speed_mb_s": max(0.0, round(float(speed_mb_s), 2)),
        "error": "",
        "done": False,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    target = os.path.abspath(STATUS_FILE)
    temporary = target + ".download.tmp"
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary, target)
    except OSError:
        try:
            if os.path.exists(temporary):
                os.remove(temporary)
        except OSError:
            pass


def content_length(url: str) -> int:
    try:
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "MioAgent/0.7"})
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            length = max(0, int(response.headers.get("Content-Length") or 0))
            if length:
                return length
    except (OSError, ValueError):
        pass
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "MioAgent/0.7", "Range": "bytes=0-0"},
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            content_range = str(response.headers.get("Content-Range") or "")
            if "/" in content_range:
                return max(0, int(content_range.rsplit("/", 1)[1]))
            return max(0, int(response.headers.get("Content-Length") or 0))
    except (OSError, ValueError):
        return 0


def format_mb(value: int) -> str:
    return f"{max(0, value) / 1024 / 1024:.1f}"


def download(
    url: str,
    target: str,
    *,
    file_name: str,
    file_index: int,
    file_count: int,
    completed_bytes: int,
    grand_total: int,
    expected_file_bytes: int,
) -> int:
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".part"
    if os.path.isfile(target):
        final_size = os.path.getsize(target)
        if final_size > 0 and (expected_file_bytes <= 0 or final_size == expected_file_bytes):
            print(f"  已存在并通过大小校验：{target}")
            return final_size
    for attempt in range(1, RETRY + 1):
        try:
            resume_bytes = os.path.getsize(tmp) if os.path.isfile(tmp) else 0
            headers = {"User-Agent": "MioAgent/0.7"}
            if resume_bytes > 0:
                headers["Range"] = f"bytes={resume_bytes}-"
                print(f"  从 {format_mb(resume_bytes)} MB 继续下载")
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                partial_response = int(getattr(resp, "status", 200) or 200) == 206
                if resume_bytes > 0 and not partial_response:
                    resume_bytes = 0
                response_bytes = int(resp.headers.get("Content-Length") or 0)
                total = expected_file_bytes or (resume_bytes + response_bytes if response_bytes else 0)
                done = resume_bytes
                last_update = 0.0
                last_percent = -1
                request_started = time.monotonic()
                mode = "ab" if partial_response and resume_bytes > 0 else "wb"
                with open(tmp, mode) as out:
                    while True:
                        chunk = resp.read(1024 * 256)
                        if not chunk:
                            break
                        out.write(chunk)
                        done += len(chunk)
                        if total:
                            percent = done * 100 // max(1, total)
                            print(f"\r  {os.path.basename(target)}: {percent}%", end="", flush=True)
                        now = time.monotonic()
                        aggregate_done = completed_bytes + done
                        elapsed = max(0.001, now - request_started)
                        speed_mb_s = max(0, done - resume_bytes) / elapsed / 1024 / 1024
                        if grand_total > 0:
                            download_percent = min(100, aggregate_done * 100 // grand_total)
                            overall_percent = START_PERCENT + (
                                (END_PERCENT - START_PERCENT) * download_percent // 100
                            )
                            message = (
                                f"正在下载 {file_name}：{format_mb(done)} / {format_mb(total)} MB；"
                                f"全部 {format_mb(aggregate_done)} / {format_mb(grand_total)} MB（{download_percent}%）"
                            )
                        else:
                            file_percent = done * 100 // max(1, total) if total else 0
                            fraction = ((file_index - 1) + file_percent / 100) / max(1, file_count)
                            overall_percent = START_PERCENT + int((END_PERCENT - START_PERCENT) * fraction)
                            download_percent = 0
                            file_total = f" / {format_mb(total)} MB" if total else " MB"
                            message = f"正在下载 {file_name}：{format_mb(done)}{file_total}"
                        if now - last_update >= 0.25 or download_percent != last_percent:
                            write_status(
                                percent=overall_percent,
                                message=message,
                                file_name=file_name,
                                downloaded_bytes=aggregate_done,
                                total_bytes=grand_total,
                                download_percent=download_percent,
                                target_path=os.path.abspath(target),
                                speed_mb_s=speed_mb_s,
                            )
                            last_update = now
                            last_percent = download_percent
            print()
            if expected_file_bytes > 0 and done != expected_file_bytes:
                raise RuntimeError(
                    f"文件大小不完整：{done} / {expected_file_bytes} 字节；已保留断点文件"
                )
            os.replace(tmp, target)
            return done
        except Exception as exc:
            print(f"\n  第 {attempt} 次尝试失败：{exc}")
            if attempt < RETRY:
                time.sleep(2)
    raise RuntimeError(f"下载失败：{url}")


def main() -> int:
    if len(sys.argv) < 5:
        print("用法：download-from-modelscope.py <repo> <local_dir> <snapshot_key> <file...>")
        print("或：  download-from-modelscope.py --flat <repo> <local_dir> <file...>")
        print("或：  download-from-modelscope.py --dir-name <目录名> <repo> <local_dir> <snapshot_key> <file...>")
        return 2
    dir_name = ""
    if sys.argv[1] == "--dir-name":
        dir_name = sys.argv[2]
        sys.argv = [sys.argv[0]] + sys.argv[3:]
    flat = sys.argv[1] == "--flat"
    if flat:
        repo = sys.argv[2]
        local_dir = sys.argv[3]
        files = sys.argv[4:]
        snapshot_root = local_dir
    else:
        repo = sys.argv[1]
        local_dir = sys.argv[2]
        snapshot_key = sys.argv[3]
        files = sys.argv[4:]
        directory = dir_name or f"models--{repo.replace('/', '--')}--{snapshot_key}"
        snapshot_root = os.path.join(local_dir, directory, "snapshots", "main")
    plans = []
    for name in files:
        url = BASE_URL.format(repo=repo, path=urllib.parse.quote(name))
        target = os.path.join(snapshot_root, name)
        plans.append((name, url, target, content_length(url)))
    grand_total = sum(plan[3] for plan in plans) if all(plan[3] > 0 for plan in plans) else 0
    completed_bytes = 0
    for index, (name, url, target, expected_bytes) in enumerate(plans, start=1):
        print(f"下载 {name} ...")
        completed_bytes += download(
            url,
            target,
            file_name=name,
            file_index=index,
            file_count=len(plans),
            completed_bytes=completed_bytes,
            grand_total=grand_total,
            expected_file_bytes=expected_bytes,
        )
    write_status(
        percent=END_PERCENT,
        message=f"模型文件下载完成：{format_mb(completed_bytes)} MB",
        downloaded_bytes=completed_bytes,
        total_bytes=grand_total,
        download_percent=100,
        target_path=os.path.abspath(snapshot_root),
    )
    print("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
