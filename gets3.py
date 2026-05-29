#!/usr/bin/env python3
"""
从 S3 兼容服务器下载文件（图片、文档、视频、音频、压缩包等）。

单次模式: 指定文件路径（可多个）+ token 下载文件。
批量模式: 从 JSONL 记录文件逐行读取并批量下载。
信息模式: 仅获取下载链接，不下载文件。

用法:
    python gets3.py --single --objKey "path/to/img.png" --token "xxx" --output "./downloads"
    python gets3.py --single --objKey "file.pdf" --token "xxx" --info
    python gets3.py --batch --input records.json --output ./files --limit 100
    python gets3.py --batch --input records.json --type document --output ./docs
    python gets3.py --batch --input records.json --info --output ./urls
"""

import argparse
import json
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

import requests

# ============ 配置（通过环境变量设置）============
API_URL = os.environ.get("S3_API_URL", "")
if not API_URL:
    print("错误: 请设置 S3_API_URL 环境变量", file=sys.stderr)
    sys.exit(1)

_auth_raw = os.environ.get("S3_AUTH_PARAMS", "{}")
try:
    AUTH_PARAMS = json.loads(_auth_raw)
except json.JSONDecodeError:
    print("错误: S3_AUTH_PARAMS 不是合法的 JSON", file=sys.stderr)
    sys.exit(1)

MAX_WORKERS = int(os.environ.get("S3_MAX_WORKERS", "4"))
DOWNLOAD_TIMEOUT = int(os.environ.get("S3_DOWNLOAD_TIMEOUT", "300"))
BATCH_TOKEN_FIELD = os.environ.get("S3_BATCH_TOKEN_FIELD", "token")
BATCH_KEYS_FIELD = os.environ.get("S3_BATCH_KEYS_FIELD", "objKey")
# ==============================================

# ============ 文件类型映射 ============
FILE_TYPE_MAP = {
    "image": {"png", "jpg", "jpeg", "gif", "bmp", "webp", "svg", "tiff", "tif", "ico", "heic", "heif", "raw", "cr2"},
    "document": {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv", "json", "xml", "html", "htm",
                 "md", "markdown", "yaml", "yml", "ini", "cfg", "log", "rtf", "odt", "ods", "odp"},
    "video": {"mp4", "avi", "mov", "mkv", "wmv", "flv", "webm", "m4v", "3gp", "ts"},
    "audio": {"mp3", "wav", "flac", "aac", "ogg", "wma", "m4a", "opus", "ape"},
    "archive": {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "lz", "lz4", "zst"},
}
# =====================================

_lock = Lock()
_stats = {"success": 0, "skipped": 0, "failed": 0, "processed": 0}


def get_file_category(filename: str) -> str:
    """根据文件扩展名判断文件类别"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    for category, extensions in FILE_TYPE_MAP.items():
        if ext in extensions:
            return category
    return "other"


def get_download_url(obj_key: str, token: str) -> str:
    """调用 API 获取单个文件的下载链接"""
    params = dict(AUTH_PARAMS)
    params["token"] = token
    params["objKey"] = obj_key
    resp = requests.get(API_URL, params=params, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    return _extract_url(result, obj_key)


def _extract_url(result, obj_key: str) -> str:
    """从 API 响应中提取下载 URL"""
    if isinstance(result, dict):
        data_map = result.get("data")
        if isinstance(data_map, dict):
            url = data_map.get(obj_key)
            if url:
                return str(url)
        url_map = result.get("url")
        if isinstance(url_map, dict):
            url = url_map.get(obj_key)
            if url:
                return str(url)
        if isinstance(url_map, str):
            return url_map
    if isinstance(result, str):
        return result
    raise ValueError(f"无法从响应中提取 URL: {result}")


def download_file(url: str, save_path: Path) -> str:
    """流式下载文件，返回实际的 Content-Type"""
    resp = requests.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "")
    with open(save_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
    return content_type


def resolve_file_category(filename: str, content_type: str = "") -> str:
    """综合文件扩展名和 Content-Type 判断文件类别"""
    ext_category = get_file_category(filename)
    if ext_category != "other":
        return ext_category
    if content_type:
        ct_category = _get_category_from_content_type(content_type)
        if ct_category != "other":
            return ct_category
    return "other"


def _get_category_from_content_type(content_type: str) -> str:
    """根据 Content-Type 字符串推断文件类别"""
    if not content_type:
        return "other"
    main_type = content_type.split(";")[0].strip().lower()
    for prefix, cat in [("image", "image"), ("video", "video"), ("audio", "audio")]:
        if main_type.startswith(prefix):
            return cat
    doc_prefixes = (
        "application/pdf", "application/msword",
        "application/vnd.openxmlformats-officedocument",
        "text/", "application/json", "application/xml",
    )
    for dp in doc_prefixes:
        if main_type.startswith(dp):
            return "document"
    archive_types = {
        "application/zip", "application/x-rar-compressed", "application/x-7z-compressed",
        "application/x-tar", "application/gzip", "application/x-bzip2",
    }
    if main_type in archive_types:
        return "archive"
    return "other"


def type_matches_filter(category: str, type_filter: str) -> bool:
    """检查文件类别是否匹配指定过滤条件"""
    if type_filter == "all":
        return True
    if type_filter in FILE_TYPE_MAP:
        return category == type_filter
    for cat, exts in FILE_TYPE_MAP.items():
        if type_filter in exts:
            return category == cat
    return category == type_filter


def save_metadata(file_path: Path, token: str, obj_key: str, url: str,
                  category: str, content_type: str = "") -> None:
    """保存文件元信息为同名 .txt 文件"""
    stem = file_path.stem
    txt_path = file_path.parent / f"{stem}.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"token: {token}\n")
        f.write(f"objKey: {obj_key}\n")
        f.write(f"url: {url}\n")
        f.write(f"category: {category}\n")
        if content_type:
            f.write(f"contentType: {content_type}\n")
        f.write(f"downloadedAt: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")


def download_single(obj_key: str, token: str, output_dir: Path,
                    info_only: bool = False, flat: bool = False) -> dict:
    """下载单个文件，返回 {status, url, category, filename}"""
    filename = obj_key.rsplit("/", 1)[-1]
    category = get_file_category(filename)

    result = {
        "obj_key": obj_key,
        "filename": filename,
        "category": category,
        "status": "unknown",
        "url": None,
    }

    if info_only:
        try:
            url = get_download_url(obj_key, token)
            result["url"] = url
            result["status"] = "success"
            print(f"  [INFO] {obj_key}")
            print(f"         URL: {url}")
            return result
        except Exception:
            result["status"] = "failed"
            print(f"  [FAIL] {obj_key} - 获取链接失败", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            return result

    if flat:
        save_dir = output_dir
    else:
        save_dir = output_dir / category

    save_path = save_dir / filename

    if save_path.exists():
        with _lock:
            _stats["skipped"] += 1
        result["status"] = "skipped"
        print(f"  [SKIP] {filename} 已存在")
        return result

    try:
        save_dir.mkdir(parents=True, exist_ok=True)
        url = get_download_url(obj_key, token)
        result["url"] = url
        content_type = download_file(url, save_path)

        actual_category = resolve_file_category(filename, content_type)
        if actual_category != category and not flat:
            new_dir = output_dir / actual_category
            new_dir.mkdir(parents=True, exist_ok=True)
            new_path = new_dir / filename
            save_path.rename(new_path)
            save_path = new_path
            category = actual_category

        save_metadata(save_path, token, obj_key, url, category, content_type)

        with _lock:
            _stats["success"] += 1
        result["status"] = "success"
        result["category"] = category
        print(f"  [OK] {filename} ({category})")
        return result
    except Exception:
        print(f"  [FAIL] {obj_key}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        with _lock:
            _stats["failed"] += 1
        result["status"] = "failed"
        return result


def matches_type(obj_key: str, type_filter: str) -> bool:
    """检查 objKey 是否匹配类型过滤条件"""
    if type_filter == "all":
        return True
    filename = obj_key.rsplit("/", 1)[-1]
    return type_matches_filter(get_file_category(filename), type_filter)


def process_batch_line(line_num: int, line: str, output_dir: Path,
                       info_only: bool = False, flat: bool = False,
                       type_filter: str = "all") -> list:
    """处理单行 JSON 记录（批量模式），返回该行的结果列表"""
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        print(f"[行 {line_num}] JSON 解析失败，跳过", file=sys.stderr)
        with _lock:
            _stats["failed"] += 1
            _stats["processed"] += 1
        return []

    token = record.get(BATCH_TOKEN_FIELD, "")
    obj_keys = record.get(BATCH_KEYS_FIELD, [])
    if isinstance(obj_keys, str):
        obj_keys = [obj_keys]

    if not token or not obj_keys:
        print(f"[行 {line_num}] token 或文件列表为空，跳过", file=sys.stderr)
        with _lock:
            _stats["failed"] += 1
            _stats["processed"] += 1
        return []

    filtered_keys = [k for k in obj_keys if matches_type(k, type_filter)]
    if not filtered_keys:
        print(f"[行 {line_num}] 类型过滤后无匹配文件，跳过")

    results = []
    record_dir = output_dir if (flat or info_only) else output_dir / str(line_num)

    for obj_key in filtered_keys:
        r = download_single(obj_key, token, record_dir, info_only=info_only, flat=flat)
        r["line_num"] = line_num
        results.append(r)
        if not info_only:
            time.sleep(0.05)

    with _lock:
        _stats["processed"] += 1

    return results


# ============ 单次模式 ============
def run_single(args):
    obj_keys = args.objKey
    token = args.token
    output_dir = Path(args.output)
    info_only = args.info

    if not info_only:
        output_dir.mkdir(parents=True, exist_ok=True)

    mode_label = "信息查询" if info_only else "单次下载"
    print(f"{mode_label}: {len(obj_keys)} 个文件，输出: {output_dir}\n")
    start_time = time.time()

    all_results = []
    for obj_key in obj_keys:
        r = download_single(obj_key, token, output_dir,
                           info_only=info_only, flat=args.flat)
        all_results.append(r)
        if not info_only:
            time.sleep(0.05)

    elapsed = time.time() - start_time
    if info_only:
        print(f"\n===== 完成 =====\n"
              f"成功获取链接: {sum(1 for r in all_results if r['status'] == 'success')} | "
              f"失败: {sum(1 for r in all_results if r['status'] == 'failed')} | "
              f"耗时: {elapsed:.0f}s")
    else:
        print(f"\n===== 完成 =====\n"
              f"成功: {_stats['success']} | 跳过: {_stats['skipped']} | "
              f"失败: {_stats['failed']} | 耗时: {elapsed:.0f}s")


# ============ 批量模式 ============
def run_batch(args):
    json_file = Path(args.input)
    output_dir = Path(args.output)
    limit = args.limit
    workers = args.workers
    info_only = args.info
    urls_only = args.urls_only
    type_filter = args.type
    flat = args.flat

    if not json_file.exists():
        print(f"文件不存在: {json_file}", file=sys.stderr)
        sys.exit(1)

    if info_only and urls_only:
        print("错误: --info 和 --urls-only 不能同时使用", file=sys.stderr)
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    tasks = []
    with open(json_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if limit > 0 and len(tasks) >= limit:
                break
            tasks.append((len(tasks) + 1, line))

    total_lines = len(tasks)
    total_files = 0
    for _, line in tasks:
        try:
            record = json.loads(line)
            obj_keys = record.get(BATCH_KEYS_FIELD, [])
            if isinstance(obj_keys, str):
                obj_keys = [obj_keys]
            filtered = [k for k in obj_keys if matches_type(k, type_filter)]
            total_files += len(filtered)
        except Exception:
            pass

    type_info = f"，类型过滤: {type_filter}" if type_filter != "all" else ""
    mode_label = "信息查询" if info_only else ("URL导出" if urls_only else "批量下载")
    print(f"共 {total_lines} 行，约 {total_files} 个文件，{workers} 线程{mode_label}{type_info}\n")
    start_time = time.time()

    all_results = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for line_num, line in tasks:
            future = executor.submit(
                process_batch_line, line_num, line, output_dir,
                info_only=info_only, flat=flat, type_filter=type_filter
            )
            futures[future] = line_num

        done_count = 0
        for future in as_completed(futures):
            line_num = futures[future]
            try:
                results = future.result()
                all_results.extend(results)
            except Exception:
                print(f"[行 {line_num}] 处理异常", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
            done_count += 1
            if done_count % 100 == 0 or done_count == total_lines:
                elapsed = time.time() - start_time
                with _lock:
                    print(
                        f"进度: {done_count}/{total_lines} 行, "
                        f"成功 {_stats['success']} | 跳过 {_stats['skipped']} | "
                        f"失败 {_stats['failed']} | 耗时 {elapsed:.0f}s"
                    )

    if info_only and all_results:
        summary_file = output_dir / "urls.json"
        success_results = [r for r in all_results if r["status"] == "success"]
        with open(summary_file, "w", encoding="utf-8") as f:
            json.dump(success_results, f, ensure_ascii=False, indent=2)
        print(f"\n链接汇总已保存: {summary_file} ({len(success_results)} 条)")

    if urls_only and all_results:
        urls_file = output_dir / "urls.txt"
        success_results = [r for r in all_results if r["status"] == "success"]
        with open(urls_file, "w", encoding="utf-8") as f:
            for r in success_results:
                f.write(f"{r['url']}\n")
        print(f"\nURL 列表已保存: {urls_file} ({len(success_results)} 条)")

    elapsed = time.time() - start_time
    print(
        f"\n===== 完成 =====\n"
        f"总行数: {total_lines}\n"
        f"总文件: {total_files}\n"
        f"成功:   {_stats['success']}\n"
        f"跳过:   {_stats['skipped']}\n"
        f"失败:   {_stats['failed']}\n"
        f"耗时:   {elapsed:.0f}s"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="S3 文件下载工具 — 支持图片、文档、视频、音频、压缩包等各种文件类型"
    )
    subparsers = parser.add_mutually_exclusive_group(required=True)
    subparsers.add_argument("--single", action="store_true", help="单次下载/查询模式")
    subparsers.add_argument("--batch", action="store_true", help="批量下载/查询模式")

    parser.add_argument("--objKey", action="append", help="文件路径（可多次指定）")
    parser.add_argument("--token", default="default_token", help="认证 token")

    parser.add_argument("--input", "-i", help="JSONL 记录文件路径")
    parser.add_argument("--limit", "-n", type=int, default=500, help="最多处理行数（0=全部）")
    parser.add_argument("--workers", "-w", type=int, default=MAX_WORKERS, help="并发线程数")

    parser.add_argument("--info", action="store_true",
                        help="仅获取下载链接，不下载文件")
    parser.add_argument("--urls-only", action="store_true",
                        help="仅保存 URL 列表为纯文本文件 urls.txt")

    parser.add_argument("--type", "-t", default="all",
                        help="按文件类型过滤: image, document, video, audio, archive, other, all, "
                             "或具体扩展名如 pdf, docx, mp4（默认 all）")
    parser.add_argument("--flat", action="store_true",
                        help="平铺输出，不按类型/行号创建子文件夹")

    parser.add_argument("--output", "-o", default="./s3_downloads", help="输出目录")

    args = parser.parse_args()

    if args.single:
        if not args.objKey:
            parser.error("单次模式需要 --objKey 参数（可多次指定）")
        run_single(args)
    else:
        if not args.input:
            parser.error("批量模式需要 --input 参数指定 JSONL 记录文件路径")
        run_batch(args)