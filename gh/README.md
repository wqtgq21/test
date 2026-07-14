# GitHub Actions 批量注册（Windows Runner）

本目录把项目改造成可在 GitHub Actions 上无人值守批量注册的版本。

- **运行环境**：`windows-latest`（GitHub 托管的 Windows 镜像，自带 Chrome）
- **并发**：N 台机器同时跑（matrix 矩阵），每台注册指定数量
- **自动入库**：注册成功直接上传到你 config 里的 CPA 远程地址
- **结果**：账号文件作为 Artifact 上传（保留 7 天）

---

## 文件结构

```
gh/
├── README.md          # 本说明
└── ci_config.json     # CI 运行时用的配置（会被复制成 config.json）
.github/workflows/
└── register.yml       # Actions 工作流
```

---

## 参数说明（手动触发时填写）

| 参数 | 含义 | 示例 |
|------|------|------|
| `runners` | 并行 runner 数量，每台机器独立跑 | `2`（共 2 台并发） |
| `count_per_runner` | **每台** runner 注册多少个账号 | `5`（每台 5 个） |

**总注册数 = runners × count_per_runner**

例如 runners=2、count_per_runner=5 → 总共注册 10 个账号。

---

## 配置（`gh/ci_config.json`）

CI 会把 `gh/ci_config.json` 复制成 `config.json` 再运行。当前配置（私人仓库，写死凭据）：

```json
{
  "mail_api_url": "https://mail.minecraft-cn.net",
  "mail_domain": "olsbvgq.shop",
  "proxy": "",                                      ← CI 环境直连，留空
  "enable_nsfw": true,
  "register_count": 5,
  "cpa_auto_add": true,
  "cpa_remote_url": "http://156.229.166.49:8317",
  "cpa_management_key": "Nf5B8Smzi1AV3yN2F6MtJ6JTQI0cmo025PsZ1n8I"
}
```

> `proxy` 必须留空：GitHub Runner 在国外，能直连注册页和 `auth.x.ai`。

如需改 CPA 地址或邮箱，编辑 `gh/ci_config.json` 即可。

---

## 运行方式

### 1. 手动触发（推荐先用这个验证）

1. 推送代码到 GitHub
2. 仓库页 **Actions** → 左侧选「Grok 批量注册 (Windows)」
3. 右上 **Run workflow** → 填 `runners` 和 `count_per_runner` → 点绿色按钮
4. 点进运行中的 job，可实时看日志；注册成功的账号在右上 Artifacts 里下载

### 2. 定时触发（可选）

编辑 `.github/workflows/register.yml`，取消 `schedule` 注释：

```yaml
on:
  workflow_dispatch:
    ...
  schedule:
    - cron: "0 8 * * *"   # 每天 UTC 08:00（北京时间 16:00）自动跑
```

定时跑时用 workflow 顶部的默认值（runners=2、count_per_runner=5），如需改默认值直接改 yml。

---

## CI 模式做了哪些适配

| 改动 | 说明 |
|------|------|
| `--ci --count N` 启动参数 | 非交互模式，跳过 `input('start')` 等待，直接开跑指定数量 |
| 浏览器 headless + `--no-sandbox` | CI 无桌面、无 root 沙箱，必须无头 + 关沙箱 |
| 用户数据目录指向 `RUNNER_TEMP` | 避免权限/冲突问题 |
| Python 3.13 | 规避 3.14 的 TLS 兼容问题，也避免本地 Python 切换逻辑 |
| 依赖自动安装 | `pip install -r requirements.txt` |
| Chrome 自带 | `windows-latest` 镜像预装 Chrome，无需额外装 |

---

## 注意事项

1. **Actions 免费额度**：公开仓库无限，私有仓库每月有分钟数限制（Windows 按 1× 计费）。批量跑前留意用量。
2. **成功率**：注册可能因 Turnstile / 验证码 / 网络抖动失败，失败会自动重试，最终以日志里的「成功: X \| 失败: X」为准。
3. **账号文件**：成功账号写到 `accounts_*.txt`，同时自动上传 CPA；Artifact 保留 7 天，记得及时下载。
4. **CPA 入库**：`cpa_auto_add=true` 已开，注册成功后日志会出现 `[CPA] 已上传远程 ...`。
