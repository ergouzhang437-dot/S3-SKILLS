---
name: gets3
description: 从 S3 服务器下载文件（图片、文档、视频等）。支持单张（指定 objKey）和批量（从 JSON 记录文件）两种模式，支持仅获取下载链接不下载文件。
---

# S3 文件下载工具

从 S3 兼容服务器获取下载链接并将文件保存到本地。支持图片、文档、视频等各种文件类型。

## 配置

脚本通过环境变量读取服务器连接信息，使用前请先设置：

### 必需配置

| 环境变量 | 说明 |
|----------|------|
| `S3_API_URL` | 文件获取 API 地址 |
| `S3_AUTH_PARAMS` | 认证参数，JSON 格式，如 `{"key1":"val1","key2":"val2"}` |

### 可选配置

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `S3_MAX_WORKERS` | 并发下载线程数 | `4` |
| `S3_DOWNLOAD_TIMEOUT` | 单文件下载超时（秒） | `300` |
| `S3_BATCH_TOKEN_FIELD` | 批量模式 JSON 中 token 的字段名 | `token` |
| `S3_BATCH_KEYS_FIELD` | 批量模式 JSON 中文件列表的字段名 | `objKey` |

### 快速设置

**Linux / macOS:**
```bash
export S3_API_URL="http://your-server.com/api/endpoint"
export S3_AUTH_PARAMS='{"key1":"val1","key2":"val2"}'
```

**Windows (PowerShell):**
```powershell
$env:S3_API_URL="http://your-server.com/api/endpoint"
$env:S3_AUTH_PARAMS='{"key1":"val1","key2":"val2"}'
```

## 支持的文件类型

| 类型 | 扩展名 |
|------|------|
| image | png, jpg, jpeg, gif, bmp, webp, svg, tiff, ico, heic |
| document | pdf, doc, docx, xls, xlsx, ppt, pptx, txt, csv, json, xml, html, md |
| video | mp4, avi, mov, mkv, wmv, flv, webm |
| audio | mp3, wav, flac, aac, ogg, wma |
| archive | zip, rar, 7z, tar, gz, bz2 |
| other | 其他所有扩展名 |

## 使用方式

### 方式一：单次下载（指定文件路径）

```bash
python gets3.py --single --objKey "path/to/file.png" --token "<TOKEN>" --output "./downloads"
```

支持传入多个 `--objKey` 参数：
```bash
python gets3.py --single --objKey "path1.png" --objKey "path2.pdf" --objKey "path3.mp4" --token "<TOKEN>" --output "./downloads"
```

### 方式二：批量下载（从 JSON 记录文件）

```bash
python gets3.py --batch --input "./records.json" --output "./downloads" --limit 100
```

JSON 记录文件为每行一个 JSON 对象（JSONL 格式），每条记录包含 token 和文件路径列表。

- `--limit` 限制下载行数，`0` 表示全部（默认 500）
- `--workers` 设置并发线程数（默认 4）
- `--type` 按类型过滤（如 `--type image` 只下载图片，`--type document` 只下载文档，默认 `all`）
- `--flat` 平铺输出，不按行号创建子文件夹

### 方式三：仅获取下载链接（不下载文件）

```bash
# 单次模式 + --info
python gets3.py --single --objKey "path/file.png" --token "<TOKEN>" --info

# 批量模式 + --info
python gets3.py --batch --input records.json --info --output "./urls"
```

`--info` 模式下不会下载文件，而是：
- **单次模式**：在终端打印下载链接
- **批量模式**：将链接汇总保存为 `urls.json` 文件

### 方式四：输出 URL 列表（供其他工具使用）

```bash
python gets3.py --batch --input records.json --urls-only --output "./urls"
```

### 方式五：按类型分类下载

```bash
python gets3.py --batch --input records.json --output "./downloads" --type image
python gets3.py --batch --input records.json --output "./downloads" --type document
python gets3.py --batch --input records.json --output "./downloads" --type pdf
```

## 输出结构

### 默认批量模式（分类存储）

```
output/
├── images/
│   ├── 1/
│   │   ├── photo.png
│   │   └── photo.txt
│   └── 2/
│       ├── banner.jpg
│       └── banner.txt
├── documents/
│   └── 3/
│       ├── report.pdf
│       └── report.txt
├── videos/
├── audio/
├── archives/
└── others/
```

### --flat 模式（平铺）

```
output/
├── photo.png
├── photo.txt
├── report.pdf
├── report.txt
└── ...
```

每个下载的文件会附带一个同名 `.txt` 文件记录元信息。

## 完整参数列表

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--single` | 单次下载模式 | - |
| `--batch` | 批量下载模式 | - |
| `--objKey` | 文件路径（可多次指定，单次模式） | - |
| `--token` | 认证 token | `default_token` |
| `--input`, `-i` | JSON 记录文件路径（批量模式） | - |
| `--output`, `-o` | 输出目录 | `./s3_downloads` |
| `--limit`, `-n` | 最多处理行数，0=全部（批量模式） | `500` |
| `--workers`, `-w` | 并发线程数（批量模式） | `4` |
| `--info` | 仅获取下载链接，不下载文件 | `false` |
| `--urls-only` | 仅保存 URL 列表为纯文本文件 | `false` |
| `--type`, `-t` | 按文件类型过滤（image/document/video/audio/archive/other/all，或具体扩展名如 pdf） | `all` |
| `--flat` | 平铺输出，不创建子文件夹 | `false` |

## 注意事项

- 批量模式下默认按文件类型和行号分类存储
- 已存在的文件会自动跳过
- 每个下载的文件会附带一个同名 `.txt` 文件记录元信息
- `--info` 和 `--urls-only` 互斥，不能同时使用
- 大文件下载超时时间可通过 `S3_DOWNLOAD_TIMEOUT` 调整