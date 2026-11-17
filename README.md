# YCTU 自定义终端

面向 RoboMaster 2027 / RMUL 的 Windows 桌面终端。Python + PySide6，MQTT/Protobuf 状态与指令，UDP HEVC 图传。协议依据：2027 V1.0.0（20261029）。

## 启动

当前目录：`E:\自定义终端\yctu-ctcb-main`。

- **双击 `demo.bat`**：启动本地 MQTT 模拟服务器、HEVC 测试图传和操作台，自动连接，全部战况标为演示。
- **双击 `start.bat`**：打开终端，配置服务器和机器人 ID 后点击连接。
- **双击 `simulator.bat`**：独立运行模拟器，可供其他客户端联调。不要同时运行另一个占用 3333 的 broker 或第二个图传接收端。
- **双击 `test.bat`**：运行自动化测试。

首次在其他机器安装：先安装 Python **3.12 x64**，在该目录 PowerShell 执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
.\.venv\Scripts\python.exe -m terminal --demo
```

setup 创建 `.venv`、安装固定版本依赖并生成 Protobuf 类型；以后运行无需重新安装。当前开发环境已在项目目录准备。复制工程到另一台机器时应重新创建虚拟环境，不能依赖复制的 `.venv`。

本机虚拟环境基于 Codex 提供的 Python 3.12；若以后应用运行时路径改变导致环境失效，使用独立安装的 Python 3.12 重新创建 `.venv` 并执行 setup。当前交付为源码工程，尚未打包独立 EXE。

## 界面使用

### 操作台

顶部显示服务器、机器人 ID、连接状态和收发数；卡片显示倒计时、比分、血量、热量、弹量和经济。星号表示状态已过期，缺失字段显示 `—`。图传区域显示原始解码画面、准星和过期提示。

点击“启用键鼠”进入捕获，鼠标会回到图传中心；Esc、窗口失焦、切换页面或断开连接释放全部键鼠。捕获过程中只处理本窗口事件，无全局键盘钩子。鼠标按每个发送周期累积位移；比例和 Y 符号须与电控实测校准。Windows/Qt 定时调度不能保证精确 75Hz；程序使用单调时钟保证不超过配置上限。

常用指令只发送一次。`已提交` 表示进入通信发送流程；实际执行以裁判同步消息为准。哨兵结果在事件日志和 `SentryCtrlResult` 中查看。

地图已接入用户提供的 `RMUL2027_3V3_V1.0.0.stp` 正交俯视底图。模型外包围约12.6×8.6m，主地坪为12×8m；当前局部投影原点对应模型 X=-6、Z=-4，模型红方色块在右下。**裁判坐标原点与方向尚未校准**，实时模式默认只显示底图；核对坐标和 MapClick 单位后配置 `map_calibrated=true`，再显示真实点位并启用点击。点击仅填入 `MapClickCmd` 编辑器，仍需手动发送。演示点位仅用于检查显示。

操作台仅提供步兵、哨兵、重装身份：哨兵显示姿态/控制方式，重装显示装配操作；其他赛项消息保留在协议调试页。

### 协议调试

左侧选择 9 条上行消息，编辑 JSON 并发送；右侧检查 23 条下行消息和原始 HEX。JSON 字段采用官方 snake_case，bytes 为 Base64；自定义指令也可直接输入 HEX。未知字段、错误枚举、越界/非有限数、非法字节长度、超频发送都会报错。

键鼠调试发送为短脉冲，20ms 后自动发全零状态；连续键鼠控制请用操作台。`CustomControl` 发送自定义数据最大 60B；`CustomByteBlock` 接收最大 300B。二者不要交换方向。

### 记录和回放

点击“开始记录”将收发消息写到 `output/session-*.jsonl`。记录包含时间、方向、topic、protobuf 原始 HEX 和 JSON，不包含视频录像。回放会断开实时连接，只重放下行，禁止任何上行发送；回放结束仍保持只读，点击“结束回放”后可重新连接。

## 官方接入

1. 将专用网卡设为 `192.168.12.2`；程序不自动修改 Windows 网卡。
2. 服务器 `192.168.12.1:3333`；clientID 使用所连接机器人十进制 ID，例如 `3` 或 `103`。不能填串口选手端 ID `0x0103`。
3. `video_bind=0.0.0.0`，UDP `3334`；默认只收来自 `192.168.12.1` 的图传。若设备抓包证明来源不同，修改 `video_source`；空字符串允许任何来源。
4. 在官方选手端选择“自定义终端”作为键鼠来源。先检验松键、失焦和机器人侧输入超时保护，再操作机器人。
5. Windows 防火墙如阻止接收，为专用比赛网卡/程序设置所需规则；本项目不修改系统防火墙。

## 配置字段

默认文件 `config/default.json`；保存后写入 `config/local.json`。`--config 路径` 指定其他文件。

| 字段 | 用途 |
|---|---|
| host / port / robot_id | MQTT 服务器与身份 |
| video_bind / video_port / video_source | 接收接口、端口、来源过滤 |
| video_mode | `au` 直接解码完整帧块；`stream` 通过 FFmpeg parser 解析连续码流 |
| radar_format | `nested`（默认）或 `flat`（24个 packed uint32）；需官方确认 |
| map_width_m / map_height_m | 地图尺寸，单位 m |
| map_image | 底图路径，默认 assets/maps/rmul2027-top.png；空字符串显示网格 |
| map_calibrated | 是否已校准地图，false 时禁止点击生成命令 |
| map_click_units_per_m | 地图米转换到 MapClick 线格式的比例 |
| map_role | `operator`：间隔3秒；`commander`：间隔0.5秒 |
| path_mode | `origin`：相对起点；`previous`：逐点累加，官方两章存在冲突 |
| mouse_scale / mouse_y_sign | 鼠标比例、Y符号（±1） |
| control_hz | 1–75，实际频率取决于 Windows 调度 |

## 架构与修改

| 文件 | 职责 |
|---|---|
| `terminal/ui.py` | 操作台、图传/地图控件、协议调试、配置 |
| `terminal/protocol.py` | 消息注册、字段校验、限频定义和坐标转换 |
| `terminal/transport.py` | Paho MQTT、重连重订阅、断线清除指令 |
| `terminal/video.py` | 大端 UDP 重组、顺序恢复、HEVC 解码与最新图像 |
| `terminal/input.py` | 键鼠状态和发送周期 |
| `terminal/recording.py` | 状态快照、有限历史、JSONL 记录/回放 |
| `terminal/simulator.py` | 本机 broker、23条下行演示、上行记录、HEVC生成 |
| `proto/` | 2027 协议定义（MIT 来源见 THIRD_PARTY_NOTICES） |
| `tests/` | 协议字节/边界、UDP故障、真实网络和 GUI 联调 |
| `scripts/build_field_map.py` / `assets/maps/` | STEP 导入、俯视底图和尺寸来源，详见其中 README |

修改 proto 后：

```powershell
.\.venv\Scripts\python.exe scripts\generate_proto.py
.\.venv\Scripts\python.exe -m pytest -q
```

独立模拟器可改变分辨率：

```powershell
.\.venv\Scripts\python.exe -m terminal.simulator --width 1920 --height 1080 --fps 60
```

默认演示为 960×540@30，便于开发机运行；协议标称 1080p60。解码界面根据实际流自适应。模拟器只绑定 127.0.0.1，避免伪装成比赛网络服务。它对哨兵、性能选择、地图指令提供示例反馈；常用兑换等指令仅记录，不实现金币结算或全部裁判规则。

## 边界与待实机验证

- 官方 `RadarInfoToClient` 示例缺字段号且类型有歧义；目前支持两种明确配置，不能宣称任一种已被官方设备证实。
- 官方对路径增量、地图坐标单位的表述需实机核对；项目提供配置。
- UDP 每包≤1400B，包头8B大端；接收支持变长分片，最多8帧在途、单帧4MiB、每帧最多16384片、缺片等待120ms。完整帧按16位帧号排序；无数据2秒后允许发送端帧号重新起始。
- H.265 参考帧丢失可能产生花屏；码流恢复依赖参数集/关键帧。解码队列最多8块，积压时清队列并重建解码器；界面显示超时或解码错误，不保证恢复时间。
- 机器人侧仍需要独立输入超时机制；电脑断电或网络断开时，全零释放消息无法保证到达。
- 本地测试不等于裁判实机、RMUL赛场合规、1080p60延迟或长期稳定性验收。具体赛项规则与兵种需队伍确认。

长期参考文档位于 `D:\obsidian_file\dev\自定义终端`，沿用用户调整后的名称：`Project.md`、`Development Progress.md`、`Decisions and Pitfalls.md`。
