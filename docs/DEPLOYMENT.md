# 部署与构建

下面的域名、路径都是模板。不会连接任何原部署服务器。推荐把仓库作为独立服务目录，AstrBot 仅安装根目录插件。

## 1. 准备依赖与私有输入

- Python 3.12（服务本身仅标准库；插件依赖 httpx）。
- JDK 17 或以上，`java`、`javac`、`keytool` 在 PATH 中。
- 自行取得乐健体育 3.10.3 原版 APK；工具会检查支持的 ARM64/ARMv7 原生库和业务结构。
- Android SDK Build Tools 配套 `lib/apksigner.jar`（包含 `com.android.apksig` 类），作为 `--apksig-jar` 输入；第三方工具不随本项目分发。
- 为自己的部署新建 PKCS12 签名，私钥和密码文件只能由构建服务账户读取。

示例（在仓库根目录，`work/` 被 Git 忽略）：

```bash
mkdir -p work/private
python -c "from pathlib import Path; import secrets; p=Path('work/private/signing-password.txt'); p.write_text(secrets.token_urlsafe(32), encoding='utf8'); p.chmod(0o600)"
keytool -genkeypair -alias lejian -keyalg RSA -keysize 2048 -validity 3650 -dname "CN=Lejian Reminder" -storetype PKCS12 -keystore work/private/reminder.p12 -storepass:file work/private/signing-password.txt
keytool -exportcert -alias lejian -keystore work/private/reminder.p12 -storepass:file work/private/signing-password.txt -file work/private/signer.der
```

Windows 可通过 `New-Item -ItemType Directory -Force work/private` 创建目录。私钥、口令与构建目录需要额外设置 Windows ACL；Python `chmod` 不能代替 ACL。

## 2. 准备构建目录

```bash
python scripts/prepare_build.py --base-apk /PRIVATE/original-3.10.3.apk --apksig-jar /SDK/build-tools/VERSION/lib/apksigner.jar --keystore work/private/reminder.p12 --password-file work/private/signing-password.txt --certificate work/private/signer.der --public-base https://YOUR_DOMAIN/lejian
python scripts/test_build.py
```

`prepare_build.py` 先验证基底，再复制私有输入、编译签名器并生成 `build/build-config.json`。目标目录已存在会拒绝覆盖；更新代码时使用 `--output /PRIVATE/new-build` 准备新目录、验证后再切换 `LEJIAN_BUILD_DIR`，保留原签名。

`--public-base` 是 APK 公网回调地址，必须为 `https://你的域名/lejian`，不是内网服务地址。这个 URL 会进入 APK；服务凭据不会。证书 DER 是公开证书，不是私钥。构建时计算 Android `Signature.hashCode`，签名器再检查实际私钥证书与该 DER 一致，防止签名与原生兼容常量错配。

构建产物包含私人授权，整个 `build/`、`work/` 都不可发布。测试 APK 的授权没有对应账号，仅用于验证构建。

## 3. 启动提醒服务

生成两个不同的随机服务端令牌，不要复用网站主密钥：

```bash
python -c "from pathlib import Path; import secrets; d=Path('server/data'); d.mkdir(parents=True,exist_ok=True); [(p.write_text(secrets.token_urlsafe(48)),p.chmod(0o600)) for p in [d/'bot-token.txt',d/'website-token.txt']]"
```

配置以下环境变量（本仓库不会自动加载 `.env`；应由 systemd、容器或进程管理器注入）：

| 变量 | 说明 |
| --- | --- |
| `LEJIAN_HOST` | 默认 `127.0.0.1`；容器监听按私网拓扑设置，不直接暴露服务端口到公网 |
| `LEJIAN_PORT` | 默认 `18090` |
| `LEJIAN_DB` | SQLite 文件绝对路径，默认 `server/data/lejian.db` |
| `LEJIAN_TOKEN_FILE` | 机器人令牌文件绝对路径，例如生成的 `server/data/bot-token.txt` |
| `LEJIAN_WEBSITE_TOKEN_FILE` | 网站令牌文件绝对路径，例如 `server/data/website-token.txt` |
| `LEJIAN_BINDING_RESOLVE_URL` | 必填，自己网站 `/comments/bridge/lejian/resolve` 的完整 URL |
| `LEJIAN_BINDING_RESOLVE_HOST` | 可选，仅私网回源需指定虚拟主机时设置 |
| `LEJIAN_DOWNLOAD_DIR` | 专属 APK 目录，默认 `server/data/downloads` |
| `LEJIAN_BUILD_DIR` | 上一步准备的私有构建目录绝对路径，默认仓库 `build/` |
| `LEJIAN_LEAD_SECONDS` | 默认 `1800`；当前用户文案按 30 分钟设计，建议保留默认 |

```bash
python server/service.py
```

长期运行交给独立低权限服务账户和进程管理器，工作目录不允许 Web 静态公开。使用**单个提醒服务进程**：SQLite 连接与构建锁设计为进程内共享，不支持直接增加多个服务副本。不要把 `service.py` 当作无状态多进程服务启动。

公网只代理手机需要的接口。例如 Caddy 在与服务相同主机时：

```caddyfile
YOUR_DOMAIN {
    @mobile path /lejian/activate /lejian/hello /lejian/hook /lejian/watch-state /lejian/health
    handle @mobile {
        reverse_proxy 127.0.0.1:18090
    }
    # 其余请求转发到你的网站应用，包括 /tools/lejian/* 和绑定复核接口。
    handle {
        reverse_proxy 127.0.0.1:8000
    }
}
```

该片段只说明路由，不包括平台特定的限流配置。`/issue`、`/download/*` 使用网站令牌，`/pending`、`/claim-check`、`/ack` 使用机器人令牌，只供私网调用。网站回调可走私网，也必须校验网站令牌。

## 4. 网站与 AstrBot

按 [网站契约](WEBSITE.md) 挂载接口，网站配置 `LEJIAN_SERVICE_URL` 为服务私网 origin；`LEJIAN_SERVICE_TOKEN_FILE` 指向同一网站令牌的安全副本，或使用 `LEJIAN_SERVICE_TOKEN` 环境变量。

AstrBot 配置见 README。它使用独立机器人令牌，与网站令牌不同。服务地址必须在 AstrBot 所在容器可达；确认后启用插件。命令「乐健下载」应返回你自己的网页。

## 5. 验收与备份

1. 未登录不能生成/下载，未绑定 QQ 不能生成；另一账号不能下载前一账号的 APK。
2. 手机上确认专属版与官方版共存、正常登录、自动激活；首次安装后可重新下载同签名新包测试覆盖升级。
3. 报名成功收到确认，取消后不再发送待发送提醒；设置报名提醒收到确认，取消按钮和四个限额正确。
4. 解绑/更换 QQ 或账号授权版本变化后，旧包下载和旧设备上报应失效。
5. 备份 SQLite、一致的签名私钥/口令和服务配置到受限位置；不要把个人 APK、日志或数据库作为 GitHub Release 附件。

模板不会部署或修改任何现有线上服务。服务日志只报告异常类型，不打印请求体或签名口令；排查时也应保持该边界。
