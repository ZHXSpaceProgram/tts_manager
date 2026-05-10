# Tabletop Simulator Mod Manager

一个**简单易用，安全可靠**的TTS图包管理器。

- 自动读取模组 JSON 中引用的文件
- 支持查看文件、命名分组、删除分组
- 自动显示图像预览（第一张是模组封面，其他图像随机抽取）
- 自动备份删除文件（保留目录结构，拖回即可恢复）

使用方法：
- exe：从 [Releases](https://github.com/ZHXSpaceProgram/tts_manager/releases) 下载
- python：`python tts_manager.py`

注意事项：
- 如果运行过 v1.x 的旧版软件，在运行 v2 版本软件前请删除 tts_manager_data.json，否则会报错 KeyError: 'json_path' 退出。