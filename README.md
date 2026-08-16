# 定时任务集合 (Scheduled Tasks)

零依赖（仅 Python 标准库）的 macOS 定时任务集合。统一入口 `run_all.py` 遍历本目录内所有 `*.py` 逐个执行，新增任务 = 往目录丢一个 `.py` 即可，无需改任何调度配置。

首个任务：**TRAE Work 每日自动签到**（逆向自开源 `traework2api`），每天领 200 Work 积分。

## 快速开始

```bash
# 首次登录（浏览器手机号登录，本地 127.0.0.1:18080 自动接收回调，无需手动粘贴）
python3 trae_work_signin.py login

# 每日签到（也可由定时任务自动跑）
python3 trae_work_signin.py signin
# 不带参数等价于 signin
python3 trae_work_signin.py
```

登录成功后凭证保存在 `~/.trae_work_auth.json`（权限 0600，不进入仓库）。

## 新增定时任务

往本目录放一个 `your_task.py`，脚本被「无参运行」时即执行其定时逻辑。`run_all.py` 会自动发现并运行它（跳过自身与 `_` 开头的文件），单任务失败/超时不影响其他任务。

## macOS 定时运行（每天 08:00）

两种方式任选其一：

**方式 A — 登录项（图形化，需本仓库的 .app）**

```bash
bash make_app.sh        # 在本地生成 ScheduledTasks.app（路径便携，已 gitignore）
```

然后在 **系统设置 → 通用 → 登录项** 中点击 `+` 添加该 `.app`。它会在登录后自动运行 `run_all.py`。（注意：登录项只在用户登录时触发一次，不是严格的"每天 8 点"。）

**方式 B — launchd（精确每天 08:00，推荐）**

创建 `~/Library/LaunchAgents/com.hejinlin.scheduled-tasks.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.hejinlin.scheduled-tasks</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/你的绝对路径/scheduled-tasks/run_all.py</string>
  </array>
  <key>WorkingDirectory</key><string>/你的绝对路径/scheduled-tasks</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>8</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/你的绝对路径/scheduled-tasks/run_all.log</string>
  <key>StandardErrorPath</key><string>/你的绝对路径/scheduled-tasks/run_all.log</string>
</dict>
</plist>
```

把上面两处 `/你的绝对路径/scheduled-tasks` 换成实际目录，然后：

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hejinlin.scheduled-tasks.plist
# 或注销重登录自动加载
```

改时间 = 改 plist 里的 `StartCalendarInterval`。

## 隐私说明

- 凭证 `~/.trae_work_auth.json` 在用户 home 目录，**不进入仓库**。
- `ScheduledTasks.app/`、`*.log`、`__pycache__/` 已被 `.gitignore` 忽略（含本机绝对路径，不入库）。
- 仓库内脚本均无硬编码个人路径，可安全公开。
