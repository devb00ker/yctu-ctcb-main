# 第三方来源

`proto/rm2027_client_up.proto`、`proto/rm2027_client_down.proto`、`proto/rm2027_radar_flat.proto` 参考并复制自用户提供的 `RM2027-CustomTerminal-CommunicationBackend-main`，Copyright (c) 2026 huhu233，MIT License。许可证完整保留于 `third_party/rm2027-backend-MIT.txt`。协议已依据《RoboMaster 2027 机甲大师高校系列赛通信协议 V1.0.0（20261029）》第 2 章核验。雷达定义为对官方歧义的兼容解释，不能据此宣称官方格式已经确认。

`SharkDataSever-main`（MeroT，package 声明 ISC）作为本地模拟和消息可视化的设计参考，本项目未复制其业务代码。

我们的桌面界面、Python 通信、重组、模拟器及测试独立实现。依赖包的授权以各自发行包为准：PySide6/Qt 为 LGPLv3/GPLv3/商业许可，PyAV/FFmpeg 及其编码库的分发许可需随使用的二进制保留。当前交付为源码与通过 pip 安装的开发环境；后续打包分发应一并保留 Qt、FFmpeg 和其他依赖许可证。

官方协议文档版权归 DJI。原 PDF 不打包进本项目。
