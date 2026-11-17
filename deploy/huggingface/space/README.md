---
title: 警务遥感
emoji: 🛰️
colorFrom: indigo
colorTo: red
sdk: docker
app_port: 7860
pinned: false
short_description: 生态安全与灾害风险一张图
---

# 警务遥感 · 生态安全与灾害风险一张图

这是“警务遥感”项目的免费云部署版本（Hugging Face Spaces，Docker）。

- 历史异常、高发点、危险等级、自然数据、实时气象
- 自然条件输入 → 危险系数 → 未来 7/14 天时空动画
- 访客无需登录、无需安装任何客户端

注意：免费 CPU Space 可能在一段时间无访问后休眠，首次唤醒会冷启动；
ArcGIS/MATLAB 重算按钮需要宿主机软件，在云 Space 中不可用；
数据写入为容器临时文件，重启后恢复为随包数据。
