# gets3

从 S3 兼容服务器下载文件的命令行工具。支持图片、文档、视频、音频、压缩包等各种文件类型。

## 快速开始

```bash
# 1. 设置环境变量
export S3_API_URL="http://your-server.com/api/endpoint"
export S3_AUTH_PARAMS='{"key1":"val1","key2":"val2"}'

# 2. 下载单个文件
python gets3.py --single --objKey "path/to/file.png" --token "<TOKEN>" --output "./downloads"

# 3. 批量下载
python gets3.py --batch --input records.json --output "./downloads" --limit 100

# 4. 只查看链接不下载
python gets3.py --single --objKey "path/to/file.png" --token "<TOKEN>" --info

# 5. 按类型过滤（只下载文档）
python gets3.py --batch --input records.json --output "./downloads" --type document
```

## 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `S3_API_URL` | 是 | API 地址 |
| `S3_AUTH_PARAMS` | 是 | 认证参数（JSON） |
| `S3_MAX_WORKERS` | 否 | 并发数（默认 4） |
| `S3_DOWNLOAD_TIMEOUT` | 否 | 超时秒数（默认 300） |

## 文件结构

```
gets3/
├── README.md   ← 本文件
├── SKILL.md    ← 技能完整文档
└── gets3.py    ← 主脚本
```

## 依赖

- Python 3.8+
- requests

```bash
pip install requests
```

## 详细文档

参见 [SKILL.md](./SKILL.md)