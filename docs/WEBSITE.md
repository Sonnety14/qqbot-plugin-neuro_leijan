# 已有网站接入契约

`website/router.py` 是可注入依赖的 FastAPI 路由适配器，**不是完整登录网站**。它带有下载页静态资源，但不附带注册、登录、邮件验证或 QQ 归属验证系统。必须先有可信的账号及验证过的 QQ 绑定。

```python
from website.router import build_router

app.include_router(build_router(
    identity, "https://YOUR_DOMAIN", store,
    connect=connect, render=render, session_cookie="__Host-your_session",
))
```

安装 `website/requirements.txt`。将 `website/` 作为包放到网站应用可导入的位置，不必将整个 AstrBot 服务环境混到网站里。

## 注入对象

- `identity.db`：数据库路径。适配器使用 SQLite SQL；已有站点数据库不同，请实现兼容查询层或移植这些查询。
- `identity.load_session(cookie)`：验证真实会话并返回字典，失效返回 `None`。返回字段为 `id`（正整数账号 ID）、`verified_at`（已验证邮箱时间）、`csrf`（不可预测 CSRF 值）、`user_auth_version`（整数授权版本）。必须验证会话过期、撤销、账户状态及版本，不可仅解析客户端传来的用户 ID。
- `connect(path)`：上下文管理器，返回支持 `execute`、字典式行读取的数据库连接，正常退出提交、异常回滚并关闭连接。应开启外键约束。
- `store.limit(key, n)`：并发安全限流，建议按账号每小时最多 `n` 次，允许返回 `True`，拒绝返回 `False`。路由传入 `n=3`；不能在生产中使用永远返回 `True` 的测试实现。
- `render(title, description, body, is_admin=False, extra_style="...", extra_script="...")`：返回 HTMLResponse，包含正文、样式与脚本，沿用站点正常安全头及 HTML 转义策略。静态资源自带的 `/account/` 登录和绑定链接可按网站路由调整。

外层应用应为该页面及状态接口添加 `Cache-Control: private, no-store`，登录 Cookie 使用 Secure、HttpOnly、SameSite；POST 同时校验 Origin 和 CSRF。例子中的 `__Host-` Cookie 需 HTTPS、Path=/ 且无 Domain。不要把测试目录中的假会话实现部署到公网。

## 必要数据表字段

路由只读取下面三张宿主表，不会帮你实现验证流程：

```sql
users (id INTEGER PRIMARY KEY, status TEXT, auth_version INTEGER)
email_credentials (user_id INTEGER, verified_at INTEGER)
campus_qq_bindings (user_id INTEGER PRIMARY KEY, qq TEXT)
```

`users.status='active'` 才有效；邮箱 `verified_at` 必须非空。`campus_qq_bindings.qq` 必须来自可信验证结果，例如一次性挑战确认或正式 QQ 授权，不能让用户任意填别人的 QQ。删除/更换绑定即时影响回调结果；撤销账号授权时递增 `auth_version` 并失效相关会话。

路由初始化创建 `lejian_downloads` 表，保存服务端下载句柄、所有者、QQ 快照、授权版本与过期时间。生成过程中、最终下载时都再次检查绑定，防止构建期间账号发生变化。

## 配置与令牌

| 变量 | 用途 |
| --- | --- |
| `LEJIAN_SERVICE_URL` | 私网提醒服务 origin，默认 `http://127.0.0.1:18090` |
| `LEJIAN_SERVICE_TOKEN` | 网站专用令牌，至少 32 字符 |
| `LEJIAN_SERVICE_TOKEN_FILE` | 未设上项时从这个文件读取令牌 |

这里的网站令牌须与提醒服务的 `LEJIAN_WEBSITE_TOKEN_FILE` 内容相同。双向调用都使用它，但与机器人令牌完全分开；不是网站会话密钥或学校登录凭据。

## 接口

| 接口 | 鉴权 / 行为 |
| --- | --- |
| `GET /tools/lejian/` | 登录页面；检查已验证账号，展示绑定状态和专属下载按钮 |
| `GET /tools/lejian/status` | 登录账号的 QQ 绑定状态 |
| `POST /tools/lejian/downloads` | 同源 Origin、CSRF、真实会话，空 JSON `{}`；拒绝客户端传 QQ / account_id |
| `GET /tools/lejian/downloads/{handle}/app.apk` | 同一登录所有者、未过期且账号/QQ/授权版本未变；服务端代理下载，不暴露上游句柄和令牌 |
| `POST /comments/bridge/lejian/resolve` | 网站 Bearer 令牌；接收 `{account_id, qq, auth_version}`，返回 `{"valid": true/false}` |

下载默认 1 小时有效，激活授权 7 天内有效。激活后同一授权不能被另一设备认领；APK 不写 QQ 明文，只写随机授权。服务查询账号状态失败时拒绝上报或暂缓发送，不会假定账号仍有效。

建议网站单 worker 起步。进程内的 `building` 集合只能避免同一 worker 重复构建；多 worker 必须让宿主限流器跨进程共享，服务端本身还有全局单构建锁和每账号一分钟间隔。

## 测试和真实接入

`tests/test_website.py` 用临时 SQLite、假会话和 MockTransport 验证路由边界，不依赖原站点代码。请用真实身份系统补做登录退出、QQ 验证、更换账号、撤销授权和超时测试。表名可保留，不代表必须安装其他校园功能插件。
